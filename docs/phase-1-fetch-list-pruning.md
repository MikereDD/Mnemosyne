# Phase 1 — Explicit Fetch-List Pruning

Fetch-list files are human-owned acquisition queues. Mnemosyne never removes URLs merely because a job was attempted or even completed.

Pruning is a separate explicit operation after:

1. final placement is verified,
2. lifecycle completion is certified,
3. staging cleanup has archived a durable completed-job receipt.

## Preview

```powershell
mnemosyne prune <job-id>
```

Preview reads the durable completed-job receipt and identifies only exact active URL matches in the appropriate fetch list.

Blank lines and comments are ignored.

No fuzzy matching is used.

## Apply

Pruning requires the exact completed source URL as confirmation:

```powershell
mnemosyne prune <job-id> `
  --apply `
  --confirm-url "<exact-source-url>"
```

## Backup

Before mutation, the full fetch list is copied to:

```text
$HOME\Mnemosyne\state\fetch-list-backups\
```

The backup is byte-for-byte verified before the rewrite proceeds.

## Atomic rewrite

Mnemosyne:

1. re-reads the current fetch list,
2. re-evaluates exact matching active entries,
3. writes a temporary replacement,
4. verifies the target URL is absent from the replacement,
5. atomically replaces the live fetch list,
6. re-reads the live file,
7. verifies the target URL is absent.

If post-commit verification fails, Mnemosyne restores the verified backup.

## Duplicate exact entries

If the same completed URL appears more than once as an active line, all exact duplicates are removed in one explicit prune transaction and the removed count is recorded.

## Durable receipt update

The completed-job receipt records:

- fetch-list path
- verified backup path
- prune timestamp
- removed count
- `fetchListPruned = true`

Final-library media is never touched by pruning.
