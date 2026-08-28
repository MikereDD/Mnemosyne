from __future__ import annotations

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


def inspect_actual_quality(path: Path) -> ActualAudioQuality:
    audio = MutagenFile(path)
    if audio is None or getattr(audio, "info", None) is None:
        return ActualAudioQuality(
            codec=None,
            lossless=None,
            bitrate_bps=None,
            sample_rate_hz=None,
            channels=None,
        )

    info = audio.info
    codec = getattr(info, "codec", None) or getattr(info, "codec_description", None)
    codec_text = str(codec).lower() if codec else ""

    lossless: bool | None = None

    if codec_text:
        if "alac" in codec_text or "flac" in codec_text or "pcm" in codec_text:
            lossless = True
        elif (
            "mp4a.40" in codec_text
            or "aac" in codec_text
            or "mp3" in codec_text
            or "opus" in codec_text
            or "vorbis" in codec_text
        ):
            lossless = False

    if codec is None or lossless is None:
        parser_codec, parser_lossless = _codec_from_parser(audio, path)
        if codec is None and parser_codec is not None:
            codec = parser_codec
        if lossless is None and parser_lossless is not None:
            lossless = parser_lossless

    return ActualAudioQuality(
        codec=str(codec) if codec else None,
        lossless=lossless,
        bitrate_bps=int(info.bitrate) if getattr(info, "bitrate", None) is not None else None,
        sample_rate_hz=int(info.sample_rate) if getattr(info, "sample_rate", None) is not None else None,
        channels=int(info.channels) if getattr(info, "channels", None) is not None else None,
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
