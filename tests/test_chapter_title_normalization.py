from mnemosyne.multifile_metadata import _normalize_chapter_title


def test_strips_leading_numeric_prefix() -> None:
    assert _normalize_chapter_title("01 - Chapter I") == "Chapter I"


def test_normalizes_internal_whitespace() -> None:
    assert _normalize_chapter_title("12 - Chapter  XII") == "Chapter XII"


def test_keeps_clean_title() -> None:
    assert _normalize_chapter_title("The Cylinder") == "The Cylinder"


def test_accepts_colon_separator() -> None:
    assert _normalize_chapter_title("03: Chapter III") == "Chapter III"
