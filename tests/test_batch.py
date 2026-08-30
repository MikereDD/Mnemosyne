from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mnemosyne.batch import (
    BatchQueueError,
    build_batch_execution_preview,
    execute_batch_fetches,
    fetch_queue_path,
    parse_fetch_queue,
    resolve_batch_plans,
)
from mnemosyne.models import (
    ArchiveItem,
    CandidateKind,
    MediaCandidate,
    MediaType,
)
from mnemosyne.providers.base import ProviderError


def _write_queue(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.mark.parametrize(
    ("media_type", "filename"),
    [
        (MediaType.AUDIOBOOK, "audiobook-links.txt"),
        (MediaType.EBOOK, "ebook-links.txt"),
        (MediaType.MUSIC, "music-links.txt"),
    ],
)
def test_fetch_queue_path_uses_canonical_filename(
    tmp_path: Path,
    media_type: MediaType,
    filename: str,
) -> None:
    assert fetch_queue_path(media_type, root=tmp_path) == tmp_path / "fetch" / filename


def test_parse_queue_preserves_order_and_source_line_numbers(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "# first batch\n"
        "\n"
        "https://archive.org/details/animal-farm.sna\n"
        "https://www.archive.org/details/edisons_conquest_of_mars_0806_librivox/\n",
    )

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.total_lines == 4
    assert preview.comment_lines == 1
    assert preview.blank_lines == 1
    assert [item.line_number for item in preview.items] == [3, 4]
    assert [item.identifier for item in preview.items] == [
        "animal-farm.sna",
        "edisons_conquest_of_mars_0806_librivox",
    ]


def test_parse_queue_canonicalizes_archive_urls_for_duplicate_detection(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "http://www.archive.org/details/animal-farm.sna?view=theater\n"
        "https://archive.org/details/animal-farm.sna/\n",
    )

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.ready_count == 1
    assert preview.items[0].canonical_url == (
        "https://archive.org/details/animal-farm.sna"
    )
    assert preview.duplicate_count == 1
    assert preview.duplicates[0].line_number == 2
    assert preview.duplicates[0].detail == "Duplicates line 1."


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://archive.org/details/test", "http or https"),
        ("https://example.com/details/test", "Unsupported provider"),
        ("https://archive.org/download/test", "/details/<identifier>"),
        ("https://archive.org/details/", "/details/<identifier>"),
        ("https://archive.org/details/bad%20identifier", "unsupported characters"),
    ],
)
def test_parse_queue_classifies_invalid_entries(
    tmp_path: Path,
    url: str,
    message: str,
) -> None:
    queue = _write_queue(tmp_path / "audiobook-links.txt", url + "\n")

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.ready_count == 0
    assert preview.invalid_count == 1
    assert preview.invalid[0].line_number == 1
    assert message in preview.invalid[0].detail


def test_parse_queue_accepts_verified_year_directive(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/edison | year=1898\n",
    )

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.ready_count == 1
    assert preview.invalid_count == 0
    assert preview.items[0].source_url == "https://archive.org/details/edison"
    assert preview.items[0].verified_year == 1898


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ("https://archive.org/details/test | year=98", "exactly four decimal digits"),
        ("https://archive.org/details/test | title=Example", "Unsupported queue directive"),
        (
            "https://archive.org/details/test | year=1898 | year=1899",
            "specified more than once",
        ),
        ("https://archive.org/details/test |", "empty directive"),
    ],
)
def test_parse_queue_rejects_invalid_directives(
    tmp_path: Path,
    entry: str,
    message: str,
) -> None:
    queue = _write_queue(tmp_path / "audiobook-links.txt", entry + "\n")

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.ready_count == 0
    assert preview.invalid_count == 1
    assert message in preview.invalid[0].detail


def test_duplicate_detection_uses_canonical_url_not_directives(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/edison | year=1898\n"
        "https://www.archive.org/details/edison/ | year=2008\n",
    )

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert preview.ready_count == 1
    assert preview.items[0].verified_year == 1898
    assert preview.duplicate_count == 1
    assert preview.duplicates[0].detail == "Duplicates line 1."


