from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .quality import ActualAudioQuality, inspect_actual_quality


class QualityRecoveryError(RuntimeError):
    """A staged quality inspection could not be recovered safely."""


@dataclass(frozen=True)
class QualityRecoveryFile:
    path: Path
    quality: ActualAudioQuality
    recorded_sha256: str
    actual_sha256: str


@dataclass(frozen=True)
class QualityRecoveryPreview:
    job_dir: Path
    files: tuple[QualityRecoveryFile, ...]
    removable_warning_count: int
    preserved_warning_count: int
    ready_to_apply: bool


@dataclass(frozen=True)
class QualityRecoveryResult:
    job_dir: Path
    report_path: Path
    files: tuple[QualityRecoveryFile, ...]
    removed_warning_count: int
    preserved_warning_count: int
    status: str


_WARNING_PREFIX = "Actual audio quality inspection failed;"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualityRecoveryError(f"Could not read JSON report {path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, path)
    except (OSError, json.JSONDecodeError) as exc:
        temporary.unlink(missing_ok=True)
        raise QualityRecoveryError(f"Could not atomically update {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _audio_entries(report: dict[str, Any]) -> list[dict[str, Any]]:
    audio = report.get("audio") or {}
    entries = audio.get("files") or []
    if entries:
        return [entry for entry in entries if isinstance(entry, dict)]
    if audio.get("stagedPath") or audio.get("canonicalStagedName"):
        return [audio]
    raise QualityRecoveryError("Fetch report has no resolvable staged audio entries.")


def _entry_path(job_dir: Path, entry: dict[str, Any]) -> Path:
    staged = entry.get("stagedPath")
    if staged:
        path = Path(str(staged))
        if path.is_file():
            return path

    canonical = entry.get("canonicalStagedName")
    if canonical:
        path = job_dir / str(canonical)
        if path.is_file():
            return path

    raise QualityRecoveryError("Could not resolve a staged audio file from fetch provenance.")


def preview_quality_recovery(job_dir: Path) -> QualityRecoveryPreview:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise QualityRecoveryError(f"Staging job directory does not exist: {job_dir}")

    report_path = job_dir / "fetch-report.json"
    if not report_path.is_file():
        raise QualityRecoveryError(f"fetch-report.json not found: {report_path}")

    report = _read_json(report_path)
    warnings = list(report.get("warnings") or [])
    removable = [w for w in warnings if str(w).startswith(_WARNING_PREFIX)]
    preserved = [w for w in warnings if not str(w).startswith(_WARNING_PREFIX)]

    if not removable:
        raise QualityRecoveryError("No recoverable audio-quality inspection warning is present.")

    recovered: list[QualityRecoveryFile] = []
    for entry in _audio_entries(report):
        path = _entry_path(job_dir, entry)
        recorded_sha256 = str(entry.get("sha256") or "")
        actual_sha256 = _sha256(path)

        if not recorded_sha256 or recorded_sha256 != actual_sha256:
            raise QualityRecoveryError(
                f"Staged audio SHA-256 does not match fetch provenance: {path}"
            )

        quality = inspect_actual_quality(path)
        if quality.inspection_warning is not None or quality.codec is None or quality.lossless is None:
            raise QualityRecoveryError(f"Audio quality is still inconclusive for {path.name}.")

        recovered.append(
            QualityRecoveryFile(
                path=path,
                quality=quality,
                recorded_sha256=recorded_sha256,
                actual_sha256=actual_sha256,
            )
        )

    return QualityRecoveryPreview(
        job_dir=job_dir,
        files=tuple(recovered),
        removable_warning_count=len(removable),
        preserved_warning_count=len(preserved),
        ready_to_apply=bool(recovered),
    )


def _quality_fields(quality: ActualAudioQuality) -> dict[str, Any]:
    return {
        "actualCodec": quality.codec,
        "actualLossless": quality.lossless,
        "actualBitrateBps": quality.bitrate_bps,
        "actualSampleRateHz": quality.sample_rate_hz,
        "actualChannels": quality.channels,
        "actualInspectionSource": quality.inspection_source,
    }


def apply_quality_recovery(job_dir: Path) -> QualityRecoveryResult:
    preview = preview_quality_recovery(job_dir)
    report_path = preview.job_dir / "fetch-report.json"
    report = _read_json(report_path)

    warnings = list(report.get("warnings") or [])
    removed = [w for w in warnings if str(w).startswith(_WARNING_PREFIX)]
    preserved = [w for w in warnings if not str(w).startswith(_WARNING_PREFIX)]

    audio = report.get("audio") or {}
    entries = audio.get("files") or []
    quality_by_path = {str(item.path.resolve()): item.quality for item in preview.files}

    if entries:
        for entry in entries:
            path = _entry_path(preview.job_dir, entry)
            entry.update(_quality_fields(quality_by_path[str(path.resolve())]))
        if len(entries) == 1:
            audio.update(_quality_fields(preview.files[0].quality))
    else:
        audio.update(_quality_fields(preview.files[0].quality))

    report["audio"] = audio
    report["warnings"] = preserved
    report["status"] = "needs-attention" if preserved else "staged-normalized"
    report["schemaVersion"] = max(int(report.get("schemaVersion") or 0), 10)
    report["finalLibraryModified"] = bool(report.get("finalLibraryModified"))

    event = {
        "recoveredAt": datetime.now(timezone.utc).isoformat(),
        "method": "quality-reinspection",
        "files": [
            {
                "path": str(item.path),
                "sha256": item.actual_sha256,
                "codec": item.quality.codec,
                "lossless": item.quality.lossless,
                "bitrateBps": item.quality.bitrate_bps,
                "sampleRateHz": item.quality.sample_rate_hz,
                "channels": item.quality.channels,
                "inspectionSource": item.quality.inspection_source,
            }
            for item in preview.files
        ],
        "removedWarnings": removed,
        "preservedWarnings": preserved,
        "audioModified": False,
        "libraryModified": False,
    }
    report.setdefault("qualityInspectionHistory", []).append(event)
    report["qualityInspectionRecovery"] = {
        "status": "verified",
        "latestEvent": event,
    }

    _write_json_atomic(report_path, report)

    return QualityRecoveryResult(
        job_dir=preview.job_dir,
        report_path=report_path,
        files=preview.files,
        removed_warning_count=len(removed),
        preserved_warning_count=len(preserved),
        status=str(report["status"]),
    )
