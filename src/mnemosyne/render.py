from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .adoption import AdoptionResult
from .batch import (
    BatchExecutionPreview,
    BatchFetchSummary,
    BatchPlanPreview,
    BatchPreview,
)
from .batch_lifecycle import BatchLifecyclePreview
from .batch_source_resolution import (
    BatchSourceResolutionPreview,
    BatchSourceResolutionSummary,
)
from .comparison import ComparisonResult
from .completion import CompletionPreview, CompletionResult
from .cleanup import CleanupPreview, CleanupResult
from .fetcher import FetchResult
from .inspection import MetadataInspection, MultiFileInspection
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




def render_batch_preview(preview: BatchPreview) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Fetch-list batch preview[/dim]",
            border_style="cyan",
        )
    )

    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Media", preview.media_type.value)
    summary.add_row("Queue", str(preview.queue_path))
    summary.add_row("Lines", str(preview.total_lines))
    summary.add_row("Ready items", str(preview.ready_count))
    summary.add_row("Duplicates", str(preview.duplicate_count))
    summary.add_row("Invalid", str(preview.invalid_count))
    summary.add_row("Comments", str(preview.comment_lines))
    summary.add_row("Blank", str(preview.blank_lines))
    console.print(summary)

    if preview.items:
        items = Table(title="Batch items")
        items.add_column("#", justify="right")
        items.add_column("Line", justify="right")
        items.add_column("Provider")
        items.add_column("Identifier")
        items.add_column("Canonical URL")
        for index, item in enumerate(preview.items, start=1):
            items.add_row(
                str(index),
                str(item.line_number),
                "Internet Archive",
                item.identifier,
                item.canonical_url,
            )
        console.print(items)

    issues = [*preview.duplicates, *preview.invalid]
    if issues:
        issue_table = Table(title="Queue issues")
        issue_table.add_column("Line", justify="right")
        issue_table.add_column("Kind")
        issue_table.add_column("Detail")
        issue_table.add_column("Source")
        for issue in sorted(issues, key=lambda value: value.line_number):
            style = "yellow" if issue.kind == "duplicate" else "red"
            issue_table.add_row(
                str(issue.line_number),
                f"[{style}]{issue.kind.upper()}[/{style}]",
                issue.detail,
                issue.source_text.strip(),
            )
        console.print(issue_table)

    if not preview.items:
        console.print("[yellow]No valid batch items are ready.[/yellow]")

    console.print(
        Panel(
            "Preview only. No network requests were made.\n"
            "Queue file modified: NO\n"
            "Downloads started: NO",
            border_style="yellow",
        )
    )



def render_batch_plan_preview(preview: BatchPlanPreview) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Batch acquisition plan resolution[/dim]",
            border_style="cyan",
        )
    )

    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Resolved", str(len(preview.items)))
    summary.add_row("Actionable", str(preview.actionable_count))
    summary.add_row("Blocked", str(preview.blocked_count))
    summary.add_row("Failed", str(preview.failed_count))
    console.print(summary)

    table = Table(title="Resolved batch plans")
    table.add_column("#", justify="right")
    table.add_column("Line", justify="right")
    table.add_column("Status")
    table.add_column("Title")
    table.add_column("Creator")
    table.add_column("Year")
    table.add_column("Year source")
    table.add_column("Edition")
    table.add_column("Files", justify="right")
    table.add_column("Warnings", justify="right")

    for index, item in enumerate(preview.items, start=1):
        if item.status == "actionable":
            status = "[green]ACTIONABLE[/green]"
        elif item.status == "blocked":
            status = "[yellow]BLOCKED[/yellow]"
        else:
            status = "[red]FAILED[/red]"

        table.add_row(
            str(index),
            str(item.line_number),
            status,
            item.title or "?",
            item.creator or "?",
            str(item.year) if item.year else "?",
            item.year_provenance,
            item.selected_edition or "?",
            str(item.audio_file_count),
            str(item.warning_count),
        )
    console.print(table)

    for item in preview.items:
        if item.error:
            console.print(
                f"[red]Line {item.line_number} {item.identifier}:[/red] {item.error}"
            )
            continue
        if item.warnings:
            console.print(
                f"[yellow]Line {item.line_number} {item.identifier} warnings:[/yellow]"
            )
            for warning in item.warnings:
                console.print(f"  • {warning}")
        if item.destination is not None:
            console.print(
                f"[dim]Line {item.line_number} destination:[/dim] {item.destination}"
            )

    console.print(
        Panel(
            "Plan resolution may read provider metadata over the network.\n"
            "Media downloads started: NO\n"
            "Queue file modified: NO\n"
            "Library modified: NO",
            border_style="yellow",
        )
    )

