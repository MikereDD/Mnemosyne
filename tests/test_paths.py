from pathlib import Path

from mnemosyne.models import MediaType
from mnemosyne.paths import canonical_destination


def test_audiobook_destination() -> None:
    result = canonical_destination(
        Path(r"C:\Users\Test\Downloads\Mnemosyne"),
        MediaType.AUDIOBOOK,
        "George Orwell",
        "Animal Farm",
        1945,
    )
    assert str(result).endswith(
        str(
            Path("Audiobooks")
            / "George Orwell"
            / "Audiobook"
            / "Animal Farm - George Orwell (1945)"
        )
    )
