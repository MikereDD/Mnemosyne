# Roadmap

Mnemosyne is currently in early development.

## Phase 0 — Foundation

- [x] Name the project
- [x] Create project avatar
- [x] Define media types
- [x] Define canonical library layouts
- [x] Define core workflow
- [x] Choose implementation language — Python 3.12+
- [x] Choose CLI/UI approach — Typer + Rich, CLI-first with shared interactive layer
- [x] Define configuration format — TOML
- [x] Define provider interface — pluggable provider adapters
- [x] Define metadata model — Pydantic v2 common core + media-specific models

Supporting stack:

- [x] `uv` + `pyproject.toml`
- [x] Live user config outside the repo at `$HOME\Mnemosyne\config\config.toml`

## Phase 1 — First provider

Initial target: Archive.org.

- [x] First real test fixture selected (`animal-farm.sna`)
- [x] Initial Archive.org URL/identifier discovery
- [x] Initial metadata retrieval
- [x] Initial playable-vs-auxiliary file classification
- [x] Initial audio quality/source ranking
- [x] Initial plan-only destination naming
- [x] Initial cover candidate selection
- [x] Safe Fetch v1 — isolated staging download
- [x] Safe Fetch v1 — expected-size verification
- [x] Safe Fetch v1 — basic audio signature verification
- [x] Safe Fetch v1 — SHA-256 recording
- [x] Safe Fetch v1 — staging provenance report
- [x] Canonical staged audio naming
- [x] Cover download into staging
- [x] Cover signature/dimension verification
- [x] Staging provenance report schema v2
- [ ] External metadata enrichment
- [ ] Resume/retry
- [ ] Metadata inspection
- [ ] Metadata normalization/tagging
- [ ] Edition-aware cover validation/scoring
- [ ] Final destination placement
- [ ] Final verification
- [ ] Job ledger/state integration

## Phase 2 — Media normalization

### eBooks

- [ ] EPUB/PDF naming
- [ ] Metadata normalization
- [ ] Cover handling

### Audiobooks

- [ ] M4B handling
- [ ] MP3 chapter handling
- [ ] Chapter numbering
- [ ] Metadata normalization
- [ ] Cover embedding

### Music

- [ ] Album/track naming
- [ ] Track numbering
- [ ] Metadata normalization
- [ ] Cover embedding

## Phase 3 — Library intelligence

- [ ] Existing-item detection
- [ ] Duplicate detection
- [ ] Conflict resolution
- [ ] Audit/report mode
- [ ] Batch acquisition
- [ ] Provider expansion
