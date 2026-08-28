from __future__ import annotations

from .editions import choose_audio_edition, discover_audio_editions
from .models import AcquisitionPlan, CandidateKind, MediaType
from .paths import canonical_destination


def build_plan(
    item,
    library_root,
    *,
    preferred_audio_format: str | None = None,
) -> AcquisitionPlan:
    warnings: list[str] = []

    creator = item.creator or "Unknown Creator"
    if not item.creator:
        warnings.append("Creator/author was not identified.")

    if not item.year:
        warnings.append(
            "Publication/release year was not identified. "
            "Use --year to supply a verified year before applying."
        )

    editions = discover_audio_editions(item.candidates)
    selected_edition = choose_audio_edition(
        editions,
        preferred_format=preferred_audio_format,
    )

    selected_audio = selected_edition.candidates if selected_edition else []

    if preferred_audio_format and selected_edition is None:
        warnings.append(
            f"No playable audio edition matched preferred format "
            f"{preferred_audio_format!r}."
        )

    if item.media_type is MediaType.AUDIOBOOK and not selected_audio:
        warnings.append("No playable audiobook audio edition was found.")

    cover_candidates = sorted(
        (c for c in item.candidates if c.kind is CandidateKind.COVER),
        key=lambda candidate: (candidate.score, candidate.size or 0),
        reverse=True,
    )
    selected_cover = cover_candidates[0] if cover_candidates else None
    if not selected_cover:
        warnings.append("No cover candidate was found in the Archive item.")

    destination = canonical_destination(
        library_root=library_root,
        media_type=item.media_type,
        creator=creator,
        title=item.title,
        year=item.year,
    )

    return AcquisitionPlan(
        item=item,
        destination=destination,
        selected_audio=selected_audio,
        selected_cover=selected_cover,
        warnings=warnings,
        audio_editions=editions,
        selected_edition_key=selected_edition.key if selected_edition else None,
    )
