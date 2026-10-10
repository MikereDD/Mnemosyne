import hashlib
import json
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from mnemosyne import album_library as library
from mnemosyne.metadata_io import MetadataSnapshot
from mnemosyne.providers.musicbrainz import AlbumMetadata, AlbumTrack


GUID = "8d7ec983-f35a-4bf0-aea3-f94398038c21"


def _mp3_bytes(tags, payload):
    body = json.dumps(tags, ensure_ascii=False, sort_keys=True).encode("utf-8")
    size = len(body)
    encoded = bytes((size >> 21 & 127, size >> 14 & 127, size >> 7 & 127, size & 127))
    return b"ID3\x04\x00\x00" + encoded + body + payload


def _read_fake(path):
    content = path.read_bytes()
    size = 0
    for byte in content[6:10]:
        size = size << 7 | byte
    tags = json.loads(content[10:10 + size].decode("utf-8"))
    return tags, content[10 + size:]


@pytest.fixture
def fake_audio(monkeypatch):
    def read(path):
        tags, _ = _read_fake(path)
        return MetadataSnapshot("id3", tags, (("image/png", b"original artwork"),))

    def write(path, tags, cover):
        original, payload = _read_fake(path)
        original.update({key: [value] for key, value in tags.items()})
        path.write_bytes(_mp3_bytes(original, payload))

    def verify(path, tags, *, expected_cover_sha256):
        snapshot = read(path)
        for key, value in tags.items():
            if snapshot.tags.get(key) != [value]:
                raise library.MetadataIOError(f"Metadata mismatch: {key}")
        return snapshot

    monkeypatch.setattr(library, "read_metadata", read)
    monkeypatch.setattr(library, "write_metadata", write)
    monkeypatch.setattr(library, "verify_metadata", verify)
    monkeypatch.setattr(library, "_duration_ms", lambda path: int(_read_fake(path)[1].split(b"|")[1]))
    return write


@pytest.fixture
def album(tmp_path, fake_audio):
    folder = tmp_path / "album"
    folder.mkdir()
    release = AlbumMetadata(
        "https://musicbrainz.org/release/12345678-1234-1234-1234-123456789abc",
        "12345678-1234-1234-1234-123456789abc",
        "Inscape", "Alexandra Stréliski", "2018-10-05",
        (AlbumTrack(1, 1, "Plus tôt", "Alexandra Stréliski", 120000),
         AlbumTrack(1, 2, "The Quiet Voice", "Alexandra Stréliski", 130000)),
    )
    for track in release.tracks:
        name = f"{track.number:02d} - {track.title}_{GUID}.mp3"
        tags = {"title": [track.title], "artist": [release.artist], "track": [f"{track.number}/2"], "disc": ["1/1"], "genre": ["Classical"], "COMM": ["keep my comment"], "PRIV": ["private payload"]}
        (folder / name).write_bytes(_mp3_bytes(tags, f"audio|{track.length_ms}|{track.number}".encode()))
    (folder / "cover.png").write_bytes(b"cover remains")
    return folder, release


def test_preview_is_read_only_and_corroborates_tags_and_duration(album):
    folder, release = album
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    plan = library.preview_album(folder, release)
    assert [track.target.name for track in plan.tracks] == ["01 - Plus tôt.mp3", "02 - The Quiet Voice.mp3"]
    assert all("matching-title" in track.matching_evidence and "matching-duration" in track.matching_evidence for track in plan.tracks)
    assert plan.tracks[0].tags["date"] == "2018-10-05"
    assert "genre" not in plan.tracks[0].tags
    assert before == {path.name: path.read_bytes() for path in folder.iterdir()}
    assert plan.rename_count == 2


def test_preview_never_uses_alphabetic_order(album):
    folder, release = album
    for index, path in enumerate(sorted(folder.glob("*.mp3"))):
        _, payload = _read_fake(path)
        target = folder / f"arbitrary-{index}.mp3"
        path.rename(target)
        target.write_bytes(_mp3_bytes({}, payload))
    with pytest.raises(library.AlbumLibraryError, match="Alphabetic order"):
        library.preview_album(folder, release)


