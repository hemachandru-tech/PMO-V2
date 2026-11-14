"""Model assistant, corrections and AI settings (US-032, US-033, US-042). Acceptance: TEST-009, TEST-010."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World, h, setup_world

pytestmark = [needs_db, pytest.mark.integration]


def events(text: str) -> list[tuple[str, Any]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(ln.split(": ", 1) for ln in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture
async def manual(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                 tpg_source: dict[str, Any]) -> World:
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    resp = await stack.post("/api/v1/models", headers=h(w.tokens["admin"]), json={"connection_id": w.connection_id})
    w.model_id = resp.json()["model_id"]
    return w


async def assist(api: httpx.AsyncClient, w: World, message: str | None = None) -> dict[str, Any]:
    resp = await api.post(f"/api/v1/models/{w.model_id}/assistant", headers=h(w.tokens["admin"]),
                          json={"message": message})
    assert resp.status_code == 200, resp.text
    ev = events(resp.text)
    assert ev[-1][0] == "complete", ev
    return {"proposal": ev[0][1], "questions": [d for e, d in ev if e == "question"]}


async def test_rules_proposals_when_llm_is_off(stack: httpx.AsyncClient, manual: World) -> None:
    out = await assist(stack, manual)
    p = out["proposal"]
    assert p["source"] == "rules" and p["ops"] and p["dropped"] == []
    kinds = {o["op"] for o in p["ops"]}
    assert {"add_dimension", "add_measure"} <= kinds
    # Every money column raises an explicit question (TEST-009).
    assert any("forecast" in q["question"].lower() and "cost_total_usd" in (q.get("target") or "")
               for q in out["questions"])
    draft_before = (await stack.get(f"/api/v1/models/{manual.model_id}/draft", headers=h(manual.tokens["admin"]))).json()
    assert len(draft_before["dimensions"]) == 0  # nothing applied yet


async def test_llm_proposals_are_validated_and_links_checked(stack: httpx.AsyncClient, manual: World,
                                                            owner_conn: psycopg.Connection) -> None:
    fake = FakeGateway({"AG-04": {"ops": [
        {"op": "add_link", "from_entity": "project_requests", "to_entity": "projects",
         "from_fields": ["project_number"], "to_fields": ["project_number"], "cardinality": "many_to_one"},
        {"op": "add_measure", "key": "ghost", "label": "Ghost", "entity": "projects", "agg": "sum",
         "field": "no_such_column", "plain_rule": "x"},
        {"op": "add_measure", "key": "bad_sum", "label": "Bad", "entity": "projects", "agg": "sum", "field": "region",
         "plain_rule": "x"},
        {"op": "add_dimension", "key": "region", "label": "Region", "entity": "projects", "field": "region"},
    ], "questions": [{"question": "Does cost_total_usd include VAT?", "options": ["Yes", "No"]}]}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    out = await assist(stack, manual)
    p = out["proposal"]
    assert p["source"] == "llm"
    assert {o["op"] for o in p["ops"]} == {"add_link", "add_dimension"}
    assert {d["error"].split(" ")[0] for d in p["dropped"]} >= {"field", "sum"}  # A3: invalid ones never shown
    link = next(o for o in p["ops"] if o["op"] == "add_link")
    assert "needs your confirmation" in link["after"]
    # The source said the target key is unique, so the link stands, still unaccepted.
    assert owner_conn.execute("SELECT details->>'unique' FROM ops.audit_events WHERE action = "
                              "'model.cardinality_checked'").fetchone() == ("true",)
    # The prompt held names and types only: no values.
    prompt = json.dumps(fake.calls[0]["messages"])
    assert "character varying" in prompt and "Nomsa" not in prompt and "PRJ-0" not in prompt
    applied = await stack.post(f"/api/v1/models/{manual.model_id}/patches/{p['patch_id']}/apply",
                               headers=h(manual.tokens["admin"]), json={})
    assert applied.status_code == 200, applied.text
    draft = (await stack.get(f"/api/v1/models/{manual.model_id}/draft", headers=h(manual.tokens["admin"]))).json()
    new_link = next(ln for ln in draft["links"] if ln["from_entity"] == "project_requests")
    assert new_link["accepted"] is False and new_link["origin"] == "proposed"
    assert any(q["question"] == "Does cost_total_usd include VAT?" for q in draft["open_questions"])


async def test_correction_diff_apply_and_undo(stack: httpx.AsyncClient, manual: World) -> None:
    admin = manual.tokens["admin"]
    out = await assist(stack, manual, "Rename cost_total_usd to Forecast cost and format cost_total_usd as USD")
    p = out["proposal"]
    # cost_total_usd exists on projects and on requests: Tela asks instead of guessing (AG-02).
    clarify = [q for q in out["questions"] if q.get("clarify")]
    assert len(clarify) == 2 and p["ops"] == []
    assert "projects.cost_total_usd (Cost total usd)" in clarify[0]["options"]
    before = (await stack.get(f"/api/v1/models/{manual.model_id}/draft", headers=h(admin))).json()
    assert all(f["label"] != "Forecast cost" for e in before["entities"] for f in e["fields"])  # nothing changed yet
    answers = {str(i): next(j for j, o in enumerate(q["options"]) if o.startswith("projects."))
               for i, q in enumerate(clarify)}
    ok = await stack.post(f"/api/v1/models/{manual.model_id}/patches/{p['patch_id']}/apply", headers=h(admin),
                          json={"answers": answers})
    assert ok.status_code == 200, ok.text
    diff = ok.json()["applied"]
    assert [(d["before"], d["after"]) for d in diff if d["op"] == "rename_field"] == [("Cost total usd",
                                                                                       "Forecast cost")]
    assert any(d["op"] == "set_format" and d["after"] == "currency (USD)" for d in diff)
    after = (await stack.get(f"/api/v1/models/{manual.model_id}/draft", headers=h(admin))).json()
    labels = {f["label"] for e in after["entities"] for f in e["fields"]}
    assert "Forecast cost" in labels
    undone = await stack.post(f"/api/v1/models/{manual.model_id}/draft/undo", headers=h(admin))
    assert undone.status_code == 200
    restored = (await stack.get(f"/api/v1/models/{manual.model_id}/draft", headers=h(admin))).json()
    assert {k: restored[k] for k in ("entities", "measures")} == {k: before[k] for k in ("entities", "measures")}
    again = await stack.post(f"/api/v1/models/{manual.model_id}/draft/undo", headers=h(admin))
    assert again.status_code == 409


async def test_ambiguous_correction_asks_and_value_mapping(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                                          make_token: Callable[..., str],
                                                          tpg_source: dict[str, Any]) -> None:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    admin = w.tokens["admin"]
    out = await assist(stack, w, "exclude Cancelled from Open projects")
    clarify = [q for q in out["questions"] if q.get("clarify")]
    assert clarify and set(clarify[0]["options"]) >= {"Status", "Current state"}  # + Closure approval (US-155)
    p = out["proposal"]
    choice = clarify[0]["options"].index("Current state")
    ok = await stack.post(f"/api/v1/models/{w.model_id}/patches/{p['patch_id']}/apply", headers=h(admin),
                          json={"answers": {"0": choice}})
    assert ok.status_code == 200, ok.text
    draft = (await stack.get(f"/api/v1/models/{w.model_id}/draft", headers=h(admin))).json()
    open_m = next(m for m in draft["measures"] if m["key"] == "open_projects")
    assert "excluding current_state Cancelled" in open_m["plain_rule"]
    out = await assist(stack, w, "Treat Yellow as Amber everywhere")
    targets = {o["detail"]["dimension"] for o in out["proposal"]["ops"]}
    assert {"reported_status", "schedule_status"} <= targets
    # The impact names the starter widgets using the measure that would change.
    out = await assist(stack, w, "rename Open projects to Live projects")
    assert any("Open projects" in a for a in out["proposal"]["affected"])
    nonsense = await assist(stack, w, "make it better")
    assert nonsense["proposal"]["ops"] == [] and "phrasings" in nonsense["questions"][0]["question"]


async def test_llm_settings_and_usage(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                      make_token: Callable[..., str], tpg_source: dict[str, Any]) -> None:
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    fake = FakeGateway({"AG-01": {"ok": True}}, allowed=["ollama"])
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    got = (await stack.get("/api/v1/admin/llm-settings", headers=h(admin))).json()
    assert got["provider"] is None and got["allow_aggregates"] is False and got["allowed_providers"] == ["ollama"]
    denied = await stack.put("/api/v1/admin/llm-settings", headers=h(admin), json={"provider": "anthropic",
                                                                                   "models": {"planner": "x"}})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "provider_not_allowed"
    ok = await stack.put("/api/v1/admin/llm-settings", headers=h(admin), json={
        "provider": "ollama", "models": {"planner": "llama3.2:3b"}, "api_key": "not-needed-for-ollama",
        "rates": {"llama3.2:3b": {"input_per_mtok": 0, "output_per_mtok": 0}}})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    # Ollama needs no key, so none is stored (DEC-01 keys are per keyed provider).
    assert body["key_set"] is False and body["last_test"]["ok"] and "not-needed" not in ok.text
    assert owner_conn.execute("SELECT severity FROM ops.audit_events WHERE action = 'llm.settings.changed'"
                              ).fetchone() == ("high",)
    stack.app.state.gateway = FakeGateway(fail="planner_unavailable").client()  # type: ignore[attr-defined]
    bad = await stack.put("/api/v1/admin/llm-settings", headers=h(admin), json={
        "provider": "ollama", "models": {"planner": "missing-model"}})
    assert bad.status_code == 503
    kept = owner_conn.execute("SELECT models->>'planner' FROM ops.llm_settings").fetchone()
    assert kept == ("llama3.2:3b",)  # a failing test call is not saved
    owner_conn.execute(
        "INSERT INTO ops.llm_invocations (id, tenant_id, agent, task, provider, model, payload_hash, scanner_verdict, "
        "prompt_tokens, completion_tokens, latency_ms, status) VALUES (gen_random_uuid(), %s, 'AG-03', 'plan', "
        "'ollama', 'llama3.2:3b', 'h', 'clean', 100, 20, 900, 'ok'), (gen_random_uuid(), %s, 'AG-03', 'plan', "
        "'ollama', 'llama3.2:3b', 'h', 'blocked', NULL, NULL, NULL, 'blocked')", (w.tenant_id, w.tenant_id))
    usage = (await stack.get("/api/v1/admin/usage?group_by=task", headers=h(admin))).json()
    plan = next(i for i in usage["items"] if i["key"] == "plan")
    assert plan["calls"] == 2 and plan["prompt_tokens"] == 100 and plan["blocked"] == 1
    assert (await stack.get("/api/v1/admin/usage", headers=h(w.tokens["creator"]))).status_code == 403


async def test_keyed_providers_endpoint_and_embedding_switch(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                                             make_token: Callable[..., str],
                                                             tpg_source: dict[str, Any]) -> None:
    """DEC-01: keys per provider, Azure needs its endpoint, a new embedding model clears the old vectors."""
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    fake = FakeGateway({"AG-01": {"ok": True}}, allowed=["ollama", "anthropic", "azure_openai", "openai"])
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    put = lambda body: stack.put("/api/v1/admin/llm-settings", headers=h(admin), json=body)  # noqa: E731

    got = (await stack.get("/api/v1/admin/llm-settings", headers=h(admin))).json()
    assert got["aggregates_allowed"] is True and "anthropic" in got["keyed_providers"]
    assert "anthropic" not in got["embedding_providers"] and got["endpoint"] is None

    no_key = await put({"provider": "anthropic", "models": {"planner": "claude-opus-5-5"}})
    assert no_key.status_code == 400 and "API key" in no_key.json()["error"]["message"]
    ok = await put({"provider": "anthropic", "models": {"planner": "claude-opus-5-5"}, "api_key": "sk-ant-test-123"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["key_set"] is True and "sk-ant-test" not in ok.text
    ref = owner_conn.execute("SELECT secret_ref FROM ops.llm_settings").fetchone()
    assert ref == (f"tenants/{w.tenant_id}/llm/anthropic",)
    stored = await stack.app.state.secrets.get(ref[0])  # type: ignore[attr-defined]
    assert stored == {"api_key": "sk-ant-test-123"}
    # Same provider again: the stored key is kept.
    again = await put({"provider": "anthropic", "models": {"planner": "claude-sonnet-5-5"}, "allow_aggregates": True})
    assert again.status_code == 200 and again.json()["key_set"] is True and again.json()["allow_aggregates"] is True

    # Another keyed provider never inherits Anthropic's key; Azure also needs its endpoint.
    switched = await put({"provider": "openai", "models": {"planner": "gpt-5"}})
    assert switched.status_code == 400
    no_endpoint = await put({"provider": "azure_openai", "models": {"planner": "dep"}, "api_key": "azure-key-123"})
    assert no_endpoint.status_code == 400 and "endpoint" in no_endpoint.json()["error"]["message"]
    bad_endpoint = await put({"provider": "azure_openai", "models": {"planner": "dep"}, "api_key": "azure-key-123",
                              "endpoint": "http://acme.openai.azure.com"})
    assert bad_endpoint.status_code in (400, 422) and bad_endpoint.json()["error"]["code"] == "validation_error"

    # Knowledge vectors exist from the deployment embedder; choosing Azure embeddings clears them for re-embedding.
    doc = owner_conn.execute(
        "INSERT INTO rag.knowledge_documents (id, tenant_id, source_kind, title, content_hash, embedding_model) "
        "VALUES (gen_random_uuid(), %s, 'glossary', 'G', 'h', 'mxbai-embed-large') RETURNING id",
        (w.tenant_id,)).fetchone()[0]
    owner_conn.execute(
        "INSERT INTO rag.knowledge_chunks (id, tenant_id, document_id, chunk_no, content, metadata, embedding) "
        "VALUES (gen_random_uuid(), %s, %s, 0, 'x', '{}', %s::vector)",
        (w.tenant_id, doc, "[" + ",".join(["0.5"] * 1024) + "]"))
    azure = await put({"provider": "azure_openai", "models": {"planner": "dep", "embeddings": "emb-deploy"},
                       "api_key": "azure-key-123", "endpoint": "https://acme.openai.azure.com"})
    assert azure.status_code == 200, azure.text
    assert azure.json()["endpoint"] == "https://acme.openai.azure.com"
    left = owner_conn.execute("SELECT count(*) FROM rag.knowledge_chunks WHERE embedding IS NOT NULL").fetchone()
    assert left == (0,)
    assert owner_conn.execute("SELECT embedding_model FROM rag.knowledge_documents").fetchone() == (None,)

    # A failing test call restores the previous provider, key reference and endpoint.
    stack.app.state.gateway = FakeGateway(fail="planner_unavailable",  # type: ignore[attr-defined]
                                          allowed=["ollama", "anthropic", "azure_openai"]).client()
    failed = await put({"provider": "anthropic", "models": {"planner": "m"}, "api_key": "sk-ant-other-456"})
    assert failed.status_code == 503
    row = owner_conn.execute("SELECT provider, secret_ref, endpoint FROM ops.llm_settings").fetchone()
    assert row == ("azure_openai", f"tenants/{w.tenant_id}/llm/azure_openai", "https://acme.openai.azure.com")


async def test_llm_review_switch(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                 make_token: Callable[..., str], tpg_source: dict[str, Any]) -> None:
    """US-239, DEC-28 point 3: LLM review on for a hosted provider, off for a local one, unless the Admin says."""
    w = await setup_world(stack, owner_conn, make_token, tpg_source, approve=False)
    admin = w.tokens["admin"]
    stack.app.state.gateway = FakeGateway({"AG-01": {"ok": True}},  # type: ignore[attr-defined]
                                          allowed=["ollama", "anthropic"]).client()
    put = lambda body: stack.put("/api/v1/admin/llm-settings", headers=h(admin), json=body)  # noqa: E731

    got = (await stack.get("/api/v1/admin/llm-settings", headers=h(admin))).json()
    assert got["llm_review"] is False and got["llm_review_default"] is False
    local = (await put({"provider": "ollama", "models": {"planner": "qwen3:8b"}})).json()
    assert local["llm_review"] is False and local["llm_review_default"] is False
    hosted = await put({"provider": "anthropic", "models": {"planner": "claude-opus-5-5"}, "api_key": "sk-ant-test-1"})
    assert hosted.status_code == 200 and hosted.json()["llm_review"] is True and hosted.json()["llm_review_default"]
    # Turned off by the Admin: kept while the provider stays, even when the request leaves it out.
    off = (await put({"provider": "anthropic", "models": {"planner": "claude-opus-5-5"}, "llm_review": False})).json()
    assert off["llm_review"] is False
    kept = (await put({"provider": "anthropic", "models": {"planner": "claude-sonnet-5-5"}})).json()
    assert kept["llm_review"] is False
    # Local with review on, as the Admin asked.
    local_on = (await put({"provider": "ollama", "models": {"planner": "qwen3:8b"}, "llm_review": True})).json()
    assert local_on["llm_review"] is True
    audit = owner_conn.execute("SELECT details->>'llm_review' FROM ops.audit_events WHERE action = "
                               "'llm.settings.changed' ORDER BY occurred_at DESC LIMIT 1").fetchone()
    assert audit == ("true",)
    # A failing test call keeps the previous switch.
    stack.app.state.gateway = FakeGateway(fail="planner_unavailable",  # type: ignore[attr-defined]
                                          allowed=["ollama", "anthropic"]).client()
    failed = await put({"provider": "ollama", "models": {"planner": "qwen3:8b"}, "llm_review": False})
    assert failed.status_code == 503
    assert owner_conn.execute("SELECT llm_review FROM ops.llm_settings").fetchone() == (True,)
