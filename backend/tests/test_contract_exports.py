from pathlib import Path

from scripts.export_contracts import generate
from scripts.export_examples import generate as generate_examples


def test_committed_schemas_and_examples_match_models():
    root = Path(__file__).resolve().parents[2] / "contracts"
    for name, text in generate().items():
        assert (root / name).read_text(encoding="utf-8") == text
    assert (root / "examples.json").read_text(encoding="utf-8") == generate_examples()
