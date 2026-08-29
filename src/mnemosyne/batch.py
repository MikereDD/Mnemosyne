from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .batch_state import (
    BatchStateError,
    discover_existing_staged_job,
    load_batch_state,
    record_batch_item,
    staged_job_is_valid,
)
from .config import runtime_root
from .fetcher import FetchError, FetchResult, fetch_plan_to_staging
from .models import AcquisitionPlan, MediaType
from .planner import build_plan
from .providers.base import ProviderError


_ARCHIVE_HOSTS = {"archive.org", "www.archive.org"}
_ARCHIVE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._-]+$")


class BatchQueueError(RuntimeError):
    """Fetch-list parsing failed without modifying the queue."""


@dataclass(frozen=True)
class BatchItem:
    line_number: int
    source_url: str
    canonical_url: str
    identifier: str
    verified_year: int | None


@dataclass(frozen=True)
class BatchIssue:
    line_number: int
    source_text: str
    kind: str
    detail: str


@dataclass(frozen=True)
class BatchPreview:
    media_type: MediaType
    queue_path: Path
    total_lines: int
    blank_lines: int
    comment_lines: int
    items: tuple[BatchItem, ...]
    duplicates: tuple[BatchIssue, ...]
    invalid: tuple[BatchIssue, ...]

    @property
    def ready_count(self) -> int:
        return len(self.items)

    @property
    def duplicate_count(self) -> int:
        return len(self.duplicates)

    @property
    def invalid_count(self) -> int:
        return len(self.invalid)


@dataclass(frozen=True)
class BatchPlanItem:
    line_number: int
    canonical_url: str
    identifier: str
    status: str
    title: str | None
    creator: str | None
    year: int | None
    year_provenance: str
    destination: Path | None
    selected_edition: str | None
    audio_file_count: int
    warning_count: int
    warnings: tuple[str, ...]
    error: str | None
    plan: AcquisitionPlan | None


@dataclass(frozen=True)
class BatchPlanPreview:
    queue: BatchPreview
    items: tuple[BatchPlanItem, ...]

    @property
    def actionable_count(self) -> int:
        return sum(item.status == "actionable" for item in self.items)

    @property
    def blocked_count(self) -> int:
        return sum(item.status == "blocked" for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.items)


@dataclass(frozen=True)
class BatchExecutionItem:
    sequence: int
    line_number: int
    identifier: str
    action: str
    title: str | None
    destination: Path | None
    reason: str | None


@dataclass(frozen=True)
class BatchExecutionPreview:
    plan_preview: BatchPlanPreview
    items: tuple[BatchExecutionItem, ...]

    @property
    def execute_count(self) -> int:
        return sum(item.action == "execute" for item in self.items)

    @property
    def blocked_count(self) -> int:
        return sum(item.action == "skip-blocked" for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.action == "skip-failed" for item in self.items)


@dataclass(frozen=True)
class BatchFetchItem:
    line_number: int
    identifier: str
    status: str
    job_id: str | None
    staging_dir: Path | None
    warning_count: int
    error: str | None


@dataclass(frozen=True)
class BatchFetchSummary:
    execution_preview: BatchExecutionPreview
    items: tuple[BatchFetchItem, ...]
    state_path: Path

    @property
    def staged_count(self) -> int:
        return sum(item.status == "staged" for item in self.items)

    @property
    def already_staged_count(self) -> int:
        return sum(item.status == "already-staged" for item in self.items)

    @property
    def failed_count(self) -> int:
        return sum(item.status == "failed" for item in self.items)

    @property
    def blocked_count(self) -> int:
        return sum(item.status == "blocked" for item in self.items)

    @property
    def skipped_failed_count(self) -> int:
        return sum(item.status == "skipped-failed" for item in self.items)

    @property
    def retry_required_count(self) -> int:
        return sum(item.status == "retry-required" for item in self.items)


def fetch_queue_path(
    media_type: MediaType,
    *,
    root: Path | None = None,
) -> Path:
    base = root if root is not None else runtime_root()
    filenames = {
        MediaType.AUDIOBOOK: "audiobook-links.txt",
        MediaType.EBOOK: "ebook-links.txt",
        MediaType.MUSIC: "music-links.txt",
    }
    return base / "fetch" / filenames[media_type]


