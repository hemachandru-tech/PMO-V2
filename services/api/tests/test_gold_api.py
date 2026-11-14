"""Gold examples and the ground-truth evaluation suite (US-056, US-057; API-050; DATA-044).

Acceptance: TEST-017 (with 20+ gold examples the suite reports pass/fail per example with spec diffs for failures,
and the overall pass rate is stored), TEST-092 (recall@8 of retrieval is reported), FEAT-015 (spec validated against
the model; Creator suggests, Admin approves; low pass rate warns, never blocks; few-shot context for AG-03).
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world
from test_ask_api import ask, by_event
from test_exports_api import worker
from test_knowledge_api import BASE, bag, index_model

from tela_api.knowledge import gold as gold_mod
from tela_api.knowledge import scoring
from tela_api.knowledge.gold import core_spec, pack_draft, spec_diff

pytestmark = [needs_db, pytest.mark.integration]
GOLD = "/api/v1/knowledge/gold-examples"


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    w.extra["gateway"] = FakeGateway(embedder=bag)
    stack.app.state.gateway = w.extra["gateway"].client()  # type: ignore[attr-defined]
    return w


def gold(world: World, question: str, spec: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return {"question": question, "expected_spec": spec, "connection_id": world.connection_id, **kw}


async def run_queued(api: httpx.AsyncClient, world: World, keys: dict[str, Any], run_id: str) -> dict[str, Any]:
    """The worker's call returns at once (202); the run goes on in the background — wait for it here."""
    resp = await api.post(f"{BASE}/eval-runs/{run_id}/run", headers=worker(keys), json={"tenant_id": world.tenant_id})
    assert resp.status_code == 202, resp.text
    if resp.json()["status"] != "running":
        return dict(resp.json())
    tasks = list(api.app.state.eval_tasks)  # type: ignore[attr-defined]
    assert len(tasks) == 1
    return dict(await tasks[0])


def test_spec_diff_ignores_order_and_defaults() -> None:
    exp = {"measure": {"ref": "measure:open_projects"},
           "filters": [{"ref": "dimension:region", "op": "in", "values": ["KSA", "Jordan"]}]}
    act = {"measure": {"ref": "measure:open_projects"}, "breakdowns": [], "follow_page_filters": True,
           "filters": [{"ref": "dimension:region", "op": "in", "values": ["Jordan", "KSA"]}], "visual": {"type": "table"}}
    assert spec_diff(exp, act) == []  # visual is compared only when the example sets it
    diff = spec_diff({**exp, "visual": {"type": "donut"}}, act)
    assert [d["path"] for d in diff] == ["visual"]
    diff = spec_diff(exp, {"measure": {"ref": "measure:in_delivery"}})
    assert [d["path"] for d in diff] == ["measure", "filters"]
    assert spec_diff(exp, None)[0]["path"] == "spec"
    assert "follow_page_filters" not in core_spec(act)


def test_pack_draft_is_curated() -> None:
    examples = pack_draft()
    assert len(examples) >= 25
    tags = {t for e in examples for t in e["tags"]}
    assert {"look_alike", "date_field", "enum", "fan_out"} <= tags
    assert len({e["question"].lower() for e in examples}) == len(examples)


