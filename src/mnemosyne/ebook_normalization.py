from __future__ import annotations
from decimal import Decimal, InvalidOperation

import hashlib
import json
import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .ebook_metadata import (
    EbookMetadataError,
    EbookMetadataInspection,
    MetadataComparison,
    inspect_ebook_metadata,
)


class EbookNormalizationError(RuntimeError):
    """eBook metadata normalization could not be planned or applied safely."""


@dataclass(frozen=True)
class EbookNormalizationAction:
    field: str
    status: str
    current_value: str | None
    proposed_value: str | None
    reason: str


@dataclass(frozen=True)
class EbookNormalizationPreview:
    source_path: Path
    source_kind: str
    actions: tuple[EbookNormalizationAction, ...]
    additions: int
    preserved: int
    conflicts: int
    blocked: bool

@dataclass(frozen=True)
class EbookNormalizationResult:
    job_dir: Path
    source_path: Path
    package_path: str
    transaction_id: str
    additions: tuple[str, ...]
    original_sha256: str
    normalized_sha256: str
    original_size: int
    normalized_size: int
    normalization_report_path: Path
    fetch_report_path: Path


def _comparison_by_field(
    inspection: EbookMetadataInspection,
    field: str,
) -> MetadataComparison | None:
    for comparison in inspection.comparisons:
        if comparison.field == field:
            return comparison
    return None


def _action_from_comparison(
    comparison: MetadataComparison | None,
    *,
    field: str,
    fallback_current: str | None,
    fallback_proposed: str | None,
) -> EbookNormalizationAction:
    if comparison is None:
        return EbookNormalizationAction(
            field=field,
            status="preserve",
            current_value=fallback_current,
            proposed_value=None,
            reason="No normalization rule exists for this field yet.",
        )

    if comparison.status == "match":
        return EbookNormalizationAction(
            field=field,
            status="preserve",
            current_value=comparison.embedded,
            proposed_value=None,
            reason="Embedded metadata already agrees with verified provenance.",
        )

    if comparison.status == "missing-embedded":
        if comparison.provenance:
            return EbookNormalizationAction(
                field=field,
                status="add",
                current_value=None,
                proposed_value=comparison.provenance,
                reason="Embedded metadata is missing and verified provenance is available.",
            )
        return EbookNormalizationAction(
            field=field,
            status="preserve",
            current_value=None,
            proposed_value=None,
            reason="Embedded metadata is missing, but no verified provenance value is available.",
        )

    if comparison.status == "conflict":
        return EbookNormalizationAction(
            field=field,
            status="conflict",
            current_value=comparison.embedded,
            proposed_value=comparison.provenance,
            reason=(
                "Embedded metadata conflicts with verified provenance; "
                "Mnemosyne will not overwrite it silently."
            ),
        )

    return EbookNormalizationAction(
        field=field,
        status="preserve",
        current_value=comparison.embedded or fallback_current,
        proposed_value=None,
        reason="Field is evidence-only or unverified and will not be mutated automatically.",
    )


