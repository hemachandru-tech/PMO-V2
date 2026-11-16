"""A rate limit or a passing 5xx is tried again; a refused key or a bad model name is not, and says why."""

from __future__ import annotations

import pytest

from tela_common.errors import ErrorCode, TelaError
from tela_gateway.providers.base import unavailable, with_retries


async def _no_wait(_: float) -> None:
    return None


async def test_a_rate_limit_is_tried_again_and_then_answers() -> None:
    calls: list[int] = []

    async def call() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise unavailable(429)
        return "ok"

    assert await with_retries(call, sleep=_no_wait) == "ok" and len(calls) == 3


async def test_the_last_failure_is_raised_after_the_tries_run_out() -> None:
    async def call() -> str:
        raise unavailable(503)

    with pytest.raises(TelaError) as err:
        await with_retries(call, sleep=_no_wait)
    assert err.value.details["status"] == 503


@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_a_request_the_provider_refused_is_not_retried(status: int) -> None:
    calls: list[int] = []

    async def call() -> str:
        calls.append(1)
        raise unavailable(status)

    with pytest.raises(TelaError):
        await with_retries(call, sleep=_no_wait)
    assert len(calls) == 1


def test_a_bad_model_name_says_so() -> None:
    assert "check the model name" in unavailable(400).message
    assert "rate-limiting" in unavailable(429).message
    assert unavailable(401).code == ErrorCode.PLANNER_UNAVAILABLE
