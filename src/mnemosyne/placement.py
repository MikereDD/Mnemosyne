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


class PlacementError(RuntimeError):
    """Transactional final-library placement could not be completed safely."""


@dataclass(frozen=True)
class PlacementPreview:
    job_dir: Path
    destination: Path
    audio_source: Path
    cover_source: Path
    audio_destination: Path
    cover_destination: Path
    audio_sha256: str
    cover_sha256: str


@dataclass(frozen=True)
class PlacementResult:
    job_dir: Path
    destination: Path
    audio_path: Path
    cover_path: Path
    audio_sha256: str
    cover_sha256: str
    placement_report_path: Path
    fetch_report_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlacementError(f"Could not read JSON report {path}: {exc}") from exc


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


def _resolve_audio(job_dir: Path, fetch_report: dict[str, Any]) -> Path:
    audio = fetch_report.get("audio") or {}
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

    raise PlacementError("Could not resolve the canonical staged audio file.")


def _resolve_cover(job_dir: Path, fetch_report: dict[str, Any]) -> Path:
    cover = fetch_report.get("cover") or {}
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

    raise PlacementError("Canonical standalone cover is missing.")


def _validate_readiness(job_dir: Path, fetch_report: dict[str, Any]) -> dict[str, Any]:
    readiness_path = job_dir / "readiness-report.json"
    if not readiness_path.is_file():
        raise PlacementError(
            "readiness-report.json is missing. Run `mnemosyne ready` before placement."
        )

    readiness = _read_json(readiness_path)
    if readiness.get("status") != "ready-for-placement":
        raise PlacementError(
            "Latest readiness report is not `ready-for-placement`."
        )

    if readiness.get("jobId") != fetch_report.get("jobId"):
        raise PlacementError(
            "Readiness report job ID does not match fetch-report.json."
        )

    checks = readiness.get("checks") or []
    if not checks or not all(bool(check.get("passed")) for check in checks):
        raise PlacementError(
            "Readiness report contains a failed or missing certification check."
        )

    return readiness


def preview_final_placement(job_dir: Path) -> PlacementPreview:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise PlacementError(f"Staging job directory does not exist: {job_dir}")

    fetch_report_path = job_dir / "fetch-report.json"
    if not fetch_report_path.is_file():
        raise PlacementError(f"fetch-report.json not found: {fetch_report_path}")

    fetch_report = _read_json(fetch_report_path)

    if bool(fetch_report.get("finalLibraryModified")):
        raise PlacementError(
            "This staging job is already recorded as having modified the final library."
        )

    readiness = _validate_readiness(job_dir, fetch_report)

    audio_source = _resolve_audio(job_dir, fetch_report)
    cover_source = _resolve_cover(job_dir, fetch_report)

    destination_text = fetch_report.get("plannedDestination")
    if not destination_text:
        raise PlacementError("Planned final destination is missing from provenance.")

    destination = Path(str(destination_text)).resolve()

    if destination.exists():
        raise PlacementError(
            f"Final destination already exists; refusing to overwrite or merge: {destination}"
        )

    if destination == job_dir or job_dir in destination.parents:
        raise PlacementError("Final destination must not be inside the staging job.")

    audio_sha = _sha256(audio_source)
    expected_audio_sha = str(readiness.get("audioSha256") or "")
    if not expected_audio_sha or audio_sha != expected_audio_sha:
        raise PlacementError(
            "Staged audio changed after readiness certification; SHA-256 mismatch."
        )

    cover_sha = _sha256(cover_source)
    expected_cover_sha = str(readiness.get("coverSha256") or "")
    if not expected_cover_sha or cover_sha != expected_cover_sha:
        raise PlacementError(
            "Standalone cover changed after readiness certification; SHA-256 mismatch."
        )

    return PlacementPreview(
        job_dir=job_dir,
        destination=destination,
        audio_source=audio_source,
        cover_source=cover_source,
        audio_destination=destination / audio_source.name,
        cover_destination=destination / cover_source.name,
        audio_sha256=audio_sha,
        cover_sha256=cover_sha,
    )


def _make_parents_tracking(path: Path) -> list[Path]:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent

    path.mkdir(parents=True, exist_ok=True)
    return list(reversed(missing))


def _cleanup_empty_created_parents(created: list[Path]) -> None:
    for path in reversed(created):
        try:
            path.rmdir()
        except OSError:
            break


