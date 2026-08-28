from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import ArchiveItem, MediaType


class ProviderError(RuntimeError):
    """Provider request or parsing failure."""


class Provider(ABC):
    name: str

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def identify(
        self,
        url: str,
        media_type: MediaType,
        *,
        title_override: str | None = None,
        creator_override: str | None = None,
        year_override: int | None = None,
    ) -> ArchiveItem:
        raise NotImplementedError
