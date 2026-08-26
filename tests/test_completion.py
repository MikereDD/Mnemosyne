import hashlib
import json
from pathlib import Path

import pytest

from mnemosyne.completion import CompletionError, apply_completion, preview_completion


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_placed_job(tmp_path: Path) -> tuple[Path, Path, Path]:
    job = tmp_path / "staging" / "job"
    job.mkdir(parents=True)

    destination = tmp_path / "library" / "Book"
    destination.mkdir(parents=True)

    audio = destination / "Book.m4a"
    audio.write_bytes(b"audio")
    cover = destination / "cover.jpg"
    cover.write_bytes(b"cover")

    fetch = {
        "schemaVersion": 6,
        "jobId": "job-1",
        "status": "placed-and-verified",
        "finalLibraryModified": True,
        "finalPlacement": {
            "status": "verified",
            "destination": str(destination),
            "audioPath": str(audio),
            "audioSha256": _sha(audio),
            "coverPath": str(cover),
            "coverSha256": _sha(cover),
        },
    }
    (job / "fetch-report.json").write_text(json.dumps(fetch), encoding="utf-8")

    placement = {
        "schemaVersion": 1,
        "jobId": "job-1",
        "status": "placed-and-verified",
        "destination": str(destination),
        "audio": {
            "destination": str(audio),
            "sha256": _sha(audio),
        },
        "cover": {
            "destination": str(cover),
            "sha256": _sha(cover),
        },
    }
    (job / "placement-report.json").write_text(json.dumps(placement), encoding="utf-8")

    readiness = {
        "schemaVersion": 1,
        "jobId": "job-1",
        "status": "ready-for-placement",
    }
    (job / "readiness-report.json").write_text(json.dumps(readiness), encoding="utf-8")

    return job, audio, cover


def test_completion_preview_verifies_final_hashes(tmp_path: Path) -> None:
    job, _, _ = _build_placed_job(tmp_path)
    preview = preview_completion(job)

    assert preview.ready_to_complete is True
    assert all(check.passed for check in preview.checks)


def test_completion_blocks_changed_final_audio(tmp_path: Path) -> None:
    job, audio, _ = _build_placed_job(tmp_path)
    audio.write_bytes(b"changed")

    preview = preview_completion(job)

    assert preview.ready_to_complete is False
    failed = {check.name for check in preview.checks if not check.passed}
    assert "final-audio-sha256" in failed


def test_apply_completion_marks_job_complete_without_cleanup(tmp_path: Path) -> None:
    job, audio, cover = _build_placed_job(tmp_path)

    result = apply_completion(job)

    assert result.audio_path == audio
    assert result.cover_path == cover
    assert job.exists()
    assert audio.exists()
    assert cover.exists()

    fetch = json.loads((job / "fetch-report.json").read_text(encoding="utf-8"))
    assert fetch["status"] == "complete"
    assert fetch["completion"]["stagingRetained"] is True
    assert fetch["completion"]["fetchListPruned"] is False

    completion = json.loads((job / "completion-report.json").read_text(encoding="utf-8"))
    assert completion["status"] == "complete"
    assert completion["retention"]["automaticCleanupPerformed"] is False
    assert completion["retention"]["fetchListPruned"] is False


def test_completion_refuses_already_complete_job(tmp_path: Path) -> None:
    job, _, _ = _build_placed_job(tmp_path)
    fetch_path = job / "fetch-report.json"
    fetch = json.loads(fetch_path.read_text(encoding="utf-8"))
    fetch["status"] = "complete"
    fetch_path.write_text(json.dumps(fetch), encoding="utf-8")

    preview = preview_completion(job)
    assert preview.ready_to_complete is False
