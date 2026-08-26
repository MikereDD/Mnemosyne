# Changelog

All notable changes to Mnemosyne will be documented in this file.

## Unreleased

### Added

- Initial repository foundation and project identity.
- Canonical library layouts and design foundation.
- Python 3.12+, Typer, Rich, TOML, Pydantic v2, Mutagen, and `uv` stack.
- Initial Archive.org provider and safe plan/staging workflow.
- Playable-audio classification and provider-side ranking.
- Size/signature/SHA-256 verification and canonical staged naming.
- Cover retrieval/validation.
- Read-only metadata inspection and proposed metadata diff.
- Actual codec/quality cross-check and provider mismatch detection.
- Explicit actual candidate comparison.
- Parser/container fallback codec recognition for formats such as MP3.
- Repeatable comparison runs preserved under unique run directories.

### Changed

- Archive original/derivative provenance is now only a weak tie-breaker during actual quality comparison.
- A materially better derivative can outrank a poor original.
