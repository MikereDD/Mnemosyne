# Phase 1 — Staging Normalization and Cover Validation

This slice extends Safe Fetch while preserving the same safety boundary:

```text
final library modified: NO
```

## Canonical staged audio name

After the selected audio has downloaded and passed size/signature verification, Mnemosyne renames it *inside the isolated staging job* to the canonical media name.

For the first fixture:

```text
sachnoi.app - Animal Farm Audio book.m4a
```

becomes:

```text
Animal Farm - George Orwell (1945).m4a
```

The original provider filename remains recorded in `fetch-report.json`.

## Cover retrieval

If the plan has a selected cover candidate, Mnemosyne now downloads that candidate into the staging job and validates that the payload is actually an image.

Current supported artwork containers:

- JPEG
- PNG
- WebP

JPEG source artwork is normalized to the standalone library convention:

```text
cover.jpg
```

PNG and WebP remain in their native format until a future image-normalization slice can safely transcode them.

## Cover verification

Current cover checks include:

- successful HTTP response
- expected byte count when supplied by Archive
- rejection of HTML/XML error payloads
- image magic/signature validation
- width/height extraction when supported by the lightweight parser
- SHA-256

This does not yet prove that the image is the *correct edition cover*. Semantic/edition-aware cover scoring remains a future enrichment task.

## Staging result

A successful job now looks like:

```text
$HOME\Mnemosyne\staging\<job-id>\
├── Animal Farm - George Orwell (1945).m4a
├── cover.jpg
└── fetch-report.json
```

The next major boundary is metadata inspection/tagging and full final-placement verification.
