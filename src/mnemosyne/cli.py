from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .adoption import AdoptionError, adopt_latest_recommended_source
from .comparison import ComparisonError, compare_archive_candidates
from .completion import CompletionError, apply_completion, preview_completion
from .cleanup import CleanupError, apply_cleanup, preview_cleanup
from .config import initialize_runtime, load_config, runtime_root
from .fetcher import FetchError, fetch_plan_to_staging
from .inspection import InspectionError, inspect_staging_job, latest_staging_job
from .models import MediaType
from .multifile_metadata import (
    MultiFileMetadataError,
    apply_multifile_tagging,
    is_multifile_job,
    preview_multifile_tagging,
    verify_multifile_readiness,
)
from .multifile_placement import (
    MultiFilePlacementError,
    apply_multifile_placement,
    preview_multifile_placement,
)
from .multifile_completion import (
    MultiFileCompletionError,
    apply_multifile_cleanup,
    apply_multifile_completion,
    preview_multifile_cleanup,
    preview_multifile_completion,
)
from .planner import build_plan
from .providers.archive_org import ArchiveOrgProvider
from .providers.base import ProviderError
from .prune import PruneError, apply_prune, preview_prune
from .placement import PlacementError, apply_final_placement, preview_final_placement
from .readiness import ReadinessError, verify_staged_readiness
from .render import (
    console,
    render_adoption,
    render_comparison,
    render_fetch_result,
    render_inspection,
    render_plan,
    render_tagging_preview,
    render_tagging_result,
    render_multifile_tag_preview,
    render_multifile_tag_result,
    render_multifile_readiness,
    render_readiness,
    render_placement_preview,
    render_placement_result,
    render_multifile_placement_preview,
    render_multifile_placement_result,
    render_completion_preview,
    render_completion_result,
    render_multifile_completion_preview,
    render_multifile_completion_result,
    render_cleanup_preview,
    render_cleanup_result,
    render_multifile_cleanup_preview,
    render_multifile_cleanup_result,
    render_prune_preview,
    render_prune_result,
)
from .tagging import TaggingError, apply_metadata_normalization, preview_metadata_normalization

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
    audio_format: str | None,
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

    return config, build_plan(
        item,
        config.library_root,
        preferred_audio_format=audio_format,
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
    media_type: Annotated[MediaType, typer.Argument(help="Media type: audiobook, ebook, or music.")],
    url: Annotated[str, typer.Argument(help="Source item URL.")],
    year: Annotated[int | None, typer.Option("--year", help="Verified publication/release year override.")] = None,
    title: Annotated[str | None, typer.Option("--title", help="Verified title override.")] = None,
    creator: Annotated[str | None, typer.Option("--creator", help="Verified author/artist override.")] = None,
    audio_format: Annotated[str | None, typer.Option("--audio-format", help="Prefer a complete audio edition by extension, e.g. mp3, m4b, flac.")] = None,
) -> None:
    """Discover an item and print the proposed acquisition plan. Writes no media."""
    _, plan_result = _build_plan(
        media_type,
        url,
        year=year,
        title=title,
        creator=creator,
        audio_format=audio_format,
    )
    render_plan(plan_result)


@app.command()
def fetch(
    media_type: Annotated[MediaType, typer.Argument(help="Media type: audiobook, ebook, or music.")],
    url: Annotated[str, typer.Argument(help="Source item URL.")],
    apply: Annotated[bool, typer.Option("--apply", help="Actually download the selected file into isolated staging.")] = False,
    year: Annotated[int | None, typer.Option("--year", help="Verified publication/release year override.")] = None,
    title: Annotated[str | None, typer.Option("--title", help="Verified title override.")] = None,
    creator: Annotated[str | None, typer.Option("--creator", help="Verified author/artist override.")] = None,
    audio_format: Annotated[str | None, typer.Option("--audio-format", help="Prefer a complete audio edition by extension, e.g. mp3, m4b, flac.")] = None,
) -> None:
    """Fetch the planned audio edition into staging only."""
    _, plan_result = _build_plan(
        media_type,
        url,
        year=year,
        title=title,
        creator=creator,
        audio_format=audio_format,
    )
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
    """Adopt the latest verified comparison winner inside staging."""
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