def render_batch_execution_preview(preview: BatchExecutionPreview) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Batch execution dry-run[/dim]",
            border_style="cyan",
        )
    )

    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Would execute", str(preview.execute_count))
    summary.add_row("Skip blocked", str(preview.blocked_count))
    summary.add_row("Skip failed", str(preview.failed_count))
    summary.add_row("Execution mode", "Sequential")
    summary.add_row("Failure policy", "Continue to next item")
    console.print(summary)

    table = Table(title="Execution sequence")
    table.add_column("Seq", justify="right")
    table.add_column("Line", justify="right")
    table.add_column("Action")
    table.add_column("Identifier")
    table.add_column("Title")
    table.add_column("Destination")

    for item in preview.items:
        if item.action == "execute":
            action = "[green]WOULD EXECUTE[/green]"
            sequence = str(item.sequence)
        elif item.action == "skip-blocked":
            action = "[yellow]SKIP BLOCKED[/yellow]"
            sequence = "—"
        else:
            action = "[red]SKIP FAILED[/red]"
            sequence = "—"

        table.add_row(
            sequence,
            str(item.line_number),
            action,
            item.identifier,
            item.title or "?",
            str(item.destination) if item.destination is not None else "—",
        )

    console.print(table)

    for item in preview.items:
        if item.reason:
            console.print(
                f"[dim]Line {item.line_number} {item.identifier}: "
                f"{item.reason}[/dim]"
            )

    console.print(
        Panel(
            "Dry-run only. Execution has NOT started.\n"
            "Processing order: sequential\n"
            "Failure policy: continue to next item\n"
            "Media downloads started: NO\n"
            "Staging modified: NO\n"
            "Queue file modified: NO\n"
            "Library modified: NO",
            border_style="yellow",
        )
    )


def render_batch_lifecycle_preview(preview: BatchLifecyclePreview) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Batch lifecycle plan[/dim]",
            border_style="cyan",
        )
    )

    table = Table(title="Audiobook lifecycle state")
    table.add_column("Line", justify="right")
    table.add_column("Status")
    table.add_column("Identifier")
    table.add_column("Job")
    table.add_column("Next / Detail")

    labels = {
        "blocked": "[yellow]BLOCKED[/yellow]",
        "plan-failed": "[red]PLAN FAILED[/red]",
        "retry-required": "[yellow]RETRY REQUIRED[/yellow]",
        "not-staged": "[dim]NOT STAGED[/dim]",
        "needs-attention": "[yellow]NEEDS ATTENTION[/yellow]",
        "compare-required": "[cyan]COMPARE REQUIRED[/cyan]",
        "ready-to-tag": "[cyan]READY TO TAG[/cyan]",
        "verify-readiness": "[cyan]VERIFY READINESS[/cyan]",
        "ready-to-place": "[green]READY TO PLACE[/green]",
        "ready-to-complete": "[green]READY TO COMPLETE[/green]",
        "complete": "[bold green]COMPLETE[/bold green]",
    }

    for item in preview.items:
        table.add_row(
            str(item.line_number),
            labels.get(item.status, item.status.upper()),
            item.identifier,
            item.job_id or "—",
            item.detail,
        )

    console.print(table)
    console.print(
        Panel(
            "Read-only lifecycle inspection.\n"
            "Downloads started: NO\n"
            "Staging modified: NO\n"
            "Library modified: NO\n"
            "Queue modified: NO",
            border_style="yellow",
        )
    )


