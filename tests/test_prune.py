import json
from pathlib import Path

import pytest

from mnemosyne.prune import PruneError, apply_prune, preview_prune


def _setup(tmp_path: Path, monkeypatch):
    runtime = tmp_path / "runtime"
    monkeypatch.setattr("mnemosyne.prune.runtime_root", lambda: runtime)

    receipt_dir = runtime / "state" / "completed"
    receipt_dir.mkdir(parents=True)

    receipt = {
        "schemaVersion": 1,
        "jobId": "job-1",
        "status": "complete-staging-removed",
        "source": {"url": "https://archive.org/details/example"},
        "media": {"type": "audiobook"},
        "retention": {"fetchListPruned": False},
    }
    receipt_path = receipt_dir / "job-1.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    fetch_dir = runtime / "fetch"
    fetch_dir.mkdir(parents=True)
    list_path = fetch_dir / "audiobook-links.txt"
    list_path.write_text(
        "# keep comment\n"
        "https://archive.org/details/example\n"
        "\n"
        "https://archive.org/details/other\n"
        "https://archive.org/details/example\n",
        encoding="utf-8",
    )

    return runtime, receipt_path, list_path


def test_prune_preview_finds_only_exact_active_lines(tmp_path: Path, monkeypatch) -> None:
    _, _, _ = _setup(tmp_path, monkeypatch)
    preview = preview_prune("job-1")
    assert preview.matching_lines == (2, 5)


def test_prune_requires_exact_url_confirmation(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with pytest.raises(PruneError, match="does not exactly match"):
        apply_prune("job-1", confirm_url="https://archive.org/details/wrong")


def test_prune_removes_exact_matches_and_preserves_other_lines(tmp_path: Path, monkeypatch) -> None:
    _, receipt_path, list_path = _setup(tmp_path, monkeypatch)

    result = apply_prune(
        "job-1",
        confirm_url="https://archive.org/details/example",
    )

    text = list_path.read_text(encoding="utf-8")
    assert "https://archive.org/details/example" not in text
    assert "https://archive.org/details/other" in text
    assert "# keep comment" in text
    assert result.removed_count == 2
    assert result.backup_path.is_file()

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["retention"]["fetchListPruned"] is True
    assert receipt["retention"]["fetchListRemovedCount"] == 2


def test_prune_blocks_when_url_not_present(tmp_path: Path, monkeypatch) -> None:
    _, _, list_path = _setup(tmp_path, monkeypatch)
    list_path.write_text("https://archive.org/details/other\n", encoding="utf-8")

    with pytest.raises(PruneError, match="not present"):
        preview_prune("job-1")
