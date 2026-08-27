# Changelog

## Unreleased

### Added

- Audio-edition model for complete single-file and ordered multi-file audiobook representations.
- Ordered chapter-set grouping for Archive.org / LibriVox naming patterns.
- `--audio-format` preference for selecting a complete edition by extension rather than an individual file.
- Multi-file Safe Fetch staging under a dedicated `audio\` directory.
- Per-chapter expected-size, signature, SHA-256, codec, and quality verification.
- Fetch-report schema v9 multi-file audio provenance.

### Changed

- Planner ranks audiobook editions rather than treating every chapter as an independent competing audiobook.
- Original VBR MP3 chapter sets remain distinct from lower-bitrate derivative MP3 sets.
- Failed multi-file fetches remove the incomplete staging job instead of leaving a partial edition.
