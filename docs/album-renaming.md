# Rename and tag an existing album

Give Mnemosyne an album folder and the MusicBrainz **release page** for its
exact edition. It reads the album's ordered track list, matches the local audio,
and previews canonical filenames and tags before writing anything.

```powershell
uv run mnemosyne album
```

The command asks for the album folder, offers a MusicBrainz search link, then
asks for the selected release URL. A release-group page covers multiple editions;
choose a specific release with the same track list as the folder.

For a repeatable preview:

```powershell
uv run mnemosyne album "D:\Music\Artist\Album" --source-url "https://musicbrainz.org/release/7516696d-6a9f-4166-84ee-5d27416e1ef1"
```

The example URL identifies *Inscape* by Alexandra Stréliski. Replace both the
folder and URL for another album. The folder can be anywhere; it does not have
to be beneath Mnemosyne's configured library directory.

The `album` command defaults to filenames in this order:

```text
track - title - album - author - (year).ext
01 - Plus tôt - Inscape - Alexandra Stréliski - (2018).mp3
```

Here `author` means the track's credited artist, falling back to the album artist
when the track credit is absent. The filename uses the selected MusicBrainz
release's four-digit year. Embedded date tags retain its complete known date,
such as `2018-10-05`. Each filename component is made safe for Windows while
Unicode spelling and the original audio extension are retained. Full names
require a valid known release year; Mnemosyne blocks the preview if it is missing.

For the shorter `01 - Plus tôt.mp3` naming convention, including releases without
a known year, select `--filename-format track-title`:

```powershell
uv run mnemosyne album "D:\Music\Artist\Album" --source-url "https://musicbrainz.org/release/7516696d-6a9f-4166-84ee-5d27416e1ef1" --filename-format track-title
```

The direct Python `preview_album` API retains `track-title` as its default;
pass `filename_format=AlbumFilenameFormat.FULL` to request the full convention.

After checking the displayed track mapping, run the same command with `--apply`.
Mnemosyne displays the preview again and asks for confirmation. `--yes` may be
used with `--apply` when that confirmation is already provided by the caller.

Written metadata includes title, track artist, album artist, album, track and
disc numbers, and the selected release's date when known. Genre is retained;
it is not inferred from the directory or artist. Existing artwork and unrelated
tags are retained. MP3 uses ID3 tags; FLAC and M4A use their native metadata.

The operation copies and tags the audio without re-encoding it, verifies tags
and compressed audio payloads, updates supported album playlists, and retains
original files plus a durable transaction report outside the album. The default
backup location is `$HOME/Mnemosyne/state/album-transactions`; select another
location with `--backup-root`.

Track counts and mapping evidence must agree. Unknown or ambiguous matches,
conflicting names, changed files, unsupported formats, and unsafe references
block mutation rather than guessing. The first version supports flat,
single-disc albums in MP3, FLAC, and M4A. Multi-disc releases and nested audio
folders are blocked until disc matching is implemented. The scope is one chosen
album folder; playlists outside that folder require separate handling.

MusicBrainz is the first implemented metadata source. Other sites require their
own adapters; arbitrary web pages are not scraped as trusted track data.

API reference: <https://musicbrainz.org/doc/MusicBrainz_API>.
