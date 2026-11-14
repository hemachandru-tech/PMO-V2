"""Chart and table words in Ask, end to end (BUG-017): the answer shows the table or chart the question asks for."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import needs_db
from pmo_testkit import World, setup_world
from test_ask_api import ask, by_event

pytestmark = [needs_db, pytest.mark.integration]


@pytest.fixture
async def world(stack: httpx.AsyncClient, owner_conn: psycopg.Connection, make_token: Callable[..., str],
                tpg_source: dict[str, Any]) -> World:
    return await setup_world(stack, owner_conn, make_token, tpg_source)


@pytest.mark.parametrize(("question", "visual"), [
    ("Show me a table of overdue go-lives by programme", "table"),
    ("Pie chart of overdue go-lives by region", "donut"),
    ("How many overdue go-lives as a table?", "table"),
])
async def test_the_answer_has_the_asked_visual(stack: httpx.AsyncClient, world: World, question: str,
                                               visual: str) -> None:
    _turn, events = await ask(stack, world.tokens["creator"], question)
    ev = by_event(events)
    assert "complete" in ev, events
    assert ev["viz"]["visual"] == visual and ev["viz"]["note"] is None
