# Architecture

## Status

Conceptual architecture only. No implementation stack has been selected.

## High-level model

Mnemosyne should separate media acquisition into distinct stages so that source discovery, planning, fetching, normalization, and verification can evolve independently.

```text
Provider
   ↓
Discovery
   ↓
Identification
   ↓
Preview / Plan
   ↓
Fetch
   ↓
Normalization
   ├── Rename
   ├── Cover retrieval
   └── Metadata / artwork embedding
   ↓
Verification
   ↓
Library
```

## Planned architectural boundaries

### Providers

A provider is responsible for locating and describing available media from a source.

Archive.org is expected to be an early provider, but Mnemosyne should not be designed around Archive.org-specific assumptions.

Potential provider responsibilities:

- Search/discovery
- Item metadata
- Available formats
- Source URLs
- Source checksums when available
- Cover candidates
- Rights/access indicators
- Resume/retry information

### Planner

The planner should convert source metadata into a deterministic proposed library result before files are changed.

A plan may include:

- Destination folder
- Destination filenames
- Selected source format
- Cover source
- Metadata changes
- Existing-file conflicts
- Duplicate warnings
- Verification steps

### Fetcher

The fetch layer should support:

- Resumable downloads where possible
- Retry handling
- Temporary/staging files
- Source-size validation
- Checksum validation when available
- Atomic or safe final placement

### Normalizers

Media-specific normalizers should handle:

- eBook naming and metadata
- Audiobook chapter/file naming and tags
- Music track naming and tags
- Cover normalization and embedding

### Verifier

Verification should confirm that the expected files exist and match the plan.

Future verification may include:

- File existence
- Size/checksum checks
- Decode/readability checks
- Metadata inspection
- Cover presence
- Track/chapter completeness

## Safety philosophy

Mnemosyne should favor predictable and reversible behavior:

1. Discover first.
2. Identify the source precisely.
3. Show the planned result.
4. Fetch into staging.
5. Normalize safely.
6. Verify before declaring success.
7. Never silently overwrite valuable existing media.
