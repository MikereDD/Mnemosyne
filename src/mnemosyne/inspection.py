from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.mp4 import MP4, MP4Cover

from .config import runtime_root


class InspectionError(RuntimeError):
    """Metadata inspection could not be completed safely."""


@dataclass(frozen=True)
class ChapterInfo:
    index: int
    title: str
    start_seconds: float


@dataclass(frozen=True)
class AudioProperties:
    container: str
    codec: str | None
    duration_seconds: float | None
    bitrate_bps: int | None
    sample_rate_hz: int | None
    channels: int | None


@dataclass(frozen=True)
class MetadataInspection:
    job_dir: Path
    audio_path: Path
    properties: AudioProperties
    existing_tags: dict[str, list[str]]
    embedded_artwork_count: int
    embedded_artwork_formats: list[str]
    chapters: list[ChapterInfo]
    proposed_tags: dict[str, str]
    changes: list[tuple[str, str | None, str]]
    report: dict[str, Any] = field(repr=False)


MP4_TAG_LABELS = {
    "\xa9nam": "title",
    "\xa9ART": "artist",
    "aART": "album_artist",
    "\xa9alb": "album",
    "\xa9day": "date",
    "\xa9gen": "genre",
    "\xa9cmt": "comment",
    "desc": "description",
    "ldes": "long_description",
    "cprt": "copyright",
    "purd": "purchase_date",
    "soar": "artist_sort",
    "sonm": "title_sort",
    "soal": "album_sort",
}


def latest_staging_job(staging_root: Path | None = None) -> Path:
    root = staging_root or (runtime_root() / "staging")
    if not root.exists():
        raise InspectionError(f"Staging root does not exist: {root}")

    candidates = [
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "fetch-report.json").is_file()
    ]
    if not candidates:
        raise InspectionError(f"No completed staging jobs found under: {root}")

    return max(candidates, key=lambda path: (path / "fetch-report.json").stat().st_mtime)


def _read_report(job_dir: Path) -> dict[str, Any]:
    report_path = job_dir / "fetch-report.json"
    if not report_path.is_file():
        raise InspectionError(f"Staging report not found: {report_path}")

    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InspectionError(f"Could not read staging report: {exc}") from exc


def _resolve_audio_path(job_dir: Path, report: dict[str, Any]) -> Path:
    audio = report.get("audio") or {}
    staged_path = audio.get("stagedPath")
    if staged_path:
        path = Path(staged_path)
        if path.is_file():
            return path

    canonical_name = audio.get("canonicalStagedName")
    if canonical_name:
        path = job_dir / str(canonical_name)
        if path.is_file():
            return path

    supported = {".m4a", ".m4b", ".mp3", ".flac", ".ogg", ".opus", ".wav", ".aac"}
    matches = [p for p in job_dir.iterdir() if p.is_file() and p.suffix.lower() in supported]
    if len(matches) == 1:
        return matches[0]

    raise InspectionError("Could not uniquely resolve the staged audio file.")


def _stringify_tag_value(value: Any) -> list[str]:
    if isinstance(value, MP4Cover):
        return [f"<embedded artwork: {len(value)} bytes>"]

    if isinstance(value, bytes):
        return [f"<binary: {len(value)} bytes>"]

    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            if isinstance(item, MP4Cover):
                result.append(f"<embedded artwork: {len(item)} bytes>")
            elif isinstance(item, bytes):
                result.append(f"<binary: {len(item)} bytes>")
            else:
                result.append(str(item))
        return result

    return [str(value)]


def _friendly_tags(audio: Any) -> tuple[dict[str, list[str]], int, list[str]]:
    tags = getattr(audio, "tags", None) or {}
    friendly: dict[str, list[str]] = {}
    artwork_count = 0
    artwork_formats: list[str] = []

    for key, value in tags.items():
        if key == "covr":
            covers = value if isinstance(value, list) else [value]
            artwork_count += len(covers)
            for cover in covers:
                image_format = getattr(cover, "imageformat", None)
                if image_format == MP4Cover.FORMAT_JPEG:
                    artwork_formats.append("JPEG")
                elif image_format == MP4Cover.FORMAT_PNG:
                    artwork_formats.append("PNG")
                else:
                    artwork_formats.append("unknown")
            continue

        label = MP4_TAG_LABELS.get(key, key)
        friendly[label] = _stringify_tag_value(value)

    return friendly, artwork_count, artwork_formats