@app.command("tag")
def tag_command(
    job: Annotated[Path | None, typer.Argument(help="Staging job directory. Omit to use the most recent completed job.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Transactionally write canonical metadata and embed verified cover art in staging.")] = False,
) -> None:
    """Preview or apply canonical metadata normalization in staging."""
    try:
        job_dir = job if job is not None else latest_staging_job()
        multi_file = is_multifile_job(job_dir)
        if multi_file:
            preview_multi = preview_multifile_tagging(job_dir)
        else:
            preview = preview_metadata_normalization(job_dir)
    except (InspectionError, TaggingError, MultiFileMetadataError) as exc:
        console.print(f"[bold red]Metadata normalization blocked:[/bold red] {exc}")
        raise typer.Exit(code=11) from exc

    if not apply:
        if multi_file:
            render_multifile_tag_preview(preview_multi)
        else:
            render_tagging_preview(preview)
        return

    try:
        if multi_file:
            result_multi = apply_multifile_tagging(job_dir)
        else:
            result = apply_metadata_normalization(job_dir)
    except (TaggingError, MultiFileMetadataError, OSError) as exc:
        console.print(f"[bold red]Metadata normalization failed:[/bold red] {exc}")
        raise typer.Exit(code=12) from exc

    if multi_file:
        render_multifile_tag_result(result_multi)
    else:
        render_tagging_result(result)



@app.command("ready")
def ready_command(
    job: Annotated[Path | None, typer.Argument(help="Staging job directory. Omit to verify the most recent completed job.")] = None,
) -> None:
    """Perform the final staged readiness verification."""
    try:
        job_dir = job if job is not None else latest_staging_job()
        multi_file = is_multifile_job(job_dir)
        if multi_file:
            result_multi = verify_multifile_readiness(job_dir)
        else:
            result = verify_staged_readiness(job_dir)
    except (InspectionError, ReadinessError, MultiFileMetadataError, OSError) as exc:
        console.print(f"[bold red]Readiness verification failed:[/bold red] {exc}")
        raise typer.Exit(code=13) from exc

    if multi_file:
        render_multifile_readiness(result_multi)
        if not result_multi.ready:
            raise typer.Exit(code=14)
    else:
        render_readiness(result)
        if not result.ready:
            raise typer.Exit(code=14)



@app.command("place")
def place_command(
    job: Annotated[Path | None, typer.Argument(help="Staging job directory. Omit to use the most recent completed job.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Transactionally place the certified staged media into the final library.")] = False,
) -> None:
    try:
        job_dir = job if job is not None else latest_staging_job()
        multi_file = is_multifile_job(job_dir)
        if multi_file:
            preview_multi = preview_multifile_placement(job_dir)
        else:
            preview = preview_final_placement(job_dir)
    except (
        InspectionError,
        PlacementError,
        MultiFilePlacementError,
        MultiFileMetadataError,
        OSError,
    ) as exc:
        console.print(f"[bold red]Final placement blocked:[/bold red] {exc}")
        raise typer.Exit(code=15) from exc

    if not apply:
        if multi_file:
            render_multifile_placement_preview(preview_multi)
        else:
            render_placement_preview(preview)
        return

    try:
        if multi_file:
            result_multi = apply_multifile_placement(job_dir)
        else:
            result = apply_final_placement(job_dir)
    except (PlacementError, MultiFilePlacementError, OSError) as exc:
        console.print(f"[bold red]Final placement failed:[/bold red] {exc}")
        raise typer.Exit(code=16) from exc

    if multi_file:
        render_multifile_placement_result(result_multi)
    else:
        render_placement_result(result)



