from mnemosyne.comparison import _actual_quality_score, _edition_quality_score
from mnemosyne.models import AudioEdition, CandidateKind, MediaCandidate
from mnemosyne.quality import ActualAudioQuality


def candidate(source: str, name: str = "test.m4a") -> MediaCandidate:
    return MediaCandidate(
        name=name,
        url="https://example.invalid/test",
        extension=".m4a",
        source=source,
        kind=CandidateKind.AUDIO,
        playable=True,
    )


def test_lossless_beats_lossy() -> None:
    lossless = ActualAudioQuality("alac", True, 500000, 44100, 2)
    lossy = ActualAudioQuality("aac", False, 320000, 44100, 2)
    assert _actual_quality_score(candidate("derivative"), lossless) > _actual_quality_score(candidate("original"), lossy)


def test_materially_better_derivative_beats_original() -> None:
    original = ActualAudioQuality("MP3", False, 64000, 44100, 2)
    derivative = ActualAudioQuality("MP3", False, 320000, 44100, 2)
    assert _actual_quality_score(candidate("derivative"), derivative) > _actual_quality_score(candidate("original"), original)


def test_original_breaks_close_lossy_tie() -> None:
    actual = ActualAudioQuality("aac", False, 128000, 44100, 2)
    assert _actual_quality_score(candidate("original"), actual) > _actual_quality_score(candidate("derivative"), actual)


def test_multi_file_edition_uses_median_bitrate_not_best_chapter() -> None:
    edition = AudioEdition(
        key="set:mp3",
        label="MP3 chapter set (3 files)",
        extension=".mp3",
        source="original",
        candidates=[
            candidate("original", "01.mp3"),
            candidate("original", "02.mp3"),
            candidate("original", "03.mp3"),
        ],
        multi_file=True,
    )

    from types import SimpleNamespace
    files = [
        SimpleNamespace(actual=ActualAudioQuality("MP3", False, 64000, 44100, 1)),
        SimpleNamespace(actual=ActualAudioQuality("MP3", False, 64000, 44100, 1)),
        SimpleNamespace(actual=ActualAudioQuality("MP3", False, 320000, 44100, 1)),
    ]

    score, representative = _edition_quality_score(edition, files)
    assert representative.bitrate_bps == 64000
    assert representative.lossless is False
    assert score > 0
