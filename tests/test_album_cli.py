from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from mnemosyne import album_cli
from mnemosyne.album_library import AlbumFilenameFormat
from mnemosyne.cli import app
from mnemosyne.providers.musicbrainz import MusicBrainzError

runner = CliRunner()
SOURCE = "https://musicbrainz.org/release/7516696d-6a9f-4166-84ee-5d27416e1ef1"


@pytest.fixture
def album_stubs(tmp_path, monkeypatch):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    calls = []
    metadata = SimpleNamespace(title="Album", artist="Artist", date="2018-10-05", source_url=SOURCE)
    track = SimpleNamespace(source=album / "01 - title_UUID.mp3", target=album / "01 - Title.mp3", tags={"track": "1/1", "artist": "Artist"})
    plan = SimpleNamespace(tracks=(track,), playlists=())
    monkeypatch.setattr(album_cli, "fetch_album", lambda url: calls.append(("fetch", url)) or metadata)
    monkeypatch.setattr(album_cli, "preview_album", lambda path, data, **kwargs: calls.append(("preview", path, kwargs["filename_format"])) or plan)
    monkeypatch.setattr(album_cli, "apply_album", lambda plan, root: calls.append(("apply", root)) or SimpleNamespace(transaction_dir=root / "transaction", report_path=root / "report.json"))
    monkeypatch.setattr(album_cli, "runtime_root", lambda: tmp_path / "runtime")
    return album, calls


def test_album_default_is_preview_only(album_stubs):
    album, calls = album_stubs
    result = runner.invoke(app, ["album", str(album), "--source-url", SOURCE])
    assert result.exit_code == 0, result.output
    assert [call[0] for call in calls] == ["fetch", "preview"]
    assert calls[1][2] == AlbumFilenameFormat.FULL
    assert "No album files changed" in result.output


def test_album_prompts_for_folder_then_metadata_source(album_stubs):
    album, calls = album_stubs
    result = runner.invoke(app, ["album"], input=f"{album}\n{SOURCE}\n")
    assert result.exit_code == 0, result.output
    assert "Album folder" in result.output
    assert "Metadata source: MusicBrainz" in result.output
    assert "MusicBrainz release page URL" in result.output
    assert calls == [("fetch", SOURCE), ("preview", album, AlbumFilenameFormat.FULL)]


def test_album_apply_can_be_cancelled_after_preview(album_stubs):
    album, calls = album_stubs
    result = runner.invoke(app, ["album", str(album), "--source-url", SOURCE, "--apply"], input="n\n")
    assert result.exit_code == 0, result.output
    assert [call[0] for call in calls] == ["fetch", "preview"]
    assert "Cancelled" in result.output


def test_album_apply_uses_selected_backup_root(album_stubs, tmp_path):
    album, calls = album_stubs
    root = tmp_path / "backups"
    result = runner.invoke(app, ["album", str(album), "--source-url", SOURCE, "--apply", "--yes", "--backup-root", str(root)])
    assert result.exit_code == 0, result.output
    assert calls[-1] == ("apply", root)
    assert "Album renamed, tagged, and verified" in result.output


def test_album_yes_without_apply_is_rejected(album_stubs):
    album, calls = album_stubs
    result = runner.invoke(app, ["album", str(album), "--source-url", SOURCE, "--yes"])
    assert result.exit_code == 45
    assert not calls


def test_album_provider_error_never_applies(album_stubs, monkeypatch):
    album, calls = album_stubs
    def fail(url):
        raise MusicBrainzError("Not a release page")
    monkeypatch.setattr(album_cli, "fetch_album", fail)
    result = runner.invoke(app, ["album", str(album), "--source-url", SOURCE, "--apply", "--yes"])
    assert result.exit_code == 45
    assert "Not a release page" in result.output
    assert not calls


def test_album_bad_folder_is_rejected_before_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(album_cli, "fetch_album", lambda url: pytest.fail("must not fetch"))
    result = runner.invoke(app, ["album", str(tmp_path / "missing"), "--source-url", SOURCE])
    assert result.exit_code == 45
    assert "Album folder does not exist" in result.output


def test_album_track_title_format_can_be_selected(album_stubs):
    album, calls = album_stubs
    result = runner.invoke(app, ["album", str(album), "--source-url", SOURCE, "--filename-format", "track-title"])
    assert result.exit_code == 0, result.output
    assert calls[1][2] == AlbumFilenameFormat.TRACK_TITLE


def test_album_preview_shows_disc_for_multidisc(album_stubs, monkeypatch):
    album, _ = album_stubs
    tracks = tuple(
        SimpleNamespace(
            source=album / f"{disc}01-original.mp3",
            target=album / f"{disc}01 - Title.mp3",
            tags={"track": "1/1", "disc": f"{disc}/2", "artist": "Artist"},
        )
        for disc in (1, 2)
    )
    monkeypatch.setattr(album_cli, "preview_album", lambda *args, **kwargs: SimpleNamespace(tracks=tracks, playlists=()))
    result = runner.invoke(app, ["album", str(album), "--source-url", SOURCE])
    assert result.exit_code == 0, result.output
    assert "Disc" in result.output
    assert "1/2" in result.output
    assert "2/2" in result.output
