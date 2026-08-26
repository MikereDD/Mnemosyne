from pathlib import Path
import pytest
from mutagen.mp4 import MP4Cover
from mnemosyne.metadata_io import MetadataIOError,_mp4_cover_format,cover_mime,metadata_family

@pytest.mark.parametrize(("name","expected"),[("book.m4a","mp4"),("book.m4b","mp4"),("book.mp4","mp4"),("book.mp3","id3"),("book.flac","flac")])
def test_metadata_family_detection(name,expected):assert metadata_family(Path(name))==expected
@pytest.mark.parametrize(("name","expected"),[("cover.jpg","image/jpeg"),("cover.png","image/png"),("cover.webp","image/webp")])
def test_cover_mime_detection(name,expected):assert cover_mime(Path(name))==expected
def test_unsupported_audio_family_is_blocked():
    with pytest.raises(MetadataIOError,match="not implemented"):metadata_family(Path("book.ogg"))
def test_mp4_rejects_webp_embedding():
    with pytest.raises(MetadataIOError,match="JPEG/PNG"):_mp4_cover_format("image/webp")
def test_mp4_jpeg_and_png_cover_types():
    assert _mp4_cover_format("image/jpeg")==MP4Cover.FORMAT_JPEG
    assert _mp4_cover_format("image/png")==MP4Cover.FORMAT_PNG
