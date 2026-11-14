"""Multi-step plans and analysis recipes in Ask (ENH-012, US-179; FEAT-063 / FEAT-014), with a scripted AG-03.

Acceptance: a plan of up to three steps runs in order through ``QueryService``, each under the person's scope, so a
Creator limited to the Gulf regions never gets another region's values from a step's value list; every step is shown
in "How this was worked out" and no step's rows reach a model. AG-03's first round is three samples at 0.6 and a
multi-step plan is voted on and repaired like any other (repairs single); a reading question still works. Recipes
are indexed with the model and used only when approved; the rules and AG-03 fill their slots.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, run, setup_world
from test_ask_api import ask, by_event
from test_confidence_api import MISS, choose, plan_calls, samples
from test_knowledge_api import bag, index_model

pytestmark = [needs_db, pytest.mark.integration]

GULF = {"KSA", "GCC - UAE"}
# Step 1: the regions with projects; step 2: open projects by portfolio in those regions.
REGION_LIST = {
    "measure": "measure:open_projects", "breakdowns": ["dimension:portfolio"],
    "filters": [{"ref": "dimension:region", "op": "in", "values": {"from_step": 1}}],
    "steps": [{"measure": "measure:total_projects", "breakdowns": ["dimension:region"],
               "having": {"op": ">", "value": 0}}],
    "confidence": 0.9}


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


def steps_of(events: list[Any]) -> list[dict[str, Any]]:
    return by_event(events)["complete"]["steps"]


async def test_above_average_runs_two_scoped_steps(stack: httpx.AsyncClient, world: World,
                                                   owner_conn: psycopg.Connection) -> None:
    question = "Portfolios with above-average open projects"
    seen: dict[str, tuple[float, dict[Any, Any]]] = {}
    for who in ("creator", "admin"):
        turn_id, events = await ask(stack, world.tokens[who], question)
        ev = by_event(events)
        assert ev["plan"]["planner"] == "rules", ev
        shown = ev["complete"]["steps"]
        assert [s["step"] for s in shown] == [1, 2] and shown == ev["plan"]["steps"]
        assert shown[0]["text"].startswith("Step 1: Open projects by Portfolio → ")
        assert shown[1]["text"].startswith("Step 2: Open projects by Portfolio")
        mean = shown[0]["value"]
        spec = ev["plan"]["spec"]
        assert spec["having"] == {"op": ">", "value": mean} and "steps" not in spec and "uses" not in spec
        assert "Two steps: Open projects for the average Portfolio, then the Portfolio above it" in \
            ev["complete"]["interpreted"]
        # Step 1 is the person's own breakdown: the mean of what they can see, and only members above it remain.
        own = await run(stack, world.tokens[who], {"model_version_id": world.version_id,
                                                   "connection_id": world.connection_id,
                                                   "measure": {"ref": "measure:open_projects"},
                                                   "breakdowns": [{"ref": "dimension:portfolio"}]})
        figures = [r[-1] for r in own.json()["rows"]]
        assert mean == pytest.approx(sum(figures) / len(figures), rel=1e-5)
        assert ev["execute"]["row_count"] == sum(1 for v in figures if v > mean)
        runs = owner_conn.execute("SELECT count(*), count(DISTINCT scope_hash) FROM ops.query_executions WHERE "
                                  "turn_id = %s AND surface = 'ask'", (turn_id,)).fetchone()
        assert runs is not None and runs[0] >= 1 and runs[1] == 1
        seen[who] = (mean, {r[0]: r[-1] for r in own.json()["rows"]})
    assert seen["creator"][0] != seen["admin"][0]  # each person's average is over their own rows


async def test_a_step_value_list_stays_in_the_creators_scope(stack: httpx.AsyncClient, world: World,
                                                             owner_conn: psycopg.Connection) -> None:
    fake = FakeGateway({"AG-03": samples(REGION_LIST)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    turn_id, events = await ask(stack, world.tokens["creator"], MISS)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm", ev
    calls = plan_calls(fake)
    assert len(calls) == 3 and {c["temperature"] for c in calls} == {0.6}  # first round: three samples
    regions = steps_of(events)[0]["value"]
    assert set(regions) == GULF and steps_of(events)[0]["values_count"] == 2
    region_filter = next(f for f in ev["plan"]["spec"]["filters"] if f["ref"] == "dimension:region")
    assert region_filter == {"ref": "dimension:region", "op": "in", "values": regions}
    assert steps_of(events)[1]["text"].startswith("Step 2: Open projects by Portfolio, Region: ")
    # Each step ran through the one scoped path as the Creator; their rows never went to a model.
    runs = owner_conn.execute("SELECT count(*), count(DISTINCT scope_hash), count(DISTINCT user_id) FROM "
                              "ops.query_executions WHERE turn_id = %s AND surface = 'ask'", (turn_id,)).fetchone()
    assert runs is not None and runs[0] >= 2 and runs[1] == 1 and runs[2] == 1
    later = [json.dumps(c["messages"]) for c in fake.calls]
    assert not any("East Africa" in m or "Jordan" in m for m in later)
    assert not any("GCC - UAE" in m for m in later[:len(calls) + 1])  # AG-03 never saw step 1's list

    # The same plan for the Admin lists every region: the list is whatever the person's own step 1 returned.
    _, events = await ask(stack, world.tokens["admin"], MISS)
    assert len(steps_of(events)[0]["value"]) == 6


async def test_a_multi_step_plan_is_repaired_and_a_bad_step_is_named(stack: httpx.AsyncClient,
                                                                     world: World) -> None:
    bad = {**REGION_LIST, "filters": [{"ref": "dimension:region", "op": "in", "values": {"from_step": 2}}]}
    queue = [bad, bad, bad, REGION_LIST]

    def reply(task: str, messages: list[dict[str, Any]]) -> Any:
        return {} if "choosing candidates" in messages[0]["content"] else queue.pop(0)
    fake = FakeGateway({"AG-03": reply})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], MISS)
    ev = by_event(events)
    calls = plan_calls(fake)
    assert [c["temperature"] for c in calls] == [0.6, 0.6, 0.6, None]  # three samples, then one repair
    assert "steps: Step 2 can only use an earlier step (1 to 1)." in calls[3]["messages"][-1]["content"]
    assert ev["plan"]["interpreted"][0].startswith("Plan corrected: steps: Step 2")
    assert set(steps_of(events)[0]["value"]) == GULF

    # A step whose answer can't be used ends the turn with that step named.
    single = {**REGION_LIST, "filters": [{"ref": "dimension:region", "op": "=", "values": {"from_step": 1}}]}
    stack.app.state.gateway = FakeGateway({"AG-03": samples(single)}).client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], MISS)
    err = by_event(events)["error"]
    assert err["code"] == "step_failed" and err["step"] == 1
    assert err["message"].startswith("Step 1: A step whose single figure is used must return one row")


async def test_disagreeing_multi_step_samples_ask_which_reading(stack: httpx.AsyncClient, world: World) -> None:
    other = {**REGION_LIST, "measure": "measure:total_projects"}
    single = {"measure": "measure:open_projects", "breakdowns": ["dimension:portfolio"]}
    stack.app.state.gateway = FakeGateway({"AG-03": samples(REGION_LIST, other, single)}).client()  # type: ignore
    creator = world.tokens["creator"]
    turn_id, events = await ask(stack, creator, MISS)
    q = by_event(events)["clarify"]
    assert q["kind"] == "reading" and len(q["options"]) == 3
    assert q["options"][0]["label"].startswith("step 1: Projects by Region") and \
        "then Open projects by Portfolio, Region in step 1's members" in q["options"][0]["label"]
    assert q["options"][2]["label"] == "Open projects by Portfolio"
    resumed = by_event(await choose(stack, creator, turn_id, "b", events))
    assert resumed["plan"]["spec"]["measure"]["ref"] == "measure:total_projects"
    assert set(resumed["complete"]["steps"][0]["value"]) == GULF


async def test_recipes_are_used_once_approved_with_the_model(stack: httpx.AsyncClient, world: World,
                                                             owner_conn: psycopg.Connection,
                                                             signing_keys: dict[str, Any]) -> None:
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    out = await index_model(stack, world, signing_keys)
    assert out["analysis_recipes"] == 6
    status = owner_conn.execute("SELECT review_status FROM rag.knowledge_documents WHERE source_kind = "
                                "'analysis_recipe' AND model_version_id = %s", (world.version_id,)).fetchone()
    assert status == ("approved",)  # approved with the model, like the report blueprints

    _, events = await ask(stack, world.tokens["creator"], "Pareto of forecast cost by portfolio")
    ev = by_event(events)
    spec = ev["plan"]["spec"]
    assert ev["plan"]["recipe"]["id"] == "pareto_80_20" and ev["complete"]["recipe"]["chart"] == "pareto"
    assert spec["share_of_total"] == ["measure:forecast_cost_total"] and spec["visual"] == {"type": "pareto"}
    assert spec["breakdowns"] == [{"ref": "dimension:portfolio"}] and "recipe" not in spec
    assert "Analysis recipe: Pareto 80/20 (Forecast cost (EAC, USD), dimension: Portfolio)" in \
        ev["complete"]["interpreted"]
    found = [x["type"] for x in ev["narrative"]["insights"]]
    lead = [{"concentration": 0, "outlier": 1}.get(t, 2) for t in found]
    assert lead == sorted(lead)  # the recipe's insight checks come first

    # Back to review: Ask no longer plans with the recipes.
    owner_conn.execute("UPDATE rag.knowledge_documents SET review_status = 'pending_review' WHERE source_kind = "
                       "'analysis_recipe'")
    _, events = await ask(stack, world.tokens["creator"], "Pareto of forecast cost by portfolio")
    ev = by_event(events)
    assert ev["plan"]["recipe"] is None and "share_of_total" not in ev["plan"]["spec"]


async def test_ag03_picks_a_recipe_from_its_context(stack: httpx.AsyncClient, world: World,
                                                    signing_keys: dict[str, Any]) -> None:
    pick = {"measure": "measure:open_projects", "confidence": 0.9,
            "recipe": {"id": "aging_buckets", "slots": {"measure": "measure:open_projects",
                                                        "dimension": "dimension:on_hold_aging"}}}
    stack.app.state.gateway = FakeGateway(embedder=bag).client()  # type: ignore[attr-defined]
    await index_model(stack, world, signing_keys)
    fake = FakeGateway({"AG-03": samples(pick)}, embedder=bag)
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["creator"], MISS)
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm" and ev["plan"]["recipe"]["id"] == "aging_buckets"
    assert ev["plan"]["spec"]["breakdowns"] == [{"ref": "dimension:on_hold_aging"}]
    assert ev["plan"]["spec"]["visual"] == {"type": "columns"}
    system = plan_calls(fake)[0]["messages"][0]["content"]
    assert '<recipe id="aging_buckets" title="Aging buckets" chart="columns"' in system