def render_batch_source_resolution_preview(
    preview: BatchSourceResolutionPreview,
) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Batch source-resolution preview[/dim]",
            border_style="cyan",
        )
    )

    table = Table(title="Source resolution")
    table.add_column("Line", justify="right")
    table.add_column("Action")
    table.add_column("Identifier")
    table.add_column("Job")
    table.add_column("Detail")

    for item in preview.items:
        action = (
            "[cyan]WOULD RESOLVE[/cyan]"
            if item.status == "would-resolve"
            else "[dim]SKIP[/dim]"
        )
        table.add_row(
            str(item.line_number),
            action,
            item.identifier,
            item.job_id or "—",
            item.detail,
        )

    console.print(table)
    console.print(
        Panel(
            f"Eligible jobs: {preview.actionable_count}\n"
            "Dry-run only. No comparison downloads have started.\n"
            "Final library modified: NO\n"
            "Queue modified: NO",
            border_style="yellow",
        )
    )


def render_batch_source_resolution_summary(
    summary: BatchSourceResolutionSummary,
) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Batch source-resolution result[/dim]",
            border_style="cyan",
        )
    )

    console.print(f"Resolved  {summary.resolved_count}")
    console.print(f"Failed    {summary.failed_count}")
    console.print(f"Skipped   {summary.skipped_count}")

    table = Table(title="Source-resolution results")
    table.add_column("Line", justify="right")
    table.add_column("Status")
    table.add_column("Identifier")
    table.add_column("Recommended")
    table.add_column("Adopted staged path / Error")

    for item in summary.results:
        if item.status == "resolved":
            status = "[green]RESOLVED[/green]"
            detail = str(item.adopted_path or "—")
        elif item.status == "failed":
            status = "[red]FAILED[/red]"
            detail = item.error or "Unknown failure"
        else:
            status = "[dim]SKIPPED[/dim]"
            detail = "—"

        table.add_row(
            str(item.line_number),
            status,
            item.identifier,
            item.recommended_source or "—",
            detail,
        )

    console.print(table)
    console.print(
        Panel(
            "Comparison candidates may have been downloaded into staging.\n"
            "Winning source adoption is transactional with rollback evidence.\n"
            "Final library modified: NO\n"
            "Queue modified: NO",
            border_style="yellow",
        )
    )


def render_batch_fetch_summary(summary: BatchFetchSummary) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Batch fetch result[/dim]",
            border_style="cyan",
        )
    )

    counts = Table(show_header=False, box=None, pad_edge=False)
    counts.add_column(style="bold")
    counts.add_column()
    counts.add_row("Staged now", str(summary.staged_count))
    counts.add_row("Already staged", str(summary.already_staged_count))
    counts.add_row("Fetch failed", str(summary.failed_count))
    counts.add_row("Retry required", str(summary.retry_required_count))
    counts.add_row("Blocked", str(summary.blocked_count))
    counts.add_row("Plan failed", str(summary.skipped_failed_count))
    counts.add_row("State", str(summary.state_path))
    console.print(counts)

    table = Table(title="Batch fetch results")
    table.add_column("Line", justify="right")
    table.add_column("Status")
    table.add_column("Identifier")
    table.add_column("Job")
    table.add_column("Staging")
    table.add_column("Warnings", justify="right")

    for item in summary.items:
        if item.status == "staged":
            status = "[green]STAGED[/green]"
        elif item.status == "already-staged":
            status = "[cyan]ALREADY STAGED[/cyan]"
        elif item.status == "blocked":
            status = "[yellow]BLOCKED[/yellow]"
        elif item.status == "retry-required":
            status = "[yellow]RETRY REQUIRED[/yellow]"
        elif item.status == "skipped-failed":
            status = "[red]PLAN FAILED[/red]"
        else:
            status = "[red]FETCH FAILED[/red]"

        table.add_row(
            str(item.line_number),
            status,
            item.identifier,
            item.job_id or "—",
            str(item.staging_dir) if item.staging_dir is not None else "—",
            str(item.warning_count),
        )

    console.print(table)

    for item in summary.items:
        if item.error:
            console.print(
                f"[red]Line {item.line_number} {item.identifier}: "
                f"{item.error}[/red]"
            )

    console.print(
        Panel(
            "Batch fetch finished.\n"
            "Processing order: sequential\n"
            "Failure policy: continue to next item\n"
            "ACTIONABLE items may have modified staging: YES\n"
            "Metadata/tagging applied: NO\n"
            "Library modified: NO\n"
            "Queue file modified: NO\n"
            f"Durable batch state: {summary.state_path}\n"
            "Previously staged items re-downloaded: NO\n"
            "Failed items retried automatically: NO\n"
            "Queue auto-pruned: NO",
            border_style="yellow",
        )
    )


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
            warning_text.append("\n")
        console.print(Panel(warning_text, title="Needs attention", border_style="yellow"))

    console.print(
        Panel(
            "[bold green]Plan complete.[/bold green]\n"
            "Provider quality claims remain provisional until downloaded files are inspected.",
            border_style="green",
        )
    )



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
                "\n".join(f"• {w}" for w in result.warnings),
                title="Quality cross-check",
                border_style="yellow",
            )
        )

    console.print(
        Panel(
            "[bold]Final library modified: NO[/bold]\n"
            "Downloaded media remains isolated in Mnemosyne staging.",
            border_style="cyan",
        )
    )


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
    table.add_row("Bit depth", f"{props.bits_per_sample}-bit" if props.bits_per_sample else "?")
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


