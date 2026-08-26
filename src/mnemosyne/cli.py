from __future__ import annotations

from typing import Annotated

import typer

from .config import initialize_runtime, load_config, runtime_root
from .fetcher import FetchError, fetch_plan_to_staging
from .models import MediaType
from .planner import build_plan
from .providers.archive_org import ArchiveOrgProvider
from .providers.base import ProviderError
from .render import console, render_fetch_result, render_plan

app = typer.Typer(
    help="Mnemosyne media acquisition and library-normalization pipeline.",
    no_args_is_help=True,
)


def _build_plan(
    media_type: MediaType,
    url: str,
    *,
    year: int | None,
    title: str | None,
    creator: str | None,
):
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

    return config, build_plan(item, config.library_root)


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
    _, plan_result = _build_plan(
        media_type,
        url,
        year=year,
        title=title,
        creator=creator,
    )
    render_plan(plan_result)


@app.command()
def fetch(
    media_type: Annotated[
        MediaType,
        typer.Argument(help="Media type: audiobook, ebook, or music."),
    ],
    url: Annotated[str, typer.Argument(help="Source item URL.")],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Actually download the selected file into isolated staging.",
        ),
    ] = False,
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
    """
    Fetch the planned audio into staging only.

    Without --apply this behaves as a plan preview and writes no media.
    """
    _, plan_result = _build_plan(
        media_type,
        url,
        year=year,
        title=title,
        creator=creator,
    )

    render_plan(plan_result)

    if not apply:
        console.print(
            "[yellow]Fetch not applied.[/yellow] "
            "Re-run with [bold]--apply[/bold] to download into staging only."
        )
        return

    if plan_result.warnings:
        console.print(
            "[bold red]Fetch blocked:[/bold red] resolve plan warnings before --apply."
        )
        raise typer.Exit(code=3)

    try:
        result = fetch_plan_to_staging(
            plan_result,
            runtime_root() / "staging",
        )
    except (FetchError, OSError) as exc:
        console.print(f"[bold red]Fetch failed:[/bold red] {exc}")
        raise typer.Exit(code=4) from exc
    except Exception as exc:
        console.print(f"[bold red]Fetch failed unexpectedly:[/bold red] {exc}")
        raise typer.Exit(code=5) from exc

    render_fetch_result(result)


if __name__ == "__main__":
    app()
