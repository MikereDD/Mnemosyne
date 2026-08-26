# Roadmap

Mnemosyne is currently in early development.

## Phase 0 — Foundation

- [x] Project identity, media types, canonical library layouts, and core workflow
- [x] Python 3.12+, Typer + Rich, TOML, Pydantic v2, Mutagen, `uv`
- [x] Pluggable provider architecture and external live config

## Phase 1 — First provider

Initial target: Archive.org.

- [x] Discovery and metadata retrieval
- [x] Playable-vs-auxiliary classification
- [x] Provider-side candidate ranking
- [x] Plan-only destination naming
- [x] Safe staged fetching
- [x] Size/signature/SHA-256 verification
- [x] Canonical staged naming
- [x] Cover download and structural validation
- [x] Read-only metadata inspection
- [x] Proposed canonical metadata diff
- [x] Post-download actual codec/quality inspection
- [x] Provider quality-claim mismatch detection
- [x] Explicit actual candidate comparison
- [x] Hardened container/parser codec detection
- [x] Repeatable comparison evidence
- [x] Transactional staged source adoption
- [x] Rollback backup + adoption history
- [x] Transactional MP4-family metadata normalization
- [x] Embedded cover verification by SHA-256
- [x] Post-tag canonical metadata verification
- [ ] External metadata enrichment
- [ ] Resume/retry
- [ ] MP3/FLAC metadata writers
- [ ] Edition-aware cover validation/scoring
- [x] Final staged readiness verification
- [x] Transactional final destination placement
- [x] Final library hash verification
- [ ] Existing-destination conflict resolution
- [x] Final acquisition completion certification
- [x] Non-destructive staging retention policy v1
- [x] Durable completed-job receipt archive
- [x] Explicit strongly-confirmed staging cleanup workflow
- [x] Explicit fetch-list pruning workflow
- [x] Atomic fetch-list backup/rewrite verification
- [ ] Job ledger/state integration