def _parse_queue_entry(text: str) -> tuple[str, int | None]:
    parts = [part.strip() for part in text.split("|")]
    source_url = parts[0]

    if not source_url:
        raise BatchQueueError("Queue entry is missing a source URL.")

    verified_year: int | None = None
    seen_directives: set[str] = set()

    for directive in parts[1:]:
        if not directive:
            raise BatchQueueError("Queue entry contains an empty directive.")

        key, separator, value = directive.partition("=")
        key = key.strip().lower()
        value = value.strip()

        if not separator or not key or not value:
            raise BatchQueueError("Queue directives must use key=value syntax.")

        if key in seen_directives:
            raise BatchQueueError(
                f"Queue directive '{key}' was specified more than once."
            )
        seen_directives.add(key)

        if key != "year":
            raise BatchQueueError(
                f"Unsupported queue directive '{key}'. Supported directives: year."
            )

        if not re.fullmatch(r"\d{4}", value):
            raise BatchQueueError(
                "Verified year must be exactly four decimal digits."
            )

        verified_year = int(value)

    return source_url, verified_year


def _normalize_archive_url(text: str) -> tuple[str, str]:
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise BatchQueueError(f"Malformed URL: {exc}") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise BatchQueueError("URL must use http or https.")

    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _ARCHIVE_HOSTS:
        raise BatchQueueError(
            "Unsupported provider URL; this batch slice accepts Archive.org item URLs."
        )

    if parsed.username or parsed.password:
        raise BatchQueueError("Archive.org item URLs must not contain credentials.")

    try:
        if parsed.port not in (None, 80, 443):
            raise BatchQueueError("Archive.org item URL uses an unexpected port.")
    except ValueError as exc:
        raise BatchQueueError(f"Malformed URL port: {exc}") from exc

    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() != "details":
        raise BatchQueueError(
            "Archive.org URL must identify an item as /details/<identifier>."
        )

    identifier = parts[1]
    if not _ARCHIVE_IDENTIFIER.fullmatch(identifier):
        raise BatchQueueError(
            "Archive.org identifier contains unsupported characters."
        )

    canonical = f"https://archive.org/details/{identifier}"
    return canonical, identifier


def parse_fetch_queue(
    media_type: MediaType,
    queue_path: Path | None = None,
) -> BatchPreview:
    path = queue_path if queue_path is not None else fetch_queue_path(media_type)

    if not path.is_file():
        raise BatchQueueError(
            f"Fetch queue does not exist: {path}. Run 'mnemosyne init' first."
        )

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise BatchQueueError(f"Could not read fetch queue {path}: {exc}") from exc

    lines = text.splitlines()
    items: list[BatchItem] = []
    duplicates: list[BatchIssue] = []
    invalid: list[BatchIssue] = []
    blank_lines = 0
    comment_lines = 0
    seen: dict[str, int] = {}

    for line_number, raw in enumerate(lines, start=1):
        stripped = raw.strip()

        if not stripped:
            blank_lines += 1
            continue

        if stripped.startswith("#"):
            comment_lines += 1
            continue

        try:
            source_url, verified_year = _parse_queue_entry(stripped)
            canonical_url, identifier = _normalize_archive_url(source_url)
        except BatchQueueError as exc:
            invalid.append(
                BatchIssue(
                    line_number=line_number,
                    source_text=raw,
                    kind="invalid",
                    detail=str(exc),
                )
            )
            continue

        if canonical_url in seen:
            duplicates.append(
                BatchIssue(
                    line_number=line_number,
                    source_text=raw,
                    kind="duplicate",
                    detail=f"Duplicates line {seen[canonical_url]}.",
                )
            )
            continue

        seen[canonical_url] = line_number
        items.append(
            BatchItem(
                line_number=line_number,
                source_url=source_url,
                canonical_url=canonical_url,
                identifier=identifier,
                verified_year=verified_year,
            )
        )

    return BatchPreview(
        media_type=media_type,
        queue_path=path,
        total_lines=len(lines),
        blank_lines=blank_lines,
        comment_lines=comment_lines,
        items=tuple(items),
        duplicates=tuple(duplicates),
        invalid=tuple(invalid),
    )



def _selected_edition_label(plan: AcquisitionPlan) -> str | None:
    if not plan.selected_edition_key:
        return None
    for edition in plan.audio_editions:
        if edition.key == plan.selected_edition_key:
            return edition.label
    return None


