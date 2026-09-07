import json
from pathlib import Path

import pytest

import mnemosyne.staging_discard as staging_discard
from mnemosyne.staging_discard import (
    StagingDiscardError,
    apply_staging_discard,
    preview_staging_discard,
)


def _build_job(
    tmp_path: Path,
    monkeypatch,
    *,
    job_id: str = "job-1",
    folder_name: str | None = None,
    ebook: bool = True,
    status: str | None = None,
    library_modified: bool = False,
) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr("mnemosyne.staging_discard.runtime_root", lambda: runtime)
    job = runtime / "staging" / (folder_name or job_id)
    job.mkdir(parents=True)

    if ebook:
        report_path = job / "ebook-fetch-report.json"
        report = {
            "schemaVersion": 1,
            "jobId": job_id,
            "status": status or "staged-verified",
            "mediaType": "ebook",
            "source": {"provider": "Internet Archive", "identifier": "fixture"},
            "work": {"title": "Fixture Book", "creator": "Fixture Author", "year": 2000},
            "selection": {"extension": ".epub"},
            "verification": {"sha256": "abc", "formatVerified": True},
            "stagedFile": "book.epub",
            "finalLibraryModified": library_modified,
        }
        (job / "book.epub").write_bytes(b"ebook")
    else:
        report_path = job / "fetch-report.json"
        report = {
            "schemaVersion": 9,
            "jobId": job_id,
            "status": status or "staged-normalized",
            "source": {"provider": "Internet Archive", "identifier": "fixture"},
            "media": {"type": "audiobook", "title": "Fixture Book"},
            "plannedDestination": str(tmp_path / "library" / "Fixture Book"),
            "finalLibraryModified": library_modified,
        }
        (job / "audio.m4a").write_bytes(b"audio")

    report_path.write_text(json.dumps(report), encoding="utf-8")
    (job / "evidence.txt").write_text("evidence", encoding="utf-8")
    return job, runtime


def test_preview_is_read_only_and_reports_job_state(tmp_path: Path, monkeypatch) -> None:
    job, runtime = _build_job(tmp_path, monkeypatch)
    preview = preview_staging_discard(job)
    assert job.is_dir()
    assert preview.job_id == "job-1"
    assert preview.media_type == "ebook"
    assert preview.status == "staged-verified"
    assert preview.library_modified is False
    assert preview.file_count == 3
    assert preview.staging_size_bytes > 0
    assert preview.discard_allowed
    assert not preview.discard_receipt_path.exists()
    assert not (runtime / "state" / "discarded").exists()


def test_bare_job_id_resolves_only_under_staging(tmp_path: Path, monkeypatch) -> None:
    job, _ = _build_job(tmp_path, monkeypatch)
    assert preview_staging_discard(Path("job-1")).job_dir == job.resolve()


def test_wrong_confirmation_does_not_write_receipt_or_delete(tmp_path: Path, monkeypatch) -> None:
    job, _ = _build_job(tmp_path, monkeypatch)
    with pytest.raises(StagingDiscardError, match="does not exactly match"):
        apply_staging_discard(job, confirm_job_id="wrong")
    assert job.is_dir()
    assert not preview_staging_discard(job).discard_receipt_path.exists()


def test_outside_staging_and_staging_root_are_refused(tmp_path: Path, monkeypatch) -> None:
    _, runtime = _build_job(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "ebook-fetch-report.json").write_text(
        json.dumps({"jobId": "outside", "status": "staged-verified"}),
        encoding="utf-8",
    )
    with pytest.raises(StagingDiscardError, match="outside Mnemosyne staging"):
        preview_staging_discard(outside)
    with pytest.raises(StagingDiscardError, match="staging root"):
        preview_staging_discard(runtime / "staging")


def test_nested_target_and_identity_mismatch_are_refused(tmp_path: Path, monkeypatch) -> None:
    job, _ = _build_job(tmp_path, monkeypatch)
    nested = job / "nested"
    nested.mkdir()
    with pytest.raises(StagingDiscardError, match="direct staging job"):
        preview_staging_discard(nested)

    job2, _ = _build_job(tmp_path / "second", monkeypatch, folder_name="wrong-folder")
    with pytest.raises(StagingDiscardError, match="identity"):
        preview_staging_discard(job2)


def test_library_modified_and_completed_jobs_are_blocked(tmp_path: Path, monkeypatch) -> None:
    job, runtime = _build_job(
        tmp_path, monkeypatch, status="placed-and-verified", library_modified=True
    )
    completed = runtime / "state" / "completed" / "job-1.json"
    completed.parent.mkdir(parents=True)
    completed.write_text(json.dumps({"jobId": "job-1"}), encoding="utf-8")
    preview = preview_staging_discard(job)
    assert not preview.discard_allowed
    assert preview.completion_receipt_exists
    with pytest.raises(StagingDiscardError, match="blocked"):
        apply_staging_discard(job, confirm_job_id="job-1")
    assert job.exists()


def test_receipt_exists_before_delete_begins(tmp_path: Path, monkeypatch) -> None:
    job, _ = _build_job(tmp_path, monkeypatch)
    original_rmtree = staging_discard.shutil.rmtree
    observed = {}

    def checking_rmtree(path: Path) -> None:
        receipt = preview_staging_discard(path).discard_receipt_path
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        observed["status"] = payload["status"]
        observed["removed"] = payload["retention"]["stagingRemoved"]
        original_rmtree(path)

    monkeypatch.setattr(staging_discard.shutil, "rmtree", checking_rmtree)
    apply_staging_discard(job, confirm_job_id="job-1")
    assert observed == {"status": "discard-authorized", "removed": False}


def test_successful_discard_removes_only_staging_and_finalizes_receipt(tmp_path: Path, monkeypatch) -> None:
    job, runtime = _build_job(tmp_path, monkeypatch, ebook=False)
    library = tmp_path / "library"
    library.mkdir()
    keeper = library / "keep.txt"
    keeper.write_text("keep", encoding="utf-8")

    result = apply_staging_discard(job, confirm_job_id="job-1")
    assert not job.exists()
    assert keeper.read_text(encoding="utf-8") == "keep"
    assert result.receipt_path == runtime / "state" / "discarded" / "job-1.json"
    payload = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert payload["status"] == "discarded-staging-removed"
    assert payload["retention"]["stagingRemoved"] is True
    assert payload["retention"]["finalLibraryModified"] is False
    assert payload["fetchReportSnapshot"]["jobId"] == "job-1"


def test_delete_failure_retains_job_and_failure_receipt(tmp_path: Path, monkeypatch) -> None:
    job, _ = _build_job(tmp_path, monkeypatch)

    def fail_rmtree(path: Path) -> None:
        raise OSError("simulated delete failure")

    monkeypatch.setattr(staging_discard.shutil, "rmtree", fail_rmtree)
    with pytest.raises(StagingDiscardError, match="failure provenance"):
        apply_staging_discard(job, confirm_job_id="job-1")
    assert job.exists()
    receipt = preview_staging_discard(job).discard_receipt_path
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "discard-failed"
    assert payload["retention"]["stagingRemoved"] is False
    assert "simulated delete failure" in payload["failure"]
