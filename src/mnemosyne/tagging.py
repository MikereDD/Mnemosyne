from __future__ import annotations
import hashlib, json, os, shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from .inspection import _proposed_tags
from .metadata_io import MetadataIOError, _mp4_cover_format, cover_mime, metadata_family, verify_metadata, write_metadata

class TaggingError(RuntimeError): pass

@dataclass(frozen=True)
class TaggingPreview:
    job_dir: Path; audio_path: Path; cover_path: Path|None
    proposed_tags: dict[str,str]; metadata_family: str

@dataclass(frozen=True)
class TaggingResult:
    job_dir: Path; audio_path: Path; rollback_path: Path; report_path: Path
    pre_tag_sha256: str; post_tag_sha256: str; written_tags: dict[str,str]
    embedded_cover: bool; embedded_cover_sha256: str|None; metadata_family: str

def _cover_format(path: Path) -> int:
    """Backward-compatible MP4 cover-format helper used by existing tests."""
    try:
        return _mp4_cover_format(cover_mime(path))
    except MetadataIOError as exc:
        raise TaggingError(str(exc)) from exc

def _read_json(path):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as exc: raise TaggingError(f"Could not read JSON report {path}: {exc}") from exc

def _sha256(path):
    d=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): d.update(chunk)
    return d.hexdigest()

def _resolve_audio(job_dir,report):
    a=report.get("audio") or {}
    if a.get("stagedPath") and Path(str(a["stagedPath"])).is_file(): return Path(str(a["stagedPath"]))
    if a.get("canonicalStagedName") and (job_dir/str(a["canonicalStagedName"])).is_file(): return job_dir/str(a["canonicalStagedName"])
    raise TaggingError("Could not resolve the staged canonical audio file.")

def _resolve_cover(job_dir,report):
    c=report.get("cover") or {}
    if c.get("stagedPath") and Path(str(c["stagedPath"])).is_file(): return Path(str(c["stagedPath"]))
    if c.get("canonicalStagedName") and (job_dir/str(c["canonicalStagedName"])).is_file(): return job_dir/str(c["canonicalStagedName"])
    for name in ("cover.jpg","cover.jpeg","cover.png","cover.webp"):
        p=job_dir/name
        if p.is_file(): return p
    return None

def preview_metadata_normalization(job_dir: Path):
    job_dir=job_dir.resolve()
    if not job_dir.is_dir(): raise TaggingError(f"Staging job directory does not exist: {job_dir}")
    report_path=job_dir/"fetch-report.json"
    if not report_path.is_file(): raise TaggingError(f"fetch-report.json not found: {report_path}")
    report=_read_json(report_path)
    if report.get("warnings") or []: raise TaggingError("Staging job still has unresolved warnings; metadata mutation is blocked.")
    audio=_resolve_audio(job_dir,report)
    try: family=metadata_family(audio)
    except MetadataIOError as exc: raise TaggingError(str(exc)) from exc
    proposed=_proposed_tags(report)
    if not proposed: raise TaggingError("No canonical metadata could be derived from the staging report.")
    return TaggingPreview(job_dir,audio,_resolve_cover(job_dir,report),proposed,family)

def apply_metadata_normalization(job_dir: Path):
    preview=preview_metadata_normalization(job_dir); job_dir=preview.job_dir; audio=preview.audio_path
    report_path=job_dir/"fetch-report.json"; report=_read_json(report_path); pre=_sha256(audio)
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"); rollback=job_dir/"rollback"; rollback.mkdir(exist_ok=True)
    rollback_audio=rollback/f"{stamp}-pre-metadata-{audio.name}"
    rollback_report=rollback/f"{stamp}-pre-metadata-fetch-report.json"
    work=job_dir/f".metadata-{stamp}-{audio.name}"
    if rollback_audio.exists() or rollback_report.exists() or work.exists(): raise TaggingError("Metadata transaction path collision.")
    shutil.copy2(audio,work)
    try:
        evidence=write_metadata(work,preview.proposed_tags,preview.cover_path)
        verify_metadata(work,preview.proposed_tags,expected_cover_sha256=evidence.embedded_cover_sha256)
        post=_sha256(work)
        if post==pre: raise TaggingError("Metadata operation produced an identical file; refusing to claim mutation.")
        shutil.copy2(audio,rollback_audio); shutil.copy2(report_path,rollback_report)
        os.replace(work,audio)
        verify_metadata(audio,preview.proposed_tags,expected_cover_sha256=evidence.embedded_cover_sha256)
        canonical=_sha256(audio)
        if canonical!=post: raise TaggingError("Canonical staged audio hash changed during metadata replacement.")
        event={"normalizedAt":datetime.now(timezone.utc).isoformat(),"audioPath":str(audio),"metadataFamily":preview.metadata_family,
               "preTagSha256":pre,"postTagSha256":canonical,"rollbackAudio":str(rollback_audio),"rollbackReport":str(rollback_report),
               "writtenTags":dict(preview.proposed_tags),"embeddedCover":evidence.embedded_cover,
               "embeddedCoverSource":str(preview.cover_path) if preview.cover_path else None,
               "embeddedCoverSha256":evidence.embedded_cover_sha256,"verification":"passed"}
        report.setdefault("metadataNormalizationHistory",[]).append(event)
        ar=report.setdefault("audio",{}); ar.update({"sha256":canonical,"metadataFamily":preview.metadata_family,
            "metadataNormalized":True,"metadataVerification":"passed","embeddedArtwork":evidence.embedded_cover,
            "embeddedArtworkSha256":evidence.embedded_cover_sha256})
        report["schemaVersion"]=max(int(report.get("schemaVersion") or 0),8); report["status"]="staged-metadata-normalized"
        report["metadataNormalization"]={"status":"verified","metadataFamily":preview.metadata_family,
            "writtenTags":dict(preview.proposed_tags),"embeddedCover":evidence.embedded_cover,
            "embeddedCoverSha256":evidence.embedded_cover_sha256,"preTagSha256":pre,"postTagSha256":canonical,
            "rollbackAudio":str(rollback_audio),"rollbackReport":str(rollback_report)}
        report["finalLibraryModified"]=False
        tmp=job_dir/".fetch-report.metadata.tmp"; tmp.write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
        _read_json(tmp); os.replace(tmp,report_path)
        return TaggingResult(job_dir,audio,rollback_audio,report_path,pre,canonical,dict(preview.proposed_tags),
                             evidence.embedded_cover,evidence.embedded_cover_sha256,preview.metadata_family)
    except Exception as exc:
        work.unlink(missing_ok=True)
        if rollback_audio.exists():
            try: shutil.copy2(rollback_audio,audio)
            except OSError: pass
        if rollback_report.exists():
            try: shutil.copy2(rollback_report,report_path)
            except OSError: pass
        if isinstance(exc,TaggingError): raise
        raise TaggingError(str(exc)) from exc
