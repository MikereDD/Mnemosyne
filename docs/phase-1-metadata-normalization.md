# Phase 1 — Transactional Metadata Normalization

Metadata Write v1 introduces the first tag mutation in Mnemosyne.

The initial implementation deliberately supports only MP4-family staged audio (`.m4a`, `.m4b`, `.mp4`). MP3/FLAC tagging will receive format-specific implementations later rather than being forced through a generic writer.

## Preview

```powershell
mnemosyne tag
```

This is read-only. It shows the canonical metadata and artwork source that would be written.

## Apply

```powershell
mnemosyne tag --apply
```

Or target a specific staging job:

```powershell
mnemosyne tag "$HOME\Mnemosyne\staging\<job-id>" --apply
```

## Safety requirements

Tagging is blocked while `fetch-report.json` contains unresolved warnings.

The mutation workflow is:

1. Resolve the staged canonical audio and canonical metadata.
2. Copy the audio to a same-job temporary working file.
3. Preserve existing unrelated MP4 atoms where practical.
4. Write canonical MP4 metadata atoms to the working copy.
5. Embed the verified standalone JPEG/PNG cover when available.
6. Reopen the working copy.
7. Verify every canonical metadata field.
8. Verify embedded artwork bytes by SHA-256.
9. Hash the completed working copy.
10. Preserve the untouched pre-tag audio and pre-tag report under `rollback\`.
11. Atomically replace the staged canonical audio with the verified working copy.
12. Reopen and verify the actual canonical file a second time.
13. Atomically update `fetch-report.json`.

If a failure occurs after the staged canonical file has been replaced, Mnemosyne attempts to restore the pre-tag audio and report from rollback.

## Canonical audiobook tags

Metadata Write v1 writes:

```text
title
artist
album_artist
album
date
genre = Audiobook
```

For MP4/M4A/M4B these map to:

```text
©nam
©ART
aART
©alb
©day
©gen
```

Standalone cover art remains in staging even after embedding.

## Provenance

`fetch-report.json` advances to schema version 5 and records:

- pre-tag SHA-256
- post-tag SHA-256
- exact written metadata
- embedded artwork SHA-256
- rollback audio/report paths
- post-write verification status
- metadata normalization history

## Safety boundary

Metadata normalization still does not perform final-library placement.

The next major boundary is final staged verification followed by a separate explicit library-placement transaction.
