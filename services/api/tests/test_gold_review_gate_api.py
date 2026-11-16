"""Gold gate for LLM review (ENH-026, US-246; DEC-28 point 3), with a scripted AG-03.

Acceptance: ``tela-admin gold-run --review on|off`` plans the same examples with review forced either way, whatever
the tenant's switch says; each result records its model calls; ``tela-admin gold-gate`` compares the two runs and says
whether the default may stay on.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h
from test_ask_review_api import review_on, reviewer, reviews
from test_gold_api import GOLD, gold, run_queued, world  # noqa: F401 - the gold world fixture
from test_knowledge_api import bag, index_model

pytestmark = [needs_db, pytest.mark.integration]

# A paraphrase retrieval ranks Open projects low on, so it is asked with review off. US-202 (pack 2.2.0) moved "still
# running" over the threshold (Open projects now second in retrieval, answered right), so "underway" took its place.
QUESTIONS = (("How many open projects are there?", ["baseline"]),
             ("How many projects are underway?", ["paraphrase"]))


def cli(*argv: str) -> dict[str, Any]:
    from tela_api import cli as cli_mod

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli_mod.main(list(argv))
    return dict(json.loads(out.getvalue()))


def model_calls(fake: FakeGateway) -> int:
    return sum(c["agent"] != "embed" for c in fake.calls)


async def test_review_forced_off_and_on_then_gated(stack: httpx.AsyncClient, world: World,
                                                   owner_conn: psycopg.Connection, signing_keys: dict[str, Any],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    admin = world.tokens["admin"]
    await index_model(stack, world, signing_keys)
    for question, tags in QUESTIONS:
        resp = await stack.post(GOLD, headers=h(admin), json=gold(world, question,
                                                                  {"measure": {"ref": "measure:open_projects"}},
                                                                  tags=tags))
        assert resp.status_code == 201, resp.text
    admin_id = resp.json()["verified_by"]
    slug = owner_conn.execute("SELECT slug FROM iam.tenants WHERE id = %s", (world.tenant_id,)).fetchone()[0]
    monkeypatch.setenv("TELA_MIGRATE_DATABASE_URL", os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    review_on(owner_conn, world)  # the tenant's switch is on: the off run must still make no review call

    runs: dict[str, dict[str, Any]] = {}
    for side in ("off", "on"):
        if side == "on":
            review_on(owner_conn, world, on=False)  # ... and off: the on run must still review
        fake = FakeGateway({"AG-03": reviewer({"verdict": "confirm", "reason": "open projects"})}, embedder=bag)
        stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
        queued = cli("gold-run", "--tenant", slug, "--plan-only", "--review", side, "--run-as", admin_id)
        assert queued["review"] is (side == "on") and queued["examples"] == 2
        summary = await run_queued(stack, world, signing_keys, queued["run_id"])
        assert summary["status"] == "done" and summary["total"] == 2
        run = (await stack.get(f"/api/v1/knowledge/eval-runs/{queued['run_id']}", headers=h(admin))).json()
        assert run["review"] is (side == "on")
        assert all(r["llm_calls"] is not None for r in run["results"])
        assert sum(r["llm_calls"] for r in run["results"]) == model_calls(fake)  # every model call counted
        sent = reviews(fake)
        if side == "off":
            assert sent == []
        else:
            assert len(sent) == 2  # one review per rules-planned question
            assert all(r["llm_calls"] >= 1 for r in run["results"])
        orig = run["by_set"]["original"]
        assert orig["latency_ms"]["p95"] >= orig["latency_ms"]["p50"]
        assert orig["llm_calls"]["total"] == sum(r["llm_calls"] for r in run["results"]
                                                 if "paraphrase" not in r["tags"])
        assert cli("gold-report", "--tenant", slug, "--run", queued["run_id"])["review"] is (side == "on")
        runs[side] = run

    out = cli("gold-gate", "--tenant", slug, "--off", runs["off"]["id"], "--on", runs["on"]["id"])
    assert out["off"]["total"] == out["on"]["total"] == 2
    # The paraphrase is asked at 0.55 with review off; the confirm lifts it over the threshold and it is answered.
    assert out["off"]["by_set"]["paraphrase"]["clarified"] == 1 and out["on"]["by_set"]["paraphrase"]["right"] == 1
    assert out["delta"] == {"right": 1, "confident_wrong": 0, "clarified": -1, "clarify_right": -1, "refused": 0}
    assert out["keep_default_on"] and "Keep the default on" in out["reason"]
    assert out["on"]["by_set"]["original"]["llm_calls"]["per_question"] >= 1

    # The gate refuses runs queued the wrong way round.
    with pytest.raises(SystemExit, match="--review off"):
        cli("gold-gate", "--tenant", slug, "--off", runs["on"]["id"], "--on", runs["off"]["id"])


async def test_api_evaluate_takes_review(stack: httpx.AsyncClient, world: World,
                                         owner_conn: psycopg.Connection, signing_keys: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    await index_model(stack, world, signing_keys)
    resp = await stack.post(GOLD, headers=h(admin), json=gold(world, QUESTIONS[0][0],
                                                              {"measure": {"ref": "measure:open_projects"}}))
    assert resp.status_code == 201, resp.text
    resp = await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={"plan_only": True, "review": False})
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    assert owner_conn.execute("SELECT review FROM rag.eval_runs WHERE id = %s", (run_id,)).fetchone()[0] is False
    await run_queued(stack, world, signing_keys, run_id)
    runs = (await stack.get("/api/v1/knowledge/eval-runs", headers=h(admin))).json()["items"]
    assert runs[0]["review"] is False
