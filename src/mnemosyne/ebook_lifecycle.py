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


class EbookLifecycleError(RuntimeError):
    """eBook completion or cleanup could not be completed safely."""


@dataclass(frozen=True)
class EbookCompletionCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class EbookCompletionPreview:
    job_dir: Path
    job_id: str
    destination_dir: Path
    final_file: Path
    staged_file: Path
    sha256: str
    checks: tuple[EbookCompletionCheck, ...]
    ready_to_complete: bool


@dataclass(frozen=True)
class EbookCompletionResult:
    job_dir: Path
    job_id: str
    destination_dir: Path
    final_file: Path
    sha256: str
    completion_report_path: Path
    fetch_report_path: Path
    completed_at: str


@dataclass(frozen=True)
class EbookCleanupPreview:
    job_dir: Path
    job_id: str
    final_destination: Path
    final_file: Path
    final_sha256: str
    receipt_path: Path
    staging_size_bytes: int
    file_count: int


@dataclass(frozen=True)
class EbookCleanupResult:
    job_id: str
    removed_job_dir: Path
    receipt_path: Path
    final_destination: Path
    final_file: Path
    final_sha256: str
    staging_size_bytes: int
    file_count: int


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EbookLifecycleError(f"Could not read JSON report {path}: {exc}") from exc


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
        if temporary.exists():
            temporary.unlink(missing_ok=True)


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
        raise EbookLifecycleError(
            "Job ID cannot be converted to a safe receipt filename."
        )
    return runtime_root() / "state" / "completed" / f"{safe}.json"


def preview_ebook_completion(job_dir: Path) -> EbookCompletionPreview:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise EbookLifecycleError(
            f"Staging job directory does not exist: {job_dir}"
        )

    fetch_path = job_dir / "ebook-fetch-report.json"
    placement_path = job_dir / "ebook-placement-report.json"

    for required in (fetch_path, placement_path):
        if not required.is_file():
            raise EbookLifecycleError(
                f"Required eBook provenance report is missing: {required}"
            )

    fetch = _read_json(fetch_path)
    placement = _read_json(placement_path)

    job_id = str(fetch.get("jobId") or "").strip()
    if not job_id:
        raise EbookLifecycleError(
            "ebook-fetch-report.json does not contain a job ID."
        )

    final = fetch.get("finalPlacement") or {}
    destination_text = final.get("destination")
    file_text = final.get("file")
    expected_sha = str(final.get("sha256") or "").strip().lower()

    if not destination_text or not file_text or not expected_sha:
        raise EbookLifecycleError(
            "Final eBook placement provenance is incomplete."
        )

    staged_name = str(fetch.get("stagedFile") or "").strip()
    if not staged_name:
        raise EbookLifecycleError(
            "Staged eBook filename is missing from provenance."
        )

    destination = Path(str(destination_text))
    final_file = Path(str(file_text))
    staged_file = job_dir / staged_name

    checks: list[EbookCompletionCheck] = []

    placed_state = (
        fetch.get("status") == "placed-and-verified"
        and bool(fetch.get("finalLibraryModified"))
    )
    checks.append(
        EbookCompletionCheck(
            "placed-state",
            placed_state,
            (
                "Fetch report records placed-and-verified with finalLibraryModified=true."
                if placed_state
                else "Fetch report is not in the verified placed state."
            ),
        )
    )

    placement_verified = placement.get("status") == "placed-and-verified"
    checks.append(
        EbookCompletionCheck(
            "placement-provenance",
            placement_verified,
            (
                "eBook placement report is verified."
                if placement_verified
                else "eBook placement report is not verified."
            ),
        )
    )

    checks.append(
        EbookCompletionCheck(
            "destination-exists",
            destination.is_dir(),
            (
                f"Final destination exists: {destination}"
                if destination.is_dir()
                else f"Final destination is missing: {destination}"
            ),
        )
    )

    checks.append(
        EbookCompletionCheck(
            "final-file-exists",
            final_file.is_file(),
            (
                f"Final eBook exists: {final_file}"
                if final_file.is_file()
                else f"Final eBook is missing: {final_file}"
            ),
        )
    )

    if final_file.is_file():
        actual_final_sha = _sha256(final_file)
        final_hash_ok = actual_final_sha == expected_sha
        checks.append(
            EbookCompletionCheck(
                "final-sha256",
                final_hash_ok,
                (
                    f"Final eBook SHA-256 verified: {actual_final_sha}"
                    if final_hash_ok
                    else (
                        "Final eBook SHA-256 mismatch; "
                        f"expected={expected_sha}, actual={actual_final_sha}."
                    )
                ),
            )
        )

    checks.append(
        EbookCompletionCheck(
            "staging-retained",
            staged_file.is_file(),
            (
                f"Staged source remains retained: {staged_file}"
                if staged_file.is_file()
                else f"Staged source is missing: {staged_file}"
            ),
        )
    )

    if staged_file.is_file():
        staged_expected = str(
            (fetch.get("verification") or {}).get("sha256") or ""
        ).strip().lower()
        staged_actual = _sha256(staged_file)
        staged_hash_ok = bool(staged_expected) and staged_actual == staged_expected
        checks.append(
            EbookCompletionCheck(
                "staged-sha256",
                staged_hash_ok,
                (
                    f"Staged eBook SHA-256 re-verified: {staged_actual}"
                    if staged_hash_ok
                    else "Staged eBook SHA-256 mismatch or missing provenance."
                ),
            )
        )

    already_complete = fetch.get("status") == "complete"
    checks.append(
        EbookCompletionCheck(
            "not-already-complete",
            not already_complete,
            (
                "Job has not yet been completion-certified."
                if not already_complete
                else "Job is already marked complete."
            ),
        )
    )

    ready = all(check.passed for check in checks)

    return EbookCompletionPreview(
        job_dir=job_dir,
        job_id=job_id,
        destination_dir=destination,
        final_file=final_file,
        staged_file=staged_file,
        sha256=expected_sha,
        checks=tuple(checks),
        ready_to_complete=ready,
    )


