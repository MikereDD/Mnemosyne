from __future__ import annotations

import json
import re
import zipfile
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


class EbookMetadataError(RuntimeError):
    """Read-only eBook metadata inspection could not be completed safely."""


@dataclass(frozen=True)
class MetadataComparison:
    field: str
    embedded: str | None
    provenance: str | None
    status: str
    note: str


@dataclass(frozen=True)
class EbookMetadataInspection:
    source_path: Path
    source_kind: str
    package_path: str
    epub_version: str | None
    title: str | None
    creators: tuple[str, ...]
    language: str | None
    identifiers: tuple[str, ...]
    publisher: str | None
    dates: tuple[str, ...]
    subjects: tuple[str, ...]
    series_name: str | None
    series_index: str | None
    cover_reference: str | None
    provenance_title: str | None
    provenance_creator: str | None
    provenance_year: int | None
    provenance_series: str | None
    provenance_series_index: str | None
    comparisons: tuple[MetadataComparison, ...]


_MAX_XML_BYTES = 4 * 1024 * 1024
_WS = re.compile(r"\s+")


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise EbookMetadataError(f"Required EPUB member is missing: {name}") from exc

    if info.file_size > _MAX_XML_BYTES:
        raise EbookMetadataError(
            f"EPUB XML member is unexpectedly large ({info.file_size} bytes): {name}"
        )

    try:
        data = archive.read(info)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise EbookMetadataError(f"Could not read EPUB member {name}: {exc}") from exc

    if len(data) > _MAX_XML_BYTES:
        raise EbookMetadataError(f"EPUB XML member exceeds safety limit: {name}")
    return data