def test_parse_queue_is_read_only(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "# keep this exactly\n"
        "https://archive.org/details/animal-farm.sna\n"
        "https://archive.org/details/animal-farm.sna\n",
    )
    before = queue.read_bytes()

    parse_fetch_queue(MediaType.AUDIOBOOK, queue)

    assert queue.read_bytes() == before


def test_parse_queue_missing_file_fails_safely(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"

    with pytest.raises(BatchQueueError, match="does not exist"):
        parse_fetch_queue(MediaType.AUDIOBOOK, missing)



class _FakeProvider:
    def __init__(self, items: dict[str, ArchiveItem | Exception]) -> None:
        self.items = items
        self.calls: list[str] = []

    def identify(
        self,
        url: str,
        media_type: MediaType,
        *,
        year_override: int | None = None,
    ) -> ArchiveItem:
        self.calls.append(url)
        result = self.items[url]

        if isinstance(result, Exception):
            raise result

        assert result.media_type is media_type

        if year_override is not None:
            result = result.model_copy(update={"year": year_override})

        return result

def _archive_item(
    identifier: str,
    *,
    creator: str | None = "Example Author",
    year: int | None = 2026,
    with_audio: bool = True,
    with_cover: bool = True,
) -> ArchiveItem:
    candidates: list[MediaCandidate] = []
    if with_audio:
        candidates.append(
            MediaCandidate(
                name=f"{identifier}.mp3",
                url=f"https://archive.org/download/{identifier}/{identifier}.mp3",
                extension=".mp3",
                archive_format="VBR MP3",
                source="original",
                size=1000,
                kind=CandidateKind.AUDIO,
                playable=True,
                score=700,
            )
        )
    if with_cover:
        candidates.append(
            MediaCandidate(
                name="cover.jpg",
                url=f"https://archive.org/download/{identifier}/cover.jpg",
                extension=".jpg",
                archive_format="JPEG",
                source="original",
                size=500,
                kind=CandidateKind.COVER,
                playable=False,
                score=100,
            )
        )
    return ArchiveItem(
        identifier=identifier,
        source_url=f"https://archive.org/details/{identifier}",
        media_type=MediaType.AUDIOBOOK,
        raw_title=f"Raw {identifier}",
        title=f"Title {identifier}",
        creator=creator,
        year=year,
        candidates=candidates,
    )


def test_resolve_batch_plans_keeps_item_failures_isolated(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/good\n"
        "https://archive.org/details/blocked\n"
        "https://archive.org/details/failed\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/good": _archive_item("good"),
            "https://archive.org/details/blocked": _archive_item("blocked", year=None),
            "https://archive.org/details/failed": ProviderError("metadata unavailable"),
        }
    )

    result = resolve_batch_plans(preview, tmp_path / "library", provider)

    assert [item.status for item in result.items] == [
        "blocked",
        "blocked",
        "failed",
    ]
    assert result.actionable_count == 0
    assert result.blocked_count == 2
    assert result.failed_count == 1
    assert result.items[1].warning_count == 1
    assert "year was not identified" in result.items[1].warnings[0]
    assert result.items[2].error == "metadata unavailable"


def test_resolve_batch_plans_reports_selected_edition_and_destination(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/good | year=1926\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {"https://archive.org/details/good": _archive_item("good")}
    )

    result = resolve_batch_plans(
        preview,
        tmp_path / "library",
        provider,
    )

    item = result.items[0]
    assert item.status == "actionable"
    assert item.title == "Title good"
    assert item.creator == "Example Author"
    assert item.year == 1926
    assert item.year_provenance == "verified-queue"
    assert item.audio_file_count == 1
    assert item.selected_edition is not None
    assert item.destination is not None
    assert "Example Author" in str(item.destination)
    assert "Title good" in str(item.destination)


