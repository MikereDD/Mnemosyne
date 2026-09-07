import hashlib
import json
from pathlib import Path

import pytest

from mnemosyne.ebook_placement import preview_ebook_placement
from mnemosyne.models import ArchiveItem, MediaType
from mnemosyne.paths import canonical_destination
from mnemosyne.planner import build_plan


def test_ebook_without_series_keeps_existing_layout() -> None:
    root = Path("library")
    destination = canonical_destination(
        root,
        MediaType.EBOOK,
        "Lewis Carroll",
        "Alice's Adventures in Wonderland",
        1865,
    )
    assert destination == (
        root / "eBooks" / "Lewis Carroll"
        / "Alice's Adventures in Wonderland"
    )


def test_verified_series_adds_series_directory() -> None:
    destination = canonical_destination(
        Path("library"),
        MediaType.EBOOK,
        "Example Author",
        "Example Book",
        2024,
        series="Example Series",
    )
    assert destination == (
        Path("library") / "eBooks" / "Example Author"
        / "Example Series" / "Example Book"
    )


def test_verified_series_index_orders_book_directory() -> None:
    destination = canonical_destination(
        Path("library"),
        MediaType.EBOOK,
        "James S. A. Corey",
        "Caliban's War",
        2012,
        series="The Expanse",
        series_index=2,
    )
    assert destination == (
        Path("library") / "eBooks" / "James S. A. Corey"
        / "The Expanse" / "02 - Caliban's War"
    )


def test_fractional_series_index_is_preserved() -> None:
    destination = canonical_destination(
        Path("library"),
        MediaType.EBOOK,
        "Example Author",
        "Interlude",
        2024,
        series="Example Series",
        series_index=2.5,
    )
    assert destination.name == "02.5 - Interlude"


def test_series_index_without_series_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires"):
        canonical_destination(
            Path("library"),
            MediaType.EBOOK,
            "Example Author",
            "Example Book",
            2024,
            series_index=2,
        )


def _item(
    *,
    series: str | None,
    series_index: float | None,
    series_provenance: str | None,
    series_index_provenance: str | None,
) -> ArchiveItem:
    return ArchiveItem(
        identifier="example",
        source_url="https://archive.org/details/example",
        media_type=MediaType.EBOOK,
        raw_title="Example Book",
        title="Example Book",
        creator="Example Author",
        year=2024,
        series=series,
        series_index=series_index,
        series_provenance=series_provenance,
        series_index_provenance=series_index_provenance,
        candidates=[],
    )


def test_planner_uses_only_verified_series_provenance() -> None:
    item = _item(
        series="Example Series",
        series_index=2,
        series_provenance="verified-override",
        series_index_provenance="verified-override",
    )
    plan = build_plan(item, Path("library"))
    assert plan.destination == (
        Path("library") / "eBooks" / "Example Author"
        / "Example Series" / "02 - Example Book"
    )


def test_planner_does_not_use_unverified_series_as_structure() -> None:
    item = _item(
        series="Provider Guess",
        series_index=2,
        series_provenance="provider",
        series_index_provenance="provider",
    )
    plan = build_plan(item, Path("library"))
    assert plan.destination == (
        Path("library") / "eBooks"
        / "Example Author" / "Example Book"
    )
    assert any("not verified" in warning for warning in plan.warnings)


def test_placement_reconstructs_verified_series_destination(
    tmp_path: Path,
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    epub = job / "book.epub"
    epub.write_bytes(b"verified-test-bytes")
    sha = hashlib.sha256(epub.read_bytes()).hexdigest()

    report = {
        "schemaVersion": 2,
        "jobId": "job",
        "status": "staged-verified",
        "mediaType": "ebook",
        "finalLibraryModified": False,
        "work": {
            "title": "Caliban's War",
            "creator": "James S. A. Corey",
            "year": 2012,
            "series": "The Expanse",
            "seriesIndex": 2,
            "seriesProvenance": "verified-override",
            "seriesIndexProvenance": "verified-override",
        },
        "selection": {"extension": ".epub"},
        "verification": {
            "sha256": sha,
            "formatVerified": True,
        },
        "stagedFile": epub.name,
    }
    (job / "ebook-fetch-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )

    preview = preview_ebook_placement(
        job,
        tmp_path / "library",
    )

    assert preview.destination_dir == (
        tmp_path / "library" / "eBooks"
        / "James S. A. Corey" / "The Expanse"
        / "02 - Caliban's War"
    ).resolve()
    assert preview.destination_path.name == "Caliban's War.epub"
