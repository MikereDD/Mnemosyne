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


class MultiFileCompletionError(RuntimeError):
    """Multi-file completion or cleanup verification failed safely."""


@dataclass(frozen=True)
class MultiCompletionCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class MultiCompletionPreview:
    job_dir: Path
    destination: Path
    audio_paths: tuple[Path, ...]
    cover_path: Path
    checks: tuple[MultiCompletionCheck, ...]
    edition_sha256: str
    ready_to_complete: bool


@dataclass(frozen=True)
class MultiCompletionResult:
    job_dir: Path
    destination: Path
    audio_paths: tuple[Path, ...]
    cover_path: Path
    edition_sha256: str
    completion_report_path: Path
    fetch_report_path: Path
    completed_at: str


@dataclass(frozen=True)
class MultiCleanupPreview:
    job_dir: Path
    job_id: str
    final_destination: Path
    audio_paths: tuple[Path, ...]
    final_cover: Path
    edition_sha256: str
    receipt_path: Path
    staging_size_bytes: int
    file_count: int


@dataclass(frozen=True)
class MultiCleanupResult:
    job_id: str
    removed_job_dir: Path
    receipt_path: Path
    final_destination: Path
    edition_sha256: str
    final_cover_sha256: str
    staging_size_bytes: int
    file_count: int


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MultiFileCompletionError(
            f"Could not read JSON report {path}: {exc}"
        ) from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{uuid.uuid4().hex[:8]}.tmp"
    )
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _read_json(temporary)
    os.replace(temporary, path)


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


def _directory_stats(path: Path) -> tuple[int, int]:
    size = 0
    count = 0
    for entry in path.rglob("*"):
        if not entry.is_file():
            continue
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
        raise MultiFileCompletionError(
            "Job ID cannot be converted to a safe receipt filename."
        )
    return runtime_root() / "state" / "completed" / f"{safe}.json"


def _resolve_final_audio(
    final: dict[str, Any],
) -> list[tuple[dict[str, Any], Path]]:
    entries = list(final.get("audioFiles") or [])
    expected = int(final.get("audioFileCount") or 0)

    if not entries or expected != len(entries):
        raise MultiFileCompletionError(
            f"Final multi-file provenance count mismatch: "
            f"expected {expected}, found {len(entries)}."
        )

    resolved: list[tuple[dict[str, Any], Path]] = []
    for entry in entries:
        path = Path(str(entry.get("path") or ""))
        if not path.is_file():
            raise MultiFileCompletionError(
                f"Final chapter is missing: {path}"
            )
        resolved.append((entry, path))

    return resolved


