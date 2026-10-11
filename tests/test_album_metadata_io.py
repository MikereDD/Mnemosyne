from types import SimpleNamespace
import hashlib

import pytest
from mutagen.id3 import APIC, CHAP, COMM, ID3, Frames, TDRC, TIT2, TXXX
from mutagen.mp3 import MP3

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
    monkeypatch.setattr(metadata_io, "_assert_id3_preservable", lambda path, **kwargs: None)
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


def _legacy_mp3(tmp_path, *, year="2008", day_month=None, hour_minute=None, canonical=None):
    path = tmp_path / "legacy.mp3"
    # MPEG-1 Layer III, 128 kbps / 44.1 kHz; valid headers with opaque payload.
    path.write_bytes((b"\xff\xfb\x90\x00" + bytes(413)) * 20)
    tags = ID3()
    tags.add(TIT2(encoding=0, text=["Original title"]))
    tags.add(TXXX(encoding=0, desc="unrelated date", text=["1999-12-31"]))
    tags.add(COMM(encoding=0, lang="eng", desc="comment", text=["Keep this comment"]))
    tags.add(APIC(encoding=0, mime="image/png", type=3, desc="front", data=b"unchanged artwork"))
    for name, value in (("TYER", year), ("TDAT", day_month), ("TIME", hour_minute)):
        if value is not None:
            tags.add(Frames[name](encoding=0, text=[value]))
    if canonical is not None:
        tags.add(TDRC(encoding=0, text=[canonical]))
    tags.save(path, v2_version=3)
    return path


def _audio_and_unrelated(path):
    audio = MP3(path)
    data = path.read_bytes()
    end = len(data) - 128 if data[-128:-125] == b"TAG" else len(data)
    return (
        hashlib.sha256(data[audio.info.frame_offset:end]).hexdigest(),
        [(frame.mime, frame.type, frame.desc, frame.data) for frame in audio.tags.getall("APIC")],
        [(frame.lang, frame.desc, list(frame.text)) for frame in audio.tags.getall("COMM")],
        [(frame.desc, list(frame.text)) for frame in audio.tags.getall("TXXX")],
    )


@pytest.mark.parametrize(
    "day_month,hour_minute,expected",
    [(None, None, "2008"), ("0000", None, "2008"), ("0506", None, "2008-06-05"),
     ("2902", None, "2008-02-29"), ("0506", "0000", "2008-06-05 00:00:00"),
     ("0506", "2359", "2008-06-05 23:59:00")],
)
def test_non_date_write_preserves_legacy_date_precision_time_artwork_and_audio(
    tmp_path, day_month, hour_minute, expected
):
    path = _legacy_mp3(tmp_path, day_month=day_month, hour_minute=hour_minute)
    before = _audio_and_unrelated(path)
    assert metadata_io.read_metadata(path).tags["date"] == [expected]

    metadata_io.write_metadata(path, {"title": "Renamed title"}, None)

    assert metadata_io.read_metadata(path).tags["date"] == [expected]
    assert metadata_io.read_metadata(path).tags["title"] == ["Renamed title"]
    assert _audio_and_unrelated(path) == before
    written = ID3(path, translate=False)
    assert not any(written.getall(name) for name in ("TYER", "TDAT", "TIME"))
    assert written.version == (2, 4, 0)


@pytest.mark.parametrize(
    "legacy",
    [{"year": "2007", "day_month": "2902"}, {"day_month": "3212"},
     {"day_month": "0100"}, {"day_month": "0001"}, {"day_month": "abcd"},
     {"year": "0000"}, {"year": "200"}, {"year": None, "day_month": "0101"},
     {"hour_minute": "1200"}, {"day_month": "0000", "hour_minute": "1200"},
     {"day_month": "0101", "hour_minute": "2400"},
     {"day_month": "0101", "hour_minute": "1260"},
     {"canonical": "2009", "day_month": "0101"}],
)
def test_non_date_write_refuses_ambiguous_or_invalid_legacy_dates_without_mutation(tmp_path, legacy):
    path = _legacy_mp3(tmp_path, **legacy)
    original = path.read_bytes()
    with pytest.raises(metadata_io.MetadataIOError):
        metadata_io.read_metadata(path)
    with pytest.raises(metadata_io.MetadataIOError):
        metadata_io.write_metadata(path, {"title": "Renamed title"}, None)
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "legacy",
    [{"day_month": "0000"}, {"year": "2007", "day_month": "2902"},
     {"year": "0000", "hour_minute": "9999"}, {"year": None, "day_month": "0101"},
     {"canonical": "2009", "day_month": "0101"}],
)
def test_explicit_valid_date_replaces_only_top_level_legacy_dates(tmp_path, legacy):
    path = _legacy_mp3(tmp_path, **legacy)
    before = _audio_and_unrelated(path)
    original = path.read_bytes()
    snapshot = metadata_io.read_metadata(path, replacing_date=True)
    assert snapshot.tags["TXXX"] == ["1999-12-31"]
    assert path.read_bytes() == original

    metadata_io.write_metadata(path, {"title": "Renamed title", "date": "2008"}, None)

    metadata_io.verify_metadata(path, {"title": "Renamed title", "date": "2008"}, expected_cover_sha256=None)
    assert _audio_and_unrelated(path) == before
    assert not any(ID3(path, translate=False).getall(name) for name in ("TYER", "TDAT", "TIME"))


