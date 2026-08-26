from __future__ import annotations

from typing import Annotated

import typer

from .config import initialize_runtime, load_config, runtime_root
from .models import MediaType
from .planner import build_plan
from .providers.archive_org import ArchiveOrgProvider
from .providers.base import ProviderError
from .render import console, render_plan

app = typer.Typer(
    help="Mnemosyne media acquisition and library-normalization pipeline.",
    no_args_is_help=True,
)


@app.command()
def init() -> None:
    """Create Mnemosyne's safe per-user runtime/config/fetch directories."""
    created = initialize_runtime()
    console.print(f"[bold green]Runtime root:[/bold green] {runtime_root()}")
    if created:
        console.print("Created:")
        for path in created:
            console.print(f"  • {path}")
    else:
        console.print("[dim]Runtime structure already exists; nothing changed.[/dim]")


@app.command()
def plan(
    media_type: Annotated[
        MediaType,
        typer.Argument(help="Media type: audiobook, ebook, or music."),
    ],
    url: Annotated[str, typer.Argument(help="Source item URL.")],
    year: Annotated[
        int | None,
        typer.Option("--year", help="Verified publication/release year override."),
    ] = None,
    title: Annotated[
        str | None,
        typer.Option("--title", help="Verified title override."),
    ] = None,
    creator: Annotated[
        str | None,
        typer.Option("--creator", help="Verified author/artist override."),
    ] = None,
) -> None:
    """Discover an item and print the proposed acquisition plan. Writes no media."""
    config = load_config()
    provider = ArchiveOrgProvider()

    try:
        item = provider.identify(
            url,
            media_type,
            title_override=title,
            creator_override=creator,
            year_override=year,
        )
    except ProviderError as exc:
        console.print(f"[bold red]Provider error:[/bold red] {exc}")
        raise typer.Exit(code=2) from exc

    plan_result = build_plan(item, config.library_root)
    render_plan(plan_result)


if __name__ == "__main__":
    app()
