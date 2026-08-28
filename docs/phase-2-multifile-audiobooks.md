# Phase 2 — Multi-file Audiobook Editions

Archive.org and LibriVox commonly expose the same audiobook in several representations:

- one complete M4B
- an ordered set of original MP3 chapter files
- an ordered set of derivative Ogg files
- a second ordered set of lower-bitrate derivative MP3 files

Mnemosyne now models those as **audio editions** instead of pretending every chapter file is an independent audiobook candidate.

## Planning

Example:

```powershell
mnemosyne plan audiobook <url> --year 1898
```

The plan now shows `Playable audio editions`.

A complete single-file edition remains the default when it outranks a multi-file set.

To deliberately choose a complete MP3 chapter edition:

```powershell
mnemosyne plan audiobook <url> --year 1898 --audio-format mp3
```

The override chooses the highest-ranked **MP3 edition**, not an arbitrary individual MP3 chapter.

## Edition grouping

A chapter set is grouped only when files share:

- extension
- Archive format
- Archive source class
- a common filename pattern with an ordered numeric token

This keeps, for example:

```text
VBR MP3 originals
```

separate from:

```text
64Kbps MP3 derivatives
```

even when both contain chapters 01–18.

## Safe Fetch v2

`fetch --apply --audio-format mp3` downloads the entire selected chapter set into:

```text
<job>\
├── audio\
│   ├── 01 - Chapter 01.mp3
│   ├── 02 - Chapter 02.mp3
│   └── ...
├── cover.jpg
└── fetch-report.json
```

Every chapter receives the same safeguards as a single-file fetch:

- expected size validation
- audio signature validation
- SHA-256
- actual codec inspection
- provider-vs-actual quality cross-check

If any chapter fails, the new staging job is removed rather than leaving a partial edition presented as valid.

## Deliberate boundary

This slice stops after multi-file staging.

The existing metadata, readiness, placement, completion, and cleanup commands remain single-file-oriented until the next slice. That boundary is intentional: Mnemosyne first proves that it can identify and fetch a complete ordered edition safely before mutation logic is generalized across many files.
