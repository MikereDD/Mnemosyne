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
    container:str
    codec:str|None
    duration_seconds:float|None
    bitrate_bps:int|None
    sample_rate_hz:int|None
    channels:int|None
    bits_per_sample:int|None
@dataclass(frozen=True)
class MetadataInspection:
    job_dir:Path; audio_path:Path; properties:AudioProperties; existing_tags:dict[str,list[str]]
    embedded_artwork_count:int; embedded_artwork_formats:list[str]; chapters:list[ChapterInfo]
    proposed_tags:dict[str,str]; changes:list[tuple[str,str|None,str]]; report:dict[str,Any]=field(repr=False)

@dataclass(frozen=True)
class MultiFileInspectionEntry:
    index:int
    audio_path:Path
    properties:AudioProperties
    existing_tags:dict[str,list[str]]
    embedded_artwork_count:int
    embedded_artwork_formats:list[str]
    proposed_tags:dict[str,str]
    changes:list[tuple[str,str|None,str]]

@dataclass(frozen=True)
class MultiFileInspection:
    job_dir:Path
    entries:tuple[MultiFileInspectionEntry,...]
    total_duration_seconds:float|None
    codecs:tuple[str,...]
    sample_rates_hz:tuple[int,...]
    channels:tuple[int,...]
    bits_per_sample:tuple[int,...]
    report:dict[str,Any]=field(repr=False)

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
    if info is None:
        return AudioProperties(
            path.suffix.lstrip(".").upper(),
            None,None,None,None,None,None,
        )
    codec=getattr(info,"codec",None) or getattr(info,"codec_description",None)
    if codec is None and path.suffix.lower()==".flac":
        codec="FLAC"
    return AudioProperties(
        type(audio).__name__,
        str(codec) if codec else None,
        float(info.length) if getattr(info,"length",None) is not None else None,
        int(info.bitrate) if getattr(info,"bitrate",None) is not None else None,
        int(info.sample_rate) if getattr(info,"sample_rate",None) is not None else None,
        int(info.channels) if getattr(info,"channels",None) is not None else None,
        int(info.bits_per_sample) if getattr(info,"bits_per_sample",None) is not None else None,
    )

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

def _resolve_multifile_audio_paths(job_dir:Path,report:dict[str,Any])->list[Path]:
    audio=report.get("audio") or {}
    entries=list(audio.get("files") or [])
    expected=int(audio.get("fileCount") or 0)
    if not entries:
        raise InspectionError("Multi-file fetch report contains no staged audio entries.")
    if expected and expected!=len(entries):
        raise InspectionError(
            f"Multi-file report count mismatch: expected {expected}, found {len(entries)}."
        )

    paths:list[Path]=[]
    for index,entry in enumerate(entries,1):
        staged=entry.get("stagedPath")
        if staged:
            path=Path(str(staged))
        else:
            name=entry.get("canonicalStagedName")
            if not name:
                raise InspectionError(
                    f"Multi-file audio entry {index} has no staged path or canonical name."
                )
            path=job_dir/"audio"/str(name)
        if not path.is_file():
            raise InspectionError(f"Staged audio file {index} is missing: {path}")
        paths.append(path)

    if len(set(paths))!=len(paths):
        raise InspectionError("Multi-file fetch report resolves duplicate staged audio paths.")
    return paths


def _inspect_one_audio(path:Path,report:dict[str,Any])->MultiFileInspectionEntry:
    try:
        audio=MutagenFile(path)
    except Exception as exc:
        raise InspectionError(f"Mutagen could not inspect {path.name}: {exc}") from exc
    if audio is None:
        raise InspectionError(f"Unsupported or unrecognized audio container: {path}")

    try:
        snap=read_metadata(path)
        existing=snap.tags
        count=len(snap.artwork)
        formats=[m for m,_ in snap.artwork]
    except MetadataIOError:
        existing={}
        count=0
        formats=[]

    proposed=_proposed_tags(report)
    changes=[]
    for key,new in proposed.items():
        cur=_first(existing,key)
        if cur!=new:
            changes.append((key,cur,new))

    return MultiFileInspectionEntry(
        index=0,
        audio_path=path,
        properties=_properties(audio,path),
        existing_tags=existing,
        embedded_artwork_count=count,
        embedded_artwork_formats=formats,
        proposed_tags=proposed,
        changes=changes,
    )


def inspect_multifile_staging_job(job_dir:Path)->MultiFileInspection:
    job_dir=job_dir.resolve()
    if not job_dir.is_dir():
        raise InspectionError(f"Staging job directory does not exist: {job_dir}")

    report=_read_report(job_dir)
    paths=_resolve_multifile_audio_paths(job_dir,report)

    entries=[]
    for index,path in enumerate(paths,1):
        inspected=_inspect_one_audio(path,report)
        entries.append(MultiFileInspectionEntry(
            index=index,
            audio_path=inspected.audio_path,
            properties=inspected.properties,
            existing_tags=inspected.existing_tags,
            embedded_artwork_count=inspected.embedded_artwork_count,
            embedded_artwork_formats=inspected.embedded_artwork_formats,
            proposed_tags=inspected.proposed_tags,
            changes=inspected.changes,
        ))

    durations=[e.properties.duration_seconds for e in entries]
    total_duration=(
        sum(float(value) for value in durations if value is not None)
        if all(value is not None for value in durations)
        else None
    )
    codecs=tuple(sorted({e.properties.codec or "?" for e in entries}))
    sample_rates=tuple(sorted({e.properties.sample_rate_hz for e in entries if e.properties.sample_rate_hz is not None}))
    channels=tuple(sorted({e.properties.channels for e in entries if e.properties.channels is not None}))
    bit_depths=tuple(sorted({e.properties.bits_per_sample for e in entries if e.properties.bits_per_sample is not None}))

    return MultiFileInspection(
        job_dir=job_dir,
        entries=tuple(entries),
        total_duration_seconds=total_duration,
        codecs=codecs,
        sample_rates_hz=sample_rates,
        channels=channels,
        bits_per_sample=bit_depths,
        report=report,
    )

