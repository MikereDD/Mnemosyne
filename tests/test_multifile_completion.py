from pathlib import Path

from mnemosyne.multifile_completion import (
    MultiCompletionPreview,
    MultiCleanupPreview,
    _edition_sha,
)


def test_edition_hash_order_sensitive() -> None:
    assert _edition_sha(["a", "b"]) != _edition_sha(["b", "a"])


def test_completion_preview_shape() -> None:
    value = MultiCompletionPreview(
        job_dir=Path("job"),
        destination=Path("dest"),
        audio_paths=(Path("1.mp3"), Path("2.mp3")),
        cover_path=Path("cover.jpg"),
        checks=(),
        edition_sha256="edition",
        ready_to_complete=True,
    )
    assert len(value.audio_paths) == 2


def test_cleanup_preview_shape() -> None:
    value = MultiCleanupPreview(
        job_dir=Path("job"),
        job_id="job-1",
        final_destination=Path("dest"),
        audio_paths=(Path("1.mp3"),),
        final_cover=Path("cover.jpg"),
        edition_sha256="edition",
        receipt_path=Path("receipt.json"),
        staging_size_bytes=100,
        file_count=5,
    )
    assert value.job_id == "job-1"


def test_edition_hash_repeatable() -> None:
    assert _edition_sha(["a", "b"]) == _edition_sha(["a", "b"])
