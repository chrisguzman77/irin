"""Emit every pydantic model in backend/app/contracts.py as an OpenAPI 3.1
components document, so openapi-typescript can generate
src/types/contracts.d.ts. This covers the WebSocket shapes (StateSnapshot,
WSMessage, AlarmState, ...) that the Pi's /openapi.json leaves out because
no HTTP route references them. Read-only: it imports contracts.py, never
edits it. Run through `npm run types:contracts` with the backend venv."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))

from pydantic import BaseModel  # noqa: E402
from pydantic.json_schema import models_json_schema  # noqa: E402

from app import contracts  # noqa: E402

models = [m for _, m in inspect.getmembers(contracts, inspect.isclass)
          if issubclass(m, BaseModel) and m is not BaseModel and m.__module__ == contracts.__name__]
_, schema = models_json_schema([(m, "serialization") for m in models],
                               ref_template="#/components/schemas/{model}")
doc = {
    "openapi": "3.1.0",
    "info": {"title": "Irin contracts.py (generated, do not edit)", "version": "0"},
    "paths": {},
    "components": {"schemas": schema.get("$defs", {})},
}
out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
text = json.dumps(doc, indent=1, sort_keys=True)
if out:
    out.write_text(text + "\n", encoding="utf-8")
else:
    print(text)