def preview_ebook_normalization(path: Path) -> EbookNormalizationPreview:
    try:
        inspection = inspect_ebook_metadata(path)
    except EbookMetadataError as exc:
        raise EbookNormalizationError(str(exc)) from exc

    title_action = _action_from_comparison(
        _comparison_by_field(inspection, "title"),
        field="title",
        fallback_current=inspection.title,
        fallback_proposed=inspection.provenance_title,
    )

    creator_action = _action_from_comparison(
        _comparison_by_field(inspection, "creator"),
        field="creator",
        fallback_current=(
            "; ".join(inspection.creators) if inspection.creators else None
        ),
        fallback_proposed=inspection.provenance_creator,
    )

    series_action = _action_from_comparison(
        _comparison_by_field(inspection, "series"),
        field="series",
        fallback_current=inspection.series_name,
        fallback_proposed=inspection.provenance_series,
    )

    series_index_action = _action_from_comparison(
        _comparison_by_field(inspection, "series-index"),
        field="series-index",
        fallback_current=inspection.series_index,
        fallback_proposed=inspection.provenance_series_index,
    )

    actions: list[EbookNormalizationAction] = [
        title_action,
        creator_action,
        series_action,
        series_index_action,
        EbookNormalizationAction(
            field="publication-date",
            status="preserve",
            current_value="; ".join(inspection.dates) if inspection.dates else None,
            proposed_value=None,
            reason=(
                "EPUB dates may describe the digital edition rather than the original work. "
                "No automatic publication-date mutation is permitted yet."
            ),
        ),
        EbookNormalizationAction(
            field="language",
            status="preserve",
            current_value=inspection.language,
            proposed_value=None,
            reason=(
                "Language is retained when present; no verified provenance source is wired "
                "for automatic enrichment yet."
            ),
        ),
        EbookNormalizationAction(
            field="publisher",
            status="preserve",
            current_value=inspection.publisher,
            proposed_value=None,
            reason="Publisher remains evidence-only until a verified bibliographic source is available.",
        ),
        EbookNormalizationAction(
            field="subjects",
            status="preserve",
            current_value=(
                "; ".join(inspection.subjects) if inspection.subjects else None
            ),
            proposed_value=None,
            reason="Subjects remain descriptive metadata and are not filesystem authority.",
        ),
    ]

    additions = sum(action.status == "add" for action in actions)
    preserved = sum(action.status == "preserve" for action in actions)
    conflicts = sum(action.status == "conflict" for action in actions)

    return EbookNormalizationPreview(
        source_path=inspection.source_path,
        source_kind=inspection.source_kind,
        actions=tuple(actions),
        additions=additions,
        preserved=preserved,
        conflicts=conflicts,
        blocked=conflicts > 0,
    )

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EbookNormalizationError(
            f"Could not read JSON report {path}: {exc}"
        ) from exc