def _parse_xml(data: bytes, label: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise EbookMetadataError(f"Invalid XML in {label}: {exc}") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = _WS.sub(" ", element.text).strip()
    return value or None


def _texts(parent: ET.Element, name: str) -> tuple[str, ...]:
    values: list[str] = []
    for element in parent.iter():
        if _local_name(element.tag) == name:
            value = _text(element)
            if value:
                values.append(value)
    return tuple(values)


def _first_text(parent: ET.Element, name: str) -> str | None:
    values = _texts(parent, name)
    return values[0] if values else None


def _attr_by_local_name(element: ET.Element, name: str) -> str | None:
    for key, value in element.attrib.items():
        if _local_name(key) == name:
            return value
    return None


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    return _WS.sub(" ", value).strip().casefold()


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
    normalized = format(number, "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized or "0"


def _compare_series_index(
    embedded: str | None,
    provenance: str | None,
) -> MetadataComparison:
    embedded_value = _decimal_text(embedded)
    provenance_value = _decimal_text(provenance)

    if not embedded_value:
        return MetadataComparison(
            field="series-index",
            embedded=None,
            provenance=provenance_value,
            status="missing-embedded",
            note=(
                "No embedded series index was found; verified provenance "
                "remains authoritative evidence."
            ),
        )
    if not provenance_value:
        return MetadataComparison(
            field="series-index",
            embedded=embedded_value,
            provenance=None,
            status="unverified",
            note=(
                "Embedded series index exists but there is no verified "
                "provenance value to compare."
            ),
        )

    try:
        matches = Decimal(embedded_value) == Decimal(provenance_value)
    except InvalidOperation:
        matches = _normalize(embedded_value) == _normalize(provenance_value)

    if matches:
        return MetadataComparison(
            field="series-index",
            embedded=embedded_value,
            provenance=provenance_value,
            status="match",
            note="Embedded series index agrees with verified provenance.",
        )

    return MetadataComparison(
        field="series-index",
        embedded=embedded_value,
        provenance=provenance_value,
        status="conflict",
        note=(
            "Embedded series index differs from verified provenance; "
            "do not silently normalize."
        ),
    )


def _compare_text(
    field: str,
    embedded: str | None,
    provenance: str | None,
) -> MetadataComparison:
    if not embedded:
        return MetadataComparison(
            field=field,
            embedded=None,
            provenance=provenance,
            status="missing-embedded",
            note="No embedded value was found; provenance remains authoritative evidence.",
        )
    if not provenance:
        return MetadataComparison(
            field=field,
            embedded=embedded,
            provenance=None,
            status="unverified",
            note="Embedded value exists but there is no provenance value to compare.",
        )
    if _normalize(embedded) == _normalize(provenance):
        return MetadataComparison(
            field=field,
            embedded=embedded,
            provenance=provenance,
            status="match",
            note="Embedded metadata agrees with verified provenance.",
        )
    return MetadataComparison(
        field=field,
        embedded=embedded,
        provenance=provenance,
        status="conflict",
        note="Embedded metadata differs from verified provenance; do not silently normalize.",
    )


def _series_from_metadata(metadata: ET.Element) -> tuple[str | None, str | None]:
    calibre_name: str | None = None
    calibre_index: str | None = None

    meta_by_id: dict[str, ET.Element] = {}
    refinements: list[ET.Element] = []

    for element in metadata.iter():
        if _local_name(element.tag) != "meta":
            continue

        element_id = element.attrib.get("id")
        if element_id:
            meta_by_id[element_id] = element

        name = (element.attrib.get("name") or "").strip().casefold()
        content = (element.attrib.get("content") or "").strip()
        if name == "calibre:series" and content:
            calibre_name = content
        elif name == "calibre:series_index" and content:
            calibre_index = content

        if element.attrib.get("refines"):
            refinements.append(element)

    if calibre_name:
        return calibre_name, calibre_index

    for element_id, element in meta_by_id.items():
        property_name = (element.attrib.get("property") or "").strip()
        if property_name != "belongs-to-collection":
            continue

        collection_name = _text(element)
        if not collection_name:
            continue

        collection_type: str | None = None
        group_position: str | None = None
        ref = f"#{element_id}"

        for refinement in refinements:
            if refinement.attrib.get("refines") != ref:
                continue
            prop = (refinement.attrib.get("property") or "").strip()
            value = _text(refinement)
            if prop == "collection-type":
                collection_type = value
            elif prop == "group-position":
                group_position = value

        if collection_type and collection_type.casefold() == "series":
            return collection_name, group_position

    return None, None


def _cover_reference(metadata: ET.Element, manifest: ET.Element | None) -> str | None:
    cover_id: str | None = None

    for element in metadata.iter():
        if _local_name(element.tag) != "meta":
            continue
        if (element.attrib.get("name") or "").casefold() == "cover":
            cover_id = (element.attrib.get("content") or "").strip() or None
            if cover_id:
                break

    if manifest is None:
        return cover_id

    for item in manifest.iter():
        if _local_name(item.tag) != "item":
            continue

        item_id = item.attrib.get("id")
        properties = (item.attrib.get("properties") or "").split()
        if cover_id and item_id == cover_id:
            return item.attrib.get("href") or cover_id
        if "cover-image" in properties:
            return item.attrib.get("href") or item_id

    return cover_id


def _load_provenance(
    job_dir: Path,
) -> tuple[
    str | None,
    str | None,
    int | None,
    str | None,
    str | None,
]:
    report_path = job_dir / "ebook-fetch-report.json"
    if not report_path.is_file():
        return None, None, None, None, None

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EbookMetadataError(
            f"Could not read eBook provenance report {report_path}: {exc}"
        ) from exc

    work = report.get("work") or {}
    title = str(work.get("title") or "").strip() or None
    creator = str(work.get("creator") or "").strip() or None

    year_value = work.get("year")
    try:
        year = int(year_value) if year_value is not None else None
    except (TypeError, ValueError):
        year = None

    series = None
    series_index = None

    if str(work.get("seriesProvenance") or "").strip() == "verified-override":
        series = str(work.get("series") or "").strip() or None

    if str(work.get("seriesIndexProvenance") or "").strip() == "verified-override":
        series_index = _decimal_text(work.get("seriesIndex"))
        if series_index is not None:
            if series is None:
                raise EbookMetadataError("Verified series index requires a verified series name.")
            try:
                number = Decimal(series_index)
            except InvalidOperation as exc:
                raise EbookMetadataError("Verified series index must be a finite non-negative number.") from exc
            if not number.is_finite() or number < 0:
                raise EbookMetadataError("Verified series index must be a finite non-negative number.")

    return title, creator, year, series, series_index


def _resolve_source(path: Path) -> tuple[Path, str, Path | None]:
    path = path.resolve()
    if path.is_file():
        if path.suffix.casefold() != ".epub":
            raise EbookMetadataError(
                "Initial metadata inspection currently supports EPUB files only."
            )
        return path, "file", None

    if not path.is_dir():
        raise EbookMetadataError(f"eBook source does not exist: {path}")

    report_path = path / "ebook-fetch-report.json"
    if not report_path.is_file():
        raise EbookMetadataError(
            f"Staging directory is missing ebook-fetch-report.json: {path}"
        )

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EbookMetadataError(
            f"Could not read eBook staging report {report_path}: {exc}"
        ) from exc

    staged_name = str(report.get("stagedFile") or "").strip()
    if not staged_name:
        raise EbookMetadataError("Staged eBook filename is missing from provenance.")

    source = path / staged_name
    if not source.is_file():
        raise EbookMetadataError(f"Staged eBook file is missing: {source}")
    if source.suffix.casefold() != ".epub":
        raise EbookMetadataError(
            "Initial metadata inspection currently supports EPUB files only."
        )

    return source, "staging-job", path


def inspect_ebook_metadata(path: Path) -> EbookMetadataInspection:
    source, source_kind, job_dir = _resolve_source(path)

    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise EbookMetadataError(f"Could not open EPUB container {source}: {exc}") from exc

    with archive:
        container_root = _parse_xml(
            _read_member(archive, "META-INF/container.xml"),
            "META-INF/container.xml",
        )

        rootfile_path: str | None = None
        for element in container_root.iter():
            if _local_name(element.tag) == "rootfile":
                rootfile_path = (element.attrib.get("full-path") or "").strip() or None
                if rootfile_path:
                    break

        if not rootfile_path:
            raise EbookMetadataError(
                "EPUB container.xml does not declare a package document."
            )

        package = _parse_xml(
            _read_member(archive, rootfile_path),
            rootfile_path,
        )

    metadata: ET.Element | None = None
    manifest: ET.Element | None = None
    for child in package:
        name = _local_name(child.tag)
        if name == "metadata":
            metadata = child
        elif name == "manifest":
            manifest = child

    if metadata is None:
        raise EbookMetadataError("EPUB package document has no metadata section.")

    title = _first_text(metadata, "title")
    creators = _texts(metadata, "creator")
    language = _first_text(metadata, "language")
    identifiers = _texts(metadata, "identifier")
    publisher = _first_text(metadata, "publisher")
    dates = _texts(metadata, "date")
    subjects = _texts(metadata, "subject")
    series_name, series_index = _series_from_metadata(metadata)
    cover_reference = _cover_reference(metadata, manifest)

    provenance_title: str | None = None
    provenance_creator: str | None = None
    provenance_year: int | None = None
    provenance_series: str | None = None
    provenance_series_index: str | None = None

    if job_dir is not None:
        (
            provenance_title,
            provenance_creator,
            provenance_year,
            provenance_series,
            provenance_series_index,
        ) = _load_provenance(job_dir)

    creator_text = "; ".join(creators) if creators else None
    comparisons: list[MetadataComparison] = [
        _compare_text("title", title, provenance_title),
        _compare_text("creator", creator_text, provenance_creator),
        _compare_text("series", series_name, provenance_series),
        _compare_series_index(series_index, provenance_series_index),
    ]

    if dates:
        comparisons.append(
            MetadataComparison(
                field="date",
                embedded="; ".join(dates),
                provenance=str(provenance_year) if provenance_year is not None else None,
                status="evidence-only",
                note=(
                    "EPUB dates may describe this digital edition rather than the original work; "
                    "they are not treated as a hard year conflict."
                ),
            )
        )

    return EbookMetadataInspection(
        source_path=source,
        source_kind=source_kind,
        package_path=rootfile_path,
        epub_version=package.attrib.get("version"),
        title=title,
        creators=creators,
        language=language,
        identifiers=identifiers,
        publisher=publisher,
        dates=dates,
        subjects=subjects,
        series_name=series_name,
        series_index=series_index,
        cover_reference=cover_reference,
        provenance_title=provenance_title,
        provenance_creator=provenance_creator,
        provenance_year=provenance_year,
        provenance_series=provenance_series,
        provenance_series_index=provenance_series_index,
        comparisons=tuple(comparisons),
    )
