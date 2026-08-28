from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

from .models import AudioEdition, CandidateKind, MediaCandidate


_SEQUENCE_PATTERNS = (
    re.compile(r"(?P<prefix>.*?)[_-](?P<num>\d{2,3})(?P<suffix>[_-][^/\\]+)?$", re.IGNORECASE),
    re.compile(r"(?P<prefix>.*?)(?P<num>\d{2,3})(?P<suffix>[^0-9/\\]*)$", re.IGNORECASE),
)

_DISC_DIRECTORY_PATTERN = re.compile(
    r"^(?:disc|disk)[ _.-]*0*(?P<disc>\d+)$",
    re.IGNORECASE,
)

_DISC_TRACK_PATTERN = re.compile(
    r"^\s*0*(?P<track>\d{1,3})"
    r"(?:[._-]0*(?P<part>\d{1,3}))?"
    r"(?:\s*[.\-–—_:]\s*|\s+)",
    re.IGNORECASE,
)


_FLAT_DISC_SIDE_PATTERN = re.compile(
    r"(?:^|[_ .-])disc[ _.-]*0*(?P<disc>\d+)"
    r"[ _.-]*side[ _.-]*0*(?P<side>\d+)$",
    re.IGNORECASE,
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


def _disc_sequence(candidate: MediaCandidate) -> tuple[int, int, int] | None:
    """Return a conservative disc/track/part ordering key for Archive media."""
    normalized_name = candidate.name.replace("\\", "/")
    path = PurePosixPath(normalized_name)

    # Some Archive LP masters are stored as flat files such as
    # ``item_disc1side1.flac`` rather than inside explicit disc directories.
    flat_match = _FLAT_DISC_SIDE_PATTERN.search(path.stem)
    if flat_match:
        try:
            disc = int(flat_match.group("disc"))
            side = int(flat_match.group("side"))
        except (TypeError, ValueError):
            return None

        if disc <= 0 or side <= 0:
            return None

        return disc, side, 0

    if len(path.parts) < 2:
        return None

    disc_match = _DISC_DIRECTORY_PATTERN.match(path.parent.name)
    track_match = _DISC_TRACK_PATTERN.match(path.name)

    if not disc_match or not track_match:
        return None

    try:
        disc = int(disc_match.group("disc"))
        track = int(track_match.group("track"))
        part = int(track_match.group("part") or 0)
    except (TypeError, ValueError):
        return None

    if disc <= 0 or track <= 0 or part < 0:
        return None

    return disc, track, part


def _edition_score(candidates: list[MediaCandidate], *, multi_file: bool) -> int:
    if not candidates:
        return 0
    base = max(candidate.score for candidate in candidates)
    if multi_file:
        # A complete-looking chapter set is a valid edition but a complete
        # single-file audiobook remains a little simpler to handle by default.
        base -= 20
    return base


def _append_single_edition(
    editions: list[AudioEdition],
    candidate: MediaCandidate,
    *,
    sequence_numbers: list[int] | None = None,
    label: str | None = None,
) -> None:
    editions.append(
        AudioEdition(
            key=f"single:{candidate.name}",
            label=label or f"Complete {candidate.extension.lstrip('.').upper()}",
            extension=candidate.extension.lower(),
            archive_format=candidate.archive_format,
            source=candidate.source,
            candidates=[candidate],
            score=_edition_score([candidate], multi_file=False),
            total_size=candidate.size,
            multi_file=False,
            sequence_numbers=sequence_numbers or [],
        )
    )


def _append_multi_file_edition(
    editions: list[AudioEdition],
    *,
    key: str,
    label_kind: str,
    extension: str,
    archive_format: str | None,
    source: str | None,
    candidates_sorted: list[MediaCandidate],
    sequence_numbers: list[int],
) -> None:
    total_size = None
    if all(candidate.size is not None for candidate in candidates_sorted):
        total_size = sum(
            int(candidate.size)
            for candidate in candidates_sorted
            if candidate.size is not None
        )

    ext_label = extension.lstrip(".").upper()
    editions.append(
        AudioEdition(
            key=key,
            label=f"{ext_label} {label_kind} ({len(candidates_sorted)} files)",
            extension=extension,
            archive_format=archive_format,
            source=source,
            candidates=candidates_sorted,
            score=_edition_score(candidates_sorted, multi_file=True),
            total_size=total_size,
            multi_file=True,
            sequence_numbers=sequence_numbers,
        )
    )


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

    # Preserve the existing common-stem grouping behavior first.
    for (extension, archive_format, source, normalized_stem), members in grouped.items():
        members.sort(key=lambda item: item[0])
        numbers = [number for number, _ in members]
        candidates_sorted = [candidate for _, candidate in members]

        # One numbered file by itself is not enough evidence of a multi-file edition.
        if len(candidates_sorted) == 1:
            candidate = candidates_sorted[0]

            # Nested disc/side material such as
            # ``disc1/01.01. Book IX - ...flac`` can look like a one-member
            # legacy stem group because the descriptive suffix differs for
            # every file. Defer those candidates to the conservative
            # disc-aware pass instead of prematurely declaring them singles.
            if _disc_sequence(candidate) is not None:
                singles.append(candidate)
            else:
                _append_single_edition(
                    editions,
                    candidate,
                    sequence_numbers=numbers,
                )
            continue

        _append_multi_file_edition(
            editions,
            key=f"set:{extension}:{archive_format}:{source}:{normalized_stem}",
            label_kind="chapter set",
            extension=extension,
            archive_format=archive_format,
            source=source,
            candidates_sorted=candidates_sorted,
            sequence_numbers=numbers,
        )

    # Archive LP/rip items can encode ordering in nested disc directories while
    # each basename carries a descriptive title. Consider only candidates that
    # failed the normal common-stem grouping pass.
    disc_groups: dict[
        tuple[str, str | None, str | None],
        list[tuple[tuple[int, int, int], MediaCandidate]],
    ] = defaultdict(list)
    remaining_singles: list[MediaCandidate] = []

    for candidate in singles:
        disc_sequence = _disc_sequence(candidate)
        if disc_sequence is None:
            remaining_singles.append(candidate)
            continue

        disc_key = (
            candidate.extension.lower(),
            candidate.archive_format,
            candidate.source,
        )
        disc_groups[disc_key].append((disc_sequence, candidate))

    for (extension, archive_format, source), members in disc_groups.items():
        members.sort(key=lambda item: item[0])

        disc_numbers = {order[0] for order, _ in members}
        identities = [order for order, _ in members]

        # Safety gate: nested names become one edition only when the evidence
        # spans multiple explicit disc directories and every ordering identity
        # is unique. Otherwise every file remains independent.
        if (
            len(members) < 2
            or len(disc_numbers) < 2
            or len(set(identities)) != len(identities)
        ):
            remaining_singles.extend(candidate for _, candidate in members)
            continue

        candidates_sorted = [candidate for _, candidate in members]
        sequence_numbers = list(range(1, len(candidates_sorted) + 1))

        _append_multi_file_edition(
            editions,
            key=f"discset:{extension}:{archive_format}:{source}",
            label_kind="disc set",
            extension=extension,
            archive_format=archive_format,
            source=source,
            candidates_sorted=candidates_sorted,
            sequence_numbers=sequence_numbers,
        )

    for candidate in remaining_singles:
        _append_single_edition(editions, candidate)

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
