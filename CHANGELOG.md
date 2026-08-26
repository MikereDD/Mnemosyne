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
- Explicit actual candidate comparison with hardened actual-quality ranking.
- Repeatable comparison evidence runs.
- Transactional staged source adoption with rollback and post-adoption verification.
- Transactional MP4-family metadata normalization.
- Canonical audiobook MP4 tags for title, author/artist, album artist, album, date, and genre.
- Verified JPEG/PNG cover embedding.
- Pre/post metadata SHA-256 provenance and rollback history.
- Double post-write verification: working copy before adoption and canonical staged file after replacement.

- Final staged readiness certification with independent hash, metadata, artwork, codec, warning, destination, and final-library checks.
- Machine-readable `readiness-report.json` with `ready-for-placement` / `not-ready` status.

- Transactional final-library placement gated by a successful readiness report.
- Hidden sibling destination transaction directory with pre-commit audio/cover hash verification.
- Atomic final directory commit with post-placement audio/cover hash verification.
- Strict refusal to overwrite or merge an existing canonical destination.
- Staged `placement-report.json` and fetch-report schema v6 final-placement provenance.

- Final acquisition completion certification after verified placement.
- Re-verification of final audio and cover hashes before lifecycle completion.
- Non-destructive completion retention policy: staging and provenance stay retained by default.
- `completion-report.json` and fetch-report schema v7 completion provenance.
- Completion explicitly does not prune fetch-list entries.

- Durable completed-job receipts under `$HOME\Mnemosyne\state\completed\` before staging deletion.
- Strongly confirmed `mnemosyne cleanup` workflow requiring `--apply` plus exact job-ID confirmation.
- Final audio and cover hashes are reverified immediately before staging cleanup.
- Cleanup records staging size/file count and never prunes fetch-list entries.

- Explicit completed-job fetch-list pruning by exact source URL.
- Byte-verified fetch-list backups before mutation.
- Atomic fetch-list rewrite with post-commit exact-match verification and rollback.
- Duplicate exact active entries are removed together and counted in provenance.
- Durable completion receipts record fetch-list pruning state and backup paths.

### Changed

- Metadata mutation is blocked while staging warnings remain unresolved.
- Metadata writes operate on a temporary staged copy and only replace canonical staged audio after verification.
- Final-library mutation remains disabled.
