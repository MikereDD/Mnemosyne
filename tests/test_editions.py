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


def test_groups_nested_disc_side_flacs_into_one_ordered_edition():
    candidates = [
        _candidate(
            "disc1/01.01. Book IX - The Sacking Of The Kilkonians.flac",
            ".flac", "24bit Flac", size=471, score=1320,
        ),
        _candidate(
            "disc1/02.01. Book IX - The Escape From Polyphemos.flac",
            ".flac", "24bit Flac", size=473, score=1320,
        ),
        _candidate(
            "disc1/02.02. Book X - Aiolos And The Bag Of Winds.flac",
            ".flac", "24bit Flac", size=401, score=1320,
        ),
        _candidate(
            "disc2/03.01. Book X - Circe's Island.flac",
            ".flac", "24bit Flac", size=432, score=1320,
        ),
        _candidate(
            "disc2/04.01. Book XI - The Land Of The Dead.flac",
            ".flac", "24bit Flac", size=474, score=1320,
        ),
        _candidate(
            "disc3/05.01. Book XI - Odysseus Speaks To The Dead.flac",
            ".flac", "24bit Flac", size=378, score=1320,
        ),
    ]

    editions = discover_audio_editions(candidates)

    assert len(editions) == 1
    edition = editions[0]
    assert edition.multi_file is True
    assert edition.extension == ".flac"
    assert edition.label == "FLAC disc set (6 files)"
    assert edition.sequence_numbers == [1, 2, 3, 4, 5, 6]
    assert [candidate.name for candidate in edition.candidates] == [
        candidate.name for candidate in candidates
    ]
    assert edition.total_size == sum(candidate.size for candidate in candidates)


def test_disc_aware_grouping_keeps_formats_separate():
    candidates = [
        _candidate(
            f"disc{disc}/{track:02d}.01. Part {track}.flac",
            ".flac", "24bit Flac", score=1320,
        )
        for disc, track in [(1, 1), (2, 2)]
    ] + [
        _candidate(
            f"disc{disc}/{track:02d}.01. Part {track}.opus",
            ".opus", "Unknown", score=840,
        )
        for disc, track in [(1, 1), (2, 2)]
    ]

    editions = discover_audio_editions(candidates)

    assert len(editions) == 2
    assert {edition.extension for edition in editions} == {".flac", ".opus"}
    assert all(edition.multi_file for edition in editions)
    assert all(len(edition.candidates) == 2 for edition in editions)


def test_disc_aware_grouping_requires_multiple_explicit_discs():
    candidates = [
        _candidate(
            "disc1/01.01. First Part.flac",
            ".flac", "24bit Flac", score=1320,
        ),
        _candidate(
            "disc1/02.01. Second Part.flac",
            ".flac", "24bit Flac", score=1320,
        ),
    ]

    editions = discover_audio_editions(candidates)

    assert len(editions) == 2
    assert all(not edition.multi_file for edition in editions)


def test_disc_aware_grouping_rejects_duplicate_ordering_identity():
    candidates = [
        _candidate(
            "disc1/01.01. First Copy.flac",
            ".flac", "24bit Flac", score=1320,
        ),
        _candidate(
            "disc2/01.01. Second Disc.flac",
            ".flac", "24bit Flac", score=1320,
        ),
        _candidate(
            "disc2/01.01. Duplicate Second Disc.flac",
            ".flac", "24bit Flac", score=1320,
        ),
    ]

    editions = discover_audio_editions(candidates)

    assert len(editions) == 3
    assert all(not edition.multi_file for edition in editions)

def test_groups_flat_archive_disc_side_flacs_into_one_ordered_edition():
    candidates = [
        _candidate(
            f"lp_the-odyssey_homer-anthony-quayle_disc{disc}side{side}.flac",
            ".flac",
            "24bit Flac",
            size=size,
            score=1320,
        )
        for disc, side, size in [
            (1, 1, 471),
            (1, 2, 401),
            (2, 1, 432),
            (2, 2, 378),
            (3, 1, 473),
            (3, 2, 474),
        ]
    ]

    editions = discover_audio_editions(candidates)

    assert len(editions) == 1
    edition = editions[0]
    assert edition.multi_file is True
    assert edition.extension == ".flac"
    assert edition.label == "FLAC disc set (6 files)"
    assert edition.sequence_numbers == [1, 2, 3, 4, 5, 6]
    assert [candidate.name for candidate in edition.candidates] == [
        candidate.name for candidate in candidates
    ]

