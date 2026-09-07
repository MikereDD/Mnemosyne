from __future__ import annotations

import json
import os
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .fetcher import FetchError, _stream_download
from .models import AcquisitionPlan, MediaCandidate
from .paths import sanitize_component


@dataclass(frozen=True)
class StagedEbook:
    path: Path
    expected_size: int | None
    actual_size: int
    sha256: str
    signature: str
    source_name: str
    source_url: str


@dataclass(frozen=True)
class EbookFetchResult:
    job_id: str
    staging_dir: Path
    ebook: StagedEbook
    report_path: Path


def _job_id(identifier: str) -> str:
    safe = "".join(
        ch if ch.isalnum() or ch in "-._" else "-"
        for ch in identifier
    ).strip("-._")
    return f"{safe or 'ebook'}-{uuid.uuid4().hex[:8]}"


def _canonical_ebook_name(plan: AcquisitionPlan, candidate: MediaCandidate) -> str:
    creator = sanitize_component(plan.item.creator or "Unknown Author")
    title = sanitize_component(plan.item.title)
    year = str(plan.item.year) if plan.item.year else "Unknown"
    return f"{title} - {creator} ({year}){candidate.extension.lower()}"


def _looks_like_markup(head: bytes) -> bool:
    lower = head.lstrip().lower()
    return lower.startswith((b"<!doctype html", b"<html", b"<?xml"))


def _validate_epub(path: Path) -> str:
    if not zipfile.is_zipfile(path):
        raise FetchError("Downloaded file is not a valid ZIP container for EPUB.")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "mimetype" not in names:
                raise FetchError("EPUB is missing the required mimetype entry.")
            if "META-INF/container.xml" not in names:
                raise FetchError("EPUB is missing META-INF/container.xml.")
            if archive.read("mimetype") != b"application/epub+zip":
                raise FetchError("EPUB mimetype entry is not application/epub+zip.")
    except zipfile.BadZipFile as exc:
        raise FetchError("Downloaded EPUB container is corrupt.") from exc
    return "EPUB/ZIP"


def _validate_palm_ebook(path: Path, extension: str) -> str:
    with path.open("rb") as stream:
        head = stream.read(80)
    if _looks_like_markup(head):
        raise FetchError(f"Downloaded response looks like HTML/XML, not {extension}.")
    if len(head) < 68 or head[60:68] != b"BOOKMOBI":
        raise FetchError(
            f"Downloaded file does not match the PalmDB BOOKMOBI signature expected for {extension}."
        )
    return {
        ".mobi": "MOBI/PalmDB",
        ".azw": "AZW/PalmDB",
        ".azw3": "AZW3/KF8-PalmDB",
    }[extension]


def validate_ebook_format(path: Path, candidate: MediaCandidate) -> str:
    extension = candidate.extension.lower()
    with path.open("rb") as stream:
        head = stream.read(96)

    if not head:
        raise FetchError("Downloaded eBook file is empty.")
    if _looks_like_markup(head):
        raise FetchError("Downloaded response looks like HTML/XML, not an eBook.")

    if extension == ".epub":
        return _validate_epub(path)
    if extension == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise FetchError("Downloaded file does not match the expected PDF signature.")
        return "PDF"
    if extension in {".mobi", ".azw", ".azw3"}:
        return _validate_palm_ebook(path, extension)
    if extension == ".djvu":
        if len(head) < 16 or not head.startswith(b"AT&TFORM"):
            raise FetchError("Downloaded file does not match the expected DjVu signature.")
        if head[12:16] not in {b"DJVU", b"DJVM"}:
            raise FetchError("DjVu FORM type is not DJVU or DJVM.")
        return "DjVu"

    raise FetchError(f"Unsupported eBook extension: {candidate.extension}")


def fetch_ebook_plan_to_staging(
    plan: AcquisitionPlan,
    staging_root: Path,
    *,
    timeout: float = 60.0,
) -> EbookFetchResult:
    candidate = plan.selected_ebook
    if candidate is None:
        raise FetchError("Acquisition plan has no selected eBook edition.")
    if plan.item.creator is None:
        raise FetchError("Cannot stage eBook without a verified author/creator.")
    if plan.item.year is None:
        raise FetchError("Cannot stage eBook without a verified publication/release year.")

    job_id = _job_id(plan.item.identifier)
    job_dir = staging_root / job_id
    if job_dir.exists():
        raise FetchError(f"Staging job already exists: {job_dir}")
    job_dir.mkdir(parents=True, exist_ok=False)

    target = job_dir / _canonical_ebook_name(plan, candidate)

    try:
        actual_size, sha256 = _stream_download(
            candidate.url,
            target,
            expected_size=candidate.size,
            timeout=timeout,
            user_agent="Mnemosyne/0.2.0-dev.1",
        )

        part_path = target.with_name(target.name + ".part")
        signature = validate_ebook_format(part_path, candidate)
        os.replace(part_path, target)

        staged = StagedEbook(
            path=target,
            expected_size=candidate.size,
            actual_size=actual_size,
            sha256=sha256,
            signature=signature,
            source_name=candidate.name,
            source_url=candidate.url,
        )

        report_path = job_dir / "ebook-fetch-report.json"
        report = {
            "schemaVersion": 1,
            "jobId": job_id,
            "status": "staged-verified",
            "mediaType": "ebook",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "source": {
                "provider": "Internet Archive",
                "identifier": plan.item.identifier,
                "itemUrl": plan.item.source_url,
                "fileName": candidate.name,
                "fileUrl": candidate.url,
                "archiveFormat": candidate.archive_format,
                "source": candidate.source,
            },
            "work": {
                "title": plan.item.title,
                "creator": plan.item.creator,
                "year": plan.item.year,
                "series": plan.item.series,
                "seriesIndex": plan.item.series_index,
                "seriesProvenance": plan.item.series_provenance,
                "seriesIndexProvenance": plan.item.series_index_provenance,
            },
            "selection": {
                "editionKey": plan.selected_ebook_edition_key,
                "extension": candidate.extension.lower(),
                "score": candidate.score,
                "reasons": candidate.reasons,
            },
            "verification": {
                "expectedSize": candidate.size,
                "actualSize": actual_size,
                "sha256": sha256,
                "signature": signature,
                "formatVerified": True,
            },
            "stagedFile": target.name,
            "finalLibraryModified": False,
        }
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        return EbookFetchResult(
            job_id=job_id,
            staging_dir=job_dir,
            ebook=staged,
            report_path=report_path,
        )
    except Exception:
        if job_dir.exists():
            for child in job_dir.iterdir():
                child.unlink(missing_ok=True)
            job_dir.rmdir()
        raise
