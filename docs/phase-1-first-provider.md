# Phase 1 — First Provider

## First fixture

Mnemosyne's first real provider fixture is:

```text
https://archive.org/details/animal-farm.sna
```

The item is useful because it contains both playable audio and Archive-generated/support files. It also exposes imperfect source metadata that requires planning and user-visible uncertainty rather than silent guessing.

## First milestone

The first implementation is deliberately **plan-only**.

It must:

1. Accept one Archive.org item URL.
2. Resolve the Archive identifier.
3. Fetch Archive's metadata/file manifest.
4. Classify files.
5. Exclude non-playable helper files such as `.afpk`.
6. Rank playable audio candidates.
7. Prefer actual audio quality while considering Archive's original/derivative marker.
8. Clean obvious source branding from the title when safely derivable.
9. Resolve the canonical library destination.
10. Identify a likely cover candidate.
11. Display warnings for missing/ambiguous metadata.
12. Write **no media**.

## Hard rule: actual playable media only

Audiobook/music acquisition candidates must be real playable audio files.

Examples of potentially playable formats:

```text
.flac
.wav
.aiff
.m4a
.m4b
.mp3
.ogg
.opus
.aac
.wma
```

Files such as `.afpk`, metadata XML, playlists, torrents, databases, spectrograms, and other Archive support derivatives are not media acquisition candidates.

## Ranking principle

"Original" is useful provenance but is not an absolute quality guarantee.

Mnemosyne should rank using a combination of:

- Is it actually playable?
- Lossless vs lossy
- Codec/container
- Available bitrate
- Archive original vs derivative status
- File size as a weak tie-breaker

The selected candidate and the reason for selection must be visible in the plan.

## Unknown metadata

Mnemosyne must not silently invent missing metadata.

The prototype accepts verified overrides such as:

```powershell
mnemosyne plan audiobook "https://archive.org/details/animal-farm.sna" --year 1945
```

External metadata enrichment will be added as a separate provider/enrichment concern rather than hard-coded into Archive.org parsing.
