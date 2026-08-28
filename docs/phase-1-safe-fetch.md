# Phase 1 — Safe Fetch v1

Safe Fetch v1 extends the Archive.org plan milestone without touching the final media library.

## Safety boundary

The command:

```powershell
mnemosyne fetch audiobook "<url>"
```

is still preview-only.

A network download requires explicit intent:

```powershell
mnemosyne fetch audiobook "<url>" --apply
```

Even with `--apply`, Safe Fetch v1 writes only to:

```text
$HOME\Mnemosyne\staging\<job-id>\
```

It does **not** rename into the canonical library, fetch/attach cover art, tag metadata, or mark the acquisition complete.

## Transactional staging behavior

The selected source audio is downloaded to a temporary `.part` file.

Only after the download passes the current validations is the `.part` file atomically renamed to its staged filename.

Mnemosyne refuses to overwrite an existing staging target.

## Current verification

Safe Fetch v1 verifies:

- successful HTTP response
- response is not obviously HTML/XHTML
- non-zero byte count
- exact byte count when Archive supplies an expected file size
- basic container/file signature for supported formats
- SHA-256 of the downloaded bytes

The staging job also receives:

```text
fetch-report.json
```

containing source provenance, planned final destination, file size, signature, and SHA-256.

## Completion semantics

A successfully staged file is **not** a completed acquisition.

Safe Fetch v1 reports:

```text
STAGED + VERIFIED
Final library modified: NO
```

Later Phase 1 slices will add cover retrieval, metadata normalization, final verification, transactional final placement, and the persistent job ledger.
