"""Outbox relay (US-009; DATA-050; TEST-097).

Claims pending events across tenants with ``ops.f_claim_outbox`` (row locks,
SKIP LOCKED so several relays can run), hands each to its topic handler and
marks it published or failed. After 10 failed attempts an event is
dead-lettered for an Admin view. Handlers must be idempotent.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tela_common.logging import get_logger
from tela_worker.celery_app import app
from tela_worker.db import connect

log = get_logger(__name__)

Handler = Callable[[dict[str, Any]], None]
HANDLERS: dict[str, Handler] = {}


def handler(topic: str) -> Callable[[Handler], Handler]:
    def _register(fn: Handler) -> Handler:
        HANDLERS[topic] = fn
        return fn

    return _register


@handler("access.mappings_changed")
def _mappings_changed(event: dict[str, Any]) -> None:
    # API replicas also expire cached principals within 60 s; nothing else to do in P0.
    log.info("outbox.access_mappings_changed", tenant_id=event["tenant_id"])


@handler("export.requested")
def _export_requested(event: dict[str, Any]) -> None:
    # The render runs on its own queue so a slow PDF never holds up the relay (US-095).
    app.send_task("tela_worker.tasks.exports.render_export", args=[str(event["tenant_id"]),
                                                                    event["payload"]["job_id"]], queue="export")


@handler("model.version_approved")
def _model_approved(event: dict[str, Any]) -> None:
    # Re-index the approved model's definitions (US-100); the API then queues a gold-set run (US-057).
    app.send_task("tela_worker.tasks.knowledge.index_knowledge",
                  args=[str(event["tenant_id"]), "model_version", str(event["aggregate_id"])], queue="index")
    # ... and its dimension values (US-173), on its own task so a slow source never holds up the definitions.
    app.send_task("tela_worker.tasks.knowledge.refresh_values",
                  args=[str(event["tenant_id"]), str(event["aggregate_id"])], queue="index")


@handler("knowledge.document_uploaded")
def _document_uploaded(event: dict[str, Any]) -> None:
    app.send_task("tela_worker.tasks.knowledge.index_knowledge",
                  args=[str(event["tenant_id"]), "document", event["payload"]["document_id"]], queue="index")


@handler("knowledge.evaluation_requested")
def _evaluation_requested(event: dict[str, Any]) -> None:
    app.send_task("tela_worker.tasks.knowledge.evaluate_gold_set",
                  args=[str(event["tenant_id"]), event["payload"]["run_id"]], queue="index")


@handler("dashboard.published")
@handler("certification.decided")
@handler("connection.changed")
def _recorded(event: dict[str, Any]) -> None:
    # The API writes notifications and audit in the same transaction; these events need no worker action yet,
    # but they must not dead-letter (EXEC-016 follow-up).
    log.info("outbox.recorded", topic=event["topic"], tenant_id=event["tenant_id"])


def relay_once(batch: int = 100, conn: Any = None) -> dict[str, int]:
    own = conn is None
    conn = conn or connect()
    published = failed = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, tenant_id, topic, aggregate_id, payload FROM ops.f_claim_outbox(%s)", (batch,))
            events = [dict(zip(("id", "tenant_id", "topic", "aggregate_id", "payload"), r, strict=True))
                      for r in cur.fetchall()]
        conn.commit()
        for event in events:
            fn = HANDLERS.get(event["topic"])
            try:
                if fn is None:
                    raise LookupError(f"no handler for topic {event['topic']}")
                fn(event)
                ok, err = True, None
                published += 1
            except Exception as exc:  # noqa: BLE001 - recorded on the event, retried later
                ok, err = False, f"{type(exc).__name__}: {exc}"
                failed += 1
            with conn.cursor() as cur:
                cur.execute("SELECT ops.f_mark_outbox(%s, %s, %s)", (event["id"], ok, err))
            conn.commit()
    finally:
        if own:
            conn.close()
    return {"published": published, "failed": failed}


LAG_KEY = "tela:metrics:outbox_lag"
LAG_TTL_S = 30  # ~15 relay runs: a missing key means the relay stopped reporting


def publish_lag(lag_seconds: float, redis_client: Any = None) -> None:
    """Hand the lag to the API's /metrics through Redis (the API role cannot read ``ops``)."""
    try:
        if redis_client is None:
            from redis import Redis

            redis_client = Redis.from_url(app.conf.broker_url)
        redis_client.set(LAG_KEY, f"{lag_seconds:.3f}", ex=LAG_TTL_S)
    except Exception as exc:  # noqa: BLE001 - metrics must never fail the relay
        log.warning("outbox.lag_publish_failed", error=type(exc).__name__)


@app.task(name="tela_worker.tasks.outbox.outbox_relay")
def outbox_relay() -> dict[str, int]:
    conn = connect()
    try:
        result = relay_once(conn=conn)
        with conn.cursor() as cur:
            cur.execute("SELECT ops.f_outbox_lag_seconds()")
            row = cur.fetchone()
            lag = row[0] if row else 0.0
        conn.commit()
    finally:
        conn.close()
    publish_lag(float(lag))
    return result
