# Mnemosyne Design Foundation

> This document records Mnemosyne's agreed design direction. Some entries describe future goals rather than current functionality and may evolve as real-world testing informs the design.

## Project identity

- **Name:** Mnemosyne
- Standalone repositories are maintained on Forgejo and GitHub.
- First-class media families:
  - eBooks
  - Audiobooks
  - Music
  - Movies
  - TV
  - Documentary
- Mnemosyne is more than a downloader: it is a media acquisition, organization, normalization, and verification system.
- Acquisition and existing-library normalization are first-class workflows that share the same identity, planning, safety, provenance, and verification machinery.


## Core workflows

Acquisition:

**Discover → Identify → Preview/Plan → Fetch → Normalize → Verify → Place → Complete**

Existing-library normalization:

**Scan → Identify → Classify → Preview/Plan → Normalize → Rename/Move → Verify**

Both workflows must converge on shared internal models rather than developing
independent rules.

Core principles:

- Preview before mutation.
- Organize by stable structural relationships; keep descriptive ambiguity in metadata.
- Never silently guess when identification confidence is low.
- Preserve metadata provenance for decisions that affect canonical placement.
- Preserve original media quality whenever practical.
- Never silently overwrite valuable existing media.
- Keep provider-specific behavior behind provider interfaces.
- Keep destination naming deterministic and human-readable.
- Verify the final result before declaring success.
- Improve the design from real acquisitions rather than inventing unnecessary complexity.

## Runtime and user-data layout

Live Mnemosyne data should stay outside both the source repository and the media download tree.

```text
$HOME\Mnemosyne\
├── config\
├── fetch\
├── logs\
├── state\
├── staging\
└── cache\
```

Possible later additions:

```text
$HOME\Mnemosyne\
├── quarantine\
└── config\
    └── profiles\
```

The repository may contain example/default configuration, but never the user's live configuration.

```text
repo\config\config.example.json
```

Live configuration:

```text
$HOME\Mnemosyne\config\config.json
```

## Default media root

Default acquisition/library root:

```text
$HOME\Downloads\Mnemosyne\
├── Audiobooks\
├── eBooks\
├── Music\
├── Movies\
├── TV\
└── Documentary\
```

The user may change this through configuration.

Changing the configured library root must **not** automatically move existing media. Any future library-move operation should be separate, explicit, previewed, and confirmed.

## Canonical library layouts

Canonical structural model:

```text
Music        → Genre / Artist / Album
eBooks       → Author / Series? / Book
Audiobooks   → Author / Series? / Book
Movies       → Title (Year)
Documentary  → Title (Year)
TV           → Series (Start Year - End Year/Continuing)
               └── Season NN (Premiere Year)
```

### Filesystem genre policy

Music uses a canonical **base genre** for physical organization. Detailed
sub-genres remain metadata. For example, `Celtic Punk` may be tagged as such
while being physically placed beneath `Music/Punk/...`.

eBooks, audiobooks, movies, TV, and documentaries do not use genre as a
physical hierarchy. Genre remains metadata so works are not fragmented across
subjective or overlapping categories.

### Series and lifecycle policy

Book/audiobook series membership is structural when known, so an optional
series layer is used between author and book.

TV uses series lifetime and season premiere year as structural information:

```text
Series Title (2024 - Continuing)/
└── Season 01 (2024)/
```

A completed series becomes:

```text
Series Title (2024 - 2027)/
```

A one-year completed series may use:

```text
Series Title (2019)/
```

`Continuing` must never be inferred merely from a missing end year. The active
status must be positively identified; otherwise planning should expose the
ambiguity.

Detailed canonical examples live in `docs/library-layout.md`.

## Naming and metadata goals

Mnemosyne should normalize:

- Author and band names
- Book titles and album titles
- Publication/release dates
- Track/chapter numbering
- Track/chapter titles
- Destination folder names
- Destination filenames
- Embedded metadata where supported

Naming should remain:

- Human-readable
- Searchable
- Predictable
- Stable
- Script-friendly
- Unicode-safe

