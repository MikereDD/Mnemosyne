# Phase 2 — Multi-file MP3 Metadata + Readiness

The real 18-file LibriVox MP3 edition is treated as one metadata transaction.

`mnemosyne tag` detects a multi-file fetch report and dispatches to the whole-edition path. The existing single-file tag implementation is untouched.

Every MP3 chapter gets canonical book metadata, the verified cover, and ID3 track numbering as `N/total`. Every working copy must pass before any canonical staged file is replaced. A complete pre-tag edition and fetch report are retained under one rollback directory; if later replacement or verification fails, Mnemosyne restores the whole edition.

`mnemosyne ready` verifies all chapter hashes, canonical ID3 metadata, track order, embedded artwork, actual codec/quality, standalone cover hash, and an ordered whole-edition SHA-256.

Final placement remains unchanged in this slice.
