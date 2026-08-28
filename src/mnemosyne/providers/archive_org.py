from __future__ import annotations

from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlparse

import httpx

from ..models import ArchiveItem, CandidateKind, MediaCandidate, MediaType
from .base import Provider, ProviderError

PLAYABLE_AUDIO_EXTENSIONS = {
    ".flac", ".wav", ".wave", ".aiff", ".aif", ".m4a", ".m4b",
    ".mp3", ".ogg", ".oga", ".opus", ".aac", ".wma",
}

AUXILIARY_EXTENSIONS = {
    ".afpk", ".xml", ".sqlite", ".m3u", ".torrent", ".txt", ".json",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

LOSSLESS_FORMAT_MARKERS = (
    "flac",
    "apple lossless",
    "alac",
    "wave",
    "wav",
    "aiff",
)

LOSSY_FORMAT_MARKERS = (
    "mp3",
    "mpeg audio",
    "ogg",
    "opus",
    "aac",
)

BASE_AUDIO_SCORES = {
    ".flac": 900,
    ".wav": 880,
    ".wave": 880,
    ".aiff": 870,
    ".aif": 870,
    ".m4a": 800,
    ".m4b": 790,
    ".opus": 720,
    ".ogg": 680,
    ".oga": 680,
    ".aac": 650,
    ".mp3": 620,
    ".wma": 500,
}


class ArchiveOrgProvider(Provider):
    name = "Internet Archive"

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def can_handle(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.netloc.lower() in {"archive.org", "www.archive.org"} and "/details/" in parsed.path

    @staticmethod
    def identifier_from_url(url: str) -> str:
        parsed = urlparse(url)
        marker = "/details/"
        if marker not in parsed.path:
            raise ProviderError("Expected an Archive.org /details/<identifier> URL.")
        identifier = unquote(parsed.path.split(marker, 1)[1].split("/", 1)[0]).strip()
        if not identifier:
            raise ProviderError("Archive.org identifier is missing from the URL.")
        return identifier

    def identify(
        self,
        url: str,
        media_type: MediaType,
        *,
        title_override: str | None = None,
        creator_override: str | None = None,
        year_override: int | None = None,
    ) -> ArchiveItem:
        if not self.can_handle(url):
            raise ProviderError("This URL is not a supported Archive.org details URL.")

        identifier = self.identifier_from_url(url)
        metadata_url = f"https://archive.org/metadata/{quote(identifier, safe='')}"

        try:
            response = httpx.get(
                metadata_url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mnemosyne/0.1.0-dev.1"},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Could not retrieve Archive.org metadata: {exc}") from exc

        metadata = payload.get("metadata") or {}
        raw_title = str(metadata.get("title") or identifier)
        creator = creator_override or self._creator(metadata)
        external_link = self._first_string(metadata.get("link"))
        cleaned_title = title_override or self._clean_title(raw_title, external_link)
        year = year_override or self._year(metadata)

        candidates = [
            self._candidate(identifier, entry)
            for entry in (payload.get("files") or [])
            if entry.get("name")
        ]

        return ArchiveItem(
            identifier=identifier,
            source_url=url,
            media_type=media_type,
            raw_title=raw_title,
            title=cleaned_title,
            creator=creator,
            year=year,
            external_link=external_link,
            candidates=candidates,
            raw_metadata=metadata,
        )

    @staticmethod
    def _first_string(value: object) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, list) and value:
            return str(value[0])
        return None

    @classmethod
    def _creator(cls, metadata: dict) -> str | None:
        value = metadata.get("creator")
        if isinstance(value, list):
            return str(value[0]) if value else None
        return str(value) if value else None

    @classmethod
    def _year(cls, metadata: dict) -> int | None:
        for key in ("date", "year", "publicationdate"):
            value = cls._first_string(metadata.get(key))
            if not value:
                continue
            for token in value.replace("/", "-").split("-"):
                token = token.strip()
                if len(token) == 4 and token.isdigit():
                    year = int(token)
                    if 1000 <= year <= 2999:
                        return year
        return None

    @staticmethod
    def _clean_title(raw_title: str, external_link: str | None) -> str:
        title = raw_title.strip()
        if external_link:
            hostname = urlparse(external_link).netloc.lower()
            hostname = hostname.removeprefix("www.")
            suffix = f" - {hostname}"
            if title.lower().endswith(suffix.lower()):
                title = title[: -len(suffix)].rstrip()
        return title

    @staticmethod
    def _safe_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            text = str(value).lower().replace("kbps", "").strip()
            return float(text)
        except ValueError:
            return None

    @classmethod
    def _candidate(cls, identifier: str, entry: dict) -> MediaCandidate:
        name = str(entry.get("name"))
        extension = PurePosixPath(name).suffix.lower()
        archive_format = str(entry.get("format") or "")
        source = str(entry.get("source") or "") or None
        format_lower = archive_format.lower()

        playable = extension in PLAYABLE_AUDIO_EXTENSIONS
        lossless = playable and (
            any(marker in format_lower for marker in LOSSLESS_FORMAT_MARKERS)
            or extension in {".flac", ".wav", ".wave", ".aiff", ".aif"}
        )

        if playable:
            kind = CandidateKind.AUDIO
        elif extension in IMAGE_EXTENSIONS:
            kind = CandidateKind.COVER
        elif extension in AUXILIARY_EXTENSIONS or "metadata" in format_lower:
            kind = CandidateKind.AUXILIARY
        else:
            kind = CandidateKind.UNKNOWN

        reasons: list[str] = []
        score = 0

        if playable:
            score += BASE_AUDIO_SCORES.get(extension, 400)
            if lossless:
                score += 300
                reasons.append("lossless audio")
            else:
                reasons.append("playable audio")

            if source == "original":
                score += 120
                reasons.append("Archive original")
            elif source == "derivative":
                reasons.append("Archive derivative")

            bitrate = cls._safe_float(entry.get("bitrate"))
            if bitrate:
                score += min(int(bitrate // 8), 80)
                reasons.append(f"{bitrate:g} kbps")
        else:
            bitrate = None

        if kind is CandidateKind.COVER:
            lower_name = name.lower()
            if any(marker in lower_name for marker in ("cover", "front", "folder")):
                score += 200
                reasons.append("cover-like filename")
            if source == "original":
                score += 80
                reasons.append("Archive original")
            if "__ia_thumb" in lower_name or "item tile" in format_lower:
                score -= 50
                reasons.append("thumbnail/derived artwork")

        try:
            size = int(entry["size"]) if entry.get("size") is not None else None
        except (TypeError, ValueError):
            size = None

        encoded_name = quote(name, safe="/")
        download_url = f"https://archive.org/download/{quote(identifier, safe='')}/{encoded_name}"

        return MediaCandidate(
            name=name,
            url=download_url,
            extension=extension,
            archive_format=archive_format or None,
            source=source,
            size=size,
            bitrate_kbps=bitrate,
            kind=kind,
            playable=playable,
            lossless=lossless,
            score=score,
            reasons=reasons,
        )
