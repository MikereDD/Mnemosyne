from __future__ import annotations

import os
import re
from pathlib import Path

from .models import MediaType

_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def expand_config_path(value: str) -> Path:
    expanded = os.path.expandvars(value.replace("$HOME", str(Path.home())))
    return Path(expanded).expanduser()


def sanitize_component(value: str) -> str:
    value = _INVALID_WINDOWS_CHARS.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.rstrip(". ")
    return value or "Unknown"


def canonical_destination(
    library_root: Path,
    media_type: MediaType,
    creator: str,
    title: str,
    year: int | None,
    *,
    series: str | None = None,
    series_index: float | None = None,
) -> Path:
    creator_part = sanitize_component(creator)
    title_part = sanitize_component(title)
    date_part = str(year) if year else "Unknown"

    if media_type is MediaType.AUDIOBOOK:
        return (
            library_root
            / "Audiobooks"
            / creator_part
            / "Audiobook"
            / f"{title_part} - {creator_part} ({date_part})"
        )

    if media_type is MediaType.EBOOK:
        if series_index is not None and series is None:
            raise ValueError(
                "eBook series index requires a verified series name."
            )

        ebook_root = library_root / "eBooks" / creator_part
        if series is None:
            return ebook_root / title_part

        series_part = sanitize_component(series)
        if series_index is None:
            book_part = title_part
        else:
            if series_index < 0:
                raise ValueError("eBook series index must not be negative.")
            index_text = f"{series_index:g}"
            if "." in index_text:
                whole, fraction = index_text.split(".", 1)
                index_part = f"{whole.zfill(2)}.{fraction}"
            else:
                index_part = index_text.zfill(2)
            book_part = f"{index_part} - {title_part}"

        return ebook_root / series_part / book_part

    return (
        library_root
        / "Music"
        / creator_part
        / f"{creator_part} - {title_part} ({date_part})"
    )
