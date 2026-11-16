"""Named measures that tie go to AG-03 before Ask asks (EXEC-172), with a scripted AG-03.

Acceptance: "action items" ties Open actions and Overdue actions; AG-03 plans it first and the plan is answered when
it uses a tied measure; a failed plan or one on another measure leaves the rules' "Which measure do you mean?".
RULE-023's "By 'cost' do you mean …?" is never sent to AG-03 (``test_measure_list_unit``).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from api_testkit import needs_db
from conftest import FakeGateway
from pmo_testkit import World
from test_ask_api import ask, by_event
from test_confidence_api import (
    plan_calls,
    samples,
    world,  # noqa: F401 - the fixture with the approved pack glossary
)

pytestmark = [needs_db, pytest.mark.integration]

TIE = "action items"  # rules: Open actions and Overdue actions tie


async def _ask(stack: httpx.AsyncClient, world: World, reply: dict[str, Any],
               question: str = TIE) -> tuple[dict[str, Any], FakeGateway]:
    fake = FakeGateway({"AG-03": samples(reply)})
    stack.app.state.gateway = fake.client()  # type: ignore[attr-defined]
    _, events = await ask(stack, world.tokens["admin"], question)
    return by_event(events), fake


async def test_a_tie_is_planned_by_ag03(stack: httpx.AsyncClient, world: World) -> None:
    ev, fake = await _ask(stack, world, {"measure": "measure:open_actions", "confidence": 0.9})
    assert ev["plan"]["planner"] == "llm" and ev["plan"]["spec"]["measure"] == {"ref": "measure:open_actions"}
    assert "clarify" not in ev and "complete" in ev
    assert plan_calls(fake)


@pytest.mark.parametrize("reply", [{"unsupported": True, "missing_data": ["actions"]},
                                   {"measure": "measure:total_projects", "confidence": 0.9}])
async def test_a_failed_or_other_plan_asks_which_measure(stack: httpx.AsyncClient, world: World,
                                                         reply: dict[str, Any]) -> None:
    ev, fake = await _ask(stack, world, reply)
    assert plan_calls(fake)
    q = ev["clarify"]
    assert q["question"] == "Which measure do you mean?"
    assert {o["measure"] for o in q["options"]} >= {"measure:open_actions", "measure:overdue_actions"}

