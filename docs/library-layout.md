# Library Layout

Mnemosyne uses deterministic, human-readable paths.

## eBooks

```text
Author/
└── eBook/
    └── Title - Author (Date)/
```

Preferred contained filenames:

```text
Title - Author (Date).epub
Title - Author (Date).pdf
cover.jpg
```

## Audiobooks

```text
Author/
└── Audiobook/
    └── Title - Author (Date)/
```

Single-file example:

```text
Title - Author (Date).m4b
cover.jpg
```

Multi-file example:

```text
01 - Chapter Title.mp3
02 - Chapter Title.mp3
cover.jpg
```

## Music

```text
Band/
└── Band Name - Album (Date)/
```

Preferred contained filenames:

```text
01 - Track Title.flac
02 - Track Title.flac
cover.jpg
```

## Naming goals

Naming should be:

- Predictable
- Searchable
- Stable
- Human-readable
- Friendly to scripts and library scanners

## Cover convention

The canonical standalone cover filename is:

```text
cover.jpg
```

Mnemosyne should prefer the best suitable edition/album artwork it can identify and should embed artwork into supported media formats where practical.

## Date convention

The parenthetical date represents the publication/release year associated with the selected edition or release.

When the correct date is ambiguous, Mnemosyne should prefer explicit source metadata and expose ambiguity during planning rather than guessing silently.
