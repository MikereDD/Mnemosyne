import hashlib
import json
import os
from pathlib import Path

import pytest

from mnemosyne import music_library as music


def _setup(tmp_path: Path, monkeypatch):
    runtime = tmp_path / 'runtime'
    root = tmp_path / 'Music'
    monkeypatch.setattr(music, 'runtime_root', lambda: runtime)
    monkeypatch.setattr(music, '_read_tags', lambda _path: ({}, 'missing', None))
    album = root / 'Blues-Jazz-Soul' / 'Miles Davis' / 'Agharta (256)'
    for disc, names in {
        1: ['01-Prelude_(Part_One).mp3', '02-Prelude_(Part_Two).mp3', '03-Maiysha.mp3'],
        2: ['01-Interlude.mp3'],
    }.items():
        folder = album / f'CD #{disc}'
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).write_bytes(f'{disc}:{name}'.encode())
        (folder / f'CD #{disc}.m3u').write_text('\n'.join(names))
    return root, album, runtime


def _chain(root: Path, album: Path):
    scope = album.relative_to(root)
    scan = music.scan_music_library(root, only=scope)
    identity = music.identify_music_library(scan_report=scan['reportPath'])
    plan = music.plan_music_library(identification_report=identity['reportPath'])
    return scan, identity, plan


