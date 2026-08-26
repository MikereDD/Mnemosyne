from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mutagen.mp4 import MP4

from .inspection import _proposed_tags
from .quality import ActualAudioQuality, inspect_actual_quality


class ReadinessError(RuntimeError):
    """Final staged readiness verification could not be completed."""


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ReadinessResult:
    job_dir: Path
    ready: bool
    audio_path: Path
    cover_path: Path | None
    checks: tuple[ReadinessCheck, ...]
    actual_quality: ActualAudioQuality
    audio_sha256: str
    cover_sha256: str | None
    report_path: Path
    readiness_report_path: Path


_MP4_TAG_MAP = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album_artist": "aART",
    "album": "\xa9alb",
    "date": "\xa9day",
    "genre": "\xa9gen",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"Could not read JSON report {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_audio(job_dir: Path, report: dict[str, Any]) -> Path:
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

    raise ReadinessError("Could not resolve the staged canonical audio file.")


def _resolve_cover(job_dir: Path, report: dict[str, Any]) -> Path | None:
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


def _verify_mp4_tags_and_cover(
    audio_path: Path,
    proposed_tags: dict[str, str],
    expected_cover_sha256: str | None,
) -> list[ReadinessCheck]:
    checks: list[ReadinessCheck] = []

    try:
        audio = MP4(audio_path)
    except Exception as exc:
        return [
            ReadinessCheck(
                "container-readable",
                False,
                f"Mutagen could not reopen the staged MP4-family audio: {exc}",
            )
        ]

    checks.append(
        ReadinessCheck(
            "container-readable",
            True,
            "Staged MP4-family audio reopened successfully.",
        )
    )

    tags = audio.tags or {}
    for friendly, expected in proposed_tags.items():
        atom = _MP4_TAG_MAP.get(friendly)
        if atom is None:
            continue

        values = tags.get(atom)
        actual = str(values[0]) if values else None
        checks.append(
            ReadinessCheck(
                f"metadata-{friendly}",
                actual == expected,
                (
                    f"{friendly}={actual!r}"
                    if actual == expected
                    else f"Expected {friendly}={expected!r}, found {actual!r}."
                ),
            )
        )

    covers = tags.get("covr") or []
    if expected_cover_sha256 is None:
        checks.append(
            ReadinessCheck(
                "embedded-cover",
                bool(covers),
                (
                    "Embedded artwork is present."
                    if covers
                    else "No expected cover hash was recorded and no embedded artwork is present."
                ),
            )
        )
    else:
        if not covers:
            checks.append(
                ReadinessCheck(
                    "embedded-cover",
                    False,
                    "Expected embedded artwork is missing.",
                )
            )
        else:
            actual_cover_sha = hashlib.sha256(bytes(covers[0])).hexdigest()
            checks.append(
                ReadinessCheck(
                    "embedded-cover",
                    actual_cover_sha == expected_cover_sha256,
                    (
                        f"Embedded artwork SHA-256 verified: {actual_cover_sha}"
                        if actual_cover_sha == expected_cover_sha256
                        else (
                            "Embedded artwork SHA-256 mismatch: "
                            f"expected {expected_cover_sha256}, found {actual_cover_sha}."
                        )
                    ),
                )
            )

    return checks


