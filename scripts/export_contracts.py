"""Run from repository root: python -m scripts.export_contracts."""
import inspect
import json
from pathlib import Path

from pydantic.json_schema import models_json_schema

from backend.app import contracts
from backend.app.main import app


def generate() -> dict[str, str]:
    models = [(c, "validation") for _, c in inspect.getmembers(contracts, inspect.isclass)
              if issubclass(c, contracts.Contract) and c is not contracts.Contract]
    _, schema = models_json_schema(models, title="CQA shared contracts v1")
    return {"models.schema.json": json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            "openapi.json": json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"}


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "contracts"
    target.mkdir(exist_ok=True)
    for name, content in generate().items():
        (target / name).write_text(content, encoding="utf-8")
