import hashlib
import json
from pathlib import Path

import pytest

from mnemosyne.cleanup import CleanupError, apply_cleanup, preview_cleanup


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_complete_job(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    monkeypatch.setattr("mnemosyne.cleanup.runtime_root", lambda: runtime)

    job = runtime / "staging" / "job"
    job.mkdir(parents=True)

    final = tmp_path / "library" / "Book"
    final.mkdir(parents=True)
    audio = final / "Book.m4a"
    cover = final / "cover.jpg"
    audio.write_bytes(b"audio")
    cover.write_bytes(b"cover")

    fetch = {
        "schemaVersion": 7,
        "jobId": "job-1",
        "status": "complete",
        "source": {"provider": "Internet Archive"},
        "media": {"type": "audiobook", "title": "Book"},
        "plannedDestination": str(final),
        "finalPlacement": {
            "destination": str(final),
            "audioPath": str(audio),
            "audioSha256": _sha(audio),
            "coverPath": str(cover),
            "coverSha256": _sha(cover),
        },
        "completion": {
            "status": "certified",
            "stagingRetained": True,
            "fetchListPruned": False,
        },
    }
    (job / "fetch-report.json").write_text(json.dumps(fetch), encoding="utf-8")
    (job / "completion-report.json").write_text(
        json.dumps({"status": "complete"}), encoding="utf-8"
    )
    (job / "placement-report.json").write_text(
        json.dumps({"status": "placed-and-verified"}), encoding="utf-8"
    )
    (job / "readiness-report.json").write_text(
        json.dumps({"status": "ready-for-placement"}), encoding="utf-8"
    )
    (job / "extra-evidence.txt").write_text("evidence", encoding="utf-8")

    return job, final


def test_cleanup_preview_requires_complete_job(tmp_path: Path, monkeypatch) -> None:
    job, _ = _build_complete_job(tmp_path, monkeypatch)
    fetch_path = job / "fetch-report.json"
    fetch = json.loads(fetch_path.read_text(encoding="utf-8"))
    fetch["status"] = "placed-and-verified"
    fetch_path.write_text(json.dumps(fetch), encoding="utf-8")

    with pytest.raises(CleanupError, match="lifecycle-complete"):
        preview_cleanup(job)


def test_cleanup_blocks_changed_final_audio(tmp_path: Path, monkeypatch) -> None:
    job, final = _build_complete_job(tmp_path, monkeypatch)
    (final / "Book.m4a").write_bytes(b"changed")

    with pytest.raises(CleanupError, match="changed after completion"):
        preview_cleanup(job)


def test_cleanup_requires_exact_confirmation(tmp_path: Path, monkeypatch) -> None:
    job, _ = _build_complete_job(tmp_path, monkeypatch)

    with pytest.raises(CleanupError, match="does not exactly match"):
        apply_cleanup(job, confirm_job_id="wrong-job")


def test_cleanup_archives_receipt_before_removal(tmp_path: Path, monkeypatch) -> None:
    job, final = _build_complete_job(tmp_path, monkeypatch)

    result = apply_cleanup(job, confirm_job_id="job-1")

    assert not job.exists()
    assert final.exists()
    assert result.receipt_path.is_file()

    receipt = json.loads(result.receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "complete-staging-removed"
    assert receipt["retention"]["stagingRemoved"] is True
    assert receipt["retention"]["fetchListPruned"] is False
