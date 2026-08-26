from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import runtime_root


class CleanupError(RuntimeError):
    """Completed-job archival or staging cleanup could not be completed safely."""


@dataclass(frozen=True)
class CleanupPreview:
    job_dir: Path
    job_id: str
    final_destination: Path
    final_audio: Path
    final_cover: Path
    receipt_path: Path
    staging_size_bytes: int
    file_count: int


@dataclass(frozen=True)
class CleanupResult:
    job_id: str
    removed_job_dir: Path
    receipt_path: Path
    final_destination: Path
    final_audio_sha256: str
    final_cover_sha256: str
    staging_size_bytes: int
    file_count: int


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanupError(f"Could not read JSON report {path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _read_json(temporary)
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _directory_stats(path: Path) -> tuple[int, int]:
    size = 0
    count = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            count += 1
            try:
                size += entry.stat().st_size
            except OSError:
                pass
    return size, count


def _receipt_path(job_id: str) -> Path:
    safe = "".join(
        ch if ch.isalnum() or ch in "-._" else "-"
        for ch in job_id
    ).strip("-._")
    if not safe:
        raise CleanupError("Job ID cannot be converted to a safe receipt filename.")
    return runtime_root() / "state" / "completed" / f"{safe}.json"


def preview_cleanup(job_dir: Path) -> CleanupPreview:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise CleanupError(f"Staging job directory does not exist: {job_dir}")

    fetch_path = job_dir / "fetch-report.json"
    completion_path = job_dir / "completion-report.json"
    placement_path = job_dir / "placement-report.json"

    for required in (fetch_path, completion_path, placement_path):
        if not required.is_file():
            raise CleanupError(f"Required provenance report is missing: {required}")

    fetch = _read_json(fetch_path)
    completion = _read_json(completion_path)
    placement = _read_json(placement_path)

    job_id = str(fetch.get("jobId") or "")
    if not job_id:
        raise CleanupError("fetch-report.json does not contain a job ID.")

    if fetch.get("status") != "complete":
        raise CleanupError("Only lifecycle-complete staging jobs may be cleaned.")

    if completion.get("status") != "complete":
        raise CleanupError("completion-report.json is not in complete state.")

    if placement.get("status") != "placed-and-verified":
        raise CleanupError("placement-report.json is not verified.")

    final = fetch.get("finalPlacement") or {}
    destination_text = final.get("destination")
    audio_text = final.get("audioPath")
    cover_text = final.get("coverPath")
    expected_audio_sha = str(final.get("audioSha256") or "")
    expected_cover_sha = str(final.get("coverSha256") or "")

    if not all((destination_text, audio_text, cover_text, expected_audio_sha, expected_cover_sha)):
        raise CleanupError("Final placement provenance is incomplete.")

    destination = Path(str(destination_text))
    audio = Path(str(audio_text))
    cover = Path(str(cover_text))

    if not destination.is_dir():
        raise CleanupError(f"Final destination is missing: {destination}")
    if not audio.is_file():
        raise CleanupError(f"Final audio is missing: {audio}")
    if not cover.is_file():
        raise CleanupError(f"Final cover is missing: {cover}")

    actual_audio_sha = _sha256(audio)
    actual_cover_sha = _sha256(cover)

    if actual_audio_sha != expected_audio_sha:
        raise CleanupError(
            "Final audio changed after completion; staging cleanup is blocked."
        )
    if actual_cover_sha != expected_cover_sha:
        raise CleanupError(
            "Final cover changed after completion; staging cleanup is blocked."
        )

    receipt = _receipt_path(job_id)
    if receipt.exists():
        existing = _read_json(receipt)
        if existing.get("jobId") != job_id:
            raise CleanupError(f"Completion receipt collision: {receipt}")

    size, count = _directory_stats(job_dir)

    return CleanupPreview(
        job_dir=job_dir,
        job_id=job_id,
        final_destination=destination,
        final_audio=audio,
        final_cover=cover,
        receipt_path=receipt,
        staging_size_bytes=size,
        file_count=count,
    )


def apply_cleanup(job_dir: Path, *, confirm_job_id: str) -> CleanupResult:
    preview = preview_cleanup(job_dir)

    if confirm_job_id != preview.job_id:
        raise CleanupError(
            "Destructive cleanup confirmation does not exactly match the job ID."
        )

    fetch = _read_json(preview.job_dir / "fetch-report.json")
    completion = _read_json(preview.job_dir / "completion-report.json")
    placement = _read_json(preview.job_dir / "placement-report.json")
    readiness = _read_json(preview.job_dir / "readiness-report.json")

    final = fetch.get("finalPlacement") or {}
    audio_sha = str(final["audioSha256"])
    cover_sha = str(final["coverSha256"])

    receipt_payload = {
        "schemaVersion": 1,
        "jobId": preview.job_id,
        "status": "complete-staging-removed",
        "archivedAt": datetime.now(timezone.utc).isoformat(),
        "source": fetch.get("source"),
        "media": fetch.get("media"),
        "plannedDestination": fetch.get("plannedDestination"),
        "finalPlacement": final,
        "completion": fetch.get("completion"),
        "retention": {
            "stagingRemoved": True,
            "stagingPath": str(preview.job_dir),
            "stagingSizeBytes": preview.staging_size_bytes,
            "stagingFileCount": preview.file_count,
            "fetchListPruned": False,
        },
        "provenanceSummary": {
            "fetchSchemaVersion": fetch.get("schemaVersion"),
            "readinessStatus": readiness.get("status"),
            "placementStatus": placement.get("status"),
            "completionStatus": completion.get("status"),
            "finalAudioSha256": audio_sha,
            "finalCoverSha256": cover_sha,
        },
    }

    # Durable receipt is written and re-read before staging deletion begins.
    _write_json_atomic(preview.receipt_path, receipt_payload)
    archived = _read_json(preview.receipt_path)

    if archived.get("jobId") != preview.job_id:
        raise CleanupError("Archived completion receipt failed job-ID verification.")
    if (
        (archived.get("provenanceSummary") or {}).get("finalAudioSha256")
        != audio_sha
    ):
        raise CleanupError("Archived completion receipt failed audio-hash verification.")
    if (
        (archived.get("provenanceSummary") or {}).get("finalCoverSha256")
        != cover_sha
    ):
        raise CleanupError("Archived completion receipt failed cover-hash verification.")

    # Final files are re-checked one last time immediately before deleting staging.
    if _sha256(preview.final_audio) != audio_sha:
        raise CleanupError(
            "Final audio changed immediately before cleanup; staging was not deleted."
        )
    if _sha256(preview.final_cover) != cover_sha:
        raise CleanupError(
            "Final cover changed immediately before cleanup; staging was not deleted."
        )

    shutil.rmtree(preview.job_dir)

    if preview.job_dir.exists():
        raise CleanupError(
            "Staging cleanup did not fully remove the job directory."
        )

    return CleanupResult(
        job_id=preview.job_id,
        removed_job_dir=preview.job_dir,
        receipt_path=preview.receipt_path,
        final_destination=preview.final_destination,
        final_audio_sha256=audio_sha,
        final_cover_sha256=cover_sha,
        staging_size_bytes=preview.staging_size_bytes,
        file_count=preview.file_count,
    )
