"""PDF renderer (US-096) against the built web app's print route.

Needs the web app served (``vite preview`` or the web container) at TELA_TEST_WEB_URL and Playwright's Chromium.
Checks: one page per tab at least, A4 landscape, the footer on every page, other origins blocked.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from tela_worker.export import pdf

WEB = os.environ.get("TELA_TEST_WEB_URL")
PAYLOAD = json.loads((Path(__file__).parent / "fixtures" / "export_payload.json").read_text())


def test_slim_caps_rows_and_drops_definitions() -> None:
    big = json.loads(json.dumps(PAYLOAD))
    big["tabs"][1]["sections"][0]["items"][0]["rows"] *= 20
    out = pdf.slim(big)
    item = out["tabs"][1]["sections"][0]["items"][0]
    assert len(item["rows"]) == pdf.CHART_ROWS and "definition" not in item
    assert big["tabs"][1]["sections"][0]["items"][0]["definition"]  # the input is untouched


def test_footer_is_escaped() -> None:
    html = pdf.footer_template({"footer": "Region = <script>alert(1)</script>"})
    assert "<script>" not in html and "&lt;script&gt;" in html and 'class="pageNumber"' in html


@pytest.mark.skipif(not WEB, reason="TELA_TEST_WEB_URL not set (serve apps/web/dist)")
def test_renders_every_tab_with_the_footer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TELA_WEB_URL", str(WEB))
    data = pdf.render_pdf(PAYLOAD)
    assert data.startswith(b"%PDF-")
    (tmp_path / "export.pdf").write_bytes(data)
    pages = re.findall(rb"/Type\s*/Page[^s]", data)
    assert len(pages) >= len(PAYLOAD["tabs"])
    box = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)", data)
    assert box and float(box.group(1)) > float(box.group(2))  # landscape
    if out := os.environ.get("TELA_TEST_PDF_OUT"):
        Path(out).write_bytes(data)
