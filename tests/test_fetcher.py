from pathlib import Path

import pytest

from mnemosyne.fetcher import (
    FetchError,
    _canonical_audio_name,
    _jpeg_dimensions,
    _png_dimensions,
    _validate_audio_signature,
    _validate_cover,
)
from mnemosyne.models import AcquisitionPlan, ArchiveItem, CandidateKind, MediaCandidate, MediaType


def candidate(extension: str) -> MediaCandidate:
    return MediaCandidate(
        name=f"test{extension}",
        url="https://example.invalid/test",
        extension=extension,
        kind=CandidateKind.AUDIO,
        playable=True,
    )


def plan() -> AcquisitionPlan:
    item = ArchiveItem(
        identifier="animal-farm.sna",
        source_url="https://archive.org/details/animal-farm.sna",
        media_type=MediaType.AUDIOBOOK,
        raw_title="Animal Farm - sachnoi.app",
        title="Animal Farm",
        creator="George Orwell",
        year=1945,
    )
    return AcquisitionPlan(
        item=item,
        destination=Path(r"C:\Library\Animal Farm - George Orwell (1945)"),
    )


def test_canonical_audio_name() -> None:
    assert (
        _canonical_audio_name(plan(), ".m4a")
        == "Animal Farm - George Orwell (1945).m4a"
    )


def test_m4a_signature_accepts_iso_bmff(tmp_path: Path) -> None:
    path = tmp_path / "test.m4a"
    path.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 64)
    assert _validate_audio_signature(path, candidate(".m4a")) == "ISO-BMFF/M4A"


def test_mp3_signature_accepts_id3(tmp_path: Path) -> None:
    path = tmp_path / "test.mp3"
    path.write_bytes(b"ID3\x04\x00\x00" + b"\x00" * 64)
    assert _validate_audio_signature(path, candidate(".mp3")) == "MP3"


def test_html_is_rejected_even_with_audio_extension(tmp_path: Path) -> None:
    path = tmp_path / "fake.m4a"
    path.write_bytes(b"<!doctype html><html><body>error</body></html>")
    with pytest.raises(FetchError, match="HTML/XML"):
        _validate_audio_signature(path, candidate(".m4a"))


def test_wrong_signature_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "fake.flac"
    path.write_bytes(b"not a flac file")
    with pytest.raises(FetchError, match="FLAC"):
        _validate_audio_signature(path, candidate(".flac"))


def test_png_cover_signature_and_dimensions(tmp_path: Path) -> None:
    path = tmp_path / "cover.png"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + (600).to_bytes(4, "big")
        + (900).to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
    signature, width, height = _validate_cover(path, ".png")
    assert signature == "PNG"
    assert (width, height) == (600, 900)


def test_html_cover_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cover.jpg"
    path.write_bytes(b"<html>not an image</html>")
    with pytest.raises(FetchError, match="HTML/XML"):
        _validate_cover(path, ".jpg")


def test_invalid_jpeg_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cover.jpg"
    path.write_bytes(b"not jpeg")
    with pytest.raises(FetchError, match="JPEG"):
        _validate_cover(path, ".jpg")
