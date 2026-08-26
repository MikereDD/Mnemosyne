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
- Parser/container fallback codec recognition and hardened actual-quality ranking.
- Repeatable comparison evidence runs.
- Transactional staged source adoption with SHA-256 binding to comparison evidence.
- Rollback media/report backups and source-adoption history.
- Post-adoption codec and SHA-256 re-verification.

### Changed

- Provider quality discrepancies can now be resolved by adopting the latest verified actual-comparison winner.
- Source adoption remains confined to staging; final-library mutation is still disabled.
