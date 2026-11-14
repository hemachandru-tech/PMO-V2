"""Outbox relay (TEST-097 baseline): exactly-once-effective publication, retries and dead-lettering."""

from __future__ import annotations

import json
import os
import uuid

import psycopg
import pytest

OWNER = os.environ.get("TELA_TEST_OWNER_DATABASE_URL")
APP = os.environ.get("TELA_TEST_APP_DATABASE_URL")
pytestmark = pytest.mark.skipif(not (OWNER and APP), reason="TELA_TEST_*_DATABASE_URL not set")


@pytest.fixture
def tenant() -> str:
    assert OWNER
    tid = str(uuid.uuid4())
    with psycopg.connect(OWNER, autocommit=True) as conn:
        conn.execute("TRUNCATE ops.outbox_events")
        conn.execute(
            "INSERT INTO iam.tenants (id, slug, name) VALUES (%s, %s, 'Outbox test')",
            (tid, f"outbox-{tid[:8]}"),
        )
    return tid


def _emit(tenant: str, topic: str, n: int) -> None:
    assert OWNER
    with psycopg.connect(OWNER, autocommit=True) as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO ops.outbox_events (tenant_id, topic, aggregate_id, payload) VALUES (%s, %s, %s, %s)",
                (tenant, topic, tenant, json.dumps({"i": i})),
            )


def test_relay_publishes_all_events_once(tenant: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELA_DATABASE_URL", APP or "")
    from tela_worker.tasks import outbox

    seen: list[int] = []
    monkeypatch.setitem(outbox.HANDLERS, "test.event", lambda e: seen.append(e["payload"]["i"]))
    _emit(tenant, "test.event", 250)

    total = 0
    while (result := outbox.relay_once(batch=100))["published"]:
        total += result["published"]
    assert total == 250
    assert sorted(seen) == list(range(250))
    assert outbox.relay_once()["published"] == 0  # nothing re-published

    with psycopg.connect(APP or "") as conn:
        lag = conn.execute("SELECT ops.f_outbox_lag_seconds()").fetchone()
    assert lag is not None and lag[0] == 0


def test_failing_handler_retries_then_dead_letters(tenant: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELA_DATABASE_URL", APP or "")
    from tela_worker.tasks import outbox

    def boom(_: dict) -> None:
        raise RuntimeError("downstream unavailable")

    monkeypatch.setitem(outbox.HANDLERS, "test.boom", boom)
    _emit(tenant, "test.boom", 1)
    for _ in range(10):
        assert outbox.relay_once()["failed"] == 1
    assert outbox.relay_once() == {"published": 0, "failed": 0}

    assert OWNER
    with psycopg.connect(OWNER) as conn:
        row = conn.execute(
            "SELECT attempts, dead_at IS NOT NULL, last_error FROM ops.outbox_events WHERE topic = 'test.boom'"
        ).fetchone()
    assert row is not None
    assert row[0] == 10 and row[1] is True and "downstream unavailable" in row[2]


def test_requeue_cli_replays_dead_letters(tenant: str, monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("TELA_DATABASE_URL", APP or "")
    monkeypatch.setenv("TELA_MIGRATE_DATABASE_URL", OWNER or "")
    from tela_api.cli import main
    from tela_worker.tasks import outbox

    assert OWNER
    _emit(tenant, "test.late", 2)
    _emit(tenant, "test.other", 1)
    with psycopg.connect(OWNER, autocommit=True) as conn:
        conn.execute("UPDATE ops.outbox_events SET dead_at = now(), attempts = 10, last_error = 'LookupError: no "
                     "handler'")

    main(["outbox-requeue", "--topic", "test.late", "--dry-run"])
    assert json.loads(capsys.readouterr().out) == {"dry_run": True, "requeued": {"test.late": 2}}
    assert outbox.relay_once() == {"published": 0, "failed": 0}  # dry run changed nothing

    seen: list[int] = []
    monkeypatch.setitem(outbox.HANDLERS, "test.late", lambda e: seen.append(e["payload"]["i"]))
    main(["outbox-requeue", "--topic", "test.late"])
    assert json.loads(capsys.readouterr().out)["requeued"] == {"test.late": 2}
    assert outbox.relay_once() == {"published": 2, "failed": 0}
    assert sorted(seen) == [0, 1]

    with psycopg.connect(OWNER) as conn:
        rows = dict(conn.execute("SELECT topic, count(*) FILTER (WHERE dead_at IS NOT NULL) FROM ops.outbox_events "
                                 "GROUP BY topic").fetchall())
    assert rows == {"test.late": 0, "test.other": 1}  # other topics stay dead-lettered
    main(["outbox-requeue", "--topic", "test.late"])
    assert json.loads(capsys.readouterr().out)["requeued"] == {}  # published events are never replayed
