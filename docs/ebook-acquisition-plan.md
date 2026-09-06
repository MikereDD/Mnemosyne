# eBook Acquisition Plan

Phase 3 begins by generalizing the proven acquisition architecture without weakening
its safety rules.

## Goal

Add eBooks as a first-class acquisition path while reusing the same lifecycle:

```text
Discover -> Identify -> Preview/Plan -> Fetch -> Normalize -> Verify -> Place -> Complete
```

The first provider remains Internet Archive.

## Canonical filesystem structure

```text
eBooks/
└── Author/
    └── Series?/
        └── Book Title/
```

Genre is descriptive metadata and must not create the physical eBook hierarchy.

## First implementation sequence

1. Recognize eBook candidates independently of audio candidates.
2. Rank provider candidates without silently treating every document-like file as a book.
3. Build an eBook-specific acquisition plan.
4. Preview the selected edition before fetching.
5. Fetch into isolated staging.
6. Validate file signatures / container structure.
7. Preserve source file bytes whenever practical.
8. Normalize metadata and cover art without destructive conversion.
9. Verify staged output before canonical placement.
10. Place transactionally and emit a completion receipt.

## Initial source preference

For Internet Archive candidates, the first deterministic preference baseline is:

```text
EPUB
-> AZW3
-> MOBI
-> AZW
-> PDF
-> DJVU
```

Provider originals receive a preference bonus. This ranking is a starting policy,
not a claim that one format is universally superior.

## Safety rules

- No canonical placement from an unresolved author or title.
- No silent overwrite.
- No automatic format conversion merely to satisfy preference ordering.
- Provider metadata remains evidence, not unquestionable truth.
- Ambiguous editions remain visible to the user.
- A fetched file is not complete until its actual format has been verified.
- Staging survives failed verification or placement.
- Existing-library organization remains a separate Phase 4 workflow.

## Scope of the first code slice

The first code slice intentionally stops at provider candidate recognition:

- add a first-class `CandidateKind.EBOOK`;
- recognize `.epub`, `.pdf`, `.mobi`, `.azw`, `.azw3`, and `.djvu`;
- apply deterministic provider-level preference scoring;
- protect the behavior with tests.

It does not yet fetch or place eBooks.
