import hashlib
import json
from pathlib import Path

import pytest

from mnemosyne.tagging import TaggingError, _cover_format, preview_metadata_normalization
from mutagen.mp4 import MP4Cover


def test_cover_format_jpeg() -> None:
    assert _cover_format(Path("cover.jpg")) == MP4Cover.FORMAT_JPEG


def test_cover_format_png() -> None:
    assert _cover_format(Path("cover.png")) == MP4Cover.FORMAT_PNG


def test_cover_format_rejects_webp() -> None:
    with pytest.raises(TaggingError, match="JPEG/PNG"):
        _cover_format(Path("cover.webp"))


def test_preview_blocks_unresolved_warnings(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    audio = job / "Book.m4a"
    audio.write_bytes(b"fake")

    (job / "fetch-report.json").write_text(
        json.dumps(
            {
                "media": {
                    "type": "audiobook",
                    "title": "Book",
                    "creator": "Author",
                    "year": 2000,
                },
                "audio": {
                    "stagedPath": str(audio),
                    "canonicalStagedName": audio.name,
                },
                "warnings": ["unresolved"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(TaggingError, match="unresolved warnings"):
        preview_metadata_normalization(job)


def test_preview_builds_canonical_audiobook_metadata(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    audio = job / "Animal Farm - George Orwell (1945).m4a"
    audio.write_bytes(b"fake")
    cover = job / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff")

    (job / "fetch-report.json").write_text(
        json.dumps(
            {
                "media": {
                    "type": "audiobook",
                    "title": "Animal Farm",
                    "creator": "George Orwell",
                    "year": 1945,
                },
                "audio": {
                    "stagedPath": str(audio),
                    "canonicalStagedName": audio.name,
                },
                "cover": {
                    "stagedPath": str(cover),
                    "canonicalStagedName": cover.name,
                },
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    preview = preview_metadata_normalization(job)

    assert preview.audio_path == audio
    assert preview.cover_path == cover
    assert preview.proposed_tags == {
        "title": "Animal Farm",
        "artist": "George Orwell",
        "album_artist": "George Orwell",
        "album": "Animal Farm",
        "date": "1945",
        "genre": "Audiobook",
    }
