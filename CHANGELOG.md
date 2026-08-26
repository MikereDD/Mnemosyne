# Changelog

All notable changes to Mnemosyne will be documented in this file.

## Unreleased

### Added

- Initial repository foundation.
- Project identity and avatar.
- Canonical library layouts for ebooks, audiobooks, and music.
- Initial architecture notes.
- Initial roadmap.
- Base `.gitignore`.
- Living design-foundation document covering agreed runtime paths, fetch queues, safety rules, job state, configuration, staging, verification, and future architecture.
- Phase 0 implementation stack: Python 3.12+, Typer, Rich, TOML, Pydantic v2, and `uv`.
- Initial Archive.org provider adapter.
- Archive item URL/identifier parsing and metadata retrieval.
- Playable-audio classification that excludes Archive helper files such as `.afpk`.
- Initial audio candidate quality/source ranking.
- Canonical destination planning and cover candidate selection.
- Safe runtime initialization under `$HOME\Mnemosyne`.
- Plan-only CLI milestone with no media downloads or final-library mutation.
- Initial provider/path unit tests.
- Safe Fetch v1 with explicit `--apply`, isolated staging, `.part` downloads, expected-size verification, basic file-signature validation, SHA-256 hashing, and per-job provenance reports.
- Safe Fetch validation tests for M4A/ISO-BMFF, MP3, invalid signatures, and HTML masquerading as audio.
- Canonical staged audio renaming while preserving the provider filename in provenance.
- Staged cover retrieval with JPEG/PNG/WebP signature validation, dimensions where available, and SHA-256 recording.
- Fetch-report schema v2 with canonical staged filenames and cover provenance.

### Changed

- Roadmap now records staging normalization and cover validation as completed Phase 1 slices.
