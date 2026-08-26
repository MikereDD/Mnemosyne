from __future__ import annotations
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, TALB, TCON, TDRC, TIT2, TPE1, TPE2
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, MP4Cover

class MetadataIOError(RuntimeError):
    """Format-specific metadata I/O failed."""

@dataclass(frozen=True)
class MetadataWriteEvidence:
    family: str
    embedded_cover: bool
    embedded_cover_sha256: str | None

@dataclass(frozen=True)
class MetadataSnapshot:
    family: str
    tags: dict[str, list[str]]
    artwork: tuple[tuple[str, bytes], ...]

_MP4_TAG_MAP = {"title":"\xa9nam","artist":"\xa9ART","album_artist":"aART","album":"\xa9alb","date":"\xa9day","genre":"\xa9gen"}
_ID3_FRAME_MAP = {"title":TIT2,"artist":TPE1,"album_artist":TPE2,"album":TALB,"date":TDRC,"genre":TCON}
_FLAC_TAG_MAP = {"title":"title","artist":"artist","album_artist":"albumartist","album":"album","date":"date","genre":"genre"}

def metadata_family(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".m4a",".m4b",".mp4"}: return "mp4"
    if suffix == ".mp3": return "id3"
    if suffix == ".flac": return "flac"
    raise MetadataIOError(f"Metadata normalization is not implemented for {suffix or 'unknown'} audio.")

def cover_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg",".jpeg"}: return "image/jpeg"
    if suffix == ".png": return "image/png"
    if suffix == ".webp": return "image/webp"
    raise MetadataIOError(f"Unsupported cover format for embedding: {suffix or 'unknown'}.")

def _cover_sha(path: Path | None):
    if path is None: return None, None, None
    data = path.read_bytes()
    if not data: raise MetadataIOError(f"Cover file is empty: {path}")
    return data, hashlib.sha256(data).hexdigest(), cover_mime(path)

def _mp4_cover_format(mime: str) -> int:
    if mime == "image/jpeg": return MP4Cover.FORMAT_JPEG
    if mime == "image/png": return MP4Cover.FORMAT_PNG
    raise MetadataIOError("MP4-family cover embedding supports JPEG/PNG only.")

def _write_mp4(path: Path, tags: dict[str,str], cover_path: Path|None):
    try: audio = MP4(path)
    except Exception as exc: raise MetadataIOError(f"Could not open MP4-family audio: {exc}") from exc
    if audio.tags is None: audio.add_tags()
    assert audio.tags is not None
    for friendly,value in tags.items():
        atom = _MP4_TAG_MAP.get(friendly)
        if atom is not None: audio.tags[atom] = [value]
    data, sha, mime = _cover_sha(cover_path)
    if data is not None and mime is not None:
        audio.tags["covr"] = [MP4Cover(data, imageformat=_mp4_cover_format(mime))]
    try: audio.save()
    except Exception as exc: raise MetadataIOError(f"Could not save MP4-family metadata: {exc}") from exc
    return MetadataWriteEvidence("mp4", data is not None, sha)

def _replace_id3_front_cover(tags: Any, data: bytes, mime: str):
    preserved = [f for f in tags.getall("APIC") if getattr(f,"type",None) != 3]
    tags.delall("APIC")
    for frame in preserved: tags.add(frame)
    tags.add(APIC(encoding=3,mime=mime,type=3,desc="Cover",data=data))

def _write_id3(path: Path, tags: dict[str,str], cover_path: Path|None):
    try: audio = MP3(path)
    except Exception as exc: raise MetadataIOError(f"Could not open MP3 audio: {exc}") from exc
    if audio.tags is None: audio.add_tags()
    assert audio.tags is not None
    for friendly,value in tags.items():
        frame_type = _ID3_FRAME_MAP.get(friendly)
        if frame_type is None: continue
        frame_id = frame_type.__name__
        audio.tags.delall(frame_id)
        audio.tags.add(frame_type(encoding=3,text=[value]))
    data, sha, mime = _cover_sha(cover_path)
    if data is not None and mime is not None: _replace_id3_front_cover(audio.tags,data,mime)
    try: audio.save(v2_version=4)
    except Exception as exc: raise MetadataIOError(f"Could not save ID3 metadata: {exc}") from exc
    return MetadataWriteEvidence("id3", data is not None, sha)

def _replace_flac_front_cover(audio: FLAC, data: bytes, mime: str):
    preserved = [p for p in audio.pictures if getattr(p,"type",None) != 3]
    audio.clear_pictures()
    for picture in preserved: audio.add_picture(picture)
    front = Picture(); front.type=3; front.mime=mime; front.desc="Cover"; front.data=data
    audio.add_picture(front)

