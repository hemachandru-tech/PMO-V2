"""Writes the API's OpenAPI document for the web app (US-135).

The web app's Zod request schemas are tested against this snapshot, and a test here fails when the snapshot is out of
date, so a changed request model cannot silently drift from its form. Regenerate with `pnpm openapi` in `apps/web`
(or `uv run python -m tela_api.openapi_export apps/web/src/api/openapi.json` from the repo root).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from tela_api.main import create_app


def openapi_document() -> dict[str, Any]:
    doc = create_app().openapi()
    doc["info"].pop("version", None)  # the snapshot should not change on every release bump
    return doc


def render(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m tela_api.openapi_export <output.json>", file=sys.stderr)
        return 2
    Path(argv[1]).write_text(render(openapi_document()), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
