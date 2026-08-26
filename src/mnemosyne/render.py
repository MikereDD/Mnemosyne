from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .fetcher import FetchResult
from .models import AcquisitionPlan, CandidateKind

console = Console()


def _size(value: int | None) -> str:
    if value is None:
        return "?"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def render_plan(plan: AcquisitionPlan) -> None:
    item = plan.item

    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Archive.org acquisition plan[/dim]",
            border_style="cyan",
        )
    )

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

    audio = [c for c in item.candidates if c.kind is CandidateKind.AUDIO]
    auxiliary = [c for c in item.candidates if c.kind is CandidateKind.AUXILIARY]

    table = Table(title="Playable audio candidates", show_lines=False)
    table.add_column("Rank", justify="right")
    table.add_column("File")
    table.add_column("Format")
    table.add_column("Source")
    table.add_column("Size", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Why")

    ranked = sorted(audio, key=lambda c: (c.score, c.size or 0), reverse=True)
    for index, candidate in enumerate(ranked, start=1):
        selected = bool(plan.selected_audio and candidate.name == plan.selected_audio[0].name)
        rank = f"[green]{index} ✓[/green]" if selected else str(index)
        table.add_row(
            rank,
            candidate.name,
            candidate.archive_format or candidate.extension,
            candidate.source or "?",
            _size(candidate.size),
            str(candidate.score),
            ", ".join(candidate.reasons),
        )

    if ranked:
        console.print(table)
    else:
        console.print("[red]No playable audio files found.[/red]")

    if auxiliary:
        excluded_names = ", ".join(c.name for c in auxiliary[:8])
        if len(auxiliary) > 8:
            excluded_names += f", … (+{len(auxiliary) - 8} more)"
        console.print(
            Panel(
                f"[dim]{excluded_names}[/dim]",
                title="Excluded auxiliary/non-media files",
                border_style="dim",
            )
        )

    if plan.selected_cover:
        console.print(
            f"[bold]Cover candidate:[/bold] {plan.selected_cover.name} "
            f"([dim]{_size(plan.selected_cover.size)}[/dim])"
        )

    if plan.warnings:
        warning_text = Text()
        for warning in plan.warnings:
            warning_text.append("• ", style="yellow")
            warning_text.append(warning)
            warning_text.append("\n")
        console.print(Panel(warning_text, title="Needs attention", border_style="yellow"))

    console.print(
        Panel(
            "[bold green]Plan complete.[/bold green]\n"
            "The final media library has not been modified.",
            border_style="green",
        )
    )


def render_fetch_result(result: FetchResult) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job", result.job_id)
    table.add_row("Staging", str(result.staging_dir))
    table.add_row("Audio", str(result.audio.path))
    table.add_row("Size", _size(result.audio.actual_size))
    table.add_row("Signature", result.audio.signature)
    table.add_row("SHA-256", result.audio.sha256)
    table.add_row("Report", str(result.report_path))

    console.print(
        Panel(
            table,
            title="[bold green]STAGED + VERIFIED[/bold green]",
            border_style="green",
        )
    )
    console.print(
        Panel(
            "[bold]Final library modified: NO[/bold]\n"
            "The verified source audio remains isolated in Mnemosyne staging.",
            border_style="cyan",
        )
    )