def test_filename_position_requires_title_or_duration_corroboration(album, monkeypatch):
    folder, release = album
    path = next(folder.glob("01*.mp3"))
    _, payload = _read_fake(path)
    path.write_bytes(_mp3_bytes({}, payload))
    path.rename(folder / f"01 - unrelated_{GUID}.mp3")
    monkeypatch.setattr(library, "_duration_ms", lambda path: None)
    with pytest.raises(library.AlbumLibraryError, match="insufficient or conflicting"):
        library.preview_album(folder, release)


def test_unique_embedded_titles_match_without_filename_positions(album):
    folder, release = album
    for index, path in enumerate(sorted(folder.glob("*.mp3"))):
        tags, payload = _read_fake(path)
        tags.pop("track")
        path.write_bytes(_mp3_bytes(tags, payload))
        path.rename(folder / f"unordered-{index}.mp3")
    plan = library.preview_album(folder, release)
    assert [track.tags["track"] for track in plan.tracks] == ["1/2", "2/2"]


def test_conflicting_number_tags_are_blocked(album):
    folder, release = album
    path = next(folder.glob("01*.mp3"))
    tags, payload = _read_fake(path)
    tags["track"] = ["2/2"]
    path.write_bytes(_mp3_bytes(tags, payload))
    with pytest.raises(library.AlbumLibraryError, match="Conflicting track numbers"):
        library.preview_album(folder, release)


def test_duration_conflict_blocks_even_matching_titles(album):
    folder, release = album
    path = next(folder.glob("01*.mp3"))
    tags, _ = _read_fake(path)
    path.write_bytes(_mp3_bytes(tags, b"audio|900000|1"))
    with pytest.raises(library.AlbumLibraryError, match="conflicting"):
        library.preview_album(folder, release)


def test_duplicate_local_mapping_is_blocked(album):
    folder, release = album
    first = next(folder.glob("01*.mp3"))
    second = next(folder.glob("02*.mp3"))
    second.unlink()
    (folder / "01 - duplicate.mp3").write_bytes(first.read_bytes())
    with pytest.raises(library.AlbumLibraryError, match="More than one local file"):
        library.preview_album(folder, release)


def test_incomplete_album_and_multi_disc_release_are_blocked(album):
    folder, release = album
    next(folder.glob("02*.mp3")).unlink()
    with pytest.raises(library.AlbumLibraryError, match="incomplete"):
        library.preview_album(folder, release)
    multidisc = AlbumMetadata(release.source_url, release.release_id, release.title, release.artist, release.date, (AlbumTrack(2, 1, "Plus tôt", release.artist, 120000),))
    with pytest.raises(library.AlbumLibraryError, match="Multi-disc"):
        library.preview_album(folder, multidisc)


def test_nested_audio_is_blocked(album):
    folder, release = album
    nested = folder / "Disc 1"
    nested.mkdir()
    first = next(folder.glob("01*.mp3"))
    first.rename(nested / first.name)
    with pytest.raises(library.AlbumLibraryError, match="Nested audio"):
        library.preview_album(folder, release)


def test_playlist_preserves_bom_crlf_comments_self_reference_and_urls(album):
    folder, release = album
    first = next(folder.glob("01*.mp3"))
    second = next(folder.glob("02*.mp3"))
    original = b"\xef\xbb\xbf" + f"#EXTM3U\r\n#EXTINF:120,Plus tôt\r\n  .\\{first.name}  \r\n{second.name}\r\nhttps://example.com/{first.name}\r\nINSCAPE.m3u\r\n".encode()
    playlist = folder / "INSCAPE.m3u"
    playlist.write_bytes(original)
    plan = library.preview_album(folder, release)
    assert playlist.read_bytes() == original
    expected = original.replace(f"  .\\{first.name}  ".encode(), "  .\\01 - Plus tôt.mp3  ".encode()).replace(f"{second.name}\r\n".encode(), b"02 - The Quiet Voice.mp3\r\n")
    assert plan.playlists[0].updated_bytes == expected


def test_unsupported_referencing_cue_blocks_before_mutation(album):
    folder, release = album
    first = next(folder.glob("01*.mp3"))
    (folder / "album.cue").write_text(f'FILE "{first.name}" MP3\n', encoding="utf-8")
    with pytest.raises(library.AlbumLibraryError, match="reference format is not supported"):
        library.preview_album(folder, release)


def test_non_utf8_playlist_blocks(album):
    folder, release = album
    (folder / "album.m3u").write_bytes(b"\xffinvalid")
    with pytest.raises(library.AlbumLibraryError, match="not UTF-8"):
        library.preview_album(folder, release)


