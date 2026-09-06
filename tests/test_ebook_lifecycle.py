import hashlib
import json
from pathlib import Path

import pytest

import mnemosyne.ebook_lifecycle as ebook_lifecycle
from mnemosyne.ebook_lifecycle import (
    EbookLifecycleError,
    apply_ebook_cleanup,
    apply_ebook_completion,
    preview_ebook_cleanup,
    preview_ebook_completion,
)


def make_placed_job(tmp_path: Path) -> tuple[Path, Path, Path]:
    job = tmp_path / "staging" / "alice-job"
    job.mkdir(parents=True)

    staged = job / "Alice's Adventures in Wonderland - Lewis Carroll (1865).epub"
    staged.write_bytes(b"verified epub bytes")
    sha = hashlib.sha256(staged.read_bytes()).hexdigest()

    final_dir = (
        tmp_path
        / "library"
        / "eBooks"
        / "Lewis Carroll"
        / "Alice's Adventures in Wonderland"
    )
    final_dir.mkdir(parents=True)
    final_file = final_dir / "Alice's Adventures in Wonderland.epub"
    final_file.write_bytes(staged.read_bytes())

    fetch = {
        "schemaVersion": 2,
        "jobId": "alice-job",
        "status": "placed-and-verified",
        "mediaType": "ebook",
        "source": {"provider": "Internet Archive"},
        "work": {
            "title": "Alice's Adventures in Wonderland",
            "creator": "Lewis Carroll",
            "year": 1865,
        },
        "selection": {"extension": ".epub"},
        "verification": {
            "sha256": sha,
            "formatVerified": True,
        },
        "stagedFile": staged.name,
        "finalLibraryModified": True,
        "finalPlacement": {
            "status": "verified",
            "destination": str(final_dir),
            "file": str(final_file),
            "sha256": sha,
        },
    }
    (job / "ebook-fetch-report.json").write_text(
        json.dumps(fetch),
        encoding="utf-8",
    )

    placement = {
        "schemaVersion": 1,
        "jobId": "alice-job",
        "status": "placed-and-verified",
        "destination": {
            "directory": str(final_dir),
            "file": str(final_file),
            "sha256": sha,
        },
    }
    (job / "ebook-placement-report.json").write_text(
        json.dumps(placement),
        encoding="utf-8",
    )

    return job, staged, final_file


def test_completion_preview_reverifies_final_and_staged_hashes(tmp_path: Path) -> None:
    job, _, final_file = make_placed_job(tmp_path)

    preview = preview_ebook_completion(job)

    assert preview.ready_to_complete is True
    assert preview.final_file == final_file
    assert all(check.passed for check in preview.checks)


def test_completion_preview_blocks_modified_final_file(tmp_path: Path) -> None:
    job, _, final_file = make_placed_job(tmp_path)
    final_file.write_bytes(b"changed")

    preview = preview_ebook_completion(job)

    assert preview.ready_to_complete is False
    failed = {check.name for check in preview.checks if not check.passed}
    assert "final-sha256" in failed


def test_apply_completion_writes_report_and_updates_fetch_state(tmp_path: Path) -> None:
    job, staged, final_file = make_placed_job(tmp_path)

    result = apply_ebook_completion(job)

    assert staged.is_file()
    assert final_file.is_file()
    assert result.completion_report_path.is_file()

    completion = json.loads(
        result.completion_report_path.read_text(encoding="utf-8")
    )
    fetch = json.loads(
        result.fetch_report_path.read_text(encoding="utf-8")
    )

    assert completion["status"] == "complete"
    assert completion["retention"]["stagingRetained"] is True
    assert fetch["status"] == "complete"
    assert fetch["completion"]["status"] == "certified"
    assert fetch["finalLibraryModified"] is True


def test_cleanup_preview_requires_completed_state(tmp_path: Path) -> None:
    job, _, _ = make_placed_job(tmp_path)

    with pytest.raises(EbookLifecycleError, match="lifecycle-complete"):
        preview_ebook_cleanup(job)


def test_cleanup_requires_exact_job_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(ebook_lifecycle, "runtime_root", lambda: runtime)

    job, _, _ = make_placed_job(tmp_path)
    apply_ebook_completion(job)

    with pytest.raises(EbookLifecycleError, match="exactly match"):
        apply_ebook_cleanup(job, confirm_job_id="wrong-job")

    assert job.is_dir()


def test_cleanup_writes_durable_receipt_before_removing_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(ebook_lifecycle, "runtime_root", lambda: runtime)

    job, _, final_file = make_placed_job(tmp_path)
    completion = apply_ebook_completion(job)

    result = apply_ebook_cleanup(
        job,
        confirm_job_id=completion.job_id,
    )

    assert not job.exists()
    assert final_file.is_file()
    assert result.receipt_path.is_file()

    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["jobId"] == "alice-job"
    assert receipt["status"] == "complete-staging-removed"
    assert receipt["provenanceSummary"]["finalEbookSha256"] == result.final_sha256


def test_cleanup_blocks_if_final_file_changes_after_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(ebook_lifecycle, "runtime_root", lambda: runtime)

    job, _, final_file = make_placed_job(tmp_path)
    apply_ebook_completion(job)
    final_file.write_bytes(b"tampered after completion")

    with pytest.raises(EbookLifecycleError, match="changed after completion"):
        preview_ebook_cleanup(job)

    assert job.is_dir()
