from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class MediaType(StrEnum):
    AUDIOBOOK = "audiobook"
    EBOOK = "ebook"
    MUSIC = "music"


class CandidateKind(StrEnum):
    AUDIO = "audio"
    COVER = "cover"
    AUXILIARY = "auxiliary"
    UNKNOWN = "unknown"


class MediaCandidate(BaseModel):
    name: str
    url: str
    extension: str
    archive_format: str | None = None
    source: str | None = None
    size: int | None = None
    bitrate_kbps: float | None = None
    kind: CandidateKind
    playable: bool = False
    lossless: bool = False
    score: int = 0
    reasons: list[str] = Field(default_factory=list)


class ArchiveItem(BaseModel):
    identifier: str
    source_url: str
    media_type: MediaType
    raw_title: str
    title: str
    creator: str | None = None
    year: int | None = None
    external_link: str | None = None
    candidates: list[MediaCandidate] = Field(default_factory=list)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class AcquisitionPlan(BaseModel):
    item: ArchiveItem
    destination: Path
    selected_audio: list[MediaCandidate] = Field(default_factory=list)
    selected_cover: MediaCandidate | None = None
    warnings: list[str] = Field(default_factory=list)
