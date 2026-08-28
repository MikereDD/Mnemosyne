from pathlib import Path
import re

root = Path(__file__).resolve().parents[1]
cli_path = root / "src" / "mnemosyne" / "cli.py"
render_path = root / "src" / "mnemosyne" / "render.py"
cli = cli_path.read_text(encoding="utf-8")
render = render_path.read_text(encoding="utf-8")

if "from .multifile_placement import (" not in cli:
    anchor = "from .multifile_metadata import (\n"
    start = cli.find(anchor)
    if start < 0:
        raise SystemExit("Could not find multifile metadata import.")
    end = cli.find(")\n", start)
    if end < 0:
        raise SystemExit("Could not find import end.")
    end += 2
    cli = (
        cli[:end]
        + "from .multifile_placement import (\n"
        + "    MultiFilePlacementError,\n"
        + "    apply_multifile_placement,\n"
        + "    preview_multifile_placement,\n"
        + ")\n"
        + cli[end:]
    )

if "render_multifile_placement_preview" not in cli:
    anchor = "    render_placement_result,\n"
    cli = cli.replace(
        anchor,
        anchor
        + "    render_multifile_placement_preview,\n"
        + "    render_multifile_placement_result,\n",
        1,
    )

pattern = re.compile(
    r'^@app\.command\("place"\).*?(?=^@app\.command\("complete"\))',
    re.MULTILINE | re.DOTALL,
)
match = pattern.search(cli)
if not match:
    raise SystemExit("Could not find place command.")

replacement = r'''
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
'''

cli = cli[:match.start()] + replacement.rstrip() + "\n\n\n" + cli[match.end():]

if "def render_multifile_placement_preview(" not in render:
    render += r'''

def render_multifile_placement_preview(preview) -> None:
    console.print(Panel.fit(
        "[bold]Mnemosyne[/bold]\n[dim]Transactional multi-file placement preview[/dim]",
        border_style="cyan",
    ))
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Destination", str(preview.destination))
    table.add_row("Audio files", str(len(preview.audio_sources)))
    table.add_row("Edition SHA-256", preview.edition_sha256)
    table.add_row("Cover SHA-256", preview.cover_sha256)
    console.print(table)
    console.print(Panel(
        "[bold yellow]Preview only.[/bold yellow]\n"
        "Apply copies the entire edition into a hidden sibling directory, "
        "verifies it, then commits with one directory rename.",
        border_style="yellow",
    ))


def render_multifile_placement_result(result) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Destination", str(result.destination))
    table.add_row("Audio files", str(len(result.audio_paths)))
    table.add_row("Edition SHA-256", result.edition_sha256)
    table.add_row("Cover", str(result.cover_path))
    table.add_row("Cover SHA-256", result.cover_sha256)
    table.add_row("Placement report", str(result.placement_report_path))
    console.print(Panel(
        table,
        title="[bold green]MULTI-FILE PLACED + VERIFIED[/bold green]",
        border_style="green",
    ))
    console.print(Panel(
        "[bold]Existing destination overwritten: NO[/bold]\n"
        "[bold]Pre-commit whole-edition verification: PASSED[/bold]\n"
        "[bold]Post-placement whole-edition verification: PASSED[/bold]\n"
        "[bold]Final library modified: YES[/bold]",
        border_style="cyan",
    ))
'''

cli_path.write_text(cli, encoding="utf-8")
render_path.write_text(render, encoding="utf-8")
print("Patched multi-file placement dispatch and renderers.")
