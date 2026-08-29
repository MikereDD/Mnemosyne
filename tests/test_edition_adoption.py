from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mnemosyne.adoption import adopt_latest_recommended_edition
from mnemosyne.quality import ActualAudioQuality


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quality(path: Path) -> ActualAudioQuality:
    return ActualAudioQuality(
        codec="MP3",
        lossless=False,
        bitrate_bps=128000,
        sample_rate_hz=44100,
        channels=1,
        inspection_source="test",
    )


def test_single_to_multi_edition_adoption_is_transactional(tmp_path: Path, monkeypatch) -> None:
    job = tmp_path / "job"
    job.mkdir()
    current = job / "Book - Author (1900).m4b"
    current.write_bytes(b"old-complete-m4b")

    fetch_report = {
        "schemaVersion": 10,
        "jobId": "job-1",
        "status": "staged-normalized",
        "audioEdition": {
            "selectedEditionKey": "single:old.m4b",
            "multiFile": False,
            "fileCount": 1,
            "extension": ".m4b",
        },
        "audio": {
            "mode": "single-file",
            "fileCount": 1,
            "canonicalStagedName": current.name,
            "stagedPath": str(current),
            "files": [
                {
                    "index": 1,
                    "sourceName": "old.m4b",
                    "sha256": _sha(current),
                    "canonicalStagedName": current.name,
                    "stagedPath": str(current),
                }
            ],
            "sha256": _sha(current),
        },
        "warnings": [],
        "finalLibraryModified": False,
    }
    (job / "fetch-report.json").write_text(json.dumps(fetch_report), encoding="utf-8")

    run = job / "comparison" / "run-1"
    edition_dir = run / "edition-01"
    edition_dir.mkdir(parents=True)

    files = []
    for index in (1, 2):
        source = edition_dir / f"{index:02d}-book_{index:02d}_author.mp3"
        source.write_bytes(b"ID3" + bytes([index]) * 100)
        files.append(
            {
                "sourceName": f"book_{index:02d}_author.mp3",
                "sourceUrl": f"https://example.invalid/{index}.mp3",
                "archiveFormat": "VBR MP3",
                "archiveSource": "original",
                "providerClaimedLossless": False,
                "actualSize": source.stat().st_size,
                "sha256": _sha(source),
                "comparisonPath": str(source),
            }
        )

    comparison = {
        "schemaVersion": 3,
        "sourceJob": "job-1",
        "status": "compared-editions",
        "comparisonUnit": "complete-audio-edition",
        "editions": [
            {
                "editionKey": "set:.mp3:VBR MP3:original:book",
                "label": "MP3 chapter set (2 files)",
                "multiFile": True,
                "fileCount": 2,
                "extension": ".mp3",
                "archiveFormat": "VBR MP3",
                "archiveSource": "original",
                "files": files,
            }
        ],
        "recommendedEditionKey": "set:.mp3:VBR MP3:original:book",
        "recommendedLabel": "MP3 chapter set (2 files)",
        "recommendedMultiFile": True,
        "recommendedFileCount": 2,
        "finalLibraryModified": False,
    }
    (run / "comparison-report.json").write_text(json.dumps(comparison), encoding="utf-8")

    monkeypatch.setattr("mnemosyne.adoption.inspect_actual_quality", _quality)

    result = adopt_latest_recommended_edition(job)

    assert result.multi_file is True
    assert [path.name for path in result.canonical_paths] == [
        "01 - Chapter 01.mp3",
        "02 - Chapter 02.mp3",
    ]
    assert not current.exists()
    assert all(path.exists() for path in result.canonical_paths)

    updated = json.loads((job / "fetch-report.json").read_text(encoding="utf-8"))
    assert updated["status"] == "staged-source-resolved"
    assert updated["audioEdition"]["multiFile"] is True
    assert updated["audioEdition"]["fileCount"] == 2
    assert updated["audio"]["mode"] == "multi-file"
    assert updated["audio"]["canonicalStagedName"] is None
    assert updated["audio"]["stagedPath"] is None
    assert len(updated["audio"]["files"]) == 2
    assert updated["sourceResolution"]["status"] == "resolved-by-actual-comparison"
    assert updated["sourceResolution"]["adoptedMultiFile"] is True

    backup_audio = result.backup_dir / "audio" / current.name
    assert backup_audio.is_file()
    assert backup_audio.read_bytes() == b"old-complete-m4b"
    assert (result.backup_dir / "fetch-report.json").is_file()


