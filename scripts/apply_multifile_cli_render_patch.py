from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "src" / "mnemosyne" / "cli.py"
RENDER = ROOT / "src" / "mnemosyne" / "render.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        if new in text:
            return text
        raise SystemExit(f"Could not patch {label}: expected source text was not found.")
    return text.replace(old, new, 1)


def patch_cli(text: str) -> str:
    old = "    creator: str | None,\n):"
    new = "    creator: str | None,\n    audio_format: str | None,\n):"
    text = replace_once(text, old, new, "_build_plan signature")

    old = "    return config, build_plan(item, config.library_root)"
    new = (
        "    return config, build_plan(\n"
        "        item,\n"
        "        config.library_root,\n"
        "        preferred_audio_format=audio_format,\n"
        "    )"
    )
    text = replace_once(text, old, new, "_build_plan planner call")

    old = (
        '    creator: Annotated[str | None, typer.Option("--creator", help="Verified author/artist override.")] = None,\n'
        ') -> None:\n'
        '    """Discover an item and print the proposed acquisition plan. Writes no media."""\n'
        '    _, plan_result = _build_plan(media_type, url, year=year, title=title, creator=creator)'
    )
    new = (
        '    creator: Annotated[str | None, typer.Option("--creator", help="Verified author/artist override.")] = None,\n'
        '    audio_format: Annotated[str | None, typer.Option("--audio-format", help="Prefer a complete audio edition by extension, e.g. mp3, m4b, flac.")] = None,\n'
        ') -> None:\n'
        '    """Discover an item and print the proposed acquisition plan. Writes no media."""\n'
        '    _, plan_result = _build_plan(\n'
        '        media_type,\n'
        '        url,\n'
        '        year=year,\n'
        '        title=title,\n'
        '        creator=creator,\n'
        '        audio_format=audio_format,\n'
        '    )'
    )
    text = replace_once(text, old, new, "plan command")

    old = (
        '    creator: Annotated[str | None, typer.Option("--creator", help="Verified author/artist override.")] = None,\n'
        ') -> None:\n'
        '    """Fetch the planned audio into staging only."""\n'
        '    _, plan_result = _build_plan(media_type, url, year=year, title=title, creator=creator)'
    )
    new = (
        '    creator: Annotated[str | None, typer.Option("--creator", help="Verified author/artist override.")] = None,\n'
        '    audio_format: Annotated[str | None, typer.Option("--audio-format", help="Prefer a complete audio edition by extension, e.g. mp3, m4b, flac.")] = None,\n'
        ') -> None:\n'
        '    """Fetch the planned audio edition into staging only."""\n'
        '    _, plan_result = _build_plan(\n'
        '        media_type,\n'
        '        url,\n'
        '        year=year,\n'
        '        title=title,\n'
        '        creator=creator,\n'
        '        audio_format=audio_format,\n'
        '    )'
    )
    text = replace_once(text, old, new, "fetch command")
    return text


