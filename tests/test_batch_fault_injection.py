from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mnemosyne.batch import (
    build_batch_execution_preview,
    execute_batch_fetches,
    parse_fetch_queue,
    resolve_batch_plans,
)
from mnemosyne.models import (
    ArchiveItem,
    CandidateKind,
    MediaCandidate,
    MediaType,
)


def _write_queue(path: Path, identifiers: list[str]) -> Path:
    path.write_text(
        "".join(
            f"https://archive.org/details/{identifier} | year=1900\n"
            for identifier in identifiers
        ),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _archive_item(identifier: str) -> ArchiveItem:
    return ArchiveItem(
        identifier=identifier,
        source_url=f"https://archive.org/details/{identifier}",
        media_type=MediaType.AUDIOBOOK,
        raw_title=f"Raw {identifier}",
        title=f"Title {identifier}",
        creator="Fault Test Author",
        year=2026,
        candidates=[
            MediaCandidate(
                name=f"{identifier}.mp3",
                url=(
                    f"https://archive.org/download/{identifier}/"
                    f"{identifier}.mp3"
                ),
                extension=".mp3",
                archive_format="VBR MP3",
                source="original",
                size=1000,
                kind=CandidateKind.AUDIO,
                playable=True,
                score=700,
            ),
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
            ),
        ],
    )


class _Provider:
    def identify(
        self,
        url: str,
        media_type: MediaType,
        *,
        year_override: int | None = None,
    ) -> ArchiveItem:
        identifier = url.rsplit("/", 1)[-1]
        item = _archive_item(identifier)
        if year_override is not None:
            item = item.model_copy(update={"year": year_override})
        assert item.media_type is media_type
        return item


def _execution(tmp_path: Path, identifiers: list[str]):
    queue = _write_queue(tmp_path / "queue.txt", identifiers)
    preview = parse_fetch_queue(MediaType.AUDIOBOOK, queue)
    plans = resolve_batch_plans(
        preview,
        tmp_path / "library",
        _Provider(),
    )
    return build_batch_execution_preview(plans)


def _write_fetch_report(
    staging_dir: Path,
    *,
    identifier: str,
    job_id: str,
    status: str = "staged-normalized",
) -> None:
    (staging_dir / "fetch-report.json").write_text(
        (
            "{\n"
            '  "schemaVersion": 9,\n'
            f'  "jobId": "{job_id}",\n'
            f'  "status": "{status}",\n'
            '  "source": {\n'
            '    "provider": "Internet Archive",\n'
            f'    "identifier": "{identifier}",\n'
            f'    "url": "https://archive.org/details/{identifier}"\n'
            '  },\n'
            '  "warnings": [],\n'
            '  "finalLibraryModified": false\n'
            "}\n"
        ),
        encoding="utf-8",
    )


def _fake_result(identifier: str, staging_root: Path):
    staging_dir = staging_root / f"{identifier}-job"
    staging_dir.mkdir(parents=True, exist_ok=True)
    job_id = f"{identifier}-job"
    _write_fetch_report(
        staging_dir,
        identifier=identifier,
        job_id=job_id,
    )
    return SimpleNamespace(
        job_id=job_id,
        staging_dir=staging_dir,
        warnings=(),
    )


def test_injected_fetch_failure_is_isolated_and_later_item_still_runs(
    tmp_path: Path,
) -> None:
    execution = _execution(tmp_path, ["first", "second", "third"])
    calls: list[str] = []

    def flaky_fetcher(plan, staging_root):
        identifier = plan.item.identifier
        calls.append(identifier)
        if identifier == "second":
            raise OSError("FAULT: simulated disk write failure")
        return _fake_result(identifier, staging_root)

    summary = execute_batch_fetches(
        execution,
        tmp_path / "staging",
        fetcher=flaky_fetcher,
    )

    assert calls == ["first", "second", "third"]
    assert [item.status for item in summary.items] == [
        "staged",
        "failed",
        "staged",
    ]
    assert "FAULT: simulated disk write failure" in (
        summary.items[1].error or ""
    )


def test_failed_item_requires_explicit_retry_and_staged_item_is_not_refetched(
    tmp_path: Path,
) -> None:
    execution = _execution(tmp_path, ["good", "flaky"])
    staging_root = tmp_path / "staging"
    first_calls: list[str] = []

    def first_fetcher(plan, staging_root):
        identifier = plan.item.identifier
        first_calls.append(identifier)
        if identifier == "flaky":
            raise OSError("FAULT: first attempt fails")
        return _fake_result(identifier, staging_root)

    first = execute_batch_fetches(
        execution,
        staging_root,
        fetcher=first_fetcher,
    )

    assert first_calls == ["good", "flaky"]
    assert [item.status for item in first.items] == ["staged", "failed"]

    no_retry_calls: list[str] = []

    def should_not_run(plan, staging_root):
        no_retry_calls.append(plan.item.identifier)
        raise AssertionError("Fetcher should not run without explicit retry")

    second = execute_batch_fetches(
        execution,
        staging_root,
        fetcher=should_not_run,
    )

    assert no_retry_calls == []
    assert [item.status for item in second.items] == [
        "already-staged",
        "retry-required",
    ]

    retry_calls: list[str] = []

    def retry_fetcher(plan, staging_root):
        identifier = plan.item.identifier
        retry_calls.append(identifier)
        return _fake_result(identifier, staging_root)

    third = execute_batch_fetches(
        execution,
        staging_root,
        retry_failed=True,
        fetcher=retry_fetcher,
    )

    assert retry_calls == ["flaky"]
    assert [item.status for item in third.items] == [
        "already-staged",
        "staged",
    ]


def test_interrupted_state_recovers_existing_staging_without_redownload(
    tmp_path: Path,
) -> None:
    execution = _execution(tmp_path, ["recoverable"])
    staging_root = tmp_path / "staging"

    existing = staging_root / "recoverable-manual-job"
    existing.mkdir(parents=True)
    _write_fetch_report(
        existing,
        identifier="recoverable",
        job_id="recoverable-manual-job",
    )

    calls: list[str] = []

    def should_not_run(plan, staging_root):
        calls.append(plan.item.identifier)
        raise AssertionError("Existing valid staging must be adopted, not fetched")

    summary = execute_batch_fetches(
        execution,
        staging_root,
        fetcher=should_not_run,
    )

    assert calls == []
    assert summary.items[0].status == "already-staged"
    assert summary.items[0].staging_dir == existing