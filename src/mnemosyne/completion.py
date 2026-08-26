from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CompletionError(RuntimeError):
    """Final acquisition completion certification could not be completed."""


@dataclass(frozen=True)
class CompletionCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class CompletionPreview:
    job_dir: Path
    destination: Path
    audio_path: Path
    cover_path: Path
    checks: tuple[CompletionCheck, ...]
    ready_to_complete: bool


@dataclass(frozen=True)
class CompletionResult:
    job_dir: Path
    destination: Path
    audio_path: Path
    cover_path: Path
    completion_report_path: Path
    fetch_report_path: Path
    completed_at: str


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompletionError(f"Could not read JSON report {path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
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


def preview_completion(job_dir: Path) -> CompletionPreview:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise CompletionError(f"Staging job directory does not exist: {job_dir}")

    fetch_report_path = job_dir / "fetch-report.json"
    placement_report_path = job_dir / "placement-report.json"
    readiness_report_path = job_dir / "readiness-report.json"

    for required in (fetch_report_path, placement_report_path, readiness_report_path):
        if not required.is_file():
            raise CompletionError(f"Required provenance report is missing: {required}")

    fetch = _read_json(fetch_report_path)
    placement = _read_json(placement_report_path)
    readiness = _read_json(readiness_report_path)

    final = fetch.get("finalPlacement") or {}
    destination_text = final.get("destination") or placement.get("destination")
    audio_text = final.get("audioPath") or (placement.get("audio") or {}).get("destination")
    cover_text = final.get("coverPath") or (placement.get("cover") or {}).get("destination")

    if not destination_text or not audio_text or not cover_text:
        raise CompletionError("Final placement provenance is incomplete.")

    destination = Path(str(destination_text))
    audio_path = Path(str(audio_text))
    cover_path = Path(str(cover_text))

    checks: list[CompletionCheck] = []

    checks.append(
        CompletionCheck(
            "placed-state",
            fetch.get("status") == "placed-and-verified"
            and bool(fetch.get("finalLibraryModified")),
            (
                "Fetch report records placed-and-verified with finalLibraryModified=true."
                if fetch.get("status") == "placed-and-verified"
                and bool(fetch.get("finalLibraryModified"))
                else "Fetch report is not in the verified placed state."
            ),
        )
    )

    checks.append(
        CompletionCheck(
            "readiness-provenance",
            readiness.get("status") == "ready-for-placement",
            (
                "Readiness certification is present."
                if readiness.get("status") == "ready-for-placement"
                else "Readiness certification is not valid."
            ),
        )
    )

    checks.append(
        CompletionCheck(
            "placement-provenance",
            placement.get("status") == "placed-and-verified",
            (
                "Placement report is verified."
                if placement.get("status") == "placed-and-verified"
                else "Placement report is not verified."
            ),
        )
    )

    checks.append(
        CompletionCheck(
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
        CompletionCheck(
            "final-audio-exists",
            audio_path.is_file(),
            (
                f"Final audio exists: {audio_path}"
                if audio_path.is_file()
                else f"Final audio is missing: {audio_path}"
            ),
        )
    )

    checks.append(
        CompletionCheck(
            "final-cover-exists",
            cover_path.is_file(),
            (
                f"Final cover exists: {cover_path}"
                if cover_path.is_file()
                else f"Final cover is missing: {cover_path}"
            ),
        )
    )

    if audio_path.is_file():
        actual_audio_sha = _sha256(audio_path)
        expected_audio_sha = str(final.get("audioSha256") or "")
        checks.append(
            CompletionCheck(
                "final-audio-sha256",
                bool(expected_audio_sha) and actual_audio_sha == expected_audio_sha,
                (
                    f"Final audio SHA-256 verified: {actual_audio_sha}"
                    if expected_audio_sha and actual_audio_sha == expected_audio_sha
                    else (
                        "Final audio SHA-256 mismatch or missing provenance; "
                        f"actual={actual_audio_sha}."
                    )
                ),
            )
        )

    if cover_path.is_file():
        actual_cover_sha = _sha256(cover_path)
        expected_cover_sha = str(final.get("coverSha256") or "")
        checks.append(
            CompletionCheck(
                "final-cover-sha256",
                bool(expected_cover_sha) and actual_cover_sha == expected_cover_sha,
                (
                    f"Final cover SHA-256 verified: {actual_cover_sha}"
                    if expected_cover_sha and actual_cover_sha == expected_cover_sha
                    else (
                        "Final cover SHA-256 mismatch or missing provenance; "
                        f"actual={actual_cover_sha}."
                    )
                ),
            )
        )

    checks.append(
        CompletionCheck(
            "staging-retained",
            job_dir.is_dir(),
            "Staging/provenance remains retained."
        )
    )

    already_complete = fetch.get("status") == "complete"
    checks.append(
        CompletionCheck(
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

    return CompletionPreview(
        job_dir=job_dir,
        destination=destination,
        audio_path=audio_path,
        cover_path=cover_path,
        checks=tuple(checks),
        ready_to_complete=ready,
    )


def apply_completion(job_dir: Path) -> CompletionResult:
    preview = preview_completion(job_dir)

    if not preview.ready_to_complete:
        failed = [check.name for check in preview.checks if not check.passed]
        raise CompletionError(
            "Completion certification is blocked by failed checks: "
            + ", ".join(failed)
        )

    fetch_report_path = preview.job_dir / "fetch-report.json"
    fetch = _read_json(fetch_report_path)

    completed_at = datetime.now(timezone.utc).isoformat()

    completion_report = {
        "schemaVersion": 1,
        "jobId": fetch.get("jobId"),
        "status": "complete",
        "completedAt": completed_at,
        "destination": str(preview.destination),
        "audioPath": str(preview.audio_path),
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

    completion_report_path = preview.job_dir / "completion-report.json"
    _write_json_atomic(completion_report_path, completion_report)

    history = fetch.setdefault("completionHistory", [])
    history.append(
        {
            "completedAt": completed_at,
            "completionReport": str(completion_report_path),
            "destination": str(preview.destination),
            "stagingRetained": True,
            "fetchListPruned": False,
        }
    )

    fetch["schemaVersion"] = max(int(fetch.get("schemaVersion") or 0), 7)
    fetch["status"] = "complete"
    fetch["completedAt"] = completed_at
    fetch["completion"] = {
        "status": "certified",
        "completionReport": str(completion_report_path),
        "destination": str(preview.destination),
        "stagingRetained": True,
        "automaticCleanupPerformed": False,
        "fetchListPruned": False,
    }

    _write_json_atomic(fetch_report_path, fetch)

    return CompletionResult(
        job_dir=preview.job_dir,
        destination=preview.destination,
        audio_path=preview.audio_path,
        cover_path=preview.cover_path,
        completion_report_path=completion_report_path,
        fetch_report_path=fetch_report_path,
        completed_at=completed_at,
    )
