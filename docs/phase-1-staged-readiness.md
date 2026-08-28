# Phase 1 — Final Staged Readiness Verification

Before Mnemosyne is allowed to place anything into the final media library, the complete staging job must pass an independent read-only readiness gate.

## Command

```powershell
mnemosyne ready
```

Or target a specific job:

```powershell
mnemosyne ready "$HOME\Mnemosyne\staging\<job-id>"
```

The command does not move or modify media. It writes only:

```text
readiness-report.json
```

inside the staging job.

## Checks

Current readiness requires all of the following:

- no unresolved warnings
- source quality decision formally resolved
- canonical staged audio SHA-256 matches provenance
- metadata normalization is verified
- standalone cover SHA-256 matches provenance
- staged MP4-family container reopens successfully
- canonical title is correct
- canonical artist/author is correct
- canonical album artist is correct
- canonical album is correct
- canonical date is correct
- canonical genre is correct
- embedded artwork SHA-256 matches the verified standalone cover
- actual codec and lossy/lossless class are known
- planned final destination exists in provenance
- final library is still untouched

Only when every check passes does the readiness report state:

```text
ready-for-placement
```

## Safety boundary

`ready` is certification, not placement.

Even a successful readiness run does not create directories or copy media into the final library. Final placement will be a separate explicit transactional command.
