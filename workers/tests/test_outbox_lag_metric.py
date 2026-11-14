"""US-010: the relay hands outbox lag to Redis for the API's /metrics."""

from __future__ import annotations

from tela_worker.tasks.outbox import LAG_KEY, LAG_TTL_S, publish_lag


class FakeRedis:
    def __init__(self) -> None:
        self.set_args: tuple[str, str, int] | None = None

    def set(self, key: str, value: str, ex: int) -> None:
        self.set_args = (key, value, ex)


class BrokenRedis:
    def set(self, *_: object, **__: object) -> None:
        raise ConnectionError("redis down")


def test_lag_is_published_with_a_short_ttl() -> None:
    r = FakeRedis()
    publish_lag(12.3456, r)
    assert r.set_args == (LAG_KEY, "12.346", LAG_TTL_S)


def test_a_redis_outage_never_fails_the_relay() -> None:
    publish_lag(1.0, BrokenRedis())
