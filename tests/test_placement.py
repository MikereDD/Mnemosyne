import hashlib
import json
from pathlib import Path

import pytest

from mnemosyne.placement import PlacementError, apply_final_placement, preview_final_placement


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_ready_job(tmp_path: Path) -> tuple[Path, Path]:
    job = tmp_path / "staging" / "job"
    job.mkdir(parents=True)

    audio = job / "Book - Author (2000).m4a"
    audio.write_bytes(b"audio-content")
    cover = job / "cover.jpg"
    cover.write_bytes(b"cover-content")

    destination = tmp_path / "library" / "Author" / "Audiobook" / "Book - Author (2000)"

    fetch = {
        "schemaVersion": 5,
        "jobId": "job-1",
        "status": "staged-metadata-normalized",
        "plannedDestination": str(destination),
        "audio": {
            "stagedPath": str(audio),
            "canonicalStagedName": audio.name,
            "sha256": _sha(audio),
        },
        "cover": {
            "stagedPath": str(cover),
            "canonicalStagedName": cover.name,
            "sha256": _sha(cover),
        },
        "finalLibraryModified": False,
    }
    (job / "fetch-report.json").write_text(json.dumps(fetch), encoding="utf-8")

    readiness = {
        "schemaVersion": 1,
        "jobId": "job-1",
        "status": "ready-for-placement",
        "audioPath": str(audio),
        "audioSha256": _sha(audio),
        "coverPath": str(cover),
        "coverSha256": _sha(cover),
        "checks": [{"name": "all", "passed": True, "detail": "ok"}],
        "plannedDestination": str(destination),
        "finalLibraryModified": False,
    }
    (job / "readiness-report.json").write_text(json.dumps(readiness), encoding="utf-8")

    return job, destination


def test_preview_refuses_existing_destination(tmp_path: Path) -> None:
    job, destination = _build_ready_job(tmp_path)
    destination.mkdir(parents=True)

    with pytest.raises(PlacementError, match="already exists"):
        preview_final_placement(job)


def test_preview_detects_staged_audio_changed_after_readiness(tmp_path: Path) -> None:
    job, _ = _build_ready_job(tmp_path)
    audio = job / "Book - Author (2000).m4a"
    audio.write_bytes(b"changed-after-ready")

    with pytest.raises(PlacementError, match="changed after readiness"):
        preview_final_placement(job)


def test_transactional_placement_copies_and_verifies(tmp_path: Path) -> None:
    job, destination = _build_ready_job(tmp_path)

    result = apply_final_placement(job)

    assert result.destination == destination
    assert result.audio_path.read_bytes() == b"audio-content"
    assert result.cover_path.read_bytes() == b"cover-content"
    assert result.audio_sha256 == _sha(result.audio_path)
    assert result.cover_sha256 == _sha(result.cover_path)

    placement = json.loads((job / "placement-report.json").read_text(encoding="utf-8"))
    assert placement["status"] == "placed-and-verified"
    assert placement["rollback"]["overwroteExistingDestination"] is False

    fetch = json.loads((job / "fetch-report.json").read_text(encoding="utf-8"))
    assert fetch["status"] == "placed-and-verified"
    assert fetch["finalLibraryModified"] is True
    assert fetch["finalPlacement"]["status"] == "verified"