def preview_multifile_completion(
    job_dir: Path,
) -> MultiCompletionPreview:
    job_dir = job_dir.resolve()

    fetch_path = job_dir / "fetch-report.json"
    readiness_path = job_dir / "readiness-report.json"
    placement_path = job_dir / "placement-report.json"

    for required in (fetch_path, readiness_path, placement_path):
        if not required.is_file():
            raise MultiFileCompletionError(
                f"Required provenance report is missing: {required}"
            )

    fetch = _read_json(fetch_path)
    readiness = _read_json(readiness_path)
    placement = _read_json(placement_path)
    final = fetch.get("finalPlacement") or {}

    if final.get("mode") != "multi-file":
        raise MultiFileCompletionError(
            "Final placement provenance is not multi-file."
        )

    destination_text = final.get("destination")
    cover_text = final.get("coverPath")

    if not destination_text or not cover_text:
        raise MultiFileCompletionError(
            "Final multi-file placement provenance is incomplete."
        )

    destination = Path(str(destination_text))
    cover = Path(str(cover_text))
    audio_entries = _resolve_final_audio(final)
    audio_paths = tuple(path for _, path in audio_entries)

    checks: list[MultiCompletionCheck] = []

    placed_state = (
        fetch.get("status") == "placed-and-verified"
        and bool(fetch.get("finalLibraryModified"))
    )
    checks.append(
        MultiCompletionCheck(
            "placed-state",
            placed_state,
            (
                "Fetch report records placed-and-verified "
                "with finalLibraryModified=true."
                if placed_state
                else "Fetch report is not in verified placed state."
            ),
        )
    )

    readiness_ok = (
        readiness.get("status") == "ready-for-placement"
        and readiness.get("mode") == "multi-file"
    )
    checks.append(
        MultiCompletionCheck(
            "readiness-provenance",
            readiness_ok,
            (
                "Multi-file readiness certification is present."
                if readiness_ok
                else "Multi-file readiness certification is invalid."
            ),
        )
    )

    placement_ok = (
        placement.get("status") == "placed-and-verified"
        and placement.get("mode") == "multi-file"
    )
    checks.append(
        MultiCompletionCheck(
            "placement-provenance",
            placement_ok,
            (
                "Multi-file placement report is verified."
                if placement_ok
                else "Multi-file placement report is invalid."
            ),
        )
    )

    checks.append(
        MultiCompletionCheck(
            "destination-exists",
            destination.is_dir(),
            (
                f"Final destination exists: {destination}"
                if destination.is_dir()
                else f"Final destination is missing: {destination}"
            ),
        )
    )

    recorded_hashes: list[str] = []
    actual_hashes: list[str] = []
    file_errors: list[str] = []

    for entry, path in audio_entries:
        expected = str(entry.get("sha256") or "")
        actual = _sha256(path)
        recorded_hashes.append(expected)
        actual_hashes.append(actual)
        if not expected or actual != expected:
            file_errors.append(path.name)

    checks.append(
        MultiCompletionCheck(
            "final-audio-files-sha256",
            not file_errors,
            (
                f"Verified SHA-256 for all {len(audio_paths)} final chapters."
                if not file_errors
                else "Final chapter hash mismatch: "
                + ", ".join(file_errors[:3])
            ),
        )
    )

    edition_sha = _edition_sha(actual_hashes)
    expected_edition = str(final.get("editionSha256") or "")
    checks.append(
        MultiCompletionCheck(
            "final-edition-sha256",
            bool(expected_edition) and edition_sha == expected_edition,
            (
                f"Ordered final edition SHA-256 verified: {edition_sha}"
                if expected_edition and edition_sha == expected_edition
                else f"Final edition SHA mismatch/missing; actual={edition_sha}."
            ),
        )
    )

    cover_ok = cover.is_file()
    checks.append(
        MultiCompletionCheck(
            "final-cover-exists",
            cover_ok,
            (
                f"Final cover exists: {cover}"
                if cover_ok
                else f"Final cover is missing: {cover}"
            ),
        )
    )

    if cover_ok:
        actual_cover_sha = _sha256(cover)
        expected_cover_sha = str(final.get("coverSha256") or "")
        checks.append(
            MultiCompletionCheck(
                "final-cover-sha256",
                bool(expected_cover_sha)
                and actual_cover_sha == expected_cover_sha,
                (
                    f"Final cover SHA-256 verified: {actual_cover_sha}"
                    if expected_cover_sha
                    and actual_cover_sha == expected_cover_sha
                    else (
                        "Final cover SHA mismatch/missing; "
                        f"actual={actual_cover_sha}."
                    )
                ),
            )
        )

    checks.append(
        MultiCompletionCheck(
            "staging-retained",
            job_dir.is_dir(),
            "Staging/provenance remains retained.",
        )
    )

    already_complete = fetch.get("status") == "complete"
    checks.append(
        MultiCompletionCheck(
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

    return MultiCompletionPreview(
        job_dir=job_dir,
        destination=destination,
        audio_paths=audio_paths,
        cover_path=cover,
        checks=tuple(checks),
        edition_sha256=edition_sha,
        ready_to_complete=ready,
    )


def apply_multifile_completion(
    job_dir: Path,
) -> MultiCompletionResult:
    preview = preview_multifile_completion(job_dir)

    if not preview.ready_to_complete:
        failed = [
            check.name
            for check in preview.checks
            if not check.passed
        ]
        raise MultiFileCompletionError(
            "Completion certification is blocked by failed checks: "
            + ", ".join(failed)
        )

    fetch_path = preview.job_dir / "fetch-report.json"
    fetch = _read_json(fetch_path)
    completed_at = datetime.now(timezone.utc).isoformat()

    completion_report = {
        "schemaVersion": 2,
        "jobId": fetch.get("jobId"),
        "status": "complete",
        "mode": "multi-file",
        "completedAt": completed_at,
        "destination": str(preview.destination),
        "audioFileCount": len(preview.audio_paths),
        "audioPaths": [str(path) for path in preview.audio_paths],
        "editionSha256": preview.edition_sha256,
        "coverPath": str(preview.cover_path),
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
            "fetchListPruned": False,
        },
    }

    completion_path = preview.job_dir / "completion-report.json"
    _write_json_atomic(completion_path, completion_report)

    fetch["schemaVersion"] = max(
        int(fetch.get("schemaVersion") or 0),
        12,
    )
    fetch["status"] = "complete"
    fetch["completedAt"] = completed_at
    fetch["completion"] = {
        "status": "certified",
        "mode": "multi-file",
        "completionReport": str(completion_path),
        "destination": str(preview.destination),
        "audioFileCount": len(preview.audio_paths),
        "editionSha256": preview.edition_sha256,
        "stagingRetained": True,
        "automaticCleanupPerformed": False,
        "fetchListPruned": False,
    }

    _write_json_atomic(fetch_path, fetch)

    return MultiCompletionResult(
        job_dir=preview.job_dir,
        destination=preview.destination,
        audio_paths=preview.audio_paths,
        cover_path=preview.cover_path,
        edition_sha256=preview.edition_sha256,
        completion_report_path=completion_path,
        fetch_report_path=fetch_path,
        completed_at=completed_at,
    )


def preview_multifile_cleanup(
    job_dir: Path,
) -> MultiCleanupPreview:
    job_dir = job_dir.resolve()

    fetch_path = job_dir / "fetch-report.json"
    completion_path = job_dir / "completion-report.json"
    placement_path = job_dir / "placement-report.json"
    readiness_path = job_dir / "readiness-report.json"

    for required in (
        fetch_path,
        completion_path,
        placement_path,
        readiness_path,
    ):
        if not required.is_file():
            raise MultiFileCompletionError(
                f"Required provenance report is missing: {required}"
            )

    fetch = _read_json(fetch_path)
    completion = _read_json(completion_path)
    placement = _read_json(placement_path)

    if fetch.get("status") != "complete":
        raise MultiFileCompletionError(
            "Only lifecycle-complete staging jobs may be cleaned."
        )

    if (
        completion.get("status") != "complete"
        or completion.get("mode") != "multi-file"
    ):
        raise MultiFileCompletionError(
            "completion-report.json is not complete multi-file provenance."
        )

    if (
        placement.get("status") != "placed-and-verified"
        or placement.get("mode") != "multi-file"
    ):
        raise MultiFileCompletionError(
            "placement-report.json is not verified multi-file provenance."
        )

    job_id = str(fetch.get("jobId") or "")
    if not job_id:
        raise MultiFileCompletionError(
            "fetch-report.json does not contain a job ID."
        )

    final = fetch.get("finalPlacement") or {}
    destination = Path(str(final.get("destination") or ""))
    cover = Path(str(final.get("coverPath") or ""))

    if not destination.is_dir():
        raise MultiFileCompletionError(
            f"Final destination is missing: {destination}"
        )
    if not cover.is_file():
        raise MultiFileCompletionError(
            f"Final cover is missing: {cover}"
        )

    audio_entries = _resolve_final_audio(final)
    audio_paths = tuple(path for _, path in audio_entries)

    hashes: list[str] = []
    for entry, path in audio_entries:
        expected = str(entry.get("sha256") or "")
        actual = _sha256(path)
        if not expected or actual != expected:
            raise MultiFileCompletionError(
                f"Final chapter changed after completion: {path.name}"
            )
        hashes.append(actual)

    edition_sha = _edition_sha(hashes)
    expected_edition = str(final.get("editionSha256") or "")

    if not expected_edition or edition_sha != expected_edition:
        raise MultiFileCompletionError(
            "Final edition changed after completion; cleanup is blocked."
        )

    cover_sha = _sha256(cover)
    expected_cover = str(final.get("coverSha256") or "")

    if not expected_cover or cover_sha != expected_cover:
        raise MultiFileCompletionError(
            "Final cover changed after completion; cleanup is blocked."
        )

    receipt = _receipt_path(job_id)
    if receipt.exists():
        existing = _read_json(receipt)
        if existing.get("jobId") != job_id:
            raise MultiFileCompletionError(
                f"Completion receipt collision: {receipt}"
            )

    size, count = _directory_stats(job_dir)

    return MultiCleanupPreview(
        job_dir=job_dir,
        job_id=job_id,
        final_destination=destination,
        audio_paths=audio_paths,
        final_cover=cover,
        edition_sha256=edition_sha,
        receipt_path=receipt,
        staging_size_bytes=size,
        file_count=count,
    )


def apply_multifile_cleanup(
    job_dir: Path,
    *,
    confirm_job_id: str,
) -> MultiCleanupResult:
    preview = preview_multifile_cleanup(job_dir)

    if confirm_job_id != preview.job_id:
        raise MultiFileCompletionError(
            "Destructive cleanup confirmation does not exactly match job ID."
        )

    fetch = _read_json(preview.job_dir / "fetch-report.json")
    completion = _read_json(
        preview.job_dir / "completion-report.json"
    )
    placement = _read_json(
        preview.job_dir / "placement-report.json"
    )
    readiness = _read_json(
        preview.job_dir / "readiness-report.json"
    )

    final = fetch.get("finalPlacement") or {}
    cover_sha = str(final.get("coverSha256") or "")

    receipt_payload = {
        "schemaVersion": 2,
        "jobId": preview.job_id,
        "status": "complete-staging-removed",
        "mode": "multi-file",
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
            "audioFileCount": len(preview.audio_paths),
            "finalEditionSha256": preview.edition_sha256,
            "finalCoverSha256": cover_sha,
        },
    }

    _write_json_atomic(preview.receipt_path, receipt_payload)
    archived = _read_json(preview.receipt_path)

    summary = archived.get("provenanceSummary") or {}
    if archived.get("jobId") != preview.job_id:
        raise MultiFileCompletionError(
            "Archived receipt failed job-ID verification."
        )
    if summary.get("finalEditionSha256") != preview.edition_sha256:
        raise MultiFileCompletionError(
            "Archived receipt failed edition-hash verification."
        )
    if summary.get("finalCoverSha256") != cover_sha:
        raise MultiFileCompletionError(
            "Archived receipt failed cover-hash verification."
        )

    # One final verification immediately before deleting staging.
    final_entries = _resolve_final_audio(final)
    final_hashes: list[str] = []

    for entry, path in final_entries:
        expected = str(entry.get("sha256") or "")
        actual = _sha256(path)
        if actual != expected:
            raise MultiFileCompletionError(
                f"Final chapter changed immediately before cleanup: "
                f"{path.name}"
            )
        final_hashes.append(actual)

    if _edition_sha(final_hashes) != preview.edition_sha256:
        raise MultiFileCompletionError(
            "Final edition changed immediately before cleanup."
        )

    if _sha256(preview.final_cover) != cover_sha:
        raise MultiFileCompletionError(
            "Final cover changed immediately before cleanup."
        )

    shutil.rmtree(preview.job_dir)

    if preview.job_dir.exists():
        raise MultiFileCompletionError(
            "Staging cleanup did not fully remove the job directory."
        )

    return MultiCleanupResult(
        job_id=preview.job_id,
        removed_job_dir=preview.job_dir,
        receipt_path=preview.receipt_path,
        final_destination=preview.final_destination,
        edition_sha256=preview.edition_sha256,
        final_cover_sha256=cover_sha,
        staging_size_bytes=preview.staging_size_bytes,
        file_count=preview.file_count,
    )
