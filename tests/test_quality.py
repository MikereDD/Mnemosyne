from mnemosyne.quality import ActualAudioQuality, provider_quality_mismatch


def test_false_lossless_claim_is_flagged() -> None:
    actual = ActualAudioQuality(
        codec="mp4a.40.2",
        lossless=False,
        bitrate_bps=125600,
        sample_rate_hz=44100,
        channels=2,
    )
    warning = provider_quality_mismatch(
        provider_claimed_lossless=True,
        actual=actual,
    )
    assert warning is not None
    assert "claimed lossless" in warning
    assert "mp4a.40.2" in warning


def test_verified_lossless_does_not_warn() -> None:
    actual = ActualAudioQuality(
        codec="alac",
        lossless=True,
        bitrate_bps=600000,
        sample_rate_hz=44100,
        channels=2,
    )
    assert (
        provider_quality_mismatch(
            provider_claimed_lossless=True,
            actual=actual,
        )
        is None
    )
