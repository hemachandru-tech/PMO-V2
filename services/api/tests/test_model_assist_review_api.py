"""AG-04 reviews the rules' model proposals and corrections when LLM review is on (ENH-026, US-245; DEC-28).

Acceptance: the rules' patch is the draft the LLM confirms or corrects; the catalogue it sees holds names, types and
keys only (DEC-27); a correction passes the same validation (invalid ops dropped, an all-invalid one falls back to the
rules); a correction to a parsed instruction never replaces it silently but asks with both readings; review off keeps
today's LLM-first path.
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
from pmo_testkit import World, h, setup_world
from test_ask_review_api import review_on
from test_assistant_llm_api import assist, manual  # noqa: F401 - the unapproved TPG model

pytestmark = [needs_db, pytest.mark.integration]

REGION = {"op": "add_dimension", "key": "projects_region_x", "label": "Region", "entity": "projects",
          "field": "region"}
GHOST = {"op": "add_measure", "key": "ghost", "label": "Ghost", "entity": "projects", "agg": "sum",
         "field": "no_such_column", "plain_rule": "x"}


def reviewer(verdict: dict[str, Any]) -> FakeGateway:
    return FakeGateway({"AG-04": lambda task, messages: verdict if "Reply with verdict" in messages[0]["content"]
                        else {"ops": [], "questions": []}})


def sent(fake: FakeGateway) -> list[dict[str, Any]]:
    return [json.loads(c["messages"][1]["content"]) for c in fake.calls if c["agent"] == "AG-04"]


async def test_proposals_confirmed_keep_the_rules(stack: httpx.AsyncClient, manual: World,  # noqa: F811
                                                  owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, manual)
    fake = reviewer({"verdict": "confirm", "reason": "the rules' dimensions and measures fit"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    out = await assist(stack, manual)
    p = out["proposal"]
    assert p["source"] == "rules" and p["review"]["verdict"] == "confirm" and p["ops"]
    [body] = sent(fake)
    assert body["draft"]["ops"] and {o["op"] for o in body["draft"]["ops"]} >= {"add_dimension", "add_measure"}
    catalogue = body["context"]["catalogue"]
    assert "projects" in catalogue and "Riyadh" not in catalogue and "KSA" not in catalogue  # names and types only


async def test_a_correction_is_validated_and_shows_what_it_left_out(stack: httpx.AsyncClient,
                                                                    manual: World,  # noqa: F811
                                                                    owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, manual)
    fake = reviewer({"verdict": "correct", "replacement": {"ops": [REGION, GHOST], "questions": []},
                     "reason": "region is the main breakdown"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    p = (await assist(stack, manual))["proposal"]
    assert p["source"] == "llm" and p["review"]["verdict"] == "correct"
    assert [o["detail"]["key"] for o in p["ops"]] == ["projects_region_x"]
    assert [d["op"] for d in p["dropped"]] == ["add_measure"]  # the invented column never shown (A3)
    assert p["review"]["rules_left_out"]  # the rules' ops the correction dropped are listed

    fake = reviewer({"verdict": "correct", "replacement": {"ops": [GHOST], "questions": []}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    p = (await assist(stack, manual))["proposal"]
    assert p["source"] == "rules" and p["review"]["verdict"] == "fallback" and p["ops"]


async def test_a_different_reading_of_a_correction_asks(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                                        make_token: Callable[..., str],
                                                        tpg_source: dict[str, Any]) -> None:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    admin = w.tokens["admin"]
    review_on(owner_conn, w)
    fake = reviewer({"verdict": "confirm"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    out = await assist(stack, w, "rename Open projects to Live projects")
    assert [o["detail"]["label"] for o in out["proposal"]["ops"]] == ["Live projects"]
    assert out["proposal"]["review"]["verdict"] == "confirm"
    [body] = sent(fake)
    assert body["draft"]["ops"] == [{"op": "rename_measure", "measure": "open_projects", "label": "Live projects"}]

    other = {"op": "rename_measure", "measure": "open_projects", "label": "Active projects"}
    fake = reviewer({"verdict": "correct", "replacement": {"ops": [other], "questions": []},
                     "reason": "the PMO calls them active"})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    out = await assist(stack, w, "rename Open projects to Live projects")
    p = out["proposal"]
    assert p["ops"] == [] and p["source"] == "rules"  # never replaced silently (DEC-28)
    [q] = [q for q in out["questions"] if q.get("clarify")]
    assert q["question"] == "Which change do you mean?" and len(q["options"]) == 2
    assert q["options"][0].startswith("As written:") and "Live projects" in q["options"][0]
    assert "Active projects" in q["options"][1]
    ok = await stack.post(f"/api/v1/models/{w.model_id}/patches/{p['patch_id']}/apply", headers=h(admin),
                          json={"answers": {"0": 1}})
    assert ok.status_code == 200, ok.text
    draft = (await stack.get(f"/api/v1/models/{w.model_id}/draft", headers=h(admin))).json()
    assert next(m for m in draft["measures"] if m["key"] == "open_projects")["label"] == "Active projects"

    # The same ops counted as a confirm; an invalid correction falls back to the rules.
    fake = reviewer({"verdict": "correct", "replacement": {"ops": [
        {"op": "rename_measure", "measure": "no_such_measure", "label": "X"}], "questions": []}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    out = await assist(stack, w, "rename Active projects to Open projects")
    assert out["proposal"]["review"]["verdict"] == "fallback" and len(out["proposal"]["ops"]) == 1


async def test_review_off_keeps_the_llm_first_path(stack: httpx.AsyncClient, manual: World,  # noqa: F811
                                                   owner_conn: psycopg.Connection) -> None:
    review_on(owner_conn, manual, on=False)
    fake = FakeGateway({"AG-04": {"ops": [REGION], "questions": []}})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    p = (await assist(stack, manual))["proposal"]
    assert p["source"] == "llm" and "review" not in p
    assert all("Reply with verdict" not in c["messages"][0]["content"] for c in fake.calls if c["agent"] == "AG-04")
