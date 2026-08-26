from pathlib import Path

from mnemosyne.inspection import _proposed_tags, latest_staging_job


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
