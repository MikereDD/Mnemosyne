from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MultiFilePlacementError(RuntimeError):
    pass


@dataclass(frozen=True)
class MultiFilePlacementPreview:
    job_dir: Path
    destination: Path
    audio_sources: tuple[Path, ...]
    cover_source: Path
    file_hashes: tuple[str, ...]
    edition_sha256: str
    cover_sha256: str


@dataclass(frozen=True)
class MultiFilePlacementResult:
    job_dir: Path
    destination: Path
    audio_paths: tuple[Path, ...]
    cover_path: Path
    edition_sha256: str
    cover_sha256: str
    placement_report_path: Path
    fetch_report_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MultiFilePlacementError(f"Could not read JSON {path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _read_json(temporary)
    os.replace(temporary, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _edition_sha(hashes: list[str]) -> str:
    digest = hashlib.sha256()
    for value in hashes:
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _resolve_audio(fetch_report: dict[str, Any]) -> list[tuple[dict[str, Any], Path]]:
    audio = fetch_report.get("audio") or {}
    entries = list(audio.get("files") or [])
    expected = int(audio.get("fileCount") or 0)

    if not entries or expected != len(entries):
        raise MultiFilePlacementError(
            f"Multi-file audio count mismatch: expected {expected}, found {len(entries)}."
        )

    resolved: list[tuple[dict[str, Any], Path]] = []
    for entry in entries:
        path = Path(str(entry.get("stagedPath") or ""))
        if not path.is_file():
            raise MultiFilePlacementError(f"Staged chapter is missing: {path}")
        resolved.append((entry, path))
    return resolved


def _resolve_cover(job_dir: Path, fetch_report: dict[str, Any]) -> Path:
    cover = fetch_report.get("cover") or {}
    staged = cover.get("stagedPath")
    if staged and Path(str(staged)).is_file():
        return Path(str(staged))

    name = cover.get("canonicalStagedName")
    if name and (job_dir / str(name)).is_file():
        return job_dir / str(name)

    for filename in ("cover.jpg", "cover.jpeg", "cover.png", "cover.webp"):
        path = job_dir / filename
        if path.is_file():
            return path

    raise MultiFilePlacementError("Canonical standalone cover is missing.")


def _validate_readiness(job_dir: Path, fetch_report: dict[str, Any]) -> dict[str, Any]:
    path = job_dir / "readiness-report.json"
    if not path.is_file():
        raise MultiFilePlacementError(
            "readiness-report.json is missing. Run `mnemosyne ready` first."
        )

    readiness = _read_json(path)

    if readiness.get("status") != "ready-for-placement":
        raise MultiFilePlacementError("Readiness status is not ready-for-placement.")
    if readiness.get("mode") != "multi-file":
        raise MultiFilePlacementError("Readiness report is not multi-file.")
    if readiness.get("jobId") != fetch_report.get("jobId"):
        raise MultiFilePlacementError("Readiness job ID does not match fetch report.")

    checks = readiness.get("checks") or []
    if not checks or not all(bool(check.get("passed")) for check in checks):
        raise MultiFilePlacementError("Readiness contains failed/missing checks.")

    return readiness


def preview_multifile_placement(job_dir: Path) -> MultiFilePlacementPreview:
    job_dir = job_dir.resolve()
    fetch_report = _read_json(job_dir / "fetch-report.json")

    if (fetch_report.get("audio") or {}).get("mode") != "multi-file":
        raise MultiFilePlacementError("Staging job is not multi-file.")
    if bool(fetch_report.get("finalLibraryModified")):
        raise MultiFilePlacementError("This job already modified the final library.")

    readiness = _validate_readiness(job_dir, fetch_report)
    entries = _resolve_audio(fetch_report)
    cover = _resolve_cover(job_dir, fetch_report)

    destination_text = fetch_report.get("plannedDestination")
    if not destination_text:
        raise MultiFilePlacementError("Planned destination is missing.")

    destination = Path(str(destination_text)).resolve()

    if destination.exists():
        raise MultiFilePlacementError(
            f"Final destination already exists; refusing overwrite/merge: {destination}"
        )

    hashes: list[str] = []
    for entry, path in entries:
        actual = _sha256(path)
        expected = str(entry.get("sha256") or "")
        if not expected or actual != expected:
            raise MultiFilePlacementError(
                f"Staged chapter changed after readiness: {path.name}"
            )
        hashes.append(actual)

    edition_sha = _edition_sha(hashes)
    if edition_sha != str(readiness.get("editionSha256") or ""):
        raise MultiFilePlacementError(
            "Whole-edition SHA changed after readiness certification."
        )

    cover_sha = _sha256(cover)
    if cover_sha != str(readiness.get("coverSha256") or ""):
        raise MultiFilePlacementError(
            "Cover changed after readiness certification."
        )

    return MultiFilePlacementPreview(
        job_dir=job_dir,
        destination=destination,
        audio_sources=tuple(path for _, path in entries),
        cover_source=cover,
        file_hashes=tuple(hashes),
        edition_sha256=edition_sha,
        cover_sha256=cover_sha,
    )


def _make_parents_tracking(path: Path) -> list[Path]:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    path.mkdir(parents=True, exist_ok=True)
    return list(reversed(missing))


def _cleanup_empty_created_parents(created: list[Path]) -> None:
    for path in reversed(created):
        try:
            path.rmdir()
        except OSError:
            break


def apply_multifile_placement(job_dir: Path) -> MultiFilePlacementResult:
    preview = preview_multifile_placement(job_dir)

    fetch_report_path = preview.job_dir / "fetch-report.json"
    fetch_report = _read_json(fetch_report_path)
    previous_fetch_report = json.loads(json.dumps(fetch_report))

    destination = preview.destination
    parent = destination.parent
    created_parents = _make_parents_tracking(parent)

    transaction_id = f"placement-{uuid.uuid4().hex[:8]}"
    temporary_dir = parent / f".mnemosyne-{transaction_id}"
    final_created = False

    try:
        temporary_dir.mkdir(exist_ok=False)

        copied_hashes: list[str] = []
        for source, expected_hash in zip(preview.audio_sources, preview.file_hashes):
            target = temporary_dir / source.name
            shutil.copy2(source, target)
            actual = _sha256(target)
            if actual != expected_hash:
                raise MultiFilePlacementError(
                    f"Pre-commit chapter hash mismatch: {source.name}"
                )
            copied_hashes.append(actual)

        temp_cover = temporary_dir / preview.cover_source.name
        shutil.copy2(preview.cover_source, temp_cover)
        if _sha256(temp_cover) != preview.cover_sha256:
            raise MultiFilePlacementError("Pre-commit cover hash mismatch.")

        if _edition_sha(copied_hashes) != preview.edition_sha256:
            raise MultiFilePlacementError("Pre-commit edition hash mismatch.")

        if destination.exists():
            raise MultiFilePlacementError(
                f"Destination appeared before commit: {destination}"
            )

        os.replace(temporary_dir, destination)
        final_created = True

        final_audio = tuple(destination / source.name for source in preview.audio_sources)
        final_hashes: list[str] = []

        for path, expected in zip(final_audio, preview.file_hashes):
            actual = _sha256(path)
            if actual != expected:
                raise MultiFilePlacementError(
                    f"Post-placement chapter hash mismatch: {path.name}"
                )
            final_hashes.append(actual)

        final_edition_sha = _edition_sha(final_hashes)
        if final_edition_sha != preview.edition_sha256:
            raise MultiFilePlacementError("Post-placement edition hash mismatch.")

        final_cover = destination / preview.cover_source.name
        final_cover_sha = _sha256(final_cover)
        if final_cover_sha != preview.cover_sha256:
            raise MultiFilePlacementError("Post-placement cover hash mismatch.")

        placed_at = datetime.now(timezone.utc).isoformat()
        placement_report_path = preview.job_dir / "placement-report.json"

        placement_report = {
            "schemaVersion": 2,
            "transactionId": transaction_id,
            "jobId": fetch_report.get("jobId"),
            "status": "placed-and-verified",
            "mode": "multi-file",
            "placedAt": placed_at,
            "destination": str(destination),
            "audio": {
                "fileCount": len(final_audio),
                "editionSha256": final_edition_sha,
                "files": [
                    {
                        "source": str(source),
                        "destination": str(target),
                        "sha256": sha,
                    }
                    for source, target, sha in zip(
                        preview.audio_sources,
                        final_audio,
                        final_hashes,
                    )
                ],
            },
            "cover": {
                "source": str(preview.cover_source),
                "destination": str(final_cover),
                "sha256": final_cover_sha,
            },
            "verification": {
                "preCommit": "passed",
                "postPlacement": "passed",
            },
            "rollback": {
                "mode": "remove-new-destination",
                "overwroteExistingDestination": False,
            },
        }
        _write_json_atomic(placement_report_path, placement_report)

        fetch_report["schemaVersion"] = max(
            int(fetch_report.get("schemaVersion") or 0), 11
        )
        fetch_report["status"] = "placed-and-verified"
        fetch_report["finalLibraryModified"] = True
        fetch_report["finalPlacement"] = {
            "status": "verified",
            "mode": "multi-file",
            "transactionId": transaction_id,
            "placedAt": placed_at,
            "destination": str(destination),
            "audioFileCount": len(final_audio),
            "editionSha256": final_edition_sha,
            "audioPaths": [str(path) for path in final_audio],
            "audioFiles": [
                {"path": str(path), "sha256": sha}
                for path, sha in zip(final_audio, final_hashes)
            ],
            "coverPath": str(final_cover),
            "coverSha256": final_cover_sha,
            "placementReport": str(placement_report_path),
        }

        _write_json_atomic(fetch_report_path, fetch_report)

        return MultiFilePlacementResult(
            preview.job_dir,
            destination,
            final_audio,
            final_cover,
            final_edition_sha,
            final_cover_sha,
            placement_report_path,
            fetch_report_path,
        )

    except Exception:
        if temporary_dir.exists():
            shutil.rmtree(temporary_dir, ignore_errors=True)
        if final_created and destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        try:
            _write_json_atomic(fetch_report_path, previous_fetch_report)
        except Exception:
            pass
        _cleanup_empty_created_parents(created_parents)
        raise
