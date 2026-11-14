"""US-010: outbox lag gauge fed from the worker's Redis sample; no sample means the relay is down."""

from __future__ import annotations

import asyncio

from prometheus_client import REGISTRY

from tela_api.core.metrics import OUTBOX_LAG_KEY, refresh_outbox_lag


class FakeRedis:
    def __init__(self, value: str | None) -> None:
        self.value = value

    async def get(self, key: str) -> str | None:
        assert key == OUTBOX_LAG_KEY
        return self.value


class BrokenRedis:
    async def get(self, key: str) -> str:
        raise ConnectionError("down")


def _gauge(name: str) -> float | None:
    return REGISTRY.get_sample_value(name)


def test_sample_sets_lag_and_marks_relay_up() -> None:
    asyncio.run(refresh_outbox_lag(FakeRedis("7.5")))
    assert _gauge("tela_outbox_lag_seconds") == 7.5
    assert _gauge("tela_outbox_relay_up") == 1


def test_missing_sample_or_outage_marks_relay_down() -> None:
    for redis in (FakeRedis(None), BrokenRedis()):
        asyncio.run(refresh_outbox_lag(redis))
        assert _gauge("tela_outbox_relay_up") == 0


def test_no_redis_is_a_no_op() -> None:
    asyncio.run(refresh_outbox_lag(None))
