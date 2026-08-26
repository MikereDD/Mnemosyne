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
