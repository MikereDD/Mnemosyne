from pathlib import Path

from mnemosyne.inspection import (
    InspectionError,
    _proposed_tags,
    _resolve_multifile_audio_paths,
    latest_staging_job,
)


def test_proposed_audiobook_tags() -> None:
    report = {
        "media": {
            "type": "audiobook",
            "title": "Animal Farm",
            "creator": "George Orwell",
            "year": 1945,
        }
    }
    assert _proposed_tags(report) == {
        "title": "Animal Farm",
        "artist": "George Orwell",
        "album_artist": "George Orwell",
        "album": "Animal Farm",
        "date": "1945",
        "genre": "Audiobook",
    }


def test_latest_staging_job_uses_completed_report(tmp_path: Path) -> None:
    older = tmp_path / "older"
    newer = tmp_path / "newer"
    incomplete = tmp_path / "incomplete"
    older.mkdir()
    newer.mkdir()
    incomplete.mkdir()

    (older / "fetch-report.json").write_text("{}", encoding="utf-8")
    (newer / "fetch-report.json").write_text("{}", encoding="utf-8")

    older_report = older / "fetch-report.json"
    newer_report = newer / "fetch-report.json"

    older_report.touch()
    newer_report.touch()

    # Force deterministic timestamps.
    import os
    os.utime(older_report, (1_700_000_000, 1_700_000_000))
    os.utime(newer_report, (1_800_000_000, 1_800_000_000))

    assert latest_staging_job(tmp_path) == newer

def test_resolve_multifile_audio_paths_uses_report_order(tmp_path: Path) -> None:
    job = tmp_path / "job"
    audio_dir = job / "audio"
    audio_dir.mkdir(parents=True)
    first = audio_dir / "01 - Chapter 01.flac"
    second = audio_dir / "02 - Chapter 02.flac"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    report = {"audio": {"mode": "multi-file", "fileCount": 2, "files": [{"stagedPath": str(first)}, {"stagedPath": str(second)}]}}
    assert _resolve_multifile_audio_paths(job, report) == [first, second]


def test_resolve_multifile_audio_paths_rejects_missing_member(tmp_path: Path) -> None:
    job = tmp_path / "job"
    audio_dir = job / "audio"
    audio_dir.mkdir(parents=True)
    present = audio_dir / "01 - Chapter 01.flac"
    missing = audio_dir / "02 - Chapter 02.flac"
    present.write_bytes(b"first")
    report = {"audio": {"mode": "multi-file", "fileCount": 2, "files": [{"stagedPath": str(present)}, {"stagedPath": str(missing)}]}}
    try:
        _resolve_multifile_audio_paths(job, report)
    except InspectionError as exc:
        assert "is missing" in str(exc)
    else:
        raise AssertionError("missing multi-file member should block inspection")

