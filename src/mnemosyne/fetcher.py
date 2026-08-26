from __future__ import annotations

import hashlib
import json
import os
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .models import AcquisitionPlan, MediaCandidate
from .paths import sanitize_component
from .quality import inspect_actual_quality, provider_quality_mismatch


class FetchError(RuntimeError):
    """A staged fetch failed or did not pass validation."""


@dataclass(frozen=True)
class StagedFile:
    path: Path
    expected_size: int | None
    actual_size: int
    sha256: str
    signature: str
    actual_codec: str | None
    actual_lossless: bool | None
    bitrate_bps: int | None
    sample_rate_hz: int | None
    channels: int | None
    quality_warning: str | None


@dataclass(frozen=True)
class StagedCover:
    path: Path
    expected_size: int | None
    actual_size: int
    sha256: str
    signature: str
    width: int | None
    height: int | None


@dataclass(frozen=True)
class FetchResult:
    job_id: str
    staging_dir: Path
    audio: StagedFile
    cover: StagedCover | None
    report_path: Path
    warnings: tuple[str, ...]


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


def _canonical_audio_name(plan: AcquisitionPlan, extension: str) -> str:
    creator = sanitize_component(plan.item.creator or "Unknown Creator")
    title = sanitize_component(plan.item.title)
    date = str(plan.item.year) if plan.item.year else "Unknown"
    return f"{title} - {creator} ({date}){extension.lower()}"


def _validate_audio_signature(path: Path, candidate: MediaCandidate) -> str:
    with path.open("rb") as stream:
        head = stream.read(64)

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


def _jpeg_dimensions(path: Path) -> tuple[int | None, int | None]:
    with path.open("rb") as stream:
        if stream.read(2) != b"\xff\xd8":
            return None, None
        while True:
            marker_start = stream.read(1)
            if not marker_start:
                return None, None
            if marker_start != b"\xff":
                continue
            marker = stream.read(1)
            while marker == b"\xff":
                marker = stream.read(1)
            if not marker:
                return None, None
            marker_code = marker[0]
            if marker_code in {0xD8, 0xD9}:
                continue
            length_bytes = stream.read(2)
            if len(length_bytes) != 2:
                return None, None
            segment_length = struct.unpack(">H", length_bytes)[0]
            if segment_length < 2:
                return None, None
            if marker_code in {
                0xC0, 0xC1, 0xC2, 0xC3,
                0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB,
                0xCD, 0xCE, 0xCF,
            }:
                precision = stream.read(1)
                dimensions = stream.read(4)
                if len(precision) != 1 or len(dimensions) != 4:
                    return None, None
                height, width = struct.unpack(">HH", dimensions)
                return width, height
            stream.seek(segment_length - 2, 1)


def _png_dimensions(path: Path) -> tuple[int | None, int | None]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) >= 24 and header.startswith(b"\x89PNG\r\n\x1a\n") and header[12:16] == b"IHDR":
        width, height = struct.unpack(">II", header[16:24])
        return width, height
    return None, None


def _webp_dimensions(path: Path) -> tuple[int | None, int | None]:
    data = path.read_bytes()[:64]
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None, None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    return None, None


def _validate_cover(path: Path, extension: str) -> tuple[str, int | None, int | None]:
    with path.open("rb") as stream:
        head = stream.read(32)

    lower_head = head.lstrip().lower()
    if lower_head.startswith((b"<!doctype html", b"<html", b"<?xml")):
        raise FetchError("Cover response looks like HTML/XML, not an image.")

    ext = extension.lower()
    if ext in {".jpg", ".jpeg"}:
        if not head.startswith(b"\xff\xd8\xff"):
            raise FetchError("Cover does not match the expected JPEG signature.")
        width, height = _jpeg_dimensions(path)
        return "JPEG", width, height

    if ext == ".png":
        if not head.startswith(b"\x89PNG\r\n\x1a\n"):
            raise FetchError("Cover does not match the expected PNG signature.")
        width, height = _png_dimensions(path)
        return "PNG", width, height

    if ext == ".webp":
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
            raise FetchError("Cover does not match the expected WebP signature.")
        width, height = _webp_dimensions(path)
        return "WebP", width, height

    raise FetchError(f"Unsupported cover image extension: {extension}")


