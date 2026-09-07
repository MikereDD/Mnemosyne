import json
import os
import zipfile
from pathlib import Path

import pytest

from mnemosyne.library_identify import identify_library
from mnemosyne.library_plan import LibraryPlanError, plan_library
from mnemosyne.library_scan import scan_library


def _runtime(tmp_path: Path, monkeypatch) -> Path:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr("mnemosyne.library_scan.runtime_root", lambda: runtime)
    monkeypatch.setattr("mnemosyne.library_identify.runtime_root", lambda: runtime)
    monkeypatch.setattr("mnemosyne.library_plan.runtime_root", lambda: runtime)
    return runtime


def _epub(path: Path, *, title=None, creator=None) -> None:
    metadata = []
    if title:
        metadata.append(f"<dc:title>{title}</dc:title>")
    if creator:
        metadata.append(f"<dc:creator>{creator}</dc:creator>")
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


def _book(
    root: Path,
    relative: str,
    filename: str,
    *,
    epub=False,
    data=b"book",
    title=None,
    creator=None,
) -> Path:
    folder = root / "eBooks"
    if relative:
        folder /= Path(relative)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / filename
    if epub:
        _epub(path, title=title, creator=creator)
    else:
        path.write_bytes(data)
    return path


def _identify(root: Path) -> Path:
    scan = scan_library(root).report_path
    return identify_library(root, scan_report=scan).report_path


def test_canonical_book_produces_keep_plan_with_hash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    book = _book(
        tmp_path,
        "Lewis Carroll/Alice's Adventures in Wonderland",
        "Alice's Adventures in Wonderland.epub",
        epub=True,
    )
    identification = _identify(tmp_path)

    result = plan_library(tmp_path, identification_report=identification)
    item = result.items[0]

    assert item.status == "KEEP"
    assert item.directory_action == "KEEP"
    assert item.blocked_reasons == ()
    assert len(item.representations) == 1
    representation = item.representations[0]
    assert representation.action == "KEEP"
    assert representation.sha256
    assert representation.size_bytes == book.stat().st_size
    assert result.report_path.is_relative_to(runtime)


def test_high_confidence_noncanonical_item_plans_move_and_filename_normalization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(
        tmp_path,
        "Blake Crouch",
        "Dark Matter - Blake Crouch.epub",
        epub=True,
        title="Dark Matter",
        creator="Blake Crouch",
    )
    identification = _identify(tmp_path)

    result = plan_library(tmp_path, identification_report=identification)
    item = result.items[0]

    assert item.status == "READY"
    assert item.target_path == str(Path("Blake Crouch") / "Dark Matter")
    assert item.directory_action == "MOVE"
    assert item.representations[0].destination == str(
        Path("Blake Crouch") / "Dark Matter" / "Dark Matter.epub"
    )
    assert item.representations[0].action == "MOVE"


def test_medium_identity_is_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Blake Crouch", "Dark Matter.pdf")
    identification = _identify(tmp_path)

    item = plan_library(
        tmp_path,
        identification_report=identification,
    ).items[0]

    assert item.status == "BLOCKED"
    assert any("MEDIUM identities require review" in reason for reason in item.blocked_reasons)


def test_existing_target_directory_blocks_move(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(
        tmp_path,
        "Blake Crouch",
        "Dark Matter - Blake Crouch.epub",
        epub=True,
        title="Dark Matter",
        creator="Blake Crouch",
    )
    identification = _identify(tmp_path)
    (tmp_path / "eBooks" / "Blake Crouch" / "Dark Matter").mkdir()

    item = plan_library(
        tmp_path,
        identification_report=identification,
    ).items[0]

    assert item.status == "BLOCKED"
    assert item.destination_exists
    assert any("target directory already exists" in reason for reason in item.blocked_reasons)


def test_different_formats_plan_as_representations_without_overwrite(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(
        tmp_path,
        "Author/Book",
        "Book.epub",
        epub=True,
        title="Book",
        creator="Author",
    )
    _book(tmp_path, "Author/Book", "Book.pdf")
    identification = _identify(tmp_path)

    item = plan_library(
        tmp_path,
        identification_report=identification,
    ).items[0]

    assert item.status == "KEEP"
    assert {rep.extension for rep in item.representations} == {".epub", ".pdf"}
    assert all(rep.action == "KEEP" for rep in item.representations)


def test_same_format_multiple_representations_are_blocked(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "one.pdf")
    _book(tmp_path, "Author/Book", "two.pdf")
    identification = _identify(tmp_path)

    item = plan_library(
        tmp_path,
        identification_report=identification,
    ).items[0]

    assert item.status == "BLOCKED"
    assert any("same format" in reason for reason in item.blocked_reasons)


def test_changed_identification_evidence_requires_reidentify(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    book = _book(
        tmp_path,
        "Author/Book",
        "Book.epub",
        epub=True,
        title="Book",
        creator="Author",
    )
    identification = _identify(tmp_path)

    # Keep the same byte size impossible to guarantee with ZIP rewrite, so this
    # exercises the already-required stale-scan guard before planning.
    book.write_bytes(b"changed")

    with pytest.raises(LibraryPlanError, match="bytes changed"):
        plan_library(tmp_path, identification_report=identification)


def test_added_file_requires_rescan_before_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "Book.pdf")
    identification = _identify(tmp_path)
    _book(tmp_path, "Other/Other", "Other.pdf")

    with pytest.raises(LibraryPlanError, match="file set changed"):
        plan_library(tmp_path, identification_report=identification)


def test_identification_report_outside_state_is_refused(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "Book.pdf")
    identification = _identify(tmp_path)
    outside = tmp_path / "identification.json"
    outside.write_bytes(identification.read_bytes())

    with pytest.raises(LibraryPlanError, match="direct identification reports"):
        plan_library(tmp_path, identification_report=outside)


def test_latest_identification_is_used_when_omitted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _runtime(tmp_path, monkeypatch)
    _book(tmp_path, "Author/Book", "Book.pdf")
    first = _identify(tmp_path)
    second = _identify(tmp_path)
    stamp = second.stat().st_mtime_ns
    os.utime(first, ns=(stamp - 10_000, stamp - 10_000))
    os.utime(second, ns=(stamp + 10_000, stamp + 10_000))

    result = plan_library(tmp_path)

    assert result.source_identification_path == second.resolve()


def test_plan_report_is_read_only_for_library(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = _runtime(tmp_path, monkeypatch)
    book = _book(tmp_path, "Author/Book", "Book.pdf", data=b"original")
    before = book.read_bytes()
    identification = _identify(tmp_path)

    result = plan_library(tmp_path, identification_report=identification)

    assert book.read_bytes() == before
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.report_path.is_relative_to(runtime)
    assert payload["safety"]["directoriesCreated"] is False
    assert payload["safety"]["filesRenamed"] is False
    assert payload["safety"]["filesMoved"] is False
    assert payload["safety"]["filesOverwritten"] is False
    assert payload["safety"]["metadataModified"] is False
    assert payload["safety"]["libraryModified"] is False
    assert payload["safety"]["sourceHashesRecorded"] is True