async def test_crud_validation_and_suggestions(stack: httpx.AsyncClient, world: World,
                                               owner_conn: psycopg.Connection) -> None:
    admin, creator = world.tokens["admin"], world.tokens["creator"]
    resp = await stack.post(GOLD, headers=h(admin), json=gold(
        world, "Open projects  by region", {"measure": {"ref": "measure:open_projects"},
                                            "breakdowns": [{"ref": "dimension:region"}]}, tags=["Breakdown"]))
    assert resp.status_code == 201, resp.text
    g = resp.json()
    assert g["status"] == "approved" and g["question"] == "Open projects by region" and g["tags"] == ["breakdown"]
    assert g["embedded"] and g["verified_by"] and g["model_version_id"] == world.version_id
    # The spec must validate against the approved model.
    bad = await stack.post(GOLD, headers=h(admin), json=gold(world, "Bogus", {"measure": {"ref": "measure:nope"}}))
    assert bad.status_code == 422, bad.text
    dup = await stack.post(GOLD, headers=h(admin), json=gold(world, "open projects BY region",
                                                             {"measure": {"ref": "measure:open_projects"}}))
    assert dup.status_code == 409
    fp = await stack.post(GOLD, headers=h(admin), json=gold(world, "Projects", {"measure": {"ref": "measure:total_projects"}},
                                                            expected_result_fingerprint={"row_count": 1}))
    assert fp.status_code == 400  # a checksum needs a test scope role
    # A Creator saves an answer as a suggestion; Admins are told; only Admins list and approve.
    turn_id, events = await ask(stack, creator, "How many projects are on hold?")
    spec = by_event(events)["complete"]["spec"]
    sug = await stack.post(GOLD, headers=h(creator), json=gold(world, "How many projects are on hold?", spec,
                                                               turn_id=turn_id))
    assert sug.status_code == 201 and sug.json()["status"] == "suggested" and sug.json()["origin"] == "answer"
    assert owner_conn.execute("SELECT count(*) FROM ops.notifications WHERE kind = 'gold_example.suggested'"
                              ).fetchone()[0] >= 1
    assert (await stack.get(GOLD, headers=h(creator))).status_code == 403
    assert (await stack.post(GOLD, headers=h(world.tokens["viewer"]), json=gold(world, "x?", spec))).status_code == 403
    review = await stack.post(f"{GOLD}/review", headers=h(admin), json={"decision": "approve",
                                                                        "ids": [sug.json()["id"], g["id"]]})
    assert review.json()["updated"] == [sug.json()["id"]] and review.json()["skipped"][0]["id"] == g["id"]
    # Edit: a new question is re-embedded; a new spec is re-validated and re-pinned.
    patched = await stack.patch(f"{GOLD}/{g['id']}", headers=h(admin), json={
        "question": "Open projects per region", "tags": ["breakdown", "baseline"], "use_for_fewshot": False})
    assert patched.status_code == 200 and patched.json()["embedded"] and not patched.json()["use_for_fewshot"]
    bad_patch = await stack.patch(f"{GOLD}/{g['id']}", headers=h(admin), json={"expected_spec": {"measure": {
        "ref": "measure:open_projects"}, "breakdowns": [{"ref": "dimension:nope"}]}})
    assert bad_patch.status_code == 422
    assert (await stack.delete(f"{GOLD}/{g['id']}", headers=h(admin))).status_code == 204
    listed = (await stack.get(GOLD, headers=h(admin))).json()
    assert [i["question"] for i in listed["items"]] == ["How many projects are on hold?"]
    assert listed["threshold"] == 0.8


