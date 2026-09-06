from __future__ import annotations

import re

from typer.testing import CliRunner

from mnemosyne.cli import app


runner = CliRunner()


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _normalized(text: str) -> str:
    """Strip ANSI styling and collapse wrapping so help tests assert meaning."""
    without_ansi = _ANSI_ESCAPE_RE.sub("", text)
    return re.sub(r"\s+", " ", without_ansi).strip()


def test_top_level_help_explains_both_workflows_and_safety() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0

    output = _normalized(result.output)
    assert "safety-first media acquisition, organization, normalization" in output
    assert "Acquisition workflow:" in output
    assert (
        "Discover -> Identify -> Preview/Plan -> Fetch -> Normalize -> "
        "Verify -> Place -> Complete"
    ) in output
    assert "Existing-library workflow:" in output
    assert (
        "Scan -> Identify -> Classify -> Preview/Plan -> Normalize -> "
        "Rename/Move -> Verify"
    ) in output
    assert "preview before mutation" in output
    assert "silently guessing or overwriting valuable media" in output


def test_batch_help_distinguishes_preview_planning_and_apply() -> None:
    result = runner.invoke(app, ["batch", "--help"])

    assert result.exit_code == 0

    output = _normalized(result.output)
    assert "only parses and previews the queue" in output
    assert "--resolve-plans" in output
    assert "does not download media" in output
    assert "--execution-plan" in output
    assert "It remains read-only" in output
    assert "--apply" in output
    assert "isolated staging jobs only" in output


def test_batch_help_protects_mutation_and_recovery_safety_language() -> None:
    result = runner.invoke(app, ["batch", "--help"])

    assert result.exit_code == 0

    output = _normalized(result.output)
    assert (
        "does not automatically tag, place, complete, clean staging, or prune "
        "queue entries"
    ) in output
    assert "--retry-failed" in output
    assert "Valid previously staged items are not downloaded again" in output
    assert "--lifecycle-plan" in output
    assert "read-only" in output
    assert "Provider-derived canonical dates remain provisional" in output
    assert "Resolve BLOCKED items rather than bypassing their safety warnings" in output