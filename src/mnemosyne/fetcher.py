from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
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
    source_name: str


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
    audio_files: tuple[StagedFile, ...]
    cover: StagedCover | None
    report_path: Path
    warnings: tuple[str, ...]
    multi_file: bool


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


def _chapter_number(candidate: MediaCandidate, fallback: int) -> int:
    stem = Path(candidate.name).stem
    match = re.search(r"(?:^|[_-])(\d{2,3})(?:[_-]|$)", stem)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            pass
    return fallback


def _canonical_chapter_name(candidate: MediaCandidate, index: int) -> str:
    number = _chapter_number(candidate, index)
    return f"{number:02d} - Chapter {number:02d}{candidate.extension.lower()}"


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
                stream.read(1)
                dimensions = stream.read(4)
                if len(dimensions) != 4:
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


def _retry_delay(attempt: int) -> float:
    # Bounded exponential backoff: 1s, 2s, 4s, 8s.
    return float(min(2 ** (attempt - 1), 8))


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429, 500, 502, 503, 504}


def _stream_download(
    url: str,
    destination: Path,
    *,
    expected_size: int | None,
    timeout: float,
    user_agent: str,
    max_attempts: int = 5,
) -> tuple[int, str]:
    part_path = destination.with_name(destination.name + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() or part_path.exists():
        raise FetchError(
            f"Staging target already exists; refusing to overwrite: {destination}"
        )

    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        digest = hashlib.sha256()
        actual_size = 0
        part_path.unlink(missing_ok=True)

        try:
            with httpx.stream(
                "GET",
                url,
                timeout=httpx.Timeout(timeout, read=timeout),
                follow_redirects=True,
                headers={"User-Agent": user_agent},
            ) as response:
                if response.status_code >= 400:
                    if (
                        _is_retryable_status(response.status_code)
                        and attempt < max_attempts
                    ):
                        response.read()
                        time.sleep(_retry_delay(attempt))
                        continue

                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise FetchError(
                            f"Download failed with HTTP {response.status_code} "
                            f"after {attempt} attempt(s): {response.url}"
                        ) from exc

                content_type = response.headers.get("content-type", "").lower()
                if "text/html" in content_type or "application/xhtml" in content_type:
                    raise FetchError(
                        f"Server returned non-media content type: "
                        f"{content_type or 'unknown'}"
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

        except FetchError:
            part_path.unlink(missing_ok=True)
            raise

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ) as exc:
            last_error = exc
            part_path.unlink(missing_ok=True)

            if attempt >= max_attempts:
                raise FetchError(
                    f"Download failed after {max_attempts} attempts due to "
                    f"a transient network error: {url}"
                ) from exc

            time.sleep(_retry_delay(attempt))

        except httpx.HTTPError as exc:
            part_path.unlink(missing_ok=True)
            raise FetchError(f"HTTP download failed: {url}: {exc}") from exc

        except OSError as exc:
            part_path.unlink(missing_ok=True)
            raise FetchError(
                f"Could not write staged download {destination}: {exc}"
            ) from exc

    # Defensive guard; the loop either returns or raises.
    raise FetchError(
        f"Download failed after {max_attempts} attempts: {url}"
    ) from last_error



def _download_audio_candidate(
    plan: AcquisitionPlan,
    candidate: MediaCandidate,
    target: Path,
    *,
    timeout: float,
) -> StagedFile:
    actual_size, sha256 = _stream_download(
        candidate.url,
        target,
        expected_size=candidate.size,
        timeout=timeout,
        user_agent="Mnemosyne/0.2.0-dev.1",
    )

    part_path = target.with_name(target.name + ".part")
    signature = _validate_audio_signature(part_path, candidate)
    os.replace(part_path, target)

    actual = inspect_actual_quality(target)
    quality_warning = provider_quality_mismatch(
        provider_claimed_lossless=candidate.lossless,
        actual=actual,
    )
    if quality_warning is None:
        quality_warning = actual.inspection_warning

    return StagedFile(
        path=target,
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
        source_name=candidate.name,
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
        user_agent="Mnemosyne/0.2.0-dev.1",
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
    if not plan.selected_audio:
        raise FetchError("Safe Fetch requires at least one selected audio candidate.")

    if any(not candidate.playable for candidate in plan.selected_audio):
        raise FetchError("A selected candidate is not classified as playable audio.")

    job_id = _job_id(plan.item.identifier)
    job_dir = staging_root / job_id
    if job_dir.exists():
        raise FetchError(f"Generated staging job already exists: {job_dir}")
    job_dir.mkdir(parents=True, exist_ok=False)

    multi_file = len(plan.selected_audio) > 1

    try:
        staged_audio: list[StagedFile] = []

        if multi_file:
            media_dir = job_dir / "audio"
            media_dir.mkdir(exist_ok=False)

            for index, candidate in enumerate(plan.selected_audio, start=1):
                target = media_dir / _canonical_chapter_name(candidate, index)
                staged_audio.append(
                    _download_audio_candidate(
                        plan,
                        candidate,
                        target,
                        timeout=timeout,
                    )
                )
        else:
            candidate = plan.selected_audio[0]
            target = job_dir / _canonical_audio_name(plan, candidate.extension)
            staged_audio.append(
                _download_audio_candidate(
                    plan,
                    candidate,
                    target,
                    timeout=timeout,
                )
            )

        cover: StagedCover | None = None
        if plan.selected_cover is not None:
            cover = _download_cover(plan.selected_cover, job_dir, timeout=timeout)

        warnings = tuple(
            warning
            for staged in staged_audio
            for warning in (staged.quality_warning,)
            if warning is not None
        )

        status = "needs-attention" if warnings else "staged-normalized"
        primary = staged_audio[0]

        audio_report = {
            "mode": "multi-file" if multi_file else "single-file",
            "fileCount": len(staged_audio),
            "canonicalStagedName": primary.path.name if not multi_file else None,
            "stagedPath": str(primary.path) if not multi_file else None,
            "files": [
                {
                    "index": index,
                    "sourceName": candidate.name,
                    "sourceUrl": candidate.url,
                    "archiveFormat": candidate.archive_format,
                    "archiveSource": candidate.source,
                    "providerClaimedLossless": candidate.lossless,
                    "expectedSize": candidate.size,
                    "actualSize": staged.actual_size,
                    "sha256": staged.sha256,
                    "signature": staged.signature,
                    "actualCodec": staged.actual_codec,
                    "actualLossless": staged.actual_lossless,
                    "actualBitrateBps": staged.bitrate_bps,
                    "actualSampleRateHz": staged.sample_rate_hz,
                    "actualChannels": staged.channels,
                    "canonicalStagedName": staged.path.name,
                    "stagedPath": str(staged.path),
                }
                for index, (candidate, staged) in enumerate(
                    zip(plan.selected_audio, staged_audio),
                    start=1,
                )
            ],
        }

        if not multi_file:
            candidate = plan.selected_audio[0]
            audio_report.update(
                {
                    "sourceName": candidate.name,
                    "sourceUrl": candidate.url,
                    "archiveFormat": candidate.archive_format,
                    "archiveSource": candidate.source,
                    "providerClaimedLossless": candidate.lossless,
                    "expectedSize": candidate.size,
                    "actualSize": primary.actual_size,
                    "sha256": primary.sha256,
                    "signature": primary.signature,
                    "actualCodec": primary.actual_codec,
                    "actualLossless": primary.actual_lossless,
                    "actualBitrateBps": primary.bitrate_bps,
                    "actualSampleRateHz": primary.sample_rate_hz,
                    "actualChannels": primary.channels,
                }
            )

        report = {
            "schemaVersion": 9,
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
            "audioEdition": {
                "selectedEditionKey": plan.selected_edition_key,
                "multiFile": multi_file,
                "fileCount": len(staged_audio),
                "extension": plan.selected_audio[0].extension if plan.selected_audio else None,
            },
            "audio": audio_report,
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
            audio=primary,
            audio_files=tuple(staged_audio),
            cover=cover,
            report_path=report_path,
            warnings=warnings,
            multi_file=multi_file,
        )

    except Exception:
        for path in sorted(job_dir.rglob("*"), reverse=True):
            try:
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            except OSError:
                pass
        try:
            job_dir.rmdir()
        except OSError:
            pass
        raise
