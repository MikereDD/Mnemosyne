# CLI Help Plan

## Status

Work-in-progress design for Mnemosyne's detailed command help and recovery
guidance.

## Goal

Mnemosyne's help system is part of the safety architecture. A user should be
able to understand what a command reads, what it may write, where it sits in
the lifecycle, what prerequisites it expects, and how to recover from common
failures before applying a mutating operation.

## Top-level help

`mnemosyne --help` should explain:

- Mnemosyne's acquisition and library-normalization roles.
- The acquisition lifecycle.
- The existing-library normalization lifecycle.
- Command groups and their purpose.
- The preview-before-mutation rule.
- How to get command-specific and topic-specific help.

## Command help contract

Every substantial command should eventually document:

1. Purpose.
2. Lifecycle position.
3. What it reads.
4. What it may write or mutate.
5. Prerequisites.
6. Arguments and options.
7. Safety gates.
8. Expected output and success state.
9. Common failure states.
10. Recovery guidance.
11. Examples.

## Batch help priority

Batch acquisition is the first detailed-help target because it combines queue
parsing, metadata provenance, plan resolution, execution planning, durable
state, retries, staging, source resolution, and lifecycle progression.

`mnemosyne batch --help` should clearly distinguish:

- Queue preview only.
- `--resolve-plans` provider-backed planning.
- `--execution-plan` dry-run sequencing.
- `--apply` staging fetches only.
- `--retry-failed` explicit retry of prior failed fetches.
- `--lifecycle-plan` read-only next-action reporting.

It should explicitly state that batch fetch does **not** automatically tag,
place, complete, clean staging, or prune queue entries.

## Recovery guidance

Recovery should prefer deterministic inspection over blind repetition.

- Do not redownload a valid staged item merely because a later step failed.
- Do not retry a recorded failed batch item without explicit retry behavior.
- Do not delete staging before final-library verification and completion proof.
- Do not treat provider-derived canonical dates as verified when placement
  rules require explicit provenance.
- Inspect current job/state before deciding the next action.

## Future topic help

```text
mnemosyne help workflow
mnemosyne help safety
mnemosyne help staging
mnemosyne help editions
mnemosyne help quality
mnemosyne help metadata
mnemosyne help verification
mnemosyne help receipts
mnemosyne help queues
mnemosyne help recovery
```

## First implementation slice

1. Improve top-level Typer help.
2. Improve `mnemosyne batch --help`.
3. Add tests protecting critical safety wording.
4. Keep behavior unchanged.
5. Run the full suite.