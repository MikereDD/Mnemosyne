"""CLI integration for the explicitly read-only Music workflow."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from mnemosyne.cli import app
from mnemosyne import music_library as music


def test_music_cli_scan_identify_plan(tmp_path: Path, monkeypatch):
    root = tmp_path / 'Music'
    album = root / 'Jazz' / 'Miles Davis' / 'Agharta (256)' / 'CD #1'
    album.mkdir(parents=True)
    song = album / '01-Prelude_(Part_One).mp3'
    song.write_bytes(b'fake-mp3-for-structural-test')
    monkeypatch.setattr(music, 'runtime_root', lambda: tmp_path / 'runtime')
    monkeypatch.setattr(music, '_read_tags', lambda _path: ({}, 'missing', None))
    monkeypatch.setattr(
        'mnemosyne.cli.load_config',
        lambda: SimpleNamespace(
            library_root=tmp_path,
            library=SimpleNamespace(music='Music', ebooks='eBooks'),
        ),
    )
    runner = CliRunner()
    scan = runner.invoke(app, [
        'library-scan', '--media', 'music', '--music-root', str(root),
        '--only', 'Jazz/Miles Davis/Agharta (256)',
    ])
    assert scan.exit_code == 0, scan.output
    assert 'Music scan' in scan.output
    identity = runner.invoke(app, ['library-identify', '--media', 'music'])
    assert identity.exit_code == 0, identity.output
    assert 'Miles Davis' in identity.output
    plan = runner.invoke(app, ['library-plan', '--media', 'music'])
    assert plan.exit_code == 0, plan.output
    assert 'Music plan' in plan.output
    assert song.read_bytes() == b'fake-mp3-for-structural-test'


@pytest.mark.parametrize('prefix,disc,track', [('01', '1/1', '1/11'), ('201', '2/2', '1/14')])
def test_music_cli_preserves_full_album_filenames_and_flat_layout(tmp_path, monkeypatch, prefix, disc, track):
    root = tmp_path / 'Music'
    album = root / 'Classical' / 'A Composer' / 'Piano Collection'
    album.mkdir(parents=True)
    filename = f'{prefix} - Part One - Part Two - Piano Collection - A Composer - (2020).mp3'
    song = album / filename
    song.write_bytes(b'audio payload stays untouched')
    playlist = album / 'Piano Collection.m3u8'
    playlist.write_text('#EXTM3U\n' + filename + '\n', encoding='utf-8')
    original = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in album.iterdir()}
    runtime = tmp_path / 'runtime'
    monkeypatch.setattr(music, 'runtime_root', lambda: runtime)
    monkeypatch.setattr(music, '_read_tags', lambda _path: ({
        'title': 'Part One - Part Two', 'artist': 'A Composer', 'albumartist': 'A Composer',
        'album': 'Piano Collection', 'date': '2020', 'genre': 'Classical',
        'tracknumber': track, 'discnumber': disc,
    }, 'available', 256000))
    monkeypatch.setattr(
        'mnemosyne.cli.load_config',
        lambda: SimpleNamespace(
            library_root=tmp_path,
            library=SimpleNamespace(music='Music', ebooks='eBooks'),
        ),
    )
    runner = CliRunner()
    scan = runner.invoke(app, [
        'library-scan', '--media', 'music', '--music-root', str(root),
        '--only', 'Classical/A Composer/Piano Collection',
    ])
    assert scan.exit_code == 0, scan.output
    identity = runner.invoke(app, ['library-identify', '--media', 'music'])
    assert identity.exit_code == 0, identity.output
    assert 'HIGH:' in identity.output
    assert 'CONFLICT:' not in identity.output
    result = runner.invoke(app, ['library-plan', '--media', 'music'])
    assert result.exit_code == 0, result.output
    reports = list((runtime / 'state' / 'plans').glob('library-plan-music-*.json'))
    assert len(reports) == 1
    plan = json.loads(reports[0].read_text(encoding='utf-8'))
    assert plan['tracks'][0]['target'] == song.relative_to(root).as_posix()
    assert plan['tracks'][0]['renameAction'] == 'KEEP'
    assert plan['tracks'][0]['status'] == 'READY'
    assert plan['summary']['wouldRename'] == 0
    assert plan['safety']['applyAvailable'] is False
    assert plan['playlists']['preserved'] == [playlist.relative_to(root).as_posix()]
    assert original == {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in album.iterdir()}


def test_music_cli_has_no_apply_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(music, 'runtime_root', lambda: tmp_path / 'runtime')
    runner = CliRunner()
    result = runner.invoke(app, ['library-plan', '--media', 'music', '--apply'])
    assert result.exit_code != 0
    assert 'No such option' in result.output
    assert not (tmp_path / 'runtime').exists()
