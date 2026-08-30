from __future__ import annotations

import json
from pathlib import Path

from mnemosyne.batch_state import (
    discover_existing_staged_job,
    load_batch_state,
    record_batch_item,
    staged_job_is_valid,
)
from mnemosyne.models import MediaType


def test_state_round_trip_and_attempt_count(tmp_path: Path) -> None:
    queue = tmp_path / "audiobook-links.txt"
    queue.write_text("https://archive.org/details/example | year=1901\n", encoding="utf-8")

    state_path, state = load_batch_state(
        tmp_path / "state",
        MediaType.AUDIOBOOK,
        queue,
    )
    record_batch_item(
        state_path,
        state,
        identifier="example",
        line_number=1,
        canonical_url="https://archive.org/details/example",
        status="failed",
        job_id=None,
        staging_dir=None,
        attempts=2,
        error="simulated",
    )

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["items"]["example"]["status"] == "failed"
    assert payload["items"]["example"]["attempts"] == 2


def test_existing_staged_job_is_discovered(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    job = staging / "example-1234"
    job.mkdir(parents=True)
    report = job / "fetch-report.json"
    report.write_text(
        json.dumps(
            {
                "jobId": "example-1234",
                "status": "needs-attention",
                "source": {"identifier": "example"},
                "warnings": ["parser warning"],
            }
        ),
        encoding="utf-8",
    )

    found = discover_existing_staged_job(staging, "example")
    assert found is not None
    job_id, found_dir, warning_count = found
    assert job_id == "example-1234"
    assert found_dir == job
    assert warning_count == 1
    assert staged_job_is_valid({"stagingDir": str(job)})