def test_nested_playlist_blocks_instead_of_leaving_stale_references(album):
    folder, release = album
    nested = folder / "lists"
    nested.mkdir()
    (nested / "album.m3u").write_text("../" + next(folder.glob("01*.mp3")).name, encoding="utf-8")
    with pytest.raises(library.AlbumLibraryError, match="Nested playlists"):
        library.preview_album(folder, release)


def test_encoded_unsupported_reference_format_is_blocked(album):
    folder, release = album
    first = next(folder.glob("01*.mp3"))
    (folder / "album.xspf").write_text(f"<location>{first.as_uri()}</location>", encoding="utf-8")
    with pytest.raises(library.AlbumLibraryError, match="reference format is not supported"):
        library.preview_album(folder, release)


def test_file_uri_literal_percent_is_decoded_once(album):
    folder, release = album
    first = next(folder.glob("01*.mp3"))
    renamed = folder / f"01 - Plus%20tôt_{GUID}.mp3"
    first.rename(renamed)
    (folder / "album.m3u").write_text(renamed.as_uri() + "\n", encoding="utf-8", newline="")
    plan = library.preview_album(folder, release)
    assert plan.playlists[0].updated_bytes == ((folder / "01 - Plus tôt.mp3").as_uri() + "\n").encode("utf-8")


def test_apply_retains_originals_preserves_payload_art_and_other_tags(album, tmp_path):
    folder, release = album
    originals = {path.name: path.read_bytes() for path in folder.glob("*.mp3")}
    first = next(folder.glob("01*.mp3"))
    (folder / "album.m3u8").write_text(first.name + "\n", encoding="utf-8")
    plan = library.preview_album(folder, release)
    result = library.apply_album(plan, tmp_path / "transactions")
    assert result.playlists_updated == 1
    assert (folder / "album.m3u8").read_text(encoding="utf-8") == "01 - Plus tôt.mp3\n"
    assert (folder / "cover.png").read_bytes() == b"cover remains"
    for track in plan.tracks:
        tags, _ = _read_fake(track.target)
        assert tags["album"] == ["Inscape"]
        assert tags["disc"] == ["1/1"]
        assert tags["genre"] == ["Classical"]
        assert tags["COMM"] == ["keep my comment"]
        assert library.audio_payload_sha256(track.target) == track.payload_sha256
        assert (result.transaction_dir / "originals" / track.source.name).read_bytes() == originals[track.source.name]
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["phase"] == "complete"
    assert report["source"]["releaseId"] == release.release_id
    assert "private payload" not in result.report_path.read_text(encoding="utf-8")
    assert not list(folder.glob(".mnemosyne*"))


def test_apply_source_and_playlist_staleness_block_before_transaction(album, tmp_path):
    folder, release = album
    playlist = folder / "album.m3u"
    playlist.write_text(next(folder.glob("01*.mp3")).name + "\n", encoding="utf-8")
    plan = library.preview_album(folder, release)
    playlist.write_bytes(playlist.read_bytes() + b"# changed\n")
    with pytest.raises(library.AlbumLibraryError, match="playlists changed"):
        library.apply_album(plan, tmp_path / "transactions")
    playlist.write_bytes(plan.playlists[0].original_bytes)
    source = plan.tracks[0].source
    data = source.read_bytes()
    source.write_bytes(data[:-1] + b"9")  # Same file size must still be stale.
    with pytest.raises(library.AlbumLibraryError, match="Source changed"):
        library.apply_album(plan, tmp_path / "transactions")
    assert not (tmp_path / "transactions").exists()


def test_apply_blocks_changed_file_set_and_transaction_root_inside_album(album):
    folder, release = album
    plan = library.preview_album(folder, release)
    with pytest.raises(library.AlbumLibraryError, match="outside the album"):
        library.apply_album(plan, folder / "backups")
    extra = folder / "extra.mp3"
    extra.write_bytes(plan.tracks[0].source.read_bytes())
    with pytest.raises(library.AlbumLibraryError, match="file set changed"):
        library.apply_album(plan, folder.parent / "transactions")


