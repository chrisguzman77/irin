"""Write tests/fixtures/<name>.json from tests/examples.py. Run from backend/:
    .venv/bin/python -m tests.fixtures.generate
Re-run whenever contracts.py changes (an announced commit)."""

from __future__ import annotations

from pathlib import Path

from tests.examples import examples

OUT = Path(__file__).resolve().parent


def main() -> None:
    for name, model in examples().items():
        (OUT / f"{name}.json").write_text(model.model_dump_json(indent=2) + "\n")
        print(name)


if __name__ == "__main__":
    main()
