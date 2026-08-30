from __future__ import annotations

import hashlib
import json
from pathlib import Path

import mnemosyne.quality_recovery as recovery_module
from mnemosyne.quality import ActualAudioQuality
from mnemosyne.quality_recovery import apply_quality_recovery, preview_quality_recovery


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_quality_recovery_updates_report_without_touching_audio(
    tmp_path: Path,
    monkeypatch,
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    audio = job / "book.m4b"
    audio.write_bytes(b"\x00\x00\x00\x18ftypM4B " + b"x" * 100)
    digest = _sha256(audio)

    report = {
        "schemaVersion": 9,
        "jobId": "book-job",
        "status": "needs-attention",
        "source": {"identifier": "book"},
        "audio": {
            "mode": "single-file",
            "fileCount": 1,
            "stagedPath": str(audio),
            "canonicalStagedName": audio.name,
            "sha256": digest,
            "files": [
                {
                    "stagedPath": str(audio),
                    "canonicalStagedName": audio.name,
                    "sha256": digest,
                }
            ],
        },
        "warnings": [
            "Actual audio quality inspection failed; the downloaded file passed container/signature validation but could not be parsed for codec details: bad atom"
        ],
        "finalLibraryModified": False,
    }
    (job / "fetch-report.json").write_text(json.dumps(report), encoding="utf-8")
    before = _sha256(audio)

    monkeypatch.setattr(
        recovery_module,
        "inspect_actual_quality",
        lambda _path: ActualAudioQuality(
            codec="aac",
            lossless=False,
            bitrate_bps=63044,
            sample_rate_hz=44100,
            channels=1,
            inspection_warning=None,
            inspection_source="ffprobe",
        ),
    )

    preview = preview_quality_recovery(job)
    assert preview.ready_to_apply
    result = apply_quality_recovery(job)

    assert result.status == "staged-normalized"
    assert _sha256(audio) == before

    updated = json.loads((job / "fetch-report.json").read_text(encoding="utf-8"))
    assert updated["warnings"] == []
    assert updated["audio"]["actualCodec"] == "aac"
    assert updated["audio"]["actualLossless"] is False
    assert updated["audio"]["actualInspectionSource"] == "ffprobe"
    assert updated["qualityInspectionRecovery"]["status"] == "verified"
    assert updated["finalLibraryModified"] is False