def verify_staged_readiness(job_dir: Path) -> ReadinessResult:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise ReadinessError(f"Staging job directory does not exist: {job_dir}")

    report_path = job_dir / "fetch-report.json"
    if not report_path.is_file():
        raise ReadinessError(f"fetch-report.json not found: {report_path}")

    report = _read_json(report_path)
    audio_path = _resolve_audio(job_dir, report)
    cover_path = _resolve_cover(job_dir, report)

    checks: list[ReadinessCheck] = []

    warnings = report.get("warnings") or []
    checks.append(
        ReadinessCheck(
            "warnings-cleared",
            not warnings,
            "No unresolved staging warnings." if not warnings else f"Unresolved warnings: {warnings}",
        )
    )

    source_resolution = report.get("sourceResolution") or {}
    source_resolved = source_resolution.get("status") == "resolved-by-actual-comparison"
    checks.append(
        ReadinessCheck(
            "source-resolved",
            source_resolved,
            (
                "Source quality decision was resolved by actual candidate comparison."
                if source_resolved
                else "Source quality decision has not been formally resolved."
            ),
        )
    )

    audio_sha = _sha256(audio_path)
    recorded_audio_sha = str((report.get("audio") or {}).get("sha256") or "")
    checks.append(
        ReadinessCheck(
            "audio-sha256",
            bool(recorded_audio_sha) and audio_sha == recorded_audio_sha,
            (
                f"Audio SHA-256 verified: {audio_sha}"
                if recorded_audio_sha and audio_sha == recorded_audio_sha
                else f"Audio SHA-256 mismatch or missing report value; actual={audio_sha}."
            ),
        )
    )

    metadata = report.get("metadataNormalization") or {}
    metadata_verified = metadata.get("status") == "verified"
    checks.append(
        ReadinessCheck(
            "metadata-normalization",
            metadata_verified,
            (
                "Metadata normalization report is verified."
                if metadata_verified
                else "Metadata normalization has not reached verified state."
            ),
        )
    )

    expected_cover_sha = metadata.get("embeddedCoverSha256")
    cover_sha: str | None = None
    if cover_path is not None:
        cover_sha = _sha256(cover_path)
        recorded_standalone_cover_sha = str((report.get("cover") or {}).get("sha256") or "")
        checks.append(
            ReadinessCheck(
                "standalone-cover-sha256",
                bool(recorded_standalone_cover_sha) and cover_sha == recorded_standalone_cover_sha,
                (
                    f"Standalone cover SHA-256 verified: {cover_sha}"
                    if recorded_standalone_cover_sha and cover_sha == recorded_standalone_cover_sha
                    else (
                        "Standalone cover SHA-256 mismatch or missing report value; "
                        f"actual={cover_sha}."
                    )
                ),
            )
        )
    else:
        checks.append(
            ReadinessCheck(
                "standalone-cover-sha256",
                False,
                "Canonical standalone cover is missing.",
            )
        )

    proposed = _proposed_tags(report)
    checks.extend(_verify_mp4_tags_and_cover(audio_path, proposed, expected_cover_sha))

    actual_quality = inspect_actual_quality(audio_path)
    checks.append(
        ReadinessCheck(
            "actual-codec-known",
            actual_quality.codec is not None and actual_quality.lossless is not None,
            (
                f"Actual codec={actual_quality.codec}, "
                f"quality={'lossless' if actual_quality.lossless else 'lossy'}."
                if actual_quality.codec is not None and actual_quality.lossless is not None
                else "Actual codec/quality classification is incomplete."
            ),
        )
    )

    planned_destination = report.get("plannedDestination")
    checks.append(
        ReadinessCheck(
            "planned-destination",
            bool(planned_destination),
            (
                f"Planned destination recorded: {planned_destination}"
                if planned_destination
                else "Planned final destination is missing."
            ),
        )
    )

    final_library_modified = bool(report.get("finalLibraryModified"))
    checks.append(
        ReadinessCheck(
            "final-library-untouched",
            not final_library_modified,
            (
                "Final library is still untouched."
                if not final_library_modified
                else "Report indicates the final library has already been modified."
            ),
        )
    )

    ready = all(check.passed for check in checks)

    readiness_report = {
        "schemaVersion": 1,
        "jobId": report.get("jobId"),
        "status": "ready-for-placement" if ready else "not-ready",
        "audioPath": str(audio_path),
        "audioSha256": audio_sha,
        "coverPath": str(cover_path) if cover_path is not None else None,
        "coverSha256": cover_sha,
        "actualCodec": actual_quality.codec,
        "actualLossless": actual_quality.lossless,
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "detail": check.detail,
            }
            for check in checks
        ],
        "plannedDestination": planned_destination,
        "finalLibraryModified": final_library_modified,
    }

    readiness_report_path = job_dir / "readiness-report.json"
    temp_path = job_dir / ".readiness-report.json.tmp"
    temp_path.write_text(
        json.dumps(readiness_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _read_json(temp_path)
    temp_path.replace(readiness_report_path)

    return ReadinessResult(
        job_dir=job_dir,
        ready=ready,
        audio_path=audio_path,
        cover_path=cover_path,
        checks=tuple(checks),
        actual_quality=actual_quality,
        audio_sha256=audio_sha,
        cover_sha256=cover_sha,
        report_path=report_path,
        readiness_report_path=readiness_report_path,
    )
