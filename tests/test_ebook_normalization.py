import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import mnemosyne.ebook_normalization as ebook_normalization
from mnemosyne.ebook_metadata import inspect_ebook_metadata
from mnemosyne.ebook_normalization import (
    EbookNormalizationError,
    apply_ebook_normalization,
    preview_ebook_normalization,
)


def make_epub(
    path: Path,
    *,
    title: str | None = None,
    creator: str | None = None,
    date: str | None = None,
) -> None:
    metadata_parts = ['<dc:identifier>urn:test:1</dc:identifier>']
    if title is not None:
        metadata_parts.append(f"<dc:title>{title}</dc:title>")
    if creator is not None:
        metadata_parts.append(f"<dc:creator>{creator}</dc:creator>")
    if date is not None:
        metadata_parts.append(f"<dc:date>{date}</dc:date>")

    package = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         version="3.0">
  <metadata>
    {''.join(metadata_parts)}
  </metadata>
  <manifest/>
</package>
"""
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", package)


def make_job(
    tmp_path: Path,
    *,
    embedded_title: str | None,
    embedded_creator: str | None,
    verified_title: str = "Verified Title",
    verified_creator: str = "Verified Author",
) -> Path:
    job = tmp_path / "job"
    job.mkdir()
    epub = job / "book.epub"
    make_epub(
        epub,
        title=embedded_title,
        creator=embedded_creator,
        date="2025-01-01",
    )

    sha = hashlib.sha256(epub.read_bytes()).hexdigest()
    report = {
        "schemaVersion": 1,
        "jobId": "ebook-job",
        "status": "staged-verified",
        "stagedFile": epub.name,
        "finalLibraryModified": False,
        "work": {
            "title": verified_title,
            "creator": verified_creator,
            "year": 1901,
        },
        "verification": {
            "actualSize": epub.stat().st_size,
            "sha256": sha,
            "formatVerified": True,
        },
    }
    (job / "ebook-fetch-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )
    return job


def test_missing_title_and_creator_become_additions(tmp_path: Path) -> None:
    job = make_job(
        tmp_path,
        embedded_title=None,
        embedded_creator=None,
    )

    preview = preview_ebook_normalization(job)

    by_field = {action.field: action for action in preview.actions}
    assert by_field["title"].status == "add"
    assert by_field["title"].proposed_value == "Verified Title"
    assert by_field["creator"].status == "add"
    assert by_field["creator"].proposed_value == "Verified Author"
    assert preview.additions == 2
    assert preview.conflicts == 0
    assert preview.blocked is False


def test_matching_metadata_is_preserved(tmp_path: Path) -> None:
    job = make_job(
        tmp_path,
        embedded_title="Verified Title",
        embedded_creator="Verified Author",
    )

    preview = preview_ebook_normalization(job)

    by_field = {action.field: action for action in preview.actions}
    assert by_field["title"].status == "preserve"
    assert by_field["creator"].status == "preserve"
    assert preview.conflicts == 0
    assert preview.blocked is False


def test_conflicting_metadata_blocks_automatic_normalization(tmp_path: Path) -> None:
    job = make_job(
        tmp_path,
        embedded_title="Wrong Title",
        embedded_creator="Verified Author",
    )

    preview = preview_ebook_normalization(job)

    by_field = {action.field: action for action in preview.actions}
    assert by_field["title"].status == "conflict"
    assert by_field["title"].current_value == "Wrong Title"
    assert by_field["title"].proposed_value == "Verified Title"
    assert preview.conflicts == 1
    assert preview.blocked is True


def test_publication_date_is_never_auto_rewritten(tmp_path: Path) -> None:
    job = make_job(
        tmp_path,
        embedded_title=None,
        embedded_creator=None,
    )

    preview = preview_ebook_normalization(job)

    action = next(
        action for action in preview.actions
        if action.field == "publication-date"
    )
    assert action.status == "preserve"
    assert action.current_value == "2025-01-01"
    assert action.proposed_value is None


def test_plain_epub_without_provenance_is_read_only_preserve(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    make_epub(epub, title=None, creator=None)

    preview = preview_ebook_normalization(epub)

    by_field = {action.field: action for action in preview.actions}
    assert by_field["title"].status == "preserve"
    assert by_field["creator"].status == "preserve"
    assert preview.additions == 0
    assert preview.conflicts == 0
    assert preview.blocked is False



def test_apply_adds_verified_metadata_and_updates_hash_provenance(
    tmp_path: Path,
) -> None:
    job = make_job(
        tmp_path,
        embedded_title=None,
        embedded_creator=None,
    )
    epub = job / "book.epub"
    original_sha = hashlib.sha256(epub.read_bytes()).hexdigest()

    result = apply_ebook_normalization(job)

    assert result.original_sha256 == original_sha
    assert result.normalized_sha256 != original_sha

    inspection = inspect_ebook_metadata(epub)
    assert inspection.title == "Verified Title"
    assert inspection.creators == ("Verified Author",)
    assert inspection.dates == ("2025-01-01",)

    with zipfile.ZipFile(epub) as archive:
        infos = archive.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED

    fetch = json.loads(
        (job / "ebook-fetch-report.json").read_text(
            encoding="utf-8"
        )
    )

    assert fetch["verification"]["sha256"] == result.normalized_sha256
    assert fetch["normalization"]["status"] == "normalized-and-verified"
    assert fetch["normalization"]["originalSha256"] == original_sha

    report = json.loads(
        result.normalization_report_path.read_text(
            encoding="utf-8"
        )
    )

    assert report["additions"] == {
        "title": "Verified Title",
        "creator": "Verified Author",
    }
    assert report["safety"]["publicationDateModified"] is False
    assert report["safety"]["seriesModified"] is False


def test_apply_refuses_conflicting_metadata(
    tmp_path: Path,
) -> None:
    job = make_job(
        tmp_path,
        embedded_title="Wrong Title",
        embedded_creator="Verified Author",
    )

    with pytest.raises(
        EbookNormalizationError,
        match="conflicts",
    ):
        apply_ebook_normalization(job)


def test_apply_refuses_if_staged_hash_changed(
    tmp_path: Path,
) -> None:
    job = make_job(
        tmp_path,
        embedded_title=None,
        embedded_creator=None,
    )
    epub = job / "book.epub"

    with epub.open("ab") as stream:
        stream.write(b"changed")

    with pytest.raises(
        EbookNormalizationError,
        match="hash",
    ):
        apply_ebook_normalization(job)


def test_plain_epub_cannot_be_mutated(
    tmp_path: Path,
) -> None:
    epub = tmp_path / "plain.epub"
    make_epub(
        epub,
        title=None,
        creator=None,
    )

    with pytest.raises(
        EbookNormalizationError,
        match="staging jobs",
    ):
        apply_ebook_normalization(epub)


def test_fetch_report_write_failure_rolls_back_epub(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = make_job(
        tmp_path,
        embedded_title=None,
        embedded_creator=None,
    )
    epub = job / "book.epub"
    fetch_path = job / "ebook-fetch-report.json"

    original_epub = epub.read_bytes()
    original_fetch = fetch_path.read_bytes()

    real_writer = ebook_normalization._write_json_atomic
    calls = {"count": 0}

    def fail_second_write(
        path: Path,
        payload: dict,
    ) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated fetch report write failure")
        real_writer(path, payload)

    monkeypatch.setattr(
        ebook_normalization,
        "_write_json_atomic",
        fail_second_write,
    )

    with pytest.raises(
        OSError,
        match="simulated",
    ):
        apply_ebook_normalization(job)

    assert epub.read_bytes() == original_epub
    assert fetch_path.read_bytes() == original_fetch
    assert not (
        job / "ebook-normalization-report.json"
    ).exists()
