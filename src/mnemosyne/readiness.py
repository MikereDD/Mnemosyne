from __future__ import annotations
import hashlib,json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .inspection import _proposed_tags
from .metadata_io import MetadataIOError, verify_metadata
from .quality import ActualAudioQuality, inspect_actual_quality

class ReadinessError(RuntimeError): pass
@dataclass(frozen=True)
class ReadinessCheck: name:str; passed:bool; detail:str
@dataclass(frozen=True)
class ReadinessResult:
    job_dir:Path;ready:bool;audio_path:Path;cover_path:Path|None;checks:tuple[ReadinessCheck,...]
    actual_quality:ActualAudioQuality;audio_sha256:str;cover_sha256:str|None;report_path:Path;readiness_report_path:Path

def _read_json(p):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:raise ReadinessError(f"Could not read JSON report {p}: {exc}") from exc
def _sha(p):
    d=hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda:f.read(1024*1024),b""):d.update(c)
    return d.hexdigest()
def _audio(job,r):
    a=r.get("audio") or {}
    if a.get("stagedPath") and Path(str(a["stagedPath"])).is_file():return Path(str(a["stagedPath"]))
    if a.get("canonicalStagedName") and (job/str(a["canonicalStagedName"])).is_file():return job/str(a["canonicalStagedName"])
    raise ReadinessError("Could not resolve the staged canonical audio file.")
def _cover(job,r):
    c=r.get("cover") or {}
    if c.get("stagedPath") and Path(str(c["stagedPath"])).is_file():return Path(str(c["stagedPath"]))
    if c.get("canonicalStagedName") and (job/str(c["canonicalStagedName"])).is_file():return job/str(c["canonicalStagedName"])
    for n in ("cover.jpg","cover.jpeg","cover.png","cover.webp"):
        p=job/n
        if p.is_file():return p
    return None

def _metadata_checks(path,tags,cover_sha):
    try:s=verify_metadata(path,tags,expected_cover_sha256=cover_sha)
    except MetadataIOError as exc:return [ReadinessCheck("metadata-container-readable",False,str(exc))]
    checks=[ReadinessCheck("metadata-container-readable",True,f"Metadata family {s.family} reopened successfully.")]
    for k,e in tags.items():
        vals=s.tags.get(k);a=vals[0] if vals else None
        checks.append(ReadinessCheck(f"metadata-{k}",a==e,f"{k}={a!r}" if a==e else f"Expected {k}={e!r}, found {a!r}."))
    hashes={hashlib.sha256(d).hexdigest() for _,d in s.artwork}
    checks.append(ReadinessCheck("embedded-cover",cover_sha in hashes if cover_sha else bool(s.artwork),
        f"Embedded artwork SHA-256 verified: {cover_sha}" if cover_sha and cover_sha in hashes else ("Embedded artwork is present." if not cover_sha and s.artwork else "Embedded artwork verification failed.")))
    return checks

def verify_staged_readiness(job_dir:Path):
    job=job_dir.resolve(); rp=job/"fetch-report.json"
    if not job.is_dir():raise ReadinessError(f"Staging job directory does not exist: {job}")
    if not rp.is_file():raise ReadinessError(f"fetch-report.json not found: {rp}")
    r=_read_json(rp); audio=_audio(job,r); cover=_cover(job,r); checks=[]
    warnings=r.get("warnings") or [];checks.append(ReadinessCheck("warnings-cleared",not warnings,"No unresolved staging warnings." if not warnings else f"Unresolved warnings: {warnings}"))
    sr=r.get("sourceResolution") or {};resolved=sr.get("status")=="resolved-by-actual-comparison"
    checks.append(ReadinessCheck("source-resolved",resolved,"Source quality decision was resolved by actual candidate comparison." if resolved else "Source quality decision has not been formally resolved."))
    ash=_sha(audio); recorded=str((r.get("audio") or {}).get("sha256") or "")
    checks.append(ReadinessCheck("audio-sha256",bool(recorded) and ash==recorded,f"Audio SHA-256 verified: {ash}" if recorded and ash==recorded else f"Audio SHA-256 mismatch or missing report value; actual={ash}."))
    meta=r.get("metadataNormalization") or {}; mv=meta.get("status")=="verified"
    checks.append(ReadinessCheck("metadata-normalization",mv,f"Metadata normalization verified for {meta.get('metadataFamily') or 'recorded format'}." if mv else "Metadata normalization has not reached verified state."))
    csha=None
    if cover:
        csha=_sha(cover); rec=str((r.get("cover") or {}).get("sha256") or "")
        checks.append(ReadinessCheck("standalone-cover-sha256",bool(rec) and csha==rec,f"Standalone cover SHA-256 verified: {csha}" if rec and csha==rec else f"Standalone cover SHA-256 mismatch or missing report value; actual={csha}."))
    else:checks.append(ReadinessCheck("standalone-cover-sha256",False,"Canonical standalone cover is missing."))
    checks.extend(_metadata_checks(audio,_proposed_tags(r),meta.get("embeddedCoverSha256")))
    q=inspect_actual_quality(audio); known=q.codec is not None and q.lossless is not None
    checks.append(ReadinessCheck("actual-codec-known",known,f"Actual codec={q.codec}, quality={'lossless' if q.lossless else 'lossy'}." if known else "Actual codec/quality classification is incomplete."))
    dest=r.get("plannedDestination");checks.append(ReadinessCheck("planned-destination",bool(dest),f"Planned destination recorded: {dest}" if dest else "Planned final destination is missing."))
    modified=bool(r.get("finalLibraryModified"));checks.append(ReadinessCheck("final-library-untouched",not modified,"Final library is still untouched." if not modified else "Report indicates the final library has already been modified."))
    ready=all(c.passed for c in checks); out={"schemaVersion":2,"jobId":r.get("jobId"),"status":"ready-for-placement" if ready else "not-ready",
      "audioPath":str(audio),"audioSha256":ash,"coverPath":str(cover) if cover else None,"coverSha256":csha,
      "metadataFamily":(r.get("audio") or {}).get("metadataFamily"),"actualCodec":q.codec,"actualLossless":q.lossless,
      "checks":[{"name":c.name,"passed":c.passed,"detail":c.detail} for c in checks],"plannedDestination":dest,"finalLibraryModified":modified}
    op=job/"readiness-report.json";tmp=job/".readiness-report.json.tmp";tmp.write_text(json.dumps(out,indent=2,ensure_ascii=False)+"\n",encoding="utf-8");_read_json(tmp);tmp.replace(op)
    return ReadinessResult(job,ready,audio,cover,tuple(checks),q,ash,csha,rp,op)
