from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile


@dataclass(frozen=True)
class ActualAudioQuality:
    codec: str | None
    lossless: bool | None
    bitrate_bps: int | None
    sample_rate_hz: int | None
    channels: int | None
    inspection_warning: str | None = None
    inspection_source: str | None = None


def _codec_from_parser(audio: object, path: Path) -> tuple[str | None, bool | None]:
    parser_name = type(audio).__name__.lower()
    suffix = path.suffix.lower()

    if parser_name in {"mp3", "easyid3"} or suffix == ".mp3":
        return "MP3", False
    if parser_name in {"flac"} or suffix == ".flac":
        return "FLAC", True
    if parser_name in {"wave"} or suffix in {".wav", ".wave"}:
        return "PCM/WAVE", True
    if parser_name in {"aiff"} or suffix in {".aif", ".aiff"}:
        return "PCM/AIFF", True
    if parser_name in {"oggopus"} or suffix == ".opus":
        return "Opus", False
    if parser_name in {"oggvobis", "oggvorbis"} or suffix in {".ogg", ".oga"}:
        return "Vorbis", False
    if suffix == ".aac":
        return "AAC", False
    return None, None


def _lossless_from_codec(codec: str | None) -> bool | None:
    if not codec:
        return None
    text = codec.lower()
    if any(token in text for token in ("alac", "flac", "pcm", "wavpack", "ape")):
        return True
    if any(token in text for token in ("aac", "mp3", "opus", "vorbis", "ac3", "eac3", "mp2")):
        return False
    return None


def _int_or_none(value: object) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _inspect_with_ffprobe(path: Path) -> ActualAudioQuality | None:
    executable = shutil.which("ffprobe")
    if executable is None:
        return None

    try:
        completed = subprocess.run(
            [
                executable,
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries",
                "stream=codec_name,codec_long_name,bit_rate,sample_rate,channels",
                "-of", "json",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None

    streams = payload.get("streams") or []
    if not streams or not isinstance(streams[0], dict):
        return None

    stream = streams[0]
    codec = str(stream.get("codec_name") or stream.get("codec_long_name") or "").strip() or None

    return ActualAudioQuality(
        codec=codec,
        lossless=_lossless_from_codec(codec),
        bitrate_bps=_int_or_none(stream.get("bit_rate")),
        sample_rate_hz=_int_or_none(stream.get("sample_rate")),
        channels=_int_or_none(stream.get("channels")),
        inspection_warning=None,
        inspection_source="ffprobe",
    )


def inspect_actual_quality(path: Path) -> ActualAudioQuality:
    mutagen_error: Exception | None = None
    try:
        audio = MutagenFile(path)
    except Exception as exc:
        audio = None
        mutagen_error = exc

    if mutagen_error is not None:
        fallback = _inspect_with_ffprobe(path)
        if fallback is not None:
            return fallback
        return ActualAudioQuality(
            codec=None,
            lossless=None,
            bitrate_bps=None,
            sample_rate_hz=None,
            channels=None,
            inspection_warning=(
                "Actual audio quality inspection failed; the downloaded file "
                "passed container/signature validation but could not be parsed "
                f"for codec details: {mutagen_error}"
            ),
            inspection_source=None,
        )

    if audio is None or getattr(audio, "info", None) is None:
        fallback = _inspect_with_ffprobe(path)
        if fallback is not None:
            return fallback
        return ActualAudioQuality(
            codec=None,
            lossless=None,
            bitrate_bps=None,
            sample_rate_hz=None,
            channels=None,
            inspection_source=None,
        )

    info = audio.info
    codec = getattr(info, "codec", None) or getattr(info, "codec_description", None)
    lossless = _lossless_from_codec(str(codec) if codec else None)

    if codec is None or lossless is None:
        parser_codec, parser_lossless = _codec_from_parser(audio, path)
        if codec is None and parser_codec is not None:
            codec = parser_codec
        if lossless is None and parser_lossless is not None:
            lossless = parser_lossless

    return ActualAudioQuality(
        codec=str(codec) if codec else None,
        lossless=lossless,
        bitrate_bps=_int_or_none(getattr(info, "bitrate", None)),
        sample_rate_hz=_int_or_none(getattr(info, "sample_rate", None)),
        channels=_int_or_none(getattr(info, "channels", None)),
        inspection_warning=None,
        inspection_source="mutagen",
    )


def provider_quality_mismatch(
    *,
    provider_claimed_lossless: bool,
    actual: ActualAudioQuality,
) -> str | None:
    if provider_claimed_lossless and actual.lossless is False:
        return (
            "Provider metadata claimed lossless audio, but the downloaded file "
            f"inspects as lossy codec {actual.codec or 'unknown'}."
        )
    return None
