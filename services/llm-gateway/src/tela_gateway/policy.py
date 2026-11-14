"""Policy checks before any provider call: task, provider allow-list, aggregates, budget, rate (US-011).

In-process counters are used for a single replica. Multi-replica deployments
swap in the Redis-backed limiter (planned with the Helm profile, US-121).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any

from tela_common.errors import ErrorCode, TelaError
from tela_gateway.settings import GatewaySettings
from tela_gateway.tenant_settings import ROLE_BY_TASK

# Tasks whose payload carries aggregate results (AG-08 narration; RULE-019, DEC-01).
AGGREGATE_TASKS = frozenset({"narrate"})


class PolicyEngine:
    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self._user_calls: dict[str, deque[float]] = defaultdict(deque)
        self._tenant_tokens: dict[tuple[str, str], int] = defaultdict(int)

    def check(self, *, tenant_id: str, user_id: str | None, task: str,
              tenant: dict[str, Any] | None = None) -> tuple[str, str]:
        """Return (provider, model) to use, or raise a typed refusal.

        The tenant's AI settings choose the provider and models; the deployment allow-list bounds them.
        """
        s = self.settings
        if task not in s.llm_allowed_tasks:
            raise TelaError(ErrorCode.FORBIDDEN, f"Task '{task}' is not allowed through the gateway.")
        if task in AGGREGATE_TASKS and (not s.llm_allow_aggregates or (tenant or {}).get("allow_aggregates") is False):
            raise TelaError(ErrorCode.FORBIDDEN, "Sending totals and counts to a model is turned off.")
        provider = (tenant or {}).get("provider") or s.llm_provider
        if not provider:
            raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "No language model is enabled for this deployment.")
        if provider not in s.llm_allowed_providers:
            raise TelaError(ErrorCode.PROVIDER_NOT_ALLOWED, "This model provider is not approved.")
        models = (tenant or {}).get("models") or {}
        model = (models.get(ROLE_BY_TASK.get(task, "planner")) or models.get("planner")
                 or s.llm_model_by_task.get(task) or s.llm_model_default or "")
        if not model:
            raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "No model is configured for this task.")

        budget = int((tenant or {}).get("monthly_token_budget") or 0) or s.llm_budget_tokens_monthly
        month = datetime.now(UTC).strftime("%Y-%m")
        if budget and self._tenant_tokens[(tenant_id, month)] >= budget:
            raise TelaError(ErrorCode.BUDGET_EXCEEDED, "This month's AI budget is used up.")

        if user_id:
            window = self._user_calls[f"{tenant_id}:{user_id}"]
            now = time.monotonic()
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= s.llm_user_requests_per_minute:
                raise TelaError(ErrorCode.RATE_LIMITED, "Too many AI requests; try again in a minute.")
            window.append(now)
        return provider, model

    def record_usage(self, tenant_id: str, tokens: int) -> None:
        month = datetime.now(UTC).strftime("%Y-%m")
        self._tenant_tokens[(tenant_id, month)] += max(tokens, 0)