def apply_ebook_completion(job_dir: Path) -> EbookCompletionResult:
    preview = preview_ebook_completion(job_dir)

    if not preview.ready_to_complete:
        failed = [check.name for check in preview.checks if not check.passed]
        raise EbookLifecycleError(
            "eBook completion certification is blocked by failed checks: "
            + ", ".join(failed)
        )

    fetch_path = preview.job_dir / "ebook-fetch-report.json"
    fetch = _read_json(fetch_path)
    completed_at = datetime.now(timezone.utc).isoformat()

    completion_report_path = preview.job_dir / "ebook-completion-report.json"
    completion_report = {
        "schemaVersion": 1,
        "jobId": preview.job_id,
        "status": "complete",
        "completedAt": completed_at,
        "mediaType": "ebook",
        "destination": str(preview.destination_dir),
        "file": str(preview.final_file),
        "sha256": preview.sha256,
        "checks": [
            {
                "name": check.name,
                "passed": check.passed,
                "detail": check.detail,
            }
            for check in preview.checks
        ],
        "retention": {
            "stagingRetained": True,
            "automaticCleanupPerformed": False,
        },
    }

    _write_json_atomic(completion_report_path, completion_report)

    history = fetch.setdefault("completionHistory", [])
    history.append(
        {
            "completedAt": completed_at,
            "completionReport": str(completion_report_path),
            "destination": str(preview.destination_dir),
            "file": str(preview.final_file),
            "sha256": preview.sha256,
            "stagingRetained": True,
        }
    )

    fetch["schemaVersion"] = max(int(fetch.get("schemaVersion") or 0), 3)
    fetch["status"] = "complete"
    fetch["completedAt"] = completed_at
    fetch["completion"] = {
        "status": "certified",
        "completionReport": str(completion_report_path),
        "destination": str(preview.destination_dir),
        "file": str(preview.final_file),
        "sha256": preview.sha256,
        "stagingRetained": True,
        "automaticCleanupPerformed": False,
    }

    _write_json_atomic(fetch_path, fetch)

    return EbookCompletionResult(
        job_dir=preview.job_dir,
        job_id=preview.job_id,
        destination_dir=preview.destination_dir,
        final_file=preview.final_file,
        sha256=preview.sha256,
        completion_report_path=completion_report_path,
        fetch_report_path=fetch_path,
        completed_at=completed_at,
    )