No hard-coded usernames or user-specific paths.

## Cover handling

Cover/artwork retrieval is part of the normal workflow for supported media families where artwork is applicable.

Canonical standalone filename:

```text
cover.jpg
```

Mnemosyne should:

- Prefer the best suitable high-resolution artwork.
- Prefer official or edition/release-appropriate artwork.
- Avoid poor thumbnails where better artwork exists.
- Avoid watermarked artwork when practical.
- Embed artwork into supported media without unnecessary transcoding.
- Preserve the standalone `cover.jpg` even when artwork is embedded.

For audiobooks, audiobook-specific artwork should be preferred when it identifies the actual narrator/publisher/edition. For music, prefer official square album artwork for the selected release.

## Existing-library normalization

Mnemosyne should organize media the user already owns using the same safety
philosophy as acquisition.

Identification precedence:

1. Embedded metadata.
2. Existing folder structure.
3. Filename parsing.
4. External metadata lookup only when needed.

A normalization scan should remain read-only until a plan is approved.

Potential findings include:

- Artists located beneath the wrong canonical base genre
- Over-specific music genre folders
- Inconsistent artist/author/title naming
- Incorrect or missing series structure
- Duplicate album/book/movie folders
- Stray tracks, chapters, or episodes
- Inconsistent TV season naming/year structure
- Missing or inconsistent artwork
- Metadata conflicts
- Low-confidence identities requiring review

The organizer should support incremental approval rather than requiring an
entire library to be reorganized in one operation.

Cross-format understanding is also a roadmap goal. Mnemosyne should eventually
be able to recognize an eBook and audiobook as two representations of the same
underlying work while keeping their physical libraries separate.


## Single-link acquisition

Mnemosyne should support immediate acquisition of one item.

Possible CLI shape:

```text
mnemosyne fetch audiobook <url>
mnemosyne fetch ebook <url>
mnemosyne fetch music <url>
```

A single-link job must enter the same internal pipeline used by batch jobs.

Before application, the plan should show at least:

- Provider
- Media type
- Author/band
- Title/album
- Date
- Selected source files
- Destination path
- Cover source
- Rename operations
- Metadata operations
- Verification steps

## Human-editable fetch queues

Permanent queue files should live under:

```text
$HOME\Mnemosyne\fetch\
├── audiobook-links.txt
├── ebook-links.txt
└── music-links.txt
```

Rules:

- One URL per line.
- Blank lines are ignored.
- Lines beginning with `#` are comments.
- Queue files stay intentionally simple.
- Machine processing state is never written into the queue files.

Example:

```text
# Arthur C. Clarke
https://archive.org/details/example-one

# Another item
https://archive.org/details/example-two
```

## Adding links safely

Future commands may include:

```text
mnemosyne links add audiobook
mnemosyne links add ebook
mnemosyne links add music
```

Paste mode should:

- Accept one or more URLs.
- Validate each URL.
- Detect duplicates already in the queue.
- Report new vs existing entries.
- Preview changes.
- Confirm before writing.
- Append atomically.

Possible direct form:

```text
mnemosyne link add audiobook <url>
```

Possible stdin form:

```powershell
Get-Clipboard | mnemosyne links add audiobook --stdin
```

## Machine-managed state

The fetch lists belong to the user. Processing state belongs to Mnemosyne.

Initial state may live in something such as:

```text
$HOME\Mnemosyne\state\fetch-ledger.jsonl
```

A later SQLite database remains an option.

Possible job states:

```text
discovered
identified
planned
fetching
downloaded
normalizing
verifying
complete
failed
needs-attention
```

All queued URLs remain in their source text files until the user explicitly chooses to prune confirmed-complete entries.

## Definition of completion

A downloader exit code alone does not mean the job is complete.

A completed job should mean the expected pipeline succeeded:

```text
Download succeeded            ✓
Expected media found          ✓
Rename completed              ✓
Cover retrieved               ✓
Metadata/tagging completed    ✓
Final destination completed   ✓
Verification completed        ✓
Ledger updated                ✓

STATUS: COMPLETE
```

