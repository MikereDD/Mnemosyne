from mnemosyne.comparison import _actual_quality_score
from mnemosyne.models import CandidateKind, MediaCandidate
from mnemosyne.quality import ActualAudioQuality


def candidate(source: str) -> MediaCandidate:
    return MediaCandidate(
        name="test.m4a",
        url="https://example.invalid/test",
        extension=".m4a",
        source=source,
        kind=CandidateKind.AUDIO,
        playable=True,
    )


def test_lossless_beats_lossy() -> None:
    lossless = ActualAudioQuality(
        codec="alac",
        lossless=True,
        bitrate_bps=500000,
        sample_rate_hz=44100,
        channels=2,
    )
    lossy = ActualAudioQuality(
        codec="mp4a.40.2",
        lossless=False,
        bitrate_bps=320000,
        sample_rate_hz=44100,
        channels=2,
    )
    assert _actual_quality_score(candidate("derivative"), lossless) > _actual_quality_score(candidate("original"), lossy)


def test_materially_better_derivative_beats_original() -> None:
    original = ActualAudioQuality(
        codec="MP3",
        lossless=False,
        bitrate_bps=64000,
        sample_rate_hz=44100,
        channels=2,
    )
    derivative = ActualAudioQuality(
        codec="MP3",
        lossless=False,
        bitrate_bps=320000,
        sample_rate_hz=44100,
        channels=2,
    )
    assert _actual_quality_score(candidate("derivative"), derivative) > _actual_quality_score(candidate("original"), original)


def test_original_breaks_close_lossy_tie() -> None:
    actual = ActualAudioQuality(
        codec="mp4a.40.2",
        lossless=False,
        bitrate_bps=128000,
        sample_rate_hz=44100,
        channels=2,
    )
    assert _actual_quality_score(candidate("original"), actual) > _actual_quality_score(candidate("derivative"), actual)
