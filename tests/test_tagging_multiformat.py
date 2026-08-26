import json
from pathlib import Path
import pytest
from mnemosyne.tagging import TaggingError,preview_metadata_normalization

@pytest.mark.parametrize(("suffix","family"),[(".m4a","mp4"),(".mp3","id3"),(".flac","flac")])
def test_preview_accepts_supported_metadata_families(tmp_path,suffix,family):
    job=tmp_path/"job";job.mkdir();audio=job/f"Book - Author (2000){suffix}";audio.write_bytes(b"fixture")
    (job/"fetch-report.json").write_text(json.dumps({"media":{"type":"audiobook","title":"Book","creator":"Author","year":2000},
      "audio":{"stagedPath":str(audio),"canonicalStagedName":audio.name},"warnings":[]}),encoding="utf-8")
    assert preview_metadata_normalization(job).metadata_family==family
def test_preview_rejects_unsupported_metadata_family(tmp_path):
    job=tmp_path/"job";job.mkdir();audio=job/"Book.ogg";audio.write_bytes(b"fixture")
    (job/"fetch-report.json").write_text(json.dumps({"media":{"type":"audiobook","title":"Book","creator":"Author"},
      "audio":{"stagedPath":str(audio),"canonicalStagedName":audio.name},"warnings":[]}),encoding="utf-8")
    with pytest.raises(TaggingError,match="not implemented"):preview_metadata_normalization(job)
