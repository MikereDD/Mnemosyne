import json
from pathlib import Path

from mnemosyne.adoption import adopt_latest_recommended_source
from mnemosyne.quality import ActualAudioQuality


def _write_mp3(path: Path, payload: bytes = b"ID3\x04\x00\x00" + b"\x00" * 64) -> None:
    path.write_bytes(payload)


def test_transactional_adoption_of_byte_identical_winner(tmp_path: Path, monkeypatch) -> None:
    job = tmp_path / "job"
    job.mkdir()

    current = job / "Animal Farm - George Orwell (1945).mp3"
    _write_mp3(current)

    fetch_report = {
        "schemaVersion": 3,
        "jobId": "test-job",
        "status": "needs-attention",
        "audio": {
            "canonicalStagedName": current.name,
            "stagedPath": str(current),
            "sha256": "old",
        },
        "warnings": ["Provider metadata claimed lossless audio, but the downloaded file inspects as lossy codec MP3."],
        "finalLibraryModified": False,
    }
    (job / "fetch-report.json").write_text(json.dumps(fetch_report), encoding="utf-8")

    run = job / "comparison" / "run-12345678"
    run.mkdir(parents=True)
    winner = run / "01-source.mp3"
    winner.write_bytes(current.read_bytes())

    import hashlib
    sha = hashlib.sha256(winner.read_bytes()).hexdigest()

    comparison_report = {
        "schemaVersion": 2,
        "sourceJob": "test-job",
        "runId": "run-12345678",
        "status": "compared",
        "candidates": [
            {
                "sourceName": "source.mp3",
                "archiveFormat": "VBR MP3",
                "archiveSource": "original",
                "providerClaimedLossless": False,
                "sha256": sha,
                "comparisonPath": str(winner),
            }
        ],
        "recommendedSourceName": "source.mp3",
        "recommendedPath": str(winner),
        "finalLibraryModified": False,
    }
    (run / "comparison-report.json").write_text(json.dumps(comparison_report), encoding="utf-8")

    monkeypatch.setattr(
        "mnemosyne.adoption.inspect_actual_quality",
        lambda path: ActualAudioQuality(
            codec="MP3",
            lossless=False,
            bitrate_bps=128000,
            sample_rate_hz=44100,
            channels=2,
        ),
    )

    result = adopt_latest_recommended_source(job)

    assert result.canonical_path.exists()
    assert result.backup_path.exists()

    updated = json.loads((job / "fetch-report.json").read_text(encoding="utf-8"))
    assert updated["status"] == "staged-source-resolved"
    assert updated["sourceResolution"]["status"] == "resolved-by-actual-comparison"
    assert updated["finalLibraryModified"] is False
    assert updated["warnings"] == []


def test_adoption_rejects_changed_comparison_file(tmp_path: Path) -> None:
    job = tmp_path / "job"
    job.mkdir()

    current = job / "Book.mp3"
    _write_mp3(current)
    (job / "fetch-report.json").write_text(
        json.dumps(
            {
                "jobId": "x",
                "audio": {
                    "canonicalStagedName": current.name,
                    "stagedPath": str(current),
                },
            }
        ),
        encoding="utf-8",
    )

    run = job / "comparison" / "run-1"
    run.mkdir(parents=True)
    winner = run / "winner.mp3"
    _write_mp3(winner, b"ID3different")

    (run / "comparison-report.json").write_text(
        json.dumps(
            {
                "recommendedSourceName": "winner.mp3",
                "recommendedPath": str(winner),
                "candidates": [
                    {
                        "sourceName": "winner.mp3",
                        "comparisonPath": str(winner),
                        "sha256": "definitely-wrong",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    import pytest
    from mnemosyne.adoption import AdoptionError

    with pytest.raises(AdoptionError, match="SHA-256 mismatch"):
        adopt_latest_recommended_source(job)