async def test_evaluation_suite_reports_by_tag(stack: httpx.AsyncClient, world: World,
                                               owner_conn: psycopg.Connection,
                                               signing_keys: dict[str, Any]) -> None:
    """TEST-017 and TEST-092 over the drafted PMO gold set."""
    admin = world.tokens["admin"]
    await index_model(stack, world, signing_keys)  # retrieval recall needs the model indexed
    imported = await stack.post(f"{GOLD}/import-pack", headers=h(admin), json={"connection_id": world.connection_id})
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] >= 25 and imported.json()["skipped"] == []
    again = await stack.post(f"{GOLD}/import-pack", headers=h(admin), json={"connection_id": world.connection_id})
    assert again.json()["created"] == 0
    # Nothing to evaluate until an Admin approves the draft.
    assert (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).status_code == 409
    ids = [i["id"] for i in (await stack.get(GOLD, headers=h(admin), params={"status": "suggested"})).json()["items"]]
    approved = await stack.post(f"{GOLD}/review", headers=h(admin), json={"decision": "approve", "ids": ids})
    assert len(approved.json()["updated"]) == len(ids) >= 25
    # One deliberately wrong expectation, so a failure and its diff show up.
    wrong = await stack.post(GOLD, headers=h(admin), json=gold(
        world, "Open projects by platform", {"measure": {"ref": "measure:in_delivery"},
                                             "breakdowns": [{"ref": "dimension:platform"}]}, tags=["look_alike"]))
    assert wrong.status_code == 201
    # US-184: a different spec with the same rows passes on execution; a follow-up is planned as a refinement.
    same = await stack.post(GOLD, headers=h(admin), json=gold(
        world, "Open projects per region", {"measure": {"ref": "measure:total_projects"},
                                            "filters": [{"ref": "dimension:status", "op": "=", "values": ["Active"]}],
                                            "breakdowns": [{"ref": "dimension:region"}]}, tags=["equivalent"]))
    assert same.status_code == 201, same.text
    follow = await stack.post(GOLD, headers=h(admin), json=gold(
        world, "top 3", {"measure": {"ref": "measure:overdue_go_lives"}, "breakdowns": [{"ref": "dimension:portfolio"}],
                         "sort": [{"by": "measure", "dir": "desc"}], "limit": 3},
        tags=["multi_turn"], turns=["Overdue go-lives by portfolio"]))
    assert follow.status_code == 201 and follow.json()["turns"] == ["Overdue go-lives by portfolio"]
    stack.app.state.gateway = FakeGateway({"AG-03": {"unsupported": True, "missing_data": ["x"]}},  # type: ignore[attr-defined]
                                          embedder=bag).client()
    q = await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})
    assert q.status_code == 202, q.text
    run_id = q.json()["run_id"]
    assert (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).status_code == 409  # one at a time
    assert owner_conn.execute("SELECT count(*) FROM ops.outbox_events WHERE topic = 'knowledge.evaluation_requested'"
                              ).fetchone()[0] == 1
    summary = await run_queued(stack, world, signing_keys, run_id)
    assert summary["status"] == "done" and summary["total"] == len(ids) + 3
    run = (await stack.get(f"/api/v1/knowledge/eval-runs/{run_id}", headers=h(admin))).json()
    assert run["status"] == "done" and run["pass_rate"] == round(run["passed"] / run["total"], 4)
    assert run["recall_at_8"] is not None and 0 <= run["recall_at_8"] <= 1
    assert set(run["by_tag"]) >= {"look_alike", "date_field", "enum", "baseline"}
    assert all(t["total"] >= t["passed"] for t in run["by_tag"].values())
    print("\nPMO draft gold set:", run["passed"], "/", run["total"], "recall@8", run["recall_at_8"],
          json.dumps({k: v["pass_rate"] for k, v in sorted(run["by_tag"].items())}))
    for r in run["results"]:
        if not r["passed"]:
            print("  FAIL", r["question"], json.dumps(r["diff"])[:200])
    failed = next(r for r in run["results"] if r["question"] == "Open projects by platform")
    assert not failed["passed"] and [d["path"] for d in failed["diff"]] == ["measure"]
    assert failed["diff"][0] == {"path": "measure", "expected": {"ref": "measure:in_delivery"},
                                 "actual": {"ref": "measure:open_projects"}}
    assert failed["actual_spec"]["breakdowns"] == [{"ref": "dimension:platform"}]
    assert failed["error_class"] == "measure" and failed["exec_match"] is False
    # Execution accuracy: same rows from another spec pass; the spec-match rate still counts it as different.
    eq = next(r for r in run["results"] if r["question"] == "Open projects per region")
    assert eq["passed"] and not eq["spec_match"] and eq["exec_match"] and eq["error_class"] is None
    assert eq["values_linked"] is None  # it needed no status filter, so it says nothing about value linking
    assert eq["diff"] and eq["actual_spec"]["measure"] == {"ref": "measure:open_projects"}
    fu = next(r for r in run["results"] if r["question"] == "top 3")
    assert fu["passed"] and fu["spec_match"], fu
    assert all(r["passed"] == (r["exec_match"] is True and r["fingerprint_ok"] is not False) for r in run["results"])
    assert all(r["error_class"] is None if r["passed"] else r["error_class"] in scoring.TAXONOMY
               for r in run["results"])
    assert run["spec_match_rate"] < run["pass_rate"] or run["passed"] == run["total"]
    assert sum(r["spec_match"] for r in run["results"]) == round(run["spec_match_rate"] * run["total"])
    assert sum(run["by_error"].values()) == run["total"] - run["passed"]
    assert 0 < run["mrr"] <= 1 and 0 < run["ndcg_at_8"] <= 1
    assert run["recall_by_kind"]["measure"] == run["recall_at_8"] and run["recall_by_kind"]["dimension"] is not None
    assert run["value_linking"]["total"] >= 1 and 0 <= run["value_linking"]["rate"] <= 1
    print("execution", run["pass_rate"], "spec match", run["spec_match_rate"], "MRR", run["mrr"], "nDCG@8",
          run["ndcg_at_8"], "recall by kind", run["recall_by_kind"], "value linking", run["value_linking"],
          "errors", run["by_error"])
    assert run["planner"].get("rules", 0) >= 1
    # US-190: the held-out paraphrases are scored beside the original set, per planner path.
    # US-220: and the wording-noise set beside them.
    assert not run["plan_only"] and set(run["by_set"]) == {"original", "paraphrase", "noise"}
    para = run["by_set"]["paraphrase"]
    assert para["total"] == sum("paraphrase" in r["tags"] for r in run["results"]) >= 100
    assert run["by_set"]["noise"]["total"] == sum("noise" in r["tags"] for r in run["results"]) >= 250
    assert sum(para["outcomes"].values()) == para["total"] and set(para["by_path"]) <= {"rules", "freehand", "llm"}
    assert sum(g["total"] for g in run["by_path"].values()) == run["total"]
    # No over-clarification. US-193: only a value alias asks here — this test builds no value index, so rules plan
    # "Open projects where Omar is the project manager" without its filter, and the back-check asks instead.
    # US-250: the EXEC-186 lists and count twins are a held-out baseline too (their asks show in the shape grid).
    asked = [r for r in run["results"] if r["status"] == "clarify"
             and not {"paraphrase", "noise", "exec186"} & set(r["tags"])]
    assert all("alias" in r["tags"] for r in asked), [r["question"] for r in asked]
    assert all(r["status"] in ("ok", "clarify", "unsupported") and r["latency_ms"] is not None
               for r in run["results"])
    assert all(r["confident_wrong"] == (r["status"] == "ok" and not r["passed"] and r["confidence"] >= 0.6)
               for r in run["results"])
    refused = [r for r in run["results"] if r["status"] == "unsupported"]
    assert refused and all(r["required"] is not None for r in refused if not r["tags"] == ["multi_turn"])
    print("paraphrases", para["passed"], "/", para["total"], para["outcomes"], "confident wrong",
          para["confident_wrong"], "required found", para["required_recall"]["found"])
    # The gold list shows each example's latest result.
    items = (await stack.get(GOLD, headers=h(admin))).json()["items"]
    assert all(i["last_passed"] is not None for i in items)
    # Below the threshold → Admins are warned (never blocked).
    if run["below_threshold"]:
        assert owner_conn.execute("SELECT count(*) FROM ops.notifications WHERE kind = 'gold_example.below_threshold'"
                                  ).fetchone()[0] >= 1
    runs = (await stack.get("/api/v1/knowledge/eval-runs", headers=h(admin))).json()
    assert runs["items"][0]["id"] == run_id and runs["items"][0]["trigger"] == "manual"
    # Re-running a finished run is a no-op.
    assert (await run_queued(stack, world, signing_keys, run_id)) == {"status": "done"}


