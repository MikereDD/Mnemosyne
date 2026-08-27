<p align="center">
  <img src="assets/mnemosyne-avatar.png" alt="Mnemosyne" width="320">
</p>

<h1 align="center">Mnemosyne</h1>

<p align="center">
  <strong>Media acquisition that understands what the library should become.</strong>
</p>

<p align="center">
  A safety-first acquisition, normalization, and verification pipeline for
  ebooks, audiobooks, and music.
</p>

<p align="center">
  <a href="https://www.python.org/">
    <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white">
  </a>
  <a href="LICENSE.md">
    <img alt="GPL-3.0-or-later" src="https://img.shields.io/badge/License-GPL--3.0--or--later-663399">
  </a>
  <img alt="Status: Development" src="https://img.shields.io/badge/Status-Development-E9A23B">
  <img alt="CLI: Typer + Rich" src="https://img.shields.io/badge/CLI-Typer%20%2B%20Rich-5B5BD6">
</p>

<p align="center">
  <a href="#development-quick-start"><strong>Quick Start</strong></a>
  &nbsp;•&nbsp;
  <a href="#what-mnemosyne-does"><strong>Features</strong></a>
  &nbsp;•&nbsp;
  <a href="#safety-model"><strong>Safety Model</strong></a>
  &nbsp;•&nbsp;
  <a href="docs/architecture.md"><strong>Architecture</strong></a>
  &nbsp;•&nbsp;
  <a href="docs/roadmap.md"><strong>Roadmap</strong></a>
</p>

---

## What is Mnemosyne?

Most download tools stop when the bytes arrive.

**Mnemosyne does not.**

Mnemosyne is a provider-based media acquisition pipeline built around the idea
that a successful acquisition is not merely a downloaded file — it is media
that has been identified, validated, normalized, verified, placed into a
deterministic library structure, and proven correct before temporary evidence
is removed.

Its core workflow is:

```text
Discover → Identify → Preview / Plan → Fetch → Normalize → Verify → Place → Complete
```

Every mutating stage is designed to make its intent visible first and to refuse
unsafe shortcuts such as silent overwrites, partial multi-file placement, or
unverified cleanup.

## What Mnemosyne does

| Capability | Status |
| --- | :---: |
| Internet Archive provider | ✅ |
| Read-only acquisition planning | ✅ |
| Provider metadata and media candidate discovery | ✅ |
| Playable-audio filtering | ✅ |
| Audio edition grouping | ✅ |
| Single-file audiobook acquisition | ✅ |
| Multi-file audiobook acquisition | ✅ |
| Bounded download retry handling | ✅ |
| Actual codec / quality inspection | ✅ |
| Candidate quality comparison | ✅ |
| Transactional source adoption | ✅ |
| MP4 / M4A / M4B metadata | ✅ |
| MP3 / ID3 metadata | ✅ |
| FLAC metadata support | ✅ |
| Cover retrieval and embedding | ✅ |
| Transactional metadata normalization | ✅ |
| Whole-edition rollback for chapter sets | ✅ |
| Readiness certification | ✅ |
| Atomic final-library placement | ✅ |
| Completion certification | ✅ |
| Durable completion receipts | ✅ |
| Explicit staging cleanup | ✅ |
| Fetch-list pruning with confirmation | ✅ |
| eBook acquisition pipeline | 🚧 |
| Music acquisition pipeline | 🚧 |
| Cross-run interrupted-job resume | 🚧 |
| Additional providers | 🚧 |

> **Development status:** Mnemosyne is actively being built and tested against
> real acquisitions. Interfaces and report schemas may still change before the
> first stable release.

## Safety model

Mnemosyne treats acquisition as a sequence of increasingly trusted states.

### Preview before mutation

Commands that can alter staged media or the final library provide a preview
path first wherever practical.

### Staging before placement

Downloaded media is isolated beneath the Mnemosyne runtime staging directory.
The final library is not modified during download, normalization, tagging, or
readiness verification.

### Verify what was actually downloaded

Provider labels are treated as claims until the downloaded media is inspected.
Actual container, codec, bitrate, lossless/lossy state, file signatures, size,
and SHA-256 evidence are used to validate acquisition results.

### Whole-edition transactions

For multi-file audiobooks, a chapter set is treated as one edition rather than
a bag of unrelated files.

Metadata normalization prepares and verifies every working copy before staged
canonical files are replaced. Final placement copies the complete edition into
a hidden sibling directory, verifies it, and commits the entire directory with
one rename.

No half-placed audiobook is considered success.

### No silent destructive overwrite

Existing final destinations are not merged into or overwritten automatically.

### Cleanup requires proof

Staging cleanup is explicit. Before deletion, Mnemosyne re-verifies the final
library and writes a durable completion receipt containing final provenance and
hash evidence.

## Proven acquisition lifecycle

The current implementation has been exercised end-to-end with both single-file
and multi-file audiobook acquisitions.

For a multi-file edition, the lifecycle includes:

```text
Discover provider item
        ↓
Identify playable editions
        ↓
Select a complete chapter set
        ↓
Fetch every chapter + cover
        ↓
Verify size / signature / SHA-256 / actual codec
        ↓
Normalize metadata transactionally
        ↓
Embed cover + write track order
        ↓
Verify every chapter again
        ↓
Certify staged readiness
        ↓
Prepare complete final directory
        ↓
Atomic directory placement
        ↓
Verify final edition
        ↓
Certify completion
        ↓
Write durable receipt
        ↓
Explicitly remove staging
```

