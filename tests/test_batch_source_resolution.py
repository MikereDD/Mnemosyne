from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mnemosyne.batch_lifecycle import BatchLifecycleItem
from mnemosyne.batch_source_resolution import (
    build_batch_source_resolution_preview,
    execute_batch_source_resolution,
)


def _lifecycle(tmp_path: Path):
    compare_job = tmp_path / "compare-job"
    compare_job.mkdir()
    ready_job = tmp_path / "ready-job"
    ready_job.mkdir()
    return SimpleNamespace(
        items=(
            BatchLifecycleItem(1, "compare-me", "compare-required", "compare-job", compare_job, "compare"),
            BatchLifecycleItem(2, "already-ready", "ready-to-tag", "ready-job", ready_job, "ready"),
        )
    )


def test_preview_only_marks_compare_required_items_actionable(tmp_path: Path) -> None:
    preview = build_batch_source_resolution_preview(_lifecycle(tmp_path))
    assert preview.actionable_count == 1
    assert preview.items[0].status == "would-resolve"
    assert preview.items[1].status == "skipped"


def test_multi_file_winner_is_adopted_as_whole_edition(tmp_path: Path) -> None:
    preview = build_batch_source_resolution_preview(_lifecycle(tmp_path))
    calls = []

    edition = SimpleNamespace(label="MP3 chapter set (18 files)", multi_file=True)
    comparison = SimpleNamespace(recommended=SimpleNamespace(edition=edition))
    adoption = SimpleNamespace(
        job_dir=tmp_path / "compare-job",
        canonical_paths=(
            tmp_path / "compare-job" / "audio" / "01 - Chapter 01.mp3",
            tmp_path / "compare-job" / "audio" / "02 - Chapter 02.mp3",
        ),
    )

    def comparer(job):
        calls.append(("compare", job))
        return comparison

    def adopter(job):
        calls.append(("adopt", job))
        return adoption

    summary = execute_batch_source_resolution(
        preview,
        comparer=comparer,
        adopter=adopter,
    )

    assert summary.resolved_count == 1
    assert summary.failed_count == 0
    assert calls == [
        ("compare", tmp_path / "compare-job"),
        ("adopt", tmp_path / "compare-job"),
    ]
    assert summary.results[0].recommended_source == "MP3 chapter set (18 files)"
    assert summary.results[0].adopted_path == tmp_path / "compare-job" / "audio"


def test_execute_continues_after_comparison_failure(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    lifecycle = SimpleNamespace(
        items=(
            BatchLifecycleItem(1, "first", "compare-required", "first", first, "compare"),
            BatchLifecycleItem(2, "second", "compare-required", "second", second, "compare"),
        )
    )
    preview = build_batch_source_resolution_preview(lifecycle)

    def comparer(job):
        if job == first:
            raise OSError("simulated comparison failure")
        return SimpleNamespace(
            recommended=SimpleNamespace(
                edition=SimpleNamespace(label="Complete M4B", multi_file=False)
            )
        )

    def adopter(job):
        return SimpleNamespace(
            job_dir=job,
            canonical_paths=(job / "book.m4b",),
        )

    summary = execute_batch_source_resolution(
        preview,
        comparer=comparer,
        adopter=adopter,
    )
    assert summary.failed_count == 1
    assert summary.resolved_count == 1
