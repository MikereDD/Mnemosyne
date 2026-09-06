import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import mnemosyne.ebook_fetcher as ebook_fetcher
from mnemosyne.ebook_fetcher import fetch_ebook_plan_to_staging, validate_ebook_format
from mnemosyne.fetcher import FetchError
from mnemosyne.models import AcquisitionPlan, ArchiveItem, CandidateKind, EbookEdition, MediaCandidate, MediaType


def candidate(extension: str) -> MediaCandidate:
    return MediaCandidate(
        name=f"source{extension}",
        url=f"https://example.invalid/source{extension}",
        extension=extension,
        archive_format=extension.lstrip(".").upper(),
        source="original",
        size=None,
        kind=CandidateKind.EBOOK,
        score=900,
        reasons=["test"],
    )


def write_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("OEBPS/content.opf", "<package/>")


@pytest.mark.parametrize(
    ("extension", "payload", "label"),
    [
        (".pdf", b"%PDF-1.7\nexample", "PDF"),
        (".mobi", b"x" * 60 + b"BOOKMOBI" + b"x" * 16, "MOBI/PalmDB"),
        (".azw", b"x" * 60 + b"BOOKMOBI" + b"x" * 16, "AZW/PalmDB"),
        (".azw3", b"x" * 60 + b"BOOKMOBI" + b"x" * 16, "AZW3/KF8-PalmDB"),
        (".djvu", b"AT&TFORM" + b"\x00\x00\x00\x08" + b"DJVU" + b"x", "DjVu"),
    ],
)
def test_signature_validation_for_non_epub_formats(tmp_path, extension, payload, label):
    path = tmp_path / f"book{extension}"
    path.write_bytes(payload)
    assert validate_ebook_format(path, candidate(extension)) == label


def test_epub_requires_valid_container_structure(tmp_path):
    path = tmp_path / "book.epub"
    write_epub(path)
    assert validate_ebook_format(path, candidate(".epub")) == "EPUB/ZIP"


def test_html_masquerading_as_book_is_rejected(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"<html><body>error</body></html>")
    with pytest.raises(FetchError, match="HTML/XML"):
        validate_ebook_format(path, candidate(".pdf"))


def test_invalid_epub_is_rejected(tmp_path):
    path = tmp_path / "book.epub"
    path.write_bytes(b"not a zip")
    with pytest.raises(FetchError, match="ZIP container"):
        validate_ebook_format(path, candidate(".epub"))


def test_fetch_stages_verified_ebook_and_writes_report(tmp_path, monkeypatch):
    selected = candidate(".epub")
    item = ArchiveItem(
        identifier="example-book",
        source_url="https://archive.org/details/example-book",
        media_type=MediaType.EBOOK,
        raw_title="Example Book",
        title="Example Book",
        creator="Example Author",
        year=2024,
        candidates=[selected],
    )
    edition = EbookEdition(
        key="ebook:source.epub",
        label="EPUB",
        extension=".epub",
        archive_format="EPUB",
        source="original",
        candidate=selected,
        score=900,
    )
    plan = AcquisitionPlan(
        item=item,
        destination=tmp_path / "library",
        selected_ebook=selected,
        ebook_editions=[edition],
        selected_ebook_edition_key=edition.key,
    )

    def fake_stream_download(url, destination, *, expected_size, timeout, user_agent):
        part = destination.with_name(destination.name + ".part")
        write_epub(part)
        data = part.read_bytes()
        return len(data), hashlib.sha256(data).hexdigest()

    monkeypatch.setattr(ebook_fetcher, "_stream_download", fake_stream_download)

    result = fetch_ebook_plan_to_staging(plan, tmp_path / "staging")

    assert result.ebook.path.exists()
    assert result.ebook.signature == "EPUB/ZIP"
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["status"] == "staged-verified"
    assert report["verification"]["formatVerified"] is True
    assert report["verification"]["sha256"] == result.ebook.sha256
    assert report["finalLibraryModified"] is False
    assert not list(result.staging_dir.glob("*.part"))


def test_failed_verification_cleans_partial_job(tmp_path, monkeypatch):
    selected = candidate(".pdf")
    item = ArchiveItem(
        identifier="broken-book",
        source_url="https://archive.org/details/broken-book",
        media_type=MediaType.EBOOK,
        raw_title="Broken Book",
        title="Broken Book",
        creator="Example Author",
        year=2024,
        candidates=[selected],
    )
    plan = AcquisitionPlan(
        item=item,
        destination=tmp_path / "library",
        selected_ebook=selected,
    )

    def fake_stream_download(url, destination, *, expected_size, timeout, user_agent):
        part = destination.with_name(destination.name + ".part")
        part.write_bytes(b"<html>not a pdf</html>")
        data = part.read_bytes()
        return len(data), hashlib.sha256(data).hexdigest()

    monkeypatch.setattr(ebook_fetcher, "_stream_download", fake_stream_download)

    with pytest.raises(FetchError):
        fetch_ebook_plan_to_staging(plan, tmp_path / "staging")

    staging = tmp_path / "staging"
    assert not staging.exists() or not any(staging.iterdir())
