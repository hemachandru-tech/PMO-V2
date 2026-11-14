"""Metadata-only invocation ledger (DATA-046). Never stores prompt or completion text (S02 §1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tela_common.ids import new_id


@dataclass(frozen=True)
class Invocation:
    tenant_id: str
    user_id: str | None
    turn_id: str | None
    agent: str
    task: str
    provider: str
    model: str
    payload_hash: str
    scanner_verdict: str  # clean | blocked
    status: str  # ok | blocked | invalid_output | timeout | error
    blocked_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None


class LedgerSink(Protocol):
    async def write(self, inv: Invocation) -> None: ...


class MemoryLedger:
    def __init__(self) -> None:
        self.rows: list[Invocation] = []

    async def write(self, inv: Invocation) -> None:
        self.rows.append(inv)


class DbLedger:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def write(self, inv: Invocation) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": inv.tenant_id})
            await conn.execute(
                text(
                    """
                    INSERT INTO ops.llm_invocations
                      (id, tenant_id, user_id, turn_id, agent, task, provider, model, payload_hash,
                       scanner_verdict, blocked_reason, prompt_tokens, completion_tokens, latency_ms, status)
                    VALUES (:id, :tenant_id, :user_id, :turn_id, :agent, :task, :provider, :model, :payload_hash,
                            :scanner_verdict, :blocked_reason, :prompt_tokens, :completion_tokens, :latency_ms,
                            :status)
                    """
                ),
                {"id": new_id(), **inv.__dict__},
            )
