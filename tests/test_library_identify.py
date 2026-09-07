import json
import zipfile
from pathlib import Path

import pytest

from mnemosyne.library_identify import (
    LibraryConfidence,
    LibraryIdentifyError,
    identify_library,
)
from mnemosyne.library_scan import LibraryScanMedia, scan_library


def _runtime(tmp_path: Path, monkeypatch) -> Path:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr("mnemosyne.library_scan.runtime_root", lambda: runtime)
    monkeypatch.setattr("mnemosyne.library_identify.runtime_root", lambda: runtime)
    return runtime


def _epub(path: Path, *, title=None, creator=None, series=None, index=None) -> None:
    metadata = []
    if title:
        metadata.append(f"<dc:title>{title}</dc:title>")
    if creator:
        metadata.append(f"<dc:creator>{creator}</dc:creator>")
    if series:
        metadata.append(f'<meta property="belongs-to-collection" id="series">{series}</meta>')
        metadata.append('<meta refines="#series" property="collection-type">series</meta>')
        if index is not None:
            metadata.append(
                f'<meta refines="#series" property="group-position">{index}</meta>'
            )
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


def _book(root: Path, relative: str, name: str, *, epub=False, **metadata) -> Path:
    folder = root / "eBooks"
    if relative:
        folder /= Path(relative)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if epub:
        _epub(path, **metadata)
    else:
        path.write_bytes(b"book")
    return path


def _scan(root: Path) -> Path:
    return scan_library(root, media=LibraryScanMedia.EBOOK).report_path


def test_canonical_sparse_structure_is_high_confidence_keep(
    tmp_path: Path, monkeypatch
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(
        tmp_path,
        "Lewis Carroll/Alice's Adventures in Wonderland",
        "Alice's Adventures in Wonderland.epub",
        epub=True,
    )
    scan = _scan(tmp_path)

    result = identify_library(tmp_path, scan_report=scan)
    item = result.items[0]

    assert item.author == "Lewis Carroll"
    assert item.title == "Alice's Adventures in Wonderland"
    assert item.confidence is LibraryConfidence.HIGH
    assert item.action == "KEEP"
    assert item.external_lookup == "not-required"
    assert item.review_required is False


def test_matching_embedded_series_identity_is_high_and_kept(
    tmp_path: Path, monkeypatch
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(
        tmp_path,
        "James S. A. Corey/The Expanse/02 - Caliban's War",
        "Caliban's War - James S. A. Corey.epub",
        epub=True,
        title="Caliban's War",
        creator="James S. A. Corey",
        series="The Expanse",
        index="2",
    )
    scan = _scan(tmp_path)

    item = identify_library(tmp_path, scan_report=scan).items[0]

    assert item.confidence is LibraryConfidence.HIGH
    assert item.series == "The Expanse"
    assert item.series_index == "2"
    assert item.action == "KEEP"


def test_embedded_structure_conflict_requires_review(
    tmp_path: Path, monkeypatch
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(
        tmp_path,
        "Lewis Carroll/Alice",
        "Alice.epub",
        epub=True,
        title="Not Alice",
        creator="Someone Else",
    )
    scan = _scan(tmp_path)

    item = identify_library(tmp_path, scan_report=scan).items[0]

    assert item.confidence is LibraryConfidence.CONFLICT
    assert item.action == "REVIEW"
    assert item.external_lookup == "required"
    assert item.review_required
    assert any("evidence conflicts" in reason for reason in item.reasons)


def test_filename_only_root_item_is_low_confidence_would_move(
    tmp_path: Path, monkeypatch
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "", "Dark Matter - Blake Crouch.pdf")
    scan = _scan(tmp_path)

    item = identify_library(tmp_path, scan_report=scan).items[0]

    assert item.author == "Blake Crouch"
    assert item.title == "Dark Matter"
    assert item.confidence is LibraryConfidence.LOW
    assert item.action == "WOULD-MOVE"
    assert item.proposed_path == str(Path("Blake Crouch") / "Dark Matter")
    assert item.review_required


def test_direct_author_plus_filename_is_medium_and_reviewed(
    tmp_path: Path, monkeypatch
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Blake Crouch", "Dark Matter.pdf")
    scan = _scan(tmp_path)

    item = identify_library(tmp_path, scan_report=scan).items[0]

    assert item.author == "Blake Crouch"
    assert item.title == "Dark Matter"
    assert item.confidence is LibraryConfidence.MEDIUM
    assert item.action == "WOULD-MOVE"
    assert item.external_lookup == "recommended"
    assert item.review_required


def test_possible_duplicate_blocks_automatic_acceptance(
    tmp_path: Path, monkeypatch
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Series A/Book", "Book.pdf")
    _book(tmp_path, "Author/Series B/Book", "Book.mobi")
    scan = _scan(tmp_path)

    result = identify_library(tmp_path, scan_report=scan)

    assert len(result.items) == 2
    assert all(item.confidence is LibraryConfidence.CONFLICT for item in result.items)
    assert all(item.action == "REVIEW" for item in result.items)


def test_changed_media_bytes_make_scan_stale(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    book = _book(tmp_path, "Author/Book", "Book.pdf")
    scan = _scan(tmp_path)
    book.write_bytes(b"changed-after-scan")

    with pytest.raises(LibraryIdentifyError, match="bytes changed"):
        identify_library(tmp_path, scan_report=scan)


def test_added_media_file_makes_scan_stale(tmp_path: Path, monkeypatch) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "Book.pdf")
    scan = _scan(tmp_path)
    _book(tmp_path, "Other/New", "New.pdf")

    with pytest.raises(LibraryIdentifyError, match="file set changed"):
        identify_library(tmp_path, scan_report=scan)


def test_latest_scan_is_used_when_report_is_omitted(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "Book.pdf")
    first = _scan(tmp_path)
    second = _scan(tmp_path)
    first.touch()
    second.touch()
    second_stat = second.stat().st_mtime_ns
    first.touch()
    # Force deterministic ordering: make second newest.
    import os
    os.utime(first, ns=(second_stat - 10_000, second_stat - 10_000))
    os.utime(second, ns=(second_stat + 10_000, second_stat + 10_000))

    result = identify_library(tmp_path)

    assert result.source_scan_path == second.resolve()
    assert result.report_path.is_relative_to(runtime)


def test_identification_report_is_read_only_for_library(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    book = _book(tmp_path, "Author/Book", "Book.pdf")
    before = book.read_bytes()
    scan = _scan(tmp_path)

    result = identify_library(tmp_path, scan_report=scan)

    assert book.read_bytes() == before
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.report_path.is_relative_to(runtime)
    assert payload["safety"]["filesRenamed"] is False
    assert payload["safety"]["filesMoved"] is False
    assert payload["safety"]["metadataModified"] is False
    assert payload["safety"]["libraryModified"] is False
    assert payload["safety"]["externalLookupPerformed"] is False
    assert payload["confidencePolicy"]["automaticPlanningEligible"] == [
        "VERIFIED",
        "HIGH",
    ]


def test_scan_report_outside_state_scans_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "Book.pdf")
    scan = _scan(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(scan.read_bytes())

    with pytest.raises(LibraryIdentifyError, match="direct scan reports"):
        identify_library(tmp_path, scan_report=outside)
