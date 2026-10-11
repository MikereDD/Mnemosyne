"""Preview and transactionally normalize one explicitly selected music album.

MusicBrainz release metadata is evidence selected by the user. Local tracks must
corroborate their release positions; directory order is never identity evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import date as CalendarDate
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit
from urllib.request import url2pathname

from mutagen import File as MutagenFile

from .metadata_io import MetadataIOError, read_metadata, verify_metadata, write_metadata
from .paths import sanitize_component
from .providers.musicbrainz import AlbumMetadata, AlbumTrack


class AlbumLibraryError(RuntimeError):
    """An album cannot be identified or modified safely."""


class AlbumFilenameFormat(str, Enum):
    TRACK_TITLE = "track-title"
    FULL = "full"


class _AlbumRenameCompensationError(AlbumLibraryError):
    """A no-overwrite rename left a known path requiring manual recovery."""


@dataclass(frozen=True)
class AlbumTrackPlan:
    source: Path
    target: Path
    tags: dict[str, str]
    sha256: str
    payload_sha256: str
    matching_evidence: tuple[str, ...]
    original_tags: tuple[tuple[str, tuple[str, ...]], ...]
    artwork_hashes: tuple[tuple[str, str], ...]
    duration_ms: int | None

    @property
    def source_name(self) -> str:
        return self.source.name

    @property
    def target_name(self) -> str:
        return self.target.name


@dataclass(frozen=True)
class AlbumPlaylistPlan:
    path: Path
    sha256: str
    original_bytes: bytes
    updated_bytes: bytes

    @property
    def changed(self) -> bool:
        return self.original_bytes != self.updated_bytes


@dataclass(frozen=True)
class AlbumPlan:
    album_dir: Path
    metadata: AlbumMetadata
    tracks: tuple[AlbumTrackPlan, ...]
    playlists: tuple[AlbumPlaylistPlan, ...]
    plan_id: str
    filename_format: AlbumFilenameFormat = AlbumFilenameFormat.TRACK_TITLE

    @property
    def rename_count(self) -> int:
        return sum(track.source.name != track.target.name for track in self.tracks)


@dataclass(frozen=True)
class AlbumResult:
    album_dir: Path
    transaction_dir: Path
    report_path: Path
    tracks: tuple[Path, ...]
    playlists_updated: int


_SUPPORTED_AUDIO = {".mp3", ".flac", ".m4a"}
_OTHER_AUDIO = {".wav", ".wave", ".aiff", ".aif", ".m4b", ".mp4", ".ogg", ".oga", ".opus", ".aac", ".wma", ".ape", ".alac"}
_PLAYLISTS = {".m3u", ".m3u8"}
_OTHER_REFERENCES = {".cue", ".pls", ".xspf", ".wpl", ".asx", ".b4s"}
_UUID_SUFFIX = re.compile(r"_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_TRACK_PREFIX = re.compile(r"^\s*([0-9]+)(?:\s*[-._]\s*|\s+)(.+)$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _linked(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _album_root(path: Path) -> Path:
    raw = Path(path).expanduser()
    if _linked(raw):
        raise AlbumLibraryError("Select a real album directory, not a symlink or junction.")
    try:
        root = raw.resolve(strict=True)
    except OSError as exc:
        raise AlbumLibraryError(f"Album directory cannot be resolved: {raw}: {exc}") from exc
    if not root.is_dir():
        raise AlbumLibraryError(f"Album directory does not exist: {root}")
    return root


def _album_files(root: Path) -> tuple[Path, ...]:
    audio: list[Path] = []
    def walk_error(exc: OSError) -> None:
        raise AlbumLibraryError(f"Album contents could not be inspected completely: {exc}") from exc
    for current, directories, filenames in os.walk(root, followlinks=False, onerror=walk_error):
        here = Path(current)
        for directory in tuple(directories):
            child = here / directory
            if _linked(child):
                raise AlbumLibraryError(f"Album contains a linked directory; traversal is blocked: {child.name}")
        for name in filenames:
            path = here / name
            if _linked(path):
                raise AlbumLibraryError(f"Album contains a symlinked file: {path.name}")
            if not path.resolve().is_relative_to(root):
                raise AlbumLibraryError(f"Album file escapes the selected directory: {path}")
            if path.parent != root and path.suffix.lower() in _PLAYLISTS | _OTHER_REFERENCES:
                raise AlbumLibraryError("Nested playlists or cue/reference files require a folder-aware reference workflow; album changes are blocked.")
            if path.suffix.lower() in _SUPPORTED_AUDIO | _OTHER_AUDIO:
                if path.parent != root:
                    raise AlbumLibraryError("Nested audio/disc folders require a folder-aware workflow; this command supports one flat album.")
                if path.suffix.lower() not in _SUPPORTED_AUDIO:
                    raise AlbumLibraryError(f"Album metadata writing is not supported for {path.suffix}; supported formats are MP3, FLAC, and M4A.")
                audio.append(path)
    return tuple(sorted(audio, key=lambda path: path.name.casefold()))


def _text_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in normalized if not unicodedata.combining(c)).split())


def _number(value: str | None) -> int | None:
    match = re.match(r"^\s*(\d+)(?:\s*/\s*\d+)?\s*$", value or "")
    if not match:
        return None
    number = int(match.group(1))
    return number if number > 0 else None


def _first(tags: dict[str, list[str]], key: str) -> str | None:
    values = tags.get(key) or []
    return str(values[0]) if values else None


def _duration_ms(path: Path) -> int | None:
    try:
        audio = MutagenFile(path)
        length = getattr(getattr(audio, "info", None), "length", None)
        return round(float(length) * 1000) if length is not None else None
    except Exception as exc:
        raise AlbumLibraryError(f"Audio duration could not be inspected for {path.name}: {exc}") from exc


def _range_digest(handle, start: int, end: int, digest) -> None:
    handle.seek(start)
    remaining = end - start
    if remaining < 0:
        raise AlbumLibraryError("Invalid audio payload boundaries.")
    while remaining:
        block = handle.read(min(1024 * 1024, remaining))
        if not block:
            raise AlbumLibraryError("Audio payload ended unexpectedly.")
        digest.update(block)
        remaining -= len(block)


def _id3_end(handle, size: int) -> int:
    handle.seek(0)
    header = handle.read(10)
    if header[:3] != b"ID3":
        return 0
    if len(header) != 10 or any(byte & 0x80 for byte in header[6:10]):
        raise AlbumLibraryError("Malformed ID3 header; audio payload cannot be verified.")
    length = 0
    for byte in header[6:10]:
        length = (length << 7) | byte
    end = 10 + length + (10 if header[3] == 4 and header[5] & 0x10 else 0)
    if end > size:
        raise AlbumLibraryError("ID3 header extends beyond the audio file.")
    return end


def audio_payload_sha256(path: Path) -> str:
    """Hash compressed audio bytes, excluding mutable container metadata."""
    digest = hashlib.sha256()
    size = path.stat().st_size
    with path.open("rb") as handle:
        suffix = path.suffix.lower()
        if suffix == ".mp3":
            start, end = _id3_end(handle, size), size
            if end - start >= 128:
                handle.seek(end - 128)
                if handle.read(3) == b"TAG":
                    end -= 128
            if end - start >= 32:
                handle.seek(end - 32)
                footer = handle.read(32)
                if footer[:8] == b"APETAGEX":
                    ape_size = int.from_bytes(footer[12:16], "little")
                    if ape_size < 32 or end - ape_size < start:
                        raise AlbumLibraryError("Malformed APE audio tag.")
                    end -= ape_size
                    if end - start >= 32:
                        handle.seek(end - 32)
                        if handle.read(8) == b"APETAGEX":
                            end -= 32
            if end <= start:
                raise AlbumLibraryError(f"No MP3 audio payload found: {path.name}")
            _range_digest(handle, start, end, digest)
        elif suffix == ".flac":
            start = _id3_end(handle, size)
            handle.seek(start)
            if handle.read(4) != b"fLaC":
                raise AlbumLibraryError(f"FLAC marker is missing: {path.name}")
            offset = start + 4
            while True:
                handle.seek(offset)
                header = handle.read(4)
                if len(header) != 4:
                    raise AlbumLibraryError("FLAC metadata blocks are truncated.")
                offset += 4 + int.from_bytes(header[1:4], "big")
                if offset > size:
                    raise AlbumLibraryError("FLAC metadata block extends beyond the file.")
                if header[0] & 0x80:
                    break
            if offset >= size:
                raise AlbumLibraryError(f"No FLAC audio payload found: {path.name}")
            _range_digest(handle, offset, size, digest)
        elif suffix == ".m4a":
            offset, found = 0, False
            while offset < size:
                handle.seek(offset)
                header = handle.read(8)
                if len(header) != 8:
                    raise AlbumLibraryError("MP4 atom header is truncated.")
                length, kind = struct.unpack(">I4s", header)
                header_size = 8
                if length == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        raise AlbumLibraryError("MP4 extended atom header is truncated.")
                    length = int.from_bytes(extended, "big")
                    header_size = 16
                elif length == 0:
                    length = size - offset
                if length < header_size or offset + length > size:
                    raise AlbumLibraryError("MP4 atom extends beyond the file.")
                if kind == b"mdat":
                    digest.update(struct.pack(">Q", length - header_size))
                    _range_digest(handle, offset + header_size, offset + length, digest)
                    found = True
                offset += length
            if not found:
                raise AlbumLibraryError(f"No M4A media data atom found: {path.name}")
        else:
            raise AlbumLibraryError(f"Audio payload verification is unsupported for {suffix}.")
    return digest.hexdigest()


def _artwork(snapshot) -> tuple[tuple[str, str], ...]:
    return tuple((mime, hashlib.sha256(data).hexdigest()) for mime, data in snapshot.artwork)


def _release_disc_counts(release: tuple[AlbumTrack, ...]) -> dict[int, int]:
    positions: dict[int, list[int]] = {}
    for track in release:
        if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535 for value in (track.disc, track.number)):
            raise AlbumLibraryError("Release disc and track positions must be positive supported integers.")
        positions.setdefault(track.disc, []).append(track.number)
    if sorted(positions) != list(range(1, len(positions) + 1)):
        raise AlbumLibraryError("Release disc positions must be complete, unique, and numbered from 1.")
    for disc, numbers in positions.items():
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            raise AlbumLibraryError(f"Release track positions on disc {disc} must be complete, unique, and numbered from 1.")
    return {disc: len(numbers) for disc, numbers in positions.items()}


def _tag_position(path: Path, tags: dict[str, list[str]], key: str) -> tuple[int | None, int | None]:
    values = tags.get(key) or []
    numbers: set[int] = set()
    totals: set[int] = set()
    for value in values:
        match = re.fullmatch(r"\s*([0-9]+)(?:\s*/\s*([0-9]+))?\s*", str(value))
        if match is None:
            raise AlbumLibraryError(f"Invalid embedded {key} number in {path.name}: {value!r}.")
        number = int(match.group(1))
        total = int(match.group(2)) if match.group(2) else None
        if not 1 <= number <= 65535 or (total is not None and not number <= total <= 65535):
            raise AlbumLibraryError(f"Invalid embedded {key} number or total in {path.name}: {value!r}.")
        numbers.add(number)
        if total is not None:
            totals.add(total)
    if len(numbers) > 1 or len(totals) > 1:
        raise AlbumLibraryError(f"Conflicting embedded {key} values in {path.name}.")
    return (next(iter(numbers)) if numbers else None, next(iter(totals)) if totals else None)


def _filename_positions(digits: str, disc_counts: dict[int, int]) -> set[tuple[int | None, int]]:
    number = int(digits)
    if len(disc_counts) == 1:
        return {(1, number)} if 1 <= number <= disc_counts[1] else set()
    positions: set[tuple[int | None, int]] = set()
    if 1 <= number <= max(disc_counts.values()):
        positions.add((None, number))
    # Recognize standard 101/201 names and the wider canonical padding needed
    # for albums containing more than 99 tracks on a disc.
    for width in {2, max(2, len(str(max(disc_counts.values()))))}:
        if len(digits) <= width:
            continue
        disc, track = int(digits[:-width]), int(digits[-width:])
        if disc in disc_counts and 1 <= track <= disc_counts[disc]:
            positions.add((disc, track))
    return positions


def _title_qualification(value: str) -> tuple[str, tuple[str, ...]]:
    """Separate trailing guest credits from explicit song-version qualifiers."""
    remaining = value.strip()
    qualifiers: list[str] = []
    while match := re.search(r"\s*\(([^()]*)\)\s*$", remaining):
        qualifier = match.group(1).strip()
        remaining = remaining[:match.start()].rstrip()
        if not re.match(r"^(?:with|feat\.?|featuring|ft\.?)\s+\S", qualifier, re.I):
            qualifiers.insert(0, _text_identity(qualifier))
    return _text_identity(remaining), tuple(qualifiers)


def _compatible_title_qualification(local: str, canonical: str) -> bool:
    local_base, local_qualifiers = _title_qualification(local)
    canonical_base, canonical_qualifiers = _title_qualification(canonical)
    if not local_base or local_base != canonical_base:
        return False
    # A source may supply a qualifier absent from an unqualified local title.
    # Explicit existing version/live/remix qualifiers must continue to agree.
    return not local_qualifiers or local_qualifiers == canonical_qualifiers


def _match_track(path: Path, snapshot, duration: int | None, release: tuple[AlbumTrack, ...], *, disc_counts: dict[int, int] | None = None) -> tuple[AlbumTrack, tuple[str, ...]]:
    disc_counts = disc_counts or _release_disc_counts(release)
    multi_disc = len(disc_counts) > 1
    stem = _UUID_SUFFIX.sub("", path.stem)
    prefix = _TRACK_PREFIX.match(stem)
    filename_positions = _filename_positions(prefix.group(1), disc_counts) if prefix else set()
    tag_number, track_total = _tag_position(path, snapshot.tags, "track")
    tag_disc, disc_total = _tag_position(path, snapshot.tags, "disc")
    if tag_disc is not None and tag_disc not in disc_counts:
        raise AlbumLibraryError(f"Conflicting embedded disc number in {path.name}: disc {tag_disc} is not in the selected release.")
    if disc_total is not None and disc_total != len(disc_counts):
        raise AlbumLibraryError(f"Embedded disc total in {path.name} conflicts with the selected release.")
    if track_total is not None and track_total not in ({disc_counts[tag_disc]} if tag_disc is not None else set(disc_counts.values())):
        raise AlbumLibraryError(f"Embedded track total in {path.name} conflicts with the selected release.")
    if not multi_disc and tag_number is not None and prefix is not None and int(prefix.group(1)) != tag_number:
        raise AlbumLibraryError(f"Conflicting track numbers in filename and tags: {path.name}.")
    if multi_disc and len({_text_identity(str(value)) for value in snapshot.tags.get("title", []) if str(value).strip()}) > 1:
        raise AlbumLibraryError(f"Conflicting embedded title values in {path.name}.")
    embedded_title = _first(snapshot.tags, "title")
    title_keys = {_text_identity(title) for title in (embedded_title, prefix.group(2) if prefix else None) if title}
    if tag_number is None and prefix is None and not embedded_title:
        raise AlbumLibraryError(f"Cannot safely identify {path.name}: no track tag or numeric filename prefix. Alphabetic order is not used.")
    hard_candidates = [track for track in release if (tag_disc is None or track.disc == tag_disc) and (tag_number is None or track.number == tag_number) and (track_total is None or disc_counts[track.disc] == track_total)]
    candidates: list[tuple[AlbumTrack, bool, bool, bool]] = []
    embedded_key = _text_identity(embedded_title) if embedded_title else ""
    for track in hard_candidates:
        embedded_match = bool(embedded_key) and embedded_key == _text_identity(track.title)
        title_match = _text_identity(track.title) in title_keys
        duration_match = duration is not None and track.length_ms is not None and abs(duration - track.length_ms) <= max(3000, round(track.length_ms * 0.01))
        if duration is not None and track.length_ms is not None and not duration_match:
            continue
        compatible_title = (
            multi_disc and bool(embedded_title) and not embedded_match and duration_match
            and tag_disc == track.disc and tag_number == track.number
            and filename_positions == {(track.disc, track.number)}
            and _compatible_title_qualification(embedded_title, track.title)
        )
        if multi_disc and embedded_title and not embedded_match and not compatible_title:
            continue
        title_match = title_match or compatible_title
        if not title_match and not duration_match:
            continue
        if tag_number is None and prefix is None and not embedded_match:
            continue
        candidates.append((track, embedded_match and duration_match, title_match, duration_match))
    filename_candidates = {(track.disc, track.number) for track in hard_candidates if any(number == track.number and (disc is None or disc == track.disc) for disc, number in filename_positions)}
    eligible = []
    for candidate in candidates:
        track, strong, _, _ = candidate
        filename_match = any(number == track.number and (disc is None or disc == track.disc) for disc, number in filename_positions)
        if prefix is not None and not filename_match:
            continue
        disc_identified = tag_disc is not None or (len(filename_candidates) == 1 and (track.disc, track.number) in filename_positions)
        if multi_disc and (not disc_identified or (tag_number is None and prefix is None)) and not strong:
            continue
        eligible.append(candidate)
    overridden = False
    if len(eligible) == 1:
        chosen = eligible[0]
    else:
        # A stale/ambiguous filename may be repaired only through unique strong
        # embedded evidence within every trusted disc/track constraint. This is
        # checked against the whole release, never the remaining spare slots.
        strong_candidates = [candidate for candidate in candidates if candidate[1]] if multi_disc else []
        if len(strong_candidates) != 1:
            if tag_number is not None and prefix is not None and filename_positions and all(number != tag_number for _, number in filename_positions):
                raise AlbumLibraryError(f"Conflicting track numbers in filename and tags: {path.name}.")
            raise AlbumLibraryError(f"{path.name} has {'ambiguous' if len(eligible) > 1 or len(strong_candidates) > 1 else 'insufficient or conflicting'} title/duration evidence for the selected MusicBrainz release.")
        chosen = strong_candidates[0]
        overridden = prefix is not None
    track, _, title_match, duration_match = chosen
    evidence = []
    if tag_number is not None:
        evidence.append("embedded-track-number")
    if tag_disc is not None:
        evidence.append("embedded-disc-number")
    if prefix is not None:
        evidence.append("filename-position-overridden" if overridden else "filename-track-number")
        if not overridden and (track.disc, track.number) in filename_positions and multi_disc:
            evidence.append("filename-disc-number")
    if title_match:
        evidence.append("matching-title")
        if multi_disc and embedded_title and embedded_key != _text_identity(track.title):
            evidence.append("compatible-title-qualification")
    if duration_match:
        evidence.append("matching-duration")
    return track, tuple(evidence)


def _rewrite_reference(reference: str, album: Path, mapping: dict[str, AlbumTrackPlan]) -> str:
    parsed = urlsplit(reference)
    file_url = parsed.scheme.casefold() == "file"
    if parsed.scheme and not file_url and not re.match(r"^[A-Za-z]:[\\/]", reference):
        return reference
    if file_url:
        if parsed.netloc.casefold() not in {"", "localhost"}:
            return reference
        local = Path(url2pathname(parsed.path))
    else:
        local = Path(reference.replace("\\", os.sep).replace("/", os.sep))
    resolved = local.resolve() if local.is_absolute() else (album / local).resolve()
    item = mapping.get(str(resolved).casefold())
    if item is None or item.source.name == item.target.name:
        return reference
    separator = max(reference.rfind("/"), reference.rfind("\\"))
    return reference[:separator + 1] + (quote(item.target.name) if file_url else item.target.name)


def _playlist_plans(album: Path, tracks: tuple[AlbumTrackPlan, ...]) -> tuple[AlbumPlaylistPlan, ...]:
    mapping = {str(track.source.resolve()).casefold(): track for track in tracks}
    renamed = [track.source.name.casefold() for track in tracks if track.source.name != track.target.name]
    playlists: list[AlbumPlaylistPlan] = []
    for path in sorted(album.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file():
            continue
        if path.suffix.lower() in _OTHER_REFERENCES:
            if renamed:
                raise AlbumLibraryError(f"{path.name} may reference tracks being renamed. Its {path.suffix} reference format is not supported; album changes are blocked.")
        if path.suffix.lower() not in _PLAYLISTS:
            continue
        original = path.read_bytes()
        bom = original.startswith(b"\xef\xbb\xbf")
        try:
            text = original.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AlbumLibraryError(f"Playlist {path.name} is not UTF-8; its paths cannot be rewritten safely.") from exc
        rewritten: list[str] = []
        for line in text.splitlines(keepends=True):
            body = line.rstrip("\r\n")
            ending = line[len(body):]
            stripped = body.strip()
            if stripped and not stripped.startswith("#"):
                reference = _rewrite_reference(stripped, album, mapping)
                if reference != stripped:
                    left = body[:len(body) - len(body.lstrip())]
                    right = body[len(body.rstrip()):]
                    body = left + reference + right
            rewritten.append(body + ending)
        updated = (b"\xef\xbb\xbf" if bom else b"") + "".join(rewritten).encode("utf-8")
        playlists.append(AlbumPlaylistPlan(path, hashlib.sha256(original).hexdigest(), original, updated))
    return tuple(playlists)


def _release_year(value: str | None) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?", value):
        raise AlbumLibraryError("Full filenames require a known valid MusicBrainz release year; use track-title format when the release date is unknown.")
    complete = value + ("-01-01" if len(value) == 4 else "-01" if len(value) == 7 else "")
    try:
        CalendarDate.fromisoformat(complete)
    except ValueError as exc:
        raise AlbumLibraryError("Full filenames require a known valid MusicBrainz release year; the selected release date is invalid.") from exc
    return value[:4]


def preview_album(album_dir: Path, metadata: AlbumMetadata, *, filename_format: AlbumFilenameFormat = AlbumFilenameFormat.TRACK_TITLE) -> AlbumPlan:
    """Read one album and build a complete, corroborated, mutation-free plan."""
    album = _album_root(album_dir)
    try:
        selected_format = AlbumFilenameFormat(filename_format)
    except ValueError as exc:
        raise AlbumLibraryError(f"Unsupported album filename format: {filename_format}") from exc
    if not metadata.release_id or not metadata.source_url or not metadata.title or not metadata.artist:
        raise AlbumLibraryError("MusicBrainz release provenance, album title, and artist must be complete.")
    if not metadata.tracks:
        raise AlbumLibraryError("The selected release has no tracks.")
    release_year = _release_year(metadata.date) if metadata.date is not None or selected_format is AlbumFilenameFormat.FULL else None
    disc_counts = _release_disc_counts(metadata.tracks)
    track_width = max(2, len(str(max(disc_counts.values()))))
    disc_width = max(1, len(str(len(disc_counts))))
    files = _album_files(album)
    if len(files) != len(metadata.tracks):
        raise AlbumLibraryError(f"Album is incomplete or has extra audio: found {len(files)} local tracks; the selected release has {len(metadata.tracks)}.")
    plans: list[AlbumTrackPlan] = []
    assigned: set[tuple[int, int]] = set()
    for source in files:
        try:
            snapshot = read_metadata(source, replacing_date=bool(metadata.date))
            duration = _duration_ms(source)
            track, evidence = _match_track(source, snapshot, duration, metadata.tracks, disc_counts=disc_counts)
        except MetadataIOError as exc:
            raise AlbumLibraryError(str(exc)) from exc
        position = track.disc, track.number
        if position in assigned:
            raise AlbumLibraryError(f"More than one local file matches release disc {track.disc}, track {track.number}; mapping is ambiguous.")
        assigned.add(position)
        number_component = f"{track.number:0{track_width}d}"
        if len(disc_counts) > 1:
            number_component = f"{track.disc:0{disc_width}d}" + number_component
        components = [number_component, sanitize_component(track.title)]
        if selected_format is AlbumFilenameFormat.FULL:
            components.extend((sanitize_component(metadata.title), sanitize_component(track.artist or metadata.artist), f"({release_year})"))
        filename = " - ".join(components) + source.suffix
        if len(filename.encode("utf-16-le")) // 2 > 255:
            raise AlbumLibraryError(f"Canonical filename exceeds the Windows component limit: {filename}")
        target = album / filename
        tags = {"title": track.title, "artist": track.artist or metadata.artist, "album_artist": metadata.artist, "album": metadata.title, "track": f"{track.number}/{disc_counts[track.disc]}", "disc": f"{track.disc}/{len(disc_counts)}"}
        if metadata.date:
            tags["date"] = metadata.date
        plans.append(AlbumTrackPlan(source, target, tags, _sha256(source), audio_payload_sha256(source), evidence, tuple(sorted((key, tuple(values)) for key, values in snapshot.tags.items())), _artwork(snapshot), duration))
    if assigned != {(track.disc, track.number) for track in metadata.tracks}:
        raise AlbumLibraryError("The local album does not completely and uniquely cover every release disc and track.")
    sources = {str(path).casefold() for path in files}
    destinations: set[str] = set()
    for track in plans:
        key = str(track.target).casefold()
        if key in destinations:
            raise AlbumLibraryError(f"Canonical filename collision: {track.target.name}")
        destinations.add(key)
        for existing in album.iterdir():
            if existing.name.casefold() == track.target.name.casefold() and str(existing).casefold() not in sources:
                raise AlbumLibraryError(f"Destination already exists and will not be overwritten: {existing.name}")
    tracks = tuple(sorted(plans, key=lambda item: (_number(item.tags["disc"]) or 0, _number(item.tags["track"]) or 0)))
    playlists = _playlist_plans(album, tracks)
    return AlbumPlan(album, metadata, tracks, playlists, f"album-plan-{uuid.uuid4().hex}", selected_format)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _link_rename_no_overwrite(source: Path, target: Path) -> None:
    os.link(source, target, follow_symlinks=False)
    try:
        source.unlink()
    except OSError as unlink_exc:
        try:
            if not source.exists() or not os.path.samefile(source, target):
                raise OSError("The just-created link no longer has the expected source identity.")
            target.unlink()
        except OSError as compensation_exc:
            raise _AlbumRenameCompensationError(f"Source removal failed and the created link could not be safely removed: {target}; recovery required: {compensation_exc}") from unlink_exc
        raise


def _rename_no_overwrite(source: Path, target: Path) -> None:
    if target.exists() or _linked(target):
        raise AlbumLibraryError(f"Destination appeared before commit: {target.name}")
    if os.name == "nt":
        # Windows rename refuses an existing destination, including a race.
        os.rename(source, target)
    else:
        # POSIX rename overwrites; link/unlink gives regular files no-clobber semantics.
        _link_rename_no_overwrite(source, target)


def _verify_normalized(path: Path, track: AlbumTrackPlan) -> None:
    snapshot = verify_metadata(path, track.tags, expected_cover_sha256=None)
    if _artwork(snapshot) != track.artwork_hashes:
        raise AlbumLibraryError(f"Artwork changed while normalizing {track.source.name}.")
    for key, original in track.original_tags:
        if key not in track.tags and tuple(snapshot.tags.get(key) or []) != original:
            raise AlbumLibraryError(f"Unrelated metadata {key!r} changed in {track.source.name}.")
    if audio_payload_sha256(path) != track.payload_sha256:
        raise AlbumLibraryError(f"Compressed audio payload changed in {track.source.name}; no reencoding is permitted.")
    duration = _duration_ms(path)
    if track.duration_ms is not None and (duration is None or abs(duration - track.duration_ms) > 1):
        raise AlbumLibraryError(f"Audio duration changed in {track.source.name}.")


def _preflight(plan: AlbumPlan) -> None:
    if _album_root(plan.album_dir) != plan.album_dir:
        raise AlbumLibraryError("Album directory changed after preview.")
    if set(_album_files(plan.album_dir)) != {track.source for track in plan.tracks}:
        raise AlbumLibraryError("The album audio file set changed after preview; preview again.")
    for track in plan.tracks:
        if track.source.parent != plan.album_dir or track.target.parent != plan.album_dir:
            raise AlbumLibraryError("A planned audio path escapes the selected album.")
        if _sha256(track.source) != track.sha256:
            raise AlbumLibraryError(f"Source changed after preview: {track.source.name}; preview again.")
    current = _playlist_plans(plan.album_dir, plan.tracks)
    if current != plan.playlists:
        raise AlbumLibraryError("Album playlists changed after preview; preview again.")
    source_names = {track.source.name.casefold() for track in plan.tracks}
    target_names = [track.target.name.casefold() for track in plan.tracks]
    if len(target_names) != len(set(target_names)):
        raise AlbumLibraryError("Planned target names collide.")
    for existing in plan.album_dir.iterdir():
        if existing.name.casefold() in target_names and existing.name.casefold() not in source_names:
            raise AlbumLibraryError(f"Destination appeared after preview: {existing.name}")


def apply_album(plan: AlbumPlan, transaction_root: Path) -> AlbumResult:
    """Stage, verify, and commit an album with retained original backups."""
    _preflight(plan)
    raw_root = Path(transaction_root).expanduser()
    if _linked(raw_root):
        raise AlbumLibraryError("Transaction storage must not be a symlink or junction.")
    root = raw_root.resolve()
    if root == plan.album_dir or root.is_relative_to(plan.album_dir):
        raise AlbumLibraryError("Transaction reports and backups must be stored outside the album.")
    root.mkdir(parents=True, exist_ok=True)
    transaction_id = f"album-{uuid.uuid4().hex}"
    transaction = root / transaction_id
    transaction.mkdir(exist_ok=False)
    backups, work = transaction / "originals", transaction / "normalized"
    backups.mkdir()
    work.mkdir()
    report_path = transaction / "transaction.json"
    report: dict[str, Any] = {"schemaVersion": 1, "transactionId": transaction_id, "planId": plan.plan_id, "albumDirectory": str(plan.album_dir), "phase": "preparing", "source": {"provider": "MusicBrainz", "url": plan.metadata.source_url, "releaseId": plan.metadata.release_id, "album": plan.metadata.title, "artist": plan.metadata.artist, "date": plan.metadata.date}, "audioVerification": "compressed-payload-sha256; metadata-only writes; no audio reencoding", "tracks": [], "playlists": [], "rollback": {"status": "not-required", "originalBackups": str(backups)}}
    report["filenameFormat"] = plan.filename_format.value
    rows: list[dict[str, Any]] = []
    playlist_rows: list[dict[str, Any]] = []
    installed: list[tuple[AlbumTrackPlan, dict[str, Any]]] = []
    held: list[tuple[AlbumTrackPlan, dict[str, Any]]] = []
    changed_playlists: list[tuple[AlbumPlaylistPlan, dict[str, Any]]] = []
    _write_json_atomic(report_path, report)
    try:
        for index, track in enumerate(plan.tracks):
            backup = backups / track.source.name
            shutil.copy2(track.source, backup)
            if _sha256(backup) != track.sha256:
                raise AlbumLibraryError(f"Original backup verification failed: {track.source.name}")
            normalized = work / track.target.name
            shutil.copy2(backup, normalized)
            write_metadata(normalized, track.tags, None)
            _verify_normalized(normalized, track)
            updated_sha = _sha256(normalized)
            hold = plan.album_dir / f".mnemosyne-{transaction_id}-{index}.original{track.source.suffix}"
            prepared = plan.album_dir / f".mnemosyne-{transaction_id}-{index}.new{track.source.suffix}"
            row = {"source": str(track.source), "target": str(track.target), "originalBackup": str(backup), "normalizedCopy": str(normalized), "originalSha256": track.sha256, "normalizedSha256": updated_sha, "audioPayloadSha256": track.payload_sha256, "matchingEvidence": list(track.matching_evidence), "tags": track.tags, "hold": str(hold), "prepared": str(prepared), "state": "verified"}
            rows.append(row)
        for index, playlist in enumerate(plan.playlists):
            backup = backups / playlist.path.name
            backup.write_bytes(playlist.original_bytes)
            if _sha256(backup) != playlist.sha256:
                raise AlbumLibraryError(f"Playlist backup verification failed: {playlist.path.name}")
            normalized = work / playlist.path.name
            normalized.write_bytes(playlist.updated_bytes)
            playlist_rows.append({"path": str(playlist.path), "backup": str(backup), "normalizedCopy": str(normalized), "originalSha256": playlist.sha256, "updatedSha256": hashlib.sha256(playlist.updated_bytes).hexdigest(), "changed": playlist.changed, "prepared": str(plan.album_dir / f".mnemosyne-{transaction_id}-playlist-{index}.tmp"), "state": "verified"})
        report["tracks"], report["playlists"] = rows, playlist_rows
        report["phase"] = "verified"
        _write_json_atomic(report_path, report)
        _preflight(plan)
        for row in rows:
            prepared = Path(row["prepared"])
            if prepared.exists():
                raise AlbumLibraryError("A transaction temporary path already exists.")
            shutil.copy2(row["normalizedCopy"], prepared)
            if _sha256(prepared) != row["normalizedSha256"]:
                raise AlbumLibraryError("Prepared media copy does not match its verified staged copy.")
        for row in playlist_rows:
            if row["changed"]:
                prepared = Path(row["prepared"])
                with prepared.open("xb") as handle:
                    handle.write(Path(row["normalizedCopy"]).read_bytes())
        report["phase"] = "committing"
        _write_json_atomic(report_path, report)
        for track, row in zip(plan.tracks, rows, strict=True):
            if _sha256(track.source) != track.sha256:
                raise AlbumLibraryError(f"Source changed immediately before commit: {track.source.name}")
            _rename_no_overwrite(track.source, Path(row["hold"]))
            held.append((track, row))
            row["state"] = "original-held"
            _write_json_atomic(report_path, report)
        for track, row in zip(plan.tracks, rows, strict=True):
            _rename_no_overwrite(Path(row["prepared"]), track.target)
            installed.append((track, row))
            row["state"] = "installed"
            _write_json_atomic(report_path, report)
            if _sha256(track.target) != row["normalizedSha256"]:
                raise AlbumLibraryError(f"Final normalized bytes failed verification: {track.target.name}")
            _verify_normalized(track.target, track)
        for playlist, row in zip(plan.playlists, playlist_rows, strict=True):
            if _sha256(playlist.path) != playlist.sha256:
                raise AlbumLibraryError(f"Playlist changed immediately before commit: {playlist.path.name}")
            if playlist.changed:
                os.replace(row["prepared"], playlist.path)
                changed_playlists.append((playlist, row))
                row["state"] = "installed"
                _write_json_atomic(report_path, report)
                if _sha256(playlist.path) != row["updatedSha256"]:
                    raise AlbumLibraryError(f"Final playlist failed verification: {playlist.path.name}")
        report["phase"] = "complete"
        report["rollback"]["status"] = "retained-original-backups"
        _write_json_atomic(report_path, report)
    except BaseException as exc:
        failures: list[str] = [str(exc)] if isinstance(exc, _AlbumRenameCompensationError) else []
        for playlist, row in reversed(changed_playlists):
            try:
                if _sha256(playlist.path) != row["updatedSha256"]:
                    raise AlbumLibraryError("Playlist was edited externally during rollback.")
                temporary = Path(row["prepared"])
                temporary.write_bytes(Path(row["backup"]).read_bytes())
                os.replace(temporary, playlist.path)
                if _sha256(playlist.path) != playlist.sha256:
                    raise AlbumLibraryError("Restored playlist bytes differ from the original.")
            except Exception as rollback_exc:
                failures.append(f"{playlist.path.name}: {rollback_exc}")
        for track, row in reversed(installed):
            try:
                if _sha256(track.target) != row["normalizedSha256"]:
                    raise AlbumLibraryError("Normalized file was edited externally during rollback.")
                track.target.unlink()
            except Exception as rollback_exc:
                failures.append(f"{track.target.name}: {rollback_exc}")
        for track, row in reversed(held):
            try:
                hold = Path(row["hold"])
                if _sha256(hold) != track.sha256:
                    raise AlbumLibraryError("Held original does not match its preflight hash.")
                _rename_no_overwrite(hold, track.source)
                if _sha256(track.source) != track.sha256:
                    raise AlbumLibraryError("Restored original bytes differ.")
            except Exception as rollback_exc:
                failures.append(f"{track.source.name}: {rollback_exc}")
        for row in rows + playlist_rows:
            prepared = Path(row["prepared"])
            if prepared.exists():
                try:
                    if _sha256(prepared) == row.get("normalizedSha256", row.get("updatedSha256")):
                        prepared.unlink()
                except OSError as cleanup_exc:
                    failures.append(f"Temporary cleanup: {cleanup_exc}")
        report["phase"] = "rollback-incomplete" if failures else "rolled-back"
        report["error"] = str(exc)
        report["rollback"] = {"status": "incomplete" if failures else "verified", "originalBackups": str(backups), "failures": failures}
        try:
            _write_json_atomic(report_path, report)
        except OSError as report_exc:
            failures.append(f"Could not write final recovery report: {report_exc}")
        detail = "; rollback incomplete: " + "; ".join(failures) if failures else "; original album restored"
        raise AlbumLibraryError(f"Album transaction failed: {exc}{detail}. Retained backups/report: {transaction}") from exc
    leftovers: list[str] = []
    for track, row in held:
        hold = Path(row["hold"])
        try:
            if _sha256(hold) == track.sha256 and _sha256(Path(row["originalBackup"])) == track.sha256:
                hold.unlink()
            else:
                leftovers.append(str(hold))
        except OSError:
            leftovers.append(str(hold))
    if leftovers:
        report["retainedAlbumTemporaryFiles"] = leftovers
        _write_json_atomic(report_path, report)
    return AlbumResult(plan.album_dir, transaction, report_path, tuple(track.target for track in plan.tracks), len(changed_playlists))
