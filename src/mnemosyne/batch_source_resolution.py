from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .adoption import (
    AdoptionError,
    EditionAdoptionResult,
    adopt_latest_recommended_edition,
)
from .batch_lifecycle import BatchLifecyclePreview
from .comparison import ComparisonError, ComparisonResult, compare_archive_candidates


@dataclass(frozen=True)
class BatchSourceResolutionItem:
    line_number: int
    identifier: str
    status: str
    job_id: str | None
    staging_dir: Path | None
    detail: str


@dataclass(frozen=True)
class BatchSourceResolutionPreview:
    lifecycle_preview: BatchLifecyclePreview
    items: tuple[BatchSourceResolutionItem, ...]

    @property
    def actionable_count(self) -> int:
        return sum(item.status == "would-resolve" for item in self.items)


@dataclass(frozen=True)
class BatchSourceResolutionResult:
    line_number: int
    identifier: str
    status: str
    job_id: str | None
    staging_dir: Path | None
    recommended_source: str | None
    adopted_path: Path | None
    error: str | None


@dataclass(frozen=True)
class BatchSourceResolutionSummary:
    preview: BatchSourceResolutionPreview
    results: tuple[BatchSourceResolutionResult, ...]

    @property
    def resolved_count(self) -> int:
        return sum(item.status == "resolved" for item in self.results)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.results)

    @property
    def skipped_count(self) -> int:
        return sum(item.status == "skipped" for item in self.results)


def build_batch_source_resolution_preview(
    lifecycle_preview: BatchLifecyclePreview,
) -> BatchSourceResolutionPreview:
    items: list[BatchSourceResolutionItem] = []

    for item in lifecycle_preview.items:
        if item.status == "compare-required" and item.staging_dir is not None:
            status = "would-resolve"
            detail = (
                "Would compare complete audio editions and transactionally adopt "
                "the verified winning edition inside staging."
            )
        else:
            status = "skipped"
            detail = f"Lifecycle state {item.status!r} is not eligible for source resolution."

        items.append(
            BatchSourceResolutionItem(
                line_number=item.line_number,
                identifier=item.identifier,
                status=status,
                job_id=item.job_id,
                staging_dir=item.staging_dir,
                detail=detail,
            )
        )

    return BatchSourceResolutionPreview(
        lifecycle_preview=lifecycle_preview,
        items=tuple(items),
    )


def execute_batch_source_resolution(
    preview: BatchSourceResolutionPreview,
    *,
    comparer: Callable[[Path], ComparisonResult] = compare_archive_candidates,
    adopter: Callable[[Path], EditionAdoptionResult] = adopt_latest_recommended_edition,
) -> BatchSourceResolutionSummary:
    results: list[BatchSourceResolutionResult] = []

    for item in preview.items:
        if item.status != "would-resolve" or item.staging_dir is None:
            results.append(
                BatchSourceResolutionResult(
                    line_number=item.line_number,
                    identifier=item.identifier,
                    status="skipped",
                    job_id=item.job_id,
                    staging_dir=item.staging_dir,
                    recommended_source=None,
                    adopted_path=None,
                    error=None,
                )
            )
            continue

        try:
            comparison = comparer(item.staging_dir)
        except (ComparisonError, OSError, ValueError) as exc:
            results.append(
                BatchSourceResolutionResult(
                    line_number=item.line_number,
                    identifier=item.identifier,
                    status="failed",
                    job_id=item.job_id,
                    staging_dir=item.staging_dir,
                    recommended_source=None,
                    adopted_path=None,
                    error=str(exc),
                )
            )
            continue

        recommended_label = comparison.recommended.edition.label

        try:
            adoption = adopter(item.staging_dir)
        except (AdoptionError, OSError, ValueError) as exc:
            results.append(
                BatchSourceResolutionResult(
                    line_number=item.line_number,
                    identifier=item.identifier,
                    status="failed",
                    job_id=item.job_id,
                    staging_dir=item.staging_dir,
                    recommended_source=recommended_label,
                    adopted_path=None,
                    error=str(exc),
                )
            )
            continue

        results.append(
            BatchSourceResolutionResult(
                line_number=item.line_number,
                identifier=item.identifier,
                status="resolved",
                job_id=item.job_id,
                staging_dir=item.staging_dir,
                recommended_source=recommended_label,
                adopted_path=(
                    adoption.canonical_paths[0]
                    if len(adoption.canonical_paths) == 1
                    else adoption.job_dir / "audio"
                ),
                error=None,
            )
        )

    return BatchSourceResolutionSummary(
        preview=preview,
        results=tuple(results),
    )
