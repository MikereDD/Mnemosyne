from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .models import AudioEdition, CandidateKind, MediaCandidate


_SEQUENCE_PATTERNS = (
    re.compile(r"(?P<prefix>.*?)[_-](?P<num>\d{2,3})(?P<suffix>[_-][^/\\]+)?$", re.IGNORECASE),
    re.compile(r"(?P<prefix>.*?)(?P<num>\d{2,3})(?P<suffix>[^0-9/\\]*)$", re.IGNORECASE),
)


def _stem_sequence(candidate: MediaCandidate) -> tuple[str, int] | None:
    stem = Path(candidate.name).stem
    for pattern in _SEQUENCE_PATTERNS:
        match = pattern.match(stem)
        if not match:
            continue
        try:
            number = int(match.group("num"))
        except (TypeError, ValueError):
            continue
        if number <= 0:
            continue
        prefix = (match.group("prefix") or "").rstrip("_- .").lower()
        suffix = (match.groupdict().get("suffix") or "").lower()
        normalized = f"{prefix}|{suffix}"
        return normalized, number
    return None


def _edition_score(candidates: list[MediaCandidate], *, multi_file: bool) -> int:
    if not candidates:
        return 0
    base = max(candidate.score for candidate in candidates)
    if multi_file:
        # A complete-looking chapter set is a valid edition but a complete
        # single-file audiobook remains a little simpler to handle by default.
        base -= 20
    return base


def discover_audio_editions(candidates: list[MediaCandidate]) -> list[AudioEdition]:
    playable = [
        c for c in candidates
        if c.kind is CandidateKind.AUDIO and c.playable
    ]

    grouped: dict[tuple[str, str | None, str | None, str], list[tuple[int, MediaCandidate]]] = defaultdict(list)
    singles: list[MediaCandidate] = []

    for candidate in playable:
        sequence = _stem_sequence(candidate)
        if sequence is None:
            singles.append(candidate)
            continue
        normalized_stem, number = sequence
        key = (
            candidate.extension.lower(),
            candidate.archive_format,
            candidate.source,
            normalized_stem,
        )
        grouped[key].append((number, candidate))

    editions: list[AudioEdition] = []

    for candidate in singles:
        total_size = candidate.size
        editions.append(
            AudioEdition(
                key=f"single:{candidate.name}",
                label=f"Complete {candidate.extension.lstrip('.').upper()}",
                extension=candidate.extension.lower(),
                archive_format=candidate.archive_format,
                source=candidate.source,
                candidates=[candidate],
                score=_edition_score([candidate], multi_file=False),
                total_size=total_size,
                multi_file=False,
                sequence_numbers=[],
            )
        )

    for (extension, archive_format, source, normalized_stem), members in grouped.items():
        members.sort(key=lambda item: item[0])
        numbers = [number for number, _ in members]
        candidates_sorted = [candidate for _, candidate in members]

        # One numbered file by itself is not enough evidence of a multi-file edition.
        if len(candidates_sorted) == 1:
            candidate = candidates_sorted[0]
            editions.append(
                AudioEdition(
                    key=f"single:{candidate.name}",
                    label=f"Single {extension.lstrip('.').upper()} file",
                    extension=extension,
                    archive_format=archive_format,
                    source=source,
                    candidates=[candidate],
                    score=_edition_score([candidate], multi_file=False),
                    total_size=candidate.size,
                    multi_file=False,
                    sequence_numbers=numbers,
                )
            )
            continue

        total_size = None
        if all(candidate.size is not None for candidate in candidates_sorted):
            total_size = sum(int(candidate.size) for candidate in candidates_sorted if candidate.size is not None)

        ext_label = extension.lstrip(".").upper()
        editions.append(
            AudioEdition(
                key=f"set:{extension}:{archive_format}:{source}:{normalized_stem}",
                label=f"{ext_label} chapter set ({len(candidates_sorted)} files)",
                extension=extension,
                archive_format=archive_format,
                source=source,
                candidates=candidates_sorted,
                score=_edition_score(candidates_sorted, multi_file=True),
                total_size=total_size,
                multi_file=True,
                sequence_numbers=numbers,
            )
        )

    editions.sort(
        key=lambda edition: (
            edition.score,
            edition.total_size or 0,
            -len(edition.candidates) if edition.multi_file else 0,
        ),
        reverse=True,
    )
    return editions


def choose_audio_edition(
    editions: list[AudioEdition],
    *,
    preferred_format: str | None = None,
) -> AudioEdition | None:
    if not editions:
        return None

    if preferred_format:
        normalized = preferred_format.lower().lstrip(".")
        matches = [
            edition for edition in editions
            if edition.extension.lower().lstrip(".") == normalized
        ]
        if not matches:
            return None
        return sorted(
            matches,
            key=lambda edition: (edition.score, edition.total_size or 0),
            reverse=True,
        )[0]

    return editions[0]
