"""Append-only audit events (FEAT-048 backend, US-043; REQ-115, NFR-007; DATA-045).

Events are written in the caller's transaction so a state change and its
audit record commit together. Details never contain secrets or row values;
they pass through the same redaction as logs as a second line of defence.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tela_common.logging import redact

Outcome = Literal["allow", "deny", "success", "failure"]
Severity = Literal["normal", "high", "security"]


async def record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    action: str,
    object_type: str,
    outcome: Outcome,
    correlation_id: str | None,
    actor_id: str | None = None,
    object_id: str | None = None,
    severity: Severity = "normal",
    details: dict[str, Any] | None = None,
    actor_ip: str | None = None,
) -> None:
    safe_details = dict(redact(dict(details or {})))
    await conn.execute(
        text(
            """
            INSERT INTO ops.audit_events
              (tenant_id, actor_id, actor_ip, action, object_type, object_id, outcome, severity,
               correlation_id, details)
            VALUES (:tenant_id, :actor_id, CAST(:actor_ip AS inet), :action, :object_type, :object_id,
                    :outcome, :severity, :correlation_id, CAST(:details AS jsonb))
            """
        ),
        {
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "actor_ip": actor_ip,
            "action": action,
            "object_type": object_type,
            "object_id": object_id,
            "outcome": outcome,
            "severity": severity,
            "correlation_id": correlation_id or "none",
            "details": json.dumps(safe_details, default=str),
        },
    )
