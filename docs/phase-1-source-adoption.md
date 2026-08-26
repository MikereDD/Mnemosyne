# Phase 1 — Transactional Staged Source Adoption

After actual candidate comparison, Mnemosyne can explicitly adopt the latest verified winner into the staged canonical source slot.

## Preview

```powershell
mnemosyne adopt
```

No source file changes.

## Apply

```powershell
mnemosyne adopt --apply
```

Or target a specific staging job:

```powershell
mnemosyne adopt "$HOME\Mnemosyne\staging\<job-id>" --apply
```

## Transactional workflow

1. Read `fetch-report.json`.
2. Locate the newest completed `comparison-report.json`.
3. Confirm the recommended source exactly matches a recorded candidate.
4. Re-hash the comparison source and require it to match the recorded SHA-256.
5. Copy the recommended winner to a temporary file inside the staging job.
6. Verify the temporary copy hash.
7. Move the current staged canonical source into `rollback\`.
8. Atomically place the verified winner in the canonical staged slot.
9. Re-hash and re-inspect the adopted media.
10. Atomically update `fetch-report.json`.
11. Preserve adoption history and rollback information.

If a failure occurs after the original staged source has been backed up, Mnemosyne attempts to restore it.

## Rollback evidence

The staging job now preserves:

```text
rollback\
├── <timestamp>-<previous canonical audio>
└── <timestamp>-fetch-report.json
```

The main fetch report records `sourceAdoptionHistory` and `sourceResolution`.

## Byte-identical winners

If the comparison winner is byte-identical to the current staged source, Mnemosyne does not perform a pointless media swap. It still preserves a rollback copy and records that the comparison resolved the source decision.

## Safety boundary

Source adoption operates only inside `$HOME\Mnemosyne\staging`.

It does not:

- write tags
- modify cover art
- move files into the final library
- mark the acquisition complete

The next safe mutation boundary is metadata normalization/tagging.
