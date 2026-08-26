from pathlib import Path

import pytest

from mnemosyne.fetcher import FetchError, _validate_signature
from mnemosyne.models import CandidateKind, MediaCandidate


def candidate(extension: str) -> MediaCandidate:
    return MediaCandidate(
        name=f"test{extension}",
        url="https://example.invalid/test",
        extension=extension,
        kind=CandidateKind.AUDIO,
        playable=True,
    )


def test_m4a_signature_accepts_iso_bmff(tmp_path: Path) -> None:
    path = tmp_path / "test.m4a"
    path.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 64)
    assert _validate_signature(path, candidate(".m4a")) == "ISO-BMFF/M4A"


def test_mp3_signature_accepts_id3(tmp_path: Path) -> None:
    path = tmp_path / "test.mp3"
    path.write_bytes(b"ID3\x04\x00\x00" + b"\x00" * 64)
    assert _validate_signature(path, candidate(".mp3")) == "MP3"


def test_html_is_rejected_even_with_audio_extension(tmp_path: Path) -> None:
    path = tmp_path / "fake.m4a"
    path.write_bytes(b"<!doctype html><html><body>error</body></html>")
    with pytest.raises(FetchError, match="HTML/XML"):
        _validate_signature(path, candidate(".m4a"))


def test_wrong_signature_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fake.flac"
    path.write_bytes(b"not a flac file")
    with pytest.raises(FetchError, match="FLAC"):
        _validate_signature(path, candidate(".flac"))
