# Changelog fragment — Archive retry hardening

- Retry transient Archive/download HTTP failures: 408, 425, 429, 500, 502, 503, 504.
- Retry transient timeout/network/protocol failures with bounded exponential backoff.
- Remove stale `.part` files before retry attempts.
- Convert raw HTTP client failures into Mnemosyne `FetchError`.
- Preserve all-or-nothing multi-file staging behavior after retry exhaustion.
