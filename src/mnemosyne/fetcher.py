from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

import httpx

from .models import AcquisitionPlan, MediaCandidate


class FetchError(RuntimeError):
    """A staged fetch failed or did not pass validation."""


@dataclass(frozen=True)
class StagedFile:
    path: Path
    expected_size: int | None
    actual_size: int
    sha256: str
    signature: str


@dataclass(frozen=True)
class FetchResult:
    job_id: str
    staging_dir: Path
    audio: StagedFile
    report_path: Path


_AUDIO_SIGNATURES = {
    ".flac": ("FLAC", lambda head: head.startswith(b"fLaC")),
    ".wav": ("WAVE", lambda head: len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"),
    ".wave": ("WAVE", lambda head: len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"),
    ".aiff": ("AIFF", lambda head: len(head) >= 12 and head[:4] == b"FORM" and head[8:12] in {b"AIFF", b"AIFC"}),
    ".aif": ("AIFF", lambda head: len(head) >= 12 and head[:4] == b"FORM" and head[8:12] in {b"AIFF", b"AIFC"}),
    ".ogg": ("Ogg", lambda head: head.startswith(b"OggS")),
    ".oga": ("Ogg", lambda head: head.startswith(b"OggS")),
    ".opus": ("Ogg/Opus", lambda head: head.startswith(b"OggS")),
    ".mp3": (
        "MP3",
        lambda head: head.startswith(b"ID3")
        or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0),
    ),
    ".m4a": ("ISO-BMFF/M4A", lambda head: len(head) >= 12 and head[4:8] == b"ftyp"),
    ".m4b": ("ISO-BMFF/M4B", lambda head: len(head) >= 12 and head[4:8] == b"ftyp"),
    ".aac": (
        "AAC",
        lambda head: len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0,
    ),
}


def _job_id(identifier: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in identifier).strip("-._")
    return f"{safe or 'job'}-{uuid.uuid4().hex[:8]}"


def _validate_signature(path: Path, candidate: MediaCandidate) -> str:
    with path.open("rb") as stream:
        head = stream.read(64)

    # Reject common server/error responses before extension-specific checks.
    lower_head = head.lstrip().lower()
    if lower_head.startswith((b"<!doctype html", b"<html", b"<?xml")):
        raise FetchError("Downloaded response looks like HTML/XML, not audio.")

    signature = _AUDIO_SIGNATURES.get(candidate.extension)
    if signature is None:
        if not head:
            raise FetchError("Downloaded file is empty.")
        return "unverified-format"

    label, validator = signature
    if not validator(head):
        raise FetchError(
            f"Downloaded file does not match the expected {label} signature "
            f"for extension {candidate.extension}."
        )
    return label


def _download(
    candidate: MediaCandidate,
    destination: Path,
    *,
    timeout: float = 60.0,
) -> StagedFile:
    part_path = destination.with_name(destination.name + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() or part_path.exists():
        raise FetchError(
            f"Staging target already exists; refusing to overwrite: {destination}"
        )

    digest = hashlib.sha256()
    actual_size = 0

    try:
        with httpx.stream(
            "GET",
            candidate.url,
            timeout=httpx.Timeout(timeout, read=timeout),
            follow_redirects=True,
            headers={"User-Agent": "Mnemosyne/0.1.0-dev.2"},
        ) as response:
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type or "application/xhtml" in content_type:
                raise FetchError(
                    f"Server returned non-audio content type: {content_type or 'unknown'}"
                )

            with part_path.open("xb") as output:
                for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    digest.update(chunk)
                    actual_size += len(chunk)

        if actual_size <= 0:
            raise FetchError("Downloaded file is empty.")

        if candidate.size is not None and actual_size != candidate.size:
            raise FetchError(
                f"Size verification failed: expected {candidate.size} bytes, "
                f"received {actual_size} bytes."
            )

        signature = _validate_signature(part_path, candidate)
        os.replace(part_path, destination)

        return StagedFile(
            path=destination,
            expected_size=candidate.size,
            actual_size=actual_size,
            sha256=digest.hexdigest(),
            signature=signature,
        )
    except Exception:
        part_path.unlink(missing_ok=True)
        raise


def fetch_plan_to_staging(
    plan: AcquisitionPlan,
    staging_root: Path,
    *,
    timeout: float = 60.0,
) -> FetchResult:
    if len(plan.selected_audio) != 1:
        raise FetchError(
            "Safe Fetch v1 requires exactly one selected audio candidate."
        )

    candidate = plan.selected_audio[0]
    if not candidate.playable:
        raise FetchError("Selected candidate is not classified as playable audio.")

    job_id = _job_id(plan.item.identifier)
    job_dir = staging_root / job_id
    if job_dir.exists():
        raise FetchError(f"Generated staging job already exists: {job_dir}")
    job_dir.mkdir(parents=True, exist_ok=False)

    try:
        audio = _download(candidate, job_dir / Path(candidate.name).name, timeout=timeout)

        report = {
            "schemaVersion": 1,
            "jobId": job_id,
            "status": "staged",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "source": {
                "provider": "Internet Archive",
                "identifier": plan.item.identifier,
                "url": plan.item.source_url,
            },
            "media": {
                "type": plan.item.media_type.value,
                "title": plan.item.title,
                "creator": plan.item.creator,
                "year": plan.item.year,
            },
            "plannedDestination": str(plan.destination),
            "audio": {
                "sourceName": candidate.name,
                "sourceUrl": candidate.url,
                "archiveFormat": candidate.archive_format,
                "archiveSource": candidate.source,
                "expectedSize": candidate.size,
                "actualSize": audio.actual_size,
                "sha256": audio.sha256,
                "signature": audio.signature,
                "stagedPath": str(audio.path),
            },
            "finalLibraryModified": False,
        }

        report_path = job_dir / "fetch-report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        return FetchResult(
            job_id=job_id,
            staging_dir=job_dir,
            audio=audio,
            report_path=report_path,
        )
    except Exception:
        # Preserve a failed job directory only if a useful completed artifact exists.
        # Otherwise remove the empty shell to avoid confusing it with a staged job.
        try:
            if job_dir.exists() and not any(job_dir.iterdir()):
                job_dir.rmdir()
        except OSError:
            pass
        raise
