from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
cli_path = root / "src/mnemosyne/cli.py"
render_path = root / "src/mnemosyne/render.py"

cli = cli_path.read_text(encoding="utf-8")
render = render_path.read_text(encoding="utf-8")

if "from .multifile_metadata import (" not in cli:
    anchor = "from .models import MediaType\n"
    if anchor not in cli:
        raise SystemExit("Could not find models import anchor.")
    cli = cli.replace(
        anchor,
        anchor
        + "from .multifile_metadata import (\n"
        + "    MultiFileMetadataError,\n"
        + "    apply_multifile_tagging,\n"
        + "    is_multifile_job,\n"
        + "    preview_multifile_tagging,\n"
        + "    verify_multifile_readiness,\n"
        + ")\n",
        1,
    )

if "render_multifile_tag_preview" not in cli:
    anchor = "    render_tagging_result,\n"
    if anchor not in cli:
        raise SystemExit("Could not find render import anchor.")
    cli = cli.replace(
        anchor,
        anchor
        + "    render_multifile_tag_preview,\n"
        + "    render_multifile_tag_result,\n"
        + "    render_multifile_readiness,\n",
        1,
    )


def replace_command(text: str, command: str, next_command: str, replacement: str) -> str:
    pattern = re.compile(
        rf'^@app\.command\("{re.escape(command)}"\).*?(?=^@app\.command\("{re.escape(next_command)}"\))',
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"Could not find command block: {command}")
    return text[:match.start()] + replacement.rstrip() + "\n\n\n" + text[match.end():]


tag_block = r'''
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
'''

ready_block = r'''
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
'''

cli = replace_command(cli, "tag", "ready", tag_block)
cli = replace_command(cli, "ready", "place", ready_block)

if "def render_multifile_tag_preview(" not in render:
    render += r'''

def render_multifile_tag_preview(preview) -> None:
    console.print(Panel.fit(
        "[bold]Mnemosyne[/bold]\n[dim]Whole-edition metadata preview[/dim]",
        border_style="cyan",
    ))
    table = Table(title=f"MP3 chapter metadata ({len(preview.tracks)} files)")
    table.add_column("#", justify="right")
    table.add_column("File")
    table.add_column("Title")
    table.add_column("Track")
    for track in preview.tracks:
        table.add_row(
            str(track.index),
            track.path.name,
            track.tags.get("title", ""),
            f"{track.index}/{track.total}",
        )
    console.print(table)
    console.print(Panel(
        "[bold yellow]Preview only.[/bold yellow]\n"
        "Every chapter will be prepared and verified before canonical staged files are replaced.\n"
        "No staged audio or final-library media was modified.",
        border_style="yellow",
    ))


def render_multifile_tag_result(result) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Audio mode", "multi-file")
    table.add_row("Files", str(result.file_count))
    table.add_row("Rollback edition", str(result.rollback_dir))
    table.add_row("Edition SHA-256", result.edition_sha256)
    table.add_row("Embedded cover SHA-256", result.embedded_cover_sha256)
    table.add_row("Updated report", str(result.report_path))
    console.print(Panel(
        table,
        title="[bold green]WHOLE EDITION METADATA NORMALIZED + VERIFIED[/bold green]",
        border_style="green",
    ))
    console.print(Panel(
        "[bold]All working copies verified before commit: YES[/bold]\n"
        "[bold]Whole-edition rollback preserved: YES[/bold]\n"
        "[bold]Final library modified: NO[/bold]",
        border_style="cyan",
    ))


def render_multifile_readiness(result) -> None:
    console.print(Panel.fit(
        "[bold]Mnemosyne[/bold]\n[dim]Multi-file staged readiness verification[/dim]",
        border_style="cyan",
    ))
    table = Table(title="Readiness checks")
    table.add_column("Result")
    table.add_column("Check")
    table.add_column("Detail")
    for check in result.checks:
        table.add_row(
            "[green]PASS[/green]" if check.passed else "[red]FAIL[/red]",
            check.name,
            check.detail,
        )
    console.print(table)
    console.print(f"[bold]Audio files:[/bold] {result.file_count}")
    console.print(f"[bold]Edition SHA-256:[/bold] {result.edition_sha256}")
    console.print(Panel(
        "[bold green]READY FOR MULTI-FILE PLACEMENT[/bold green]"
        if result.ready else
        "[bold red]NOT READY[/bold red]",
        border_style="green" if result.ready else "red",
    ))
'''

cli_path.write_text(cli, encoding="utf-8")
render_path.write_text(render, encoding="utf-8")
print("Patched CLI dispatch and renderers without changing single-file implementations.")
