from pathlib import Path
from mnemosyne.multifile_metadata import _edition_sha, _fallback_title

def test_fallback_title_strips_track_prefix() -> None:
    assert _fallback_title(Path("01 - Chapter 01.mp3")) == "Chapter 01"

def test_edition_hash_is_order_sensitive() -> None:
    assert _edition_sha(["abc", "def"]) != _edition_sha(["def", "abc"])

def test_edition_hash_is_repeatable() -> None:
    assert _edition_sha(["abc", "def"]) == _edition_sha(["abc", "def"])
