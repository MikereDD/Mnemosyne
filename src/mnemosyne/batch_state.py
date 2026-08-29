from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import MediaType


class BatchStateError(RuntimeError):
    """Durable batch state could not be read or written safely."""


_RESUMABLE_FETCH_STATUSES = {
    "staged-normalized",
    "needs-attention",
    "tagged-normalized",
    "ready-for-placement",
    "placed-and-verified",
    "complete",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchStateError(f"Could not read batch state JSON {path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
        raise BatchStateError(f"Could not write batch state {path}: {exc}") from exc


def queue_fingerprint(media_type: MediaType, queue_path: Path) -> str:
    try:
        queue_bytes = queue_path.read_bytes()
    except OSError as exc:
        raise BatchStateError(f"Could not fingerprint queue {queue_path}: {exc}") from exc

    digest = hashlib.sha256()
    digest.update(media_type.value.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(queue_path.resolve()).encode("utf-8"))
    digest.update(b"\0")
    digest.update(queue_bytes)
    return digest.hexdigest()


def load_batch_state(
    state_root: Path,
    media_type: MediaType,
    queue_path: Path,
) -> tuple[Path, dict[str, Any]]:
    fingerprint = queue_fingerprint(media_type, queue_path)
    path = state_root / "batches" / f"{media_type.value}-{fingerprint[:16]}.json"

    if path.is_file():
        payload = _read_json(path)
        if payload.get("queueFingerprint") != fingerprint:
            raise BatchStateError(
                f"Batch state fingerprint mismatch; refusing unsafe resume: {path}"
            )
        return path, payload

    now = datetime.now(timezone.utc).isoformat()
    return path, {
        "schemaVersion": 1,
        "mediaType": media_type.value,
        "queuePath": str(queue_path.resolve()),
        "queueFingerprint": fingerprint,
        "createdAt": now,
        "updatedAt": now,
        "items": {},
    }


def record_batch_item(
    state_path: Path,
    state: dict[str, Any],
    *,
    identifier: str,
    line_number: int,
    canonical_url: str,
    status: str,
    job_id: str | None,
    staging_dir: Path | None,
    attempts: int,
    error: str | None,
) -> None:
    items = state.setdefault("items", {})
    items[identifier] = {
        "lineNumber": line_number,
        "canonicalUrl": canonical_url,
        "status": status,
        "jobId": job_id,
        "stagingDir": str(staging_dir) if staging_dir is not None else None,
        "attempts": attempts,
        "error": error,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    _write_json_atomic(state_path, state)


def staged_job_is_valid(entry: dict[str, Any]) -> bool:
    staging_text = entry.get("stagingDir")
    if not staging_text:
        return False
    staging_dir = Path(str(staging_text))
    report_path = staging_dir / "fetch-report.json"
    if not staging_dir.is_dir() or not report_path.is_file():
        return False
    try:
        report = _read_json(report_path)
    except BatchStateError:
        return False
    return bool(report.get("jobId"))


def discover_existing_staged_job(
    staging_root: Path,
    identifier: str,
) -> tuple[str, Path, int] | None:
    if not staging_root.is_dir():
        return None

    candidates: list[tuple[float, str, Path, int]] = []

    for job_dir in staging_root.iterdir():
        if not job_dir.is_dir():
            continue
        report_path = job_dir / "fetch-report.json"
        if not report_path.is_file():
            continue

        try:
            report = _read_json(report_path)
        except BatchStateError:
            continue

        source = report.get("source") or {}
        if source.get("identifier") != identifier:
            continue

        status = str(report.get("status") or "")
        if status not in _RESUMABLE_FETCH_STATUSES:
            continue

        job_id = str(report.get("jobId") or "")
        if not job_id:
            continue

        warnings = report.get("warnings") or []
        warning_count = len(warnings) if isinstance(warnings, list) else 0
        try:
            modified = report_path.stat().st_mtime
        except OSError:
            modified = 0.0
        candidates.append((modified, job_id, job_dir, warning_count))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    _, job_id, job_dir, warning_count = candidates[0]
    return job_id, job_dir, warning_count