def test_whole_edition_adoption_rejects_changed_member_before_mutation(
    tmp_path: Path, monkeypatch
) -> None:
    job = tmp_path / "job"
    job.mkdir()
    current = job / "Book.m4b"
    current.write_bytes(b"original")
    (job / "fetch-report.json").write_text(
        json.dumps(
            {
                "jobId": "job",
                "audio": {
                    "mode": "single-file",
                    "fileCount": 1,
                    "canonicalStagedName": current.name,
                    "stagedPath": str(current),
                    "files": [
                        {
                            "index": 1,
                            "canonicalStagedName": current.name,
                            "stagedPath": str(current),
                        }
                    ],
                },
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )

    run = job / "comparison" / "run-1"
    edition_dir = run / "edition-01"
    edition_dir.mkdir(parents=True)
    winner1 = edition_dir / "01-book_01.mp3"
    winner2 = edition_dir / "02-book_02.mp3"

    winner1.write_bytes(b"candidate-one")
    winner2.write_bytes(b"candidate-two")

    (run / "comparison-report.json").write_text(
        json.dumps(
            {
                "comparisonUnit": "complete-audio-edition",
                "editions": [
                    {
                        "editionKey": "set",
                        "label": "MP3 chapter set",
                        "multiFile": True,
                        "files": [
                            {
                                "sourceName": "book_01.mp3",
                                "comparisonPath": str(winner1),
                                "sha256": "wrong",
                            },
                            {
                                "sourceName": "book_02.mp3",
                                "comparisonPath": str(winner2),
                                "sha256": _sha(winner2),
                            },
                        ],
                    }
                ],
                "recommendedEditionKey": "set",
            }
        ),
        encoding="utf-8",
    )
    import pytest
    from mnemosyne.adoption import AdoptionError

    with pytest.raises(AdoptionError, match="changed after comparison"):
        adopt_latest_recommended_edition(job)

    assert current.read_bytes() == b"original"
    assert not (job / "audio").exists()

def test_whole_edition_adoption_rejects_current_path_outside_job_before_mutation(
    tmp_path: Path,
) -> None:
    import pytest
    from mnemosyne.adoption import AdoptionError, _current_edition_paths

    job = tmp_path / "job"
    job.mkdir()
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"must-survive")

    report = {
        "audio": {
            "mode": "single-file",
            "fileCount": 1,
            "files": [
                {
                    "index": 1,
                    "canonicalStagedName": outside.name,
                    "stagedPath": str(outside),
                }
            ],
        }
    }

    with pytest.raises(AdoptionError, match="outside this staging job"):
        _current_edition_paths(job, report)

    assert outside.read_bytes() == b"must-survive"


def test_whole_edition_adoption_surfaces_incomplete_rollback(
    tmp_path: Path, monkeypatch
) -> None:
    import pytest
    import mnemosyne.adoption as adoption_module
    from mnemosyne.adoption import AdoptionError

    job = tmp_path / "job"
    job.mkdir()
    current = job / "Book - Author (1900).m4b"
    current.write_bytes(b"old-edition")

    fetch_report = {
        "schemaVersion": 10,
        "jobId": "job-rollback",
        "status": "staged-normalized",
        "media": {"title": "Book", "creator": "Author", "year": 1900},
        "audioEdition": {
            "selectedEditionKey": "single:old.m4b",
            "multiFile": False,
            "fileCount": 1,
            "extension": ".m4b",
        },
        "audio": {
            "mode": "single-file",
            "fileCount": 1,
            "canonicalStagedName": current.name,
            "stagedPath": str(current),
            "files": [
                {
                    "index": 1,
                    "sourceName": "old.m4b",
                    "sha256": _sha(current),
                    "canonicalStagedName": current.name,
                    "stagedPath": str(current),
                }
            ],
        },
        "warnings": [],
        "finalLibraryModified": False,
    }
    (job / "fetch-report.json").write_text(
        json.dumps(fetch_report), encoding="utf-8"
    )

    run = job / "comparison" / "run-1"
    edition_dir = run / "edition-01"
    edition_dir.mkdir(parents=True)
    winner = edition_dir / "01-book.m4b"
    winner.write_bytes(b"new-edition")

    comparison = {
        "schemaVersion": 3,
        "sourceJob": "job-rollback",
        "status": "compared-editions",
        "comparisonUnit": "complete-audio-edition",
        "editions": [
            {
                "editionKey": "single:new.m4b",
                "label": "Complete M4B",
                "multiFile": False,
                "fileCount": 1,
                "extension": ".m4b",
                "archiveFormat": "M4B",
                "archiveSource": "original",
                "files": [
                    {
                        "sourceName": "new.m4b",
                        "sourceUrl": "https://example.invalid/new.m4b",
                        "archiveFormat": "M4B",
                        "archiveSource": "original",
                        "providerClaimedLossless": False,
                        "expectedSize": winner.stat().st_size,
                        "actualSize": winner.stat().st_size,
                        "signature": "ISO-BMFF/M4B",
                        "sha256": _sha(winner),
                        "comparisonPath": str(winner),
                    }
                ],
            }
        ],
        "recommendedEditionKey": "single:new.m4b",
        "recommendedLabel": "Complete M4B",
        "recommendedMultiFile": False,
        "recommendedFileCount": 1,
        "finalLibraryModified": False,
    }
    (run / "comparison-report.json").write_text(
        json.dumps(comparison), encoding="utf-8"
    )

    def fail_after_install(_path: Path):
        raise RuntimeError("forced post-install failure")

    monkeypatch.setattr(
        "mnemosyne.adoption.inspect_actual_quality",
        fail_after_install,
    )

    real_copy2 = adoption_module.shutil.copy2

    def fail_old_media_restore(src, dst, *args, **kwargs):
        src_path = Path(src)
        dst_path = Path(dst)
        if (
            src_path.parent.name == "audio"
            and src_path.parent.parent.name.endswith("-pre-edition-adoption")
            and dst_path == current
        ):
            raise OSError("simulated rollback restore failure")
        return real_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(
        "mnemosyne.adoption.shutil.copy2",
        fail_old_media_restore,
    )

    with pytest.raises(
        AdoptionError,
        match="rollback was incomplete.*Manual recovery is required",
    ) as caught:
        adopt_latest_recommended_edition(job)

    message = str(caught.value)
    assert "simulated rollback restore failure" in message
    assert "pre-edition-adoption" in message

    rollback_dirs = list((job / "rollback").glob("*-pre-edition-adoption"))
    assert len(rollback_dirs) == 1
    assert (rollback_dirs[0] / "audio" / current.name).is_file()
