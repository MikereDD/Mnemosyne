# Phase 1 — Transactional Final-Library Placement

A staging job may enter the final media library only after `mnemosyne ready` has certified every current readiness gate.

## Preview

```powershell
mnemosyne place
```

Preview revalidates the readiness report and current staged hashes. It creates no final-library files or directories.

## Apply

```powershell
mnemosyne place --apply
```

Or target a specific staging job:

```powershell
mnemosyne place "$HOME\Mnemosyne\staging\<job-id>" --apply
```

## Conflict rule

Mnemosyne does not overwrite, merge into, or silently reuse an existing final destination.

If the canonical destination already exists, placement stops and requires a future explicit conflict-resolution workflow.

## Transaction model

1. Re-read `fetch-report.json`.
2. Require `readiness-report.json` with status `ready-for-placement`.
3. Require every recorded readiness check to pass.
4. Re-hash staged audio and cover and bind them to the readiness report.
5. Refuse any existing final destination.
6. Create required parent directories.
7. Create a hidden sibling transaction directory on the destination filesystem.
8. Copy canonical audio and cover into that transaction directory.
9. Verify both copied hashes before commit.
10. Re-check that the final destination is still absent.
11. Atomically rename the complete transaction directory to the canonical destination.
12. Re-hash the final audio and cover after placement.
13. Write `placement-report.json` in staging.
14. Atomically update `fetch-report.json`.

## Rollback

This placement transaction never overwrites an existing destination.

If failure occurs after commit but before the transaction is fully recorded, rollback removes only the destination created by the current transaction and restores the previous staging report.

Temporary transaction directories and empty parent directories created by a failed transaction are cleaned up where possible.

## Final directory contents

For the first audiobook fixture:

```text
$HOME\Downloads\Mnemosyne\Audiobooks\
└── George Orwell\
    └── Audiobook\
        └── Animal Farm - George Orwell (1945)\
            ├── Animal Farm - George Orwell (1945).m4a
            └── cover.jpg
```

Internal provenance remains in the staging job rather than polluting the user-facing media directory.

## Completion state

Successful placement changes the staging report to:

```text
status               = placed-and-verified
finalLibraryModified = true
```

Placement is not yet the same thing as complete acquisition lifecycle cleanup. A later job-ledger/completion phase can govern staging retention, pruning, and archival policy.
