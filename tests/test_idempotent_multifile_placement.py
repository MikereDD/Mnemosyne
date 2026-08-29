from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest

from mnemosyne.multifile_placement import (
    MultiFilePlacementError,
    apply_multifile_placement,
    preview_multifile_placement,
)

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _job(tmp_path: Path, destination: Path):
    job = tmp_path / "job"
    audio = job / "audio"
    audio.mkdir(parents=True)
    a = audio / "01 - Chapter 01.mp3"
    b = audio / "02 - Chapter 02.mp3"
    a.write_bytes(b"stage-one")
    b.write_bytes(b"stage-two")
    cover = job / "cover.jpg"
    cover.write_bytes(b"same-cover")
    entries = []
    for i, p in enumerate((a, b), 1):
        entries.append({
            "index": i,
            "stagedPath": str(p),
            "canonicalStagedName": p.name,
            "sha256": _sha(p),
            "writtenTags": {"title": f"Chapter {i}", "track": f"{i}/2"},
        })
    h = hashlib.sha256()
    for e in entries:
        h.update(e["sha256"].encode("ascii"))
        h.update(b"\n")
    fetch = {
        "jobId": "job",
        "status": "staged-metadata-normalized",
        "plannedDestination": str(destination),
        "audio": {"mode": "multi-file", "fileCount": 2, "files": entries},
        "cover": {"stagedPath": str(cover), "sha256": _sha(cover)},
        "finalLibraryModified": False,
    }
    (job / "fetch-report.json").write_text(json.dumps(fetch), encoding="utf-8")
    ready = {
        "jobId": "job",
        "status": "ready-for-placement",
        "mode": "multi-file",
        "editionSha256": h.hexdigest(),
        "coverSha256": _sha(cover),
        "checks": [{"name": "all", "passed": True}],
    }
    (job / "readiness-report.json").write_text(json.dumps(ready), encoding="utf-8")
    return job, a, b, cover

def _populate(dest: Path, a: Path, b: Path, cover: Path):
    dest.mkdir(parents=True)
    for p in (a, b, cover):
        (dest / p.name).write_bytes(p.read_bytes())

def test_identical_existing_destination_is_idempotent(tmp_path: Path):
    dest = tmp_path / "library"
    job, a, b, cover = _job(tmp_path, dest)
    _populate(dest, a, b, cover)
    preview = preview_multifile_placement(job)
    assert preview.existing_destination_equivalent is True
    before = {p.name: _sha(p) for p in dest.iterdir()}
    result = apply_multifile_placement(job)
    after = {p.name: _sha(p) for p in dest.iterdir()}
    assert result.verified_existing_destination is True
    assert before == after
    fetch = json.loads((job / "fetch-report.json").read_text(encoding="utf-8"))
    assert fetch["finalLibraryModified"] is False
    assert fetch["finalLibraryVerifiedEquivalent"] is True

def test_serialization_difference_uses_metadata_and_decoded_audio(tmp_path: Path, monkeypatch):
    dest = tmp_path / "library"
    job, a, b, cover = _job(tmp_path, dest)
    _populate(dest, a, b, cover)
    (dest / b.name).write_bytes(b"different-container-bytes")
    monkeypatch.setattr("mnemosyne.multifile_placement.verify_metadata", lambda *a, **k: object())
    monkeypatch.setattr("mnemosyne.multifile_placement._decoded_audio_sha", lambda p: "same")
    assert preview_multifile_placement(job).existing_destination_equivalent is True

def test_different_decoded_audio_blocks(tmp_path: Path, monkeypatch):
    dest = tmp_path / "library"
    job, a, b, cover = _job(tmp_path, dest)
    _populate(dest, a, b, cover)
    (dest / b.name).write_bytes(b"different-audio")
    monkeypatch.setattr("mnemosyne.multifile_placement.verify_metadata", lambda *a, **k: object())
    monkeypatch.setattr(
        "mnemosyne.multifile_placement._decoded_audio_sha",
        lambda p: "stage" if p.parent.name == "audio" else "dest",
    )
    with pytest.raises(MultiFilePlacementError, match="audio content differs"):
        preview_multifile_placement(job)

def test_extra_audio_blocks(tmp_path: Path):
    dest = tmp_path / "library"
    job, a, b, cover = _job(tmp_path, dest)
    _populate(dest, a, b, cover)
    (dest / "03 - Surprise.mp3").write_bytes(b"extra")
    with pytest.raises(MultiFilePlacementError, match="same complete audio file set"):
        preview_multifile_placement(job)

def test_wrong_cover_blocks(tmp_path: Path):
    dest = tmp_path / "library"
    job, a, b, cover = _job(tmp_path, dest)
    _populate(dest, a, b, cover)
    (dest / cover.name).write_bytes(b"wrong")
    with pytest.raises(MultiFilePlacementError, match="cover differs"):
        preview_multifile_placement(job)
