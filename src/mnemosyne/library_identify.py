from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any

from .config import runtime_root
from .ebook_metadata import EbookMetadataError, inspect_ebook_metadata
from .library_scan import LibraryScanMedia
from .paths import sanitize_component


class LibraryIdentifyError(RuntimeError):
    """Existing-library identification planning could not be completed safely."""


class LibraryConfidence(str, Enum):
    VERIFIED = "VERIFIED"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    CONFLICT = "CONFLICT"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class EbookIdentityEvidence:
    structure_author: str | None
    structure_title: str | None
    structure_series: str | None
    structure_series_index: str | None
    embedded_author: str | None
    embedded_title: str | None
    embedded_series: str | None
    embedded_series_index: str | None
    filename_author: str | None
    filename_title: str | None
    filename_year: int | None


@dataclass(frozen=True)
class LibraryIdentificationItem:
    current_path: str
    proposed_path: str | None
    author: str | None
    title: str | None
    series: str | None
    series_index: str | None
    confidence: LibraryConfidence
    action: str
    external_lookup: str
    review_required: bool
    possible_duplicate: bool
    evidence: EbookIdentityEvidence
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class LibraryIdentificationResult:
    identification_id: str
    identified_at: str
    source_scan_id: str
    source_scan_path: Path
    media: LibraryScanMedia
    library_root: Path
    category_root: Path
    report_path: Path
    item_count: int
    keep_count: int
    would_move_count: int
    review_count: int
    high_confidence_count: int
    items: tuple[LibraryIdentificationItem, ...]


_EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw", ".azw3", ".djvu"}
_WS = re.compile(r"\s+")
_TRAILING_YEAR = re.compile(r"^(?P<body>.+?)\s+\((?P<year>(?:18|19|20)\d{2})\)$")


def _normalize(value: str | None) -> str:
    return _WS.sub(" ", value or "").strip().casefold()