def test_resolve_batch_plans_does_not_modify_queue(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/good\n",
    )
    before = queue.read_bytes()
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {"https://archive.org/details/good": _archive_item("good")}
    )

    resolve_batch_plans(preview, tmp_path / "library", provider)

    assert queue.read_bytes() == before



def test_provider_year_does_not_make_audiobook_batch_plan_actionable(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/recording-year-not-publication-year\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/recording-year-not-publication-year":
                _archive_item(
                    "recording-year-not-publication-year",
                    year=2008,
                )
        }
    )

    result = resolve_batch_plans(
        preview,
        tmp_path / "library",
        provider,
    )

    item = result.items[0]
    assert item.status == "blocked"
    assert item.year == 2008
    assert item.year_provenance == "provider"
    assert any(
        "provider-derived" in warning and "verified" in warning
        for warning in item.warnings
    )


def test_verified_year_override_clears_batch_year_provenance_gate(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/edison | year=1898\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/edison":
                _archive_item("edison", year=2008)
        }
    )

    result = resolve_batch_plans(
        preview,
        tmp_path / "library",
        provider,
    )

    item = result.items[0]
    assert item.status == "actionable"
    assert item.year == 1898
    assert item.year_provenance == "verified-queue"
    assert not any("provider-derived" in warning for warning in item.warnings)



def test_conflicting_verified_year_sources_fail_closed(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/edison | year=1898\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/edison":
                _archive_item("edison", year=2008)
        }
    )

    result = resolve_batch_plans(
        preview,
        tmp_path / "library",
        provider,
        verified_year_overrides={"edison": 2008},
    )

    item = result.items[0]
    assert item.status == "failed"
    assert item.year_provenance == "conflict"
    assert item.error is not None
    assert "queue=1898" in item.error
    assert "override=2008" in item.error
    assert provider.calls == []



def test_execution_preview_preserves_actionable_order_and_skips_others(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/first | year=1901\n"
        "https://archive.org/details/blocked\n"
        "https://archive.org/details/third | year=1903\n"
        "https://archive.org/details/failed\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/first": _archive_item("first", year=2001),
            "https://archive.org/details/blocked": _archive_item("blocked", year=None),
            "https://archive.org/details/third": _archive_item("third", year=2003),
            "https://archive.org/details/failed": ProviderError("metadata unavailable"),
        }
    )

    plans = resolve_batch_plans(preview, tmp_path / "library", provider)
    execution = build_batch_execution_preview(plans)

    assert [item.action for item in execution.items] == [
        "execute",
        "skip-blocked",
        "execute",
        "skip-failed",
    ]
    assert [item.sequence for item in execution.items] == [1, 0, 2, 0]
    assert execution.execute_count == 2
    assert execution.blocked_count == 1
    assert execution.failed_count == 1
    assert [item.identifier for item in execution.items if item.action == "execute"] == [
        "first",
        "third",
    ]


def test_execution_preview_carries_destination_for_actionable_items(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/edison | year=1898\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/edison":
                _archive_item("edison", year=2008)
        }
    )

    plans = resolve_batch_plans(preview, tmp_path / "library", provider)
    execution = build_batch_execution_preview(plans)

    item = execution.items[0]
    assert item.action == "execute"
    assert item.sequence == 1
    assert item.destination is not None
    assert "1898" in str(item.destination)


def test_execution_preview_is_read_only(tmp_path: Path) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/edison | year=1898\n",
    )
    before = queue.read_bytes()
    library = tmp_path / "library"
    provider = _FakeProvider(
        {
            "https://archive.org/details/edison":
                _archive_item("edison", year=2008)
        }
    )

    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    plans = resolve_batch_plans(preview, library, provider)
    build_batch_execution_preview(plans)

    assert queue.read_bytes() == before
    assert not library.exists()