A problem should become `FAILED` or `NEEDS ATTENTION` rather than being silently treated as success.

## Safe queue cleanup

Mnemosyne should never silently delete queued URLs.

Possible commands:

```text
mnemosyne links prune audiobook
mnemosyne links prune ebook
mnemosyne links prune music
mnemosyne links prune all
```

Pruning should:

- Match queue entries only against confirmed-complete jobs.
- Preview eligible entries.
- Default confirmation to **No**.
- Preserve a safe backup or atomic replacement.
- Leave failed, unprocessed, and needs-attention URLs untouched.

Safety invariant:

> A URL is never considered done until the final media result has been verified, and Mnemosyne never deletes a user's queued URL without explicit cleanup and confirmation.

## Queue status

Possible command:

```text
mnemosyne links status
```

Example:

```text
Mnemosyne Fetch Queues

Audiobooks
  Links       : 14
  Complete    : 9
  Pending     : 3
  Failed      : 1
  Attention   : 1

eBooks
  Links       : 27
  Complete    : 25
  Pending     : 2

Music
  Links       : 8
  Complete    : 8

Total pending work: 7
```

## Batch acquisition

Possible commands:

```text
mnemosyne fetch audiobooks
mnemosyne fetch ebooks
mnemosyne fetch music
mnemosyne fetch all
```

Single-link and batch input must converge on the same job engine.

## Plan-only mode

Plan/dry-run behavior is a first-class requirement.

Possible examples:

```text
mnemosyne fetch audiobook <url> --plan
mnemosyne fetch audiobooks --plan
mnemosyne fetch all --plan
```

Plan mode should:

- Discover and identify media.
- Resolve destination paths.
- Detect conflicts.
- Show planned actions.
- Avoid writing final media.
- Avoid destructive mutation.

A later explicit execution flag may be:

```text
--apply
```

## Job model

Every item should eventually become an internal job regardless of input source.

Potential sources:

- Single CLI URL
- Fetch queue
- Future YAML manifest
- Future interactive terminal
- Future GUI
- Future inbox/watch mechanism

Each job may record:

- Stable job ID
- Source URL
- Provider
- Media type
- Detected metadata
- Confidence values
- Selected source files
- Destination
- Current state
- Error information
- Verification result
- Acquisition timestamp
- Mnemosyne version

## Provider architecture

Archive.org is expected to be an early provider, but Mnemosyne should not be architected around a single source.

A provider may eventually supply:

- Search/discovery
- Item metadata
- Available files/formats
- Download URLs
- Source checksums
- Cover candidates
- Access/rights indicators
- Resume/retry information

All providers should feed the same planning/acquisition engine.

## Candidate ranking

When multiple source files or providers are available, Mnemosyne may eventually rank candidates using:

- Completeness
- Original/source quality
- Lossless vs lossy
- Bitrate
- Format preference
- Metadata quality
- Cover availability
- Source reliability
- Edition/release match

The user should be able to override recommendations.

## Metadata confidence

Mnemosyne should not hide uncertainty.

Example:

```text
Author : Arthur C. Clarke   99%
Title  : Childhood's End    99%
Year   : 1953               92%
Cover  : 1953 edition       76%
```

Low-confidence values may trigger `NEEDS ATTENTION` rather than silent guessing.

## Edition and release awareness

Books/audiobooks should eventually distinguish the underlying work from the acquired edition, including narrator, publisher, abridged/unabridged status, and audiobook release date where available.

Music should similarly distinguish:

- Original album year
- Specific release/remaster year
- Deluxe edition
- Reissue

## Staging

Downloads should eventually land in:

```text
$HOME\Mnemosyne\staging\
```

Expected pipeline:

```text
Download to staging
        ↓
Validate
        ↓
Rename
        ↓
Fetch cover
        ↓
Tag/embed
        ↓
Verify
        ↓
Move into final library
```

Partial or failed acquisitions should not appear as completed library items.

## Quarantine

Possible later path:

```text
$HOME\Mnemosyne\quarantine\
```

