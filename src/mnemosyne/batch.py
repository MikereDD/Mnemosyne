from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import runtime_root
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
            canonical_url, identifier = _normalize_archive_url(stripped)
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
                source_url=stripped,
                canonical_url=canonical_url,
                identifier=identifier,
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
        verified_year = verified_years.get(item.identifier)

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
                )
            )
            continue

        warnings_list = list(plan.warnings)
        year_provenance = "verified-override" if verified_year is not None else "provider"

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
            )
        )

    return BatchPlanPreview(queue=preview, items=tuple(resolved))
