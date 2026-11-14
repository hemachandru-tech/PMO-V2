"""The web app's OpenAPI snapshot matches the API (US-135): its Zod form schemas are tested against that snapshot."""

from __future__ import annotations

from pathlib import Path

from tela_api.openapi_export import openapi_document, render

SNAPSHOT = Path(__file__).resolve().parents[3] / "apps" / "web" / "src" / "api" / "openapi.json"


def test_web_openapi_snapshot_is_current() -> None:
    assert SNAPSHOT.exists(), "Run `pnpm openapi` in apps/web to write the snapshot."
    assert SNAPSHOT.read_text(encoding="utf-8") == render(openapi_document()), (
        "apps/web/src/api/openapi.json is out of date: run `pnpm openapi` in apps/web and re-run the web tests."
    )
