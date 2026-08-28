from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "src" / "mnemosyne" / "multifile_metadata.py"
text = path.read_text(encoding="utf-8")

old = '''def _track_tags(report: dict[str, Any], path: Path) -> dict[str, str]:
    tags = _proposed_tags(report)
    try:
        snapshot = read_metadata(path)
        existing = (snapshot.tags.get("title") or [None])[0]
    except MetadataIOError:
        existing = None
    tags["title"] = str(existing).strip() if existing else _fallback_title(path)
    return tags
'''

new = '''def _normalize_chapter_title(value: str) -> str:
    title = re.sub(r"^\\s*\\d+\\s*[-–—:]\\s*", "", value).strip()
    title = re.sub(r"\\s+", " ", title)
    return title or value.strip()


def _track_tags(report: dict[str, Any], path: Path) -> dict[str, str]:
    tags = _proposed_tags(report)
    try:
        snapshot = read_metadata(path)
        existing = (snapshot.tags.get("title") or [None])[0]
    except MetadataIOError:
        existing = None

    if existing:
        tags["title"] = _normalize_chapter_title(str(existing))
    else:
        tags["title"] = _normalize_chapter_title(_fallback_title(path))

    return tags
'''

if new not in text:
    if old not in text:
        raise SystemExit("Could not find _track_tags() source block.")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Patched chapter-title normalization.")
