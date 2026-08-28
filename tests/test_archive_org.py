from mnemosyne.models import CandidateKind
from mnemosyne.providers.archive_org import ArchiveOrgProvider


def test_identifier_from_url() -> None:
    assert (
        ArchiveOrgProvider.identifier_from_url(
            "https://archive.org/details/animal-farm.sna"
        )
        == "animal-farm.sna"
    )


def test_title_cleanup_uses_external_hostname() -> None:
    assert (
        ArchiveOrgProvider._clean_title(
            "Animal Farm - sachnoi.app",
            "https://sachnoi.app/play/animal-farm",
        )
        == "Animal Farm"
    )


def test_afpk_is_not_playable_audio() -> None:
    candidate = ArchiveOrgProvider._candidate(
        "animal-farm.sna",
        {
            "name": "Animal Farm.afpk",
            "format": "Columbia Peaks",
            "source": "derivative",
        },
    )
    assert candidate.kind is CandidateKind.AUXILIARY
    assert candidate.playable is False


def test_lossless_original_outranks_mp3_derivative() -> None:
    lossless = ArchiveOrgProvider._candidate(
        "animal-farm.sna",
        {
            "name": "Animal Farm.m4a",
            "format": "Apple Lossless Audio",
            "source": "original",
            "size": "200000000",
        },
    )
    mp3 = ArchiveOrgProvider._candidate(
        "animal-farm.sna",
        {
            "name": "Animal Farm.mp3",
            "format": "VBR MP3",
            "source": "derivative",
            "size": "80000000",
            "bitrate": "192",
        },
    )
    assert lossless.playable
    assert lossless.lossless
    assert lossless.score > mp3.score
