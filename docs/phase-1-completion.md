# Phase 1 — Final Acquisition Completion Certification

Placement is not automatically equivalent to lifecycle completion.

Mnemosyne performs one final certification pass after placement and before a job is marked `complete`.

## Preview

```powershell
mnemosyne complete
```

Preview is read-only.

## Apply

```powershell
mnemosyne complete --apply
```

## Completion checks

The completion gate verifies:

- fetch report records `placed-and-verified`
- readiness provenance is present
- placement provenance is present
- canonical final destination exists
- final audio exists
- final cover exists
- final audio SHA-256 still matches placement provenance
- final cover SHA-256 still matches placement provenance
- staging/provenance still exists
- job is not already completion-certified

## Retention policy v1

Completion is deliberately non-destructive.

A successful completion does **not**:

- delete the staging job
- delete rollback evidence
- delete comparison evidence
- remove provenance reports
- prune fetch-list URLs
- alter final-library media

Instead, it writes:

```text
completion-report.json
```

and updates `fetch-report.json` to:

```text
status = complete
```

with retention metadata explicitly recording:

```text
stagingRetained          = true
automaticCleanupPerformed = false
fetchListPruned          = false
```

Cleanup and fetch-list pruning will be separate explicit operations with their own preview/confirmation rules.
