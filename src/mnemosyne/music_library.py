"""Read-only existing-library music recovery: scan, identify, preview.

Music data lives only in Mnemosyne's state directory. This module has no function
that writes to, renames, retags, or moves files under the scanned music root.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from datetime import date as CalendarDate
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen import MutagenError

from .config import runtime_root
from .paths import sanitize_component


class MusicLibraryError(RuntimeError):
    """A music-library report or source is not safe to use."""


AUDIO_EXTENSIONS = {".mp3", ".flac", ".m4a", ".m4b", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif"}
PLAYLIST_EXTENSIONS = {".m3u", ".m3u8", ".pls"}
TAG_FIELDS = ("artist", "albumartist", "album", "title", "tracknumber", "discnumber", "genre", "date")
BASE_GENRES = {
    "blues": "Blues", "jazz": "Jazz", "soul": "Soul", "funk": "Funk",
    "rock": "Rock", "punk": "Punk", "metal": "Metal", "pop": "Pop",
    "electronic": "Electronic", "hip hop": "Hip-Hop", "hip-hop": "Hip-Hop",
    "country": "Country", "classical": "Classical", "folk": "Folk",
    "reggae": "Reggae", "r&b": "R&B", "soundtrack": "Soundtrack",
    "latin": "Latin", "world": "World", "ambient": "Ambient",
}
_DISC = re.compile(r"^(?:CD|Disc|Disk)\s*[#_ -]*0*(\d{1,2})$", re.IGNORECASE)
_TRACK = re.compile(r"^\s*(\d+)[\s._-]+(.+)$")
_BITRATE_LABEL = re.compile(r"^(.+?)\s*\((?:128|160|192|224|256|320)\)$")
_WS = re.compile(r"\s+")


def _now_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        json.loads(temporary.read_text(encoding="utf-8"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_report(kind: str, path: Path | None) -> tuple[Path, dict[str, Any]]:
    base = (runtime_root() / "state" / kind).resolve()
    prefix = {"scans": "library-scan-music-", "identifications": "library-identification-music-"}[kind]
    if path is None:
        matches = [p for p in base.glob(f"{prefix}*.json") if p.is_file() and not p.is_symlink()]
        if not matches:
            raise MusicLibraryError(f"No Music {kind} report. Run the preceding music command first.")
        matches.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        selected = matches[0]
    else:
        raw = Path(path)
        selected = raw if raw.is_absolute() else base / raw
        if selected.is_symlink() or selected.parent.resolve() != base:
            raise MusicLibraryError(f"Report must be a direct non-symlinked file in {base}.")
    if selected.is_symlink() or not selected.is_file() or selected.resolve().parent != base:
        raise MusicLibraryError(f"Invalid Music report: {selected}")
    try:
        result = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MusicLibraryError(f"Cannot read Music report {selected}: {exc}") from exc
    if not isinstance(result, dict) or result.get("media") != "music" or result.get("schemaVersion") != 1:
        raise MusicLibraryError("Expected a Music report with schemaVersion 1.")
    safety = result.get("safety") or {}
    if any(safety.get(k) is not False for k in ("libraryModified", "metadataModified", "filesRenamed", "filesMoved")):
        raise MusicLibraryError("Report has invalid read-only safety provenance.")
    return selected.resolve(), result


def _walk_music(root: Path, scope: Path) -> tuple[list[Path], list[Path], int]:
    audio: list[Path] = []
    playlists: list[Path] = []
    skipped = 0
    for current, dirs, files in os.walk(scope, followlinks=False):
        base = Path(current)
        safe_dirs: list[str] = []
        for d in dirs:
            subdir = base / d
            if subdir.is_symlink() or not subdir.resolve().is_relative_to(root):
                skipped += 1
            else:
                safe_dirs.append(d)
        dirs[:] = sorted(safe_dirs, key=str.casefold)
        for f in files:
            p = base / f
            if p.is_symlink() or not p.resolve().is_relative_to(root):
                skipped += 1
            elif p.suffix.casefold() in AUDIO_EXTENSIONS:
                audio.append(p)
            elif p.suffix.casefold() in PLAYLIST_EXTENSIONS:
                playlists.append(p)
    return (sorted(audio, key=lambda p: str(p).casefold()),
            sorted(playlists, key=lambda p: str(p).casefold()), skipped)


def _tag_first(value: object) -> str | None:
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_tags(path: Path) -> tuple[dict[str, str], str, int | None]:
    try:
        media = MutagenFile(path, easy=True)
        if media is None:
            return {}, "unreadable", None
        tags: dict[str, str] = {}
        for field in TAG_FIELDS:
            value = _tag_first(media.get(field))
            if value:
                tags[field] = value
        bitrate = getattr(getattr(media, "info", None), "bitrate", None)
        return tags, "available" if tags else "missing", int(bitrate) if bitrate else None
    except (MutagenError, OSError, ValueError, TypeError) as exc:
        return {}, f"unreadable: {type(exc).__name__}", None


def scan_music_library(music_root: Path, *, only: Path | None = None) -> dict[str, Any]:
    root = Path(music_root).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise MusicLibraryError(f"Music root is missing or symlinked: {root}")
    root = root.resolve()
    if only is None:
        scope = root
    else:
        raw = Path(only)
        # Scope is relative to the Music root, never to process CWD.
        scope = (raw if raw.is_absolute() else root / raw)
        if not scope.resolve().is_relative_to(root):
            raise MusicLibraryError("Music scan scope escapes root or is symlinked.")
        # Refuse symlinked ancestors even if their target remains within root.
        relative_parts = scope.relative_to(root).parts
        current = root
        for component in relative_parts:
            current = current / component
            if current.is_symlink():
                raise MusicLibraryError("Music scan scope escapes root or is symlinked.")
        scope = scope.resolve()
    if not scope.is_dir():
        raise MusicLibraryError(f"Music scan scope is not a directory: {scope}")

    audio, playlists, skipped = _walk_music(root, scope)
    tracks = []
    for path in audio:
        try:
            stat = path.stat()
        except OSError as exc:
            raise MusicLibraryError(f"Could not stat music file {path}: {exc}") from exc
        tags, tag_state, bitrate = _read_tags(path)
        tracks.append({
            "path": path.relative_to(root).as_posix(),
            "sizeBytes": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "embedded": tags,
            "tagState": tag_state,
            "bitrateBps": bitrate,
        })
    scan_id = _now_id("library-scan-music")
    report = runtime_root() / "state" / "scans" / f"{scan_id}.json"
    data = {
        "schemaVersion": 1, "scanId": scan_id,
        "scannedAt": datetime.now(timezone.utc).isoformat(), "media": "music",
        "musicRoot": str(root), "scope": scope.relative_to(root).as_posix(),
        "summary": {"tracks": len(tracks), "missingTags": sum(t["tagState"] == "missing" for t in tracks),
                    "unreadable": sum(t["tagState"].startswith("unreadable") for t in tracks),
                    "playlists": len(playlists), "symlinksSkipped": skipped},
        "tracks": tracks,
        "playlists": [p.relative_to(root).as_posix() for p in playlists],
        "safety": {"libraryModified": False, "metadataModified": False,
                   "filesRenamed": False, "filesMoved": False, "reportWrittenOutsideLibrary": True},
    }
    if report.resolve().is_relative_to(root):
        raise MusicLibraryError("Mnemosyne runtime reports must be outside the Music root.")
    _atomic_json(report, data)
    data["reportPath"] = str(report)
    return data


def _validated_scan(path: Path | None) -> tuple[Path, dict[str, Any], Path, Path]:
    report_path, scan = _load_report("scans", path)
    root = Path(str(scan.get("musicRoot") or "")).resolve()
    scope = (root / str(scan.get("scope") or ".")).resolve()
    if not root.is_dir() or not scope.is_dir() or not scope.is_relative_to(root):
        raise MusicLibraryError("Music scan root/scope is unavailable or escapes the root.")
    current, current_playlists, _ = _walk_music(root, scope)
    current_keys = {p.relative_to(root).as_posix().casefold() for p in current}
    playlist_keys = {p.relative_to(root).as_posix().casefold() for p in current_playlists}
    recorded_playlists = scan.get("playlists")
    if not isinstance(recorded_playlists, list) or playlist_keys != {str(x).casefold() for x in recorded_playlists}:
        raise MusicLibraryError("Music playlist set changed since scan; rescan before continuing.")
    recorded_tracks = scan.get("tracks")
    if not isinstance(recorded_tracks, list):
        raise MusicLibraryError("Music scan track list is invalid.")
    recorded_keys = {str(t.get("path") or "").casefold() for t in recorded_tracks}
    if current_keys != recorded_keys or len(recorded_keys) != len(recorded_tracks):
        raise MusicLibraryError("Music file set changed since scan; rescan before continuing.")
    for track in recorded_tracks:
        relative = Path(str(track.get("path") or ""))
        full = root / relative
        if full.is_symlink() or not full.resolve().is_relative_to(scope):
            raise MusicLibraryError("Music scan includes a symlink or path outside scan scope.")
        stat = full.stat()
        if stat.st_size != track.get("sizeBytes") or stat.st_mtime_ns != track.get("mtimeNs"):
            raise MusicLibraryError(f"Music file changed since scan: {relative}; rescan before continuing.")
    return report_path, scan, root, scope


def _normal(text: str | None) -> str:
    return _WS.sub(" ", text or "").strip().casefold()


def _path_evidence(path: Path) -> dict[str, Any]:
    parts = path.parts
    album_artist = album = genre_folder = None
    disc = None
    disc_folder = None
    notes: list[str] = []
    if len(parts) >= 4:
        # Expected existing layout: Genre/Artist/Album/[Disc]/Track.ext
        genre_folder, album_artist, album = parts[:3]
        if len(parts) == 5:
            d = _DISC.match(parts[3])
            if d:
                disc = int(d.group(1))
                disc_folder = parts[3]
            else:
                notes.append("Unrecognized disc-level folder; review structure.")
        elif len(parts) > 5:
            notes.append("Unexpected deep nesting; review folder structure.")
    else:
        # An incomplete layout is not an alternative album or artist identity.
        # A known genre or a nearby disc folder can still supply that evidence.
        if len(parts) > 1:
            genre_folder = parts[0]
        if len(parts) > 2 and (d := _DISC.match(parts[-2])):
            disc = int(d.group(1))
            disc_folder = parts[-2]
        notes.append("Expected Genre/Artist/Album[/Disc]/Track; folder identity incomplete.")
    if album:
        bitrate_suffix = _BITRATE_LABEL.match(album)
        if bitrate_suffix:
            album = bitrate_suffix.group(1).strip()
            notes.append("Removed possible bitrate-style album-folder suffix for preview only.")
    track_number = None
    digits = None
    match = _TRACK.match(path.stem)
    if match:
        digits = match.group(1)
        track_number = int(digits) or None
        title = match.group(2)
    else:
        title = path.stem
        notes.append("Track number not found in filename.")
    filename_text = title
    title = _WS.sub(" ", title.replace("_", " ")).strip()
    return {
        "genreFolder": genre_folder, "artist": album_artist, "album": album,
        "albumFolder": parts[2] if len(parts) >= 4 else None,
        "disc": disc, "discFolder": disc_folder,
        "track": track_number, "trackDigits": digits, "title": title,
        "filenameText": filename_text, "filenameFormat": "track-title",
        "notes": notes,
    }


def _canonical_genre(value: str | None) -> tuple[str | None, list[str]]:
    if not value:
        return None, []
    key = _normal(value)
    if key in BASE_GENRES:
        return BASE_GENRES[key], []
    # Combined folders such as Blues-Jazz-Soul provide *candidates*, never a verdict.
    pieces = re.split(r"\s*(?:[-/|;,]+)\s*", value)
    candidates = sorted({BASE_GENRES[_normal(piece)] for piece in pieces if _normal(piece) in BASE_GENRES})
    return None, candidates


def _number(value: str | None) -> int | None:
    if not value:
        return None
    match = re.match(r"^\s*(\d+)(?:\s*/\s*(\d+))?\s*$", value)
    number = int(match.group(1)) if match else None
    total = int(match.group(2)) if match and match.group(2) is not None else None
    return number if number is not None and number > 0 and (total is None or number <= total) else None


def _number_total(value: str | None) -> int | None:
    if _number(value) is None or "/" not in value:
        return None
    return int(value.rsplit("/", 1)[1].strip())


def _release_year(value: str | None) -> str | None:
    if not value or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return None
    try:
        CalendarDate.fromisoformat(value + "-01" * (3 - len(value.split("-"))))
    except ValueError:
        return None
    return value[:4]


def _corroborate_full_filename(folder: dict[str, Any], embedded: dict[str, str]) -> list[str]:
    """Recognize the whole metadata suffix, with no ambiguous field splitting."""
    title, album = embedded.get("title"), embedded.get("album")
    artist = embedded.get("artist") or embedded.get("albumartist")
    if not all((title, album, artist)) or folder["trackDigits"] is None:
        return []
    metadata_body = " - ".join(sanitize_component(value) for value in (title, album, artist))
    # Only the final year is parsed, after the complete three-field suffix is
    # corroborated. Delimiters inside those fields remain literal characters.
    pattern = re.compile(re.escape(_normal(metadata_body)) + r" - \((\d{4})\)$")
    match = pattern.fullmatch(_normal(folder["filenameText"]))
    if not match:
        return []
    folder["title"] = title
    folder["filenameYear"] = match.group(1)
    year = _release_year(embedded.get("date"))
    if year is None:
        folder["notes"].append("Full filename year has no valid embedded release date to corroborate it.")
        return []
    if year != match.group(1):
        return [f"date: embedded {embedded['date']!r} disagrees with filename year {match.group(1)!r}."]
    folder["filenameFormat"] = "full"
    if _normal(sanitize_component(album)) == _normal(folder["albumFolder"]):
        # A genuine album called "Album (256)" is not a bitrate label when
        # embedded metadata and the complete full filename corroborate it.
        folder["album"] = folder["albumFolder"]
        folder["notes"] = [note for note in folder["notes"] if not note.startswith("Removed possible bitrate-style")]
    return []


def _album_folder_agrees(album: str, folder_album: str, album_artist: str | None, *, full_filename: bool) -> bool:
    if _normal(album) == _normal(folder_album):
        return True
    if not full_filename:
        return False
    if _normal(sanitize_component(album)) == _normal(folder_album):
        return True
    candidate = _normal(folder_album)
    prefix = _normal(sanitize_component(album_artist)) + " - " if album_artist else None
    if prefix and candidate.startswith(prefix):
        candidate = candidate[len(prefix):]
    # A filename independently corroborated by tags permits these narrow
    # cosmetic folder aliases, never arbitrary differences in album identity.
    return re.sub(r"^the\s+", "", candidate) == re.sub(r"^the\s+", "", _normal(sanitize_component(album)))


def _position_evidence(folder: dict[str, Any], embedded: dict[str, str]) -> tuple[int | None, int | None, bool, list[str]]:
    tag_track, tag_disc = _number(embedded.get("tracknumber")), _number(embedded.get("discnumber"))
    path_track, path_disc = folder["track"], folder["disc"]
    conflicts: list[str] = []
    for field, number in (("tracknumber", tag_track), ("discnumber", tag_disc)):
        if embedded.get(field) and number is None:
            conflicts.append(f"{field}: invalid embedded number {embedded[field]!r}.")
    if tag_disc is not None and path_disc is not None and tag_disc != path_disc:
        conflicts.append(f"disc: embedded {tag_disc} disagrees with path {path_disc}.")
    disc = tag_disc if tag_disc is not None else path_disc
    track = tag_track if tag_track is not None else path_track
    digits = folder["trackDigits"]
    folder["filenameNumbering"] = "track"
    ambiguous = False
    # An explicit track 101 corroborates a literal 101 before any combined
    # interpretation. This also protects albums whose track counts exceed 99.
    if path_track is not None and tag_track == path_track:
        return track, disc, ambiguous, conflicts
    combined = None
    single_disc = _number_total(embedded.get("discnumber")) == 1
    if not single_disc and digits is not None and len(digits) >= 3:
        prefix_disc, prefix_track = int(digits[:-2]), int(digits[-2:])
        if prefix_disc > 0 and prefix_track > 0:
            combined = (prefix_disc, prefix_track)
    # Wider per-disc track fields (e.g. 2101) can be corroborated directly by
    # the explicit tag pair; no wider interpretation is guessed without tags.
    if not single_disc and tag_disc is not None and tag_track is not None and digits is not None:
        if any(int(digits[:-width]) == tag_disc and int(digits[-width:]) == tag_track
               for width in range(2, len(digits))):
            combined = (tag_disc, tag_track)
    if combined is not None:
        filename_disc, filename_track = combined
        if disc is not None and filename_disc == disc and (tag_track is None or filename_track == tag_track):
            folder["filenameNumbering"] = "disc-track"
            folder["filenameDisc"] = filename_disc
            folder["track"] = filename_track
            return filename_track if tag_track is None else tag_track, disc, False, conflicts
        if disc is None and (tag_track is None or filename_track == tag_track):
            ambiguous = True
            folder["notes"].append("Numeric filename prefix could be a disc/track code or a literal track number; disc evidence is required.")
            # A track tag may identify the track without resolving the disc.
            return tag_track, None, ambiguous, conflicts
        if disc is not None and filename_disc != disc and tag_track is None:
            conflicts.append(f"disc: filename prefix suggests {filename_disc}, which disagrees with disc {disc}.")
            return None, disc, False, conflicts
    if tag_track is not None and path_track is not None and tag_track != path_track:
        conflicts.append(f"track: embedded {tag_track} disagrees with path {path_track}.")
    return track, disc, ambiguous, conflicts


def _identity(track: dict[str, Any]) -> dict[str, Any]:
    path = Path(track["path"])
    folder = _path_evidence(path)
    embedded = track.get("embedded") or {}
    sources: dict[str, str] = {}
    conflicts = _corroborate_full_filename(folder, embedded)
    full_filename = folder["filenameFormat"] == "full"
    fields = {
        "artist": (embedded.get("artist") or embedded.get("albumartist"), folder["artist"]),
        "albumArtist": (embedded.get("albumartist") or embedded.get("artist"), folder["artist"]),
        "album": (embedded.get("album"), folder["album"]),
        "title": (embedded.get("title"), folder["title"]),
    }
    identity: dict[str, Any] = {}
    for field, (tag, inferred) in fields.items():
        identity[field] = tag or inferred
        sources[field] = "embedded" if tag else ("folder" if field != "title" else "filename")
        if not tag or not inferred or field == "artist":
            # The artist folder identifies the album artist, not a featured
            # track artist. Album-artist agreement is checked independently.
            continue
        if field == "album":
            agrees = _album_folder_agrees(tag, inferred, identity["albumArtist"], full_filename=full_filename)
        elif field == "title" and not full_filename:
            agrees = _normal(sanitize_component(tag)) == _normal(folder["filenameText"]) or _normal(tag) == _normal(inferred)
        elif field == "albumArtist" and full_filename:
            agrees = _normal(sanitize_component(tag)) == _normal(inferred)
        else:
            agrees = _normal(tag) == _normal(inferred)
        if not agrees:
            conflicts.append(f"{field}: embedded {tag!r} disagrees with path {inferred!r}.")

    resolved_track, resolved_disc, ambiguous_position, position_conflicts = _position_evidence(folder, embedded)
    conflicts.extend(position_conflicts)
    identity["track"], identity["disc"] = resolved_track, resolved_disc
    sources["track"] = "embedded" if _number(embedded.get("tracknumber")) is not None else "filename"
    sources["disc"] = "embedded" if _number(embedded.get("discnumber")) is not None else ("folder" if folder["disc"] is not None else "unresolved")

    embedded_genre, embedded_candidates = _canonical_genre(embedded.get("genre"))
    folder_genre, folder_candidates = _canonical_genre(folder["genreFolder"])
    genre = embedded_genre or folder_genre
    if embedded_genre and folder_genre and embedded_genre != folder_genre:
        conflicts.append(f"genre: embedded {embedded_genre!r} disagrees with folder {folder_genre!r}.")
    identity["genre"] = genre
    identity["date"] = embedded.get("date")  # NEVER infer 1975 from nowhere.
    sources["genre"] = "embedded" if embedded_genre else ("folder" if folder_genre else "unresolved")
    sources["date"] = "embedded" if embedded.get("date") else "unresolved"
    genre_candidates = sorted(set(embedded_candidates + folder_candidates))
    notes = list(folder["notes"])
    if not genre:
        notes.append("Canonical base genre unresolved; explicit review required.")
    if track.get("tagState") != "available":
        notes.append("Embedded tags missing/unreadable; proposed identity uses folder and filename evidence.")
    if not all(identity.get(k) for k in ("artist", "albumArtist", "album", "title", "track")):
        notes.append("Required track identity is incomplete.")
    if conflicts:
        confidence = "CONFLICT"
    elif not all(identity.get(k) for k in ("artist", "album", "title", "track")):
        confidence = "UNRESOLVED"
    elif not ambiguous_position and track.get("tagState") == "available" and all(embedded.get(k) for k in ("artist", "album", "title")):
        confidence = "HIGH"
    else:
        confidence = "MEDIUM"
    review = confidence not in ("HIGH", "VERIFIED") or not genre or bool(notes) or bool(conflicts)
    return {
        "path": track["path"], "identity": identity,
        "confidence": confidence, "reviewRequired": review,
        "genreCandidates": genre_candidates,
        "provenance": sources,
        "evidence": {"embedded": embedded, "folder": folder},
        "conflicts": conflicts, "notes": notes,
        "tagState": track.get("tagState"),
    }


def identify_music_library(*, scan_report: Path | None = None) -> dict[str, Any]:
    path, scan, root, _ = _validated_scan(scan_report)
    items = [_identity(track) for track in scan["tracks"]]
    identification_id = _now_id("library-identification-music")
    report = runtime_root() / "state" / "identifications" / f"{identification_id}.json"
    data = {
        "schemaVersion": 1, "identificationId": identification_id,
        "identifiedAt": datetime.now(timezone.utc).isoformat(),
        "media": "music", "musicRoot": str(root),
        "sourceScan": {"scanId": scan["scanId"], "path": str(path)},
        "summary": {"tracks": len(items), "reviewRequired": sum(i["reviewRequired"] for i in items),
                    "conflicts": sum(i["confidence"] == "CONFLICT" for i in items)},
        "tracks": items, "playlists": scan["playlists"],
        "safety": {"libraryModified": False, "metadataModified": False,
                   "filesRenamed": False, "filesMoved": False, "externalLookupPerformed": False},
    }
    _atomic_json(report, data)
    data["reportPath"] = str(report)
    return data


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return sanitize_component(value)


def _proposed_path(track: dict[str, Any], identity: dict[str, Any]) -> str | None:
    required = ("genre", "albumArtist", "album", "title", "track")
    if any(not identity.get(key) for key in required):
        return None
    original = Path(track["path"])
    evidence = _identity(track)["evidence"]["folder"]
    parts = [_safe_name(identity["genre"]), _safe_name(identity["albumArtist"]),
             _safe_name(identity["album"])]
    if evidence["discFolder"] is not None:
        parts.append(evidence["discFolder"] if evidence["filenameFormat"] == "full" else f"CD {int(identity['disc']):02d}")
    if evidence["filenameFormat"] == "full":
        parts.append(original.name)
    else:
        number = evidence["trackDigits"] if evidence["filenameNumbering"] == "disc-track" else f"{int(identity['track']):02d}"
        parts.append(f"{number} - {_safe_name(identity['title'])}{original.suffix.lower()}")
    return Path(*parts).as_posix()


def plan_music_library(*, identification_report: Path | None = None) -> dict[str, Any]:
    identify_path, identification = _load_report("identifications", identification_report)
    source = identification.get("sourceScan") or {}
    if not source.get("path") or not source.get("scanId"):
        raise MusicLibraryError("Identification is missing scan provenance.")
    scan_path, scan, root, _ = _validated_scan(Path(source["path"]))
    if scan_path != Path(source["path"]).resolve() or scan["scanId"] != source["scanId"]:
        raise MusicLibraryError("Identification source scan ID/path mismatch.")
    if str(root) != str(identification.get("musicRoot")):
        raise MusicLibraryError("Music root differs between scan and identification.")
    by_path = {track["path"]: track for track in scan["tracks"]}
    recorded = identification.get("tracks")
    if not isinstance(recorded, list) or len(recorded) != len(by_path):
        raise MusicLibraryError("Identification track list does not match the scan.")
    planned: list[dict[str, Any]] = []
    destinations: dict[str, list[int]] = {}
    for entry in recorded:
        track = by_path.get(entry.get("path"))
        if track is None:
            raise MusicLibraryError("Identification track differs from source scan.")
        # Recompute from trusted scan evidence instead of blindly trusting edited identities.
        fresh = _identity(track)
        if entry != fresh:
            raise MusicLibraryError("Identification evidence changed; re-run music identification.")
        identity = fresh["identity"]
        destination = _proposed_path(track, identity)
        blocked = list(fresh["conflicts"])
        if fresh["confidence"] in ("CONFLICT", "UNRESOLVED"):
            blocked.append("Identity is conflicting or unresolved.")
        if not identity.get("genre"):
            blocked.append("Canonical genre requires review; no destination inferred.")
        if destination and destination.casefold() != str(track["path"]).casefold():
            target_file = root / Path(destination)
            if target_file.exists() or target_file.is_symlink():
                blocked.append("Proposed destination already exists; no overwrite is allowed.")
        source_file = root / track["path"]
        source_sha = _hash_file(source_file)
        stat = source_file.stat()
        if stat.st_size != track["sizeBytes"] or stat.st_mtime_ns != track["mtimeNs"]:
            raise MusicLibraryError(f"Music file changed during planning: {track['path']}")
        current_path = Path(track["path"]).as_posix()
        needs_rename = destination is not None and destination.casefold() != current_path.casefold()
        proposed_tags = {
            "artist": identity.get("artist"), "albumartist": identity.get("albumArtist"),
            "album": identity.get("album"), "title": identity.get("title"),
            "tracknumber": str(identity["track"]) if identity.get("track") is not None else None,
            "discnumber": str(identity["disc"]) if identity.get("disc") is not None else None,
            "genre": identity.get("genre"), "date": identity.get("date"),
        }
        present = track.get("embedded") or {}
        missing_tags = [key for key, value in proposed_tags.items() if value is not None and not present.get(key)]
        item = {
            "source": current_path, "sourceBytes": track["sizeBytes"], "sourceSha256": source_sha,
            "target": destination, "proposedTags": proposed_tags,
            "missingTags": missing_tags, "tagAction": "WOULD-TAG" if missing_tags else "KEEP",
            "renameAction": "WOULD-RENAME" if needs_rename else "KEEP",
            "status": "BLOCKED" if blocked else ("REVIEW" if fresh["reviewRequired"] else "READY"),
            "blockedReasons": blocked, "reviewRequired": fresh["reviewRequired"],
            "genreCandidates": fresh["genreCandidates"], "confidence": fresh["confidence"],
            "provenance": fresh["provenance"],
        }
        planned.append(item)
        if destination:
            destinations.setdefault(destination.casefold(), []).append(len(planned) - 1)
    for _, indices in destinations.items():
        if len(indices) > 1:
            for i in indices:
                planned[i]["status"] = "BLOCKED"
                planned[i]["blockedReasons"].append("Multiple files have the same proposed destination.")
    plan_id = _now_id("library-plan-music")
    report = runtime_root() / "state" / "plans" / f"{plan_id}.json"
    data = {
        "schemaVersion": 1, "planId": plan_id,
        "plannedAt": datetime.now(timezone.utc).isoformat(),
        "media": "music", "musicRoot": str(root),
        "sourceScan": {"scanId": scan["scanId"], "path": str(scan_path)},
        "sourceIdentification": {"identificationId": identification["identificationId"], "path": str(identify_path)},
        "summary": {"tracks": len(planned), "wouldTag": sum(i["tagAction"] == "WOULD-TAG" for i in planned),
                    "wouldRename": sum(i["renameAction"] == "WOULD-RENAME" for i in planned),
                    "blocked": sum(i["status"] == "BLOCKED" for i in planned),
                    "reviewRequired": sum(i["reviewRequired"] for i in planned)},
        "tracks": planned,
        "playlists": {"preserved": scan["playlists"],
                      "warning": "Playlist paths may require repair if a future apply renames files; this preview never modifies them."},
        "safety": {"libraryModified": False, "metadataModified": False,
                   "filesRenamed": False, "filesMoved": False, "filesOverwritten": False,
                   "sourceHashesRecorded": True, "applyAvailable": False},
    }
    _atomic_json(report, data)
    data["reportPath"] = str(report)
    return data
