# Roadmap

This roadmap distinguishes **implemented**, **active**, and **planned** work.
Roadmap entries describe direction, not promises of current functionality.

## Foundation

Mnemosyne's long-term identity is:

> A safety-first media acquisition, organization, normalization, and
> verification system.

Two first-class workflows share one core architecture:

```text
Acquisition
Discover → Identify → Preview/Plan → Fetch → Normalize → Verify → Place → Complete

Existing library
Scan → Identify → Classify → Preview/Plan → Normalize → Rename/Move → Verify
```

Canonical target layouts:

```text
Music        → Genre / Artist / Album
eBooks       → Author / Series? / Book
Audiobooks   → Author / Series? / Book
Movies       → Title (Year)
Documentary  → Title (Year)
TV           → Series (Start Year - End Year/Continuing)
               └── Season NN (Premiere Year)
```

## Phase 1 — Proven audiobook lifecycle

- [x] Internet Archive provider
- [x] Read-only acquisition planning
- [x] Provider metadata and candidate discovery
- [x] Playable-audio filtering
- [x] Single-file audiobook acquisition
- [x] Multi-file audiobook edition grouping
- [x] Transactional multi-file staged fetching
- [x] Actual codec / quality inspection
- [x] Candidate comparison
- [x] Transactional source adoption
- [x] MP4 / M4A / M4B metadata
- [x] MP3 / ID3 metadata
- [x] FLAC metadata
- [x] Cover retrieval / embedding
- [x] Transactional multi-file metadata normalization
- [x] Multi-file staged readiness verification
- [x] Transactional multi-file final placement
- [x] Multi-file completion certification
- [x] Durable completion receipts
- [x] Explicit staging cleanup
- [x] Fetch-list pruning with confirmation
- [x] Prove full lifecycle with an MP3 audiobook
- [x] Prove full lifecycle with a 24-bit / 96 kHz FLAC audiobook

## Phase 2 — Batch acquisition and recovery

- [x] Read-only fetch-list parsing and duplicate classification
- [ ] Safe batch acquisition-plan resolution
- [ ] Verified metadata provenance gates for canonical placement
- [ ] Queue-local or explicit metadata overrides
- [ ] Sequential batch execution through the proven single-item engine
- [ ] Per-item COMPLETE / FAILED / NEEDS ATTENTION / SKIPPED reporting
- [ ] Resume / retry across interrupted jobs
- [ ] Durable batch state / recovery
- [ ] Individual retry without blindly rerunning completed work
- [ ] Detailed CLI help and recovery guidance
- [ ] CI and fault-injection coverage

## Phase 3 — Generalize acquisition

- [ ] eBook acquisition pipeline
- [ ] Music acquisition pipeline
- [ ] Video acquisition pipeline
- [ ] Additional providers
- [ ] Provider-independent source preference profiles
- [ ] Richer work / edition / release modeling
- [ ] Expanded duplicate and alternate-edition detection

## Phase 4 — Existing-library organizer

Build a read-only scanner first. Mutation follows only after plans are reliable.

- [ ] Scan existing media libraries without mutation
- [ ] Embedded metadata extraction
- [ ] Existing-folder context analysis
- [ ] Filename parsing
- [ ] Confidence / provenance model
- [ ] External metadata lookup only for unresolved identities
- [ ] Deterministic organization preview
- [ ] Conflict / duplicate reporting
- [ ] Transactional or rollback-capable rename/move operations
- [ ] Post-move verification
- [ ] Incremental approval by artist, author, album, work, series, or show

### Music organization

- [ ] Canonical base-genre map
- [ ] Preserve detailed sub-genres in embedded metadata
- [ ] Detect overly specific physical genre folders
- [ ] Detect artists placed beneath inconsistent base genres
- [ ] Normalize Artist / Album / Track structure
- [ ] Preserve source audio quality
- [ ] Verify complete albums after reorganization

Canonical rule:

```text
Celtic Punk tag → Punk filesystem genre
```

The tag remains detailed; only physical placement is generalized.

### eBook and audiobook organization

- [ ] Author-first physical hierarchy
- [ ] Optional Series layer
- [ ] Series ordering
- [ ] Genre as metadata only
- [ ] Work / edition distinction
- [ ] Cross-format eBook / audiobook matching
- [ ] Report eBook-present / audiobook-missing and inverse cases
- [ ] Future ebook-to-audiobook workflow integration

### Movies and documentaries

- [ ] `Title (Year)` canonical folders
- [ ] Genre / subject as metadata only
- [ ] Artwork and metadata normalization
- [ ] Extra / sample detection
- [ ] Duplicate / alternate-release awareness

### TV

- [ ] `Series (Start Year - End Year/Continuing)` canonical folders
- [ ] `Season NN (Premiere Year)` canonical season folders
- [ ] Episode naming / numbering normalization
- [ ] Positively verified continuing/completed status
- [ ] Safe active-series rename when an end year becomes known
- [ ] Irregular season / special handling
- [ ] Episode completeness verification

Safety invariant:

> A missing end year does not mean `Continuing`.

## Phase 5 — Library intelligence

- [ ] Persistent normalized library index
- [ ] Cross-format work relationships
- [ ] Missing-format reports
- [ ] Exact duplicate vs alternate edition/release classification
- [ ] Artwork quality audit
- [ ] Metadata consistency audit
- [ ] Library verification / health command
- [ ] Repair-plan generation
- [ ] Rebuildable provenance from durable state

## Phase 6 — Productization

- [ ] Comprehensive command/topic help
- [ ] Stable configuration migration
- [ ] Packaging
- [ ] Release tooling
- [ ] Signed release / updater integration where appropriate
- [ ] Stable-version compatibility guarantees

## Governing rules

- Preview before mutation.
- Never silently overwrite valuable media.
- Preserve source quality whenever practical.
- Treat provider/external metadata as evidence, not unquestionable truth.
- Never silently guess when confidence is low.
- Organize by stable structure; keep descriptive ambiguity in metadata.
- Verify the final result before cleanup.
- Keep enough provenance to explain what changed and why.
- Grow features from real media and real failure cases.

> **A convenient failure is better than an unverified success.**