async def test_plan_only_run_from_the_cli(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
                                          signing_keys: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """US-190: a plan-only run (a live source) fetches no result rows and still scores per set and path; the operator
    queues one with ``tela-admin gold-run``."""
    from tela_api import cli

    admin = world.tokens["admin"]
    await index_model(stack, world, signing_keys)
    for question, measure, tags in (("How many open projects are there?", "open_projects", ["baseline"]),
                                    ("How many projects are still running?", "open_projects", ["paraphrase"]),
                                    ("What's our live project count?", "open_projects", ["paraphrase", "colloquial"]),
                                    ("Why are projects on hold?", "on_hold", ["paraphrase", "breakdown_word"])):
        spec: dict[str, Any] = {"measure": {"ref": f"measure:{measure}"}}
        if measure == "on_hold":
            spec["breakdowns"] = [{"ref": "dimension:hold_reason"}]
        resp = await stack.post(GOLD, headers=h(admin), json=gold(world, question, spec, tags=tags,
                                                                  use_for_fewshot="paraphrase" not in tags))
        assert resp.status_code == 201, resp.text
    admin_id = resp.json()["verified_by"]
    slug = owner_conn.execute("SELECT slug FROM iam.tenants WHERE id = %s", (world.tenant_id,)).fetchone()[0]

    async def no_rows(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("a plan-only run must not fetch result rows")

    monkeypatch.setattr(gold_mod, "_rows", no_rows)
    stack.app.state.gateway = FakeGateway({"AG-03": {"unsupported": True, "missing_data": ["x"]}},  # type: ignore[attr-defined]
                                          embedder=bag).client()
    # Nobody has run one by hand yet: the CLI needs to be told who to plan as.
    monkeypatch.setenv("TELA_MIGRATE_DATABASE_URL", os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    with pytest.raises(SystemExit, match="--run-as"):
        cli.main(["gold-run", "--tenant", slug, "--plan-only"])
    cli.main(["gold-run", "--tenant", slug, "--plan-only", "--tag", "paraphrase", "--tag", "baseline",
              "--run-as", admin_id])
    run_id, plan_only = owner_conn.execute("SELECT id, plan_only FROM rag.eval_runs ORDER BY created_at DESC LIMIT 1"
                                           ).fetchone()
    assert plan_only
    summary = await run_queued(stack, world, signing_keys, str(run_id))
    assert summary["status"] == "done" and summary["total"] == 4
    run = (await stack.get(f"/api/v1/knowledge/eval-runs/{run_id}", headers=h(admin))).json()
    assert run["plan_only"] and set(run["by_set"]) == {"original", "paraphrase"}
    assert run["by_set"]["original"]["outcomes"] == {"right": 1}
    assert all(r["exec_match"] is None for r in run["results"])  # nothing was run
    assert all(r["passed"] == r["spec_match"] for r in run["results"])
    para = run["by_set"]["paraphrase"]
    assert para["total"] == 3 and sum(para["outcomes"].values()) == 3
    for r in run["results"]:
        assert r["latency_ms"] >= 0 and r["status"] in ("ok", "clarify", "unsupported")
        assert r["required"] is not None and "found" in r["required"]  # recorded whatever the outcome
        if r["status"] == "ok" and not r["passed"]:
            assert r["confident_wrong"] == (r["confidence"] >= 0.6) and r["error_class"] in scoring.TAXONOMY
    report = cli_report(slug, str(run_id))
    assert report["id"] == str(run_id) and report["plan_only"] and report["by_set"]["paraphrase"]["total"] == 3


def cli_report(tenant: str, run_id: str, *extra: str) -> Any:
    import contextlib
    import io

    from tela_api import cli

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["gold-report", "--tenant", tenant, "--run", run_id, *extra])
    return out.getvalue() if extra else dict(json.loads(out.getvalue()))


async def test_runs_grade_the_answer_shape(stack: httpx.AsyncClient, world: World, owner_conn: psycopg.Connection,
                                           signing_keys: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """US-250: an example names the kind of answer it expects; a list answered with its count fails as a shape
    failure, and the run reports right / confident-wrong / clarified / refused per shape and shape by tag, in the API
    and in ``tela-admin gold-report``."""
    admin = world.tokens["admin"]
    await index_model(stack, world, signing_keys)
    open_ = {"measure": {"ref": "measure:open_projects"}}
    examples = [("Which projects are open?", {**open_, "breakdowns": [{"ref": "field:projects.name"}]}, ["exec186", "list"]),
                ("How many projects are open?", open_, ["exec186", "count"]),
                ("Open projects by region", {**open_, "breakdowns": [{"ref": "dimension:region"}]}, ["breakdown"])]
    for question, spec, tags in examples:
        resp = await stack.post(GOLD, headers=h(admin), json=gold(world, question, spec, tags=tags))
        assert resp.status_code == 201, resp.text  # a record breakdown is a valid expected spec
    shapes = {i["question"]: i["shape"] for i in (await stack.get(GOLD, headers=h(admin))).json()["items"]}
    assert shapes == {"Which projects are open?": "list_of_records", "How many projects are open?": "number",
                      "Open projects by region": "breakdown"}
    stack.app.state.gateway = FakeGateway({"AG-03": {"unsupported": True, "missing_data": ["x"]}},  # type: ignore[attr-defined]
                                          embedder=bag).client()
    run_id = (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).json()["run_id"]
    assert (await run_queued(stack, world, signing_keys, run_id))["status"] == "done"
    run = (await stack.get(f"/api/v1/knowledge/eval-runs/{run_id}", headers=h(admin))).json()
    by_q = {r["question"]: r for r in run["results"]}
    listed = by_q["Which projects are open?"]
    assert listed["shape"] == "list_of_records" and not listed["passed"]
    if listed["status"] == "ok":  # today's planner answers the list with the count (EXEC-186)
        assert {"path": "answer_shape", "expected": "list_of_records", "actual": "number"} in listed["diff"]
        assert listed["error_class"] in ("shape", "measure")
    assert by_q["How many projects are open?"]["shape"] == "number"
    grid = run["shape_grid"]
    assert set(grid["rows"]) == {"list_of_records", "number", "breakdown"}
    assert grid["total"]["total"] == run["total"] == sum(r["total"] for r in grid["rows"].values())
    assert grid["total"]["right"] == run["passed"]
    assert grid["rows"]["list_of_records"]["right"] == 0 and grid["cells"]["list_of_records"]["list"]["total"] == 1
    slug = owner_conn.execute("SELECT slug FROM iam.tenants WHERE id = %s", (world.tenant_id,)).fetchone()[0]
    monkeypatch.setenv("TELA_MIGRATE_DATABASE_URL", os.environ["TELA_TEST_OWNER_DATABASE_URL"])
    assert cli_report(slug, run_id)["shape_grid"] == grid
    text = cli_report(slug, run_id, "--grid")
    assert "list_of_records" in text and "0/1" in text and text.strip().splitlines()[-1].startswith("all")


async def test_threshold_warning_and_triggers(stack: httpx.AsyncClient, world: World,
                                              owner_conn: psycopg.Connection,
                                              signing_keys: dict[str, Any]) -> None:
    admin = world.tokens["admin"]
    await stack.post(GOLD, headers=h(admin), json=gold(world, "How many projects are in delivery right now?",
                                                       {"measure": {"ref": "measure:open_projects"}}))
    assert (await stack.put("/api/v1/knowledge/policy", headers=h(admin),
                            json={"eval_threshold": 0.95})).json() == {"eval_threshold": 0.95}
    run_id = (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).json()["run_id"]
    out = await run_queued(stack, world, signing_keys, run_id)
    assert out["pass_rate"] == 0.0 and out["below_threshold"]
    note = owner_conn.execute("SELECT body FROM ops.notifications WHERE kind = 'gold_example.below_threshold'"
                              ).fetchone()
    assert note and "threshold is 95%" in note[0] and "Nothing is blocked" in note[0]
    # Model approval (re-indexing the current version) queues a run as the approver.
    idx = await index_model(stack, world, signing_keys)
    assert idx["eval_run_id"]
    trig = owner_conn.execute("SELECT trigger FROM rag.eval_runs WHERE id = %s", (idx["eval_run_id"],)).fetchone()
    assert trig[0] == "model_approval"
    await run_queued(stack, world, signing_keys, idx["eval_run_id"])
    # A provider or model change re-runs the suite too.
    put = await stack.put("/api/v1/admin/llm-settings", headers=h(admin), json={
        "provider": "ollama", "models": {"planner": "llama3.2:3b"}, "allow_samples": False, "allow_aggregates": False})
    assert put.status_code == 200, put.text
    trig = owner_conn.execute("SELECT id, trigger FROM rag.eval_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    assert trig[1] == "llm_settings"
    await run_queued(stack, world, signing_keys, str(trig[0]))
    # Nightly: one run per tenant with approved examples, as the Admin who last ran it by hand.
    nightly = await stack.post(f"{BASE}/eval-runs/nightly", headers=worker(signing_keys))
    assert nightly.json() == {"tenants": 1, "queued": 1}


async def test_gold_fewshots_reach_the_llm_planner(stack: httpx.AsyncClient, world: World,
                                                   signing_keys: dict[str, Any]) -> None:
    """Approved examples are AG-03 few-shot context, with retrieved knowledge as quoted data."""
    admin = world.tokens["admin"]
    await index_model(stack, world, signing_keys)
    await stack.post(GOLD, headers=h(admin), json=gold(world, "Where are we slipping on schedule?", {
        "measure": {"ref": "measure:schedule_risk"}, "breakdowns": [{"ref": "dimension:region"}]}))
    fake = FakeGateway({"AG-03": {"measure": "measure:schedule_risk", "breakdowns": ["dimension:region"]}},
                       embedder=bag)
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, admin, "where are we slipping")
    ev = by_event(events)
    assert ev["plan"]["planner"] == "llm"
    prompt = json.dumps(next(c for c in fake.calls if c["agent"] == "AG-03"
                             and "choosing candidates" not in c["messages"][0]["content"])["messages"])
    assert "Verified examples" in prompt and "Where are we slipping on schedule?" in prompt
    assert "quoted reference data" in prompt and "chunk:" in prompt
    assert ev["retrieval"]["knowledge_refs"] and not ev["retrieval"]["limited_context"]


async def test_long_runs_go_on_in_the_background(stack: httpx.AsyncClient, world: World,
                                                 owner_conn: psycopg.Connection, signing_keys: dict[str, Any],
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """A run outlives the worker's HTTP call: claimed once, run in the background, interrupted on shutdown, and a
    run a crash left ``running`` is failed after four hours instead of blocking evaluations for ever."""
    admin = world.tokens["admin"]
    await stack.post(GOLD, headers=h(admin), json=gold(world, "How many open projects are there now?",
                                                       {"measure": {"ref": "measure:open_projects"}}))
    release = asyncio.Event()
    real = gold_mod.run_suite

    async def slow(app: Any, tenant_id: str, run: dict[str, Any]) -> dict[str, Any]:
        await release.wait()  # stands for a suite longer than any HTTP timeout
        return await real(app, tenant_id, run)

    monkeypatch.setattr(gold_mod, "run_suite", slow)
    run_id = (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).json()["run_id"]
    url, body = f"{BASE}/eval-runs/{run_id}/run", {"tenant_id": world.tenant_id}
    first = await stack.post(url, headers=worker(signing_keys), json=body)
    assert first.status_code == 202 and first.json() == {"status": "running", "run_id": run_id}
    # A redelivered task or retry doesn't start it twice.
    again = await stack.post(url, headers=worker(signing_keys), json=body)
    assert again.json() == {"status": "running"} and len(stack.app.state.eval_tasks) == 1  # type: ignore[attr-defined]
    assert (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).status_code == 409
    # Even when it started more than an hour ago.
    owner_conn.execute("UPDATE rag.eval_runs SET created_at = now() - interval '2 hours', started_at = now() - "
                       "interval '2 hours' WHERE id = %s", (run_id,))
    assert (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).status_code == 409
    release.set()
    done = await next(iter(stack.app.state.eval_tasks))  # type: ignore[attr-defined]
    assert done["status"] == "done" and done["total"] == 1
    assert owner_conn.execute("SELECT count(*) FROM rag.eval_results WHERE run_id = %s", (run_id,)).fetchone()[0] == 1

    # Shutdown: the run in progress is marked interrupted, not left running.
    release.clear()
    run2 = (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).json()["run_id"]
    await stack.post(f"{BASE}/eval-runs/{run2}/run", headers=worker(signing_keys), json=body)
    assert owner_conn.execute("SELECT total FROM rag.eval_runs WHERE id = %s", (run2,)).fetchone()[0] == 0
    await gold_mod.stop_runs(stack.app)  # type: ignore[arg-type]
    status, error = owner_conn.execute("SELECT status, error FROM rag.eval_runs WHERE id = %s", (run2,)).fetchone()
    assert status == "failed" and error == gold_mod.MSG_INTERRUPTED and not stack.app.state.eval_tasks  # type: ignore[attr-defined]

    # A crash (no shutdown): the run stays running until it is four hours old, then the next request fails it.
    run3 = (await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})).json()["run_id"]
    owner_conn.execute("UPDATE rag.eval_runs SET status = 'running', started_at = now() - interval '5 hours' "
                       "WHERE id = %s", (run3,))
    fresh = await stack.post(f"{GOLD}/evaluate", headers=h(admin), json={})
    assert fresh.status_code == 202, fresh.text
    status, error = owner_conn.execute("SELECT status, error FROM rag.eval_runs WHERE id = %s", (run3,)).fetchone()
    assert status == "failed" and error == gold_mod.MSG_STALE

