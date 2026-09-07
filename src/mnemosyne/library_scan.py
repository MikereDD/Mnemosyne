from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .config import runtime_root
from .ebook_metadata import EbookMetadataError, inspect_ebook_metadata


class LibraryScanError(RuntimeError):
    """Existing-library scanning could not be completed safely."""


class LibraryScanMedia(str, Enum):
    EBOOK = "ebook"


@dataclass(frozen=True)
class LibraryScanItem:
    relative_path: str
    structure: str
    probable_author: str | None
    probable_series: str | None
    probable_title: str | None
    series_index: str | None
    probable_year: int | None
    files: tuple[str, ...]
    file_count: int
    total_bytes: int
    extensions: tuple[str, ...]
    embedded_metadata: str
    canonical_looking: bool
    needs_identification: bool
    possible_duplicate: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class LibraryScanResult:
    scan_id: str
    scanned_at: str
    media: LibraryScanMedia
    library_root: Path
    category_root: Path
    report_path: Path
    item_count: int
    file_count: int
    total_bytes: int
    canonical_count: int
    needs_identification_count: int
    possible_duplicate_count: int
    ignored_non_media_files: int
    skipped_symlinks: int
    items: tuple[LibraryScanItem, ...]


_EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw", ".azw3", ".djvu"}
_SERIES_INDEX = re.compile(r"^(?P<index>\d{2,}(?:\.\d+)?)\s+-\s+(?P<title>.+)$")
_YEAR = re.compile(r"(?<!\d)((?:18|19|20)\d{2})(?!\d)")
_WS = re.compile(r"\s+")


def _normalize(value: str | None) -> str:
    return _WS.sub(" ", value or "").strip().casefold()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        json.loads(temp.read_text(encoding="utf-8"))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _resolve_category_root(library_root: Path, category_name: str) -> tuple[Path, Path]:
    root = library_root.resolve()
    category = (root / category_name).resolve()
    if category == root:
        raise LibraryScanError("Media category root may not be the library root itself.")
    if not category.is_relative_to(root):
        raise LibraryScanError(f"Configured media category resolves outside the library root: {category}")
    if not category.is_dir():
        raise LibraryScanError(f"eBook library directory does not exist: {category}")
    return root, category


def _collect_ebook_files(category_root: Path) -> tuple[list[Path], int, int]:
    media: list[Path] = []
    ignored = 0
    skipped = 0
    for current, dirnames, filenames in os.walk(category_root, followlinks=False):
        current_path = Path(current)
        kept: list[str] = []
        for dirname in dirnames:
            path = current_path / dirname
            if path.is_symlink():
                skipped += 1
            else:
                kept.append(dirname)
        dirnames[:] = kept
        for filename in filenames:
            path = current_path / filename
            if path.is_symlink():
                skipped += 1
            elif path.suffix.lower() in _EBOOK_EXTENSIONS:
                media.append(path)
            else:
                ignored += 1
    return sorted(media, key=lambda p: str(p).casefold()), ignored, skipped


def _structure(category_root: Path, parent: Path, files: list[Path]):
    parts = parent.relative_to(category_root).parts
    issues: list[str] = []
    structure = "noncanonical"
    author = series = title = index = None
    canonical = False

    if len(parts) == 2:
        structure = "author/book"
        author, title = parts
        canonical = True
    elif len(parts) == 3:
        structure = "author/series/book"
        author, series, book = parts
        match = _SERIES_INDEX.match(book)
        if match:
            index = match.group("index")
            title = match.group("title").strip()
        else:
            title = book
        canonical = True
    elif len(parts) == 1:
        author = parts[0]
        title = files[0].stem
        issues.append("eBook file is directly under the author directory.")
    elif not parts:
        title = files[0].stem
        issues.append("eBook file is directly under the eBook library root.")
    else:
        author = parts[0]
        title = parts[-1]
        issues.append("eBook nesting is deeper than Author/Series/Book and requires identification.")

    if not author:
        issues.append("Author cannot be inferred from stable folder structure.")
    if not title:
        issues.append("Book title cannot be inferred from stable folder structure.")
    return structure, author, series, title, index, canonical, issues


def _embedded(files: list[Path]):
    epub = next((p for p in files if p.suffix.lower() == ".epub"), None)
    if epub is None:
        return "unsupported", None, None, None, []
    try:
        value = inspect_ebook_metadata(epub)
    except (EbookMetadataError, OSError) as exc:
        return "unreadable", None, None, None, [f"EPUB embedded metadata could not be inspected: {exc}"]
    creator = value.creators[0] if value.creators else None
    available = any((value.title, creator, value.series_name, value.language, value.publisher, value.subjects))
    return "available" if available else "sparse", value.title, creator, value.series_name, []