def resolve_batch_plans(
    preview: BatchPreview,
    library_root: Path,
    provider,
    *,
    verified_year_overrides: dict[str, int] | None = None,
) -> BatchPlanPreview:
    resolved: list[BatchPlanItem] = []

    verified_years = verified_year_overrides or {}

    for item in preview.items:
        external_verified_year = verified_years.get(item.identifier)

        if (
            item.verified_year is not None
            and external_verified_year is not None
            and item.verified_year != external_verified_year
        ):
            resolved.append(
                BatchPlanItem(
                    line_number=item.line_number,
                    canonical_url=item.canonical_url,
                    identifier=item.identifier,
                    status="failed",
                    title=None,
                    creator=None,
                    year=None,
                    year_provenance="conflict",
                    destination=None,
                    selected_edition=None,
                    audio_file_count=0,
                    warning_count=0,
                    warnings=(),
                    error=(
                        "Conflicting verified year values: "
                        f"queue={item.verified_year}, "
                        f"override={external_verified_year}."
                    ),
                    plan=None,
                )
            )
            continue

        verified_year = (
            item.verified_year
            if item.verified_year is not None
            else external_verified_year
        )

        try:
            archive_item = provider.identify(
                item.canonical_url,
                preview.media_type,
                year_override=verified_year,
            )
            plan = build_plan(archive_item, library_root)
        except (ProviderError, OSError, ValueError) as exc:
            resolved.append(
                BatchPlanItem(
                    line_number=item.line_number,
                    canonical_url=item.canonical_url,
                    identifier=item.identifier,
                    status="failed",
                    title=None,
                    creator=None,
                    year=None,
                    year_provenance="unresolved",
                    destination=None,
                    selected_edition=None,
                    audio_file_count=0,
                    warning_count=0,
                    warnings=(),
                    error=str(exc),
                    plan=None,
                )
            )
            continue

        warnings_list = list(plan.warnings)
        if item.verified_year is not None:
            year_provenance = "verified-queue"
        elif external_verified_year is not None:
            year_provenance = "verified-override"
        elif plan.item.year is None:
            year_provenance = "missing"
        else:
            year_provenance = "provider"

        if (
            preview.media_type is MediaType.AUDIOBOOK
            and verified_year is None
            and plan.item.year is not None
        ):
            warnings_list.append(
                "Publication/release year is provider-derived and has not been "
                "verified for canonical audiobook placement. Supply a verified "
                "year override before applying."
            )

        warnings = tuple(warnings_list)
        status = "blocked" if warnings else "actionable"
        resolved.append(
            BatchPlanItem(
                line_number=item.line_number,
                canonical_url=item.canonical_url,
                identifier=item.identifier,
                status=status,
                title=plan.item.title,
                creator=plan.item.creator,
                year=plan.item.year,
                year_provenance=year_provenance,
                destination=plan.destination,
                selected_edition=_selected_edition_label(plan),
                audio_file_count=len(plan.selected_audio),
                warning_count=len(warnings),
                warnings=warnings,
                error=None,
                plan=plan,
            )
        )

    return BatchPlanPreview(queue=preview, items=tuple(resolved))



def build_batch_execution_preview(
    plan_preview: BatchPlanPreview,
) -> BatchExecutionPreview:
    items: list[BatchExecutionItem] = []
    sequence = 0

    for plan_item in plan_preview.items:
        if plan_item.status == "actionable":
            sequence += 1
            action = "execute"
            reason = None
        elif plan_item.status == "blocked":
            action = "skip-blocked"
            reason = (
                plan_item.warnings[0]
                if plan_item.warnings
                else "Plan is blocked."
            )
        else:
            action = "skip-failed"
            reason = plan_item.error or "Plan resolution failed."

        items.append(
            BatchExecutionItem(
                sequence=sequence if action == "execute" else 0,
                line_number=plan_item.line_number,
                identifier=plan_item.identifier,
                action=action,
                title=plan_item.title,
                destination=plan_item.destination,
                reason=reason,
            )
        )

    return BatchExecutionPreview(
        plan_preview=plan_preview,
        items=tuple(items),
    )



