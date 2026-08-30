from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import dataclass
from pathlib import Path

from .editions import discover_audio_editions
from .fetcher import FetchError, _stream_download, _validate_audio_signature
from .models import AudioEdition, MediaCandidate, MediaType
from .providers.archive_org import ArchiveOrgProvider
from .quality import ActualAudioQuality, inspect_actual_quality


class ComparisonError(RuntimeError):
    """Edition comparison could not be completed safely."""


@dataclass(frozen=True)
class ComparedCandidate:
    candidate: MediaCandidate
    path: Path
    actual: ActualAudioQuality
    sha256: str
    actual_size: int
    expected_size: int | None
    signature: str
    quality_score: int


@dataclass(frozen=True)
class ComparedEdition:
    edition: AudioEdition
    files: tuple[ComparedCandidate, ...]
    quality_score: int
    actual_size: int
    representative_quality: ActualAudioQuality

    @property
    def multi_file(self) -> bool:
        return self.edition.multi_file

    @property
    def label(self) -> str:
        return self.edition.label


@dataclass(frozen=True)
class ComparisonResult:
    job_dir: Path
    comparison_dir: Path
    editions: tuple[ComparedEdition, ...]
    recommended: ComparedEdition
    report_path: Path

    @property
    def candidates(self) -> tuple[ComparedCandidate, ...]:
        """Compatibility view for older callers when the winner is single-file."""
        return tuple(
            file
            for edition in self.editions
            for file in edition.files
        )


