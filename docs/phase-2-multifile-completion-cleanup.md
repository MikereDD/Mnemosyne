# Multi-file Completion + Durable Cleanup

Multi-file lifecycle completion certifies the final directory as one ordered
edition rather than pretending one chapter represents the audiobook.

Completion verifies every final chapter SHA-256, the ordered edition SHA-256,
the final cover SHA-256, readiness provenance, placement provenance, and final
destination state before marking the acquisition complete.

Cleanup then re-verifies the entire final edition and writes a durable receipt
containing the full `finalPlacement` structure, including every final file path
and SHA-256 plus the ordered edition SHA-256. Only after that receipt is
written and verified does explicit cleanup delete staging.

Destructive cleanup still requires the exact job ID through `--confirm`.
Single-file completion and cleanup implementations remain unchanged.