def replace_function(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(
        rf"^def {re.escape(name)}\(.*?(?=^def |\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(f"Could not patch render function {name}.")
    return text[:match.start()] + replacement.rstrip() + "\n\n\n" + text[match.end():]


PLAN_RENDER = '''
def render_plan(plan: AcquisitionPlan) -> None:
    item = plan.item
    console.print(Panel.fit("[bold]Mnemosyne[/bold]\\n[dim]Archive.org acquisition plan[/dim]", border_style="cyan"))
    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Provider", "Internet Archive")
    summary.add_row("Identifier", item.identifier)
    summary.add_row("Media", item.media_type.value)
    summary.add_row("Raw title", item.raw_title)
    summary.add_row("Title", item.title)
    summary.add_row("Creator", item.creator or "[yellow]Unknown[/yellow]")
    summary.add_row("Year", str(item.year) if item.year else "[yellow]Unknown[/yellow]")
    summary.add_row("Destination", str(plan.destination))
    console.print(summary)

    if plan.audio_editions:
        table = Table(title="Playable audio editions")
        table.add_column("Rank", justify="right")
        table.add_column("Edition")
        table.add_column("Files", justify="right")
        table.add_column("Format")
        table.add_column("Source")
        table.add_column("Total size", justify="right")
        table.add_column("Score", justify="right")
        for index, edition in enumerate(plan.audio_editions, start=1):
            selected = edition.key == plan.selected_edition_key
            table.add_row(
                f"[green]{index} ✓[/green]" if selected else str(index),
                edition.label,
                str(len(edition.candidates)),
                edition.archive_format or edition.extension,
                edition.source or "?",
                _size(edition.total_size),
                str(edition.score),
            )
        console.print(table)

        selected = next(
            (edition for edition in plan.audio_editions if edition.key == plan.selected_edition_key),
            None,
        )
        if selected and selected.multi_file:
            members = Table(title=f"Selected chapter set ({len(selected.candidates)} files)")
            members.add_column("#", justify="right")
            members.add_column("Source file")
            members.add_column("Size", justify="right")
            for index, candidate in enumerate(selected.candidates, start=1):
                members.add_row(str(index), candidate.name, _size(candidate.size))
            console.print(members)

    auxiliary = [c for c in item.candidates if c.kind is CandidateKind.AUXILIARY]
    if auxiliary:
        excluded_names = ", ".join(c.name for c in auxiliary[:8])
        if len(auxiliary) > 8:
            excluded_names += f", … (+{len(auxiliary) - 8} more)"
        console.print(Panel(f"[dim]{excluded_names}[/dim]", title="Excluded auxiliary/non-media files", border_style="dim"))

    if plan.selected_cover:
        console.print(f"[bold]Cover candidate:[/bold] {plan.selected_cover.name} ([dim]{_size(plan.selected_cover.size)}[/dim])")

    if plan.warnings:
        warning_text = Text()
        for warning in plan.warnings:
            warning_text.append("• ", style="yellow")
            warning_text.append(warning)
            warning_text.append("\\n")
        console.print(Panel(warning_text, title="Needs attention", border_style="yellow"))

    console.print(
        Panel(
            "[bold green]Plan complete.[/bold green]\\n"
            "Provider quality claims remain provisional until downloaded files are inspected.",
            border_style="green",
        )
    )
'''


FETCH_RENDER = '''
def render_fetch_result(result: FetchResult) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job", result.job_id)
    table.add_row("Staging", str(result.staging_dir))
    table.add_row("Audio mode", "multi-file" if result.multi_file else "single-file")
    table.add_row("Audio files", str(len(result.audio_files)))

    if not result.multi_file:
        table.add_row("Audio", str(result.audio.path))
        table.add_row("Audio size", _size(result.audio.actual_size))
        table.add_row("Signature", result.audio.signature)
        table.add_row("Actual codec", result.audio.actual_codec or "?")
        table.add_row("Audio SHA-256", result.audio.sha256)
    else:
        total = sum(file.actual_size for file in result.audio_files)
        table.add_row("Audio folder", str(result.audio_files[0].path.parent))
        table.add_row("Total audio size", _size(total))

    if result.cover:
        table.add_row("Cover", str(result.cover.path))
    table.add_row("Report", str(result.report_path))

    title = (
        "[bold yellow]STAGED + NEEDS ATTENTION[/bold yellow]"
        if result.warnings
        else "[bold green]STAGED + VERIFIED[/bold green]"
    )
    console.print(Panel(table, title=title, border_style="yellow" if result.warnings else "green"))

    if result.multi_file:
        files = Table(title="Staged audio files")
        files.add_column("#", justify="right")
        files.add_column("Canonical file")
        files.add_column("Codec")
        files.add_column("Size", justify="right")
        files.add_column("SHA-256")
        for index, staged in enumerate(result.audio_files, start=1):
            files.add_row(
                str(index),
                staged.path.name,
                staged.actual_codec or "?",
                _size(staged.actual_size),
                staged.sha256,
            )
        console.print(files)

    if result.warnings:
        console.print(
            Panel(
                "\\n".join(f"• {w}" for w in result.warnings),
                title="Quality cross-check",
                border_style="yellow",
            )
        )

    console.print(
        Panel(
            "[bold]Final library modified: NO[/bold]\\n"
            "Downloaded media remains isolated in Mnemosyne staging.",
            border_style="cyan",
        )
    )
'''


def patch_render(text: str) -> str:
    text = replace_function(text, "render_plan", PLAN_RENDER)
    text = replace_function(text, "render_fetch_result", FETCH_RENDER)
    return text


def main() -> None:
    cli = CLI.read_text(encoding="utf-8")
    render = RENDER.read_text(encoding="utf-8")
    CLI.write_text(patch_cli(cli), encoding="utf-8")
    RENDER.write_text(patch_render(render), encoding="utf-8")
    print("Patched cli.py while preserving all existing commands.")
    print("Patched only render_plan() and render_fetch_result().")


if __name__ == "__main__":
    main()
