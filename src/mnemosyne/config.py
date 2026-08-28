from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel
from tomlkit import dumps, parse

from .paths import expand_config_path


class LibraryConfig(BaseModel):
    root: str = r"$HOME\Downloads\Mnemosyne"
    audiobooks: str = "Audiobooks"
    ebooks: str = "eBooks"
    music: str = "Music"


class CoversConfig(BaseModel):
    enabled: bool = True
    filename: str = "cover.jpg"
    embed_when_supported: bool = True


class SafetyConfig(BaseModel):
    confirm_before_apply: bool = True
    overwrite_existing: bool = False
    use_staging: bool = True


class MnemosyneConfig(BaseModel):
    library: LibraryConfig = LibraryConfig()
    covers: CoversConfig = CoversConfig()
    safety: SafetyConfig = SafetyConfig()

    @property
    def library_root(self) -> Path:
        return expand_config_path(self.library.root)


def runtime_root() -> Path:
    return Path.home() / "Mnemosyne"


def config_path() -> Path:
    return runtime_root() / "config" / "config.toml"


def _default_toml() -> str:
    return """# Mnemosyne live configuration.
# This file is safe to edit by hand.

[library]
root = "$HOME\\\\Downloads\\\\Mnemosyne"
audiobooks = "Audiobooks"
ebooks = "eBooks"
music = "Music"

[covers]
enabled = true
filename = "cover.jpg"
embed_when_supported = true

[safety]
confirm_before_apply = true
overwrite_existing = false
use_staging = true
"""


def initialize_runtime() -> list[Path]:
    root = runtime_root()
    directories = [
        root / "config",
        root / "fetch",
        root / "logs",
        root / "state",
        root / "staging",
        root / "cache",
    ]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []
    cfg = config_path()
    if not cfg.exists():
        cfg.write_text(_default_toml(), encoding="utf-8")
        created.append(cfg)

    queue_files = [
        root / "fetch" / "audiobook-links.txt",
        root / "fetch" / "ebook-links.txt",
        root / "fetch" / "music-links.txt",
    ]
    for queue in queue_files:
        if not queue.exists():
            queue.write_text(
                "# One URL per line. Blank lines and comments are ignored.\n",
                encoding="utf-8",
            )
            created.append(queue)

    return created


def load_config() -> MnemosyneConfig:
    initialize_runtime()
    document = parse(config_path().read_text(encoding="utf-8"))
    return MnemosyneConfig.model_validate(document.unwrap())
