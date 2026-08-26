from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .adoption import AdoptionResult
from .comparison import ComparisonResult
from .completion import CompletionPreview, CompletionResult
from .cleanup import CleanupPreview, CleanupResult
from .fetcher import FetchResult
from .inspection import MetadataInspection
from .models import AcquisitionPlan, CandidateKind
from .placement import PlacementPreview, PlacementResult
from .readiness import ReadinessResult
from .prune import PrunePreview, PruneResult
from .tagging import TaggingPreview, TaggingResult

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


def render_adoption(result: AdoptionResult) -> None:
    quality = result.adopted_quality
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Canonical staged source", str(result.canonical_path))
    table.add_row("Comparison winner", str(result.source_comparison_path))
    table.add_row("Rollback backup", str(result.backup_path))
    table.add_row("SHA-256", result.adopted_sha256)
    table.add_row("Actual codec", quality.codec or "?")
    table.add_row("Actual quality", "lossless" if quality.lossless is True else "lossy" if quality.lossless is False else "unknown")
    if quality.bitrate_bps:
        table.add_row("Actual bitrate", f"{quality.bitrate_bps / 1000:.1f} kbps")
    if quality.sample_rate_hz:
        table.add_row("Sample rate", f"{quality.sample_rate_hz} Hz")
    if quality.channels:
        table.add_row("Channels", str(quality.channels))
    table.add_row("Updated report", str(result.report_path))

    console.print(Panel(table, title="[bold green]STAGED SOURCE RESOLVED[/bold green]", border_style="green"))
    console.print(Panel("[bold]Rollback preserved: YES[/bold]\n[bold]Final library modified: NO[/bold]\nThe chosen source is adopted only inside staging.", border_style="cyan"))


def render_tagging_preview(preview: TaggingPreview) -> None:
    console.print(Panel.fit("[bold]Mnemosyne[/bold]\n[dim]Transactional metadata normalization preview[/dim]", border_style="cyan"))

    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Audio", str(preview.audio_path))
    summary.add_row("Cover", str(preview.cover_path) if preview.cover_path else "[yellow]none[/yellow]")
    console.print(summary)

    table = Table(title="Canonical metadata to write")
    table.add_column("Field")
    table.add_column("Value")
    for key, value in preview.proposed_tags.items():
        table.add_row(key, value)
    console.print(table)

    console.print(
        Panel(
            "[bold yellow]Preview only.[/bold yellow]\n"
            "No tags, artwork, staged media, rollback data, or final-library files were changed.\n"
            "Re-run with [bold]--apply[/bold] to perform the transactional staged mutation.",
            border_style="yellow",
        )
    )


def render_tagging_result(result: TaggingResult) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Staged audio", str(result.audio_path))
    table.add_row("Rollback audio", str(result.rollback_path))
    table.add_row("Pre-tag SHA-256", result.pre_tag_sha256)
    table.add_row("Post-tag SHA-256", result.post_tag_sha256)
    table.add_row("Embedded cover", "YES" if result.embedded_cover else "NO")
    if result.embedded_cover_sha256:
        table.add_row("Cover SHA-256", result.embedded_cover_sha256)
    table.add_row("Updated report", str(result.report_path))

    console.print(
        Panel(
            table,
            title="[bold green]METADATA NORMALIZED + VERIFIED[/bold green]",
            border_style="green",
        )
    )

    tags = Table(title="Verified canonical metadata")
    tags.add_column("Field")
    tags.add_column("Value")
    for key, value in result.written_tags.items():
        tags.add_row(key, value)
    console.print(tags)

    console.print(
        Panel(
            "[bold]Rollback preserved: YES[/bold]\n"
            "[bold]Post-write verification: PASSED[/bold]\n"
            "[bold]Final library modified: NO[/bold]\n"
            "The normalized media remains isolated in Mnemosyne staging.",
            border_style="cyan",
        )
    )



