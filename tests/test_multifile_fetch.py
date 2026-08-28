from pathlib import Path

from mnemosyne.fetcher import _canonical_chapter_name, _chapter_number
from mnemosyne.models import CandidateKind, MediaCandidate


def _candidate(name):
    return MediaCandidate(
        name=name,
        url="https://example.invalid/audio.mp3",
        extension=".mp3",
        archive_format="VBR MP3",
        source="original",
        size=123,
        kind=CandidateKind.AUDIO,
        playable=True,
        score=740,
    )


def test_chapter_number_from_archive_filename():
    candidate = _candidate("edisonsconquestofmars_07_serviss.mp3")
    assert _chapter_number(candidate, 1) == 7


def test_chapter_name_is_deterministic():
    candidate = _candidate("edisonsconquestofmars_07_serviss.mp3")
    assert _canonical_chapter_name(candidate, 1) == "07 - Chapter 07.mp3"
