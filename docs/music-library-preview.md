# Music library recovery — first read-only slice

The Music path is intentionally separate from eBook identification. It does not
modify music files, tags, playlists, or directories. Reports are written to
`$HOME/Mnemosyne/state/` only.

## Agharta acceptance test

With an album structured as:

```text
P:\Music\Blues-Jazz-Soul\Miles Davis\Agharta (256)\
  CD #1\01-Prelude_(Part_One).mp3
  CD #1\02-Prelude_(Part_Two).mp3
  CD #1\03-Maiysha.mp3
  CD #1\CD #1.m3u
  CD #2\01-Interlude.mp3
  CD #2\CD #2.m3u
```

Run these commands from the repository, without modifying the configured default:

```powershell
uv run mnemosyne library-scan --media music --music-root "P:\Music" --only "Blues-Jazz-Soul\Miles Davis\Agharta (256)"
uv run mnemosyne library-identify --media music
uv run mnemosyne library-plan --media music
```

Review the complete JSON reports printed by those commands. A scan is scoped to
only one album, but retains the full path context from `P:\Music`.

Expected: four audio tracks, two preserved playlists. Metadata is recovered
from `Miles Davis`, `Agharta (256)`, `CD #1` / `CD #2`, and numbered filenames.
The `(256)` suffix is treated only as a *possible bitrate label*, never as a
work year or a verified audio bitrate. Source bitrates are read independently
when supported by the metadata reader.

`Blues-Jazz-Soul` is ambiguous. The preview lists Blues, Jazz and Soul as
candidates and **blocks canonical destination planning until the genre is
resolved**. A year is not invented: `1975` must be separately verified before
being written. The original `.m3u` playlists are preserved; future mutation
must rewrite playlist references transactionally or deliberately retain them.

## Albums already renamed by Mnemosyne

The preview recognizes `track - title - album - author - (year)` names when the
complete filename agrees with embedded metadata. Titles, albums, and artist
credits can themselves contain ` - `; the preview checks the complete metadata
suffix instead of splitting those fields by separator. Featured track artists
remain separate from the album artist used for folders.

For flat multi-disc albums, `101` and `201` identify disc 1/track 1 and disc
2/track 1 only when the embedded disc/track information corroborates that
interpretation. A genuine single-disc track 101 stays track 101. Missing or
conflicting evidence requires review; the preview does not guess a disc from
the leading digits alone.

Proposed destinations retain a recognized full filename and the album's flat
or disc-folder layout. Ambiguous genres and genuine filename, folder, or tag
conflicts still block acceptance. The preview never applies those proposals.

## Safety and next steps

- No `--apply` option exists for Music in this slice.
- The read-only scan saves paths, sizes, nanosecond timestamps and embedded tag evidence.
- Identification checks for added/removed/changed audio files before using a scan.
- Planning reconfirms the scan, checks recorded identity and records SHA-256 hashes.
- Genre conflicts or insufficient evidence block automatic acceptance.
- Use small album-scoped tests before attempting a full-library scan.
- **A subsequent separate phase** will support explicit review of genre/date,
  safe metadata writes to a *copy*, verification in Cadence Studio, then
  transactional rename/move with playlist rewrites and rollback.
