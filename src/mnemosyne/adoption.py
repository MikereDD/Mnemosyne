from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .quality import ActualAudioQuality, inspect_actual_quality


class AdoptionError(RuntimeError):
    """Transactional staged source adoption could not be completed safely."""


@dataclass(frozen=True)
class AdoptionResult:
    job_dir: Path
    canonical_path: Path
    backup_path: Path
    source_comparison_path: Path
    adopted_sha256: str
    adopted_quality: ActualAudioQuality
    report_path: Path


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdoptionError(f"Could not read JSON report {path}: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _latest_comparison_report(job_dir: Path) -> Path:
    comparison_root = job_dir / "comparison"
    if not comparison_root.is_dir():
        raise AdoptionError(f"No comparison directory found: {comparison_root}")

    reports = [
        path
        for path in comparison_root.glob("run-*/comparison-report.json")
        if path.is_file()
    ]
    if not reports:
        raise AdoptionError("No completed comparison reports were found.")

    return max(reports, key=lambda path: path.stat().st_mtime)


def _canonical_audio_path(job_dir: Path, fetch_report: dict) -> Path:
    audio = fetch_report.get("audio") or {}
    staged_path = audio.get("stagedPath")
    if staged_path:
        path = Path(staged_path)
        if path.is_file():
            return path

    canonical_name = audio.get("canonicalStagedName")
    if canonical_name:
        path = job_dir / str(canonical_name)
        if path.is_file():
            return path

    raise AdoptionError("Could not resolve the current canonical staged audio file.")


def _recommended_candidate(comparison_report: dict) -> dict:
    recommended_name = comparison_report.get("recommendedSourceName")
    recommended_path = comparison_report.get("recommendedPath")
    candidates = comparison_report.get("candidates") or []

    if not recommended_name or not recommended_path:
        raise AdoptionError("Comparison report does not contain a recommended candidate.")

    for candidate in candidates:
        if (
            candidate.get("sourceName") == recommended_name
            and candidate.get("comparisonPath") == recommended_path
        ):
            return candidate

    raise AdoptionError("Recommended candidate does not match a recorded comparison candidate.")


def _canonical_extension(source: Path) -> str:
    return source.suffix.lower()


def _canonical_name_from_existing(existing: Path, new_extension: str) -> str:
    return f"{existing.stem}{new_extension}"


def adopt_latest_recommended_source(job_dir: Path) -> AdoptionResult:
    """
    Transactionally adopt the latest comparison winner into the staged canonical slot.

    Safety model:
    1. Validate reports and source paths.
    2. Copy winner to a same-directory temporary file.
    3. Verify copied SHA-256 matches comparison evidence.
    4. Move current canonical source into rollback/.
    5. Atomically replace canonical source with the verified temp copy.
    6. Re-inspect and re-hash the adopted source.
    7. Update fetch-report.json atomically.
    8. Roll back the media swap if any step after backup fails.
    """
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise AdoptionError(f"Staging job directory does not exist: {job_dir}")

    fetch_report_path = job_dir / "fetch-report.json"
    if not fetch_report_path.is_file():
        raise AdoptionError(f"fetch-report.json not found: {fetch_report_path}")

    fetch_report = _read_json(fetch_report_path)
    comparison_report_path = _latest_comparison_report(job_dir)
    comparison_report = _read_json(comparison_report_path)
    recommended = _recommended_candidate(comparison_report)

    source_path = Path(str(recommended["comparisonPath"])).resolve()
    if not source_path.is_file():
        raise AdoptionError(f"Recommended comparison source is missing: {source_path}")

    try:
        source_path.relative_to((job_dir / "comparison").resolve())
    except ValueError as exc:
        raise AdoptionError(
            "Recommended source is outside this staging job's comparison directory."
        ) from exc

    expected_sha256 = str(recommended.get("sha256") or "")
    if not expected_sha256:
        raise AdoptionError("Recommended comparison candidate has no SHA-256 evidence.")

    actual_source_sha = _sha256(source_path)
    if actual_source_sha != expected_sha256:
        raise AdoptionError(
            "Recommended comparison candidate changed after comparison; SHA-256 mismatch."
        )

    current_path = _canonical_audio_path(job_dir, fetch_report)
    new_extension = _canonical_extension(source_path)
    canonical_path = job_dir / _canonical_name_from_existing(current_path, new_extension)

    current_sha = _sha256(current_path)

    # If the winner is byte-identical to the current staged source, do not perform
    # a pointless swap. We still record that the comparison resolved the choice.
    byte_identical = current_sha == expected_sha256

    rollback_dir = job_dir / "rollback"
    rollback_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = rollback_dir / f"{timestamp}-{current_path.name}"

    if backup_path.exists():
        raise AdoptionError(f"Rollback backup already exists: {backup_path}")

    temp_path = job_dir / f".adopt-{source_path.name}.tmp"
    if temp_path.exists():
        raise AdoptionError(f"Temporary adoption path already exists: {temp_path}")

    report_backup = rollback_dir / f"{timestamp}-fetch-report.json"
    shutil.copy2(fetch_report_path, report_backup)

    swapped = False
    try:
        if byte_identical:
            shutil.copy2(current_path, backup_path)
            adopted_path = current_path
        else:
            shutil.copy2(source_path, temp_path)
            copied_sha = _sha256(temp_path)
            if copied_sha != expected_sha256:
                raise AdoptionError("Temporary adopted copy failed SHA-256 verification.")

            os.replace(current_path, backup_path)
            swapped = True

            if canonical_path != current_path and canonical_path.exists():
                raise AdoptionError(
                    f"New canonical staged path already exists: {canonical_path}"
                )

            os.replace(temp_path, canonical_path)
            adopted_path = canonical_path

        adopted_sha = _sha256(adopted_path)
        if adopted_sha != expected_sha256:
            raise AdoptionError("Adopted staged source failed post-replacement SHA-256 verification.")

        adopted_quality = inspect_actual_quality(adopted_path)

        audio = fetch_report.setdefault("audio", {})
        history = fetch_report.setdefault("sourceAdoptionHistory", [])
        history.append(
            {
                "adoptedAt": datetime.now(timezone.utc).isoformat(),
                "comparisonReport": str(comparison_report_path),
                "recommendedSourceName": recommended.get("sourceName"),
                "recommendedComparisonPath": str(source_path),
                "previousStagedPath": str(current_path),
                "previousSha256": current_sha,
                "rollbackBackup": str(backup_path),
                "byteIdenticalToPrevious": byte_identical,
                "adoptedStagedPath": str(adopted_path),
                "adoptedSha256": adopted_sha,
                "actualCodec": adopted_quality.codec,
                "actualLossless": adopted_quality.lossless,
                "actualBitrateBps": adopted_quality.bitrate_bps,
                "actualSampleRateHz": adopted_quality.sample_rate_hz,
                "actualChannels": adopted_quality.channels,
            }
        )

        audio["sourceName"] = recommended.get("sourceName")
        audio["archiveFormat"] = recommended.get("archiveFormat")
        audio["archiveSource"] = recommended.get("archiveSource")
        audio["providerClaimedLossless"] = recommended.get("providerClaimedLossless")
        audio["actualSize"] = adopted_path.stat().st_size
        audio["sha256"] = adopted_sha
        audio["actualCodec"] = adopted_quality.codec
        audio["actualLossless"] = adopted_quality.lossless
        audio["actualBitrateBps"] = adopted_quality.bitrate_bps
        audio["actualSampleRateHz"] = adopted_quality.sample_rate_hz
        audio["actualChannels"] = adopted_quality.channels
        audio["canonicalStagedName"] = adopted_path.name
        audio["stagedPath"] = str(adopted_path)

        fetch_report["schemaVersion"] = max(int(fetch_report.get("schemaVersion") or 0), 4)
        fetch_report["status"] = "staged-source-resolved"
        fetch_report["warnings"] = [
            warning
            for warning in (fetch_report.get("warnings") or [])
            if "Provider metadata claimed lossless audio" not in str(warning)
        ]
        fetch_report["sourceResolution"] = {
            "status": "resolved-by-actual-comparison",
            "comparisonReport": str(comparison_report_path),
            "recommendedSourceName": recommended.get("sourceName"),
            "adoptedStagedPath": str(adopted_path),
            "rollbackBackup": str(backup_path),
            "byteIdenticalToPrevious": byte_identical,
        }
        fetch_report["finalLibraryModified"] = False

        new_report_path = job_dir / ".fetch-report.json.tmp"
        new_report_path.write_text(
            json.dumps(fetch_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _read_json(new_report_path)
        os.replace(new_report_path, fetch_report_path)

        return AdoptionResult(
            job_dir=job_dir,
            canonical_path=adopted_path,
            backup_path=backup_path,
            source_comparison_path=source_path,
            adopted_sha256=adopted_sha,
            adopted_quality=adopted_quality,
            report_path=fetch_report_path,
        )

    except Exception:
        temp_path.unlink(missing_ok=True)

        if swapped:
            try:
                if canonical_path.exists():
                    canonical_path.unlink()
                if backup_path.exists():
                    os.replace(backup_path, current_path)
            except OSError:
                pass

        raise

@dataclass(frozen=True)
class EditionAdoptionResult:
    job_dir: Path
    multi_file: bool
    canonical_paths: tuple[Path, ...]
    backup_dir: Path
    comparison_report_path: Path
    recommended_edition_key: str
    recommended_label: str
    report_path: Path


def _recommended_edition(comparison_report: dict) -> dict:
    key = comparison_report.get("recommendedEditionKey")
    editions = comparison_report.get("editions") or []
    if not key:
        raise AdoptionError("Comparison report does not contain a recommended edition key.")
    for edition in editions:
        if edition.get("editionKey") == key:
            return edition
    raise AdoptionError("Recommended edition does not match a recorded comparison edition.")


def _validate_current_canonical_path(job_dir: Path, path: Path) -> Path:
    resolved_job = job_dir.resolve()
    resolved = path.resolve()

    try:
        relative = resolved.relative_to(resolved_job)
    except ValueError as exc:
        raise AdoptionError(
            f"Current staged edition path is outside this staging job: {resolved}"
        ) from exc

    canonical_shape = (
        len(relative.parts) == 1
        or (
            len(relative.parts) == 2
            and relative.parts[0].lower() == "audio"
        )
    )
    if not canonical_shape:
        raise AdoptionError(
            f"Current staged edition path is not canonical job media: {resolved}"
        )

    return resolved


def _current_edition_paths(job_dir: Path, fetch_report: dict) -> list[Path]:
    audio = fetch_report.get("audio") or {}
    entries = audio.get("files") or []
    paths: list[Path] = []

    for entry in entries:
        staged = entry.get("stagedPath")
        canonical = entry.get("canonicalStagedName")
        path = (
            Path(str(staged))
            if staged
            else (job_dir / str(canonical) if canonical else None)
        )
        if path is None or not path.is_file():
            raise AdoptionError("Could not resolve every current staged edition file.")
        paths.append(_validate_current_canonical_path(job_dir, path))

    if not paths:
        fallback = _canonical_audio_path(job_dir, fetch_report)
        paths.append(_validate_current_canonical_path(job_dir, fallback))

    if len(set(paths)) != len(paths):
        raise AdoptionError("Current staged edition contains duplicate file paths.")

    return paths


def _chapter_number_from_name(name: str, fallback: int) -> int:
    import re
    stem = Path(name).stem
    match = re.search(r"(?:^|[_-])(\d{2,3})(?:[_-]|$)", stem)
    if match:
        try:
            value = int(match.group(1))
            if value > 0:
                return value
        except ValueError:
            pass
    return fallback


def _canonical_winner_name(entry: dict, index: int, *, multi_file: bool) -> str:
    source_name = str(entry.get("sourceName") or "")
    extension = Path(source_name).suffix.lower()
    if not extension:
        extension = Path(str(entry.get("comparisonPath") or "")).suffix.lower()
    if not extension:
        raise AdoptionError("Recommended comparison file has no usable extension.")

    if multi_file:
        number = _chapter_number_from_name(source_name, index)
        return f"{number:02d} - Chapter {number:02d}{extension}"

    return extension


def _remove_single_audio_summary(audio: dict) -> None:
    for key in (
        "sourceName",
        "sourceUrl",
        "archiveFormat",
        "archiveSource",
        "providerClaimedLossless",
        "expectedSize",
        "actualSize",
        "sha256",
        "signature",
        "actualCodec",
        "actualLossless",
        "actualBitrateBps",
        "actualSampleRateHz",
        "actualChannels",
        "actualInspectionSource",
        "metadataFamily",
        "metadataNormalized",
        "metadataVerification",
        "embeddedArtwork",
        "embeddedArtworkSha256",
    ):
        audio.pop(key, None)


def adopt_latest_recommended_edition(job_dir: Path) -> EditionAdoptionResult:
    """
    Transactionally adopt a complete recommended edition.

    Supports single→single, single→multi, multi→single, and multi→multi.
    Every winner file is SHA-256 verified before any current staged media moves.
    The complete previous edition and fetch report are retained together in one
    rollback directory. Report provenance is rewritten to match the adopted
    edition exactly.
    """
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise AdoptionError(f"Staging job directory does not exist: {job_dir}")

    fetch_report_path = job_dir / "fetch-report.json"
    if not fetch_report_path.is_file():
        raise AdoptionError(f"fetch-report.json not found: {fetch_report_path}")

    fetch_report = _read_json(fetch_report_path)
    comparison_report_path = _latest_comparison_report(job_dir)
    comparison_report = _read_json(comparison_report_path)

    if comparison_report.get("comparisonUnit") != "complete-audio-edition":
        raise AdoptionError("Latest comparison report is not edition-aware.")

    recommended = _recommended_edition(comparison_report)
    winner_files = recommended.get("files") or []
    if not winner_files:
        raise AdoptionError("Recommended edition contains no files.")

    multi_file = bool(recommended.get("multiFile"))
    if multi_file != (len(winner_files) > 1):
        raise AdoptionError("Recommended edition shape is internally inconsistent.")

    comparison_root = (job_dir / "comparison").resolve()
    verified: list[tuple[dict, Path, str]] = []

    for entry in winner_files:
        source = Path(str(entry.get("comparisonPath") or "")).resolve()
        if not source.is_file():
            raise AdoptionError(f"Recommended comparison file is missing: {source}")
        try:
            source.relative_to(comparison_root)
        except ValueError as exc:
            raise AdoptionError(
                "Recommended edition file is outside this staging job's comparison directory."
            ) from exc

        expected_sha = str(entry.get("sha256") or "")
        if not expected_sha:
            raise AdoptionError("Recommended edition file has no SHA-256 evidence.")
        actual_sha = _sha256(source)
        if actual_sha != expected_sha:
            raise AdoptionError(
                f"Recommended edition file changed after comparison: {source.name}"
            )
        verified.append((entry, source, actual_sha))

    current_paths = _current_edition_paths(job_dir, fetch_report)
    current_hashes = {path: _sha256(path) for path in current_paths}
    current_report_bytes = fetch_report_path.read_bytes()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rollback_root = job_dir / "rollback"
    rollback_root.mkdir(exist_ok=True)
    backup_dir = rollback_root / f"{stamp}-pre-edition-adoption"
    if backup_dir.exists():
        raise AdoptionError(f"Rollback directory already exists: {backup_dir}")

    work_dir = job_dir / f".edition-adopt-{stamp}"
    if work_dir.exists():
        raise AdoptionError(f"Edition adoption work directory already exists: {work_dir}")

    backup_dir.mkdir()
    work_dir.mkdir()

    canonical_paths: list[Path] = []
    copied_entries: list[dict] = []
    current_media_mutated = False
    fetch_report_replaced = False

    try:
        # Build and verify the complete new edition before touching current media.
        if multi_file:
            work_audio = work_dir / "audio"
            work_audio.mkdir()
            for index, (entry, source, expected_sha) in enumerate(verified, start=1):
                name = _canonical_winner_name(entry, index, multi_file=True)
                target = work_audio / name
                if target.exists():
                    raise AdoptionError(f"Duplicate canonical winner path: {target.name}")
                shutil.copy2(source, target)
                if _sha256(target) != expected_sha:
                    raise AdoptionError(f"Copied winner failed SHA-256 verification: {target.name}")
                copied_entries.append(
                    {
                        "entry": entry,
                        "workPath": target,
                        "sha256": expected_sha,
                        "canonicalName": name,
                    }
                )
        else:
            entry, source, expected_sha = verified[0]
            extension = _canonical_winner_name(entry, 1, multi_file=False)
            old_audio = fetch_report.get("audio") or {}
            existing_name = old_audio.get("canonicalStagedName")
            if existing_name:
                stem = Path(str(existing_name)).stem
            else:
                media = fetch_report.get("media") or {}
                stem = f"{media.get('title') or 'Unknown'} - {media.get('creator') or 'Unknown'} ({media.get('year') or 'Unknown'})"
            name = f"{stem}{extension}"
            target = work_dir / name
            shutil.copy2(source, target)
            if _sha256(target) != expected_sha:
                raise AdoptionError("Copied single-file winner failed SHA-256 verification.")
            copied_entries.append(
                {
                    "entry": entry,
                    "workPath": target,
                    "sha256": expected_sha,
                    "canonicalName": name,
                }
            )

        # Preserve current report and every current media file as one rollback unit.
        backup_report_path = backup_dir / "fetch-report.json"
        backup_report_path.write_bytes(current_report_bytes)
        if backup_report_path.read_bytes() != current_report_bytes:
            raise AdoptionError("Rollback fetch-report copy verification failed.")

        backup_audio_dir = backup_dir / "audio"
        backup_audio_dir.mkdir()

        for current in current_paths:
            destination = backup_audio_dir / current.name
            if destination.exists():
                raise AdoptionError(f"Rollback filename collision: {destination.name}")
            shutil.copy2(current, destination)
            if _sha256(destination) != current_hashes[current]:
                raise AdoptionError(f"Rollback copy verification failed: {current.name}")

        # Remove current canonical media only after all backup copies verify.
        # Set the mutation flag before the first unlink so partial deletion is
        # always treated as requiring restoration.
        current_media_mutated = True
        for current in current_paths:
            current.unlink()

        old_audio_dir = job_dir / "audio"
        if old_audio_dir.is_dir() and not any(old_audio_dir.iterdir()):
            old_audio_dir.rmdir()

        # Commit prepared winner files into canonical staging.
        if multi_file:
            final_audio_dir = job_dir / "audio"
            if final_audio_dir.exists():
                raise AdoptionError(f"Canonical audio directory already exists: {final_audio_dir}")
            canonical_paths.extend(
                final_audio_dir / item["canonicalName"]
                for item in copied_entries
            )
            os.replace(work_dir / "audio", final_audio_dir)
        else:
            item = copied_entries[0]
            final_path = job_dir / item["canonicalName"]
            if final_path.exists():
                raise AdoptionError(f"Canonical staged path already exists: {final_path}")
            canonical_paths.append(final_path)
            os.replace(item["workPath"], final_path)

        # Verify canonical files after the move.
        for path, item in zip(canonical_paths, copied_entries):
            if _sha256(path) != item["sha256"]:
                raise AdoptionError(f"Canonical adopted file failed SHA-256 verification: {path.name}")

        audio_files: list[dict] = []
        for index, (path, item) in enumerate(zip(canonical_paths, copied_entries), start=1):
            source_entry = item["entry"]
            quality = inspect_actual_quality(path)
            if quality.inspection_warning:
                raise AdoptionError(
                    f"Adopted file quality inspection is inconclusive: {path.name}: "
                    f"{quality.inspection_warning}"
                )
            audio_files.append(
                {
                    "index": index,
                    "sourceName": source_entry.get("sourceName"),
                    "sourceUrl": source_entry.get("sourceUrl"),
                    "archiveFormat": source_entry.get("archiveFormat"),
                    "archiveSource": source_entry.get("archiveSource"),
                    "providerClaimedLossless": source_entry.get("providerClaimedLossless"),
                    "expectedSize": source_entry.get("expectedSize"),
                    "actualSize": path.stat().st_size,
                    "sha256": item["sha256"],
                    "signature": source_entry.get("signature"),
                    "actualCodec": quality.codec,
                    "actualLossless": quality.lossless,
                    "actualBitrateBps": quality.bitrate_bps,
                    "actualSampleRateHz": quality.sample_rate_hz,
                    "actualChannels": quality.channels,
                    "actualInspectionSource": quality.inspection_source,
                    "canonicalStagedName": path.name,
                    "stagedPath": str(path),
                }
            )

        audio = fetch_report.setdefault("audio", {})
        _remove_single_audio_summary(audio)
        audio["mode"] = "multi-file" if multi_file else "single-file"
        audio["fileCount"] = len(audio_files)
        audio["files"] = audio_files
        audio["canonicalStagedName"] = None if multi_file else canonical_paths[0].name
        audio["stagedPath"] = None if multi_file else str(canonical_paths[0])

        if not multi_file:
            first = audio_files[0]
            for key in (
                "sourceName",
                "sourceUrl",
                "archiveFormat",
                "archiveSource",
                "providerClaimedLossless",
                "expectedSize",
                "actualSize",
                "sha256",
                "signature",
                "actualCodec",
                "actualLossless",
                "actualBitrateBps",
                "actualSampleRateHz",
                "actualChannels",
                "actualInspectionSource",
            ):
                audio[key] = first.get(key)

        edition = fetch_report.setdefault("audioEdition", {})
        edition["selectedEditionKey"] = recommended.get("editionKey")
        edition["label"] = recommended.get("label")
        edition["multiFile"] = multi_file
        edition["fileCount"] = len(audio_files)
        edition["extension"] = recommended.get("extension")
        edition["archiveFormat"] = recommended.get("archiveFormat")
        edition["archiveSource"] = recommended.get("archiveSource")

        event = {
            "adoptedAt": datetime.now(timezone.utc).isoformat(),
            "comparisonReport": str(comparison_report_path),
            "recommendedEditionKey": recommended.get("editionKey"),
            "recommendedLabel": recommended.get("label"),
            "previousEditionFileCount": len(current_paths),
            "adoptedEditionFileCount": len(canonical_paths),
            "adoptedMultiFile": multi_file,
            "adoptedPaths": [str(path) for path in canonical_paths],
            "rollbackDirectory": str(backup_dir),
            "verification": "passed",
        }
        fetch_report.setdefault("sourceAdoptionHistory", []).append(event)
        fetch_report["sourceResolution"] = {
            "status": "resolved-by-actual-comparison",
            "comparisonUnit": "complete-audio-edition",
            "comparisonReport": str(comparison_report_path),
            "recommendedEditionKey": recommended.get("editionKey"),
            "recommendedLabel": recommended.get("label"),
            "adoptedMultiFile": multi_file,
            "adoptedPaths": [str(path) for path in canonical_paths],
            "rollbackDirectory": str(backup_dir),
        }
        fetch_report["status"] = "staged-source-resolved"
        fetch_report["warnings"] = []
        fetch_report["schemaVersion"] = max(int(fetch_report.get("schemaVersion") or 0), 11)
        fetch_report["finalLibraryModified"] = False

        temp_report = job_dir / ".fetch-report.edition-adoption.tmp"
        temp_report.write_text(
            json.dumps(fetch_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _read_json(temp_report)
        os.replace(temp_report, fetch_report_path)
        fetch_report_replaced = True

        # Work directory should now be empty.
        try:
            work_dir.rmdir()
        except OSError:
            pass

        return EditionAdoptionResult(
            job_dir=job_dir,
            multi_file=multi_file,
            canonical_paths=tuple(canonical_paths),
            backup_dir=backup_dir,
            comparison_report_path=comparison_report_path,
            recommended_edition_key=str(recommended.get("editionKey")),
            recommended_label=str(recommended.get("label")),
            report_path=fetch_report_path,
        )

    except Exception as exc:
        rollback_failures: list[str] = []

        if fetch_report_replaced:
            backup_report = backup_dir / "fetch-report.json"
            if not backup_report.is_file():
                rollback_failures.append("fetch-report rollback copy is missing")
            else:
                try:
                    shutil.copy2(backup_report, fetch_report_path)
                    if fetch_report_path.read_bytes() != current_report_bytes:
                        rollback_failures.append(
                            "fetch-report restoration verification failed"
                        )
                except OSError as restore_exc:
                    rollback_failures.append(
                        f"fetch-report restoration failed: {restore_exc}"
                    )

        for path in canonical_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                rollback_failures.append(
                    f"could not remove adopted file {path.name}: {cleanup_exc}"
                )

        try:
            audio_dir = job_dir / "audio"
            if audio_dir.is_dir() and not any(audio_dir.iterdir()):
                audio_dir.rmdir()
        except OSError as cleanup_exc:
            rollback_failures.append(
                f"could not remove empty canonical audio directory: {cleanup_exc}"
            )

        if current_media_mutated:
            backup_audio_dir = backup_dir / "audio"
            for original in current_paths:
                backup = backup_audio_dir / original.name
                if not backup.is_file():
                    rollback_failures.append(
                        f"rollback copy is missing for {original.name}"
                    )
                    continue
                try:
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, original)
                    restored_sha = _sha256(original)
                    expected_sha = current_hashes[original]
                    if restored_sha != expected_sha:
                        rollback_failures.append(
                            f"restored SHA-256 mismatch for {original.name}"
                        )
                except OSError as restore_exc:
                    rollback_failures.append(
                        f"could not restore {original.name}: {restore_exc}"
                    )

        try:
            if work_dir.exists():
                shutil.rmtree(work_dir)
        except OSError as cleanup_exc:
            rollback_failures.append(
                f"could not remove edition-adoption work directory: {cleanup_exc}"
            )

        if rollback_failures:
            detail = "; ".join(rollback_failures)
            raise AdoptionError(
                "Edition adoption failed and rollback was incomplete. "
                f"Manual recovery is required from {backup_dir}. "
                f"Rollback failures: {detail}. Original error: {exc}"
            ) from exc

        if isinstance(exc, AdoptionError):
            raise
        raise AdoptionError(str(exc)) from exc
