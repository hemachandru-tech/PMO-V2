"""Celery application (ADR-011; technical_document §Background jobs and events).

Queues: default, index, export, introspect, maintenance. Tasks are idempotent
and acknowledged late so a lost worker never loses a job.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog
from celery import Celery
from celery.schedules import crontab
from celery.signals import task_postrun, task_prerun, worker_process_init
from kombu import Queue

from tela_common.logging import configure_logging
from tela_common.observability import setup_tracing


def _redis_url() -> str:
    path = os.environ.get("TELA_REDIS_URL_FILE")
    if path:
        return Path(path).read_text().strip()
    return os.environ.get("TELA_REDIS_URL", "redis://localhost:6379/0")


configure_logging("tela-worker", os.environ.get("TELA_LOG_LEVEL", "INFO"))



@worker_process_init.connect
def _start_tracing(**_: object) -> None:
    """Per child process: the OTLP exporter's thread must not be created before the fork."""
    setup_tracing("tela-worker", os.environ.get("TELA_OTEL_EXPORTER_OTLP_ENDPOINT") or None,
                  instrument=("httpx", "celery"))


@task_prerun.connect
def _bind_task(task_id: str, task: object, **_: object) -> None:
    """The task id is the correlation id for everything the task logs or calls (forwarded to the API)."""
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=task_id, task=getattr(task, "name", None))


@task_postrun.connect
def _unbind_task(**_: object) -> None:
    structlog.contextvars.clear_contextvars()


app = Celery("tela", broker=_redis_url(), include=["tela_worker.tasks.outbox", "tela_worker.tasks.maintenance",
                                                "tela_worker.tasks.exports", "tela_worker.tasks.knowledge"])
app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 3600},
    task_default_queue="default",
    task_queues=[Queue(q) for q in ("default", "index", "export", "introspect", "maintenance")],
    task_routes={
        "tela_worker.tasks.maintenance.*": {"queue": "maintenance"},
        "tela_worker.tasks.outbox.*": {"queue": "default"},
        "tela_worker.tasks.exports.render_export": {"queue": "export"},
        "tela_worker.tasks.exports.sweep_exports": {"queue": "maintenance"},
        "tela_worker.tasks.exports.sweep_files": {"queue": "maintenance"},
        "tela_worker.tasks.knowledge.*": {"queue": "index"},
    },
    timezone="UTC",
    beat_schedule={
        "outbox-relay": {"task": "tela_worker.tasks.outbox.outbox_relay", "schedule": 2.0},
        "ensure-partitions": {"task": "tela_worker.tasks.maintenance.ensure_partitions",
                              "schedule": crontab(minute=10, hour=1)},
        "export-sweep": {"task": "tela_worker.tasks.exports.sweep_exports", "schedule": crontab(minute=15)},
        "file-source-sweep": {"task": "tela_worker.tasks.exports.sweep_files", "schedule": crontab(minute=20, hour=3)},
        "eval-gold-set": {"task": "tela_worker.tasks.knowledge.eval_gold_set",
                          "schedule": crontab(minute=45, hour=2)},
        "knowledge-reembed": {"task": "tela_worker.tasks.knowledge.reembed", "schedule": crontab(minute="*/20")},
        "knowledge-values": {"task": "tela_worker.tasks.knowledge.refresh_values_nightly",
                             "schedule": crontab(minute=15, hour=2)},
        "retention-sweep": {"task": "tela_worker.tasks.maintenance.retention_sweep",
                            "schedule": crontab(minute=30, hour=2)},
        "agent-runs-sweep": {"task": "tela_worker.tasks.maintenance.agent_runs_sweep",
                             "schedule": crontab(minute=35, hour=2)},
        "pack-update-check": {"task": "tela_worker.tasks.maintenance.pack_update_check",
                              "schedule": crontab(minute=0, hour=5)},
        "scim-reconcile": {"task": "tela_worker.tasks.maintenance.scim_reconcile", "schedule": crontab(minute=40)},
        "permission-grant-sweep": {"task": "tela_worker.tasks.maintenance.permission_grant_sweep",
                                   "schedule": crontab(minute=5, hour=6)},
    },
)