def render_multifile_inspection(result: MultiFileInspection) -> None:
    console.print(Panel.fit("[bold]Mnemosyne[/bold]\n[dim]Read-only multi-file staged inspection[/dim]", border_style="cyan"))

    summary=Table(show_header=False,box=None,pad_edge=False)
    summary.add_column(style="bold")
    summary.add_column()
    summary.add_row("Job",str(result.job_dir))
    summary.add_row("Audio files",str(len(result.entries)))
    summary.add_row("Total duration",_duration(result.total_duration_seconds))
    summary.add_row("Codecs",", ".join(result.codecs) if result.codecs else "?")
    summary.add_row("Sample rates",", ".join(f"{v} Hz" for v in result.sample_rates_hz) or "?")
    summary.add_row("Channels",", ".join(str(v) for v in result.channels) or "?")
    summary.add_row("Bit depths",", ".join(f"{v}-bit" for v in result.bits_per_sample) or "?")
    console.print(summary)

    files=Table(title="Staged audio inspection")
    files.add_column("#",justify="right")
    files.add_column("File")
    files.add_column("Codec")
    files.add_column("Duration",justify="right")
    files.add_column("Bitrate",justify="right")
    files.add_column("Rate",justify="right")
    files.add_column("Bits",justify="right")
    files.add_column("Ch",justify="right")
    files.add_column("Art",justify="right")
    files.add_column("Tag changes",justify="right")

    for entry in result.entries:
        props=entry.properties
        files.add_row(
            str(entry.index), entry.audio_path.name, props.codec or "?",
            _duration(props.duration_seconds),
            f"{props.bitrate_bps/1000:.1f} kbps" if props.bitrate_bps else "?",
            f"{props.sample_rate_hz} Hz" if props.sample_rate_hz else "?",
            str(props.bits_per_sample) if props.bits_per_sample else "?",
            str(props.channels) if props.channels else "?",
            str(entry.embedded_artwork_count), str(len(entry.changes)),
        )
    console.print(files)

    inconsistent=[]
    if len(result.codecs)>1: inconsistent.append(f"codecs: {', '.join(result.codecs)}")
    if len(result.sample_rates_hz)>1: inconsistent.append("sample rates: "+", ".join(str(v) for v in result.sample_rates_hz))
    if len(result.channels)>1: inconsistent.append("channel counts: "+", ".join(str(v) for v in result.channels))
    if len(result.bits_per_sample)>1: inconsistent.append("bit depths: "+", ".join(str(v) for v in result.bits_per_sample))

    if inconsistent:
        console.print(Panel("\n".join(f"• {item}" for item in inconsistent), title="Edition consistency needs attention", border_style="yellow"))
    else:
        console.print(Panel("[bold green]Edition properties are internally consistent.[/bold green]\n[bold]No tags were written.[/bold]\nStaged media and the final library are unchanged.", border_style="green"))


