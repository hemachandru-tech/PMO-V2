"""Audit trail integrity (US-043; REQ-115, NFR-007): append-only, hash-chained, no row values or secrets."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from api_testkit import APP_URL, needs_db
from pmo_testkit import run, setup_world

pytestmark = [needs_db, pytest.mark.integration]


async def test_chain_is_intact_and_append_only(stack: httpx.AsyncClient, owner_conn: psycopg.Connection,
                                               make_token: Callable[..., str], tpg_source: dict[str, Any]) -> None:
    w = await setup_world(stack, owner_conn, make_token, tpg_source)
    await run(stack, w.tokens["viewer"], w.spec("open_projects"))  # a refusal is audited too
    rows = owner_conn.execute(
        "SELECT tenant_id::text, occurred_at::text, coalesce(actor_id::text, ''), action, object_type, "
        "coalesce(object_id, ''), outcome, details::text, prev_hash, row_hash FROM ops.audit_events "
        "WHERE tenant_id = %s ORDER BY id", (w.tenant_id,)).fetchall()
    actions = {r[3] for r in rows}
    assert {"connection.created", "connection.tested", "connection.introspected", "starter_pack.installed",
            "model.approved", "access.scope_policy.created", "query.scope_unresolved"} <= actions
    prev = None
    for tenant, at, actor, action, otype, oid, outcome, details, prev_hash, row_hash in rows:
        assert prev_hash == prev
        expected = hashlib.sha256("|".join([prev or "", tenant, at, actor, action, otype, oid, outcome, details]
                                           ).encode()).hexdigest()
        assert row_hash == expected, action
        prev = row_hash
    text = " ".join(r[7] for r in rows)
    assert tpg_source["ro"][1] not in text and "PRJ-0" not in text

    with psycopg.connect(APP_URL or "", autocommit=True) as app:
        app.execute("SELECT set_config('app.tenant_id', %s, false)", (w.tenant_id,))
        for stmt in ("UPDATE ops.audit_events SET outcome = 'allow'", "DELETE FROM ops.audit_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(stmt)
