import hashlib
import json
from pathlib import Path

import pytest

from mnemosyne.ebook_placement import EbookPlacementError, preview_ebook_placement


def make_job(tmp_path: Path) -> tuple[Path, Path]:
    job = tmp_path / "staging" / "alice-job"
    job.mkdir(parents=True)

    ebook = job / "Alice's Adventures in Wonderland - Lewis Carroll (1865).epub"
    ebook.write_bytes(b"verified epub bytes")
    sha = hashlib.sha256(ebook.read_bytes()).hexdigest()

    report = {
        "schemaVersion": 1,
        "jobId": "alice-job",
        "status": "staged-verified",
        "mediaType": "ebook",
        "work": {
            "title": "Alice's Adventures in Wonderland",
            "creator": "Lewis Carroll",
            "year": 1865,
        },
        "selection": {
            "extension": ".epub",
        },
        "verification": {
            "sha256": sha,
            "formatVerified": True,
        },
        "stagedFile": ebook.name,
        "finalLibraryModified": False,
    }
    (job / "ebook-fetch-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )
    return job, ebook


def test_preview_computes_canonical_destination_and_filename(tmp_path: Path) -> None:
    job, ebook = make_job(tmp_path)

    preview = preview_ebook_placement(
        job,
        tmp_path / "library",
    )

    assert preview.source_path == ebook
    assert preview.destination_dir == (
        tmp_path
        / "library"
        / "eBooks"
        / "Lewis Carroll"
        / "Alice's Adventures in Wonderland"
    ).resolve()
    assert preview.destination_path.name == "Alice's Adventures in Wonderland.epub"
    assert preview.actual_sha256 == preview.expected_sha256
    assert preview.conflict is False


def test_preview_rejects_staged_hash_mismatch(tmp_path: Path) -> None:
    job, ebook = make_job(tmp_path)
    ebook.write_bytes(b"tampered")

    with pytest.raises(EbookPlacementError, match="SHA-256 mismatch"):
        preview_ebook_placement(job, tmp_path / "library")


def test_preview_rejects_existing_destination_directory(tmp_path: Path) -> None:
    job, _ = make_job(tmp_path)
    destination = (
        tmp_path
        / "library"
        / "eBooks"
        / "Lewis Carroll"
        / "Alice's Adventures in Wonderland"
    )
    destination.mkdir(parents=True)

    with pytest.raises(EbookPlacementError, match="already exists"):
        preview_ebook_placement(job, tmp_path / "library")


def test_preview_rejects_missing_required_metadata(tmp_path: Path) -> None:
    job, _ = make_job(tmp_path)
    report_path = job / "ebook-fetch-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["work"]["creator"] = ""
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(EbookPlacementError, match="author/creator"):
        preview_ebook_placement(job, tmp_path / "library")
