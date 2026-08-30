from __future__ import annotations

import inspect

from mnemosyne.adoption import adopt_latest_recommended_edition
from mnemosyne.cli import adopt_command
from mnemosyne.comparison import ComparedCandidate


def test_compared_candidate_preserves_provider_and_signature_provenance() -> None:
    fields = ComparedCandidate.__dataclass_fields__
    assert "expected_size" in fields
    assert "signature" in fields


def test_whole_edition_adoption_uses_provider_expected_size() -> None:
    source = inspect.getsource(adopt_latest_recommended_edition)
    assert 'source_entry.get("expectedSize")' in source
    assert '"expectedSize": source_entry.get("actualSize")' not in source


def test_standalone_adopt_command_uses_whole_edition_adopter() -> None:
    source = inspect.getsource(adopt_command)
    assert "adopt_latest_recommended_edition" in source
    assert "adopt_latest_recommended_source" not in source