def test_batch_fetch_executes_only_actionable_items_in_order(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/first | year=1901\n"
        "https://archive.org/details/blocked\n"
        "https://archive.org/details/third | year=1903\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/first": _archive_item("first", year=2001),
            "https://archive.org/details/blocked": _archive_item("blocked", year=None),
            "https://archive.org/details/third": _archive_item("third", year=2003),
        }
    )
    plans = resolve_batch_plans(preview, tmp_path / "library", provider)
    execution = build_batch_execution_preview(plans)
    calls: list[str] = []

    def fake_fetcher(plan, staging_root):
        calls.append(plan.item.identifier)
        return SimpleNamespace(
            job_id=f"{plan.item.identifier}-job",
            staging_dir=staging_root / f"{plan.item.identifier}-job",
            warnings=(),
        )

    summary = execute_batch_fetches(
        execution,
        tmp_path / "staging",
        fetcher=fake_fetcher,
    )

    assert calls == ["first", "third"]
    assert [item.status for item in summary.items] == [
        "staged",
        "blocked",
        "staged",
    ]
    assert summary.staged_count == 2
    assert summary.blocked_count == 1
    assert summary.failed_count == 0


def test_batch_fetch_failure_is_isolated_and_processing_continues(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/first | year=1901\n"
        "https://archive.org/details/second | year=1902\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/first": _archive_item("first"),
            "https://archive.org/details/second": _archive_item("second"),
        }
    )
    plans = resolve_batch_plans(preview, tmp_path / "library", provider)
    execution = build_batch_execution_preview(plans)
    calls: list[str] = []

    def fake_fetcher(plan, staging_root):
        calls.append(plan.item.identifier)
        if plan.item.identifier == "first":
            raise OSError("simulated staging failure")
        return SimpleNamespace(
            job_id="second-job",
            staging_dir=staging_root / "second-job",
            warnings=(),
        )

    summary = execute_batch_fetches(
        execution,
        tmp_path / "staging",
        fetcher=fake_fetcher,
    )

    assert calls == ["first", "second"]
    assert [item.status for item in summary.items] == ["failed", "staged"]
    assert summary.failed_count == 1
    assert summary.staged_count == 1
    assert "simulated staging failure" in (summary.items[0].error or "")


def test_batch_fetch_skips_plan_failures_without_calling_fetcher(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/good | year=1901\n"
        "https://archive.org/details/bad\n",
    )
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/good": _archive_item("good"),
            "https://archive.org/details/bad": ProviderError("metadata unavailable"),
        }
    )
    plans = resolve_batch_plans(preview, tmp_path / "library", provider)
    execution = build_batch_execution_preview(plans)
    calls: list[str] = []

    def fake_fetcher(plan, staging_root):
        calls.append(plan.item.identifier)
        return SimpleNamespace(
            job_id="good-job",
            staging_dir=staging_root / "good-job",
            warnings=(),
        )

    summary = execute_batch_fetches(
        execution,
        tmp_path / "staging",
        fetcher=fake_fetcher,
    )

    assert calls == ["good"]
    assert [item.status for item in summary.items] == [
        "staged",
        "skipped-failed",
    ]
    assert summary.skipped_failed_count == 1
    assert summary.items[1].error == "metadata unavailable"


def test_batch_fetch_does_not_modify_queue_or_library_with_fake_fetcher(
    tmp_path: Path,
) -> None:
    queue = _write_queue(
        tmp_path / "audiobook-links.txt",
        "https://archive.org/details/edison | year=1898\n",
    )
    before = queue.read_bytes()
    library = tmp_path / "library"
    staging = tmp_path / "staging"
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    provider = _FakeProvider(
        {
            "https://archive.org/details/edison":
                _archive_item("edison", year=2008)
        }
    )
    plans = resolve_batch_plans(preview, library, provider)
    execution = build_batch_execution_preview(plans)

    def fake_fetcher(plan, staging_root):
        return SimpleNamespace(
            job_id="edison-job",
            staging_dir=staging_root / "edison-job",
            warnings=(),
        )

    execute_batch_fetches(execution, staging, fetcher=fake_fetcher)

    assert queue.read_bytes() == before
    assert not library.exists()