def apply_final_placement(job_dir: Path) -> PlacementResult:
    preview = preview_final_placement(job_dir)

    job_dir = preview.job_dir
    fetch_report_path = job_dir / "fetch-report.json"
    fetch_report = _read_json(fetch_report_path)

    destination = preview.destination
    parent = destination.parent
    created_parents = _make_parents_tracking(parent)

    transaction_id = f"placement-{uuid.uuid4().hex[:8]}"
    temporary_dir = parent / f".mnemosyne-{transaction_id}"
    if temporary_dir.exists():
        _cleanup_empty_created_parents(created_parents)
        raise PlacementError(
            f"Placement transaction directory already exists: {temporary_dir}"
        )

    final_created = False
    previous_fetch_report = json.loads(json.dumps(fetch_report))

    try:
        temporary_dir.mkdir(exist_ok=False)

        temp_audio = temporary_dir / preview.audio_source.name
        temp_cover = temporary_dir / preview.cover_source.name

        shutil.copy2(preview.audio_source, temp_audio)
        shutil.copy2(preview.cover_source, temp_cover)

        copied_audio_sha = _sha256(temp_audio)
        if copied_audio_sha != preview.audio_sha256:
            raise PlacementError(
                "Copied final audio failed SHA-256 verification before commit."
            )

        copied_cover_sha = _sha256(temp_cover)
        if copied_cover_sha != preview.cover_sha256:
            raise PlacementError(
                "Copied final cover failed SHA-256 verification before commit."
            )

        # The destination must remain absent right up to the commit boundary.
        if destination.exists():
            raise PlacementError(
                f"Final destination appeared during placement; refusing commit: {destination}"
            )

        # Same-parent directory rename is the commit point.
        os.replace(temporary_dir, destination)
        final_created = True

        final_audio = destination / preview.audio_source.name
        final_cover = destination / preview.cover_source.name

        final_audio_sha = _sha256(final_audio)
        final_cover_sha = _sha256(final_cover)

        if final_audio_sha != preview.audio_sha256:
            raise PlacementError(
                "Final-library audio failed post-placement SHA-256 verification."
            )
        if final_cover_sha != preview.cover_sha256:
            raise PlacementError(
                "Final-library cover failed post-placement SHA-256 verification."
            )

        placed_at = datetime.now(timezone.utc).isoformat()

        placement_report = {
            "schemaVersion": 1,
            "transactionId": transaction_id,
            "jobId": fetch_report.get("jobId"),
            "status": "placed-and-verified",
            "placedAt": placed_at,
            "destination": str(destination),
            "audio": {
                "source": str(preview.audio_source),
                "destination": str(final_audio),
                "sha256": final_audio_sha,
            },
            "cover": {
                "source": str(preview.cover_source),
                "destination": str(final_cover),
                "sha256": final_cover_sha,
            },
            "verification": {
                "preCommitCopyHashes": "passed",
                "postPlacementHashes": "passed",
            },
            "rollback": {
                "mode": "remove-new-destination",
                "overwroteExistingDestination": False,
            },
        }

        placement_report_path = job_dir / "placement-report.json"
        _write_json_atomic(placement_report_path, placement_report)

        history = fetch_report.setdefault("placementHistory", [])
        history.append(
            {
                "transactionId": transaction_id,
                "placedAt": placed_at,
                "destination": str(destination),
                "audioPath": str(final_audio),
                "audioSha256": final_audio_sha,
                "coverPath": str(final_cover),
                "coverSha256": final_cover_sha,
                "placementReport": str(placement_report_path),
                "verification": "passed",
            }
        )

        fetch_report["schemaVersion"] = max(
            int(fetch_report.get("schemaVersion") or 0),
            6,
        )
        fetch_report["status"] = "placed-and-verified"
        fetch_report["finalLibraryModified"] = True
        fetch_report["finalPlacement"] = {
            "status": "verified",
            "transactionId": transaction_id,
            "placedAt": placed_at,
            "destination": str(destination),
            "audioPath": str(final_audio),
            "audioSha256": final_audio_sha,
            "coverPath": str(final_cover),
            "coverSha256": final_cover_sha,
            "placementReport": str(placement_report_path),
        }

        _write_json_atomic(fetch_report_path, fetch_report)

        return PlacementResult(
            job_dir=job_dir,
            destination=destination,
            audio_path=final_audio,
            cover_path=final_cover,
            audio_sha256=final_audio_sha,
            cover_sha256=final_cover_sha,
            placement_report_path=placement_report_path,
            fetch_report_path=fetch_report_path,
        )

    except Exception:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)

        if final_created and destination.exists():
            # This transaction never overwrites an existing destination, so
            # rollback is safely defined as deleting only what this transaction created.
            shutil.rmtree(destination, ignore_errors=True)

        try:
            _write_json_atomic(fetch_report_path, previous_fetch_report)
        except Exception:
            pass

        _cleanup_empty_created_parents(created_parents)
        raise
