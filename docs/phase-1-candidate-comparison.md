# Phase 1 — Actual Candidate Comparison

When provider metadata conflicts with downloaded media, Mnemosyne explicitly compares playable alternatives using their actual stream properties.

## Repeatable evidence

Each comparison is preserved as a separate run:

```text
$HOME\Mnemosyne\staging\<job-id>\comparison\
├── run-xxxxxxxx\
│   ├── ...
│   └── comparison-report.json
└── run-yyyyyyyy\
    ├── ...
    └── comparison-report.json
```

A new comparison never overwrites a previous result.

## Actual codec identification

Some Mutagen parsers, notably MP3, expose bitrate/sample-rate/channel data without a generic `info.codec` field.

Mnemosyne therefore also identifies codecs from the parser/container itself. An MP3 must be reported as:

```text
codec   = MP3
quality = lossy
```

rather than `unknown`.

## Ranking policy

Ranking order:

1. verified lossless class
2. actual stream bitrate within a quality class
3. sample rate/channels as weak secondary information
4. provider original/derivative provenance as a **weak tie-breaker only**

The `original` marker is worth very little by design. A poor original must not beat a materially better derivative merely because Archive labeled it original.

Provider labels remain visible for provenance but do not control actual quality ranking.

## Safety

Comparison still performs no staged-source replacement and no final-library mutation.
