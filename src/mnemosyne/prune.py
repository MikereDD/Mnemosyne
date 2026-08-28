from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import runtime_root


class PruneError(RuntimeError):
    """Fetch-list pruning could not be completed safely."""


@dataclass(frozen=True)
class PrunePreview:
    job_id: str
    source_url: str
    media_type: str
    list_path: Path
    backup_path: Path
    matching_lines: tuple[int, ...]
    total_lines: int


@dataclass(frozen=True)
class PruneResult:
    job_id: str
    source_url: str
    list_path: Path
    backup_path: Path
    removed_count: int
    remaining_lines: int
    receipt_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PruneError(f"Could not read JSON file {path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _read_json(temporary)
    os.replace(temporary, path)


def _completed_receipt(job_id: str) -> Path:
    safe = "".join(
        ch if ch.isalnum() or ch in "-._" else "-"
        for ch in job_id
    ).strip("-._")
    if not safe:
        raise PruneError("Job ID cannot be converted to a safe receipt filename.")
    return runtime_root() / "state" / "completed" / f"{safe}.json"


def _fetch_list_path(media_type: str) -> Path:
    mapping = {
        "audiobook": "audiobook-links.txt",
        "ebook": "ebook-links.txt",
        "music": "music-links.txt",
    }
    filename = mapping.get(media_type)
    if filename is None:
        raise PruneError(f"Unsupported media type for fetch-list pruning: {media_type}")
    return runtime_root() / "fetch" / filename


def _normalize_url(text: str) -> str:
    return text.strip()


def _matching_lines(lines: list[str], source_url: str) -> list[int]:
    target = _normalize_url(source_url)
    matches: list[int] = []
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _normalize_url(stripped) == target:
            matches.append(index)
    return matches


def preview_prune(job_id: str) -> PrunePreview:
    receipt_path = _completed_receipt(job_id)
    if not receipt_path.is_file():
        raise PruneError(
            f"Durable completed-job receipt not found: {receipt_path}"
        )

    receipt = _read_json(receipt_path)
    if receipt.get("status") != "complete-staging-removed":
        raise PruneError("Completed-job receipt is not in cleanup-complete state.")

    source = receipt.get("source") or {}
    media = receipt.get("media") or {}
    source_url = str(source.get("url") or "")
    media_type = str(media.get("type") or "")

    if not source_url:
        raise PruneError("Completed-job receipt does not contain a source URL.")
    if not media_type:
        raise PruneError("Completed-job receipt does not contain a media type.")

    list_path = _fetch_list_path(media_type)
    if not list_path.is_file():
        raise PruneError(f"Fetch list does not exist: {list_path}")

    lines = list_path.read_text(encoding="utf-8").splitlines()
    matches = _matching_lines(lines, source_url)

    if not matches:
        raise PruneError(
            "The completed source URL is not present as an exact active entry in the fetch list."
        )

    backup_dir = runtime_root() / "state" / "fetch-list-backups"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{timestamp}-{list_path.name}"

    return PrunePreview(
        job_id=job_id,
        source_url=source_url,
        media_type=media_type,
        list_path=list_path,
        backup_path=backup_path,
        matching_lines=tuple(matches),
        total_lines=len(lines),
    )


def apply_prune(job_id: str, *, confirm_url: str) -> PruneResult:
    preview = preview_prune(job_id)

    if _normalize_url(confirm_url) != _normalize_url(preview.source_url):
        raise PruneError(
            "Prune confirmation URL does not exactly match the completed source URL."
        )

    receipt_path = _completed_receipt(job_id)
    receipt = _read_json(receipt_path)

    lines = preview.list_path.read_text(encoding="utf-8").splitlines()

    # Re-evaluate immediately before mutation.
    matches = _matching_lines(lines, preview.source_url)
    if not matches:
        raise PruneError(
            "The source URL disappeared from the fetch list before pruning."
        )

    preview.backup_path.parent.mkdir(parents=True, exist_ok=True)
    if preview.backup_path.exists():
        raise PruneError(
            f"Fetch-list backup already exists: {preview.backup_path}"
        )

    shutil.copy2(preview.list_path, preview.backup_path)

    # Verify the backup captured the exact pre-mutation bytes.
    if preview.backup_path.read_bytes() != preview.list_path.read_bytes():
        preview.backup_path.unlink(missing_ok=True)
        raise PruneError("Fetch-list backup verification failed.")

    match_set = set(matches)
    rewritten = [
        line
        for index, line in enumerate(lines, start=1)
        if index not in match_set
    ]

    temporary = preview.list_path.with_name(
        f".{preview.list_path.name}.{uuid.uuid4().hex[:8]}.tmp"
    )

    # Preserve a final newline for stable human-edited text files.
    temporary.write_text(
        "\n".join(rewritten) + ("\n" if rewritten else ""),
        encoding="utf-8",
    )

    rewritten_lines = temporary.read_text(encoding="utf-8").splitlines()
    if _matching_lines(rewritten_lines, preview.source_url):
        temporary.unlink(missing_ok=True)
        raise PruneError(
            "Rewritten fetch list still contains the source URL; refusing commit."
        )

    os.replace(temporary, preview.list_path)

    final_lines = preview.list_path.read_text(encoding="utf-8").splitlines()
    if _matching_lines(final_lines, preview.source_url):
        # Roll back from the verified backup.
        shutil.copy2(preview.backup_path, preview.list_path)
        raise PruneError(
            "Post-commit verification found the source URL; fetch list was restored."
        )

    pruned_at = datetime.now(timezone.utc).isoformat()

    receipt["schemaVersion"] = max(int(receipt.get("schemaVersion") or 0), 2)
    retention = receipt.setdefault("retention", {})
    retention["fetchListPruned"] = True
    retention["fetchListPrunedAt"] = pruned_at
    retention["fetchListPath"] = str(preview.list_path)
    retention["fetchListBackup"] = str(preview.backup_path)
    retention["fetchListRemovedCount"] = len(matches)

    history = receipt.setdefault("fetchListPruneHistory", [])
    history.append(
        {
            "prunedAt": pruned_at,
            "sourceUrl": preview.source_url,
            "listPath": str(preview.list_path),
            "backupPath": str(preview.backup_path),
            "removedCount": len(matches),
        }
    )

    _write_json_atomic(receipt_path, receipt)

    return PruneResult(
        job_id=preview.job_id,
        source_url=preview.source_url,
        list_path=preview.list_path,
        backup_path=preview.backup_path,
        removed_count=len(matches),
        remaining_lines=len(final_lines),
        receipt_path=receipt_path,
    )