## Canonical library layouts

### Audiobooks — single file

```text
Author/
└── Audiobook/
    └── Title - Author (Date)/
        ├── Title - Author (Date).m4b
        └── cover.jpg
```

### Audiobooks — chapter set

```text
Author/
└── Audiobook/
    └── Title - Author (Date)/
        ├── 01 - Chapter Title.mp3
        ├── 02 - Chapter Title.mp3
        ├── ...
        └── cover.jpg
```

### eBooks

```text
Author/
└── eBook/
    └── Title - Author (Date)/
        ├── Title - Author (Date).epub
        ├── Title - Author (Date).pdf
        └── cover.jpg
```

### Music

```text
Band/
└── Band Name - Album (Date)/
    ├── 01 - Track Title.flac
    ├── 02 - Track Title.flac
    └── cover.jpg
```

See [`docs/library-layout.md`](docs/library-layout.md) for the evolving library
conventions.

## Development quick start

Mnemosyne is currently a development project rather than a packaged stable
release. The supported way to try it today is directly from the repository.

### Requirements

- Python **3.12+**
- [`uv`](https://docs.astral.sh/uv/) recommended
- Network access for provider-backed acquisition

Clone the repository and sync the development environment:

```powershell
git clone https://github.com/MikereDD/Mnemosyne.git
cd Mnemosyne
uv sync
```

Initialize Mnemosyne's per-user runtime directories and configuration:

```powershell
uv run mnemosyne init
```

Run the CLI:

```powershell
uv run mnemosyne --help
```

Run the test suite:

```powershell
uv run pytest
```

## Example workflow

Plan an Internet Archive audiobook acquisition without changing anything:

```powershell
uv run mnemosyne plan audiobook `
  "<archive-item-url>" `
  --year 1945
```

Fetch into isolated staging:

```powershell
uv run mnemosyne fetch audiobook `
  "<archive-item-url>" `
  --year 1945 `
  --apply
```

Then advance the staged job deliberately:

```powershell
uv run mnemosyne tag
uv run mnemosyne tag --apply

uv run mnemosyne ready

uv run mnemosyne place
uv run mnemosyne place --apply

uv run mnemosyne complete
uv run mnemosyne complete --apply

uv run mnemosyne cleanup
```

Destructive cleanup requires the exact job ID shown by the cleanup preview:

```powershell
uv run mnemosyne cleanup --apply `
  --confirm <job-id>
```

## Runtime layout

Mnemosyne keeps working state separate from the media library:

```text
$HOME/Mnemosyne/
├── config/
├── fetch/
├── logs/
├── state/
│   └── completed/
├── staging/
└── cache/
```

Default acquisition output is organized below:

```text
$HOME/Downloads/Mnemosyne/
├── Audiobooks/
├── eBooks/
└── Music/
```

No username-specific absolute paths are required by the application.

## Fetch queues

Human-owned text queues are intentionally simple:

```text
$HOME/Mnemosyne/fetch/
├── audiobook-links.txt
├── ebook-links.txt
└── music-links.txt
```

One URL per line. Blank lines and comments are ignored.

Mnemosyne does not silently delete completed entries. Pruning is a separate,
explicit operation with preview, confirmation, backup, and atomic rewrite
behavior.

## Architecture

Mnemosyne separates provider-specific discovery from the acquisition lifecycle.

Provider adapters are responsible for concepts such as:

```text
identify(url)
discover(...)
get_metadata(...)
get_media_candidates(...)
get_cover_candidates(...)
prepare_download(...)
```

The rest of the pipeline works with normalized models and provenance rather
than provider-specific page structure.

See:

- [`docs/design-foundation.md`](docs/design-foundation.md) — project philosophy and original design direction
- [`docs/architecture.md`](docs/architecture.md) — architecture and provider boundaries
- [`docs/library-layout.md`](docs/library-layout.md) — deterministic library conventions
- [`docs/roadmap.md`](docs/roadmap.md) — implementation roadmap

## Design principles

- **Understand before changing.**
- **Preview before mutation.**
- **Preserve source quality whenever practical.**
- **Treat provider metadata as provisional until verified.**
- **Never claim success for a partial edition.**
- **Never silently overwrite an existing library destination.**
- **Keep rollback evidence until the next state is proven.**
- **Use deterministic, human-readable paths.**
- **Keep provider-specific logic behind adapters.**
- **Verify the final library before deleting staging evidence.**

## Contributing

Mnemosyne is still in active development. Issues, test cases, provider edge
cases, and carefully scoped improvements are welcome.

Changes touching acquisition safety, placement, rollback, verification, or
cleanup should preserve the project's central rule:

> **A convenient failure is better than an unverified success.**

## License

Mnemosyne is free software licensed under the
[GNU General Public License v3.0 or later](LICENSE.md).

You may use, study, modify, and redistribute Mnemosyne under the terms of the
GPL. If you distribute a modified version or other covered derivative work, the
GPL's source-availability and copyleft requirements apply.

See [`LICENSE.md`](LICENSE.md) for the complete license text.
