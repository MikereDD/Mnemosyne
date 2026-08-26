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
- [x] Container/parser fallback codec detection
- [x] Provenance reduced to weak quality-ranking tie-breaker
- [x] Repeatable non-overwriting comparison runs
- [x] Transactional staged source adoption
- [x] Rollback backup + adoption history
- [x] Post-adoption hash/codec re-verification
- [ ] External metadata enrichment
- [ ] Resume/retry
- [ ] Metadata normalization/tagging
- [ ] Edition-aware cover validation/scoring
- [ ] Final destination placement
- [ ] Final verification
- [ ] Job ledger/state integration
