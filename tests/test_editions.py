from mnemosyne.editions import choose_audio_edition, discover_audio_editions
from mnemosyne.models import CandidateKind, MediaCandidate


def _candidate(name, ext, fmt, source="original", size=100, score=740):
    return MediaCandidate(
        name=name,
        url=f"https://example.invalid/{name}",
        extension=ext,
        archive_format=fmt,
        source=source,
        size=size,
        kind=CandidateKind.AUDIO,
        playable=True,
        score=score,
    )


def test_groups_numbered_mp3_files_into_one_edition():
    candidates = [
        _candidate(f"book_{i:02d}_author.mp3", ".mp3", "VBR MP3")
        for i in range(1, 5)
    ]
    editions = discover_audio_editions(candidates)
    assert len(editions) == 1
    edition = editions[0]
    assert edition.multi_file is True
    assert len(edition.candidates) == 4
    assert edition.sequence_numbers == [1, 2, 3, 4]


def test_keeps_complete_m4b_as_single_file_edition():
    candidates = [
        _candidate("book.m4b", ".m4b", "Audiobook", size=1000, score=910),
        *[
            _candidate(f"book_{i:02d}_author.mp3", ".mp3", "VBR MP3")
            for i in range(1, 4)
        ],
    ]
    editions = discover_audio_editions(candidates)
    assert editions[0].extension == ".m4b"
    assert editions[0].multi_file is False


def test_audio_format_override_selects_mp3_chapter_set():
    candidates = [
        _candidate("book.m4b", ".m4b", "Audiobook", size=1000, score=910),
        *[
            _candidate(f"book_{i:02d}_author.mp3", ".mp3", "VBR MP3")
            for i in range(1, 4)
        ],
    ]
    editions = discover_audio_editions(candidates)
    chosen = choose_audio_edition(editions, preferred_format="mp3")
    assert chosen is not None
    assert chosen.extension == ".mp3"
    assert chosen.multi_file is True
    assert len(chosen.candidates) == 3


def test_derivative_64kbps_set_stays_separate_from_vbr_originals():
    candidates = [
        *[
            _candidate(f"book_{i:02d}_author.mp3", ".mp3", "VBR MP3", source="original", score=740)
            for i in range(1, 4)
        ],
        *[
            _candidate(f"book_{i:02d}_author_64kb.mp3", ".mp3", "64Kbps MP3", source="derivative", score=628)
            for i in range(1, 4)
        ],
    ]
    editions = discover_audio_editions(candidates)
    assert len(editions) == 2
    assert {edition.archive_format for edition in editions} == {"VBR MP3", "64Kbps MP3"}
