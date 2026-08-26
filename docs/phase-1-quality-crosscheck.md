# Phase 1 — Provider Quality Cross-Check

Provider metadata is advisory, not authoritative.

The first real fixture exposed why this rule matters:

```text
Archive format label: Apple Lossless Audio
Actual staged codec: mp4a.40.2
Actual bitrate: ~125.6 kbps
```

`mp4a.40.2` is AAC-LC, a lossy codec. Therefore Mnemosyne must not continue treating this file as verified lossless simply because Archive's file manifest labels it that way.

## New rule

After a selected audio file is downloaded and structurally validated, Mnemosyne inspects the actual media stream.

The staged report records both:

```text
providerClaimedLossless
actualCodec
actualLossless
actualBitrateBps
actualSampleRateHz
actualChannels
```

If the provider claims lossless but the actual file is identified as lossy, the staging job becomes:

```text
needs-attention
```

rather than silently proceeding as though the claim were true.

## Why the file is still kept

A provider-quality mismatch does not automatically mean the file is bad.

For example, the AAC source may still be better than a lower-bitrate MP3 derivative. Mnemosyne preserves the verified staged file and reports the discrepancy so a later comparison/reselection step can make an informed decision.

## Safety consequence

Metadata writing and final placement should not proceed automatically while a quality mismatch remains unresolved.
