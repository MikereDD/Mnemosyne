<p align="center">
  <img src="assets/mnemosyne-avatar.png" alt="Mnemosyne" width="320">
</p>

<h1 align="center">Mnemosyne</h1>

<p align="center">
  Unified media acquisition, organization, cover retrieval, metadata normalization, and verification for ebooks, audiobooks, and music.
</p>

> **Status:** Concept / repository foundation.  
> Mnemosyne does not have an implementation yet. This repository currently defines the project identity, library conventions, workflow, and initial roadmap.

## Vision

Mnemosyne is intended to become a provider-based media acquisition and library-normalization tool for personal collections.

The goal is not merely to download files. Mnemosyne should help turn source media into a clean, predictable, searchable library with consistent naming, structure, cover art, and metadata.

## Supported media types

Planned first-class media types:

- eBooks
- Audiobooks
- Music

The architecture should remain open to additional providers and media sources without coupling the project to a single service.

## Canonical library layouts

### eBooks

```text
Author/
└── eBook/
    └── Title - Author (Date)/
        ├── Title - Author (Date).epub
        ├── Title - Author (Date).pdf
        └── cover.jpg
```

### Audiobooks

```text
Author/
└── Audiobook/
    └── Title - Author (Date)/
        ├── Title - Author (Date).m4b
        ├── or chapter files
        └── cover.jpg
```

Multi-file audiobook example:

```text
Author/
└── Audiobook/
    └── Title - Author (Date)/
        ├── 01 - Chapter Title.mp3
        ├── 02 - Chapter Title.mp3
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

## Core workflow

Mnemosyne should follow this workflow:

**Discover → Identify → Preview/Plan → Fetch → Rename → Fetch Cover → Tag/Embed → Verify**

The user should be able to understand what Mnemosyne intends to do before destructive or library-mutating actions occur.

## Initial design principles

- Provider-based architecture rather than hard-coding one source.
- Consistent and deterministic library paths.
- Clean, human-readable filenames.
- Cover art fetched for ebooks, audiobooks, and music.
- Embedded artwork and metadata when practical and appropriate.
- Duplicate and existing-item detection.
- Safe resume and retry behavior.
- Verification after acquisition and normalization.
- No silent destructive overwrite.
- Preserve original media quality whenever possible.
- Separate discovery/planning from mutation.

## Repository status

This repository intentionally starts small. Implementation language, CLI framework, provider APIs, metadata backend, and persistent database format have not been selected yet.

See:

- [`docs/architecture.md`](docs/architecture.md)
- [`docs/library-layout.md`](docs/library-layout.md)
- [`docs/roadmap.md`](docs/roadmap.md)

## License

License selection is still pending. See [`LICENSE.md`](LICENSE.md).
