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


def test_epub_is_classified_as_ebook() -> None:
    candidate = ArchiveOrgProvider._candidate(
        "example-book",
        {
            "name": "Example Book.epub",
            "format": "EPUB",
            "source": "original",
            "size": "1234567",
        },
    )

    assert candidate.kind is CandidateKind.EBOOK
    assert candidate.playable is False
    assert candidate.score > 0
    assert "eBook format" in candidate.reasons
    assert "Archive original" in candidate.reasons


def test_epub_original_outranks_pdf_derivative() -> None:
    epub = ArchiveOrgProvider._candidate(
        "example-book",
        {
            "name": "Example Book.epub",
            "format": "EPUB",
            "source": "original",
            "size": "1234567",
        },
    )
    pdf = ArchiveOrgProvider._candidate(
        "example-book",
        {
            "name": "Example Book.pdf",
            "format": "Text PDF",
            "source": "derivative",
            "size": "3456789",
        },
    )

    assert epub.kind is CandidateKind.EBOOK
    assert pdf.kind is CandidateKind.EBOOK
    assert epub.score > pdf.score


def test_archive_metadata_xml_remains_auxiliary_not_ebook() -> None:
    candidate = ArchiveOrgProvider._candidate(
        "example-book",
        {
            "name": "example-book_meta.xml",
            "format": "Metadata",
            "source": "original",
        },
    )

    assert candidate.kind is CandidateKind.AUXILIARY