def render_comparison(result: ComparisonResult) -> None:
    console.print(
        Panel.fit(
            "[bold]Mnemosyne[/bold]\n[dim]Complete audio-edition quality comparison[/dim]",
            border_style="cyan",
        )
    )
    table = Table(title="Downloaded edition comparison")
    table.add_column("Rank", justify="right")
    table.add_column("Edition")
    table.add_column("Files", justify="right")
    table.add_column("Format")
    table.add_column("Actual codec")
    table.add_column("Quality")
    table.add_column("Median bitrate", justify="right")
    table.add_column("Total size", justify="right")
    table.add_column("Score", justify="right")

    for index, compared in enumerate(result.editions, start=1):
        actual = compared.representative_quality
        quality = (
            "lossless"
            if actual.lossless is True
            else "lossy"
            if actual.lossless is False
            else "mixed/unknown"
        )
        bitrate = (
            f"{actual.bitrate_bps / 1000:.1f} kbps"
            if actual.bitrate_bps
            else "?"
        )
        rank = f"[green]{index} ✓[/green]" if compared is result.recommended else str(index)
        table.add_row(
            rank,
            compared.edition.label,
            str(len(compared.files)),
            compared.edition.archive_format or compared.edition.extension,
            actual.codec or "?",
            quality,
            bitrate,
            _size(compared.actual_size),
            str(compared.quality_score),
        )

    console.print(table)
    console.print(
        Panel(
            f"[bold]Recommended complete edition:[/bold] {result.recommended.edition.label}\n"
            f"[bold]Files:[/bold] {len(result.recommended.files)}\n"
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


def render_multifile_tag_preview(preview) -> None:
    console.print(Panel.fit(
        "[bold]Mnemosyne[/bold]\n[dim]Whole-edition metadata preview[/dim]",
        border_style="cyan",
    ))
    table = Table(title=f"Whole-edition metadata ({len(preview.tracks)} files)")
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
        "Every file will be prepared and verified before canonical staged files are replaced.\n"
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
    if getattr(preview, "existing_destination_equivalent", False):
        table.add_row("Placement mode", "Verified existing destination")
    console.print(table)

    if getattr(preview, "existing_destination_equivalent", False):
        detail = (
            "[bold yellow]Preview only.[/bold yellow]\n"
            "The existing destination has been verified equivalent to the certified staged edition.\n"
            "Apply records placement provenance only; existing library media will not be rewritten."
        )
    else:
        detail = (
            "[bold yellow]Preview only.[/bold yellow]\n"
            "Apply copies the entire edition into a hidden sibling directory, "
            "verifies it, then commits with one directory rename."
        )

    console.print(Panel(
        detail,
        border_style="yellow",
    ))


def render_multifile_placement_result(result) -> None:
    existing = bool(getattr(result, "verified_existing_destination", False))

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Destination", str(result.destination))
    table.add_row("Audio files", str(len(result.audio_paths)))
    table.add_row("Edition SHA-256", result.edition_sha256)
    table.add_row("Cover", str(result.cover_path))
    table.add_row("Cover SHA-256", result.cover_sha256)
    table.add_row("Placement report", str(result.placement_report_path))
    if existing:
        table.add_row("Placement mode", "Verified existing destination")

    title = (
        "[bold green]EXISTING MULTI-FILE DESTINATION VERIFIED[/bold green]"
        if existing
        else "[bold green]MULTI-FILE PLACED + VERIFIED[/bold green]"
    )

    console.print(Panel(
        table,
        title=title,
        border_style="green",
    ))

    if existing:
        detail = (
            "[bold]Existing destination overwritten: NO[/bold]\n"
            "[bold]Existing destination equivalence verification: PASSED[/bold]\n"
            "[bold]Existing library media rewritten: NO[/bold]\n"
            "[bold]Final library modified: NO[/bold]\n"
            "[bold]Placement provenance updated: YES[/bold]"
        )
    else:
        detail = (
            "[bold]Existing destination overwritten: NO[/bold]\n"
            "[bold]Pre-commit whole-edition verification: PASSED[/bold]\n"
            "[bold]Post-placement whole-edition verification: PASSED[/bold]\n"
            "[bold]Final library modified: YES[/bold]"
        )

    console.print(Panel(
        detail,
        border_style="cyan",
    ))


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
