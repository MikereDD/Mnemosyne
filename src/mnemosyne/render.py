from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .comparison import ComparisonResult
from .fetcher import FetchResult
from .inspection import MetadataInspection
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


def _duration(value: float | None) -> str:
    if value is None:
        return "?"
    total = int(round(value))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def render_plan(plan: AcquisitionPlan) -> None:
    item = plan.item
    console.print(Panel.fit("[bold]Mnemosyne[/bold]\n[dim]Archive.org acquisition plan[/dim]", border_style="cyan"))
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
        table.add_row(
            f"[green]{index} ✓[/green]" if selected else str(index),
            candidate.name,
            candidate.archive_format or candidate.extension,
            candidate.source or "?",
            _size(candidate.size),
            str(candidate.score),
            ", ".join(candidate.reasons),
        )
    if ranked:
        console.print(table)

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
            warning_text.append("\n")
        console.print(Panel(warning_text, title="Needs attention", border_style="yellow"))

    console.print(Panel("[bold green]Plan complete.[/bold green]\nProvider quality claims remain provisional until the file is inspected.", border_style="green"))


def render_fetch_result(result: FetchResult) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job", result.job_id)
    table.add_row("Staging", str(result.staging_dir))
    table.add_row("Audio", str(result.audio.path))
    table.add_row("Audio size", _size(result.audio.actual_size))
    table.add_row("Signature", result.audio.signature)
    table.add_row("Actual codec", result.audio.actual_codec or "?")
    table.add_row("Actual quality", "lossless" if result.audio.actual_lossless is True else "lossy" if result.audio.actual_lossless is False else "unknown")
    if result.audio.bitrate_bps:
        table.add_row("Actual bitrate", f"{result.audio.bitrate_bps / 1000:.1f} kbps")
    table.add_row("Audio SHA-256", result.audio.sha256)
    if result.cover:
        table.add_row("Cover", str(result.cover.path))
    table.add_row("Report", str(result.report_path))

    title = "[bold yellow]STAGED + NEEDS ATTENTION[/bold yellow]" if result.warnings else "[bold green]STAGED + NORMALIZED + VERIFIED[/bold green]"
    console.print(Panel(table, title=title, border_style="yellow" if result.warnings else "green"))
    if result.warnings:
        console.print(Panel("\n".join(f"• {w}" for w in result.warnings), title="Quality cross-check", border_style="yellow"))
    console.print(Panel("[bold]Final library modified: NO[/bold]\nDownloaded media remains isolated in Mnemosyne staging.", border_style="cyan"))


def render_inspection(result: MetadataInspection) -> None:
    console.print(Panel.fit("[bold]Mnemosyne[/bold]\n[dim]Read-only staged metadata inspection[/dim]", border_style="cyan"))
    props = result.properties
    table = Table(title="Audio properties", show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("File", str(result.audio_path))
    table.add_row("Container", props.container)
    table.add_row("Codec", props.codec or "?")
    table.add_row("Duration", _duration(props.duration_seconds))
    table.add_row("Bitrate", f"{props.bitrate_bps / 1000:.1f} kbps" if props.bitrate_bps else "?")
    table.add_row("Sample rate", f"{props.sample_rate_hz} Hz" if props.sample_rate_hz else "?")
    table.add_row("Channels", str(props.channels) if props.channels else "?")
    table.add_row("Embedded artwork", str(result.embedded_artwork_count))
    table.add_row("Chapters", str(len(result.chapters)))
    console.print(table)

    tags = Table(title="Existing metadata")
    tags.add_column("Field")
    tags.add_column("Value")
    if result.existing_tags:
        for key in sorted(result.existing_tags):
            tags.add_row(key, " | ".join(result.existing_tags[key]))
    else:
        tags.add_row("[dim]none[/dim]", "[dim]No readable embedded tags found[/dim]")
    console.print(tags)

    proposed = Table(title="Proposed canonical metadata")
    proposed.add_column("Field")
    proposed.add_column("Current")
    proposed.add_column("Proposed")
    proposed.add_column("Action")
    change_map = {key: (old, new) for key, old, new in result.changes}
    for key, new_value in result.proposed_tags.items():
        if key in change_map:
            old, new = change_map[key]
            proposed.add_row(key, old if old is not None else "[dim]<missing>[/dim]", new, "[yellow]CHANGE[/yellow]")
        else:
            current = result.existing_tags.get(key, [new_value])[0]
            proposed.add_row(key, current, new_value, "[green]KEEP[/green]")
    console.print(proposed)

    console.print(Panel(f"[bold]Proposed tag changes: {len(result.changes)}[/bold]\n[bold green]No tags were written.[/bold green]\nThe staged audio and final media library are unchanged.", border_style="green"))


def render_comparison(result: ComparisonResult) -> None:
    console.print(Panel.fit("[bold]Mnemosyne[/bold]\n[dim]Actual candidate quality comparison[/dim]", border_style="cyan"))

    table = Table(title="Downloaded candidate comparison")
    table.add_column("Rank", justify="right")
    table.add_column("Source file")
    table.add_column("Archive label")
    table.add_column("Actual codec")
    table.add_column("Quality")
    table.add_column("Bitrate", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Score", justify="right")

    for index, compared in enumerate(result.candidates, start=1):
        actual = compared.actual
        quality = "lossless" if actual.lossless is True else "lossy" if actual.lossless is False else "unknown"
        bitrate = f"{actual.bitrate_bps / 1000:.1f} kbps" if actual.bitrate_bps else "?"
        rank = f"[green]{index} ✓[/green]" if compared is result.recommended else str(index)
        table.add_row(
            rank,
            compared.candidate.name,
            compared.candidate.archive_format or "?",
            actual.codec or "?",
            quality,
            bitrate,
            _size(compared.actual_size),
            str(compared.quality_score),
        )

    console.print(table)
    console.print(
        Panel(
            f"[bold]Recommended actual source:[/bold] {result.recommended.candidate.name}\n"
            f"[bold]Actual codec:[/bold] {result.recommended.actual.codec or '?'}\n"
            f"[bold]Comparison report:[/bold] {result.report_path}\n\n"
            "[bold green]Final library modified: NO[/bold green]",
            border_style="green",
        )
    )
