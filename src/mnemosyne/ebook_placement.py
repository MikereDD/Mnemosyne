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

from .models import MediaType
from .paths import canonical_destination, sanitize_component


class EbookPlacementError(RuntimeError):
    """eBook placement could not be completed safely."""


@dataclass(frozen=True)
class EbookPlacementPreview:
    job_dir: Path
    source_path: Path
    destination_dir: Path
    destination_path: Path
    title: str
    creator: str
    year: int | None
    extension: str
    expected_sha256: str
    actual_sha256: str
    conflict: bool


@dataclass(frozen=True)
class EbookPlacementResult:
    job_dir: Path
    source_path: Path
    destination_dir: Path
    destination_path: Path
    sha256: str
    transaction_id: str
    placement_report_path: Path
    fetch_report_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EbookPlacementError(
            f"Could not read eBook staging report {path}: {exc}"
        ) from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
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


def preview_ebook_placement(
    job_dir: Path,
    library_root: Path,
) -> EbookPlacementPreview:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise EbookPlacementError(
            f"Staging job directory does not exist: {job_dir}"
        )

    report_path = job_dir / "ebook-fetch-report.json"
    if not report_path.is_file():
        raise EbookPlacementError(
            f"ebook-fetch-report.json not found: {report_path}"
        )

    report = _read_json(report_path)

    if report.get("status") != "staged-verified":
        raise EbookPlacementError(
            "eBook staging report is not in `staged-verified` state."
        )

    if bool(report.get("finalLibraryModified")):
        raise EbookPlacementError(
            "This eBook staging job is already recorded as modifying the final library."
        )

    work = report.get("work") or {}
    title = str(work.get("title") or "").strip()
    creator = str(work.get("creator") or "").strip()
    year_value = work.get("year")
    series_value = work.get("series")
    series_index_value = work.get("seriesIndex")
    series_provenance = str(
        work.get("seriesProvenance") or ""
    ).strip()
    series_index_provenance = str(
        work.get("seriesIndexProvenance") or ""
    ).strip()

    series = (
        str(series_value).strip()
        if series_value is not None
        else None
    )
    if series == "":
        series = None

    if series is not None and series_provenance != "verified-override":
        raise EbookPlacementError(
            "eBook series hierarchy is not backed by verified provenance."
        )

    try:
        series_index = (
            float(series_index_value)
            if series_index_value is not None
            else None
        )
    except (TypeError, ValueError) as exc:
        raise EbookPlacementError(
            f"Invalid series index in provenance: {series_index_value!r}"
        ) from exc

    if series_index is not None:
        if series is None:
            raise EbookPlacementError(
                "Series index exists without a verified series name."
            )
        if series_index_provenance != "verified-override":
            raise EbookPlacementError(
                "eBook series index is not backed by verified provenance."
            )
        if series_index < 0:
            raise EbookPlacementError(
                "eBook series index must not be negative."
            )

    if not title:
        raise EbookPlacementError("Verified eBook title is missing from provenance.")
    if not creator:
        raise EbookPlacementError("Verified eBook author/creator is missing from provenance.")

    try:
        year = int(year_value) if year_value is not None else None
    except (TypeError, ValueError) as exc:
        raise EbookPlacementError(
            f"Invalid publication/release year in provenance: {year_value!r}"
        ) from exc

    staged_name = str(report.get("stagedFile") or "").strip()
    if not staged_name:
        raise EbookPlacementError("Staged eBook filename is missing from provenance.")

    source_path = job_dir / staged_name
    if not source_path.is_file():
        raise EbookPlacementError(
            f"Staged eBook file is missing: {source_path}"
        )

    verification = report.get("verification") or {}
    if verification.get("formatVerified") is not True:
        raise EbookPlacementError(
            "Staged eBook does not have a positive actual-format verification."
        )

    expected_sha = str(verification.get("sha256") or "").strip().lower()
    if not expected_sha:
        raise EbookPlacementError(
            "Verified staged SHA-256 is missing from provenance."
        )

    actual_sha = _sha256(source_path)
    if actual_sha.lower() != expected_sha:
        raise EbookPlacementError(
            "Staged eBook changed after fetch verification; SHA-256 mismatch."
        )

    extension = source_path.suffix.lower()
    expected_extension = str(
        (report.get("selection") or {}).get("extension") or ""
    ).strip().lower()

    if not extension:
        raise EbookPlacementError("Staged eBook has no file extension.")
    if expected_extension and extension != expected_extension:
        raise EbookPlacementError(
            "Staged eBook extension no longer matches the selected edition."
        )

    destination_dir = canonical_destination(
        library_root,
        MediaType.EBOOK,
        creator,
        title,
        year,
        series=series,
        series_index=series_index,
    ).resolve()

    canonical_filename = f"{sanitize_component(title)}{extension}"
    destination_path = destination_dir / canonical_filename

    if destination_dir == job_dir or job_dir in destination_dir.parents:
        raise EbookPlacementError(
            "Final eBook destination must not be inside the staging job."
        )

    if destination_dir.exists() or destination_path.exists():
        raise EbookPlacementError(
            "Final eBook destination already exists; refusing overwrite or merge: "
            f"{destination_dir}"
        )

    return EbookPlacementPreview(
        job_dir=job_dir,
        source_path=source_path,
        destination_dir=destination_dir,
        destination_path=destination_path,
        title=title,
        creator=creator,
        year=year,
        extension=extension,
        expected_sha256=expected_sha,
        actual_sha256=actual_sha,
        conflict=False,
    )