def test_mid_commit_failure_restores_all_original_bytes_and_names(album, tmp_path, monkeypatch):
    folder, release = album
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    plan = library.preview_album(folder, release)
    rename = library._rename_no_overwrite

    def fail_second_install(source, target):
        if ".new." in source.name and target.name.startswith("02"):
            raise OSError("Injected rename failure")
        rename(source, target)

    monkeypatch.setattr(library, "_rename_no_overwrite", fail_second_install)
    with pytest.raises(library.AlbumLibraryError, match="original album restored"):
        library.apply_album(plan, tmp_path / "transactions")
    assert before == {path.name: path.read_bytes() for path in folder.iterdir()}
    report = next((tmp_path / "transactions").glob("*/transaction.json"))
    assert json.loads(report.read_text())["rollback"]["status"] == "verified"


def test_failure_after_playlist_commit_restores_playlist_and_audio(album, tmp_path, monkeypatch):
    folder, release = album
    playlist = folder / "album.m3u"
    playlist.write_text(next(folder.glob("01*.mp3")).name + "\r\n", encoding="utf-8", newline="")
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    plan = library.preview_album(folder, release)
    write = library._write_json_atomic
    failed = False

    def fail_completion(path, report):
        nonlocal failed
        if report["phase"] == "complete" and not failed:
            failed = True
            raise OSError("Injected report failure")
        write(path, report)

    monkeypatch.setattr(library, "_write_json_atomic", fail_completion)
    with pytest.raises(library.AlbumLibraryError, match="original album restored"):
        library.apply_album(plan, tmp_path / "transactions")
    assert before == {path.name: path.read_bytes() for path in folder.iterdir()}


def test_payload_change_is_detected_before_originals_are_touched(album, tmp_path, monkeypatch, fake_audio):
    folder, release = album
    before = {path.name: path.read_bytes() for path in folder.iterdir()}
    plan = library.preview_album(folder, release)

    def corrupt(path, tags, cover):
        fake_audio(path, tags, cover)
        path.write_bytes(path.read_bytes() + b"changed audio")

    monkeypatch.setattr(library, "write_metadata", corrupt)
    with pytest.raises(library.AlbumLibraryError, match="payload changed"):
        library.apply_album(plan, tmp_path / "transactions")
    assert before == {path.name: path.read_bytes() for path in folder.iterdir()}


def test_payload_hash_excludes_flac_metadata_and_m4a_container_metadata(tmp_path):
    flac = tmp_path / "track.flac"
    flac.write_bytes(b"fLaC\x80\x00\x00\x03old" + b"audio frames")
    original = library.audio_payload_sha256(flac)
    flac.write_bytes(b"fLaC\x80\x00\x00\x06newtag" + b"audio frames")
    assert library.audio_payload_sha256(flac) == original
    m4a = tmp_path / "track.m4a"

    def atom(kind, payload):
        return struct.pack(">I4s", 8 + len(payload), kind) + payload

    m4a.write_bytes(atom(b"moov", b"old tags") + atom(b"mdat", b"compressed audio"))
    original = library.audio_payload_sha256(m4a)
    m4a.write_bytes(atom(b"moov", b"new and longer tags") + atom(b"mdat", b"compressed audio"))
    assert library.audio_payload_sha256(m4a) == original
    m4a.write_bytes(atom(b"moov", b"new tags") + atom(b"mdat", b"different audio"))
    assert library.audio_payload_sha256(m4a) != original


def test_no_overwrite_rename_preserves_existing_destination(tmp_path):
    source, target = tmp_path / "source.mp3", tmp_path / "target.mp3"
    source.write_bytes(b"source")
    target.write_bytes(b"target")
    with pytest.raises(library.AlbumLibraryError, match="Destination appeared"):
        library._rename_no_overwrite(source, target)
    assert source.read_bytes() == b"source"
    assert target.read_bytes() == b"target"


