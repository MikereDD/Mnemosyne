from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

import typer
from rich.table import Table
from rich.text import Text

from .album_library import AlbumFilenameFormat, AlbumLibraryError, apply_album, preview_album
from .config import runtime_root
from .providers.musicbrainz import MusicBrainzError, fetch_album
from .render import console


def album_command(
    album_dir: Annotated[
        Path | None,
        typer.Argument(help="Existing album folder. Omit to enter its path interactively."),
    ] = None,
    source_url: Annotated[
        str | None,
        typer.Option("--source-url", help="MusicBrainz release page URL for the exact album edition."),
    ] = None,
    filename_format: Annotated[
        AlbumFilenameFormat,
        typer.Option("--filename-format", help="Filename layout: full = track - title - album - author - (year); track-title = track - title."),
    ] = AlbumFilenameFormat.FULL,
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Apply the displayed rename/tag plan after confirmation, keeping original backups."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="With --apply, confirm the displayed plan without an interactive question."),
    ] = False,
    backup_root: Annotated[
        Path | None,
        typer.Option("--backup-root", help="Backup/report directory outside the album. Default: Mnemosyne/state/album-transactions."),
    ] = None,
) -> None:
    """Rename and tag an existing album from MusicBrainz; preview by default.

    Run without arguments to enter an album path and choose its MusicBrainz
    release page. Matches must be complete and unambiguous. Existing artwork
    and unrelated tags are retained; audio is not re-encoded.
    """
    if yes and not apply:
        console.print("[bold red]--yes requires --apply.[/bold red]")
        raise typer.Exit(code=45)
    if album_dir is None:
        album_dir = Path(typer.prompt("Album folder").strip().strip('"'))
    album_dir = album_dir.expanduser()
    if not album_dir.is_dir():
        console.print(Text(f"Album folder does not exist: {album_dir}", style="bold red"))
        raise typer.Exit(code=45)
    if source_url is None:
        query = f"{album_dir.parent.name} {album_dir.name}"
        search_url = "https://musicbrainz.org/search?" + urlencode({"query": query, "type": "release", "method": "indexed"})
        console.print("[bold]Metadata source: MusicBrainz[/bold]")
        console.print(Text(f"Find this album: {search_url}"))
        console.print("Choose the release edition with the same track list as your folder.")
        source_url = typer.prompt("MusicBrainz release page URL").strip()

    try:
        metadata = fetch_album(source_url)
        plan = preview_album(album_dir, metadata, filename_format=filename_format)
        console.print(Text(f"Album: {metadata.title} — {metadata.artist}"))
        console.print(Text(f"Source: {metadata.source_url}"))
        console.print(Text(f"Release date: {metadata.date or 'not supplied; existing date retained'}"))
        console.print("Filename format: " + ("track - title - album - author - (year)" if filename_format == AlbumFilenameFormat.FULL else "track - title"))
        table = Table(title="Album rename and tag preview")
        table.add_column("Track")
        table.add_column("Current filename")
        table.add_column("New filename")
        table.add_column("Artist")
        for track in plan.tracks:
            table.add_row(track.tags["track"], track.source.name, track.target.name, track.tags["artist"])
        console.print(table)
        console.print("Tags: title, artist, album artist, album, track and disc number, and release date when supplied.")
        console.print(Text(f"Playlists to update: {len(plan.playlists)}"))
        if not apply:
            console.print("[bold green]Preview complete. No album files changed.[/bold green]")
            console.print("Run again with --apply to save these names and tags.")
            return
        if not yes and not typer.confirm("Apply this album plan and retain original backups?", default=False):
            console.print("Cancelled. No album files changed.")
            return
        result = apply_album(plan, backup_root or runtime_root() / "state" / "album-transactions")
    except (MusicBrainzError, AlbumLibraryError, OSError) as exc:
        console.print(Text(f"Album operation blocked: {exc}", style="bold red"))
        raise typer.Exit(code=45) from exc

    console.print("[bold green]Album renamed, tagged, and verified.[/bold green]")
    console.print(Text(f"Original backups: {result.transaction_dir}"))
    console.print(Text(f"Transaction report: {result.report_path}"))
