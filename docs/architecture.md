# Architecture

## Status

Mnemosyne is under active development. The current acquisition pipeline is
implemented for real audiobook workflows; the broader library-organization
architecture in this document includes agreed roadmap direction that is not yet
fully implemented.

## Architectural identity

Mnemosyne is not only a downloader. It is a media acquisition, organization,
normalization, and verification system.

Two first-class workflows share the same core services.

### Acquisition

```text
Provider
   ↓
Discovery
   ↓
Identification
   ↓
Preview / Plan
   ↓
Fetch to staging
   ↓
Normalization
   ↓
Verification
   ↓
Placement
   ↓
Completion
```

### Existing-library normalization

```text
Library scan
   ↓
Identification
   ↓
Classification
   ↓
Preview / Plan
   ↓
Metadata normalization
   ↓
Rename / Move
   ↓
Verification
```

The workflows should converge rather than becoming separate applications.

## Shared core

Both acquisition and organization should use shared concepts for:

- Media identity
- Work / edition / release relationships
- Metadata provenance
- Confidence and ambiguity
- Canonical destination planning
- Conflict detection
- Safe mutation
- Rollback / recovery
- Verification
- Durable provenance

A provider URL, an existing folder, a filename, and embedded tags are different
sources of evidence about the same underlying media identity.

## Evidence precedence

For existing libraries, the preferred identification order is:

1. Embedded metadata.
2. Existing folder structure.
3. Filename parsing.
4. External metadata lookup when ambiguity remains.

Provider and external metadata are evidence, not unquestionable truth.
Mnemosyne should retain provenance for important decisions and must not silently
promote low-confidence values into canonical placement.

## Stable structure vs descriptive metadata

A central architectural rule is:

> **Organize according to stable structural relationships; descriptive metadata
> should not create unstable filesystem hierarchies.**

Examples:

- Music uses a canonical **base genre** as a physical folder layer.
- Music sub-genres remain embedded metadata.
- eBooks and audiobooks do not use genre in their physical hierarchy.
- Movies, documentaries, and TV do not use genre in their physical hierarchy.
- Book series and TV season relationships are structural and therefore belong
  in the filesystem.
- TV lifecycle years/status are structural only when positively identified.

## Planned architectural boundaries

### Providers

Providers locate and describe media available from external sources.

Provider responsibilities may include:

- Search / discovery
- Item metadata
- Available formats
- Source URLs
- Source checksums when available
- Cover candidates
- Rights / access indicators
- Resume / retry information

Provider-specific assumptions should stay behind provider adapters.

### Scanners

Library scanners inspect media already owned without mutating it.

Scanner responsibilities may include:

- Discovering candidate media files
- Reading embedded metadata
- Recording existing folder context
- Detecting media type
- Grouping files that appear to belong to one work, album, season, or edition
- Reporting unidentified or ambiguous material

### Identifier

The identifier reconciles available evidence into a normalized media identity.

It should understand concepts such as:

- Artist / album / track
- Author / series / book / edition
- Movie / release
- TV series / season / episode
- Documentary / release
- Cross-format relationships such as eBook and audiobook versions of one work

Confidence and provenance should remain visible.

### Classifier

Classification determines the stable structural category required for physical
placement.

Examples include:

- Canonical base music genre
- Book series membership and order
- TV series lifecycle years
- TV season number and season premiere year

Classification must not erase richer descriptive metadata.

### Planner

The planner converts normalized identity into a deterministic proposed library
result before files are changed.

A plan may include:

- Destination folder
- Destination filenames
- Selected source format
- Canonical base genre
- Series / season structure
- Cover source
- Metadata changes
- Existing-file conflicts
- Duplicate warnings
- Confidence warnings
- Verification steps

### Fetcher

The fetch layer supports:

- Resumable downloads where possible
- Retry handling
- Temporary / staging files
- Source-size validation
- Checksum validation when available
- Safe transfer into staged jobs

### Normalizers

Media-specific normalizers handle:

- eBook naming and metadata
- Audiobook chapter/file naming and tags
- Music track naming, base-genre placement, and detailed genre tags
- Movie / documentary naming and metadata
- TV series / season / episode naming and metadata
- Cover normalization and embedding

### Organizer / mover

Existing media should never be moved merely because it was discovered.

The organizer executes an approved plan and should support:

- Preview before mutation
- Conflict detection
- Transactional or rollback-capable moves where practical
- No silent overwrite
- Verification before source cleanup
- Whole-work / whole-album / whole-season semantics where appropriate

### Verifier

Verification confirms that the result actually matches the approved plan.

Verification may include:

- File existence
- Size / checksum checks
- Decode / readability checks
- Metadata inspection
- Cover presence
- Track / chapter / episode completeness
- Destination path correctness
- Source cleanup safety

## Safety philosophy

Mnemosyne favors predictable, reversible, evidence-based behavior.

1. Discover or scan first.
2. Identify the media precisely.
3. Surface ambiguity rather than guessing.
4. Show the planned result.
5. Mutate only after an explicit safe path exists.
6. Normalize without unnecessary quality loss.
7. Verify the resulting library.
8. Never silently overwrite valuable existing media.
9. Keep provenance sufficient to explain what Mnemosyne changed and why.

> **A convenient failure is better than an unverified success.**