def _stream_download(
    url: str,
    destination: Path,
    *,
    expected_size: int | None,
    timeout: float,
    user_agent: str,
) -> tuple[int, str]:
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
            url,
            timeout=httpx.Timeout(timeout, read=timeout),
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        ) as response:
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()
            if "text/html" in content_type or "application/xhtml" in content_type:
                raise FetchError(
                    f"Server returned non-media content type: {content_type or 'unknown'}"
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

        if expected_size is not None and actual_size != expected_size:
            raise FetchError(
                f"Size verification failed: expected {expected_size} bytes, "
                f"received {actual_size} bytes."
            )

        return actual_size, digest.hexdigest()
    except Exception:
        part_path.unlink(missing_ok=True)
        raise


def _download_audio(
    plan: AcquisitionPlan,
    candidate: MediaCandidate,
    job_dir: Path,
    *,
    timeout: float,
) -> StagedFile:
    source_path = job_dir / Path(candidate.name).name

    actual_size, sha256 = _stream_download(
        candidate.url,
        source_path,
        expected_size=candidate.size,
        timeout=timeout,
        user_agent="Mnemosyne/0.1.0-dev.4",
    )

    part_path = source_path.with_name(source_path.name + ".part")
    signature = _validate_audio_signature(part_path, candidate)

    canonical_path = job_dir / _canonical_audio_name(plan, candidate.extension)
    if canonical_path.exists():
        part_path.unlink(missing_ok=True)
        raise FetchError(f"Canonical staging target already exists: {canonical_path}")

    os.replace(part_path, canonical_path)

    actual = inspect_actual_quality(canonical_path)
    quality_warning = provider_quality_mismatch(
        provider_claimed_lossless=candidate.lossless,
        actual=actual,
    )

    return StagedFile(
        path=canonical_path,
        expected_size=candidate.size,
        actual_size=actual_size,
        sha256=sha256,
        signature=signature,
        actual_codec=actual.codec,
        actual_lossless=actual.lossless,
        bitrate_bps=actual.bitrate_bps,
        sample_rate_hz=actual.sample_rate_hz,
        channels=actual.channels,
        quality_warning=quality_warning,
    )


def _download_cover(
    candidate: MediaCandidate,
    job_dir: Path,
    *,
    timeout: float,
) -> StagedCover:
    extension = candidate.extension.lower()
    temp_destination = job_dir / f"cover-source{extension}"

    actual_size, sha256 = _stream_download(
        candidate.url,
        temp_destination,
        expected_size=candidate.size,
        timeout=timeout,
        user_agent="Mnemosyne/0.1.0-dev.4",
    )

    part_path = temp_destination.with_name(temp_destination.name + ".part")
    signature, width, height = _validate_cover(part_path, extension)

    canonical_extension = ".jpg" if extension in {".jpg", ".jpeg"} else extension
    canonical_path = job_dir / f"cover{canonical_extension}"
    if canonical_path.exists():
        part_path.unlink(missing_ok=True)
        raise FetchError(f"Canonical cover target already exists: {canonical_path}")

    os.replace(part_path, canonical_path)

    return StagedCover(
        path=canonical_path,
        expected_size=candidate.size,
        actual_size=actual_size,
        sha256=sha256,
        signature=signature,
        width=width,
        height=height,
    )


def fetch_plan_to_staging(
    plan: AcquisitionPlan,
    staging_root: Path,
    *,
    timeout: float = 60.0,
) -> FetchResult:
    if len(plan.selected_audio) != 1:
        raise FetchError(
            "Safe Fetch currently requires exactly one selected audio candidate."
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
        audio = _download_audio(plan, candidate, job_dir, timeout=timeout)

        cover: StagedCover | None = None
        if plan.selected_cover is not None:
            cover = _download_cover(plan.selected_cover, job_dir, timeout=timeout)

        warnings = tuple(
            warning
            for warning in (audio.quality_warning,)
            if warning is not None
        )

        status = "needs-attention" if warnings else "staged-normalized"

        report = {
            "schemaVersion": 3,
            "jobId": job_id,
            "status": status,
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
                "providerClaimedLossless": candidate.lossless,
                "expectedSize": candidate.size,
                "actualSize": audio.actual_size,
                "sha256": audio.sha256,
                "signature": audio.signature,
                "actualCodec": audio.actual_codec,
                "actualLossless": audio.actual_lossless,
                "actualBitrateBps": audio.bitrate_bps,
                "actualSampleRateHz": audio.sample_rate_hz,
                "actualChannels": audio.channels,
                "canonicalStagedName": audio.path.name,
                "stagedPath": str(audio.path),
            },
            "cover": (
                {
                    "sourceName": plan.selected_cover.name,
                    "sourceUrl": plan.selected_cover.url,
                    "expectedSize": plan.selected_cover.size,
                    "actualSize": cover.actual_size,
                    "sha256": cover.sha256,
                    "signature": cover.signature,
                    "width": cover.width,
                    "height": cover.height,
                    "canonicalStagedName": cover.path.name,
                    "stagedPath": str(cover.path),
                }
                if cover is not None and plan.selected_cover is not None
                else None
            ),
            "warnings": list(warnings),
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
            cover=cover,
            report_path=report_path,
            warnings=warnings,
        )
    except Exception:
        try:
            if job_dir.exists() and not any(job_dir.iterdir()):
                job_dir.rmdir()
        except OSError:
            pass
        raise
