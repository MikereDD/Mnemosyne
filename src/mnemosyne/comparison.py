from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from .fetcher import FetchError, _stream_download, _validate_audio_signature
from .models import MediaCandidate, MediaType
from .providers.archive_org import ArchiveOrgProvider
from .quality import ActualAudioQuality, inspect_actual_quality


class ComparisonError(RuntimeError):
    """Candidate comparison could not be completed safely."""


@dataclass(frozen=True)
class ComparedCandidate:
    candidate: MediaCandidate
    path: Path
    actual: ActualAudioQuality
    sha256: str
    actual_size: int
    quality_score: int


@dataclass(frozen=True)
class ComparisonResult:
    job_dir: Path
    comparison_dir: Path
    candidates: tuple[ComparedCandidate, ...]
    recommended: ComparedCandidate
    report_path: Path


def _read_report(job_dir: Path) -> dict:
    path = job_dir / "fetch-report.json"
    if not path.is_file():
        raise ComparisonError(f"fetch-report.json not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"Could not read staging report: {exc}") from exc


def _actual_quality_score(candidate: MediaCandidate, actual: ActualAudioQuality) -> int:
    """
    Compare broad quality classes first, then actual stream properties.

    Provenance is deliberately a weak tie-breaker. An Archive 'original'
    marker must never outweigh a materially better actual derivative.
    """
    score = 0

    if actual.lossless is True:
        score += 10000
    elif actual.lossless is False:
        score += 3000

    if actual.bitrate_bps:
        score += min(actual.bitrate_bps // 1000, 2000)

    if actual.sample_rate_hz:
        score += min(actual.sample_rate_hz // 1000, 192)

    if actual.channels:
        score += min(actual.channels * 10, 80)

    if candidate.source == "original":
        score += 25

    return score


def compare_archive_candidates(
    job_dir: Path,
    *,
    timeout: float = 60.0,
) -> ComparisonResult:
    job_dir = job_dir.resolve()
    if not job_dir.is_dir():
        raise ComparisonError(f"Staging job directory does not exist: {job_dir}")

    report = _read_report(job_dir)
    source = report.get("source") or {}
    media = report.get("media") or {}
    source_url = source.get("url")
    media_type_text = media.get("type")

    if not source_url:
        raise ComparisonError("Source URL is missing from fetch-report.json.")

    try:
        media_type = MediaType(str(media_type_text))
    except ValueError as exc:
        raise ComparisonError(f"Unsupported media type in report: {media_type_text}") from exc

    provider = ArchiveOrgProvider()
    try:
        item = provider.identify(
            str(source_url),
            media_type,
            title_override=media.get("title"),
            creator_override=media.get("creator"),
            year_override=media.get("year"),
        )
    except Exception as exc:
        raise ComparisonError(f"Could not refresh Archive candidate list: {exc}") from exc

    audio_candidates = sorted(
        (c for c in item.candidates if c.playable),
        key=lambda c: (c.score, c.size or 0),
        reverse=True,
    )

    if len(audio_candidates) < 2:
        raise ComparisonError("Fewer than two playable candidates are available to compare.")

    comparison_root = job_dir / "comparison"
    comparison_root.mkdir(parents=False, exist_ok=True)

    run_id = f"run-{uuid.uuid4().hex[:8]}"
    comparison_dir = comparison_root / run_id
    comparison_dir.mkdir(parents=False, exist_ok=False)

    compared: list[ComparedCandidate] = []

    for index, candidate in enumerate(audio_candidates, start=1):
        target = comparison_dir / f"{index:02d}-{Path(candidate.name).name}"

        actual_size, sha256 = _stream_download(
            candidate.url,
            target,
            expected_size=candidate.size,
            timeout=timeout,
            user_agent="Mnemosyne/0.1.0-dev.6",
        )

        part_path = target.with_name(target.name + ".part")
        _validate_audio_signature(part_path, candidate)
        part_path.replace(target)

        actual = inspect_actual_quality(target)
        compared.append(
            ComparedCandidate(
                candidate=candidate,
                path=target,
                actual=actual,
                sha256=sha256,
                actual_size=actual_size,
                quality_score=_actual_quality_score(candidate, actual),
            )
        )

    ranked = sorted(
        compared,
        key=lambda c: (c.quality_score, c.actual_size),
        reverse=True,
    )
    recommended = ranked[0]

    comparison_report = {
        "schemaVersion": 2,
        "sourceJob": report.get("jobId"),
        "runId": run_id,
        "status": "compared",
        "candidates": [
            {
                "sourceName": c.candidate.name,
                "archiveFormat": c.candidate.archive_format,
                "archiveSource": c.candidate.source,
                "providerClaimedLossless": c.candidate.lossless,
                "actualCodec": c.actual.codec,
                "actualLossless": c.actual.lossless,
                "actualBitrateBps": c.actual.bitrate_bps,
                "actualSampleRateHz": c.actual.sample_rate_hz,
                "actualChannels": c.actual.channels,
                "actualSize": c.actual_size,
                "sha256": c.sha256,
                "qualityScore": c.quality_score,
                "comparisonPath": str(c.path),
            }
            for c in ranked
        ],
        "recommendedSourceName": recommended.candidate.name,
        "recommendedPath": str(recommended.path),
        "finalLibraryModified": False,
    }

    report_path = comparison_dir / "comparison-report.json"
    report_path.write_text(
        json.dumps(comparison_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return ComparisonResult(
        job_dir=job_dir,
        comparison_dir=comparison_dir,
        candidates=tuple(ranked),
        recommended=recommended,
        report_path=report_path,
    )
