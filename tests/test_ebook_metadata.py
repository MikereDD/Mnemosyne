import json
import zipfile
from pathlib import Path

import pytest

from mnemosyne.ebook_metadata import EbookMetadataError, inspect_ebook_metadata


def make_epub(
    path: Path,
    *,
    title: str = "Example Book",
    creator: str = "Example Author",
    date: str = "2024-01-02",
    series: bool = True,
) -> None:
    package = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         version="3.0">
  <metadata>
    <dc:title>{title}</dc:title>
    <dc:creator>{creator}</dc:creator>
    <dc:language>en</dc:language>
    <dc:identifier>urn:isbn:9780000000001</dc:identifier>
    <dc:publisher>Example Press</dc:publisher>
    <dc:date>{date}</dc:date>
    <dc:subject>Fiction</dc:subject>
    {"<meta id='series' property='belongs-to-collection'>Example Series</meta><meta refines='#series' property='collection-type'>series</meta><meta refines='#series' property='group-position'>2</meta>" if series else ""}
  </metadata>
  <manifest>
    <item id="cover" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
  </manifest>
</package>
"""
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)


def test_inspects_epub_package_metadata(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    make_epub(epub)

    result = inspect_ebook_metadata(epub)

    assert result.source_kind == "file"
    assert result.package_path == "OEBPS/content.opf"
    assert result.epub_version == "3.0"
    assert result.title == "Example Book"
    assert result.creators == ("Example Author",)
    assert result.language == "en"
    assert result.publisher == "Example Press"
    assert result.series_name == "Example Series"
    assert result.series_index == "2"
    assert result.cover_reference == "images/cover.jpg"


def test_staging_job_compares_embedded_metadata_to_provenance(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    epub = job / "staged.epub"
    make_epub(epub)

    report = {
        "stagedFile": epub.name,
        "work": {
            "title": "Example Book",
            "creator": "Example Author",
            "year": 1901,
        },
    }
    (job / "ebook-fetch-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )

    result = inspect_ebook_metadata(job)

    statuses = {item.field: item.status for item in result.comparisons}
    assert statuses["title"] == "match"
    assert statuses["creator"] == "match"
    assert statuses["date"] == "evidence-only"
    assert result.provenance_year == 1901


def test_reports_title_conflict_without_mutating(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()
    epub = job / "staged.epub"
    make_epub(epub, title="Wrong Title")

    (job / "ebook-fetch-report.json").write_text(
        json.dumps(
            {
                "stagedFile": epub.name,
                "work": {
                    "title": "Verified Title",
                    "creator": "Example Author",
                    "year": 1901,
                },
            }
        ),
        encoding="utf-8",
    )

    result = inspect_ebook_metadata(job)
    comparison = next(item for item in result.comparisons if item.field == "title")

    assert comparison.status == "conflict"
    assert epub.is_file()


def test_supports_calibre_series_metadata(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    package = """<package xmlns="http://www.idpf.org/2007/opf"
        xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
      <metadata>
        <dc:title>Book</dc:title>
        <dc:creator>Author</dc:creator>
        <meta name="calibre:series" content="Legacy Series"/>
        <meta name="calibre:series_index" content="3"/>
      </metadata>
      <manifest/>
    </package>"""
    container = """<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="content.opf"/></rootfiles>
    </container>"""
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", package)

    result = inspect_ebook_metadata(epub)

    assert result.series_name == "Legacy Series"
    assert result.series_index == "3"


def test_rejects_non_epub_file(tmp_path: Path) -> None:
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7")

    with pytest.raises(EbookMetadataError, match="EPUB"):
        inspect_ebook_metadata(path)


def test_rejects_missing_package_document(tmp_path: Path) -> None:
    epub = tmp_path / "broken.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            "<container><rootfiles/></container>",
        )

    with pytest.raises(EbookMetadataError, match="package document"):
        inspect_ebook_metadata(epub)
