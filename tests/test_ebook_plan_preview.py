import re
from pathlib import Path

from typer.testing import CliRunner
import mnemosyne.cli as cli_module
from mnemosyne.cli import app
from mnemosyne.models import (
    AcquisitionPlan,
    ArchiveItem,
    CandidateKind,
    EbookEdition,
    MediaCandidate,
    MediaType,
)
from mnemosyne.render import console, render_plan

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def candidate(name, extension, score, source, size):
    return MediaCandidate(
        name=name,
        url=f"https://example.invalid/{name}",
        extension=extension,
        archive_format=extension.lstrip(".").upper(),
        source=source,
        size=size,
        kind=CandidateKind.EBOOK,
        score=score,
    )

def sample_plan():
    epub = candidate("Example Book.epub", ".epub", 1020, "original", 2000000)
    pdf = candidate("Example Book.pdf", ".pdf", 700, "derivative", 5000000)
    item = ArchiveItem(
        identifier="example-book",
        source_url="https://archive.org/details/example-book",
        media_type=MediaType.EBOOK,
        raw_title="Example Book",
        title="Example Book",
        creator="Example Author",
        year=2024,
        candidates=[epub, pdf],
    )
    editions = [
        EbookEdition(
            key="ebook:Example Book.epub",
            label="EPUB",
            extension=".epub",
            archive_format="EPUB",
            source="original",
            candidate=epub,
            score=1020,
            size=2000000,
        ),
        EbookEdition(
            key="ebook:Example Book.pdf",
            label="PDF",
            extension=".pdf",
            archive_format="PDF",
            source="derivative",
            candidate=pdf,
            score=700,
            size=5000000,
        ),
    ]
    return AcquisitionPlan(
        item=item,
        destination=Path("library") / "Example Author" / "Example Book",
        selected_ebook=epub,
        ebook_editions=editions,
        selected_ebook_edition_key=editions[0].key,
    )

def test_render_plan_shows_selected_ebook_and_alternatives():
    with console.capture() as capture:
        render_plan(sample_plan())
    output = ANSI.sub("", capture.get())
    assert "eBook editions" in output
    assert "Example Book.epub" in output
    assert "Example Book.pdf" in output
    assert "Selected eBook:" in output
    assert "score 1020" in output
    assert "Preview only. Media downloads started: NO" in output
    assert "Library modified: NO" in output

def test_plan_help_exposes_ebook_format_preference():
    result = runner.invoke(app, ["plan", "--help"])
    assert result.exit_code == 0
    output = ANSI.sub("", result.stdout)
    assert "--ebook-format" in output
    assert "preview only" in output.lower()

def test_plan_forwards_ebook_format_to_builder(monkeypatch):
    captured = {}
    plan = sample_plan()

    def fake_build_plan(media_type, url, **kwargs):
        captured["media_type"] = media_type
        captured["url"] = url
        captured.update(kwargs)
        return object(), plan

    monkeypatch.setattr(cli_module, "_build_plan", fake_build_plan)
    monkeypatch.setattr(cli_module, "render_plan", lambda value: None)

    result = runner.invoke(
        app,
        [
            "plan",
            "ebook",
            "https://archive.org/details/example-book",
            "--ebook-format",
            "pdf",
        ],
    )
    assert result.exit_code == 0
    assert captured["ebook_format"] == "pdf"
    assert captured["audio_format"] is None
