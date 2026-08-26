from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from mutagen import File as MutagenFile
from .config import runtime_root
from .metadata_io import MetadataIOError, read_metadata

class InspectionError(RuntimeError): pass
@dataclass(frozen=True)
class ChapterInfo: index:int; title:str; start_seconds:float
@dataclass(frozen=True)
class AudioProperties:
    container:str; codec:str|None; duration_seconds:float|None; bitrate_bps:int|None; sample_rate_hz:int|None; channels:int|None
@dataclass(frozen=True)
class MetadataInspection:
    job_dir:Path; audio_path:Path; properties:AudioProperties; existing_tags:dict[str,list[str]]
    embedded_artwork_count:int; embedded_artwork_formats:list[str]; chapters:list[ChapterInfo]
    proposed_tags:dict[str,str]; changes:list[tuple[str,str|None,str]]; report:dict[str,Any]=field(repr=False)

def latest_staging_job(staging_root:Path|None=None):
    root=staging_root or (runtime_root()/"staging")
    if not root.exists(): raise InspectionError(f"Staging root does not exist: {root}")
    c=[p for p in root.iterdir() if p.is_dir() and (p/"fetch-report.json").is_file()]
    if not c: raise InspectionError(f"No completed staging jobs found under: {root}")
    return max(c,key=lambda p:(p/"fetch-report.json").stat().st_mtime)

def _read_report(job_dir):
    p=job_dir/"fetch-report.json"
    if not p.is_file(): raise InspectionError(f"Staging report not found: {p}")
    try:return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc: raise InspectionError(f"Could not read staging report: {exc}") from exc

def _resolve_audio_path(job_dir,report):
    a=report.get("audio") or {}
    if a.get("stagedPath") and Path(str(a["stagedPath"])).is_file(): return Path(str(a["stagedPath"]))
    if a.get("canonicalStagedName") and (job_dir/str(a["canonicalStagedName"])).is_file(): return job_dir/str(a["canonicalStagedName"])
    supported={".m4a",".m4b",".mp3",".flac",".ogg",".opus",".wav",".aac"}
    m=[p for p in job_dir.iterdir() if p.is_file() and p.suffix.lower() in supported]
    if len(m)==1:return m[0]
    raise InspectionError("Could not uniquely resolve the staged audio file.")

def _chapters(audio):
    data=getattr(audio,"chapters",None)
    if not data:return []
    out=[]
    for i,ch in enumerate(data,1):
        try:start=float(getattr(ch,"start",0) or 0)
        except: start=0.0
        out.append(ChapterInfo(i,str(getattr(ch,"title",None) or f"Chapter {i}"),start))
    return out

def _properties(audio,path):
    info=getattr(audio,"info",None)
    if info is None:return AudioProperties(path.suffix.lstrip(".").upper(),None,None,None,None,None)
    codec=getattr(info,"codec",None) or getattr(info,"codec_description",None)
    return AudioProperties(type(audio).__name__,str(codec) if codec else None,
        float(info.length) if getattr(info,"length",None) is not None else None,
        int(info.bitrate) if getattr(info,"bitrate",None) is not None else None,
        int(info.sample_rate) if getattr(info,"sample_rate",None) is not None else None,
        int(info.channels) if getattr(info,"channels",None) is not None else None)

def _first(existing,key):
    v=existing.get(key); return v[0] if v else None

def _proposed_tags(report):
    m=report.get("media") or {}; t=str(m.get("title") or "").strip(); c=str(m.get("creator") or "").strip(); y=m.get("year")
    p={}
    if t:p["title"]=t
    if c:p["artist"]=c;p["album_artist"]=c
    if t:p["album"]=t
    if y:p["date"]=str(y)
    if str(m.get("type") or "")=="audiobook":p["genre"]="Audiobook"
    return p

def inspect_staging_job(job_dir:Path):
    job_dir=job_dir.resolve()
    if not job_dir.is_dir():raise InspectionError(f"Staging job directory does not exist: {job_dir}")
    report=_read_report(job_dir); audio_path=_resolve_audio_path(job_dir,report)
    try: audio=MutagenFile(audio_path)
    except Exception as exc: raise InspectionError(f"Mutagen could not inspect the audio file: {exc}") from exc
    if audio is None: raise InspectionError(f"Unsupported or unrecognized audio container: {audio_path}")
    try:
        snap=read_metadata(audio_path); existing=snap.tags; count=len(snap.artwork); formats=[m for m,_ in snap.artwork]
    except MetadataIOError:
        existing={};count=0;formats=[]
    proposed=_proposed_tags(report); changes=[]
    for key,new in proposed.items():
        cur=_first(existing,key)
        if cur!=new:changes.append((key,cur,new))
    return MetadataInspection(job_dir,audio_path,_properties(audio,audio_path),existing,count,formats,_chapters(audio),proposed,changes,report)
