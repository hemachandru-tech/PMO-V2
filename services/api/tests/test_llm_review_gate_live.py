"""US-246 final gate on a live model: the AG-05 report plan and AG-06 chat edit gold sets with LLM review off and on.

Skipped unless ``TELA_LIVE_GATEWAY_URL`` names a running LLM gateway that accepts the test service key (run it from
the checkout against the test database; see implement_log EXEC-168). The tenant uses Ollama with
``TELA_LIVE_MODEL`` (default qwen3:8b). Each set is scored off (rules only) and on (one review call a request):
right, wrong, and confident-wrong — for report plans, a case the rules had right that the shown AI plan gets wrong;
for chat edits, a preview holding an op nobody asked for. Results go to ``TELA_LIVE_GATE_OUT`` (JSON) when set.
Nothing is asserted beyond the runs completing: the keep-or-drop decision is DEC-28 point 3's, recorded in the log.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, h, setup_world
from test_authoring_api import world  # noqa: F401 - the TPG world with Ask pins
from test_canvas_gold_api import gold_canvas

from tela_api.content import canvas_gold, plan_gold
from tela_api.core.settings import get_settings
from tela_api.llm.client import GatewayClient

URL = os.environ.get("TELA_LIVE_GATEWAY_URL")
MODEL = os.environ.get("TELA_LIVE_MODEL", "qwen3:8b")
pytestmark = [needs_db, pytest.mark.integration,
              pytest.mark.skipif(not URL, reason="TELA_LIVE_GATEWAY_URL not set (live model gate)")]


def tenant(conn: psycopg.Connection, w: World, *, review: bool) -> None:
    conn.execute("INSERT INTO ops.llm_settings (tenant_id, provider, models, llm_review) VALUES (%s, 'ollama', %s, %s) "
                 "ON CONFLICT (tenant_id) DO UPDATE SET provider = 'ollama', models = EXCLUDED.models, "
                 "llm_review = EXCLUDED.llm_review",
                 (w.tenant_id, json.dumps({"planner": MODEL, "narrator": MODEL, "advisor": MODEL}), review))


def live(stack: httpx.AsyncClient, signing_keys: dict[str, Any]) -> None:
    assert URL is not None
    stack.app.state.gateway = GatewayClient(URL, signing_keys["service"].encode(),  # type: ignore[attr-defined]
                                            timeout_s=120)


def record(name: str, result: dict[str, Any]) -> None:
    print(f"\nUS-246 {name}: " + json.dumps({k: v for k, v in result.items() if k != "cases"}))
    if out := os.environ.get("TELA_LIVE_GATE_OUT"):
        path = Path(out)
        data = json.loads(path.read_text()) if path.exists() else {}
        data[name] = result
        path.write_text(json.dumps(data, indent=2))


def summary(rows: dict[str, dict[str, Any]], seconds: list[float]) -> dict[str, Any]:
    ordered = sorted(seconds)
    return {"right": sum(not r["misses"] for r in rows.values()),
            "confident_wrong": sum(bool(r.get("confident_wrong")) for r in rows.values()),
            "verdicts": {v: sum(r.get("verdict") == v for r in rows.values())
                         for v in ("off", "confirm", "correct", "ask", "fallback")},
            "p50_s": round(ordered[len(ordered) // 2], 1) if ordered else 0,
            "p95_s": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1) if ordered else 0,
            "cases": rows}


@pytest.fixture
async def plan_world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                     tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


async def test_report_plan_gate(stack: httpx.AsyncClient, plan_world: World, owner_conn: psycopg.Connection,
                                signing_keys: dict[str, Any], make_token: Callable[..., str],
                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", [])  # measured with AG-05 back in review
    cases = plan_gold.load()
    live(stack, signing_keys)
    runs: dict[str, dict[str, Any]] = {}
    for mode in ("off", "on"):
        tenant(owner_conn, plan_world, review=mode == "on")
        rows: dict[str, dict[str, Any]] = {}
        seconds = []
        for case in cases:
            began = time.monotonic()
            token = make_token("creator-1", groups=["TELA-Creators"], name="Cleo Creator")  # a run outlasts 5 min
            resp = await stack.post("/api/v1/reports/plan", headers=h(token), json={"request": case["request"]})
            seconds.append(time.monotonic() - began)
            assert resp.status_code == 200, (case["id"], resp.text)
            got = resp.json()
            misses = plan_gold.score(got, case["expect"])
            review = got.get("review") or {}
            rows[case["id"]] = {"misses": misses, "verdict": review.get("verdict", "off"),
                                "showing": review.get("showing")}
        runs[mode] = summary(rows, seconds)
    for cid, row in runs["on"]["cases"].items():
        row["confident_wrong"] = bool(row["misses"]) and not runs["off"]["cases"][cid]["misses"]
    runs["on"]["confident_wrong"] = sum(r["confident_wrong"] for r in runs["on"]["cases"].values())
    record("report_plan", {"model": MODEL, **{m: runs[m] for m in runs}})


async def test_canvas_edit_gate(stack: httpx.AsyncClient, world: World,  # noqa: F811
                                owner_conn: psycopg.Connection, signing_keys: dict[str, Any],
                                make_token: Callable[..., str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "llm_review_off_agents", [])  # measured with AG-06 back in review
    gold = canvas_gold.load()
    def fresh() -> str:  # a run outlasts the 5-minute test token
        return make_token("creator-1", groups=["TELA-Creators"], name="Cleo Creator")
    tenant(owner_conn, world, review=False)
    rid, before = await gold_canvas(stack, world)  # pinned with review off: Ask isn't what is measured here
    live(stack, signing_keys)
    runs: dict[str, dict[str, Any]] = {}
    for mode in ("off", "on"):
        tenant(owner_conn, world, review=mode == "on")
        rows: dict[str, dict[str, Any]] = {}
        seconds = []
        for case in gold["cases"]:
            began = time.monotonic()
            resp = await stack.post(f"/api/v1/reports/{rid}/chat-edit", headers=h(fresh()),
                                    json={"instruction": case["instruction"]})
            seconds.append(time.monotonic() - began)
            assert resp.status_code == 200, (case["id"], resp.text)
            got = resp.json()
            rows[case["id"]] = {"misses": canvas_gold.score(got, case["expect"], before),
                                "confident_wrong": canvas_gold.wrong(got, case["expect"], before),
                                "verdict": (got.get("review") or {}).get("verdict", "off")}
        runs[mode] = summary(rows, seconds)
    record("canvas_edit", {"model": MODEL, **runs})
