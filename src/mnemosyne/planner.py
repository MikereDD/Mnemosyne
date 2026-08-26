from __future__ import annotations

from .models import AcquisitionPlan, CandidateKind, MediaType
from .paths import canonical_destination


def build_plan(item, library_root) -> AcquisitionPlan:
    warnings: list[str] = []

    creator = item.creator or "Unknown Creator"
    if not item.creator:
        warnings.append("Creator/author was not identified.")

    if not item.year:
        warnings.append(
            "Publication/release year was not identified. "
            "Use --year to supply a verified year before applying."
        )

    audio_candidates = sorted(
        (
            candidate
            for candidate in item.candidates
            if candidate.kind is CandidateKind.AUDIO and candidate.playable
        ),
        key=lambda candidate: (candidate.score, candidate.size or 0),
        reverse=True,
    )

    selected_audio = audio_candidates[:1]

    if item.media_type is MediaType.AUDIOBOOK and not selected_audio:
        warnings.append("No playable audiobook audio candidate was found.")

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
    )