def test_agharta_four_tracks_two_playlists_and_no_mutation(tmp_path, monkeypatch):
    root, album, runtime = _setup(tmp_path, monkeypatch)
    expected = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    scan, identity, plan = _chain(root, album)
    assert scan['summary']['tracks'] == 4
    assert scan['summary']['missingTags'] == 4
    assert scan['summary']['playlists'] == 2
    assert len(identity['tracks']) == 4
    assert len(plan['tracks']) == 4
    assert plan['playlists']['preserved'] == scan['playlists']
    assert plan['safety']['applyAvailable'] is False
    assert all(Path(p['reportPath']).is_relative_to(runtime) for p in (scan, identity, plan))
    assert expected == {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_agharta_identity_recovers_artist_album_track_and_disc(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    _, result, _ = _chain(root, album)
    tracks = {Path(x['path']).name: x for x in result['tracks']}
    part_one = tracks['01-Prelude_(Part_One).mp3']
    assert part_one['identity']['artist'] == 'Miles Davis'
    assert part_one['identity']['album'] == 'Agharta'
    assert part_one['identity']['title'] == 'Prelude (Part One)'
    assert part_one['identity']['track'] == 1
    assert part_one['identity']['disc'] == 1
    assert part_one['confidence'] == 'MEDIUM'
    assert part_one['provenance']['title'] == 'filename'
    assert tracks['01-Interlude.mp3']['identity']['disc'] == 2


def test_ambiguous_genre_blocks_destination_without_guess(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    _, identity, plan = _chain(root, album)
    assert all(x['identity']['genre'] is None for x in identity['tracks'])
    assert set(identity['tracks'][0]['genreCandidates']) == {'Blues', 'Jazz', 'Soul'}
    assert all(x['target'] is None and x['status'] == 'BLOCKED' for x in plan['tracks'])
    assert all(x['proposedTags']['date'] is None for x in plan['tracks'])
    assert all(x['proposedTags']['album'] == 'Agharta' for x in plan['tracks'])
    assert all(x['proposedTags']['artist'] == 'Miles Davis' for x in plan['tracks'])


def test_exact_jazz_folder_allows_preview_without_mutation(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    jazz = root / 'Jazz'
    (root / 'Blues-Jazz-Soul').rename(jazz)
    # Fixture relocation occurred before the scan; the code under test stays read-only.
    scan, identity, plan = _chain(root, jazz / 'Miles Davis' / 'Agharta (256)')
    assert all(x['identity']['genre'] == 'Jazz' for x in identity['tracks'])
    assert plan['summary']['wouldRename'] == 4
    assert plan['tracks'][0]['target'].startswith('Jazz/Miles Davis/Agharta/CD 01/')
    assert plan['tracks'][0]['proposedTags']['genre'] == 'Jazz'


def test_changed_audio_after_scan_is_rejected(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    scan = music.scan_music_library(root, only=album.relative_to(root))
    (album / 'CD #1' / '03-Maiysha.mp3').write_bytes(b'changed')
    with pytest.raises(music.MusicLibraryError, match='changed since scan'):
        music.identify_music_library(scan_report=scan['reportPath'])


def test_added_track_after_scan_is_rejected(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    scan = music.scan_music_library(root, only=album.relative_to(root))
    (album / 'CD #1' / '04-New.mp3').write_bytes(b'x')
    with pytest.raises(music.MusicLibraryError, match='file set changed'):
        music.identify_music_library(scan_report=scan['reportPath'])


def test_music_scan_cannot_escape_root(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(music.MusicLibraryError, match='escapes root'):
        music.scan_music_library(root, only=Path('../outside'))


def test_outside_report_rejected(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    scan = music.scan_music_library(root, only=album.relative_to(root))
    outside = tmp_path / 'forged.json'
    outside.write_bytes(Path(scan['reportPath']).read_bytes())
    with pytest.raises(music.MusicLibraryError, match='direct non-symlinked'):
        music.identify_music_library(scan_report=outside)


def test_conflicting_embedded_title_is_not_silently_accepted(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    def faux_tags(path):
        if 'Prelude_(Part_One)' in path.name:
            return ({'title': 'A Different Song', 'artist': 'Miles Davis', 'album': 'Agharta'}, 'available', 256000)
        return {}, 'missing', None
    monkeypatch.setattr(music, '_read_tags', faux_tags)
    _, identity, plan = _chain(root, album)
    item = next(x for x in identity['tracks'] if 'Prelude_(Part_One)' in x['path'])
    assert item['confidence'] == 'CONFLICT'
    assert item['reviewRequired'] is True
    planned = next(x for x in plan['tracks'] if 'Prelude_(Part_One)' in x['source'])
    assert planned['status'] == 'BLOCKED'


def test_plan_tracks_sha256_provenance(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    _, _, plan = _chain(root, album)
    source = plan['tracks'][0]
    content = (root / source['source']).read_bytes()
    assert source['sourceSha256'] == hashlib.sha256(content).hexdigest()
    assert plan['safety']['sourceHashesRecorded'] is True


def test_reject_tampered_identity(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    _, identity, _ = _chain(root, album)
    path = Path(identity['reportPath'])
    payload = json.loads(path.read_text())
    payload['tracks'][0]['identity']['artist'] = 'Wrong Artist'
    path.write_text(json.dumps(payload))
    with pytest.raises(music.MusicLibraryError, match='evidence changed'):
        music.plan_music_library(identification_report=path)


def test_symlinks_are_skipped(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    link = album / 'CD #1' / 'linked.mp3'
    try:
        link.symlink_to(album / 'CD #1' / '03-Maiysha.mp3')
    except OSError:
        pytest.skip('Symlink permission unavailable on this platform.')
    scan = music.scan_music_library(root, only=album.relative_to(root))
    assert scan['summary']['tracks'] == 4
    assert scan['summary']['symlinksSkipped'] == 1


def test_playlist_removed_after_scan_is_rejected(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    scan = music.scan_music_library(root, only=album.relative_to(root))
    (album / 'CD #1' / 'CD #1.m3u').unlink()
    with pytest.raises(music.MusicLibraryError, match='playlist set changed'):
        music.identify_music_library(scan_report=scan['reportPath'])


def test_preexisting_destination_is_blocked(tmp_path, monkeypatch):
    root, album, _ = _setup(tmp_path, monkeypatch)
    jazz = root / 'Jazz'
    (root / 'Blues-Jazz-Soul').rename(jazz)
    replacement_album = jazz / 'Miles Davis' / 'Agharta (256)'
    proposed = jazz / 'Miles Davis' / 'Agharta' / 'CD 01' / '01 - Prelude (Part One).mp3'
    proposed.parent.mkdir(parents=True)
    proposed.write_bytes(b'valuable existing file')
    _, _, plan = _chain(root, replacement_album)
    entry = next(t for t in plan['tracks'] if 'Prelude_(Part_One)' in t['source'])
    assert entry['status'] == 'BLOCKED'
    assert any('already exists' in note for note in entry['blockedReasons'])
    assert proposed.read_bytes() == b'valuable existing file'


def test_real_mp3_missing_tags_detected(tmp_path):
    """An actual MP3, when ffmpeg is available, must be reported as tagless."""
    import shutil
    import subprocess
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg unavailable')
    path = tmp_path / 'sample.mp3'
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
        '-i', 'anullsrc=r=22050:cl=mono', '-t', '0.15', '-map_metadata', '-1',
        '-id3v2_version', '0', str(path),
    ], check=True)
    tags, state, bitrate = music._read_tags(path)
    assert tags == {}
    assert state == 'missing'
    assert bitrate is not None


def _tagged_album(tmp_path, monkeypatch, relative_folder, tracks):
    """Provide scan evidence without depending on an audio encoder."""
    root = tmp_path / 'Music'
    album = root / relative_folder
    album.mkdir(parents=True)
    runtime = tmp_path / 'runtime'
    embedded = {}
    for name, tags in tracks:
        (album / name).write_bytes(('audio fixture: ' + name).encode('utf-8'))
        embedded[name] = tags
    (album / 'album.m3u8').write_text(
        '#EXTM3U\n' + '\n'.join(name for name, _ in tracks) + '\n', encoding='utf-8',
    )
    (album / 'cover.jpg').write_bytes(b'untouched album artwork')
    monkeypatch.setattr(music, 'runtime_root', lambda: runtime)
    monkeypatch.setattr(
        music, '_read_tags',
        lambda path: (dict(embedded[path.name]), 'available' if embedded[path.name] else 'missing', 256000),
    )
    return root, album, runtime


def _source_snapshot(root):
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob('*') if path.is_file()
    }


def test_full_single_disc_filename_remains_intact_through_preview(tmp_path, monkeypatch):
    filename = '01 - Plus tôt - Inscape - Alexandra Stréliski - (2018).mp3'
    tags = {
        'title': 'Plus tôt', 'artist': 'Alexandra Stréliski',
        'albumartist': 'Alexandra Stréliski', 'album': 'Inscape',
        'tracknumber': '1/11', 'discnumber': '1/1',
        'genre': 'Classical', 'date': '2018-10-05',
    }
    root, album, runtime = _tagged_album(
        tmp_path, monkeypatch, Path('Classical/Alexandra Stréliski/INSCAPE'), [(filename, tags)],
    )
    original = _source_snapshot(root)
    scan, identity, plan = _chain(root, album)
    item = identity['tracks'][0]
    assert item['identity']['title'] == 'Plus tôt'
    assert item['identity']['album'] == 'Inscape'
    assert item['identity']['date'] == '2018-10-05'
    assert item['identity']['track'] == 1
    assert item['identity']['disc'] == 1
    assert item['conflicts'] == []
    assert item['confidence'] == 'HIGH'
    assert plan['tracks'][0]['target'].casefold() == scan['tracks'][0]['path'].casefold()
    assert plan['tracks'][0]['renameAction'] == 'KEEP'
    assert plan['summary']['wouldRename'] == 0
    assert plan['playlists']['preserved'] == scan['playlists']
    assert all(Path(report['reportPath']).is_relative_to(runtime) for report in (scan, identity, plan))
    assert original == _source_snapshot(root)


def test_full_flat_multidisc_names_use_tags_and_preserve_basenames_and_flat_layout(tmp_path, monkeypatch):
    album_title = 'White Collection - Lion of Zion'
    tracks = []
    for number, disc, title in [('101', 1, 'Soul Shake Down'), ('211', 2, 'Mellow Mood')]:
        name = f'{number} - {title} - {album_title} - Bob Marley - (2008).mp3'
        tracks.append((name, {
            'title': title, 'artist': 'Bob Marley', 'albumartist': 'Bob Marley',
            'album': album_title, 'date': '2008', 'genre': 'Reggae',
            'tracknumber': f'{int(number[-2:])}/14', 'discnumber': f'{disc}/2',
        }))
    root, album, _ = _tagged_album(
        tmp_path, monkeypatch,
        Path('White_Collection/Bob Marley - White Collection (2008) (2 CD)'), tracks,
    )
    original = _source_snapshot(root)
    scan, identity, plan = _chain(root, album)
    by_title = {entry['identity']['title']: entry for entry in identity['tracks']}
    assert (by_title['Soul Shake Down']['identity']['disc'], by_title['Soul Shake Down']['identity']['track']) == (1, 1)
    assert (by_title['Mellow Mood']['identity']['disc'], by_title['Mellow Mood']['identity']['track']) == (2, 11)
    assert all(entry['identity']['album'] == album_title for entry in identity['tracks'])
    assert all(entry['identity']['albumArtist'] == 'Bob Marley' for entry in identity['tracks'])
    assert all(entry['conflicts'] == [] for entry in identity['tracks'])
    assert all(entry['confidence'] == 'HIGH' for entry in identity['tracks'])
    assert all(Path(entry['target']).name == Path(entry['source']).name for entry in plan['tracks'])
    assert all(entry['target'].startswith(f'Reggae/Bob Marley/{album_title}/') for entry in plan['tracks'])
    assert all(len(Path(entry['target']).parts) == 4 for entry in plan['tracks'])
    assert all(entry['status'] != 'BLOCKED' for entry in plan['tracks'])
    assert plan['summary']['wouldRename'] == 2
    assert plan['playlists']['preserved'] == scan['playlists']
    assert original == _source_snapshot(root)


def test_full_filename_accepts_album_folder_prefix_and_featured_track_artist(tmp_path, monkeypatch):
    author = 'Alicia Keys feat. Tony! Toni! Toné!'
    filename = f'07 - Diary - The Diary of Alicia Keys - {author} - (2003).mp3'
    root, album, _ = _tagged_album(
        tmp_path, monkeypatch, Path('Blues-Jazz-Soul/Alicia Keys/Alicia Keys - Diary Of Alicia Keys'),
        [(filename, {
            'title': 'Diary', 'artist': author, 'albumartist': 'Alicia Keys',
            'album': 'The Diary of Alicia Keys', 'date': '2003-12-02', 'genre': 'Soul',
            'tracknumber': '7/15', 'discnumber': '1/1',
        })],
    )
    original = _source_snapshot(root)
    _, identity, plan = _chain(root, album)
    item = identity['tracks'][0]
    assert item['identity']['artist'] == author
    assert item['identity']['albumArtist'] == 'Alicia Keys'
    assert item['identity']['album'] == 'The Diary of Alicia Keys'
    assert item['identity']['title'] == 'Diary'
    assert item['conflicts'] == []
    assert plan['tracks'][0]['status'] != 'BLOCKED'
    assert plan['tracks'][0]['proposedTags']['artist'] == author
    assert plan['tracks'][0]['proposedTags']['albumartist'] == 'Alicia Keys'
    assert Path(plan['tracks'][0]['target']).name == filename
    assert plan['tracks'][0]['target'].startswith('Soul/Alicia Keys/The Diary of Alicia Keys/')
    assert len(Path(plan['tracks'][0]['target']).parts) == 4
    assert original == _source_snapshot(root)


def test_full_format_with_delimiters_inside_title_and_album_is_not_split_generically(tmp_path, monkeypatch):
    title = 'One - Two - Three'
    album_title = 'Collected - Piano Works'
    filename = f'02 - {title} - {album_title} - A Composer - (2020).mp3'
    root, album, _ = _tagged_album(
        tmp_path, monkeypatch, Path('Classical/A Composer/Collected - Piano Works'),
        [(filename, {
            'title': title, 'artist': 'A Composer', 'albumartist': 'A Composer',
            'album': album_title, 'date': '2020', 'genre': 'Classical',
            'tracknumber': '2/10', 'discnumber': '1/1',
        })],
    )
    _, identity, plan = _chain(root, album)
    assert identity['tracks'][0]['identity']['title'] == title
    assert identity['tracks'][0]['identity']['album'] == album_title
    assert identity['tracks'][0]['conflicts'] == []
    assert plan['tracks'][0]['target'] == plan['tracks'][0]['source']


def test_tagged_single_disc_track_101_is_not_a_disc_code(tmp_path, monkeypatch):
    filename = '101 - Long Collection Finale - Piano Collection - A Composer - (2020).mp3'
    root, album, _ = _tagged_album(
        tmp_path, monkeypatch, Path('Classical/A Composer/Piano Collection'),
        [(filename, {
            'title': 'Long Collection Finale', 'artist': 'A Composer', 'albumartist': 'A Composer',
            'album': 'Piano Collection', 'date': '2020', 'genre': 'Classical',
            'tracknumber': '101/110', 'discnumber': '1/1',
        })],
    )
    _, identity, plan = _chain(root, album)
    item = identity['tracks'][0]
    assert item['identity']['track'] == 101
    assert item['identity']['disc'] == 1
    assert item['conflicts'] == []
    assert plan['tracks'][0]['status'] != 'BLOCKED'
    assert plan['tracks'][0]['target'] == plan['tracks'][0]['source']


@pytest.mark.parametrize('number,tags', [
    ('101', {}),
    ('101', {'title': 'Song', 'artist': 'An Artist', 'album': 'An Album', 'tracknumber': '1/14', 'genre': 'Jazz'}),
])
def test_ambiguous_flat_disc_codes_do_not_create_definitive_disc_identity(tmp_path, monkeypatch, number, tags):
    root, album, _ = _tagged_album(
        tmp_path, monkeypatch, Path('Jazz/An Artist/An Album'), [(f'{number} - Song.mp3', tags)],
    )
    original = _source_snapshot(root)
    _, identity, plan = _chain(root, album)
    item = identity['tracks'][0]
    assert item['identity']['disc'] is None
    if not tags:
        assert item['identity']['track'] != 1
    assert item['reviewRequired'] is True
    assert plan['tracks'][0]['status'] in {'REVIEW', 'BLOCKED'}
    assert original == _source_snapshot(root)


@pytest.mark.parametrize('updates', [
    {'title': 'A Different Song'},
    {'album': 'An Unrelated Album'},
    {'albumartist': 'An Unrelated Artist'},
    {'date': '2009'},
    {'tracknumber': '2/14'},
    {'discnumber': '2/2'},
])
def test_full_filename_never_hides_conflicting_embedded_metadata(tmp_path, monkeypatch, updates):
    tags = {
        'title': 'Soul Shake Down', 'artist': 'Bob Marley', 'albumartist': 'Bob Marley',
        'album': 'White Collection - Lion of Zion', 'date': '2008', 'genre': 'Reggae',
        'tracknumber': '1/14', 'discnumber': '1/2',
    }
    tags.update(updates)
    filename = '101 - Soul Shake Down - White Collection - Lion of Zion - Bob Marley - (2008).mp3'
    root, album, _ = _tagged_album(
        tmp_path, monkeypatch, Path('Reggae/Bob Marley/White Collection - Lion of Zion'), [(filename, tags)],
    )
    original = _source_snapshot(root)
    _, identity, plan = _chain(root, album)
    assert identity['tracks'][0]['confidence'] == 'CONFLICT'
    assert identity['tracks'][0]['conflicts']
    assert plan['tracks'][0]['status'] == 'BLOCKED'
    assert original == _source_snapshot(root)


def test_folder_album_prefix_without_full_filename_corroboration_remains_a_conflict(tmp_path, monkeypatch):
    root, album, _ = _tagged_album(
        tmp_path, monkeypatch, Path('Soul/Alicia Keys/Alicia Keys - Diary Of Alicia Keys'),
        [('07 - Diary.mp3', {
            'title': 'Diary', 'artist': 'Alicia Keys', 'albumartist': 'Alicia Keys',
            'album': 'The Diary of Alicia Keys', 'tracknumber': '7/15',
            'discnumber': '1/1', 'date': '2003-12-02', 'genre': 'Soul',
        })],
    )
    _, identity, plan = _chain(root, album)
    assert identity['tracks'][0]['confidence'] == 'CONFLICT'
    assert any(conflict.startswith('album:') for conflict in identity['tracks'][0]['conflicts'])
    assert plan['tracks'][0]['status'] == 'BLOCKED'


@pytest.mark.parametrize('relative_folder', [
    'Classical/An Unrelated Artist/Inscape',
    'Classical/Alexandra Stréliski/An Unrelated Album',
])
def test_agreeing_full_filename_and_tags_do_not_override_genuine_folder_conflicts(tmp_path, monkeypatch, relative_folder):
    filename = '01 - Plus tôt - Inscape - Alexandra Stréliski - (2018).mp3'
    root, album, _ = _tagged_album(tmp_path, monkeypatch, Path(relative_folder), [(filename, {
        'title': 'Plus tôt', 'artist': 'Alexandra Stréliski', 'albumartist': 'Alexandra Stréliski',
        'album': 'Inscape', 'date': '2018-10-05', 'genre': 'Classical',
        'tracknumber': '1/11', 'discnumber': '1/1',
    })])
    _, identity, plan = _chain(root, album)
    assert identity['tracks'][0]['confidence'] == 'CONFLICT'
    assert identity['tracks'][0]['conflicts']
    assert plan['tracks'][0]['status'] == 'BLOCKED'


def test_ordinary_short_title_delimiters_and_years_remain_part_of_title(tmp_path, monkeypatch):
    title = 'One - Two - Three (2020)'
    root, album, _ = _tagged_album(tmp_path, monkeypatch, Path('Jazz/An Artist/An Album'), [
        (f'01 - {title}.mp3', {}),
    ])
    _, identity, plan = _chain(root, album)
    assert identity['tracks'][0]['identity']['title'] == title
    assert identity['tracks'][0]['identity']['date'] is None
    assert Path(plan['tracks'][0]['target']).name == f'01 - {title}.mp3'


def test_single_disc_tags_cannot_corroborate_filename_disc_code_101(tmp_path, monkeypatch):
    root, album, _ = _tagged_album(tmp_path, monkeypatch, Path('Jazz/An Artist/An Album'), [
        ('101 - Song - An Album - An Artist - (2008).mp3', {
            'title': 'Song', 'artist': 'An Artist', 'albumartist': 'An Artist',
            'album': 'An Album', 'date': '2008', 'genre': 'Jazz',
            'tracknumber': '1/14', 'discnumber': '1/1',
        }),
    ])
    _, identity, plan = _chain(root, album)
    assert identity['tracks'][0]['confidence'] == 'CONFLICT'
    assert identity['tracks'][0]['conflicts']
    assert plan['tracks'][0]['status'] == 'BLOCKED'


@pytest.mark.parametrize('separator', ['/', ':'])
def test_sanitized_full_filename_and_parent_components_corroborate_raw_metadata(tmp_path, monkeypatch, separator):
    title = f'A{separator}Song'
    artist = f'An{separator}Artist'
    album_title = f'An{separator}Album'
    filename = '101 - A_Song - An_Album - An_Artist - (2008).mp3'
    root, album, _ = _tagged_album(tmp_path, monkeypatch, Path('Jazz/An_Artist/An_Album'), [
        (filename, {
            'title': title, 'artist': artist, 'albumartist': artist,
            'album': album_title, 'date': '2008', 'genre': 'Jazz',
            'tracknumber': '1/14', 'discnumber': '1/2',
        }),
    ])
    original = _source_snapshot(root)
    _, identity, plan = _chain(root, album)
    item = identity['tracks'][0]
    assert item['confidence'] == 'HIGH'
    assert item['conflicts'] == []
    assert item['identity']['title'] == title
    assert item['identity']['artist'] == artist
    assert item['identity']['album'] == album_title
    assert plan['tracks'][0]['target'] == plan['tracks'][0]['source']
    assert plan['tracks'][0]['renameAction'] == 'KEEP'
    assert plan['tracks'][0]['status'] == 'READY'
    assert plan['tracks'][0]['proposedTags']['title'] == title
    assert plan['tracks'][0]['proposedTags']['album'] == album_title
    assert original == _source_snapshot(root)


@pytest.mark.parametrize('relative_folder', [
    'Jazz/An-Artist/An_Album',
    'Jazz/An_Artist/An-Album',
])
def test_full_filename_does_not_treat_arbitrary_punctuation_as_folder_sanitization(tmp_path, monkeypatch, relative_folder):
    root, album, _ = _tagged_album(tmp_path, monkeypatch, Path(relative_folder), [
        ('101 - A_Song - An_Album - An_Artist - (2008).mp3', {
            'title': 'A/Song', 'artist': 'An/Artist', 'albumartist': 'An/Artist',
            'album': 'An/Album', 'date': '2008', 'genre': 'Jazz',
            'tracknumber': '1/14', 'discnumber': '1/2',
        }),
    ])
    _, identity, plan = _chain(root, album)
    assert identity['tracks'][0]['confidence'] == 'CONFLICT'
    assert identity['tracks'][0]['conflicts']
    assert plan['tracks'][0]['status'] == 'BLOCKED'


def test_wide_flat_disc_code_can_be_corroborated_without_changing_basename(tmp_path, monkeypatch):
    filename = '1001 - Song - An Album - An Artist - (2008).mp3'
    root, album, _ = _tagged_album(tmp_path, monkeypatch, Path('Jazz/An Artist/An Album'), [
        (filename, {
            'title': 'Song', 'artist': 'An Artist', 'albumartist': 'An Artist',
            'album': 'An Album', 'date': '2008', 'genre': 'Jazz',
            'tracknumber': '1/14', 'discnumber': '1/2',
        }),
    ])
    _, identity, plan = _chain(root, album)
    item = identity['tracks'][0]
    assert (item['identity']['disc'], item['identity']['track']) == (1, 1)
    assert item['confidence'] == 'HIGH'
    assert item['conflicts'] == []
    assert plan['tracks'][0]['target'] == plan['tracks'][0]['source']
    assert plan['tracks'][0]['renameAction'] == 'KEEP'