def test_posix_source_unlink_failure_removes_only_created_link(tmp_path, monkeypatch):
    source, target = tmp_path / "source.mp3", tmp_path / "target.mp3"
    source.write_bytes(b"original")
    unlink = Path.unlink

    def fail_source(path, *args, **kwargs):
        if path == source:
            raise OSError("Injected source unlink failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_source)
    with pytest.raises(OSError, match="source unlink failure"):
        library._link_rename_no_overwrite(source, target)
    assert source.read_bytes() == b"original"
    assert not target.exists()


def test_posix_compensation_failure_is_reported_incomplete(album, tmp_path, monkeypatch):
    folder, release = album
    plan = library.preview_album(folder, release)
    source = plan.tracks[0].source
    unlink = Path.unlink

    def fail_source_and_hold(path, *args, **kwargs):
        if path == source or ".original." in path.name:
            raise OSError("Injected unlink and compensation failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_source_and_hold)
    monkeypatch.setattr(library, "_rename_no_overwrite", library._link_rename_no_overwrite)
    with pytest.raises(library.AlbumLibraryError, match="rollback incomplete"):
        library.apply_album(plan, tmp_path / "transactions")
    report_path = next((tmp_path / "transactions").glob("*/transaction.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["phase"] == "rollback-incomplete"
    assert report["rollback"]["status"] == "incomplete"
    assert "created link could not" in report["rollback"]["failures"][0]
    assert source.exists()


def test_full_filename_uses_unicode_title_album_track_artist_and_year(album):
    folder, release = album
    release = replace(release, tracks=(replace(release.tracks[0], artist="Alexandra Stréliski & Guest"), release.tracks[1]))
    plan = library.preview_album(folder, release, filename_format=library.AlbumFilenameFormat.FULL)
    assert plan.tracks[0].target.name == "01 - Plus tôt - Inscape - Alexandra Stréliski & Guest - (2018).mp3"
    assert plan.tracks[1].target.name == "02 - The Quiet Voice - Inscape - Alexandra Stréliski - (2018).mp3"
    assert plan.tracks[0].tags["date"] == "2018-10-05"
    assert plan.filename_format is library.AlbumFilenameFormat.FULL


@pytest.mark.parametrize("release_date", [None, "", "Unknown", "0000", "2018garbage", "2018-13", "2018-02-31"])
def test_full_filename_blocks_missing_or_invalid_release_year(album, release_date):
    folder, release = album
    release = replace(release, date=release_date)
    with pytest.raises(library.AlbumLibraryError, match="known valid MusicBrainz release year"):
        library.preview_album(folder, release, filename_format=library.AlbumFilenameFormat.FULL)
    assert library.preview_album(folder, release).tracks[0].target.name == "01 - Plus tôt.mp3"


@pytest.mark.parametrize("release_date", ["2018", "2018-10", "2018-10-05"])
def test_full_filename_uses_only_year_and_album_artist_fallback(album, release_date):
    folder, release = album
    release = replace(release, date=release_date, tracks=(replace(release.tracks[0], artist=""), release.tracks[1]))
    plan = library.preview_album(folder, release, filename_format=library.AlbumFilenameFormat.FULL)
    assert plan.tracks[0].target.name == "01 - Plus tôt - Inscape - Alexandra Stréliski - (2018).mp3"
    assert plan.tracks[0].tags["date"] == release_date


def test_full_filename_components_are_individually_safe(album):
    folder, release = album
    release = replace(release, title="Inscape: deluxe?", artist='Alexandra / ensemble')
    release = replace(release, tracks=(replace(release.tracks[0], artist='Alexandra / ensemble'), release.tracks[1]))
    plan = library.preview_album(folder, release, filename_format=library.AlbumFilenameFormat.FULL)
    assert plan.tracks[0].target.name == "01 - Plus tôt - Inscape_ deluxe_ - Alexandra _ ensemble - (2018).mp3"
    assert plan.tracks[0].tags["album"] == "Inscape: deluxe?"
    assert plan.tracks[0].tags["artist"] == "Alexandra / ensemble"


def test_full_filename_apply_updates_playlist_and_repeated_preview_is_stable(album, tmp_path):
    folder, release = album
    playlist = folder / "album.m3u8"
    playlist.write_text(next(folder.glob("01*.mp3")).name + "\n", encoding="utf-8", newline="")
    plan = library.preview_album(folder, release, filename_format=library.AlbumFilenameFormat.FULL)
    result = library.apply_album(plan, tmp_path / "transactions")
    assert playlist.read_text(encoding="utf-8") == plan.tracks[0].target.name + "\n"
    repeated = library.preview_album(folder, release, filename_format=library.AlbumFilenameFormat.FULL)
    assert repeated.rename_count == 0
    assert [track.tags for track in repeated.tracks] == [track.tags for track in plan.tracks]
    assert all("matching-title" in track.matching_evidence for track in repeated.tracks)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["filenameFormat"] == "full"
    assert report["source"]["date"] == "2018-10-05"
