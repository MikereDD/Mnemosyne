from pathlib import Path

from mnemosyne.models import ArchiveItem, CandidateKind, MediaCandidate, MediaType
from mnemosyne.planner import build_plan


def _ebook(name: str, extension: str, score: int) -> MediaCandidate:
    return MediaCandidate(
        name=name,
        url=f"https://example.invalid/{name}",
        extension=extension,
        archive_format=extension.lstrip(".").upper(),
        source="original",
        size=1000,
        kind=CandidateKind.EBOOK,
        score=score,
    )


def _item(candidates: list[MediaCandidate]) -> ArchiveItem:
    return ArchiveItem(
        identifier="example-book",
        source_url="https://archive.org/details/example-book",
        media_type=MediaType.EBOOK,
        raw_title="Example Book",
        title="Example Book",
        creator="Example Author",
        year=2024,
        candidates=candidates,
    )


def test_ebook_plan_selects_best_ranked_edition() -> None:
    epub = _ebook("Example Book.epub", ".epub", 1020)
    pdf = _ebook("Example Book.pdf", ".pdf", 700)

    plan = build_plan(_item([pdf, epub]), Path("library"))

    assert plan.selected_ebook is epub
    assert plan.selected_ebook_edition_key == "ebook:Example Book.epub"
    assert len(plan.ebook_editions) == 2
    assert plan.selected_audio == []


def test_ebook_plan_honors_explicit_format_preference() -> None:
    epub = _ebook("Example Book.epub", ".epub", 1020)
    pdf = _ebook("Example Book.pdf", ".pdf", 700)

    plan = build_plan(
        _item([epub, pdf]),
        Path("library"),
        preferred_ebook_format="pdf",
    )

    assert plan.selected_ebook is pdf


def test_ebook_plan_blocks_missing_requested_format_without_fallback() -> None:
    epub = _ebook("Example Book.epub", ".epub", 1020)

    plan = build_plan(
        _item([epub]),
        Path("library"),
        preferred_ebook_format="mobi",
    )

    assert plan.selected_ebook is None
    assert any("preferred format" in warning for warning in plan.warnings)
    assert any("No supported eBook edition" in warning for warning in plan.warnings)


def test_ebook_plan_reports_no_supported_candidate() -> None:
    cover = MediaCandidate(
        name="cover.jpg",
        url="https://example.invalid/cover.jpg",
        extension=".jpg",
        kind=CandidateKind.COVER,
        score=100,
    )

    plan = build_plan(_item([cover]), Path("library"))

    assert plan.selected_ebook is None
    assert plan.ebook_editions == []
    assert any("No supported eBook edition" in warning for warning in plan.warnings)
