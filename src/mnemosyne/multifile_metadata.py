from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .inspection import _proposed_tags
from .metadata_io import MetadataIOError, metadata_family, read_metadata, verify_metadata, write_metadata
from .quality import inspect_actual_quality
from .readiness import ReadinessCheck


class MultiFileMetadataError(RuntimeError):
    """Whole-edition metadata/readiness operation failed."""


@dataclass(frozen=True)
class MultiTrackPlan:
    index: int
    total: int
    path: Path
    tags: dict[str, str]


@dataclass(frozen=True)
class MultiTagPreview:
    job_dir: Path
    cover_path: Path
    tracks: tuple[MultiTrackPlan, ...]


@dataclass(frozen=True)
class MultiTagResult:
    job_dir: Path
    rollback_dir: Path
    report_path: Path
    file_count: int
    edition_sha256: str
    embedded_cover_sha256: str


@dataclass(frozen=True)
class MultiReadinessResult:
    job_dir: Path
    ready: bool
    checks: tuple[ReadinessCheck, ...]
    file_count: int
    edition_sha256: str
    readiness_report_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MultiFileMetadataError(f"Could not read JSON {path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _read_json(temp)
    os.replace(temp, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _edition_sha(hashes: list[str]) -> str:
    digest = hashlib.sha256()
    for value in hashes:
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def is_multifile_job(job_dir: Path) -> bool:
    report = _read_json(job_dir / "fetch-report.json")
    audio = report.get("audio") or {}
    edition = report.get("audioEdition") or {}
    return audio.get("mode") == "multi-file" or bool(edition.get("multiFile"))


def _cover_path(job_dir: Path, report: dict[str, Any]) -> Path:
    cover = report.get("cover") or {}
    staged = cover.get("stagedPath")
    if staged and Path(str(staged)).is_file():
        return Path(str(staged))
    name = cover.get("canonicalStagedName")
    if name and (job_dir / str(name)).is_file():
        return job_dir / str(name)
    for filename in ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp"):
        path = job_dir / filename
        if path.is_file():
            return path
    raise MultiFileMetadataError("Canonical standalone cover is missing.")


def _audio_entries(report: dict[str, Any]) -> list[tuple[dict[str, Any], Path]]:
    audio = report.get("audio") or {}
    entries = list(audio.get("files") or [])
    expected = int(audio.get("fileCount") or 0)
    if not entries or expected != len(entries):
        raise MultiFileMetadataError(
            f"Multi-file report count mismatch: expected {expected}, found {len(entries)}."
        )

    result: list[tuple[dict[str, Any], Path]] = []
    for entry in entries:
        path = Path(str(entry.get("stagedPath") or ""))
        if not path.is_file():
            raise MultiFileMetadataError(f"Staged audio file is missing: {path}")
        if path.suffix.lower() not in {".mp3", ".flac"}:
            raise MultiFileMetadataError(
                "Multi-file metadata currently supports MP3 and FLAC editions."
            )
        result.append((entry, path))

    families = {metadata_family(path) for _, path in result}
    if len(families) != 1:
        raise MultiFileMetadataError(
            f"Multi-file edition mixes metadata families: {sorted(families)}"
        )
    return result


def _fallback_title(path: Path) -> str:
    return re.sub(r"^\d+\s*-\s*", "", path.stem).strip() or path.stem


_FLAT_DISC_SIDE_TITLE = re.compile(
    r"(?:^|[_ .-])disc[ _.-]*0*(?P<disc>\d+)"
    r"[ _.-]*side[ _.-]*0*(?P<side>\d+)(?:$|[_ .-])",
    re.IGNORECASE,
)


def _source_identity_title(source_name: str | None) -> str | None:
    if not source_name:
        return None
    stem = Path(str(source_name).replace("\\", "/")).stem
    match = _FLAT_DISC_SIDE_TITLE.search(stem)
    if not match:
        return None
    try:
        disc = int(match.group("disc"))
        side = int(match.group("side"))
    except (TypeError, ValueError):
        return None
    if disc <= 0 or side <= 0:
        return None
    return f"Disc {disc} Side {side}"


def _normalize_chapter_title(value: str) -> str:
    title = re.sub(r"^\s*\d+\s*[-–—:]\s*", "", value).strip()
    title = re.sub(r"\s+", " ", title)
    return title or value.strip()


def _track_tags(
    report: dict[str, Any],
    path: Path,
    *,
    source_name: str | None = None,
) -> dict[str, str]:
    tags = _proposed_tags(report)
    try:
        snapshot = read_metadata(path)
        existing = (snapshot.tags.get("title") or [None])[0]
    except MetadataIOError:
        existing = None

    if existing:
        tags["title"] = _normalize_chapter_title(str(existing))
    else:
        source_title = _source_identity_title(source_name)
        tags["title"] = source_title or _normalize_chapter_title(_fallback_title(path))

    return tags


def preview_multifile_tagging(job_dir: Path) -> MultiTagPreview:
    job_dir = job_dir.resolve()
    report = _read_json(job_dir / "fetch-report.json")

    if report.get("warnings"):
        raise MultiFileMetadataError(
            "Staging job still has unresolved warnings; metadata mutation is blocked."
        )

    cover = _cover_path(job_dir, report)
    entries = _audio_entries(report)
    total = len(entries)

    tracks = tuple(
        MultiTrackPlan(
            index=index,
            total=total,
            path=path,
            tags={
                **_track_tags(
                    report,
                    path,
                    source_name=str(entry.get("sourceName") or ""),
                ),
                "track": f"{index}/{total}",
            },
        )
        for index, (entry, path) in enumerate(entries, start=1)
    )
    return MultiTagPreview(job_dir, cover, tracks)


def apply_multifile_tagging(job_dir: Path) -> MultiTagResult:
    preview = preview_multifile_tagging(job_dir)
    report_path = preview.job_dir / "fetch-report.json"
    report = _read_json(report_path)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work_dir = preview.job_dir / f".metadata-work-{stamp}"
    rollback_dir = preview.job_dir / "rollback" / f"{stamp}-pre-metadata-multifile"

    if work_dir.exists() or rollback_dir.exists():
        raise MultiFileMetadataError("Metadata transaction path collision.")

    work_dir.mkdir()
    rollback_dir.mkdir(parents=True)
    rollback_report = rollback_dir / "fetch-report.json"

    pre_hashes: list[str] = []
    post_hashes: list[str] = []
    work_paths: list[Path] = []
    cover_sha = _sha256(preview.cover_path)

    try:
        # Phase 1: prepare and verify every chapter. Canonical staged files untouched.
        for track in preview.tracks:
            pre_hashes.append(_sha256(track.path))
            work = work_dir / track.path.name
            shutil.copy2(track.path, work)

            evidence = write_metadata(work, track.tags, preview.cover_path)
            if evidence.embedded_cover_sha256 != cover_sha:
                raise MultiFileMetadataError(
                    f"Cover SHA changed while tagging {track.path.name}."
                )

            verify_metadata(work, track.tags, expected_cover_sha256=cover_sha)

            post_hashes.append(_sha256(work))
            work_paths.append(work)

        # Phase 2: retain the complete pre-tag edition.
        for track in preview.tracks:
            shutil.copy2(track.path, rollback_dir / track.path.name)
        shutil.copy2(report_path, rollback_report)

        # Phase 3: commit canonical staged files.
        for track, work in zip(preview.tracks, work_paths):
            os.replace(work, track.path)

        # Phase 4: reopen and verify canonical staged files.
        canonical_hashes: list[str] = []
        for track, expected_hash in zip(preview.tracks, post_hashes):
            verify_metadata(track.path, track.tags, expected_cover_sha256=cover_sha)

            actual_hash = _sha256(track.path)
            if actual_hash != expected_hash:
                raise MultiFileMetadataError(
                    f"Post-commit hash mismatch: {track.path.name}"
                )
            canonical_hashes.append(actual_hash)

        edition_sha = _edition_sha(canonical_hashes)
        audio = report.setdefault("audio", {})
        report_entries = audio.get("files") or []

        if len(report_entries) != len(preview.tracks):
            raise MultiFileMetadataError(
                "Fetch-report chapter count changed during metadata transaction."
            )

        for entry, track, pre_hash, post_hash in zip(
            report_entries,
            preview.tracks,
            pre_hashes,
            canonical_hashes,
        ):
            entry.update(
                {
                    "sha256": post_hash,
                    "metadataNormalized": True,
                    "metadataVerification": "passed",
                    "embeddedArtwork": True,
                    "embeddedArtworkSha256": cover_sha,
                    "preTagSha256": pre_hash,
                    "trackNumber": track.tags["track"],
                    "writtenTags": dict(track.tags),
                }
            )

        family = metadata_family(preview.tracks[0].path)
        audio.update(
            {
                "metadataFamily": family,
                "metadataNormalized": True,
                "metadataVerification": "passed",
                "embeddedArtwork": True,
                "embeddedArtworkSha256": cover_sha,
                "editionSha256": edition_sha,
            }
        )

        report["schemaVersion"] = max(int(report.get("schemaVersion") or 0), 10)
        report["status"] = "staged-metadata-normalized"
        report["metadataNormalization"] = {
            "status": "verified",
            "mode": "multi-file",
            "metadataFamily": family,
            "fileCount": len(preview.tracks),
            "embeddedCover": True,
            "embeddedCoverSha256": cover_sha,
            "editionSha256": edition_sha,
            "rollbackDirectory": str(rollback_dir),
            "rollbackReport": str(rollback_report),
        }
        report["finalLibraryModified"] = False

        _write_json_atomic(report_path, report)
        shutil.rmtree(work_dir, ignore_errors=True)

        return MultiTagResult(
            preview.job_dir,
            rollback_dir,
            report_path,
            len(preview.tracks),
            edition_sha,
            cover_sha,
        )

    except Exception as exc:
        # If commit began, restore the whole pre-tag edition.
        if rollback_report.is_file():
            for track in preview.tracks:
                backup = rollback_dir / track.path.name
                if backup.is_file():
                    try:
                        shutil.copy2(backup, track.path)
                    except OSError:
                        pass
            try:
                shutil.copy2(rollback_report, report_path)
            except OSError:
                pass

        shutil.rmtree(work_dir, ignore_errors=True)

        if isinstance(exc, MultiFileMetadataError):
            raise
        if isinstance(exc, MetadataIOError):
            raise MultiFileMetadataError(str(exc)) from exc
        raise


def verify_multifile_readiness(job_dir: Path) -> MultiReadinessResult:
    job_dir = job_dir.resolve()
    report_path = job_dir / "fetch-report.json"
    report = _read_json(report_path)

    entries = _audio_entries(report)
    cover = _cover_path(job_dir, report)
    cover_sha = _sha256(cover)

    checks: list[ReadinessCheck] = []

    warnings = report.get("warnings") or []
    checks.append(
        ReadinessCheck(
            "warnings-cleared",
            not warnings,
            "No unresolved staging warnings."
            if not warnings
            else f"Unresolved warnings: {warnings}",
        )
    )

    edition = report.get("audioEdition") or {}
    selected = bool(edition.get("selectedEditionKey"))
    checks.append(
        ReadinessCheck(
            "source-resolved",
            selected,
            (
                f"Complete edition explicitly selected: {edition.get('selectedEditionKey')}"
                if selected
                else "Edition-selection provenance is missing."
            ),
        )
    )

    metadata = report.get("metadataNormalization") or {}
    metadata_ok = (
        metadata.get("status") == "verified"
        and metadata.get("mode") == "multi-file"
    )
    checks.append(
        ReadinessCheck(
            "metadata-normalization",
            metadata_ok,
            (
                f"Whole-edition metadata normalization verified for "
                f"{metadata.get('fileCount')} files."
                if metadata_ok
                else "Whole-edition metadata normalization is not verified."
            ),
        )
    )

    recorded_cover = str((report.get("cover") or {}).get("sha256") or "")
    checks.append(
        ReadinessCheck(
            "standalone-cover-sha256",
            recorded_cover == cover_sha,
            (
                f"Standalone cover SHA-256 verified: {cover_sha}"
                if recorded_cover == cover_sha
                else "Standalone cover SHA-256 mismatch."
            ),
        )
    )

    actual_hashes: list[str] = []
    chapter_errors: list[str] = []
    quality_errors: list[str] = []
    total = len(entries)

    for index, (entry, path) in enumerate(entries, start=1):
        actual_hash = _sha256(path)
        actual_hashes.append(actual_hash)

        if actual_hash != str(entry.get("sha256") or ""):
            chapter_errors.append(f"{path.name}: SHA-256 mismatch")
            continue

        expected_tags = {
            str(key): str(value)
            for key, value in (entry.get("writtenTags") or {}).items()
        }

        try:
            verify_metadata(
                path,
                expected_tags,
                expected_cover_sha256=cover_sha,
            )
        except (MetadataIOError, MultiFileMetadataError) as exc:
            chapter_errors.append(f"{path.name}: {exc}")

        quality = inspect_actual_quality(path)
        if quality.codec is None or quality.lossless is None:
            quality_errors.append(path.name)

    checks.append(
        ReadinessCheck(
            "chapters-hash-metadata-artwork",
            not chapter_errors,
            (
                f"SHA-256, canonical metadata, track order, and embedded "
                f"cover verified for all {total} files."
                if not chapter_errors
                else "; ".join(chapter_errors[:3])
            ),
        )
    )

    checks.append(
        ReadinessCheck(
            "actual-codec-all",
            not quality_errors,
            (
                f"Actual codec/quality classified for all {total} files."
                if not quality_errors
                else f"Unclassified chapters: {', '.join(quality_errors)}"
            ),
        )
    )

    edition_sha = _edition_sha(actual_hashes)
    recorded_edition = str(
        (report.get("audio") or {}).get("editionSha256")
        or metadata.get("editionSha256")
        or ""
    )
    checks.append(
        ReadinessCheck(
            "edition-sha256",
            recorded_edition == edition_sha,
            (
                f"Ordered edition SHA-256 verified: {edition_sha}"
                if recorded_edition == edition_sha
                else f"Edition SHA-256 mismatch; actual={edition_sha}."
            ),
        )
    )

    destination = report.get("plannedDestination")
    checks.append(
        ReadinessCheck(
            "planned-destination",
            bool(destination),
            (
                f"Planned destination recorded: {destination}"
                if destination
                else "Planned destination is missing."
            ),
        )
    )

    untouched = not bool(report.get("finalLibraryModified"))
    checks.append(
        ReadinessCheck(
            "final-library-untouched",
            untouched,
            "Final library is still untouched."
            if untouched
            else "Report says final library has already been modified.",
        )
    )

    ready = all(check.passed for check in checks)

    readiness_path = job_dir / "readiness-report.json"
    _write_json_atomic(
        readiness_path,
        {
            "schemaVersion": 3,
            "jobId": report.get("jobId"),
            "status": "ready-for-placement" if ready else "not-ready",
            "mode": "multi-file",
            "audioFileCount": total,
            "audioPaths": [str(path) for _, path in entries],
            "editionSha256": edition_sha,
            "coverPath": str(cover),
            "coverSha256": cover_sha,
            "plannedDestination": destination,
            "finalLibraryModified": not untouched,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "detail": check.detail,
                }
                for check in checks
            ],
        },
    )

    return MultiReadinessResult(
        job_dir,
        ready,
        tuple(checks),
        total,
        edition_sha,
        readiness_path,
    )
