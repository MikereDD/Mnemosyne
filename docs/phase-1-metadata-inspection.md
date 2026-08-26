# Phase 1 — Read-Only Metadata Inspection

This slice adds read-only inspection of verified staged audio before Mnemosyne writes metadata.

## Dependency

Mnemosyne uses `mutagen` for container/tag inspection.

## Command

Inspect the most recent completed staging job:

```powershell
mnemosyne inspect
```

Or inspect a specific job directory:

```powershell
mnemosyne inspect "$HOME\Mnemosyne\staging\<job-id>"
```

## Current inspection

Where supported by the audio container, Mnemosyne reports:

- container/parser type
- codec/codec description
- duration
- bitrate
- sample rate
- channels
- existing embedded tags
- embedded artwork count and format
- embedded chapters

For a staged acquisition, it also derives the proposed canonical metadata from `fetch-report.json`.

For the first audiobook fixture, the proposed baseline is:

```text
title        = Animal Farm
artist       = George Orwell
album_artist = George Orwell
album        = Animal Farm
date         = 1945
genre        = Audiobook
```

The inspection output compares current values with proposed values and marks each field as either:

```text
KEEP
CHANGE
```

## Safety boundary

This command is strictly read-only.

It does not:

- write tags
- remove existing tags
- embed or replace artwork
- alter chapters
- rename staged files
- move anything into the final library

The next slice can implement tag mutation only after the proposed metadata has been reviewed against real-world files.