def preview_ebook_cleanup(job_dir: Path) -> EbookCleanupPreview:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise EbookLifecycleError(
            f"Staging job directory does not exist: {job_dir}"
        )

    fetch_path = job_dir / "ebook-fetch-report.json"
    completion_path = job_dir / "ebook-completion-report.json"
    placement_path = job_dir / "ebook-placement-report.json"

    if not fetch_path.is_file():
        raise EbookLifecycleError(
            f"Required eBook provenance report is missing: {fetch_path}"
        )

    fetch = _read_json(fetch_path)

    job_id = str(fetch.get("jobId") or "").strip()
    if not job_id:
        raise EbookLifecycleError(
            "ebook-fetch-report.json does not contain a job ID."
        )

    if fetch.get("status") != "complete":
        raise EbookLifecycleError(
            "Only lifecycle-complete eBook staging jobs may be cleaned."
        )

    for required in (completion_path, placement_path):
        if not required.is_file():
            raise EbookLifecycleError(
                f"Required eBook provenance report is missing: {required}"
            )

    completion = _read_json(completion_path)
    placement = _read_json(placement_path)
    if completion.get("status") != "complete":
        raise EbookLifecycleError(
            "ebook-completion-report.json is not in complete state."
        )
    if placement.get("status") != "placed-and-verified":
        raise EbookLifecycleError(
            "ebook-placement-report.json is not verified."
        )

    final = fetch.get("finalPlacement") or {}
    destination_text = final.get("destination")
    file_text = final.get("file")
    expected_sha = str(final.get("sha256") or "").strip().lower()

    if not destination_text or not file_text or not expected_sha:
        raise EbookLifecycleError(
            "Final eBook placement provenance is incomplete."
        )

    destination = Path(str(destination_text))
    final_file = Path(str(file_text))

    if not destination.is_dir():
        raise EbookLifecycleError(
            f"Final eBook destination is missing: {destination}"
        )
    if not final_file.is_file():
        raise EbookLifecycleError(
            f"Final eBook file is missing: {final_file}"
        )

    actual_sha = _sha256(final_file)
    if actual_sha != expected_sha:
        raise EbookLifecycleError(
            "Final eBook changed after completion; staging cleanup is blocked."
        )

    receipt = _receipt_path(job_id)
    if receipt.exists():
        existing = _read_json(receipt)
        if existing.get("jobId") != job_id:
            raise EbookLifecycleError(
                f"Completion receipt collision: {receipt}"
            )

    size, count = _directory_stats(job_dir)

    return EbookCleanupPreview(
        job_dir=job_dir,
        job_id=job_id,
        final_destination=destination,
        final_file=final_file,
        final_sha256=expected_sha,
        receipt_path=receipt,
        staging_size_bytes=size,
        file_count=count,
    )


def apply_ebook_cleanup(
    job_dir: Path,
    *,
    confirm_job_id: str,
) -> EbookCleanupResult:
    preview = preview_ebook_cleanup(job_dir)

    if confirm_job_id != preview.job_id:
        raise EbookLifecycleError(
            "Destructive cleanup confirmation does not exactly match the job ID."
        )

    fetch = _read_json(preview.job_dir / "ebook-fetch-report.json")
    completion = _read_json(preview.job_dir / "ebook-completion-report.json")
    placement = _read_json(preview.job_dir / "ebook-placement-report.json")

    receipt_payload = {
        "schemaVersion": 1,
        "jobId": preview.job_id,
        "status": "complete-staging-removed",
        "archivedAt": datetime.now(timezone.utc).isoformat(),
        "mediaType": "ebook",
        "source": fetch.get("source"),
        "work": fetch.get("work"),
        "selection": fetch.get("selection"),
        "verification": fetch.get("verification"),
        "finalPlacement": fetch.get("finalPlacement"),
        "completion": fetch.get("completion"),
        "retention": {
            "stagingRemoved": True,
            "stagingPath": str(preview.job_dir),
            "stagingSizeBytes": preview.staging_size_bytes,
            "stagingFileCount": preview.file_count,
        },
        "provenanceSummary": {
            "fetchSchemaVersion": fetch.get("schemaVersion"),
            "placementStatus": placement.get("status"),
            "completionStatus": completion.get("status"),
            "finalEbookSha256": preview.final_sha256,
        },
    }

    _write_json_atomic(preview.receipt_path, receipt_payload)
    archived = _read_json(preview.receipt_path)

    if archived.get("jobId") != preview.job_id:
        raise EbookLifecycleError(
            "Archived eBook completion receipt failed job-ID verification."
        )
    if (
        (archived.get("provenanceSummary") or {}).get("finalEbookSha256")
        != preview.final_sha256
    ):
        raise EbookLifecycleError(
            "Archived eBook completion receipt failed hash verification."
        )

    if _sha256(preview.final_file) != preview.final_sha256:
        raise EbookLifecycleError(
            "Final eBook changed immediately before cleanup; staging was not deleted."
        )

    shutil.rmtree(preview.job_dir)

    if preview.job_dir.exists():
        raise EbookLifecycleError(
            "eBook staging cleanup did not fully remove the job directory."
        )

    return EbookCleanupResult(
        job_id=preview.job_id,
        removed_job_dir=preview.job_dir,
        receipt_path=preview.receipt_path,
        final_destination=preview.final_destination,
        final_file=preview.final_file,
        final_sha256=preview.final_sha256,
        staging_size_bytes=preview.staging_size_bytes,
        file_count=preview.file_count,
    )
