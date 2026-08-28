from pathlib import Path

path = Path("pyproject.toml")
text = path.read_text(encoding="utf-8")

if 'license = "GPL-3.0-or-later"' not in text:
    anchor = 'readme = "README.md"\n'
    if anchor not in text:
        raise SystemExit("Could not find pyproject readme metadata.")
    text = text.replace(
        anchor,
        anchor + 'license = "GPL-3.0-or-later"\n',
        1,
    )

path.write_text(text, encoding="utf-8")
print("Added GPL-3.0-or-later package metadata.")
