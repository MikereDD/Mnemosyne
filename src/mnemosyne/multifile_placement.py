from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metadata_io import MetadataIOError, verify_metadata


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
    existing_destination_equivalent: bool = False


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
    verified_existing_destination: bool = False


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


_AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".m4b", ".mp4"}


def _decoded_audio_sha(path: Path) -> str:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "framemd5", "-"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise MultiFilePlacementError(
            f"Could not run ffmpeg for destination equivalence: {exc}"
        ) from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise MultiFilePlacementError(
            f"Could not decode audio for destination equivalence: {path.name}"
            + (f": {detail}" if detail else "")
        )
    return hashlib.sha256(proc.stdout).hexdigest()


def _verify_existing_destination(
    destination: Path,
    entries: list[tuple[dict[str, Any], Path]],
    cover: Path,
    cover_sha: str,
) -> tuple[tuple[Path, ...], tuple[str, ...], str, Path]:
    if not destination.is_dir():
        raise MultiFilePlacementError(
            f"Final destination exists but is not a directory: {destination}"
        )

    expected_names = [path.name for _, path in entries]
    expected_set = set(expected_names)
    destination_audio = sorted(
        (
            path
            for path in destination.iterdir()
            if path.is_file() and path.suffix.lower() in _AUDIO_SUFFIXES
        ),
        key=lambda path: path.name,
    )
    destination_names = [path.name for path in destination_audio]

    if set(destination_names) != expected_set or len(destination_names) != len(expected_names):
        missing = sorted(expected_set - set(destination_names))
        extra = sorted(set(destination_names) - expected_set)
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing[:3]))
        if extra:
            detail.append("extra: " + ", ".join(extra[:3]))
        raise MultiFilePlacementError(
            "Existing destination is not the same complete audio file set"
            + (f" ({'; '.join(detail)})" if detail else "")
            + ". Refusing overwrite/merge."
        )

    destination_cover = destination / cover.name
    if not destination_cover.is_file():
        raise MultiFilePlacementError(
            f"Existing destination cover is missing: {destination_cover}"
        )
    if _sha256(destination_cover) != cover_sha:
        raise MultiFilePlacementError(
            "Existing destination cover differs from the certified staged cover; "
            "refusing overwrite/merge."
        )

    dest_paths = []
    dest_hashes = []
    for entry, staged in entries:
        target = destination / staged.name
        staged_sha = _sha256(staged)
        target_sha = _sha256(target)

        if staged_sha != target_sha:
            expected_tags = {
                str(key): str(value)
                for key, value in (entry.get("writtenTags") or {}).items()
            }
            try:
                verify_metadata(
                    target,
                    expected_tags,
                    expected_cover_sha256=cover_sha,
                )
            except MetadataIOError as exc:
                raise MultiFilePlacementError(
                    f"Existing destination metadata/artwork differs: {target.name}: {exc}"
                ) from exc

            if _decoded_audio_sha(staged) != _decoded_audio_sha(target):
                raise MultiFilePlacementError(
                    f"Existing destination audio content differs: {target.name}; "
                    "refusing overwrite/merge."
                )

        dest_paths.append(target)
        dest_hashes.append(target_sha)

    return (
        tuple(dest_paths),
        tuple(dest_hashes),
        _edition_sha(dest_hashes),
        destination_cover,
    )


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

    existing_equivalent = False
    if destination.exists():
        _verify_existing_destination(destination, entries, cover, cover_sha)
        existing_equivalent = True

    return MultiFilePlacementPreview(
        job_dir=job_dir,
        destination=destination,
        audio_sources=tuple(path for _, path in entries),
        cover_source=cover,
        file_hashes=tuple(hashes),
        edition_sha256=edition_sha,
        cover_sha256=cover_sha,
        existing_destination_equivalent=existing_equivalent,
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

    if preview.existing_destination_equivalent:
        entries = _resolve_audio(fetch_report)
        final_audio, final_hashes, final_edition_sha, final_cover = (
            _verify_existing_destination(
                destination,
                entries,
                preview.cover_source,
                preview.cover_sha256,
            )
        )
        verified_at = datetime.now(timezone.utc).isoformat()
        transaction_id = f"placement-existing-{uuid.uuid4().hex[:8]}"
        placement_report_path = preview.job_dir / "placement-report.json"

        placement_report = {
            "schemaVersion": 3,
            "transactionId": transaction_id,
            "jobId": fetch_report.get("jobId"),
            "status": "placed-and-verified",
            "mode": "multi-file",
            "placementMode": "verified-existing",
            "placedAt": verified_at,
            "destination": str(destination),
            "audio": {
                "fileCount": len(final_audio),
                "stagedEditionSha256": preview.edition_sha256,
                "editionSha256": final_edition_sha,
                "files": [
                    {
                        "source": str(source),
                        "destination": str(target),
                        "sha256": sha,
                        "equivalence": (
                            "byte-identical"
                            if _sha256(source) == sha
                            else "decoded-audio-and-metadata-equivalent"
                        ),
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
                "sha256": preview.cover_sha256,
            },
            "verification": {
                "existingDestination": "equivalent",
                "destinationModified": False,
                "preCommit": "not-applicable",
                "postPlacement": "verified-existing",
            },
            "rollback": {
                "mode": "none-existing-destination-preserved",
                "overwroteExistingDestination": False,
            },
        }
        _write_json_atomic(placement_report_path, placement_report)

        fetch_report["schemaVersion"] = max(
            int(fetch_report.get("schemaVersion") or 0), 12
        )
        fetch_report["status"] = "placed-and-verified"
        fetch_report["finalLibraryModified"] = False
        fetch_report["finalLibraryVerifiedEquivalent"] = True
        fetch_report["finalPlacement"] = {
            "status": "verified",
            "mode": "multi-file",
            "placementMode": "verified-existing",
            "transactionId": transaction_id,
            "placedAt": verified_at,
            "destination": str(destination),
            "audioFileCount": len(final_audio),
            "stagedEditionSha256": preview.edition_sha256,
            "editionSha256": final_edition_sha,
            "audioPaths": [str(path) for path in final_audio],
            "audioFiles": [
                {"path": str(path), "sha256": sha}
                for path, sha in zip(final_audio, final_hashes)
            ],
            "coverPath": str(final_cover),
            "coverSha256": preview.cover_sha256,
            "placementReport": str(placement_report_path),
            "destinationModified": False,
            "verifiedExistingDestination": True,
        }
        _write_json_atomic(fetch_report_path, fetch_report)

        return MultiFilePlacementResult(
            preview.job_dir,
            destination,
            final_audio,
            final_cover,
            final_edition_sha,
            preview.cover_sha256,
            placement_report_path,
            fetch_report_path,
            True,
        )
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
        fetch_report["finalLibraryVerifiedEquivalent"] = False
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
            False,
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
