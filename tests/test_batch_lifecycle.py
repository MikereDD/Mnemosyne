from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemosyne.batch import BatchPlanItem, BatchPlanPreview, BatchPreview
from mnemosyne.batch_lifecycle import build_batch_lifecycle_preview
from mnemosyne.models import MediaType


def _preview(tmp_path: Path) -> BatchPlanPreview:
    queue_path = tmp_path / "queue.txt"
    queue_path.write_text(
        "https://archive.org/details/example | year=1901\n",
        encoding="utf-8",
    )
    queue = BatchPreview(
        media_type=MediaType.AUDIOBOOK,
        queue_path=queue_path,
        total_lines=1,
        blank_lines=0,
        comment_lines=0,
        items=(),
        duplicates=(),
        invalid=(),
    )
    plan = BatchPlanItem(
        line_number=1,
        canonical_url="https://archive.org/details/example",
        identifier="example",
        status="actionable",
        title="Example",
        creator="Test Creator",
        year=1901,
        year_provenance="verified-queue",
        destination=tmp_path / "library" / "Example",
        selected_edition="Complete M4B",
        audio_file_count=1,
        warning_count=0,
        warnings=(),
        error=None,
        plan=None,
    )
    return BatchPlanPreview(queue=queue, items=(plan,))


def _job(
    tmp_path: Path,
    *,
    fetch: dict,
    readiness: dict | None = None,
    placement: dict | None = None,
    completion: bool = False,
) -> None:
    job = tmp_path / "staging" / "example-job"
    job.mkdir(parents=True)
    fetch.setdefault("jobId", "example-job")
    fetch.setdefault("source", {"identifier": "example"})
    (job / "fetch-report.json").write_text(json.dumps(fetch), encoding="utf-8")
    if readiness is not None:
        (job / "readiness-report.json").write_text(
            json.dumps(readiness), encoding="utf-8"
        )
    if placement is not None:
        (job / "placement-report.json").write_text(
            json.dumps(placement), encoding="utf-8"
        )
    if completion:
        (job / "completion-report.json").write_text("{}", encoding="utf-8")


@pytest.mark.parametrize(
    ("fetch", "readiness", "placement", "completion", "expected"),
    [
        ({"status": "needs-attention", "warnings": ["x"]}, None, None, False, "needs-attention"),
        ({"status": "staged-normalized", "warnings": []}, None, None, False, "compare-required"),
        (
            {
                "status": "staged-normalized",
                "warnings": [],
                "sourceResolution": {"status": "resolved-by-actual-comparison"},
            },
            None,
            None,
            False,
            "ready-to-tag",
        ),
        (
            {
                "status": "staged-metadata-normalized",
                "warnings": [],
                "metadataNormalization": {"status": "verified"},
            },
            None,
            None,
            False,
            "verify-readiness",
        ),
        (
            {"status": "staged-metadata-normalized", "warnings": []},
            {"status": "ready-for-placement"},
            None,
            False,
            "ready-to-place",
        ),
        (
            {"status": "placed-and-verified", "warnings": []},
            {"status": "ready-for-placement"},
            {"status": "placed-and-verified"},
            False,
            "ready-to-complete",
        ),
        (
            {"status": "complete", "warnings": []},
            None,
            None,
            True,
            "complete",
        ),
    ],
)
def test_lifecycle_classification(
    tmp_path: Path,
    fetch: dict,
    readiness: dict | None,
    placement: dict | None,
    completion: bool,
    expected: str,
) -> None:
    preview = _preview(tmp_path)
    _job(
        tmp_path,
        fetch=fetch,
        readiness=readiness,
        placement=placement,
        completion=completion,
    )

    result = build_batch_lifecycle_preview(
        preview,
        tmp_path / "staging",
        tmp_path / "state",
    )

    assert result.items[0].status == expected