def _write_json_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        _read_json(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _package_path(epub: Path) -> str:
    try:
        with zipfile.ZipFile(epub) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise EbookNormalizationError(
            f"Could not resolve EPUB package document: {exc}"
        ) from exc

    for element in container.iter():
        if element.tag.rsplit("}", 1)[-1] == "rootfile":
            value = (element.attrib.get("full-path") or "").strip()
            if value:
                return value
    raise EbookNormalizationError(
        "EPUB container.xml does not declare a package document."
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _epub_major_version(root: ET.Element) -> int:
    raw = str(root.attrib.get("version") or "").strip()
    try:
        return int(raw.split(".", 1)[0])
    except (TypeError, ValueError):
        return 2


def _unique_meta_id(metadata: ET.Element, base: str) -> str:
    existing = {
        str(element.attrib.get("id") or "")
        for element in metadata.iter()
    }
    if base not in existing:
        return base

    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def _existing_series_style(
    metadata: ET.Element,
) -> tuple[str | None, ET.Element | None]:
    for element in metadata.iter():
        if _local_name(element.tag) != "meta":
            continue
        name = str(element.attrib.get("name") or "").strip().casefold()
        if name == "calibre:series":
            return "calibre", element

    for element in metadata.iter():
        if _local_name(element.tag) != "meta":
            continue
        if str(element.attrib.get("property") or "").strip() != "belongs-to-collection":
            continue

        element_id = str(element.attrib.get("id") or "").strip()
        if not element_id:
            continue

        ref = f"#{element_id}"
        for refinement in metadata.iter():
            if _local_name(refinement.tag) != "meta":
                continue
            if str(refinement.attrib.get("refines") or "").strip() != ref:
                continue
            if str(refinement.attrib.get("property") or "").strip() != "collection-type":
                continue
            if (refinement.text or "").strip().casefold() == "series":
                return "epub3", element

    return None, None


def _rewrite_package(package_bytes: bytes, additions: dict[str, str]) -> bytes:
    try:
        root = ET.fromstring(package_bytes)
    except ET.ParseError as exc:
        raise EbookNormalizationError(
            f"Could not parse EPUB package document: {exc}"
        ) from exc

    metadata = next(
        (child for child in root if _local_name(child.tag) == "metadata"),
        None,
    )
    if metadata is None:
        raise EbookNormalizationError(
            "EPUB package document has no metadata section."
        )

    dc = "http://purl.org/dc/elements/1.1/"
    opf = "http://www.idpf.org/2007/opf"

    if "title" in additions:
        node = ET.Element(f"{{{dc}}}title")
        node.text = additions["title"]
        metadata.append(node)

    if "creator" in additions:
        node = ET.Element(f"{{{dc}}}creator")
        node.text = additions["creator"]
        metadata.append(node)

    series_name = additions.get("series")
    series_index = additions.get("series-index")

    if series_name is not None or series_index is not None:
        style, existing_series = _existing_series_style(metadata)
        major = _epub_major_version(root)

        if series_name is not None:
            if major >= 3:
                series_id = _unique_meta_id(root, "mnemosyne-series")

                node = ET.Element(f"{{{opf}}}meta")
                node.attrib["id"] = series_id
                node.attrib["property"] = "belongs-to-collection"
                node.text = series_name
                metadata.append(node)

                collection_type = ET.Element(f"{{{opf}}}meta")
                collection_type.attrib["refines"] = f"#{series_id}"
                collection_type.attrib["property"] = "collection-type"
                collection_type.text = "series"
                metadata.append(collection_type)

                if series_index is not None:
                    position = ET.Element(f"{{{opf}}}meta")
                    position.attrib["refines"] = f"#{series_id}"
                    position.attrib["property"] = "group-position"
                    position.text = series_index
                    metadata.append(position)
            else:
                node = ET.Element(f"{{{opf}}}meta")
                node.attrib["name"] = "calibre:series"
                node.attrib["content"] = series_name
                metadata.append(node)

                if series_index is not None:
                    position = ET.Element(f"{{{opf}}}meta")
                    position.attrib["name"] = "calibre:series_index"
                    position.attrib["content"] = series_index
                    metadata.append(position)

        elif series_index is not None:
            if style == "epub3" and existing_series is not None:
                series_id = str(existing_series.attrib.get("id") or "").strip()
                if not series_id:
                    raise EbookNormalizationError(
                        "Existing EPUB 3 series metadata has no id for group-position refinement."
                    )
                position = ET.Element(f"{{{opf}}}meta")
                position.attrib["refines"] = f"#{series_id}"
                position.attrib["property"] = "group-position"
                position.text = series_index
                metadata.append(position)
            elif style == "calibre":
                position = ET.Element(f"{{{opf}}}meta")
                position.attrib["name"] = "calibre:series_index"
                position.attrib["content"] = series_index
                metadata.append(position)
            else:
                raise EbookNormalizationError(
                    "Cannot add a series index without an embedded series declaration."
                )

    ET.register_namespace("", opf)
    ET.register_namespace("dc", dc)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _copy_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    copied = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    copied.compress_type = info.compress_type
    copied.comment = info.comment
    copied.extra = info.extra
    copied.create_system = info.create_system
    copied.create_version = info.create_version
    copied.extract_version = info.extract_version
    copied.flag_bits = info.flag_bits
    copied.volume = info.volume
    copied.internal_attr = info.internal_attr
    copied.external_attr = info.external_attr
    return copied


def _build_normalized_epub(
    source: Path,
    target: Path,
    package_path: str,
    additions: dict[str, str],
) -> None:
    try:
        with zipfile.ZipFile(source, "r") as src:
            infos = src.infolist()
            names = {info.filename for info in infos}
            if "mimetype" not in names:
                raise EbookNormalizationError(
                    "EPUB is missing the required mimetype entry."
                )
            if package_path not in names:
                raise EbookNormalizationError(
                    f"EPUB package document is missing: {package_path}"
                )

            package_bytes = _rewrite_package(
                src.read(package_path),
                additions,
            )

            with zipfile.ZipFile(target, "w") as dst:
                mimetype_info = zipfile.ZipInfo("mimetype")
                mimetype_info.compress_type = zipfile.ZIP_STORED
                dst.writestr(mimetype_info, src.read("mimetype"))

                for info in infos:
                    if info.filename == "mimetype":
                        continue
                    data = (
                        package_bytes
                        if info.filename == package_path
                        else src.read(info.filename)
                    )
                    dst.writestr(_copy_info(info), data)
    except EbookNormalizationError:
        raise
    except (OSError, KeyError, zipfile.BadZipFile, RuntimeError) as exc:
        raise EbookNormalizationError(
            f"Could not build normalized EPUB transaction file: {exc}"
        ) from exc


def _verify_normalized_epub(
    path: Path,
    *,
    expected_title: str | None,
    expected_creator: str | None,
    expected_series: str | None,
    expected_series_index: str | None,
) -> None:
    if not zipfile.is_zipfile(path):
        raise EbookNormalizationError(
            "Normalized output is not a valid ZIP container."
        )

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if not infos or infos[0].filename != "mimetype":
                raise EbookNormalizationError(
                    "Normalized EPUB does not keep mimetype first."
                )
            if infos[0].compress_type != zipfile.ZIP_STORED:
                raise EbookNormalizationError(
                    "Normalized EPUB mimetype entry is compressed."
                )
            if archive.read("mimetype") != b"application/epub+zip":
                raise EbookNormalizationError(
                    "Normalized EPUB mimetype value is invalid."
                )
            if "META-INF/container.xml" not in archive.namelist():
                raise EbookNormalizationError(
                    "Normalized EPUB lost META-INF/container.xml."
                )
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise EbookNormalizationError(
            f"Could not validate normalized EPUB: {exc}"
        ) from exc

    inspection = inspect_ebook_metadata(path)
    if expected_title is not None and inspection.title != expected_title:
        raise EbookNormalizationError(
            "Normalized EPUB title did not verify after rewrite."
        )
    actual_creator = (
        "; ".join(inspection.creators) if inspection.creators else None
    )
    if expected_creator is not None and actual_creator != expected_creator:
        raise EbookNormalizationError(
            "Normalized EPUB creator did not verify after rewrite."
        )

    if expected_series is not None and inspection.series_name != expected_series:
        raise EbookNormalizationError(
            "Normalized EPUB series did not verify after rewrite."
        )

    if expected_series_index is not None:
        actual_index = inspection.series_index
        try:
            index_matches = (
                actual_index is not None
                and Decimal(actual_index).is_finite()
                and Decimal(expected_series_index).is_finite()
                and Decimal(actual_index) == Decimal(expected_series_index)
            )
        except (InvalidOperation, ValueError):
            index_matches = False

        if not index_matches:
            raise EbookNormalizationError(
                "Normalized EPUB series index did not verify after rewrite."
            )

def apply_ebook_normalization(path: Path) -> EbookNormalizationResult:
    preview = preview_ebook_normalization(path)
    if preview.source_kind != "staging-job":
        raise EbookNormalizationError(
            "Metadata mutation is allowed only for isolated eBook staging jobs."
        )
    if preview.blocked:
        raise EbookNormalizationError(
            "Metadata normalization is blocked by embedded/provenance conflicts."
        )

    additions = {
        action.field: action.proposed_value
        for action in preview.actions
        if action.status == "add"
        and action.proposed_value
        and action.field in {"title", "creator", "series", "series-index"}
    }
    if not additions:
        raise EbookNormalizationError(
            "No approved metadata additions are available to apply."
        )

    source = preview.source_path.resolve()
    job_dir = source.parent
    fetch_path = job_dir / "ebook-fetch-report.json"
    fetch = _read_json(fetch_path)

    if fetch.get("status") != "staged-verified":
        raise EbookNormalizationError(
            "Only staged-verified eBooks may be metadata-normalized."
        )
    if bool(fetch.get("finalLibraryModified")):
        raise EbookNormalizationError(
            "Final library already modified; staging normalization is blocked."
        )

    recorded_sha = str((fetch.get("verification") or {}).get("sha256") or "")
    original_sha = _sha256(source)
    if not recorded_sha or original_sha != recorded_sha:
        raise EbookNormalizationError(
            "Staged eBook hash no longer matches fetch provenance."
        )

    package_path = _package_path(source)
    transaction_id = f"ebook-normalize-{uuid.uuid4().hex[:8]}"
    temp = source.with_name(f".{source.name}.{transaction_id}.tmp.epub")
    backup = source.with_name(f".{source.name}.{transaction_id}.backup.epub")
    report_path = job_dir / "ebook-normalization-report.json"

    previous_fetch = fetch_path.read_bytes()
    previous_report = report_path.read_bytes() if report_path.exists() else None
    replaced = False
    report_written = False

    try:
        _build_normalized_epub(source, temp, package_path, additions)
        _verify_normalized_epub(
            temp,
            expected_title=additions.get("title"),
            expected_creator=additions.get("creator"),
            expected_series=additions.get("series"),
            expected_series_index=additions.get("series-index"),
        )

        normalized_sha = _sha256(temp)
        normalized_size = temp.stat().st_size
        original_size = source.stat().st_size

        shutil.copy2(source, backup)
        if _sha256(backup) != original_sha:
            raise EbookNormalizationError(
                "Rollback backup failed SHA-256 verification."
            )
        if _sha256(source) != original_sha:
            raise EbookNormalizationError(
                "Staged eBook changed during normalization."
            )

        os.replace(temp, source)
        replaced = True

        if _sha256(source) != normalized_sha:
            raise EbookNormalizationError(
                "Post-commit SHA-256 verification failed."
            )
        _verify_normalized_epub(
            source,
            expected_title=additions.get("title"),
            expected_creator=additions.get("creator"),
            expected_series=additions.get("series"),
            expected_series_index=additions.get("series-index"),
        )

        applied_at = datetime.now(timezone.utc).isoformat()
        report = {
            "schemaVersion": 1,
            "jobId": fetch.get("jobId"),
            "status": "normalized-and-verified",
            "mediaType": "ebook",
            "transactionId": transaction_id,
            "appliedAt": applied_at,
            "file": str(source),
            "packagePath": package_path,
            "additions": additions,
            "original": {"size": original_size, "sha256": original_sha},
            "normalized": {
                "size": normalized_size,
                "sha256": normalized_sha,
                "formatVerified": True,
                "metadataVerified": True,
            },
            "safety": {
                "conflictsBlocked": True,
                "publicationDateModified": False,
                "seriesModified": any(
                    field in additions
                    for field in ("series", "series-index")
                ),
                "finalLibraryModified": False,
            },
        }
        _write_json_atomic(report_path, report)
        report_written = True

        verification = fetch.get("verification") or {}
        verification["actualSize"] = normalized_size
        verification["sha256"] = normalized_sha
        verification["formatVerified"] = True
        fetch["verification"] = verification
        fetch["schemaVersion"] = max(int(fetch.get("schemaVersion") or 0), 2)
        fetch["normalization"] = {
            "status": "normalized-and-verified",
            "transactionId": transaction_id,
            "report": str(report_path),
            "additions": additions,
            "originalSha256": original_sha,
            "normalizedSha256": normalized_sha,
        }
        fetch.setdefault("normalizationHistory", []).append({
            "transactionId": transaction_id,
            "appliedAt": applied_at,
            "report": str(report_path),
            "additions": additions,
            "originalSha256": original_sha,
            "normalizedSha256": normalized_sha,
        })
        _write_json_atomic(fetch_path, fetch)
        backup.unlink(missing_ok=True)

        return EbookNormalizationResult(
            job_dir=job_dir,
            source_path=source,
            package_path=package_path,
            transaction_id=transaction_id,
            additions=tuple(additions.keys()),
            original_sha256=original_sha,
            normalized_sha256=normalized_sha,
            original_size=original_size,
            normalized_size=normalized_size,
            normalization_report_path=report_path,
            fetch_report_path=fetch_path,
        )
    except Exception:
        temp.unlink(missing_ok=True)
        if replaced and backup.exists():
            os.replace(backup, source)
        else:
            backup.unlink(missing_ok=True)

        if report_written:
            if previous_report is None:
                report_path.unlink(missing_ok=True)
            else:
                report_path.write_bytes(previous_report)

        try:
            if fetch_path.read_bytes() != previous_fetch:
                restore = fetch_path.with_name(
                    f".{fetch_path.name}.{uuid.uuid4().hex[:8]}.restore.tmp"
                )
                restore.write_bytes(previous_fetch)
                os.replace(restore, fetch_path)
        except OSError:
            pass
        raise
    finally:
        temp.unlink(missing_ok=True)
        backup.unlink(missing_ok=True)
