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
        return (
            library_root
            / "eBooks"
            / creator_part
            / "eBook"
            / f"{title_part} - {creator_part} ({date_part})"
        )

    return (
        library_root
        / "Music"
        / creator_part
        / f"{creator_part} - {title_part} ({date_part})"
    )
