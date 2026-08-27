# Phase 2 — Archive Download Retry Hardening

A real 18-file LibriVox MP3 acquisition exposed a transient HTTP 500 from an
Archive.org storage node after the canonical `archive.org/download/...` URL
redirected to that node.

Mnemosyne now treats selected transport failures as transient.

## Retryable HTTP responses

```text
408
425
429
500
502
503
504
```

The downloader uses bounded exponential backoff:

```text
attempt 1 -> wait 1 second
attempt 2 -> wait 2 seconds
attempt 3 -> wait 4 seconds
attempt 4 -> wait 8 seconds
attempt 5 -> fail
```

Retries restart the individual file from byte zero. A stale `.part` file is
removed before every attempt.

## Retryable transport failures

Timeouts, network failures, and remote-protocol interruptions are retried using
the same bounded policy.

## Permanent failures

Non-transient HTTP failures such as 400, 401, 403, 404, and 410 fail
immediately.

## Error boundary

Raw `httpx` exceptions no longer escape through the CLI for normal HTTP
failures. They are converted into `FetchError`, allowing Mnemosyne to print a
controlled `Fetch failed:` message instead of a Python traceback.

## Transaction rule

This does not weaken multi-file atomicity.

If one chapter still cannot be obtained after all retry attempts, the entire
new staging job is removed. Mnemosyne never presents a partial chapter set as a
completed staged audiobook.

Cross-run resume of already verified chapters remains a separate future
feature.