def _write_flac(path: Path, tags: dict[str,str], cover_path: Path|None):
    try: audio = FLAC(path)
    except Exception as exc: raise MetadataIOError(f"Could not open FLAC audio: {exc}") from exc
    for friendly,value in tags.items():
        key = _FLAC_TAG_MAP.get(friendly)
        if key is not None: audio[key] = [value]
    data, sha, mime = _cover_sha(cover_path)
    if data is not None and mime is not None: _replace_flac_front_cover(audio,data,mime)
    try: audio.save()
    except Exception as exc: raise MetadataIOError(f"Could not save FLAC metadata: {exc}") from exc
    return MetadataWriteEvidence("flac", data is not None, sha)

def write_metadata(path: Path, tags: dict[str,str], cover_path: Path|None):
    family = metadata_family(path)
    if family=="mp4": return _write_mp4(path,tags,cover_path)
    if family=="id3": return _write_id3(path,tags,cover_path)
    if family=="flac": return _write_flac(path,tags,cover_path)
    raise MetadataIOError(f"Unsupported metadata family: {family}")

def _text_list(value: Any) -> list[str]:
    if value is None: return []
    if isinstance(value,(list,tuple)): return [str(i) for i in value]
    return [str(value)]

def _snapshot_mp4(path: Path):
    audio=MP4(path); tags=audio.tags or {}; friendly={}
    for name,atom in _MP4_TAG_MAP.items():
        vals=tags.get(atom)
        if vals: friendly[name]=_text_list(vals)
    for key,value in tags.items():
        if key=="covr" or key in _MP4_TAG_MAP.values(): continue
        friendly[key]=_text_list(value)
    artwork=[]
    for c in tags.get("covr") or []:
        mime="image/jpeg" if c.imageformat==MP4Cover.FORMAT_JPEG else ("image/png" if c.imageformat==MP4Cover.FORMAT_PNG else "application/octet-stream")
        artwork.append((mime,bytes(c)))
    return MetadataSnapshot("mp4",friendly,tuple(artwork))

def _first_id3_text(tags: Any, frame_id: str):
    frames=tags.getall(frame_id)
    if not frames: return []
    return _text_list(getattr(frames[0],"text",None))

def _snapshot_id3(path: Path):
    audio=MP3(path); tags=audio.tags; friendly={}
    artwork=()
    if tags is not None:
        for name,ft in _ID3_FRAME_MAP.items():
            vals=_first_id3_text(tags,ft.__name__)
            if vals: friendly[name]=vals
        canonical={f.__name__ for f in _ID3_FRAME_MAP.values()}
        for frame in tags.values():
            fid=getattr(frame,"FrameID",type(frame).__name__)
            if fid in canonical or fid=="APIC": continue
            friendly.setdefault(fid,[]).append(str(frame))
        artwork=tuple((str(getattr(f,"mime","") or "application/octet-stream"),bytes(f.data)) for f in tags.getall("APIC"))
    return MetadataSnapshot("id3",friendly,artwork)

def _snapshot_flac(path: Path):
    audio=FLAC(path); friendly={}; reverse={v:k for k,v in _FLAC_TAG_MAP.items()}
    if audio.tags:
        for key,values in audio.tags.items():
            friendly[reverse.get(key.lower(),key)] = _text_list(values)
    artwork=tuple((str(p.mime or "application/octet-stream"),bytes(p.data)) for p in audio.pictures)
    return MetadataSnapshot("flac",friendly,artwork)

def read_metadata(path: Path):
    family=metadata_family(path)
    try:
        if family=="mp4": return _snapshot_mp4(path)
        if family=="id3": return _snapshot_id3(path)
        if family=="flac": return _snapshot_flac(path)
    except Exception as exc:
        raise MetadataIOError(f"Could not read {family} metadata from {path.name}: {exc}") from exc
    raise MetadataIOError(f"Unsupported metadata family: {family}")

def verify_metadata(path: Path, expected_tags: dict[str,str], *, expected_cover_sha256: str|None):
    snap=read_metadata(path)
    for key,expected in expected_tags.items():
        vals=snap.tags.get(key); actual=vals[0] if vals else None
        if actual != expected:
            raise MetadataIOError(f"Post-write verification failed for {key}: expected {expected!r}, found {actual!r}.")
    if expected_cover_sha256 is not None:
        if not snap.artwork: raise MetadataIOError("Post-write verification failed: embedded artwork is missing.")
        hashes={hashlib.sha256(data).hexdigest() for _,data in snap.artwork}
        if expected_cover_sha256 not in hashes:
            raise MetadataIOError("Post-write verification failed: embedded artwork SHA-256 mismatch.")
    return snap