Useful for downloaded files that fail validation, such as:

- Corrupt audio
- Broken EPUB
- Zero-byte files
- HTML/error pages masquerading as media
- Incomplete downloads
- Invalid archives
- Unexpected source formats

## Configuration

Live configuration:

```text
$HOME\Mnemosyne\config\config.json
```

Possible commands:

```text
mnemosyne config show
mnemosyne config validate
mnemosyne config set ...
mnemosyne config edit
mnemosyne config wizard
mnemosyne paths
```

Configuration-changing commands should:

- Expand `$HOME` and environment variables.
- Normalize and validate paths.
- Reject malformed or obviously dangerous destinations.
- Show old and proposed values.
- Preview directories that will be created.
- Ask for confirmation.
- Write atomically.
- Preserve the previous configuration.
- Validate after writing.

## Profiles

Possible later structure:

```text
$HOME\Mnemosyne\config\profiles\
├── default.json
├── archival.json
└── portable.json
```

Profiles may define destination roots, quality/format preferences, cover policy, metadata policy, and overwrite behavior.

## Persistent ledger and provenance

A persistent library ledger may eventually record:

- Provider and source URL
- Provider item ID
- Media type
- Author/band
- Title/album
- Work/release date
- Edition information
- Destination path
- Original and final filenames
- File hashes
- Cover source
- Acquisition date
- Mnemosyne version

A per-item hidden provenance file is also a possible feature:

```text
.mnemosyne.json
```

This could allow the main database to be rebuilt from the library itself.

## Duplicate detection

Potential signals:

- Provider item ID
- Source URL
- Canonical metadata
- Destination path
- File hashes
- Audio fingerprints where appropriate

Mnemosyne should distinguish between:

- Exact duplicates
- Alternate editions
- Alternate releases
- Higher-quality sources
- Partial acquisitions

## Interactive mode

A future interactive terminal might provide:

```text
Mnemosyne

[1] Fetch one item
[2] Process audiobook queue
[3] Process ebook queue
[4] Process music queue
[5] Process all queues
[6] Review failed jobs
[7] Verify library
[8] Configuration
[9] Exit
```

Interactive mode must use the same underlying engine as the CLI. No core capability should exist only in the menu.

## Rich manifests

Simple `.txt` queues remain the primary batch mechanism.

A richer optional manifest may eventually be added:

```yaml
items:
  - type: audiobook
    url: https://archive.org/details/example1

  - type: ebook
    url: https://archive.org/details/example2

  - type: music
    url: https://archive.org/details/example3
```

This may later support explicit metadata/format overrides without replacing the simple queue files.

## Potential quality preferences

Possible later options:

```text
--prefer-lossless
--prefer-m4b
--prefer-epub
```

Examples:

- Music: prefer original/lossless source files and preserve sample rate/bit depth.
- Audiobooks: prefer good chaptered M4B when appropriate; preserve narrator/chapter metadata.
- eBooks: prefer EPUB when useful while preserving PDF where scan/layout matters.

## Diagnostics

Possible future command:

```text
mnemosyne doctor
```

It may inspect:

- Configuration
- Folder permissions
- External dependencies
- Provider availability
- ffmpeg/metadata tools
- Network access
- Cache/staging health

## First implementation target

The first real implementation should remain deliberately small:

- Accept one Archive.org audiobook URL.
- Discover and identify the item.
- Show a plan.
- Fetch selected audio.
- Create the canonical destination.
- Rename files.
- Retrieve `cover.jpg`.
- Apply metadata where practical.
- Verify the final result.

The first implementation should be structured so that a list of jobs can later be fed into the same acquisition engine without rewriting it.

## Development principle

Every real acquisition should be allowed to teach Mnemosyne something.

Real ebooks, audiobooks, and music will expose:

- Provider quirks
- Metadata ambiguity
- Naming edge cases
- Cover problems
- Format conflicts
- Duplicate scenarios
- Verification needs

The design should remain strong enough to guide development while flexible enough to improve when better ideas emerge.
