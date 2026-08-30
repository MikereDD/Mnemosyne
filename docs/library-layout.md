# Library Layout

Mnemosyne uses deterministic, human-readable paths and distinguishes
**structural relationships** from **descriptive metadata**.

> **Canonical rule:** organize according to stable structural relationships;
> descriptive metadata should not create unstable filesystem hierarchies.

These layouts describe the agreed target model. Some media families remain
roadmap work and are not yet implemented.

## Canonical overview

```text
Music        → Genre / Artist / Album
eBooks       → Author / Series? / Book
Audiobooks   → Author / Series? / Book
Movies       → Title (Year)
Documentary  → Title (Year)
TV           → Series (Start Year - End Year/Continuing)
               └── Season NN (Premiere Year)
```

## Music

Music keeps a genre layer because it is useful for physical browsing, but the
filesystem uses only a **canonical base genre**.

```text
Music/
└── Punk/
    └── Artist Name/
        └── Album Name/
            ├── 01 - Track Title.flac
            ├── 02 - Track Title.flac
            └── cover.jpg
```

Detailed sub-genres remain metadata rather than creating additional folders.

Example:

```text
Embedded genre : Celtic Punk
Filesystem     : Music/Punk/Artist Name/Album Name/
```

A future canonical genre map may normalize values such as:

```text
Celtic Punk  → Punk
Folk Punk    → Punk
Street Punk  → Punk
Death Metal  → Metal
Black Metal  → Metal
Synthwave    → Electronic
```

The mapping is about **placement**, not erasing descriptive metadata.

## eBooks

Genre is metadata only. It does not participate in the physical hierarchy.

Standalone work:

```text
eBooks/
└── Author Name/
    └── Book Title/
        ├── Book Title.epub
        └── cover.jpg
```

Series work:

```text
eBooks/
└── Author Name/
    └── Series Name/
        ├── 01 - First Book/
        │   ├── First Book.epub
        │   └── cover.jpg
        └── 02 - Second Book/
            ├── Second Book.epub
            └── cover.jpg
```

An author may write across several genres without being physically fragmented
across the library.

## Audiobooks

Audiobooks follow the same work/series structure as eBooks.

Standalone single-file work:

```text
Audiobooks/
└── Author Name/
    └── Book Title/
        ├── Book Title.m4b
        └── cover.jpg
```

Standalone multi-file work:

```text
Audiobooks/
└── Author Name/
    └── Book Title/
        ├── 01 - Chapter Title.mp3
        ├── 02 - Chapter Title.mp3
        └── cover.jpg
```

Series work:

```text
Audiobooks/
└── Author Name/
    └── Series Name/
        ├── 01 - First Book/
        └── 02 - Second Book/
```

Edition-specific metadata such as narrator, publisher, abridged/unabridged
status, and release date may be preserved in metadata and provenance even when
it is not required in the physical path.

## Movies

```text
Movies/
└── Movie Title (Year)/
    ├── Movie Title (Year).mkv
    └── cover.jpg
```

Genre remains metadata only.

## Documentary

```text
Documentary/
└── Documentary Title (Year)/
    ├── Documentary Title (Year).mkv
    └── cover.jpg
```

Genre and subject classifications remain metadata only.

## TV

TV uses series lifetime and the season premiere year as structural information.

Active series:

```text
TV/
└── Series Title (2024 - Continuing)/
    ├── Season 01 (2024)/
    │   ├── Series Title - S01E01 - Episode Title.mkv
    │   └── Series Title - S01E02 - Episode Title.mkv
    └── Season 02 (2025)/
```

Completed multi-year series:

```text
TV/
└── Series Title (2008 - 2013)/
    └── Season 01 (2008)/
```

Completed one-year series:

```text
TV/
└── Series Title (2019)/
    └── Season 01 (2019)/
```

`Continuing` must be based on positively identified series status. A missing end
year alone is not sufficient evidence that a series is still active. If status
cannot be established confidently, Mnemosyne should surface the ambiguity
during planning rather than invent lifecycle information.

When an active series later ends, Mnemosyne may propose a safe rename:

```text
Series Title (2024 - Continuing)
→ Series Title (2024 - 2027)
```

That change should not require reorganizing correctly structured season
directories underneath it.

## Identification precedence for existing libraries

When normalizing media already owned, Mnemosyne should prefer evidence in this
order:

1. Embedded metadata.
2. Existing folder structure.
3. Filename parsing.
4. External metadata lookup when necessary.

Low-confidence identification must be surfaced for review rather than silently
guessed.

## Naming goals

Naming should be:

- Predictable
- Searchable
- Stable
- Human-readable
- Friendly to scripts and library scanners
- Unicode-safe
- Cross-platform safe

## Cover convention

The preferred canonical standalone cover filename is:

```text
cover.jpg
```

Artwork should also be embedded into supported media formats where practical
and safe.

## Date convention

Dates in physical paths represent stable work/release/lifecycle information
defined by the media family, not merely whatever date a provider happens to
return.

Provider dates are discovery evidence, not automatically canonical dates.
Ambiguous dates should remain visible during planning and require verified
resolution when they affect canonical placement.
