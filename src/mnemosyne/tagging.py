from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mutagen.mp4 import MP4, MP4Cover

from .inspection import _proposed_tags


class TaggingError(RuntimeError):
    """Transactional staged metadata normalization failed."""


@dataclass(frozen=True)
class TaggingPreview:
    job_dir: Path
    audio_path: Path
    cover_path: Path | None
    proposed_tags: dict[str, str]


@dataclass(frozen=True)
class TaggingResult:
    job_dir: Path
    audio_path: Path
    rollback_path: Path
    report_path: Path
    pre_tag_sha256: str
    post_tag_sha256: str
    written_tags: dict[str, str]
    embedded_cover: bool
    embedded_cover_sha256: str | None


_MP4_TAG_MAP = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album_artist": "aART",
    "album": "\xa9alb",
    "date": "\xa9day",
    "genre": "\xa9gen",
}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaggingError(f"Could not read JSON report {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_audio(job_dir: Path, report: dict) -> Path:
    audio = report.get("audio") or {}

    staged_path = audio.get("stagedPath")
    if staged_path:
        path = Path(str(staged_path))
        if path.is_file():
            return path

    name = audio.get("canonicalStagedName")
    if name:
        path = job_dir / str(name)
        if path.is_file():
            return path

    raise TaggingError("Could not resolve the staged canonical audio file.")


def _resolve_cover(job_dir: Path, report: dict) -> Path | None:
    cover = report.get("cover") or {}

    staged_path = cover.get("stagedPath")
    if staged_path:
        path = Path(str(staged_path))
        if path.is_file():
            return path

    name = cover.get("canonicalStagedName")
    if name:
        path = job_dir / str(name)
        if path.is_file():
            return path

    for filename in ("cover.jpg", "cover.jpeg", "cover.png"):
        path = job_dir / filename
        if path.is_file():
            return path

    return None


def _cover_format(path: Path) -> int:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return MP4Cover.FORMAT_JPEG
    if suffix == ".png":
        return MP4Cover.FORMAT_PNG
    raise TaggingError(
        f"MP4 cover embedding currently supports JPEG/PNG only, not {suffix or 'unknown'}."
    )


def preview_metadata_normalization(job_dir: Path) -> TaggingPreview:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise TaggingError(f"Staging job directory does not exist: {job_dir}")

    report_path = job_dir / "fetch-report.json"
    if not report_path.is_file():
        raise TaggingError(f"fetch-report.json not found: {report_path}")

    report = _read_json(report_path)

    unresolved_warnings = report.get("warnings") or []
    if unresolved_warnings:
        raise TaggingError(
            "Staging job still has unresolved warnings; metadata mutation is blocked."
        )

    audio_path = _resolve_audio(job_dir, report)
    if audio_path.suffix.lower() not in {".m4a", ".m4b", ".mp4"}:
        raise TaggingError(
            "Metadata Write v1 currently supports MP4-family staged audio only."
        )

    proposed = _proposed_tags(report)
    if not proposed:
        raise TaggingError("No canonical metadata could be derived from the staging report.")

    return TaggingPreview(
        job_dir=job_dir,
        audio_path=audio_path,
        cover_path=_resolve_cover(job_dir, report),
        proposed_tags=proposed,
    )


def _write_mp4_metadata(
    path: Path,
    tags: dict[str, str],
    cover_path: Path | None,
) -> tuple[bool, str | None]:
    try:
        audio = MP4(path)
    except Exception as exc:
        raise TaggingError(f"Could not open MP4 staged audio for tagging: {exc}") from exc

    if audio.tags is None:
        audio.add_tags()

    assert audio.tags is not None

    for friendly_name, value in tags.items():
        atom = _MP4_TAG_MAP.get(friendly_name)
        if atom is not None:
            audio.tags[atom] = [value]

    cover_sha: str | None = None
    embedded = False
    if cover_path is not None:
        cover_bytes = cover_path.read_bytes()
        if not cover_bytes:
            raise TaggingError(f"Cover file is empty: {cover_path}")
        cover_sha = hashlib.sha256(cover_bytes).hexdigest()
        audio.tags["covr"] = [
            MP4Cover(
                cover_bytes,
                imageformat=_cover_format(cover_path),
            )
        ]
        embedded = True

    try:
        audio.save()
    except Exception as exc:
        raise TaggingError(f"Could not save normalized MP4 metadata: {exc}") from exc

    return embedded, cover_sha


def _verify_mp4_metadata(
    path: Path,
    tags: dict[str, str],
    *,
    expected_cover_sha256: str | None,
) -> None:
    try:
        audio = MP4(path)
    except Exception as exc:
        raise TaggingError(f"Could not reopen tagged MP4 for verification: {exc}") from exc

    actual_tags = audio.tags or {}

    for friendly_name, expected in tags.items():
        atom = _MP4_TAG_MAP.get(friendly_name)
        if atom is None:
            continue

        values = actual_tags.get(atom)
        if not values:
            raise TaggingError(
                f"Post-write verification failed: {friendly_name} is missing."
            )

        actual = str(values[0])
        if actual != expected:
            raise TaggingError(
                f"Post-write verification failed for {friendly_name}: "
                f"expected {expected!r}, found {actual!r}."
            )

    if expected_cover_sha256 is not None:
        covers = actual_tags.get("covr")
        if not covers:
            raise TaggingError(
                "Post-write verification failed: embedded artwork is missing."
            )

        actual_cover_sha = hashlib.sha256(bytes(covers[0])).hexdigest()
        if actual_cover_sha != expected_cover_sha256:
            raise TaggingError(
                "Post-write verification failed: embedded artwork SHA-256 mismatch."
            )


def apply_metadata_normalization(job_dir: Path) -> TaggingResult:
    preview = preview_metadata_normalization(job_dir)
    job_dir = preview.job_dir
    audio_path = preview.audio_path
    report_path = job_dir / "fetch-report.json"
    report = _read_json(report_path)

    pre_tag_sha = _sha256(audio_path)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rollback_dir = job_dir / "rollback"
    rollback_dir.mkdir(exist_ok=True)

    rollback_path = rollback_dir / f"{timestamp}-pre-metadata-{audio_path.name}"
    rollback_report_path = rollback_dir / f"{timestamp}-pre-metadata-fetch-report.json"

    if rollback_path.exists() or rollback_report_path.exists():
        raise TaggingError("Rollback target collision; refusing metadata mutation.")

    working_path = job_dir / f".metadata-{timestamp}-{audio_path.name}"
    if working_path.exists():
        raise TaggingError(f"Metadata working path already exists: {working_path}")

    # Work on a copy. The canonical staged file remains untouched until all
    # metadata and artwork verification has passed.
    shutil.copy2(audio_path, working_path)

    try:
        embedded_cover, cover_sha = _write_mp4_metadata(
            working_path,
            preview.proposed_tags,
            preview.cover_path,
        )

        _verify_mp4_metadata(
            working_path,
            preview.proposed_tags,
            expected_cover_sha256=cover_sha,
        )

        post_tag_sha = _sha256(working_path)
        if post_tag_sha == pre_tag_sha:
            raise TaggingError(
                "Metadata operation produced an identical file; refusing to claim mutation."
            )

        # Preserve exact pre-mutation state only after the working copy has
        # successfully passed verification.
        shutil.copy2(audio_path, rollback_path)
        shutil.copy2(report_path, rollback_report_path)

        # Atomic same-directory replacement of staged canonical audio.
        os.replace(working_path, audio_path)

        # Reopen the actual canonical path after replacement and verify again.
        _verify_mp4_metadata(
            audio_path,
            preview.proposed_tags,
            expected_cover_sha256=cover_sha,
        )

        canonical_post_sha = _sha256(audio_path)
        if canonical_post_sha != post_tag_sha:
            raise TaggingError(
                "Canonical staged audio hash changed during metadata replacement."
            )

        history = report.setdefault("metadataNormalizationHistory", [])
        history.append(
            {
                "normalizedAt": datetime.now(timezone.utc).isoformat(),
                "audioPath": str(audio_path),
                "preTagSha256": pre_tag_sha,
                "postTagSha256": canonical_post_sha,
                "rollbackAudio": str(rollback_path),
                "rollbackReport": str(rollback_report_path),
                "writtenTags": dict(preview.proposed_tags),
                "embeddedCover": embedded_cover,
                "embeddedCoverSource": (
                    str(preview.cover_path) if preview.cover_path is not None else None
                ),
                "embeddedCoverSha256": cover_sha,
                "verification": "passed",
            }
        )

        audio_report = report.setdefault("audio", {})
        audio_report["sha256"] = canonical_post_sha
        audio_report["metadataNormalized"] = True
        audio_report["metadataVerification"] = "passed"
        audio_report["embeddedArtwork"] = embedded_cover
        audio_report["embeddedArtworkSha256"] = cover_sha

        report["schemaVersion"] = max(int(report.get("schemaVersion") or 0), 5)
        report["status"] = "staged-metadata-normalized"
        report["metadataNormalization"] = {
            "status": "verified",
            "writtenTags": dict(preview.proposed_tags),
            "embeddedCover": embedded_cover,
            "embeddedCoverSha256": cover_sha,
            "preTagSha256": pre_tag_sha,
            "postTagSha256": canonical_post_sha,
            "rollbackAudio": str(rollback_path),
            "rollbackReport": str(rollback_report_path),
        }
        report["finalLibraryModified"] = False

        temporary_report = job_dir / ".fetch-report.metadata.tmp"
        temporary_report.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _read_json(temporary_report)
        os.replace(temporary_report, report_path)

        return TaggingResult(
            job_dir=job_dir,
            audio_path=audio_path,
            rollback_path=rollback_path,
            report_path=report_path,
            pre_tag_sha256=pre_tag_sha,
            post_tag_sha256=canonical_post_sha,
            written_tags=dict(preview.proposed_tags),
            embedded_cover=embedded_cover,
            embedded_cover_sha256=cover_sha,
        )

    except Exception:
        working_path.unlink(missing_ok=True)

        # If the canonical staged file was already replaced, restore the exact
        # pre-metadata audio and report whenever rollback evidence exists.
        if rollback_path.exists():
            try:
                shutil.copy2(rollback_path, audio_path)
            except OSError:
                pass

        if rollback_report_path.exists():
            try:
                shutil.copy2(rollback_report_path, report_path)
            except OSError:
                pass

        raise