def render_readiness(result: ReadinessResult) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Final staged readiness verification[/dim]",
            border_style="cyan",
        )
    )

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

    quality = result.actual_quality
    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Audio", str(result.audio_path))
    summary.add_row("Audio SHA-256", result.audio_sha256)
    summary.add_row("Cover", str(result.cover_path) if result.cover_path else "missing")
    summary.add_row("Cover SHA-256", result.cover_sha256 or "missing")
    summary.add_row("Actual codec", quality.codec or "?")
    summary.add_row(
        "Actual quality",
        "lossless" if quality.lossless is True else "lossy" if quality.lossless is False else "unknown",
    )
    summary.add_row("Readiness report", str(result.readiness_report_path))
    console.print(summary)

    if result.ready:
        console.print(
            Panel(
                "[bold green]READY FOR PLACEMENT[/bold green]\n"
                "Every current staging gate passed.\n"
                "[bold]Final library modified: NO[/bold]\n"
                "A separate explicit placement transaction is still required.",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]NOT READY FOR PLACEMENT[/bold red]\n"
                "One or more staging gates failed.\n"
                "[bold]Final library modified: NO[/bold]",
                border_style="red",
            )
        )



def render_placement_preview(preview: PlacementPreview) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Transactional final-library placement preview[/dim]",
            border_style="cyan",
        )
    )

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Destination", str(preview.destination))
    table.add_row("Audio source", str(preview.audio_source))
    table.add_row("Audio destination", str(preview.audio_destination))
    table.add_row("Audio SHA-256", preview.audio_sha256)
    table.add_row("Cover source", str(preview.cover_source))
    table.add_row("Cover destination", str(preview.cover_destination))
    table.add_row("Cover SHA-256", preview.cover_sha256)
    console.print(table)

    console.print(
        Panel(
            "[bold yellow]Preview only.[/bold yellow]\n"
            "Readiness certification and staged hashes were revalidated.\n"
            "No final-library directories or files were created.\n"
            "Existing destinations will never be overwritten or merged.\n"
            "Re-run with [bold]--apply[/bold] to perform the transaction.",
            border_style="yellow",
        )
    )


def render_placement_result(result: PlacementResult) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Final destination", str(result.destination))
    table.add_row("Audio", str(result.audio_path))
    table.add_row("Audio SHA-256", result.audio_sha256)
    table.add_row("Cover", str(result.cover_path))
    table.add_row("Cover SHA-256", result.cover_sha256)
    table.add_row("Placement report", str(result.placement_report_path))
    table.add_row("Updated fetch report", str(result.fetch_report_path))

    console.print(
        Panel(
            table,
            title="[bold green]PLACED + VERIFIED[/bold green]",
            border_style="green",
        )
    )

    console.print(
        Panel(
            "[bold green]Final library modified: YES[/bold green]\n"
            "No existing destination was overwritten.\n"
            "Pre-commit copy verification: PASSED\n"
            "Post-placement verification: PASSED\n"
            "The staged acquisition remains available as provenance and rollback evidence.",
            border_style="green",
        )
    )



def render_completion_preview(preview: CompletionPreview) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Final acquisition completion preview[/dim]",
            border_style="cyan",
        )
    )

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

    console.print(
        Panel(
            (
                "[bold green]READY TO COMPLETE[/bold green]\n"
                if preview.ready_to_complete
                else "[bold red]NOT READY TO COMPLETE[/bold red]\n"
            )
            + "[bold]Preview only.[/bold]\n"
            "No staging evidence was deleted.\n"
            "No fetch-list entry was pruned.\n"
            "No final-library media was modified.",
            border_style="green" if preview.ready_to_complete else "red",
        )
    )


def render_completion_result(result: CompletionResult) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Completed at", result.completed_at)
    table.add_row("Destination", str(result.destination))
    table.add_row("Audio", str(result.audio_path))
    table.add_row("Cover", str(result.cover_path))
    table.add_row("Completion report", str(result.completion_report_path))
    table.add_row("Updated fetch report", str(result.fetch_report_path))
    table.add_row("Staging retained", "YES")
    table.add_row("Fetch list pruned", "NO")

    console.print(
        Panel(
            table,
            title="[bold green]ACQUISITION COMPLETE[/bold green]",
            border_style="green",
        )
    )

    console.print(
        Panel(
            "[bold]Final library verification: PASSED[/bold]\n"
            "[bold]Lifecycle status: COMPLETE[/bold]\n"
            "Staging/provenance remains retained.\n"
            "Cleanup and fetch-list pruning require separate explicit operations.",
            border_style="green",
        )
    )