def execute_batch_fetches(
    execution_preview: BatchExecutionPreview,
    staging_root: Path,
    *,
    retry_failed: bool = False,
    fetcher=fetch_plan_to_staging,
) -> BatchFetchSummary:
    results: list[BatchFetchItem] = []
    plans_by_line = {
        item.line_number: item
        for item in execution_preview.plan_preview.items
    }

    queue = execution_preview.plan_preview.queue
    state_root = staging_root.parent / "state"
    state_path, state = load_batch_state(
        state_root,
        queue.media_type,
        queue.queue_path,
    )
    state_items = state.get("items") or {}

    for execution_item in execution_preview.items:
        plan_item = plans_by_line[execution_item.line_number]
        prior = state_items.get(execution_item.identifier) or {}
        attempts = int(prior.get("attempts") or 0)

        if execution_item.action == "skip-blocked":
            record_batch_item(
                state_path,
                state,
                identifier=execution_item.identifier,
                line_number=execution_item.line_number,
                canonical_url=plan_item.canonical_url,
                status="blocked",
                job_id=None,
                staging_dir=None,
                attempts=attempts,
                error=None,
            )
            results.append(
                BatchFetchItem(
                    line_number=execution_item.line_number,
                    identifier=execution_item.identifier,
                    status="blocked",
                    job_id=None,
                    staging_dir=None,
                    warning_count=plan_item.warning_count,
                    error=None,
                )
            )
            continue

        if execution_item.action == "skip-failed":
            record_batch_item(
                state_path,
                state,
                identifier=execution_item.identifier,
                line_number=execution_item.line_number,
                canonical_url=plan_item.canonical_url,
                status="plan-failed",
                job_id=None,
                staging_dir=None,
                attempts=attempts,
                error=plan_item.error,
            )
            results.append(
                BatchFetchItem(
                    line_number=execution_item.line_number,
                    identifier=execution_item.identifier,
                    status="skipped-failed",
                    job_id=None,
                    staging_dir=None,
                    warning_count=0,
                    error=plan_item.error,
                )
            )
            continue

        if prior.get("status") in {"staged", "already-staged"} and staged_job_is_valid(prior):
            results.append(
                BatchFetchItem(
                    line_number=execution_item.line_number,
                    identifier=execution_item.identifier,
                    status="already-staged",
                    job_id=str(prior.get("jobId") or ""),
                    staging_dir=Path(str(prior["stagingDir"])),
                    warning_count=0,
                    error=None,
                )
            )
            continue

        discovered = discover_existing_staged_job(
            staging_root,
            execution_item.identifier,
        )
        if discovered is not None:
            job_id, staging_dir, warning_count = discovered
            record_batch_item(
                state_path,
                state,
                identifier=execution_item.identifier,
                line_number=execution_item.line_number,
                canonical_url=plan_item.canonical_url,
                status="already-staged",
                job_id=job_id,
                staging_dir=staging_dir,
                attempts=attempts,
                error=None,
            )
            results.append(
                BatchFetchItem(
                    line_number=execution_item.line_number,
                    identifier=execution_item.identifier,
                    status="already-staged",
                    job_id=job_id,
                    staging_dir=staging_dir,
                    warning_count=warning_count,
                    error=None,
                )
            )
            continue

        if prior.get("status") == "failed" and not retry_failed:
            results.append(
                BatchFetchItem(
                    line_number=execution_item.line_number,
                    identifier=execution_item.identifier,
                    status="retry-required",
                    job_id=None,
                    staging_dir=None,
                    warning_count=0,
                    error=str(prior.get("error") or "Previous fetch attempt failed."),
                )
            )
            continue

        if plan_item.plan is None:
            error = "Resolved actionable item has no retained acquisition plan."
            record_batch_item(
                state_path,
                state,
                identifier=execution_item.identifier,
                line_number=execution_item.line_number,
                canonical_url=plan_item.canonical_url,
                status="failed",
                job_id=None,
                staging_dir=None,
                attempts=attempts,
                error=error,
            )
            results.append(
                BatchFetchItem(
                    line_number=execution_item.line_number,
                    identifier=execution_item.identifier,
                    status="failed",
                    job_id=None,
                    staging_dir=None,
                    warning_count=0,
                    error=error,
                )
            )
            continue

        attempts += 1
        try:
            fetched: FetchResult = fetcher(plan_item.plan, staging_root)
        except (FetchError, BatchStateError, OSError, ValueError) as exc:
            record_batch_item(
                state_path,
                state,
                identifier=execution_item.identifier,
                line_number=execution_item.line_number,
                canonical_url=plan_item.canonical_url,
                status="failed",
                job_id=None,
                staging_dir=None,
                attempts=attempts,
                error=str(exc),
            )
            results.append(
                BatchFetchItem(
                    line_number=execution_item.line_number,
                    identifier=execution_item.identifier,
                    status="failed",
                    job_id=None,
                    staging_dir=None,
                    warning_count=0,
                    error=str(exc),
                )
            )
            continue

        record_batch_item(
            state_path,
            state,
            identifier=execution_item.identifier,
            line_number=execution_item.line_number,
            canonical_url=plan_item.canonical_url,
            status="staged",
            job_id=fetched.job_id,
            staging_dir=fetched.staging_dir,
            attempts=attempts,
            error=None,
        )
        results.append(
            BatchFetchItem(
                line_number=execution_item.line_number,
                identifier=execution_item.identifier,
                status="staged",
                job_id=fetched.job_id,
                staging_dir=fetched.staging_dir,
                warning_count=len(fetched.warnings),
                error=None,
            )
        )

    return BatchFetchSummary(
        execution_preview=execution_preview,
        items=tuple(results),
        state_path=state_path,
    )
