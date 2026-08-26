from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .adoption import AdoptionError, adopt_latest_recommended_source
from .comparison import ComparisonError, compare_archive_candidates
from .config import initialize_runtime, load_config, runtime_root
from .fetcher import FetchError, fetch_plan_to_staging
from .inspection import InspectionError, inspect_staging_job, latest_staging_job
from .models import MediaType
from .planner import build_plan
from .providers.archive_org import ArchiveOrgProvider
from .providers.base import ProviderError
from .render import (
    console,
    render_adoption,
    render_comparison,
    render_fetch_result,
    render_inspection,
    render_plan,
)

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
    media_type: Annotated[MediaType, typer.Argument(help="Media type: audiobook, ebook, or music.")],
    url: Annotated[str, typer.Argument(help="Source item URL.")],
    year: Annotated[int | None, typer.Option("--year", help="Verified publication/release year override.")] = None,
    title: Annotated[str | None, typer.Option("--title", help="Verified title override.")] = None,
    creator: Annotated[str | None, typer.Option("--creator", help="Verified author/artist override.")] = None,
) -> None:
    """Discover an item and print the proposed acquisition plan. Writes no media."""
    _, plan_result = _build_plan(media_type, url, year=year, title=title, creator=creator)
    render_plan(plan_result)


@app.command()
def fetch(
    media_type: Annotated[MediaType, typer.Argument(help="Media type: audiobook, ebook, or music.")],
    url: Annotated[str, typer.Argument(help="Source item URL.")],
    apply: Annotated[bool, typer.Option("--apply", help="Actually download the selected file into isolated staging.")] = False,
    year: Annotated[int | None, typer.Option("--year", help="Verified publication/release year override.")] = None,
    title: Annotated[str | None, typer.Option("--title", help="Verified title override.")] = None,
    creator: Annotated[str | None, typer.Option("--creator", help="Verified author/artist override.")] = None,
) -> None:
    """Fetch the planned audio into staging only."""
    _, plan_result = _build_plan(media_type, url, year=year, title=title, creator=creator)
    render_plan(plan_result)

    if not apply:
        console.print("[yellow]Fetch not applied.[/yellow] Re-run with [bold]--apply[/bold] to download into staging only.")
        return

    if plan_result.warnings:
        console.print("[bold red]Fetch blocked:[/bold red] resolve plan warnings before --apply.")
        raise typer.Exit(code=3)

    try:
        result = fetch_plan_to_staging(plan_result, runtime_root() / "staging")
    except (FetchError, OSError) as exc:
        console.print(f"[bold red]Fetch failed:[/bold red] {exc}")
        raise typer.Exit(code=4) from exc

    render_fetch_result(result)


@app.command("inspect")
def inspect_command(
    job: Annotated[Path | None, typer.Argument(help="Staging job directory. Omit to inspect the most recent completed job.")] = None,
) -> None:
    """Inspect staged audio properties/tags and preview canonical metadata. Read-only."""
    try:
        job_dir = job if job is not None else latest_staging_job()
        result = inspect_staging_job(job_dir)
    except InspectionError as exc:
        console.print(f"[bold red]Inspection failed:[/bold red] {exc}")
        raise typer.Exit(code=6) from exc

    render_inspection(result)


@app.command("compare")
def compare_command(
    job: Annotated[Path | None, typer.Argument(help="Staging job directory. Omit to compare the most recent completed job.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Download playable alternatives into the staging job for actual comparison.")] = False,
) -> None:
    """Compare Archive audio candidates using actual downloaded media properties."""
    try:
        job_dir = job if job is not None else latest_staging_job()
    except InspectionError as exc:
        console.print(f"[bold red]Comparison failed:[/bold red] {exc}")
        raise typer.Exit(code=7) from exc

    if not apply:
        console.print(
            f"[bold]Comparison target:[/bold] {job_dir}\n"
            "[yellow]No alternatives downloaded.[/yellow] Re-run with [bold]--apply[/bold] to compare actual media."
        )
        return

    try:
        result = compare_archive_candidates(job_dir)
    except (ComparisonError, FetchError, OSError) as exc:
        console.print(f"[bold red]Comparison failed:[/bold red] {exc}")
        raise typer.Exit(code=8) from exc

    render_comparison(result)


@app.command("adopt")
def adopt_command(
    job: Annotated[Path | None, typer.Argument(help="Staging job directory. Omit to use the most recent completed job.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Transactionally adopt the latest comparison winner into the staged canonical slot.")] = False,
) -> None:
    """
    Adopt the latest verified comparison winner inside staging.

    Without --apply this is preview-only.
    """
    try:
        job_dir = job if job is not None else latest_staging_job()
    except InspectionError as exc:
        console.print(f"[bold red]Adoption failed:[/bold red] {exc}")
        raise typer.Exit(code=9) from exc

    if not apply:
        console.print(
            f"[bold]Adoption target:[/bold] {job_dir}\n"
            "[yellow]No staged source changed.[/yellow] "
            "Re-run with [bold]--apply[/bold] to adopt the latest verified comparison winner."
        )
        return

    try:
        result = adopt_latest_recommended_source(job_dir)
    except (AdoptionError, OSError) as exc:
        console.print(f"[bold red]Adoption failed:[/bold red] {exc}")
        raise typer.Exit(code=10) from exc

    render_adoption(result)


if __name__ == "__main__":
    app()
