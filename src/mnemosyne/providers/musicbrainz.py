"""Read a selected release using https://musicbrainz.org/doc/MusicBrainz_API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from threading import Lock
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx


@dataclass(frozen=True)
class AlbumTrack:
    disc: int
    number: int
    title: str
    artist: str
    length_ms: int | None
    recording_id: str | None = None


@dataclass(frozen=True)
class AlbumMetadata:
    source_url: str
    release_id: str
    title: str
    artist: str
    date: str | None
    tracks: tuple[AlbumTrack, ...]


class MusicBrainzError(ValueError):
    """The selected release could not provide a complete album track list."""


_RELEASE_PATH = re.compile(
    r"/release/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?"
)
_DATE = re.compile(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?")
_USER_AGENT = "Mnemosyne/0.1.0.dev1 (https://github.com/MikereDD/Mnemosyne)"
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_REQUEST_LOCK = Lock()
_last_request_started: float | None = None


def _release_id(source_url: str) -> str:
    if not isinstance(source_url, str):
        raise MusicBrainzError("Provide a MusicBrainz release page URL.")
    try:
        parsed = urlsplit(source_url.strip())
        valid_host = (
            parsed.scheme == "https"
            and parsed.hostname == "musicbrainz.org"
            and parsed.netloc.lower() == "musicbrainz.org"
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError as exc:
        raise MusicBrainzError("Provide a valid MusicBrainz release page URL.") from exc
    if valid_host and parsed.path.startswith("/release-group/"):
        raise MusicBrainzError(
            "Choose a release edition on the MusicBrainz album page, then copy its "
            "https://musicbrainz.org/release/<ID> URL. A release group has several editions."
        )
    match = _RELEASE_PATH.fullmatch(parsed.path) if valid_host else None
    if match is None:
        raise MusicBrainzError(
            "Use an HTTPS MusicBrainz release page URL: "
            "https://musicbrainz.org/release/<ID>. Choose the edition that matches your files."
        )
    return str(UUID(match.group(1)))


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _artist_credit(value: object, label: str) -> str | None:
    if value is None or value == []:
        return None
    if not isinstance(value, list):
        raise MusicBrainzError(f"MusicBrainz {label} artist credit is malformed.")
    parts: list[str] = []
    for credit in value:
        if not isinstance(credit, dict):
            raise MusicBrainzError(f"MusicBrainz {label} artist credit is malformed.")
        artist = credit.get("artist")
        name = _text(credit.get("name"))
        if name is None and isinstance(artist, dict):
            name = _text(artist.get("name"))
        joinphrase = credit.get("joinphrase", "")
        if name is None or not isinstance(joinphrase, str):
            raise MusicBrainzError(f"MusicBrainz {label} artist credit is incomplete.")
        parts.append(name + joinphrase)
    return "".join(parts).strip() or None


def _positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MusicBrainzError(f"MusicBrainz {label} must be a positive integer.")
    return value


def _release_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        raise MusicBrainzError("MusicBrainz release date must be YYYY, YYYY-MM, or YYYY-MM-DD.")
    parts = [int(part) for part in value.split("-")]
    try:
        date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError as exc:
        raise MusicBrainzError(f"MusicBrainz release date is invalid: {value}.") from exc
    return value


def parse_album(payload: dict, source_url: str) -> AlbumMetadata:
    """Validate a complete release response, also usable for saved offline responses."""
    release_id = _release_id(source_url)
    if not isinstance(payload, dict):
        raise MusicBrainzError("MusicBrainz returned an invalid release response.")
    if payload.get("id") != release_id:
        raise MusicBrainzError("MusicBrainz returned a different release than the selected edition.")
    title = _text(payload.get("title"))
    artist = _artist_credit(payload.get("artist-credit"), "release")
    if title is None or artist is None:
        raise MusicBrainzError("MusicBrainz release is missing its album title or artist credit.")
    release_date = _release_date(payload.get("date"))
    media = payload.get("media")
    if not isinstance(media, list) or not media:
        raise MusicBrainzError("MusicBrainz release has no complete track list.")
    if "media-count" in payload:
        media_count = _positive_int(payload["media-count"], "release disc count")
        if len(media) != media_count:
            raise MusicBrainzError("MusicBrainz release has an incomplete disc list.")

    tracks: list[AlbumTrack] = []
    discs: set[int] = set()
    for medium in media:
        if not isinstance(medium, dict):
            raise MusicBrainzError("MusicBrainz release contains a malformed disc.")
        disc = _positive_int(medium.get("position"), "disc position")
        if disc in discs:
            raise MusicBrainzError(f"MusicBrainz release repeats disc {disc}.")
        discs.add(disc)
        count = _positive_int(medium.get("track-count"), f"disc {disc} track count")
        raw_tracks = medium.get("tracks")
        if not isinstance(raw_tracks, list) or len(raw_tracks) != count:
            raise MusicBrainzError(f"MusicBrainz disc {disc} has an incomplete track list.")
        positions: set[int] = set()
        for raw_track in raw_tracks:
            if not isinstance(raw_track, dict):
                raise MusicBrainzError(f"MusicBrainz disc {disc} contains a malformed track.")
            number = _positive_int(raw_track.get("position"), f"disc {disc} track position")
            label = f"disc {disc}, track {number}"
            if number in positions:
                raise MusicBrainzError(f"MusicBrainz repeats {label}.")
            positions.add(number)
            recording = raw_track.get("recording") or {}
            if not isinstance(recording, dict):
                raise MusicBrainzError(f"MusicBrainz {label} recording is malformed.")
            track_title = _text(raw_track.get("title")) or _text(recording.get("title"))
            if track_title is None:
                raise MusicBrainzError(f"MusicBrainz {label} has no track title.")
            track_artist = (
                _artist_credit(raw_track.get("artist-credit"), label)
                or _artist_credit(recording.get("artist-credit"), label)
                or artist
            )
            length = raw_track.get("length")
            if length is None:
                length = recording.get("length")
            if length is not None and (
                isinstance(length, bool) or not isinstance(length, int) or length < 0
            ):
                raise MusicBrainzError(f"MusicBrainz {label} length is invalid.")
            recording_id = recording.get("id")
            if recording_id is not None:
                try:
                    if not isinstance(recording_id, str):
                        raise ValueError("not a string")
                    recording_id = str(UUID(recording_id))
                except ValueError as exc:
                    raise MusicBrainzError(f"MusicBrainz {label} recording ID is invalid.") from exc
            tracks.append(AlbumTrack(disc, number, track_title, track_artist, length, recording_id))
        if positions != set(range(1, count + 1)):
            raise MusicBrainzError(f"MusicBrainz disc {disc} track positions are incomplete.")
    if discs != set(range(1, len(discs) + 1)):
        raise MusicBrainzError("MusicBrainz release disc positions are incomplete.")

    return AlbumMetadata(
        source_url=f"https://musicbrainz.org/release/{release_id}",
        release_id=release_id,
        title=title,
        artist=artist,
        date=release_date,
        tracks=tuple(sorted(tracks, key=lambda track: (track.disc, track.number))),
    )


def _request_release(client: httpx.Client, release_id: str) -> dict:
    global _last_request_started
    try:
        # Serialize requests as well as their start times, including with supplied clients.
        with _REQUEST_LOCK:
            now = time.monotonic()
            if _last_request_started is not None:
                delay = 1.0 - (now - _last_request_started)
                if delay > 0:
                    time.sleep(delay)
            _last_request_started = time.monotonic()
            response = client.get(
                f"https://musicbrainz.org/ws/2/release/{release_id}",
                params={"inc": "recordings+artist-credits+release-groups", "fmt": "json"},
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                timeout=_TIMEOUT,
                follow_redirects=False,
            )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 404:
            raise MusicBrainzError("MusicBrainz could not find that release edition. Check its URL.") from exc
        if status in {429, 503}:
            raise MusicBrainzError("MusicBrainz is busy or limiting requests. Try again later.") from exc
        raise MusicBrainzError(f"MusicBrainz metadata request failed (HTTP {status}).") from exc
    except httpx.HTTPError as exc:
        raise MusicBrainzError(f"Could not retrieve MusicBrainz metadata: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise MusicBrainzError("MusicBrainz returned invalid JSON metadata.") from exc
    if not isinstance(payload, dict):
        raise MusicBrainzError("MusicBrainz returned an invalid release response.")
    return payload


def fetch_album(source_url: str, *, client: httpx.Client | None = None) -> AlbumMetadata:
    """Fetch only the selected release from the fixed MusicBrainz API host."""
    release_id = _release_id(source_url)
    if client is None:
        with httpx.Client() as owned_client:
            payload = _request_release(owned_client, release_id)
    else:
        payload = _request_release(client, release_id)
    return parse_album(payload, source_url)
