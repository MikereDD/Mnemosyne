from __future__ import annotations

from .models import CandidateKind, EbookEdition, MediaCandidate


def discover_ebook_editions(
    candidates: list[MediaCandidate],
) -> list[EbookEdition]:
    editions = [
        EbookEdition(
            key=f"ebook:{candidate.name}",
            label=candidate.extension.lstrip(".").upper(),
            extension=candidate.extension.lower(),
            archive_format=candidate.archive_format,
            source=candidate.source,
            candidate=candidate,
            score=candidate.score,
            size=candidate.size,
        )
        for candidate in candidates
        if candidate.kind is CandidateKind.EBOOK
    ]

    editions.sort(
        key=lambda edition: (
            edition.score,
            edition.size or 0,
            edition.candidate.name.lower(),
        ),
        reverse=True,
    )
    return editions


def choose_ebook_edition(
    editions: list[EbookEdition],
    *,
    preferred_format: str | None = None,
) -> EbookEdition | None:
    if not editions:
        return None

    if preferred_format:
        normalized = preferred_format.lower().lstrip(".")
        matches = [
            edition
            for edition in editions
            if edition.extension.lower().lstrip(".") == normalized
        ]
        if not matches:
            return None
        return sorted(
            matches,
            key=lambda edition: (
                edition.score,
                edition.size or 0,
                edition.candidate.name.lower(),
            ),
            reverse=True,
        )[0]

    return editions[0]