@app.command("complete")
def complete_command(
    job: Annotated[Path | None, typer.Argument(help="Staging job directory. Omit to use the most recent completed placement job.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Mark the fully verified acquisition lifecycle complete while retaining staging evidence.")] = False,
) -> None:
    try:
        job_dir = job if job is not None else latest_staging_job()
        multi_file = is_multifile_job(job_dir)
        if multi_file:
            preview_multi = preview_multifile_completion(job_dir)
        else:
            preview = preview_completion(job_dir)
    except (
        InspectionError,
        CompletionError,
        MultiFileCompletionError,
        MultiFileMetadataError,
        OSError,
    ) as exc:
        console.print(f"[bold red]Completion blocked:[/bold red] {exc}")
        raise typer.Exit(code=17) from exc

    if not apply:
        if multi_file:
            render_multifile_completion_preview(preview_multi)
            if not preview_multi.ready_to_complete:
                raise typer.Exit(code=18)
        else:
            render_completion_preview(preview)
            if not preview.ready_to_complete:
                raise typer.Exit(code=18)
        return

    try:
        if multi_file:
            result_multi = apply_multifile_completion(job_dir)
        else:
            result = apply_completion(job_dir)
    except (CompletionError, MultiFileCompletionError, OSError) as exc:
        console.print(f"[bold red]Completion failed:[/bold red] {exc}")
        raise typer.Exit(code=19) from exc

    if multi_file:
        render_multifile_completion_result(result_multi)
    else:
        render_completion_result(result)



@app.command("cleanup")
def cleanup_command(
    job: Annotated[Path | None, typer.Argument(help="Completed staging job directory. Omit to use the most recent retained staging job.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Archive a durable completion receipt and delete the retained staging job.")] = False,
    confirm: Annotated[str | None, typer.Option("--confirm", help="Required with --apply. Must exactly match the job ID being deleted.")] = None,
) -> None:
    try:
        job_dir = job if job is not None else latest_staging_job()
        multi_file = is_multifile_job(job_dir)
        if multi_file:
            preview_multi = preview_multifile_cleanup(job_dir)
        else:
            preview = preview_cleanup(job_dir)
    except (
        InspectionError,
        CleanupError,
        MultiFileCompletionError,
        MultiFileMetadataError,
        OSError,
    ) as exc:
        console.print(f"[bold red]Cleanup blocked:[/bold red] {exc}")
        raise typer.Exit(code=20) from exc

    if not apply:
        if multi_file:
            render_multifile_cleanup_preview(preview_multi)
        else:
            render_cleanup_preview(preview)
        return

    if confirm is None:
        console.print(
            "[bold red]Cleanup blocked:[/bold red] "
            "destructive cleanup requires --confirm with the exact job ID."
        )
        raise typer.Exit(code=21)

    try:
        if multi_file:
            result_multi = apply_multifile_cleanup(
                job_dir,
                confirm_job_id=confirm,
            )
        else:
            result = apply_cleanup(
                job_dir,
                confirm_job_id=confirm,
            )
    except (CleanupError, MultiFileCompletionError, OSError) as exc:
        console.print(f"[bold red]Cleanup failed:[/bold red] {exc}")
        raise typer.Exit(code=22) from exc

    if multi_file:
        render_multifile_cleanup_result(result_multi)
    else:
        render_cleanup_result(result)


@app.command("prune")
def prune_command(
    job_id: Annotated[str, typer.Argument(help="Completed job ID whose exact source URL should be removed from its fetch list.")],
    apply: Annotated[bool, typer.Option("--apply", help="Atomically remove exact matching active fetch-list entries after backup.")] = False,
    confirm_url: Annotated[str | None, typer.Option("--confirm-url", help="Required with --apply. Must exactly match the completed source URL.")] = None,
) -> None:
    """
    Preview or explicitly prune a completed source URL from the appropriate fetch list.

    This command never infers near matches and never removes comments or unrelated URLs.
    """
    try:
        preview = preview_prune(job_id)
    except (PruneError, OSError) as exc:
        console.print(f"[bold red]Fetch-list pruning blocked:[/bold red] {exc}")
        raise typer.Exit(code=23) from exc

    if not apply:
        render_prune_preview(preview)
        return

    if confirm_url is None:
        console.print(
            "[bold red]Fetch-list pruning blocked:[/bold red] "
            "pruning requires --confirm-url with the exact completed source URL."
        )
        raise typer.Exit(code=24)

    try:
        result = apply_prune(job_id, confirm_url=confirm_url)
    except (PruneError, OSError) as exc:
        console.print(f"[bold red]Fetch-list pruning failed:[/bold red] {exc}")
        raise typer.Exit(code=25) from exc

    render_prune_result(result)


if __name__ == "__main__":
    app()
