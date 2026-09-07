from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import runtime_root


class StagingDiscardError(RuntimeError):
    """A staged job could not be discarded safely."""


@dataclass(frozen=True)
class StagingDiscardPreview:
    job_dir: Path
    job_id: str
    report_path: Path
    report_kind: str
    media_type: str
    status: str
    library_modified: bool
    completion_receipt_path: Path
    completion_receipt_exists: bool
    discard_receipt_path: Path
    staging_size_bytes: int
    file_count: int
    discard_allowed: bool
    blocked_reasons: tuple[str, ...]


@dataclass(frozen=True)
class StagingDiscardResult:
    job_id: str
    removed_job_dir: Path
    receipt_path: Path
    media_type: str
    status_at_discard: str
    staging_size_bytes: int
    file_count: int
    discarded_at: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StagingDiscardError(f"Could not read JSON report {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise StagingDiscardError(f"JSON report must contain an object: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _read_json(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_receipt_name(job_id: str) -> str:
    safe = "".join(
        ch if ch.isalnum() or ch in "-._" else "-" for ch in job_id
    ).strip("-._")
    if not safe:
        raise StagingDiscardError("Job ID cannot be converted to a safe receipt filename.")
    return safe


def _completion_receipt_path(job_id: str) -> Path:
    return runtime_root() / "state" / "completed" / f"{_safe_receipt_name(job_id)}.json"


def _discard_receipt_path(job_id: str) -> Path:
    return runtime_root() / "state" / "discarded" / f"{_safe_receipt_name(job_id)}.json"


def _directory_stats(path: Path) -> tuple[int, int]:
    size = 0
    count = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            count += 1
            try:
                size += entry.stat().st_size
            except OSError as exc:
                raise StagingDiscardError(f"Could not stat staged file {entry}: {exc}") from exc
    return size, count


def _resolve_job(job: Path) -> Path:
    staging_root = (runtime_root() / "staging").resolve()
    raw = Path(job)
    candidate = raw.resolve() if raw.is_absolute() else (staging_root / raw).resolve()

    if candidate == staging_root:
        raise StagingDiscardError("The staging root itself can never be discarded.")
    if not candidate.is_relative_to(staging_root):
        raise StagingDiscardError(
            f"Discard target resolves outside Mnemosyne staging: {candidate}"
        )
    if candidate.parent != staging_root:
        raise StagingDiscardError(
            "Discard target must be one direct staging job directory, not a nested path."
        )
    if not candidate.is_dir():
        raise StagingDiscardError(f"Staging job directory does not exist: {candidate}")
    return candidate


def _load_fetch_report(job_dir: Path) -> tuple[Path, str, dict[str, Any]]:
    candidates = [
        (job_dir / "ebook-fetch-report.json", "ebook"),
        (job_dir / "fetch-report.json", "audio"),
    ]
    found = [(path, kind) for path, kind in candidates if path.is_file()]
    if not found:
        raise StagingDiscardError(
            "No recognized Mnemosyne fetch provenance report exists in the staging job."
        )
    if len(found) != 1:
        raise StagingDiscardError(
            "Staging job is ambiguous because multiple recognized fetch reports exist."
        )
    path, kind = found[0]
    return path, kind, _read_json(path)


def _media_type(report: dict[str, Any], report_kind: str) -> str:
    direct = str(report.get("mediaType") or "").strip()
    if direct:
        return direct
    media = report.get("media")
    if isinstance(media, dict):
        value = str(media.get("type") or "").strip()
        if value:
            return value
    return "ebook" if report_kind == "ebook" else "unknown"


def preview_staging_discard(job: Path) -> StagingDiscardPreview:
    job_dir = _resolve_job(job)
    report_path, report_kind, report = _load_fetch_report(job_dir)

    job_id = str(report.get("jobId") or "").strip()
    if not job_id:
        raise StagingDiscardError(f"{report_path.name} does not contain a job ID.")
    if job_dir.name != job_id:
        raise StagingDiscardError(
            "Staging directory identity does not exactly match provenance job ID."
        )

    status = str(report.get("status") or "unknown").strip() or "unknown"
    media_type = _media_type(report, report_kind)
    library_modified = bool(report.get("finalLibraryModified")) or bool(
        report.get("finalPlacement")
    )

    completion_receipt = _completion_receipt_path(job_id)
    completion_exists = completion_receipt.exists()
    if completion_exists:
        completion = _read_json(completion_receipt)
        if str(completion.get("jobId") or "").strip() != job_id:
            raise StagingDiscardError(f"Completion receipt collision: {completion_receipt}")

    discard_receipt = _discard_receipt_path(job_id)
    if discard_receipt.exists():
        prior = _read_json(discard_receipt)
        if str(prior.get("jobId") or "").strip() != job_id:
            raise StagingDiscardError(f"Discard receipt collision: {discard_receipt}")
        if (
            str(prior.get("status") or "") == "discarded-staging-removed"
            or bool((prior.get("retention") or {}).get("stagingRemoved"))
        ):
            raise StagingDiscardError(
                "Discard receipt says staging was already removed, but the job still exists."
            )

    blocked: list[str] = []
    if library_modified:
        blocked.append(
            "Final-library mutation is recorded; use the normal completion/cleanup lifecycle."
        )
    if status == "complete" or "placed" in status.lower():
        blocked.append(
            f"Job status is {status!r}; discard is only for unplaced, incomplete staging."
        )
    if completion_exists:
        blocked.append(
            "A completion receipt already exists; use lifecycle cleanup instead of discard."
        )

    size, count = _directory_stats(job_dir)
    return StagingDiscardPreview(
        job_dir=job_dir,
        job_id=job_id,
        report_path=report_path,
        report_kind=report_kind,
        media_type=media_type,
        status=status,
        library_modified=library_modified,
        completion_receipt_path=completion_receipt,
        completion_receipt_exists=completion_exists,
        discard_receipt_path=discard_receipt,
        staging_size_bytes=size,
        file_count=count,
        discard_allowed=not blocked,
        blocked_reasons=tuple(blocked),
    )


def apply_staging_discard(job: Path, *, confirm_job_id: str) -> StagingDiscardResult:
    preview = preview_staging_discard(job)
    if not preview.discard_allowed:
        raise StagingDiscardError(
            "Staging discard is blocked: " + "; ".join(preview.blocked_reasons)
        )
    if confirm_job_id != preview.job_id:
        raise StagingDiscardError(
            "Destructive discard confirmation does not exactly match the job ID."
        )

    report = _read_json(preview.report_path)
    authorized_at = datetime.now(timezone.utc).isoformat()
    receipt = {
        "schemaVersion": 1,
        "jobId": preview.job_id,
        "status": "discard-authorized",
        "authorizedAt": authorized_at,
        "discardedAt": None,
        "mediaType": preview.media_type,
        "statusAtDiscard": preview.status,
        "reportKind": preview.report_kind,
        "reportName": preview.report_path.name,
        "source": report.get("source"),
        "work": report.get("work"),
        "media": report.get("media"),
        "plannedDestination": report.get("plannedDestination"),
        "selection": report.get("selection"),
        "verification": report.get("verification"),
        "fetchReportSnapshot": report,
        "retention": {
            "stagingRemoved": False,
            "stagingPath": str(preview.job_dir),
            "stagingSizeBytes": preview.staging_size_bytes,
            "stagingFileCount": preview.file_count,
            "finalLibraryModified": False,
        },
    }

    # Durable receipt is written and re-read before destructive deletion begins.
    _write_json_atomic(preview.discard_receipt_path, receipt)
    archived = _read_json(preview.discard_receipt_path)
    if archived.get("jobId") != preview.job_id:
        raise StagingDiscardError("Discard receipt failed job-ID verification.")
    if archived.get("status") != "discard-authorized":
        raise StagingDiscardError("Discard receipt failed authorization-state verification.")
    if bool((archived.get("retention") or {}).get("stagingRemoved")):
        raise StagingDiscardError("Discard receipt has an invalid pre-deletion state.")

    try:
        shutil.rmtree(preview.job_dir)
    except OSError as exc:
        failed = _read_json(preview.discard_receipt_path)
        failed["status"] = "discard-failed"
        failed["failedAt"] = datetime.now(timezone.utc).isoformat()
        failed["failure"] = str(exc)
        failed.setdefault("retention", {})["stagingRemoved"] = False
        _write_json_atomic(preview.discard_receipt_path, failed)
        raise StagingDiscardError(
            f"Staging deletion failed; durable failure provenance was retained: {exc}"
        ) from exc

    if preview.job_dir.exists():
        failed = _read_json(preview.discard_receipt_path)
        failed["status"] = "discard-failed"
        failed["failedAt"] = datetime.now(timezone.utc).isoformat()
        failed["failure"] = "Staging directory still exists after deletion."
        failed.setdefault("retention", {})["stagingRemoved"] = False
        _write_json_atomic(preview.discard_receipt_path, failed)
        raise StagingDiscardError("Staging discard did not fully remove the job directory.")

    discarded_at = datetime.now(timezone.utc).isoformat()
    completed = _read_json(preview.discard_receipt_path)
    completed["status"] = "discarded-staging-removed"
    completed["discardedAt"] = discarded_at
    completed.pop("failure", None)
    completed.pop("failedAt", None)
    completed.setdefault("retention", {})["stagingRemoved"] = True
    _write_json_atomic(preview.discard_receipt_path, completed)

    verified = _read_json(preview.discard_receipt_path)
    if verified.get("status") != "discarded-staging-removed":
        raise StagingDiscardError("Final discard receipt failed state verification.")
    if not bool((verified.get("retention") or {}).get("stagingRemoved")):
        raise StagingDiscardError("Final discard receipt failed removal verification.")

    return StagingDiscardResult(
        job_id=preview.job_id,
        removed_job_dir=preview.job_dir,
        receipt_path=preview.discard_receipt_path,
        media_type=preview.media_type,
        status_at_discard=preview.status,
        staging_size_bytes=preview.staging_size_bytes,
        file_count=preview.file_count,
        discarded_at=discarded_at,
    )