@pytest.mark.parametrize("replacement", ["", "0000", "2008-00", "2007-02-29", "08", "2008/01/01", "2008-01-01T12:00", None, 2008])
@pytest.mark.parametrize("updates", [{"title": "New"}, {"title": "New", "track": "1/14", "disc": "1/2"}])
def test_invalid_supplied_date_is_rejected_before_title_or_full_tag_updates(tmp_path, replacement, updates):
    path = _legacy_mp3(tmp_path, day_month="0000")
    original = path.read_bytes()
    with pytest.raises(metadata_io.MetadataIOError, match="date"):
        metadata_io.write_metadata(path, {**updates, "date": replacement}, None)
    assert path.read_bytes() == original


@pytest.mark.parametrize("name,value", [("TYER", "2008"), ("TDAT", "0101"), ("TIME", "1200"), ("TORY", "1990"), ("TRDA", "1990"), ("TSIZ", "123")])
def test_replacing_album_date_does_not_authorize_nested_legacy_frame_loss(tmp_path, name, value):
    path = _legacy_mp3(tmp_path, day_month="0000")
    raw = ID3(path, translate=False)
    raw.add(CHAP(element_id="chapter", start_time=0, end_time=1000, start_offset=0, end_offset=0,
                 sub_frames=[Frames[name](encoding=0, text=[value])]))
    raw.save(path, v2_version=3)
    original = path.read_bytes()
    with pytest.raises(metadata_io.MetadataIOError, match=f"Legacy ID3 frame {name}"):
        metadata_io.read_metadata(path, replacing_date=True)
    with pytest.raises(metadata_io.MetadataIOError, match=f"Legacy ID3 frame {name}"):
        metadata_io.write_metadata(path, {"date": "2008"}, None)
    assert path.read_bytes() == original


@pytest.mark.parametrize("name,value", [("TRDA", "1990"), ("TSIZ", "123")])
def test_replacing_album_date_does_not_authorize_unrelated_legacy_frame_loss(tmp_path, name, value):
    path = _legacy_mp3(tmp_path, day_month="0000")
    raw = ID3(path, translate=False)
    raw.add(Frames[name](encoding=0, text=[value]))
    raw.save(path, v2_version=3)
    original = path.read_bytes()
    with pytest.raises(metadata_io.MetadataIOError, match=f"Legacy ID3 frame {name}"):
        metadata_io.write_metadata(path, {"date": "2008"}, None)
    assert path.read_bytes() == original


def test_unknown_legacy_frame_is_blocked_even_with_explicit_date(tmp_path):
    path = tmp_path / "unknown.mp3"
    body = b"XZZZ" + (7).to_bytes(4, "big") + b"\x00\x00" + b"private"
    size = len(body)
    size_bytes = bytes((size >> 21 & 127, size >> 14 & 127, size >> 7 & 127, size & 127))
    path.write_bytes(b"ID3\x03\x00\x00" + size_bytes + body)
    original = path.read_bytes()
    with pytest.raises(metadata_io.MetadataIOError, match="unknown legacy frames"):
        metadata_io.read_metadata(path, replacing_date=True)
    with pytest.raises(metadata_io.MetadataIOError, match="unknown legacy frames"):
        metadata_io.write_metadata(path, {"date": "2008"}, None)
    assert path.read_bytes() == original


@pytest.mark.parametrize("frame_id", ["RVAD", "EQUA"])
def test_date_replacement_keeps_other_unsupported_frame_guards(monkeypatch, tmp_path, frame_id):
    frame = SimpleNamespace(FrameID=frame_id)
    raw = SimpleNamespace(version=(2, 3, 0), unknown_frames=[], values=lambda: [frame])
    monkeypatch.setattr(metadata_io, "ID3", lambda *args, **kwargs: raw)
    with pytest.raises(metadata_io.MetadataIOError, match=f"Legacy ID3 frame {frame_id}"):
        metadata_io._assert_id3_preservable(tmp_path / "protected.mp3", replacing_date=True)


def test_release_date_update_preserves_distinct_original_release_year(tmp_path):
    path = _legacy_mp3(tmp_path, day_month="0000")
    raw = ID3(path, translate=False)
    raw.add(Frames["TORY"](encoding=0, text=["1990"]))
    raw.save(path, v2_version=3)
    before = _audio_and_unrelated(path)

    metadata_io.write_metadata(path, {"date": "2008"}, None)

    snapshot = metadata_io.read_metadata(path)
    assert snapshot.tags["date"] == ["2008"]
    assert snapshot.tags["TDOR"] == ["1990"]
    assert _audio_and_unrelated(path) == before


@pytest.mark.parametrize("year,canonical", [("0000", None), ("199", None), ("1990", "1991")])
def test_release_date_update_refuses_loss_of_unrelated_original_release_year(tmp_path, year, canonical):
    path = _legacy_mp3(tmp_path, day_month="0000")
    raw = ID3(path, translate=False)
    raw.add(Frames["TORY"](encoding=0, text=[year]))
    if canonical is not None:
        raw.add(Frames["TDOR"](encoding=0, text=[canonical]))
    raw.save(path, v2_version=3)
    original = path.read_bytes()
    with pytest.raises(metadata_io.MetadataIOError):
        metadata_io.write_metadata(path, {"date": "2008"}, None)
    assert path.read_bytes() == original
