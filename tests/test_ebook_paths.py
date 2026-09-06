from pathlib import Path

from mnemosyne.models import MediaType
from mnemosyne.paths import canonical_destination


def test_ebook_destination_is_author_then_book_title() -> None:
    root = Path("library")

    destination = canonical_destination(
        root,
        MediaType.EBOOK,
        "Lewis Carroll",
        "Alice's Adventures in Wonderland",
        1865,
    )

    assert destination == (
        root
        / "eBooks"
        / "Lewis Carroll"
        / "Alice's Adventures in Wonderland"
    )


def test_ebook_destination_does_not_include_redundant_media_folder() -> None:
    destination = canonical_destination(
        Path("library"),
        MediaType.EBOOK,
        "Example Author",
        "Example Book",
        2024,
    )

    assert "eBook" not in destination.parts
    assert destination.name == "Example Book"


def test_ebook_destination_does_not_encode_year_or_author_in_book_folder() -> None:
    destination = canonical_destination(
        Path("library"),
        MediaType.EBOOK,
        "Example Author",
        "Example Book",
        2024,
    )

    assert destination.name == "Example Book"
    assert "2024" not in destination.name
    assert "Example Author" not in destination.name


def test_ebook_destination_sanitizes_windows_components() -> None:
    destination = canonical_destination(
        Path("library"),
        MediaType.EBOOK,
        "Author: Name",
        'Book / Title?',
        None,
    )

    assert destination == (
        Path("library")
        / "eBooks"
        / "Author_ Name"
        / "Book _ Title_"
    )
