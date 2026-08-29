import mnemosyne.quality as quality_module
from mnemosyne.quality import (
    ActualAudioQuality,
    inspect_actual_quality,
    provider_quality_mismatch,
)


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



def test_inspect_actual_quality_parser_failure_is_reported_not_raised(
    tmp_path,
    monkeypatch,
) -> None:
    sample = tmp_path / "broken.m4b"
    sample.write_bytes(b"\x00\x00\x00\x18ftypM4B ")

    def explode(_path):
        raise RuntimeError("simulated mutagen parser failure")

    monkeypatch.setattr(quality_module, "MutagenFile", explode)

    actual = inspect_actual_quality(sample)

    assert actual.codec is None
    assert actual.lossless is None
    assert actual.bitrate_bps is None
    assert actual.sample_rate_hz is None
    assert actual.channels is None
    assert actual.inspection_warning is not None
    assert "simulated mutagen parser failure" in actual.inspection_warning