def _chapters(audio: Any) -> list[ChapterInfo]:
    chapter_data = getattr(audio, "chapters", None)
    if not chapter_data:
        return []

    result: list[ChapterInfo] = []
    for index, chapter in enumerate(chapter_data, start=1):
        title = getattr(chapter, "title", None) or f"Chapter {index}"
        start = getattr(chapter, "start", None)
        try:
            start_seconds = float(start)
        except (TypeError, ValueError):
            start_seconds = 0.0
        result.append(
            ChapterInfo(
                index=index,
                title=str(title),
                start_seconds=start_seconds,
            )
        )
    return result


def _properties(audio: Any, path: Path) -> AudioProperties:
    info = getattr(audio, "info", None)
    if info is None:
        return AudioProperties(
            container=path.suffix.lower().lstrip(".").upper() or "unknown",
            codec=None,
            duration_seconds=None,
            bitrate_bps=None,
            sample_rate_hz=None,
            channels=None,
        )

    codec = getattr(info, "codec", None)
    if codec is None:
        codec = getattr(info, "codec_description", None)

    return AudioProperties(
        container=type(audio).__name__,
        codec=str(codec) if codec else None,
        duration_seconds=float(info.length) if getattr(info, "length", None) is not None else None,
        bitrate_bps=int(info.bitrate) if getattr(info, "bitrate", None) is not None else None,
        sample_rate_hz=int(info.sample_rate) if getattr(info, "sample_rate", None) is not None else None,
        channels=int(info.channels) if getattr(info, "channels", None) is not None else None,
    )


def _first(existing: dict[str, list[str]], key: str) -> str | None:
    values = existing.get(key)
    if not values:
        return None
    return values[0]


def _proposed_tags(report: dict[str, Any]) -> dict[str, str]:
    media = report.get("media") or {}
    media_type = str(media.get("type") or "")
    title = str(media.get("title") or "").strip()
    creator = str(media.get("creator") or "").strip()
    year = media.get("year")

    proposed: dict[str, str] = {}
    if title:
        proposed["title"] = title
    if creator:
        proposed["artist"] = creator
        proposed["album_artist"] = creator
    if title:
        proposed["album"] = title
    if year:
        proposed["date"] = str(year)
    if media_type == "audiobook":
        proposed["genre"] = "Audiobook"

    return proposed


def inspect_staging_job(job_dir: Path) -> MetadataInspection:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise InspectionError(f"Staging job directory does not exist: {job_dir}")

    report = _read_report(job_dir)
    audio_path = _resolve_audio_path(job_dir, report)

    try:
        audio = MutagenFile(audio_path)
    except Exception as exc:
        raise InspectionError(f"Mutagen could not inspect the audio file: {exc}") from exc

    if audio is None:
        raise InspectionError(f"Unsupported or unrecognized audio container: {audio_path}")

    existing_tags, artwork_count, artwork_formats = _friendly_tags(audio)
    proposed = _proposed_tags(report)

    changes: list[tuple[str, str | None, str]] = []
    for key, new_value in proposed.items():
        current = _first(existing_tags, key)
        if current != new_value:
            changes.append((key, current, new_value))

    return MetadataInspection(
        job_dir=job_dir,
        audio_path=audio_path,
        properties=_properties(audio, audio_path),
        existing_tags=existing_tags,
        embedded_artwork_count=artwork_count,
        embedded_artwork_formats=artwork_formats,
        chapters=_chapters(audio),
        proposed_tags=proposed,
        changes=changes,
        report=report,
    )
