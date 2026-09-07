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



def _make_series_job(
    tmp_path: Path,
    *,
    embedded_series: str | None = None,
    embedded_series_index: str | None = None,
    verified_series: str = "Verified Series",
    verified_series_index: float = 2,
    epub_version: str = "3.0",
) -> Path:
    job = tmp_path / "series-job"
    job.mkdir()
    epub = job / "series.epub"

    series_markup = ""
    if embedded_series is not None:
        if epub_version.startswith("3"):
            series_markup = (
                "<meta id='series' property='belongs-to-collection'>"
                f"{embedded_series}</meta>"
                "<meta refines='#series' property='collection-type'>series</meta>"
            )
            if embedded_series_index is not None:
                series_markup += (
                    "<meta refines='#series' property='group-position'>"
                    f"{embedded_series_index}</meta>"
                )
        else:
            series_markup = (
                "<meta name='calibre:series' "
                f"content='{embedded_series}'/>"
            )
            if embedded_series_index is not None:
                series_markup += (
                    "<meta name='calibre:series_index' "
                    f"content='{embedded_series_index}'/>"
                )

    package = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf"
         xmlns:dc="http://purl.org/dc/elements/1.1/"
         version="{epub_version}">
  <metadata>
    <dc:title>Verified Title</dc:title>
    <dc:creator>Verified Author</dc:creator>
    <dc:identifier>urn:test:series</dc:identifier>
    {series_markup}
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

    with zipfile.ZipFile(epub, "w") as archive:
        mimetype = zipfile.ZipInfo("mimetype")
        mimetype.compress_type = zipfile.ZIP_STORED
        archive.writestr(mimetype, "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("content.opf", package)

    sha = hashlib.sha256(epub.read_bytes()).hexdigest()
    report = {
        "schemaVersion": 2,
        "jobId": "series-job",
        "status": "staged-verified",
        "stagedFile": epub.name,
        "finalLibraryModified": False,
        "work": {
            "title": "Verified Title",
            "creator": "Verified Author",
            "year": 2024,
            "series": verified_series,
            "seriesIndex": verified_series_index,
            "seriesProvenance": "verified-override",
            "seriesIndexProvenance": "verified-override",
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


def test_missing_verified_series_becomes_additions(
    tmp_path: Path,
) -> None:
    job = _make_series_job(tmp_path)

    preview = preview_ebook_normalization(job)
    by_field = {action.field: action for action in preview.actions}

    assert by_field["series"].status == "add"
    assert by_field["series"].proposed_value == "Verified Series"
    assert by_field["series-index"].status == "add"
    assert by_field["series-index"].proposed_value == "2"
    assert preview.conflicts == 0
    assert preview.blocked is False


def test_matching_verified_series_is_preserved(
    tmp_path: Path,
) -> None:
    job = _make_series_job(
        tmp_path,
        embedded_series="Verified Series",
        embedded_series_index="2.0",
    )

    preview = preview_ebook_normalization(job)
    by_field = {action.field: action for action in preview.actions}

    assert by_field["series"].status == "preserve"
    assert by_field["series-index"].status == "preserve"
    assert preview.conflicts == 0


def test_conflicting_series_blocks_normalization(
    tmp_path: Path,
) -> None:
    job = _make_series_job(
        tmp_path,
        embedded_series="Wrong Series",
        embedded_series_index="2",
    )

    preview = preview_ebook_normalization(job)
    by_field = {action.field: action for action in preview.actions}

    assert by_field["series"].status == "conflict"
    assert preview.blocked is True

    with pytest.raises(EbookNormalizationError, match="conflicts"):
        apply_ebook_normalization(job)


def test_apply_adds_epub3_series_metadata_transactionally(
    tmp_path: Path,
) -> None:
    job = _make_series_job(tmp_path)
    epub = job / "series.epub"
    original_sha = hashlib.sha256(epub.read_bytes()).hexdigest()

    result = apply_ebook_normalization(job)

    assert result.normalized_sha256 != original_sha
    assert "series" in result.additions
    assert "series-index" in result.additions

    inspection = inspect_ebook_metadata(epub)
    assert inspection.series_name == "Verified Series"
    assert inspection.series_index == "2"

    with zipfile.ZipFile(epub) as archive:
        package = archive.read("content.opf").decode("utf-8")
        assert 'property="belongs-to-collection"' in package
        assert 'property="collection-type"' in package
        assert 'property="group-position"' in package
        assert "calibre:series" not in package

    report = json.loads(
        result.normalization_report_path.read_text(encoding="utf-8")
    )
    assert report["safety"]["seriesModified"] is True

    second_preview = preview_ebook_normalization(job)
    assert second_preview.additions == 0
    assert second_preview.conflicts == 0


def test_apply_uses_calibre_series_metadata_for_epub2(
    tmp_path: Path,
) -> None:
    job = _make_series_job(
        tmp_path,
        epub_version="2.0",
    )
    epub = job / "series.epub"

    apply_ebook_normalization(job)

    inspection = inspect_ebook_metadata(epub)
    assert inspection.series_name == "Verified Series"
    assert inspection.series_index == "2"

    with zipfile.ZipFile(epub) as archive:
        package = archive.read("content.opf").decode("utf-8")
        assert 'name="calibre:series"' in package
        assert 'name="calibre:series_index"' in package


def test_adds_missing_index_to_existing_matching_epub3_series(
    tmp_path: Path,
) -> None:
    job = _make_series_job(
        tmp_path,
        embedded_series="Verified Series",
        embedded_series_index=None,
    )

    preview = preview_ebook_normalization(job)
    by_field = {action.field: action for action in preview.actions}
    assert by_field["series"].status == "preserve"
    assert by_field["series-index"].status == "add"

    apply_ebook_normalization(job)

    inspection = inspect_ebook_metadata(job)
    assert inspection.series_name == "Verified Series"
    assert inspection.series_index == "2"


@pytest.mark.parametrize("index", ["NaN", "Infinity", "-1", "invalid"])
def test_invalid_verified_series_index_blocks(tmp_path: Path, index: str) -> None:
    job = _make_series_job(tmp_path, verified_series_index=index)
    before = {p.name: p.read_bytes() for p in job.iterdir()}
    with pytest.raises(EbookNormalizationError, match="finite non-negative"):
        apply_ebook_normalization(job)
    assert {p.name: p.read_bytes() for p in job.iterdir()} == before


def test_verified_index_without_verified_series_blocks(tmp_path: Path) -> None:
    job = _make_series_job(tmp_path)
    report_path = job / "ebook-fetch-report.json"
    report = json.loads(report_path.read_text())
    report["work"]["seriesProvenance"] = "provider"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(EbookNormalizationError, match="requires a verified series"):
        preview_ebook_normalization(job)


def test_conflicting_series_index_blocks(tmp_path: Path) -> None:
    job = _make_series_job(tmp_path, embedded_series="Verified Series", embedded_series_index="3")
    assert preview_ebook_normalization(job).blocked
    with pytest.raises(EbookNormalizationError, match="conflicts"):
        apply_ebook_normalization(job)


def test_fractional_series_index_preserves_decimal_precision(tmp_path: Path) -> None:
    value = "2.123456789012345678901234567890123456789"
    job = _make_series_job(tmp_path, verified_series_index=value)
    apply_ebook_normalization(job)
    assert inspect_ebook_metadata(job).series_index == value
    assert preview_ebook_normalization(job).additions == 0


def test_series_verification_failure_after_replace_rolls_back(tmp_path: Path, monkeypatch) -> None:
    job = _make_series_job(tmp_path)
    before = {p.name: p.read_bytes() for p in job.iterdir()}
    original_verify = ebook_normalization._verify_normalized_epub
    calls = 0

    def fail_after_replace(*args, **kwargs):
        nonlocal calls
        calls += 1
        original_verify(*args, **kwargs)
        if calls == 2:
            raise EbookNormalizationError("simulated series verification failure")

    monkeypatch.setattr(ebook_normalization, "_verify_normalized_epub", fail_after_replace)
    with pytest.raises(EbookNormalizationError, match="simulated"):
        apply_ebook_normalization(job)
    assert calls == 2
    assert {p.name: p.read_bytes() for p in job.iterdir()} == before


def test_series_id_avoids_non_meta_id_collision() -> None:
    from xml.etree import ElementTree as ET
    package = b'<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata/><manifest><item id="mnemosyne-series"/></manifest></package>'
    root = ET.fromstring(ebook_normalization._rewrite_package(package, {"series": "Example"}))
    ids = [e.attrib["id"] for e in root.iter() if "id" in e.attrib]
    assert len(ids) == len(set(ids))


def test_index_verification_rejects_float_rounding_match(tmp_path: Path) -> None:
    job = _make_series_job(tmp_path, embedded_series="Verified Series", embedded_series_index="9007199254740992")
    with pytest.raises(EbookNormalizationError, match="index did not verify"):
        ebook_normalization._verify_normalized_epub(
            job / "series.epub", expected_title=None, expected_creator=None,
            expected_series="Verified Series", expected_series_index="9007199254740993",
        )