def _item(category_root: Path, parent: Path, files: list[Path]) -> LibraryScanItem:
    structure, author, series, title, index, canonical, issues = _structure(category_root, parent, files)
    metadata_state, e_title, e_creator, e_series, e_issues = _embedded(files)
    issues.extend(e_issues)

    if e_title and title and _normalize(e_title) != _normalize(title):
        issues.append(f"Embedded title disagrees with folder structure: {e_title!r}.")
    if e_creator and author and _normalize(e_creator) != _normalize(author):
        issues.append(f"Embedded creator disagrees with folder structure: {e_creator!r}.")
    if e_series and series and _normalize(e_series) != _normalize(series):
        issues.append(f"Embedded series disagrees with folder structure: {e_series!r}.")

    ext_counts: dict[str, int] = {}
    total = 0
    for path in files:
        ext = path.suffix.lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
        try:
            total += path.stat().st_size
        except OSError as exc:
            raise LibraryScanError(f"Could not stat library file {path}: {exc}") from exc

    same_format = any(count > 1 for count in ext_counts.values())
    if same_format:
        issues.append("Multiple files of the same eBook format exist in one structural book directory.")

    year = None
    for text in [*parent.relative_to(category_root).parts, *(p.name for p in files)]:
        match = _YEAR.search(text)
        if match:
            year = int(match.group(1))
            break

    rel = str(parent.relative_to(category_root))
    if rel == ".":
        rel = "<eBook-root>"

    return LibraryScanItem(
        relative_path=rel,
        structure=structure,
        probable_author=author or e_creator,
        probable_series=series or e_series,
        probable_title=title or e_title,
        series_index=index,
        probable_year=year,
        files=tuple(str(p.relative_to(category_root)) for p in files),
        file_count=len(files),
        total_bytes=total,
        extensions=tuple(sorted(ext_counts)),
        embedded_metadata=metadata_state,
        canonical_looking=canonical,
        needs_identification=bool(issues) or not canonical,
        possible_duplicate=same_format,
        issues=tuple(issues),
    )


def _mark_duplicates(items: list[LibraryScanItem]) -> list[LibraryScanItem]:
    keys: dict[tuple[str, str], list[int]] = {}
    for i, item in enumerate(items):
        key = (_normalize(item.probable_author), _normalize(item.probable_title))
        if all(key):
            keys.setdefault(key, []).append(i)
    duplicate_indexes = {i for group in keys.values() if len(group) > 1 for i in group}

    out: list[LibraryScanItem] = []
    for i, item in enumerate(items):
        if i not in duplicate_indexes:
            out.append(item)
            continue
        note = "Probable author/title also appears in another structural book directory."
        issues = item.issues if note in item.issues else (*item.issues, note)
        out.append(replace(item, possible_duplicate=True, needs_identification=True, issues=issues))
    return out


def scan_library(
    library_root: Path,
    *,
    media: LibraryScanMedia = LibraryScanMedia.EBOOK,
    ebooks_dir: str = "eBooks",
) -> LibraryScanResult:
    if media is not LibraryScanMedia.EBOOK:
        raise LibraryScanError(f"Unsupported library scan media: {media}")

    root, category = _resolve_category_root(library_root, ebooks_dir)
    media_files, ignored, skipped = _collect_ebook_files(category)

    grouped: dict[Path, list[Path]] = {}
    for path in media_files:
        grouped.setdefault(path.parent, []).append(path)

    items = _mark_duplicates([
        _item(category, parent, sorted(files, key=lambda p: p.name.casefold()))
        for parent, files in sorted(grouped.items(), key=lambda pair: str(pair[0]).casefold())
    ])

    scan_id = f"library-scan-{media.value}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    scanned_at = datetime.now(timezone.utc).isoformat()
    report = runtime_root() / "state" / "scans" / f"{scan_id}.json"

    result = LibraryScanResult(
        scan_id=scan_id,
        scanned_at=scanned_at,
        media=media,
        library_root=root,
        category_root=category,
        report_path=report,
        item_count=len(items),
        file_count=sum(i.file_count for i in items),
        total_bytes=sum(i.total_bytes for i in items),
        canonical_count=sum(i.canonical_looking for i in items),
        needs_identification_count=sum(i.needs_identification for i in items),
        possible_duplicate_count=sum(i.possible_duplicate for i in items),
        ignored_non_media_files=ignored,
        skipped_symlinks=skipped,
        items=tuple(items),
    )

    payload = {
        "schemaVersion": 1,
        "scanId": result.scan_id,
        "scannedAt": result.scanned_at,
        "media": result.media.value,
        "libraryRoot": str(result.library_root),
        "categoryRoot": str(result.category_root),
        "summary": {
            "items": result.item_count,
            "files": result.file_count,
            "totalBytes": result.total_bytes,
            "canonicalLooking": result.canonical_count,
            "needsIdentification": result.needs_identification_count,
            "possibleDuplicates": result.possible_duplicate_count,
            "ignoredNonMediaFiles": result.ignored_non_media_files,
            "skippedSymlinks": result.skipped_symlinks,
        },
        "items": [
            {
                **asdict(item),
                "files": list(item.files),
                "extensions": list(item.extensions),
                "issues": list(item.issues),
            }
            for item in result.items
        ],
        "safety": {
            "filesRenamed": False,
            "filesMoved": False,
            "metadataModified": False,
            "libraryModified": False,
            "reportWrittenOutsideLibrary": True,
        },
    }
    _write_json_atomic(report, payload)
    return result
