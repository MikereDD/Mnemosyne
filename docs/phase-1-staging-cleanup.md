# Phase 1 — Completed Staging Cleanup

Completed staging contains valuable provenance, rollback media, comparison evidence, and reports. Mnemosyne therefore never treats staging cleanup as ordinary housekeeping.

Before deletion, a durable completion receipt is archived outside staging.

## Preview

```powershell
mnemosyne cleanup
```

Preview:

- requires a lifecycle-complete job
- re-verifies final audio SHA-256
- re-verifies final cover SHA-256
- shows the number and size of files that would be removed
- shows the durable receipt destination
- deletes nothing

## Apply

Cleanup is deliberately harder to trigger than normal mutations.

Both flags are required:

```powershell
mnemosyne cleanup --apply --confirm <exact-job-id>
```

The confirmation text must exactly equal the job ID.

## Durable receipt

Before staging deletion, Mnemosyne writes:

```text
$HOME\Mnemosyne\state\completed\<job-id>.json
```

The receipt preserves a compact durable record of:

- job ID
- source/provider
- media identity
- planned/final destination
- final audio and cover paths
- final audio and cover SHA-256
- readiness / placement / completion states
- original staging path
- staging size and file count
- cleanup timestamp
- fetch-list pruning state

The receipt is re-read and verified before deletion begins.

## Final pre-delete guard

Immediately before deleting staging, Mnemosyne hashes the final audio and cover again.

If either final file changed, cleanup stops.

## What cleanup deletes

Only the selected completed staging job directory is removed.

It does not:

- alter final-library audio
- alter final-library cover art
- delete the durable completion receipt
- prune fetch-list URLs

Fetch-list pruning remains a separate explicit workflow.
