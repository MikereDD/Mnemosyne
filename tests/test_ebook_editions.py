from mnemosyne.ebook_editions import choose_ebook_edition, discover_ebook_editions
from mnemosyne.models import CandidateKind, MediaCandidate


def _candidate(name: str, extension: str, score: int, *, source: str = "derivative") -> MediaCandidate:
    return MediaCandidate(
        name=name,
        url=f"https://example.invalid/{name}",
        extension=extension,
        archive_format=extension.lstrip(".").upper(),
        source=source,
        size=1000,
        kind=CandidateKind.EBOOK,
        score=score,
    )


def test_discovers_only_ebook_candidates_and_sorts_by_score() -> None:
    epub = _candidate("Book.epub", ".epub", 1020, source="original")
    pdf = _candidate("Book.pdf", ".pdf", 700)
    cover = MediaCandidate(
        name="cover.jpg",
        url="https://example.invalid/cover.jpg",
        extension=".jpg",
        kind=CandidateKind.COVER,
        score=9999,
    )

    editions = discover_ebook_editions([pdf, cover, epub])

    assert [edition.extension for edition in editions] == [".epub", ".pdf"]
    assert editions[0].candidate is epub


def test_preferred_format_selects_matching_edition() -> None:
    epub = _candidate("Book.epub", ".epub", 1020)
    pdf = _candidate("Book.pdf", ".pdf", 700)

    edition = choose_ebook_edition(
        discover_ebook_editions([epub, pdf]),
        preferred_format="pdf",
    )

    assert edition is not None
    assert edition.extension == ".pdf"
    assert edition.candidate is pdf


def test_missing_preferred_format_returns_none_instead_of_guessing() -> None:
    epub = _candidate("Book.epub", ".epub", 1020)

    edition = choose_ebook_edition(
        discover_ebook_editions([epub]),
        preferred_format="mobi",
    )

    assert edition is None


def test_all_alternatives_remain_visible() -> None:
    editions = discover_ebook_editions([
        _candidate("Book.epub", ".epub", 1020),
        _candidate("Book.azw3", ".azw3", 820),
        _candidate("Book.pdf", ".pdf", 700),
    ])

    assert len(editions) == 3
    assert {edition.candidate.name for edition in editions} == {
        "Book.epub",
        "Book.azw3",
        "Book.pdf",
    }