def _create_parent_chain(path: Path) -> list[Path]:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent

    path.mkdir(parents=True, exist_ok=True)
    return list(reversed(missing))


def _cleanup_empty_parents(created: list[Path]) -> None:
    for path in reversed(created):
        try:
            path.rmdir()
        except OSError:
            break


def apply_ebook_placement(
    job_dir: Path,
    library_root: Path,
) -> EbookPlacementResult:
    preview = preview_ebook_placement(job_dir, library_root)

    report_path = preview.job_dir / "ebook-fetch-report.json"
    original_report = _read_json(report_path)
    previous_report = json.loads(json.dumps(original_report))

    destination = preview.destination_dir
    parent = destination.parent
    created_parents = _create_parent_chain(parent)

    transaction_id = f"ebook-placement-{uuid.uuid4().hex[:8]}"
    temporary_dir = parent / f".mnemosyne-{transaction_id}"
    placement_report_path = preview.job_dir / "ebook-placement-report.json"

    if temporary_dir.exists():
        _cleanup_empty_parents(created_parents)
        raise EbookPlacementError(
            f"Placement transaction directory already exists: {temporary_dir}"
        )

    destination_created = False
    placement_report_written = False

    try:
        temporary_dir.mkdir(exist_ok=False)
        temporary_file = temporary_dir / preview.destination_path.name

        shutil.copy2(preview.source_path, temporary_file)

        copied_sha = _sha256(temporary_file)
        if copied_sha != preview.actual_sha256:
            raise EbookPlacementError(
                "Copied eBook failed SHA-256 verification before commit."
            )

        if destination.exists():
            raise EbookPlacementError(
                "Final eBook destination appeared during placement; refusing commit: "
                f"{destination}"
            )

        os.replace(temporary_dir, destination)
        destination_created = True

        final_path = destination / preview.destination_path.name
        if not final_path.is_file():
            raise EbookPlacementError(
                "Final eBook file is missing immediately after placement."
            )

        final_sha = _sha256(final_path)
        if final_sha != preview.actual_sha256:
            raise EbookPlacementError(
                "Final-library eBook failed post-placement SHA-256 verification."
            )

        placed_at = datetime.now(timezone.utc).isoformat()
        placement_report = {
            "schemaVersion": 1,
            "transactionId": transaction_id,
            "jobId": original_report.get("jobId"),
            "status": "placed-and-verified",
            "placedAt": placed_at,
            "mediaType": "ebook",
            "source": {
                "stagedPath": str(preview.source_path),
                "sha256": preview.actual_sha256,
            },
            "destination": {
                "directory": str(destination),
                "file": str(final_path),
                "sha256": final_sha,
            },
            "verification": {
                "stagedHashReverified": True,
                "preCommitCopyHash": "passed",
                "postPlacementHash": "passed",
            },
            "rollback": {
                "mode": "remove-new-destination",
                "overwroteExistingDestination": False,
            },
        }
        _write_json_atomic(placement_report_path, placement_report)
        placement_report_written = True

        history = original_report.setdefault("placementHistory", [])
        history.append(
            {
                "transactionId": transaction_id,
                "placedAt": placed_at,
                "destination": str(destination),
                "file": str(final_path),
                "sha256": final_sha,
                "placementReport": str(placement_report_path),
                "verification": "passed",
            }
        )
        original_report["schemaVersion"] = max(
            int(original_report.get("schemaVersion") or 0),
            2,
        )
        original_report["status"] = "placed-and-verified"
        original_report["finalLibraryModified"] = True
        original_report["finalPlacement"] = {
            "status": "verified",
            "transactionId": transaction_id,
            "placedAt": placed_at,
            "destination": str(destination),
            "file": str(final_path),
            "sha256": final_sha,
            "placementReport": str(placement_report_path),
        }

        _write_json_atomic(report_path, original_report)

        return EbookPlacementResult(
            job_dir=preview.job_dir,
            source_path=preview.source_path,
            destination_dir=destination,
            destination_path=final_path,
            sha256=final_sha,
            transaction_id=transaction_id,
            placement_report_path=placement_report_path,
            fetch_report_path=report_path,
        )

    except Exception:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)

        if destination_created and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)

        if placement_report_written and placement_report_path.exists():
            placement_report_path.unlink(missing_ok=True)

        try:
            _write_json_atomic(report_path, previous_report)
        except Exception:
            pass

        _cleanup_empty_parents(created_parents)
        raise