def _decimal_text(value: object) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return raw
    if not number.is_finite():
        return raw
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _same(field: str, left: str | None, right: str | None) -> bool:
    if field == "series-index":
        lvalue = _decimal_text(left)
        rvalue = _decimal_text(right)
        if lvalue is None or rvalue is None:
            return lvalue == rvalue
        try:
            return Decimal(lvalue) == Decimal(rvalue)
        except InvalidOperation:
            return _normalize(lvalue) == _normalize(rvalue)
    return _normalize(left) == _normalize(right)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryIdentifyError(f"Could not read scan report {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LibraryIdentifyError(f"Scan report must contain a JSON object: {path}")
    return payload


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _scan_dir() -> Path:
    return runtime_root() / "state" / "scans"


def _resolve_scan_report(scan_report: Path | None) -> Path:
    scan_dir = _scan_dir().resolve()

    if scan_report is not None:
        raw = Path(scan_report)
        candidate = raw.resolve() if raw.is_absolute() else (scan_dir / raw).resolve()
        if candidate.parent != scan_dir:
            raise LibraryIdentifyError(
                "Identification accepts only direct scan reports from Mnemosyne state/scans."
            )
        if candidate.is_symlink():
            raise LibraryIdentifyError("Symlinked scan reports are not accepted.")
        if not candidate.is_file():
            raise LibraryIdentifyError(f"Scan report does not exist: {candidate}")
        return candidate

    if not scan_dir.is_dir():
        raise LibraryIdentifyError(
            "No durable library scan reports exist; run library-scan first."
        )

    candidates = [
        path
        for path in scan_dir.glob("library-scan-ebook-*.json")
        if path.is_file() and not path.is_symlink()
    ]
    if not candidates:
        raise LibraryIdentifyError(
            "No durable eBook scan report exists; run library-scan --media ebook first."
        )

    candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return candidates[0].resolve()


def _validate_scan(
    report_path: Path,
    *,
    library_root: Path,
    ebooks_dir: str,
) -> tuple[dict[str, Any], Path, Path]:
    scan = _read_json(report_path)

    if scan.get("schemaVersion") != 1:
        raise LibraryIdentifyError(
            f"Unsupported library scan schema version: {scan.get('schemaVersion')!r}"
        )
    if scan.get("media") != LibraryScanMedia.EBOOK.value:
        raise LibraryIdentifyError("Only eBook scan reports are supported in this slice.")

    expected_root = library_root.resolve()
    expected_category = (expected_root / ebooks_dir).resolve()
    reported_root = Path(str(scan.get("libraryRoot") or "")).resolve()
    reported_category = Path(str(scan.get("categoryRoot") or "")).resolve()

    if reported_root != expected_root or reported_category != expected_category:
        raise LibraryIdentifyError(
            "Scan report no longer matches the configured library root/category; re-run library-scan."
        )
    if not expected_category.is_dir():
        raise LibraryIdentifyError(
            f"Configured eBook category no longer exists: {expected_category}"
        )

    safety = scan.get("safety") or {}
    if safety.get("libraryModified") is not False:
        raise LibraryIdentifyError(
            "Scan report does not carry the expected read-only safety provenance."
        )

    return scan, expected_root, expected_category


def _rel_key(value: str | Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in Path(value).parts)


def _current_media_files(category_root: Path) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for current, dirnames, filenames in os.walk(category_root, followlinks=False):
        current_path = Path(current)

        kept: list[str] = []
        for dirname in dirnames:
            child = current_path / dirname
            if not child.is_symlink():
                kept.append(dirname)
        dirnames[:] = kept

        for filename in filenames:
            path = current_path / filename
            if path.is_symlink():
                continue
            if path.suffix.lower() in _EBOOK_EXTENSIONS:
                result.add(_rel_key(path.relative_to(category_root)))
    return result


def _validated_item_files(
    scan: dict[str, Any],
    category_root: Path,
) -> list[tuple[dict[str, Any], list[Path]]]:
    raw_items = scan.get("items")
    if not isinstance(raw_items, list):
        raise LibraryIdentifyError("Scan report items are missing or invalid.")

    expected_file_keys: set[tuple[str, ...]] = set()
    validated: list[tuple[dict[str, Any], list[Path]]] = []

    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise LibraryIdentifyError("Scan report contains a non-object item.")

        raw_files = raw_item.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise LibraryIdentifyError("Scan item has no media-file provenance.")

        paths: list[Path] = []
        total = 0
        for raw_file in raw_files:
            rel = Path(str(raw_file))
            candidate = category_root / rel
            if candidate.is_symlink():
                raise LibraryIdentifyError(
                    f"Scan snapshot is stale because a media path became a symlink: {candidate}"
                )
            resolved = candidate.resolve()
            if not resolved.is_relative_to(category_root):
                raise LibraryIdentifyError(
                    f"Scan item escapes the configured eBook category: {raw_file}"
                )
            if not resolved.is_file():
                raise LibraryIdentifyError(
                    "Scan snapshot is stale because a scanned media file is missing; "
                    "re-run library-scan."
                )
            try:
                total += resolved.stat().st_size
            except OSError as exc:
                raise LibraryIdentifyError(
                    f"Could not stat scanned media file {resolved}: {exc}"
                ) from exc

            key = _rel_key(rel)
            if key in expected_file_keys:
                raise LibraryIdentifyError(
                    f"Scan report references the same media file more than once: {raw_file}"
                )
            expected_file_keys.add(key)
            paths.append(resolved)

        expected_total = raw_item.get("total_bytes")
        if not isinstance(expected_total, int) or total != expected_total:
            raise LibraryIdentifyError(
                "Scan snapshot is stale because media bytes changed after scanning; "
                "re-run library-scan."
            )

        validated.append((raw_item, paths))

    if _current_media_files(category_root) != expected_file_keys:
        raise LibraryIdentifyError(
            "Scan snapshot is stale because the eBook file set changed after scanning; "
            "re-run library-scan."
        )

    return validated


def _structure_evidence(raw_item: dict[str, Any]) -> tuple[
    str | None, str | None, str | None, str | None
]:
    structure = str(raw_item.get("structure") or "")
    author = str(raw_item.get("probable_author") or "").strip() or None
    title = str(raw_item.get("probable_title") or "").strip() or None
    series = str(raw_item.get("probable_series") or "").strip() or None
    index = _decimal_text(raw_item.get("series_index"))

    if structure == "author/book":
        return author, title, None, None
    if structure == "author/series/book":
        return author, title, series, index

    relative = str(raw_item.get("relative_path") or "")
    if relative == "<eBook-root>":
        return None, None, None, None

    parts = Path(relative).parts
    if len(parts) == 1:
        # The directory name is structurally an author candidate; the scan's title
        # came from the filename and is intentionally not upgraded to structure evidence.
        return parts[0], None, None, None
    if len(parts) > 3:
        return parts[0], parts[-1], None, None
    return None, None, None, None


def _embedded_evidence(files: list[Path]) -> tuple[
    str | None, str | None, str | None, str | None, list[str]
]:
    epubs = [path for path in files if path.suffix.lower() == ".epub"]
    if not epubs:
        return None, None, None, None, []
    if len(epubs) > 1:
        return None, None, None, None, [
            "Multiple EPUB files prevent selecting one embedded-metadata source."
        ]

    try:
        inspection = inspect_ebook_metadata(epubs[0])
    except (EbookMetadataError, OSError) as exc:
        return None, None, None, None, [
            f"Embedded EPUB metadata could not be inspected: {exc}"
        ]

    return (
        inspection.creators[0] if inspection.creators else None,
        inspection.title,
        inspection.series_name,
        _decimal_text(inspection.series_index),
        [],
    )


def _filename_evidence(
    files: list[Path],
    *,
    known_author: str | None,
) -> tuple[str | None, str | None, int | None, list[str]]:
    stems: dict[str, str] = {}
    for path in files:
        stem = _WS.sub(" ", path.stem).strip()
        stems.setdefault(stem.casefold(), stem)

    if len(stems) != 1:
        return None, None, None, [
            "Representation filenames do not agree on one work-name stem."
        ]

    stem = next(iter(stems.values()))
    year: int | None = None
    match = _TRAILING_YEAR.match(stem)
    body = stem
    if match:
        body = match.group("body").strip()
        year = int(match.group("year"))

    if " - " not in body:
        return None, body or None, year, []

    title_part, author_part = (part.strip() for part in body.rsplit(" - ", 1))
    if known_author and not _same("author", author_part, known_author):
        # A hyphenated title is much more likely than a contradictory filename author.
        return None, body or None, year, [
            "Filename contains ' - ' but its trailing text does not agree with known author evidence; "
            "the full stem is retained as title-only evidence."
        ]

    return author_part or None, title_part or None, year, []


def _select(
    field: str,
    values: list[tuple[str, str | None]],
) -> tuple[str | None, str | None, bool, str | None]:
    present = [(source, value) for source, value in values if value is not None and str(value).strip()]
    if not present:
        return None, None, False, None

    selected_source, selected = present[0]
    conflicting = [
        (source, value)
        for source, value in present[1:]
        if not _same(field, selected, value)
    ]
    if conflicting:
        details = ", ".join(
            [f"{selected_source}={selected!r}"]
            + [f"{source}={value!r}" for source, value in conflicting]
        )
        return selected, selected_source, True, f"{field} evidence conflicts: {details}."

    return selected, selected_source, False, None


def _format_series_index(value: str | None) -> str | None:
    normalized = _decimal_text(value)
    if normalized is None:
        return None
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return normalized
    if not number.is_finite() or number < 0:
        return normalized
    text = format(number, "f")
    whole, dot, fraction = text.partition(".")
    whole = whole.zfill(2)
    if dot:
        fraction = fraction.rstrip("0")
        return f"{whole}.{fraction}" if fraction else whole
    return whole


def _canonical_relative(
    *,
    author: str,
    title: str,
    series: str | None,
    series_index: str | None,
) -> str:
    author_part = sanitize_component(author)
    title_part = sanitize_component(title)
    if not series:
        return str(Path(author_part) / title_part)

    series_part = sanitize_component(series)
    formatted_index = _format_series_index(series_index)
    book_part = f"{formatted_index} - {title_part}" if formatted_index else title_part
    return str(Path(author_part) / series_part / book_part)


def _path_same(left: str, right: str) -> bool:
    return _rel_key(left) == _rel_key(right)


def _identify_item(raw_item: dict[str, Any], files: list[Path]) -> LibraryIdentificationItem:
    reasons: list[str] = []

    s_author, s_title, s_series, s_index = _structure_evidence(raw_item)
    e_author, e_title, e_series, e_index, embedded_reasons = _embedded_evidence(files)
    reasons.extend(embedded_reasons)

    known_author = e_author or s_author
    f_author, f_title, f_year, filename_reasons = _filename_evidence(
        files,
        known_author=known_author,
    )
    reasons.extend(filename_reasons)

    evidence = EbookIdentityEvidence(
        structure_author=s_author,
        structure_title=s_title,
        structure_series=s_series,
        structure_series_index=s_index,
        embedded_author=e_author,
        embedded_title=e_title,
        embedded_series=e_series,
        embedded_series_index=e_index,
        filename_author=f_author,
        filename_title=f_title,
        filename_year=f_year,
    )

    author, author_source, author_conflict, author_reason = _select(
        "author",
        [("embedded", e_author), ("structure", s_author), ("filename", f_author)],
    )
    title, title_source, title_conflict, title_reason = _select(
        "title",
        [("embedded", e_title), ("structure", s_title), ("filename", f_title)],
    )
    series, _, series_conflict, series_reason = _select(
        "series",
        [("embedded", e_series), ("structure", s_series)],
    )
    series_index, _, index_conflict, index_reason = _select(
        "series-index",
        [("embedded", e_index), ("structure", s_index)],
    )

    for reason in (author_reason, title_reason, series_reason, index_reason):
        if reason:
            reasons.append(reason)

    possible_duplicate = bool(raw_item.get("possible_duplicate"))
    if possible_duplicate:
        reasons.append(
            "Scan marked this work as a possible duplicate; automatic identity acceptance is blocked."
        )

    conflict = any((author_conflict, title_conflict, series_conflict, index_conflict))
    if conflict or possible_duplicate:
        confidence = LibraryConfidence.CONFLICT
    elif not author or not title:
        confidence = LibraryConfidence.UNRESOLVED
        reasons.append("Author and title could not both be established from available evidence.")
    else:
        canonical = bool(raw_item.get("canonical_looking"))
        structure_core = bool(s_author and s_title)
        embedded_core = bool(e_author and e_title)
        filename_core = bool(f_author and f_title)

        if canonical and structure_core:
            confidence = LibraryConfidence.HIGH
            reasons.append(
                "Stable canonical folder structure establishes author/title without conflicting evidence."
            )
        elif embedded_core and (
            (s_author and _same("author", e_author, s_author) and s_title and _same("title", e_title, s_title))
            or (filename_core and _same("author", e_author, f_author) and _same("title", e_title, f_title))
        ):
            confidence = LibraryConfidence.HIGH
            reasons.append(
                "Embedded author/title are independently corroborated by filesystem or filename evidence."
            )
        elif embedded_core:
            confidence = LibraryConfidence.MEDIUM
            reasons.append(
                "Embedded metadata establishes author/title, but independent corroboration is incomplete."
            )
        elif author_source == "filename" and title_source == "filename":
            confidence = LibraryConfidence.LOW
            reasons.append("Author/title rely only on filename parsing.")
        else:
            confidence = LibraryConfidence.MEDIUM
            reasons.append(
                "Identity combines non-conflicting structural and filename evidence."
            )

    current_path = str(raw_item.get("relative_path") or "")
    proposed_path: str | None = None
    if confidence not in {LibraryConfidence.CONFLICT, LibraryConfidence.UNRESOLVED}:
        assert author is not None and title is not None
        proposed_path = _canonical_relative(
            author=author,
            title=title,
            series=series,
            series_index=series_index,
        )
        action = "KEEP" if _path_same(current_path, proposed_path) else "WOULD-MOVE"
    else:
        action = "REVIEW"

    if confidence in {LibraryConfidence.VERIFIED, LibraryConfidence.HIGH}:
        external_lookup = "not-required"
        review_required = False
    elif confidence in {LibraryConfidence.CONFLICT, LibraryConfidence.UNRESOLVED}:
        external_lookup = "required"
        review_required = True
    else:
        external_lookup = "recommended"
        review_required = True

    scan_issues = raw_item.get("issues")
    if isinstance(scan_issues, list):
        for issue in scan_issues:
            text = str(issue).strip()
            if text and text not in reasons:
                reasons.append(f"Scan evidence: {text}")

    return LibraryIdentificationItem(
        current_path=current_path,
        proposed_path=proposed_path,
        author=author,
        title=title,
        series=series,
        series_index=_decimal_text(series_index),
        confidence=confidence,
        action=action,
        external_lookup=external_lookup,
        review_required=review_required,
        possible_duplicate=possible_duplicate,
        evidence=evidence,
        reasons=tuple(reasons),
    )


def identify_library(
    library_root: Path,
    *,
    media: LibraryScanMedia = LibraryScanMedia.EBOOK,
    ebooks_dir: str = "eBooks",
    scan_report: Path | None = None,
) -> LibraryIdentificationResult:
    if media is not LibraryScanMedia.EBOOK:
        raise LibraryIdentifyError(f"Unsupported identification media: {media}")

    source_scan = _resolve_scan_report(scan_report)
    scan, root, category = _validate_scan(
        source_scan,
        library_root=library_root,
        ebooks_dir=ebooks_dir,
    )
    validated = _validated_item_files(scan, category)

    items = tuple(_identify_item(raw_item, files) for raw_item, files in validated)

    identification_id = (
        f"library-identification-{media.value}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    identified_at = datetime.now(timezone.utc).isoformat()
    report_path = (
        runtime_root()
        / "state"
        / "identifications"
        / f"{identification_id}.json"
    )

    result = LibraryIdentificationResult(
        identification_id=identification_id,
        identified_at=identified_at,
        source_scan_id=str(scan.get("scanId") or ""),
        source_scan_path=source_scan,
        media=media,
        library_root=root,
        category_root=category,
        report_path=report_path,
        item_count=len(items),
        keep_count=sum(item.action == "KEEP" for item in items),
        would_move_count=sum(item.action == "WOULD-MOVE" for item in items),
        review_count=sum(item.review_required for item in items),
        high_confidence_count=sum(
            item.confidence in {LibraryConfidence.VERIFIED, LibraryConfidence.HIGH}
            for item in items
        ),
        items=items,
    )

    payload = {
        "schemaVersion": 1,
        "identificationId": result.identification_id,
        "identifiedAt": result.identified_at,
        "sourceScan": {
            "scanId": result.source_scan_id,
            "path": str(result.source_scan_path),
        },
        "media": result.media.value,
        "libraryRoot": str(result.library_root),
        "categoryRoot": str(result.category_root),
        "summary": {
            "items": result.item_count,
            "keep": result.keep_count,
            "wouldMove": result.would_move_count,
            "reviewRequired": result.review_count,
            "highConfidence": result.high_confidence_count,
        },
        "confidencePolicy": {
            "states": [value.value for value in LibraryConfidence],
            "automaticPlanningEligible": [
                LibraryConfidence.VERIFIED.value,
                LibraryConfidence.HIGH.value,
            ],
            "note": (
                "VERIFIED is reserved for explicit trusted verification and is not "
                "invented from ordinary library evidence."
            ),
        },
        "items": [
            {
                "currentPath": item.current_path,
                "proposedPath": item.proposed_path,
                "identity": {
                    "author": item.author,
                    "title": item.title,
                    "series": item.series,
                    "seriesIndex": item.series_index,
                },
                "confidence": item.confidence.value,
                "action": item.action,
                "externalLookup": item.external_lookup,
                "reviewRequired": item.review_required,
                "possibleDuplicate": item.possible_duplicate,
                "evidence": asdict(item.evidence),
                "reasons": list(item.reasons),
            }
            for item in items
        ],
        "safety": {
            "filesRenamed": False,
            "filesMoved": False,
            "metadataModified": False,
            "libraryModified": False,
            "externalLookupPerformed": False,
            "reportWrittenOutsideLibrary": True,
        },
    }
    _write_json_atomic(report_path, payload)
    return result
