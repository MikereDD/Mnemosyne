from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import runtime_root
from .library_identify import (
    LibraryConfidence,
    LibraryIdentifyError,
    _identify_item,
    _validate_scan,
    _validated_item_files,
)
from .library_scan import LibraryScanMedia
from .paths import sanitize_component


class LibraryPlanError(RuntimeError):
    """Existing-library filesystem planning could not be completed safely."""


@dataclass(frozen=True)
class LibraryPlanRepresentation:
    source: str
    destination: str | None
    extension: str
    size_bytes: int
    sha256: str
    action: str


@dataclass(frozen=True)
class LibraryPlanItem:
    current_path: str
    target_path: str | None
    confidence: LibraryConfidence
    status: str
    directory_action: str
    destination_exists: bool
    case_only_path_change: bool
    directories_to_create: tuple[str, ...]
    representations: tuple[LibraryPlanRepresentation, ...]
    blocked_reasons: tuple[str, ...]


@dataclass(frozen=True)
class LibraryPlanResult:
    plan_id: str
    planned_at: str
    source_identification_id: str
    source_identification_path: Path
    source_scan_id: str
    source_scan_path: Path
    media: LibraryScanMedia
    library_root: Path
    category_root: Path
    report_path: Path
    item_count: int
    keep_count: int
    move_count: int
    blocked_count: int
    representation_count: int
    items: tuple[LibraryPlanItem, ...]


_EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw", ".azw3", ".djvu"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryPlanError(f"Could not read report {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LibraryPlanError(f"Report must contain a JSON object: {path}")
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


def _identification_dir() -> Path:
    return runtime_root() / "state" / "identifications"


def _scan_dir() -> Path:
    return runtime_root() / "state" / "scans"


def _resolve_identification_report(report: Path | None) -> Path:
    state_dir = _identification_dir().resolve()

    if report is not None:
        raw = Path(report)
        candidate = raw.resolve() if raw.is_absolute() else (state_dir / raw).resolve()
        if candidate.parent != state_dir:
            raise LibraryPlanError(
                "Planning accepts only direct identification reports from "
                "Mnemosyne state/identifications."
            )
        if candidate.is_symlink():
            raise LibraryPlanError("Symlinked identification reports are not accepted.")
        if not candidate.is_file():
            raise LibraryPlanError(f"Identification report does not exist: {candidate}")
        return candidate

    if not state_dir.is_dir():
        raise LibraryPlanError(
            "No durable identification reports exist; run library-identify first."
        )

    candidates = [
        path
        for path in state_dir.glob("library-identification-ebook-*.json")
        if path.is_file() and not path.is_symlink()
    ]
    if not candidates:
        raise LibraryPlanError(
            "No durable eBook identification report exists; "
            "run library-identify --media ebook first."
        )

    candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return candidates[0].resolve()


def _resolve_source_scan(
    identification: dict[str, Any],
) -> tuple[str, Path]:
    source = identification.get("sourceScan")
    if not isinstance(source, dict):
        raise LibraryPlanError("Identification report is missing source-scan provenance.")

    scan_id = str(source.get("scanId") or "").strip()
    raw_path = str(source.get("path") or "").strip()
    if not scan_id or not raw_path:
        raise LibraryPlanError("Identification source-scan provenance is incomplete.")

    scan_dir = _scan_dir().resolve()
    candidate = Path(raw_path).resolve()
    if candidate.parent != scan_dir:
        raise LibraryPlanError(
            "Identification points to a scan outside Mnemosyne state/scans."
        )
    if candidate.is_symlink():
        raise LibraryPlanError("Symlinked source scan reports are not accepted.")
    if not candidate.is_file():
        raise LibraryPlanError(
            f"Identification source scan no longer exists: {candidate}"
        )

    return scan_id, candidate


def _validate_identification(
    report_path: Path,
    *,
    library_root: Path,
    ebooks_dir: str,
) -> tuple[
    dict[str, Any],
    str,
    Path,
    dict[str, Any],
    Path,
    Path,
    list[tuple[dict[str, Any], list[Path]]],
]:
    identification = _read_json(report_path)

    if identification.get("schemaVersion") != 1:
        raise LibraryPlanError(
            "Unsupported identification schema version: "
            f"{identification.get('schemaVersion')!r}"
        )
    if identification.get("media") != LibraryScanMedia.EBOOK.value:
        raise LibraryPlanError("Only eBook identification reports are supported.")

    safety = identification.get("safety") or {}
    expected_false = (
        "filesRenamed",
        "filesMoved",
        "metadataModified",
        "libraryModified",
        "externalLookupPerformed",
    )
    if any(safety.get(key) is not False for key in expected_false):
        raise LibraryPlanError(
            "Identification report does not carry the expected read-only safety provenance."
        )

    expected_root = library_root.resolve()
    expected_category = (expected_root / ebooks_dir).resolve()
    reported_root = Path(str(identification.get("libraryRoot") or "")).resolve()
    reported_category = Path(str(identification.get("categoryRoot") or "")).resolve()
    if reported_root != expected_root or reported_category != expected_category:
        raise LibraryPlanError(
            "Identification no longer matches the configured library; "
            "re-run library-scan and library-identify."
        )

    source_scan_id, source_scan_path = _resolve_source_scan(identification)
    try:
        scan, root, category = _validate_scan(
            source_scan_path,
            library_root=library_root,
            ebooks_dir=ebooks_dir,
        )
        validated = _validated_item_files(scan, category)
    except LibraryIdentifyError as exc:
        raise LibraryPlanError(str(exc)) from exc

    if str(scan.get("scanId") or "") != source_scan_id:
        raise LibraryPlanError(
            "Identification source scan ID does not match the durable scan report."
        )

    return (
        identification,
        source_scan_id,
        source_scan_path,
        scan,
        root,
        category,
        validated,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise LibraryPlanError(f"Could not hash library file {path}: {exc}") from exc
    return digest.hexdigest()


def _rel_key(value: str | Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in Path(value).parts)


def _same_path(left: str | Path, right: str | Path) -> bool:
    return _rel_key(left) == _rel_key(right)


def _identification_items(
    identification: dict[str, Any],
) -> list[dict[str, Any]]:
    items = identification.get("items")
    if not isinstance(items, list):
        raise LibraryPlanError("Identification report items are missing or invalid.")
    if any(not isinstance(item, dict) for item in items):
        raise LibraryPlanError("Identification report contains a non-object item.")
    return items


def _compare_recomputed_identity(
    recorded: dict[str, Any],
    recomputed,
) -> None:
    identity = recorded.get("identity")
    if not isinstance(identity, dict):
        raise LibraryPlanError("Identification item is missing identity provenance.")

    comparisons = {
        "currentPath": (recorded.get("currentPath"), recomputed.current_path),
        "proposedPath": (recorded.get("proposedPath"), recomputed.proposed_path),
        "confidence": (recorded.get("confidence"), recomputed.confidence.value),
        "action": (recorded.get("action"), recomputed.action),
        "author": (identity.get("author"), recomputed.author),
        "title": (identity.get("title"), recomputed.title),
        "series": (identity.get("series"), recomputed.series),
        "seriesIndex": (identity.get("seriesIndex"), recomputed.series_index),
    }
    changed = [
        key
        for key, (stored, current) in comparisons.items()
        if stored != current
    ]
    if changed:
        raise LibraryPlanError(
            "Identification evidence changed after the report was created "
            f"({', '.join(changed)}); re-run library-identify."
        )


def _missing_parent_directories(
    category_root: Path,
    target_relative: str,
) -> tuple[str, ...]:
    target = category_root / Path(target_relative)
    parents: list[Path] = []
    current = target.parent

    while current != category_root and current.is_relative_to(category_root):
        if not current.exists():
            parents.append(current)
        current = current.parent

    return tuple(
        str(path.relative_to(category_root))
        for path in reversed(parents)
    )


def _canonical_filename(title: str, extension: str) -> str:
    return f"{sanitize_component(title)}{extension.lower()}"


def _source_relative_for_item(
    category_root: Path,
    path: Path,
) -> str:
    return str(path.relative_to(category_root))


def _build_item(
    *,
    recorded: dict[str, Any],
    raw_scan_item: dict[str, Any],
    files: list[Path],
    category_root: Path,
) -> LibraryPlanItem:
    recomputed = _identify_item(raw_scan_item, files)
    _compare_recomputed_identity(recorded, recomputed)

    try:
        confidence = LibraryConfidence(str(recorded.get("confidence") or ""))
    except ValueError as exc:
        raise LibraryPlanError(
            f"Unknown identification confidence: {recorded.get('confidence')!r}"
        ) from exc

    current_path = str(recorded.get("currentPath") or "").strip()
    target_path = str(recorded.get("proposedPath") or "").strip() or None
    identity = recorded.get("identity") or {}
    title = str(identity.get("title") or "").strip() or None

    blocked: list[str] = []
    eligible = confidence in {LibraryConfidence.VERIFIED, LibraryConfidence.HIGH}
    if not eligible:
        blocked.append(
            f"{confidence.value} identities require review and are not eligible "
            "for automatic filesystem planning."
        )
    if bool(recorded.get("reviewRequired")):
        blocked.append("Identification report marks this item as requiring review.")
    if bool(recorded.get("possibleDuplicate")):
        blocked.append("Possible duplicate must be resolved before filesystem planning.")
    if not current_path:
        blocked.append("Current structural path is missing.")
    if not target_path:
        blocked.append("No canonical target path is available.")
    if not title:
        blocked.append("Canonical filename cannot be planned without a title.")

    current_full = category_root / Path(current_path)
    target_full = category_root / Path(target_path) if target_path else None

    if target_full is not None and not target_full.resolve().is_relative_to(category_root):
        blocked.append("Canonical target escapes the configured eBook category.")

    same_path = bool(target_path and _same_path(current_path, target_path))
    case_only = bool(
        target_path
        and same_path
        and Path(current_path).parts != Path(target_path).parts
    )
    if case_only:
        blocked.append(
            "Case-only directory change requires dedicated transactional handling."
        )

    destination_exists = bool(
        target_full is not None
        and target_full.exists()
        and not same_path
    )
    if destination_exists:
        blocked.append("Canonical target directory already exists.")

    ext_counts: dict[str, int] = {}
    for path in files:
        ext = path.suffix.lower()
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    repeated = [ext for ext, count in ext_counts.items() if count > 1]
    if repeated:
        blocked.append(
            "Multiple representations use the same format: "
            + ", ".join(sorted(repeated))
            + "."
        )

    representations: list[LibraryPlanRepresentation] = []
    if target_path and title:
        seen_destinations: set[tuple[str, ...]] = set()
        source_keys = {
            _rel_key(path.relative_to(category_root))
            for path in files
        }

        for path in files:
            source_rel = _source_relative_for_item(category_root, path)
            dest_name = _canonical_filename(title, path.suffix)
            destination_rel = str(Path(target_path) / dest_name)
            destination_key = _rel_key(destination_rel)

            if destination_key in seen_destinations:
                blocked.append(
                    f"More than one representation would target {destination_rel}."
                )
            seen_destinations.add(destination_key)

            destination_full = category_root / Path(destination_rel)
            if destination_full.exists() and destination_key not in source_keys:
                blocked.append(
                    f"Destination file already exists and would not be overwritten: "
                    f"{destination_rel}."
                )

            if _same_path(source_rel, destination_rel):
                action = "KEEP"
            elif _same_path(path.parent.relative_to(category_root), target_path):
                action = "RENAME"
            else:
                action = "MOVE"

            try:
                size = path.stat().st_size
            except OSError as exc:
                raise LibraryPlanError(
                    f"Could not stat library file {path}: {exc}"
                ) from exc

            representations.append(
                LibraryPlanRepresentation(
                    source=source_rel,
                    destination=destination_rel,
                    extension=path.suffix.lower(),
                    size_bytes=size,
                    sha256=_sha256(path),
                    action=action,
                )
            )

    if blocked:
        status = "BLOCKED"
        directory_action = "BLOCKED"
    elif same_path and all(rep.action == "KEEP" for rep in representations):
        status = "KEEP"
        directory_action = "KEEP"
    else:
        status = "READY"
        directory_action = "KEEP" if same_path else "MOVE"

    directories_to_create = (
        ()
        if target_path is None or destination_exists
        else _missing_parent_directories(category_root, target_path)
    )

    return LibraryPlanItem(
        current_path=current_path,
        target_path=target_path,
        confidence=confidence,
        status=status,
        directory_action=directory_action,
        destination_exists=destination_exists,
        case_only_path_change=case_only,
        directories_to_create=directories_to_create,
        representations=tuple(representations),
        blocked_reasons=tuple(dict.fromkeys(blocked)),
    )


def _cross_item_collisions(
    items: list[LibraryPlanItem],
) -> list[LibraryPlanItem]:
    target_map: dict[tuple[str, ...], list[int]] = {}
    current_map: dict[tuple[str, ...], int] = {}

    for index, item in enumerate(items):
        if item.current_path:
            current_map[_rel_key(item.current_path)] = index
        if item.target_path:
            target_map.setdefault(_rel_key(item.target_path), []).append(index)

    reasons: dict[int, list[str]] = {}

    for indexes in target_map.values():
        if len(indexes) > 1:
            for index in indexes:
                reasons.setdefault(index, []).append(
                    "More than one library item targets the same canonical directory."
                )

    for index, item in enumerate(items):
        if not item.target_path:
            continue
        target_key = _rel_key(item.target_path)
        owner = current_map.get(target_key)
        if owner is not None and owner != index:
            reasons.setdefault(index, []).append(
                "Canonical target is another item's current structural location."
            )
            reasons.setdefault(owner, []).append(
                "Another item's canonical target is this item's current structural location."
            )

    if not reasons:
        return items

    rebuilt: list[LibraryPlanItem] = []
    for index, item in enumerate(items):
        extra = reasons.get(index)
        if not extra:
            rebuilt.append(item)
            continue
        blocked = tuple(dict.fromkeys((*item.blocked_reasons, *extra)))
        rebuilt.append(
            LibraryPlanItem(
                current_path=item.current_path,
                target_path=item.target_path,
                confidence=item.confidence,
                status="BLOCKED",
                directory_action="BLOCKED",
                destination_exists=item.destination_exists,
                case_only_path_change=item.case_only_path_change,
                directories_to_create=item.directories_to_create,
                representations=item.representations,
                blocked_reasons=blocked,
            )
        )
    return rebuilt


def plan_library(
    library_root: Path,
    *,
    media: LibraryScanMedia = LibraryScanMedia.EBOOK,
    ebooks_dir: str = "eBooks",
    identification_report: Path | None = None,
) -> LibraryPlanResult:
    if media is not LibraryScanMedia.EBOOK:
        raise LibraryPlanError(f"Unsupported planning media: {media}")

    source_identification = _resolve_identification_report(identification_report)
    (
        identification,
        source_scan_id,
        source_scan_path,
        scan,
        root,
        category,
        validated,
    ) = _validate_identification(
        source_identification,
        library_root=library_root,
        ebooks_dir=ebooks_dir,
    )

    recorded_items = _identification_items(identification)
    if len(recorded_items) != len(validated):
        raise LibraryPlanError(
            "Identification item count no longer matches its source scan."
        )

    planned_items: list[LibraryPlanItem] = []
    for recorded, (raw_scan_item, files) in zip(
        recorded_items,
        validated,
        strict=True,
    ):
        if str(recorded.get("currentPath") or "") != str(
            raw_scan_item.get("relative_path") or ""
        ):
            raise LibraryPlanError(
                "Identification item order/path no longer matches its source scan."
            )
        planned_items.append(
            _build_item(
                recorded=recorded,
                raw_scan_item=raw_scan_item,
                files=files,
                category_root=category,
            )
        )

    planned_items = _cross_item_collisions(planned_items)
    items = tuple(planned_items)

    plan_id = (
        f"library-plan-{media.value}-"
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    planned_at = datetime.now(timezone.utc).isoformat()
    report_path = runtime_root() / "state" / "plans" / f"{plan_id}.json"

    result = LibraryPlanResult(
        plan_id=plan_id,
        planned_at=planned_at,
        source_identification_id=str(
            identification.get("identificationId") or ""
        ),
        source_identification_path=source_identification,
        source_scan_id=source_scan_id,
        source_scan_path=source_scan_path,
        media=media,
        library_root=root,
        category_root=category,
        report_path=report_path,
        item_count=len(items),
        keep_count=sum(item.status == "KEEP" for item in items),
        move_count=sum(item.status == "READY" for item in items),
        blocked_count=sum(item.status == "BLOCKED" for item in items),
        representation_count=sum(len(item.representations) for item in items),
        items=items,
    )

    payload = {
        "schemaVersion": 1,
        "planId": result.plan_id,
        "plannedAt": result.planned_at,
        "sourceIdentification": {
            "identificationId": result.source_identification_id,
            "path": str(result.source_identification_path),
        },
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
            "ready": result.move_count,
            "blocked": result.blocked_count,
            "representations": result.representation_count,
        },
        "items": [
            {
                "currentPath": item.current_path,
                "targetPath": item.target_path,
                "confidence": item.confidence.value,
                "status": item.status,
                "directoryAction": item.directory_action,
                "destinationExists": item.destination_exists,
                "caseOnlyPathChange": item.case_only_path_change,
                "directoriesToCreate": list(item.directories_to_create),
                "representations": [asdict(rep) for rep in item.representations],
                "blockedReasons": list(item.blocked_reasons),
            }
            for item in items
        ],
        "safety": {
            "directoriesCreated": False,
            "filesRenamed": False,
            "filesMoved": False,
            "filesOverwritten": False,
            "metadataModified": False,
            "libraryModified": False,
            "sourceHashesRecorded": True,
            "reportWrittenOutsideLibrary": True,
        },
    }
    _write_json_atomic(report_path, payload)
    return result
