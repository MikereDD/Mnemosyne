from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
cli_path = root / "src" / "mnemosyne" / "cli.py"
render_path = root / "src" / "mnemosyne" / "render.py"

cli = cli_path.read_text(encoding="utf-8")
render = render_path.read_text(encoding="utf-8")

if "from .multifile_completion import (" not in cli:
    anchor = "from .multifile_placement import (\n"
    start = cli.find(anchor)
    if start < 0:
        raise SystemExit("Could not find multifile placement import.")
    end = cli.find(")\n", start)
    if end < 0:
        raise SystemExit("Could not find multifile placement import end.")
    end += 2
    cli = (
        cli[:end]
        + "from .multifile_completion import (\n"
        + "    MultiFileCompletionError,\n"
        + "    apply_multifile_cleanup,\n"
        + "    apply_multifile_completion,\n"
        + "    preview_multifile_cleanup,\n"
        + "    preview_multifile_completion,\n"
        + ")\n"
        + cli[end:]
    )

if "render_multifile_completion_preview" not in cli:
    anchor = "    render_completion_result,\n"
    if anchor not in cli:
        raise SystemExit("Could not find completion render import.")
    cli = cli.replace(
        anchor,
        anchor
        + "    render_multifile_completion_preview,\n"
        + "    render_multifile_completion_result,\n",
        1,
    )

if "render_multifile_cleanup_preview" not in cli:
    anchor = "    render_cleanup_result,\n"
    if anchor not in cli:
        raise SystemExit("Could not find cleanup render import.")
    cli = cli.replace(
        anchor,
        anchor
        + "    render_multifile_cleanup_preview,\n"
        + "    render_multifile_cleanup_result,\n",
        1,
    )


def replace_command(text, command, next_command, replacement):
    pattern = re.compile(
        rf'^@app\.command\("{re.escape(command)}"\).*?'
        rf'(?=^@app\.command\("{re.escape(next_command)}"\))',
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"Could not find command block: {command}")
    return (
        text[:match.start()]
        + replacement.rstrip()
        + "\n\n\n"
        + text[match.end():]
    )


complete_block = r'''
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
'''

cleanup_block = r'''
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
'''

cli = replace_command(cli, "complete", "cleanup", complete_block)
cli = replace_command(cli, "cleanup", "prune", cleanup_block)

if "def render_multifile_completion_preview(" not in render:
    render += r'''

def render_multifile_completion_preview(preview) -> None:
    console.print(Panel.fit(
        "[bold]Mnemosyne[/bold]\n[dim]Multi-file completion certification preview[/dim]",
        border_style="cyan",
    ))
    table = Table(title="Completion checks")
    table.add_column("Result")
    table.add_column("Check")
    table.add_column("Detail")
    for check in preview.checks:
        table.add_row(
            "[green]PASS[/green]" if check.passed else "[red]FAIL[/red]",
            check.name,
            check.detail,
        )
    console.print(table)
    console.print(f"[bold]Audio files:[/bold] {len(preview.audio_paths)}")
    console.print(f"[bold]Edition SHA-256:[/bold] {preview.edition_sha256}")
    console.print(Panel(
        "[bold green]READY TO COMPLETE[/bold green]"
        if preview.ready_to_complete else
        "[bold red]NOT READY TO COMPLETE[/bold red]",
        border_style="green" if preview.ready_to_complete else "red",
    ))


def render_multifile_completion_result(result) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Completed at", result.completed_at)
    table.add_row("Destination", str(result.destination))
    table.add_row("Audio files", str(len(result.audio_paths)))
    table.add_row("Edition SHA-256", result.edition_sha256)
    table.add_row("Cover", str(result.cover_path))
    table.add_row("Completion report", str(result.completion_report_path))
    console.print(Panel(
        table,
        title="[bold green]MULTI-FILE ACQUISITION COMPLETE[/bold green]",
        border_style="green",
    ))
    console.print(Panel(
        "[bold]Staging retained: YES[/bold]\n"
        "[bold]Automatic cleanup: NO[/bold]\n"
        "[bold]Fetch list pruned: NO[/bold]",
        border_style="cyan",
    ))


def render_multifile_cleanup_preview(preview) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job ID", preview.job_id)
    table.add_row("Staging", str(preview.job_dir))
    table.add_row("Final destination", str(preview.final_destination))
    table.add_row("Audio files", str(len(preview.audio_paths)))
    table.add_row("Edition SHA-256", preview.edition_sha256)
    table.add_row("Durable receipt", str(preview.receipt_path))
    table.add_row("Staging files", str(preview.file_count))
    table.add_row("Staging bytes", str(preview.staging_size_bytes))
    console.print(Panel(
        table,
        title="[bold yellow]MULTI-FILE CLEANUP PREVIEW[/bold yellow]",
        border_style="yellow",
    ))
    console.print(Panel(
        "Final edition and cover were reverified.\n"
        "No staging files were deleted.\n"
        f"Apply requires: [bold]--confirm {preview.job_id}[/bold]",
        border_style="cyan",
    ))


def render_multifile_cleanup_result(result) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job ID", result.job_id)
    table.add_row("Removed staging", str(result.removed_job_dir))
    table.add_row("Durable receipt", str(result.receipt_path))
    table.add_row("Final destination", str(result.final_destination))
    table.add_row("Edition SHA-256", result.edition_sha256)
    table.add_row("Final cover SHA-256", result.final_cover_sha256)
    table.add_row("Removed files", str(result.file_count))
    table.add_row("Removed bytes", str(result.staging_size_bytes))
    console.print(Panel(
        table,
        title="[bold green]MULTI-FILE STAGING CLEANED[/bold green]",
        border_style="green",
    ))
    console.print(Panel(
        "[bold]Durable receipt: VERIFIED[/bold]\n"
        "[bold]Final library: UNCHANGED + VERIFIED[/bold]\n"
        "[bold]Staging job: REMOVED[/bold]",
        border_style="cyan",
    ))
'''

cli_path.write_text(cli, encoding="utf-8")
render_path.write_text(render, encoding="utf-8")
print("Patched multi-file completion and cleanup dispatch/renderers.")