def _read_report(job_dir: Path) -> dict:
    path = job_dir / "fetch-report.json"
    if not path.is_file():
        raise ComparisonError(f"fetch-report.json not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"Could not read staging report: {exc}") from exc


def _actual_quality_score(candidate: MediaCandidate, actual: ActualAudioQuality) -> int:
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


def _edition_quality_score(
    edition: AudioEdition,
    files: list[ComparedCandidate],
) -> tuple[int, ActualAudioQuality]:
    if not files:
        raise ComparisonError(f"Edition {edition.label!r} contains no compared files.")

    qualities = [file.actual for file in files]

    if all(q.lossless is True for q in qualities):
        lossless: bool | None = True
        class_score = 10000
    elif all(q.lossless is False for q in qualities):
        lossless = False
        class_score = 3000
    else:
        lossless = None
        class_score = 1000

    bitrates = [q.bitrate_bps for q in qualities if q.bitrate_bps]
    sample_rates = [q.sample_rate_hz for q in qualities if q.sample_rate_hz]
    channels = [q.channels for q in qualities if q.channels]

    # Median prevents one unusually encoded chapter from dominating a whole edition.
    bitrate = int(statistics.median(bitrates)) if bitrates else None
    sample_rate = int(statistics.median(sample_rates)) if sample_rates else None
    channel_count = min(channels) if channels else None

    codecs = {q.codec for q in qualities if q.codec}
    codec = next(iter(codecs)) if len(codecs) == 1 else "mixed" if codecs else None

    score = class_score
    if bitrate:
        score += min(bitrate // 1000, 2000)
    if sample_rate:
        score += min(sample_rate // 1000, 192)
    if channel_count:
        score += min(channel_count * 10, 80)
    if edition.source == "original":
        score += 25

    representative = ActualAudioQuality(
        codec=codec,
        lossless=lossless,
        bitrate_bps=bitrate,
        sample_rate_hz=sample_rate,
        channels=channel_count,
        inspection_warning=None,
        inspection_source="edition-aggregate",
    )
    return score, representative


def compare_archive_candidates(
    job_dir: Path,
    *,
    timeout: float = 60.0,
) -> ComparisonResult:
    """
    Compare complete audiobook editions, never arbitrary playable files.

    A numbered chapter is only compared as a member of its discovered edition.
    This prevents one high-bitrate chapter from defeating a complete audiobook.
    """
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

    if media_type is not MediaType.AUDIOBOOK:
        raise ComparisonError("Edition-aware comparison currently supports audiobooks only.")

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

    editions = discover_audio_editions(item.candidates)
    if len(editions) < 2:
        raise ComparisonError("Fewer than two complete audio editions are available to compare.")

    comparison_root = job_dir / "comparison"
    comparison_root.mkdir(parents=False, exist_ok=True)

    run_id = f"run-{uuid.uuid4().hex[:8]}"
    comparison_dir = comparison_root / run_id
    comparison_dir.mkdir(parents=False, exist_ok=False)

    compared_editions: list[ComparedEdition] = []

    for edition_index, edition in enumerate(editions, start=1):
        edition_dir = comparison_dir / f"edition-{edition_index:02d}"
        edition_dir.mkdir()

        compared_files: list[ComparedCandidate] = []

        for file_index, candidate in enumerate(edition.candidates, start=1):
            target = edition_dir / f"{file_index:02d}-{Path(candidate.name).name}"

            actual_size, sha256 = _stream_download(
                candidate.url,
                target,
                expected_size=candidate.size,
                timeout=timeout,
                user_agent="Mnemosyne/0.2.0-dev.2",
            )

            part_path = target.with_name(target.name + ".part")
            signature = _validate_audio_signature(part_path, candidate)
            part_path.replace(target)

            actual = inspect_actual_quality(target)
            if actual.inspection_warning is not None:
                raise ComparisonError(
                    f"Quality inspection remained inconclusive for {candidate.name}: "
                    f"{actual.inspection_warning}"
                )

            compared_files.append(
                ComparedCandidate(
                    candidate=candidate,
                    path=target,
                    actual=actual,
                    sha256=sha256,
                    actual_size=actual_size,
                    expected_size=candidate.size,
                    signature=signature,
                    quality_score=_actual_quality_score(candidate, actual),
                )
            )

        edition_score, representative = _edition_quality_score(edition, compared_files)
        compared_editions.append(
            ComparedEdition(
                edition=edition,
                files=tuple(compared_files),
                quality_score=edition_score,
                actual_size=sum(file.actual_size for file in compared_files),
                representative_quality=representative,
            )
        )

    ranked = sorted(
        compared_editions,
        key=lambda e: (e.quality_score, e.actual_size),
        reverse=True,
    )
    recommended = ranked[0]

    comparison_report = {
        "schemaVersion": 3,
        "sourceJob": report.get("jobId"),
        "runId": run_id,
        "status": "compared-editions",
        "comparisonUnit": "complete-audio-edition",
        "editions": [
            {
                "editionKey": compared.edition.key,
                "label": compared.edition.label,
                "multiFile": compared.edition.multi_file,
                "fileCount": len(compared.files),
                "extension": compared.edition.extension,
                "archiveFormat": compared.edition.archive_format,
                "archiveSource": compared.edition.source,
                "qualityScore": compared.quality_score,
                "actualSize": compared.actual_size,
                "representativeQuality": {
                    "codec": compared.representative_quality.codec,
                    "lossless": compared.representative_quality.lossless,
                    "bitrateBps": compared.representative_quality.bitrate_bps,
                    "sampleRateHz": compared.representative_quality.sample_rate_hz,
                    "channels": compared.representative_quality.channels,
                },
                "files": [
                    {
                        "sourceName": file.candidate.name,
                        "sourceUrl": file.candidate.url,
                        "archiveFormat": file.candidate.archive_format,
                        "archiveSource": file.candidate.source,
                        "providerClaimedLossless": file.candidate.lossless,
                        "expectedSize": file.expected_size,
                        "actualCodec": file.actual.codec,
                        "actualLossless": file.actual.lossless,
                        "actualBitrateBps": file.actual.bitrate_bps,
                        "actualSampleRateHz": file.actual.sample_rate_hz,
                        "actualChannels": file.actual.channels,
                        "actualSize": file.actual_size,
                        "sha256": file.sha256,
                        "signature": file.signature,
                        "comparisonPath": str(file.path),
                    }
                    for file in compared.files
                ],
            }
            for compared in ranked
        ],
        "recommendedEditionKey": recommended.edition.key,
        "recommendedLabel": recommended.edition.label,
        "recommendedMultiFile": recommended.edition.multi_file,
        "recommendedFileCount": len(recommended.files),
        "finalLibraryModified": False,
    }

    # Preserve the old single-file recommendation fields only when they are truthful.
    if not recommended.edition.multi_file and len(recommended.files) == 1:
        winner = recommended.files[0]
        comparison_report["recommendedSourceName"] = winner.candidate.name
        comparison_report["recommendedPath"] = str(winner.path)
        comparison_report["candidates"] = [
            {
                "sourceName": winner.candidate.name,
                "archiveFormat": winner.candidate.archive_format,
                "archiveSource": winner.candidate.source,
                "providerClaimedLossless": winner.candidate.lossless,
                "expectedSize": winner.expected_size,
                "sha256": winner.sha256,
                "signature": winner.signature,
                "comparisonPath": str(winner.path),
            }
        ]

    report_path = comparison_dir / "comparison-report.json"
    report_path.write_text(
        json.dumps(comparison_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return ComparisonResult(
        job_dir=job_dir,
        comparison_dir=comparison_dir,
        editions=tuple(ranked),
        recommended=recommended,
        report_path=report_path,
    )
