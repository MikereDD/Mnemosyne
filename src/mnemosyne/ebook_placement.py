from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import MediaType
from .paths import canonical_destination, sanitize_component


class EbookPlacementError(RuntimeError):
    """Read-only eBook placement planning could not be completed safely."""


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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EbookPlacementError(
            f"Could not read eBook staging report {path}: {exc}"
        ) from exc


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
