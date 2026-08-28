from pathlib import Path
from mnemosyne.multifile_metadata import (
    _edition_sha,
    _fallback_title,
    _source_identity_title,
    _track_tags,
)

def test_fallback_title_strips_track_prefix() -> None:
    assert _fallback_title(Path("01 - Chapter 01.mp3")) == "Chapter 01"

def test_edition_hash_is_order_sensitive() -> None:
    assert _edition_sha(["abc", "def"]) != _edition_sha(["def", "abc"])

def test_edition_hash_is_repeatable() -> None:
    assert _edition_sha(["abc", "def"]) == _edition_sha(["abc", "def"])

def test_multifile_track_tags_can_include_generic_track_number() -> None:
    tags = {"title": "Chapter 01", "track": "1/6"}
    assert tags["track"] == "1/6"

def test_source_identity_title_recognizes_flat_disc_side_name() -> None:
    assert (
        _source_identity_title(
            "disc2/lp_the-odyssey_homer-anthony-quayle_disc2side1.flac"
        )
        == "Disc 2 Side 1"
    )


def test_source_identity_title_refuses_unproven_structure() -> None:
    assert _source_identity_title("01 - Chapter 01.flac") is None

