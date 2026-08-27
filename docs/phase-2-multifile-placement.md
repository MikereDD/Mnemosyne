# Multi-file Placement

A readiness-certified multi-file audiobook is placed as one directory-level transaction.

Preview rechecks every staged file hash, ordered edition SHA-256, cover SHA-256, readiness identity/checks, and destination absence.

Apply copies every chapter and the cover into a hidden sibling directory on the destination filesystem, verifies every copy and the ordered edition hash, then commits with one same-parent directory rename. After commit, every chapter, the edition hash, and the cover are verified again.

Existing destinations are never overwritten or merged. If post-commit verification fails, Mnemosyne removes only the new destination created by the current transaction and restores the previous fetch report.
