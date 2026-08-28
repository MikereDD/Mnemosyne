from __future__ import annotations

from pathlib import Path

import pytest

from mnemosyne.batch import BatchQueueError, fetch_queue_path, parse_fetch_queue
from mnemosyne.models import MediaType


def _write_queue(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.mark.parametrize(
    ("media_type", "filename"),
    [
        (MediaType.AUDIOBOOK, "audiobook-links.txt"),
        (MediaType.EBOOK, "ebook-links.txt"),
        (MediaType.MUSIC, "music-links.txt"),
    ],
)
def test_fetch_queue_path_uses_canonical_filename(
    tmp_path: Path,
    media_type: MediaType,
    filename: str,
) -> None:
    assert fetch_queue_path(media_type, root=tmp_path) == tmp_path / "fetch" / filename


def test_parse_queue_preserves_order_and_source_line_numbers(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "# first batch\n"
        "\n"
        "https://archive.org/details/animal-farm.sna\n"
        "https://www.archive.org/details/edisons_conquest_of_mars_0806_librivox/\n",
    )

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.total_lines == 4
    assert preview.comment_lines == 1
    assert preview.blank_lines == 1
    assert [item.line_number for item in preview.items] == [3, 4]
    assert [item.identifier for item in preview.items] == [
        "animal-farm.sna",
        "edisons_conquest_of_mars_0806_librivox",
    ]


def test_parse_queue_canonicalizes_archive_urls_for_duplicate_detection(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "http://www.archive.org/details/animal-farm.sna?view=theater\n"
        "https://archive.org/details/animal-farm.sna/\n",
    )

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.ready_count == 1
    assert preview.items[0].canonical_url == (
        "https://archive.org/details/animal-farm.sna"
    )
    assert preview.duplicate_count == 1
    assert preview.duplicates[0].line_number == 2
    assert preview.duplicates[0].detail == "Duplicates line 1."


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://archive.org/details/test", "http or https"),
        ("https://example.com/details/test", "Unsupported provider"),
        ("https://archive.org/download/test", "/details/<identifier>"),
        ("https://archive.org/details/", "/details/<identifier>"),
        ("https://archive.org/details/bad%20identifier", "unsupported characters"),
    ],
)
def test_parse_queue_classifies_invalid_entries(
    tmp_path: Path,
    url: str,
    message: str,
) -> None:
    queue = _write_queue(tmp_path / "audiobook-links.txt", url + "\n")

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.ready_count == 0
    assert preview.invalid_count == 1
    assert preview.invalid[0].line_number == 1
    assert message in preview.invalid[0].detail


def test_parse_queue_is_read_only(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "# keep this exactly\n"
        "https://archive.org/details/animal-farm.sna\n"
        "https://archive.org/details/animal-farm.sna\n",
    )
    before = queue.read_bytes()

    parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert queue.read_bytes() == before


def test_parse_queue_missing_file_fails_safely(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"

    with pytest.raises(BatchQueueError, match="does not exist"):
        parse_fetch_queue(MediaType.AUDIOBOOK, missing)
