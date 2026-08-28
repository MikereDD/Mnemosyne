from pathlib import Path
from mnemosyne.multifile_placement import (
    MultiFilePlacementPreview,
    MultiFilePlacementResult,
    _edition_sha,
)

def test_edition_hash_order_sensitive():
    assert _edition_sha(["a", "b"]) != _edition_sha(["b", "a"])

def test_edition_hash_repeatable():
    assert _edition_sha(["a", "b"]) == _edition_sha(["a", "b"])

def test_preview_shape():
    value = MultiFilePlacementPreview(
        Path("job"), Path("dest"),
        (Path("1.mp3"), Path("2.mp3")),
        Path("cover.jpg"), ("a", "b"), "e", "c"
    )
    assert len(value.audio_sources) == 2

def test_result_shape():
    value = MultiFilePlacementResult(
        Path("job"), Path("dest"), (Path("dest/1.mp3"),),
        Path("dest/cover.jpg"), "e", "c",
        Path("placement-report.json"), Path("fetch-report.json")
    )
    assert value.destination == Path("dest")
