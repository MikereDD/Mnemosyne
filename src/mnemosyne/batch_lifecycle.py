from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .batch import BatchPlanPreview
from .batch_state import (
    BatchStateError,
    discover_existing_staged_job,
    load_batch_state,
    staged_job_is_valid,
)


@dataclass(frozen=True)
class BatchLifecycleItem:
    line_number: int
    identifier: str
    status: str
    job_id: str | None
    staging_dir: Path | None
    detail: str


@dataclass(frozen=True)
class BatchLifecyclePreview:
    plan_preview: BatchPlanPreview
    items: tuple[BatchLifecycleItem, ...]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchStateError(f"Could not read lifecycle JSON {path}: {exc}") from exc


def _find_job(
    identifier: str,
    *,
    staging_root: Path,
    state_items: dict[str, Any],
) -> tuple[str, Path] | None:
    prior = state_items.get(identifier) or {}
    if staged_job_is_valid(prior):
        staging_dir = Path(str(prior["stagingDir"]))
        return str(prior.get("jobId") or staging_dir.name), staging_dir

    discovered = discover_existing_staged_job(staging_root, identifier)
    if discovered is None:
        return None

    job_id, staging_dir, _ = discovered
    return job_id, staging_dir


def _classify(
    line_number: int,
    identifier: str,
    job_id: str,
    job_dir: Path,
) -> BatchLifecycleItem:
    fetch = _read_json(job_dir / "fetch-report.json")
    fetch_status = str(fetch.get("status") or "")

    if fetch_status == "complete" or (job_dir / "completion-report.json").is_file():
        return BatchLifecycleItem(
            line_number, identifier, "complete", job_id, job_dir,
            "Lifecycle is completion-certified.",
        )

    placement_path = job_dir / "placement-report.json"
    if placement_path.is_file():
        placement = _read_json(placement_path)
        if placement.get("status") == "placed-and-verified":
            return BatchLifecycleItem(
                line_number, identifier, "ready-to-complete", job_id, job_dir,
                "Final placement is verified; completion certification is next.",
            )

    readiness_path = job_dir / "readiness-report.json"
    if readiness_path.is_file():
        readiness = _read_json(readiness_path)
        if readiness.get("status") == "ready-for-placement":
            return BatchLifecycleItem(
                line_number, identifier, "ready-to-place", job_id, job_dir,
                "Staging readiness is certified; final placement is next.",
            )

    metadata = fetch.get("metadataNormalization") or {}
    if metadata.get("status") == "verified":
        return BatchLifecycleItem(
            line_number, identifier, "verify-readiness", job_id, job_dir,
            "Canonical metadata is verified; staged readiness verification is next.",
        )

    warnings = fetch.get("warnings") or []
    if warnings:
        return BatchLifecycleItem(
            line_number, identifier, "needs-attention", job_id, job_dir,
            f"Staging has {len(warnings)} unresolved warning(s).",
        )

    source_resolution = fetch.get("sourceResolution") or {}
    if source_resolution.get("status") != "resolved-by-actual-comparison":
        return BatchLifecycleItem(
            line_number, identifier, "compare-required", job_id, job_dir,
            "Source quality decision has not been formally resolved.",
        )

    return BatchLifecycleItem(
        line_number, identifier, "ready-to-tag", job_id, job_dir,
        "Source decision is resolved; metadata normalization is next.",
    )


def build_batch_lifecycle_preview(
    plan_preview: BatchPlanPreview,
    staging_root: Path,
    state_root: Path,
) -> BatchLifecyclePreview:
    _, state = load_batch_state(
        state_root,
        plan_preview.queue.media_type,
        plan_preview.queue.queue_path,
    )
    state_items = state.get("items") or {}
    items: list[BatchLifecycleItem] = []

    for plan_item in plan_preview.items:
        if plan_item.status == "blocked":
            items.append(
                BatchLifecycleItem(
                    plan_item.line_number,
                    plan_item.identifier,
                    "blocked",
                    None,
                    None,
                    plan_item.warnings[0] if plan_item.warnings else "Plan is blocked.",
                )
            )
            continue

        if plan_item.status == "failed":
            items.append(
                BatchLifecycleItem(
                    plan_item.line_number,
                    plan_item.identifier,
                    "plan-failed",
                    None,
                    None,
                    plan_item.error or "Plan resolution failed.",
                )
            )
            continue

        prior = state_items.get(plan_item.identifier) or {}
        if prior.get("status") == "failed":
            items.append(
                BatchLifecycleItem(
                    plan_item.line_number,
                    plan_item.identifier,
                    "retry-required",
                    None,
                    None,
                    str(prior.get("error") or "Previous fetch failed; explicit retry required."),
                )
            )
            continue

        found = _find_job(
            plan_item.identifier,
            staging_root=staging_root,
            state_items=state_items,
        )
        if found is None:
            items.append(
                BatchLifecycleItem(
                    plan_item.line_number,
                    plan_item.identifier,
                    "not-staged",
                    None,
                    None,
                    "No validated staging job is available yet.",
                )
            )
            continue

        job_id, job_dir = found
        items.append(
            _classify(
                plan_item.line_number,
                plan_item.identifier,
                job_id,
                job_dir,
            )
        )

    return BatchLifecyclePreview(plan_preview=plan_preview, items=tuple(items))
