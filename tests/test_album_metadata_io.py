from types import SimpleNamespace

import pytest

from mnemosyne import metadata_io


@pytest.mark.parametrize("value, expected", [("1", (1, 0)), ("2/11", (2, 11)), ("1/1", (1, 1))])
def test_mp4_numeric_track_disc_values(value, expected):
    assert metadata_io._number_pair(value) == expected


@pytest.mark.parametrize("value", ["0", "2/1", "1/0", "x", "1/2/3", "65536", "1/65536", "-1"])
def test_mp4_invalid_number_is_blocked(value):
    with pytest.raises(metadata_io.MetadataIOError):
        metadata_io._number_pair(value)


def test_mp4_track_and_disc_round_trip_preserves_unrelated_atoms(monkeypatch, tmp_path):
    audio = SimpleNamespace(tags={"----:test:custom": ["preserved"]}, save=lambda: None)
    monkeypatch.setattr(metadata_io, "MP4", lambda path: audio)
    path = tmp_path / "track.m4a"
    metadata_io.write_metadata(path, {"title": "Title", "track": "2/11", "disc": "1/1"}, None)
    assert audio.tags["trkn"] == [(2, 11)]
    assert audio.tags["disk"] == [(1, 1)]
    snapshot = metadata_io.verify_metadata(path, {"title": "Title", "track": "2/11", "disc": "1/1"}, expected_cover_sha256=None)
    assert snapshot.tags["----:test:custom"] == ["preserved"]


def test_disc_number_has_format_specific_maps():
    assert metadata_io._ID3_FRAME_MAP["disc"].__name__ == "TPOS"
    assert metadata_io._FLAC_TAG_MAP["disc"] == "discnumber"


def test_unknown_legacy_id3_frames_cannot_be_silently_lost(monkeypatch, tmp_path):
    audio = SimpleNamespace(tags=SimpleNamespace(unknown_frames=[b"private"], version=(2, 3, 0)))
    monkeypatch.setattr(metadata_io, "MP3", lambda path: audio)
    monkeypatch.setattr(metadata_io, "_assert_id3_preservable", lambda path: None)
    with pytest.raises(metadata_io.MetadataIOError, match="unknown legacy frames"):
        metadata_io.write_metadata(tmp_path / "track.mp3", {"title": "Title"}, None)


@pytest.mark.parametrize("name, value", [("TRDA", "1990"), ("TSIZ", "123"), ("TDAT", "0101"), ("TIME", "1200")])
def test_raw_legacy_frame_loss_is_blocked(tmp_path, name, value):
    from mutagen.id3 import ID3, Frames
    path = tmp_path / "legacy.mp3"
    tags = ID3()
    tags.add(Frames[name](encoding=0, text=[value]))
    tags.save(path, v2_version=3)
    with pytest.raises(metadata_io.MetadataIOError, match=f"Legacy ID3 frame {name}"):
        metadata_io._assert_id3_preservable(path)


def test_nested_legacy_frame_loss_is_blocked(tmp_path):
    from mutagen.id3 import CHAP, ID3, TSIZ
    path = tmp_path / "legacy.mp3"
    tags = ID3()
    tags.add(CHAP(element_id="chapter", start_time=0, end_time=1000, start_offset=0, end_offset=0, sub_frames=[TSIZ(encoding=0, text=["123"])]))
    tags.save(path, v2_version=3)
    with pytest.raises(metadata_io.MetadataIOError, match="Legacy ID3 frame TSIZ"):
        metadata_io._assert_id3_preservable(path)
