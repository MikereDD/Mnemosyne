from __future__ import annotations

from .editions import choose_audio_edition, discover_audio_editions
from .ebook_editions import choose_ebook_edition, discover_ebook_editions
from .models import AcquisitionPlan, CandidateKind, MediaType
from .paths import canonical_destination


def build_plan(
    item,
    library_root,
    *,
    preferred_audio_format: str | None = None,
    preferred_ebook_format: str | None = None,
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

    ebook_editions = discover_ebook_editions(item.candidates)
    selected_ebook_edition = choose_ebook_edition(
        ebook_editions,
        preferred_format=preferred_ebook_format,
    )
    selected_ebook = (
        selected_ebook_edition.candidate
        if selected_ebook_edition
        else None
    )

    if preferred_ebook_format and selected_ebook_edition is None:
        warnings.append(
            f"No eBook edition matched preferred format "
            f"{preferred_ebook_format!r}."
        )

    if item.media_type is MediaType.EBOOK and selected_ebook is None:
        warnings.append("No supported eBook edition was found.")

    cover_candidates = sorted(
        (c for c in item.candidates if c.kind is CandidateKind.COVER),
        key=lambda candidate: (candidate.score, candidate.size or 0),
        reverse=True,
    )
    selected_cover = cover_candidates[0] if cover_candidates else None
    if not selected_cover:
        warnings.append("No cover candidate was found in the Archive item.")

    verified_series = None
    verified_series_index = None

    if item.media_type is MediaType.EBOOK:
        if item.series is not None:
            if item.series_provenance == "verified-override":
                verified_series = item.series
            else:
                warnings.append(
                    "Series metadata exists but is not verified; "
                    "filesystem series hierarchy will not be created."
                )

        if item.series_index is not None:
            if (
                item.series_index_provenance == "verified-override"
                and verified_series is not None
            ):
                verified_series_index = item.series_index
            else:
                warnings.append(
                    "Series index exists without verified series provenance; "
                    "filesystem ordering will not be applied."
                )

    destination = canonical_destination(
        library_root=library_root,
        media_type=item.media_type,
        creator=creator,
        title=item.title,
        year=item.year,
        series=verified_series,
        series_index=verified_series_index,
    )

    return AcquisitionPlan(
        item=item,
        destination=destination,
        selected_audio=selected_audio,
        selected_cover=selected_cover,
        warnings=warnings,
        audio_editions=editions,
        selected_edition_key=selected_edition.key if selected_edition else None,
        selected_ebook=selected_ebook,
        ebook_editions=ebook_editions,
        selected_ebook_edition_key=(
            selected_ebook_edition.key if selected_ebook_edition else None
        ),
    )