def render_cleanup_preview(preview: CleanupPreview) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Completed staging cleanup preview[/dim]",
            border_style="cyan",
        )
    )

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job ID", preview.job_id)
    table.add_row("Staging job", str(preview.job_dir))
    table.add_row("Final destination", str(preview.final_destination))
    table.add_row("Durable receipt", str(preview.receipt_path))
    table.add_row("Staging files", str(preview.file_count))
    table.add_row("Staging size", _size(preview.staging_size_bytes))
    console.print(table)

    console.print(
        Panel(
            "[bold yellow]Preview only.[/bold yellow]\n"
            "Final audio and cover hashes were reverified.\n"
            "No staging evidence was deleted.\n"
            "No fetch-list entry was pruned.\n\n"
            "Deletion requires both:\n"
            "[bold]--apply[/bold]\n"
            f"[bold]--confirm {preview.job_id}[/bold]",
            border_style="yellow",
        )
    )


def render_cleanup_result(result: CleanupResult) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job ID", result.job_id)
    table.add_row("Removed staging", str(result.removed_job_dir))
    table.add_row("Archived receipt", str(result.receipt_path))
    table.add_row("Final destination", str(result.final_destination))
    table.add_row("Final audio SHA-256", result.final_audio_sha256)
    table.add_row("Final cover SHA-256", result.final_cover_sha256)
    table.add_row("Removed files", str(result.file_count))
    table.add_row("Removed size", _size(result.staging_size_bytes))
    table.add_row("Fetch list pruned", "NO")

    console.print(
        Panel(
            table,
            title="[bold green]COMPLETED STAGING CLEANED[/bold green]",
            border_style="green",
        )
    )

    console.print(
        Panel(
            "[bold]Durable completion receipt: VERIFIED[/bold]\n"
            "[bold]Final library media: UNCHANGED + VERIFIED[/bold]\n"
            "[bold]Retained staging job: REMOVED[/bold]\n"
            "Fetch-list pruning remains a separate explicit workflow.",
            border_style="green",
        )
    )



def render_prune_preview(preview: PrunePreview) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Fetch-list pruning preview[/dim]",
            border_style="cyan",
        )
    )

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job ID", preview.job_id)
    table.add_row("Media type", preview.media_type)
    table.add_row("Source URL", preview.source_url)
    table.add_row("Fetch list", str(preview.list_path))
    table.add_row(
        "Exact matching lines",
        ", ".join(str(line) for line in preview.matching_lines),
    )
    table.add_row("Backup path", str(preview.backup_path))
    console.print(table)

    console.print(
        Panel(
            "[bold yellow]Preview only.[/bold yellow]\n"
            "No fetch-list entry was removed.\n"
            "No backup was created.\n"
            "Only exact active URL matches are eligible.\n\n"
            "Apply requires:\n"
            f"[bold]--apply --confirm-url \"{preview.source_url}\"[/bold]",
            border_style="yellow",
        )
    )


def render_prune_result(result: PruneResult) -> None:
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Job ID", result.job_id)
    table.add_row("Source URL", result.source_url)
    table.add_row("Fetch list", str(result.list_path))
    table.add_row("Backup", str(result.backup_path))
    table.add_row("Entries removed", str(result.removed_count))
    table.add_row("Remaining lines", str(result.remaining_lines))
    table.add_row("Updated receipt", str(result.receipt_path))

    console.print(
        Panel(
            table,
            title="[bold green]FETCH LIST PRUNED + VERIFIED[/bold green]",
            border_style="green",
        )
    )

    console.print(
        Panel(
            "[bold]Pre-mutation backup: VERIFIED[/bold]\n"
            "[bold]Atomic rewrite: PASSED[/bold]\n"
            "[bold]Post-rewrite exact-match check: PASSED[/bold]\n"
            "Final-library media was not modified.",
            border_style="green",
        )
    )
