"""PDF renderer (US-096; FEAT-045; technical_document: Playwright headless Chromium rendering a print route).

Chromium opens the web app's print-only route with the payload injected before any script runs, so the page needs no
sign-in and makes no API calls. Every other request is refused: the page can load the app's own assets and nothing
else. ``page.pdf`` draws the footer on every page — filters, scope, data date, version and export time (REQ-107) —
with page numbers. Charts are the same ECharts the viewer draws, as SVG.
"""

from __future__ import annotations

import html
import json
import os
from typing import Any
from urllib.parse import urlsplit

from tela_worker.export import renderer

PRINT_PATH = "/print/export"
CHART_ROWS = 500  # more marks than this are unreadable on paper; tables print 40 rows (PRINT_TABLE_ROWS)
TIMEOUT_MS = 60_000


def web_url() -> str:
    return os.environ.get("TELA_WEB_URL", "http://web:8080").rstrip("/")


def slim(payload: dict[str, Any]) -> dict[str, Any]:
    """What the print page needs: rows capped, definitions dropped (they belong to the Excel export)."""
    out = {**payload, "tabs": []}
    for tab in payload["tabs"]:
        sections = []
        for s in tab["sections"]:
            items = []
            for i in s["items"]:
                if i["kind"] == "widget":
                    i = {k: v for k, v in i.items() if k not in ("definition", "notes")}
                    if "rows" in i:
                        i["rows"] = i["rows"][:CHART_ROWS]
                items.append(i)
            sections.append({**s, "items": items})
        out["tabs"].append({**tab, "sections": sections})
    return out


def footer_template(payload: dict[str, Any]) -> str:
    text = html.escape(payload.get("footer", ""))
    return (
        '<div style="width:100%;font-family:system-ui,-apple-system,\'Segoe UI\',Roboto,Arial,sans-serif;'
            'font-size:7.5px;color:#4b5563;'
        'padding:0 10mm;display:flex;justify-content:space-between;gap:12px">'
        f'<span>{text}</span>'
        '<span style="white-space:nowrap">Page <span class="pageNumber"></span> of '
        '<span class="totalPages"></span></span>'
        "</div>"
    )


@renderer("pdf")
def render_pdf(payload: dict[str, Any]) -> bytes:
    from playwright.sync_api import Route, sync_playwright

    base = web_url()
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base))
    data = json.dumps(slim(payload), default=str).replace("</", "<\\/")

    def only_own_assets(route: Route) -> None:
        if route.request.url.startswith(origin + "/"):
            route.continue_()
        else:
            route.abort()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-dev-shm-usage", "--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1240, "height": 877}, java_script_enabled=True)
            page.route("**/*", only_own_assets)
            page.add_init_script(f"window.__TELA_EXPORT__ = {data};")
            page.goto(base + PRINT_PATH, wait_until="networkidle", timeout=TIMEOUT_MS)
            page.wait_for_function("window.__TELA_PRINT_READY__ === true", timeout=TIMEOUT_MS)
            page.emulate_media(media="print")
            pdf: bytes = page.pdf(format="A4", landscape=True, print_background=True, display_header_footer=True,
                                  header_template="<span></span>", footer_template=footer_template(payload),
                                  margin={"top": "10mm", "bottom": "14mm", "left": "10mm", "right": "10mm"})
        finally:
            browser.close()
    return pdf
