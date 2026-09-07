import json
import zipfile
from pathlib import Path

import pytest

from mnemosyne.library_scan import LibraryScanError, LibraryScanMedia, scan_library


def _runtime(tmp_path: Path, monkeypatch) -> Path:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr("mnemosyne.library_scan.runtime_root", lambda: runtime)
    return runtime


def _book(root: Path, relative: str, name: str = "book.epub", data: bytes = b"book") -> Path:
    folder = root / "eBooks" / Path(relative)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    return path


def _epub(path: Path, title=None, creator=None, series=None) -> None:
    metadata = []
    if title:
        metadata.append(f"<dc:title>{title}</dc:title>")
    if creator:
        metadata.append(f"<dc:creator>{creator}</dc:creator>")
    if series:
        metadata.append(f'<meta property="belongs-to-collection" id="series">{series}</meta>')
        metadata.append('<meta refines="#series" property="collection-type">series</meta>')
    package = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0">'
        f"<metadata>{''.join(metadata)}</metadata><manifest/><spine/></package>"
    )
    container = (
        '<?xml version="1.0"?><container '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", package)


def test_canonical_author_book(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    book = _book(tmp_path, "Lewis Carroll/Alice's Adventures in Wonderland")
    _epub(book)
    item = scan_library(tmp_path, media=LibraryScanMedia.EBOOK).items[0]
    assert item.structure == "author/book"
    assert item.probable_author == "Lewis Carroll"
    assert item.probable_title == "Alice's Adventures in Wonderland"
    assert item.canonical_looking
    assert not item.needs_identification
    assert item.embedded_metadata == "sparse"


def test_canonical_series_index(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    book = _book(tmp_path, "James S. A. Corey/The Expanse/02 - Caliban's War")
    _epub(book, "Caliban's War", "James S. A. Corey", "The Expanse")
    item = scan_library(tmp_path).items[0]
    assert item.structure == "author/series/book"
    assert item.probable_series == "The Expanse"
    assert item.series_index == "02"
    assert item.probable_title == "Caliban's War"
    assert not item.needs_identification


def test_noncanonical_and_metadata_conflict_are_flagged(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    direct = _book(tmp_path, "Lewis Carroll", "Alice.epub")
    _epub(direct, "Not Alice", "Someone Else")
    item = scan_library(tmp_path).items[0]
    assert not item.canonical_looking
    assert item.needs_identification
    assert any("directly under" in issue for issue in item.issues)


def test_structural_duplicates_are_possible_not_definitive(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Series A/Book", "book.pdf", b"a")
    _book(tmp_path, "Author/Series B/Book", "book.pdf", b"b")
    result = scan_library(tmp_path)
    assert result.possible_duplicate_count == 2
    assert all(i.possible_duplicate for i in result.items)
    assert all(i.needs_identification for i in result.items)


def test_different_formats_in_one_book_are_representations(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "book.pdf", b"a")
    _book(tmp_path, "Author/Book", "book.mobi", b"b")
    item = scan_library(tmp_path).items[0]
    assert item.file_count == 2
    assert item.extensions == (".mobi", ".pdf")
    assert not item.possible_duplicate


def test_same_format_multiple_files_are_flagged(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "one.pdf", b"a")
    _book(tmp_path, "Author/Book", "two.pdf", b"b")
    item = scan_library(tmp_path).items[0]
    assert item.possible_duplicate
    assert item.needs_identification


def test_scan_report_is_outside_library_and_library_bytes_are_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    book = _book(tmp_path, "Author/Book", "book.pdf", b"original")
    before = book.read_bytes()
    result = scan_library(tmp_path)
    assert book.read_bytes() == before
    assert result.report_path.is_relative_to(runtime)
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["safety"]["libraryModified"] is False
    assert payload["safety"]["filesRenamed"] is False
    assert payload["safety"]["filesMoved"] is False
    assert payload["safety"]["metadataModified"] is False


def test_escape_and_missing_root_are_refused(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    (tmp_path / "eBooks").mkdir()
    with pytest.raises(LibraryScanError, match="outside the library root"):
        scan_library(tmp_path, ebooks_dir="../outside")
    (tmp_path / "eBooks").rmdir()
    with pytest.raises(LibraryScanError, match="does not exist"):
        scan_library(tmp_path)
