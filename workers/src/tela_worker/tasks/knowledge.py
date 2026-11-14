"""Knowledge tasks (US-100, US-057): index a model version or document, evaluate the gold set, re-embed."""

from __future__ import annotations

from typing import Any

from tela_common.logging import get_logger
from tela_worker.api import ApiRefused, InternalApi
from tela_worker.celery_app import app
from tela_worker.knowledge import run_index

log = get_logger(__name__)


@app.task(name="tela_worker.tasks.knowledge.index_knowledge", acks_late=True, autoretry_for=(OSError,),
          max_retries=3, retry_backoff=True)
def index_knowledge(tenant_id: str, kind: str, ref_id: str) -> str:
    return run_index(tenant_id, kind, ref_id)


def run_evaluation(tenant_id: str, run_id: str, api: InternalApi | None = None) -> dict[str, Any]:
    own = api is None
    api = api or InternalApi()
    try:
        return api.post(f"/api/v1/internal/knowledge/eval-runs/{run_id}/run", {"tenant_id": tenant_id})
    except ApiRefused as exc:
        log.info("knowledge.eval_refused", run_id=run_id, code=exc.code)
        return {"status": "refused"}
    finally:
        if own:
            api.close()


@app.task(name="tela_worker.tasks.knowledge.evaluate_gold_set", acks_late=True)
def evaluate_gold_set(tenant_id: str, run_id: str) -> dict[str, Any]:
    return run_evaluation(tenant_id, run_id)


@app.task(name="tela_worker.tasks.knowledge.eval_gold_set")
def eval_gold_set() -> dict[str, Any]:
    """Nightly: the API queues one run per tenant with approved gold examples (the runs follow via the outbox)."""
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/knowledge/eval-runs/nightly")
    finally:
        api.close()
    log.info("knowledge.nightly_queued", **out)
    return out


@app.task(name="tela_worker.tasks.knowledge.reembed")
def reembed() -> dict[str, Any]:
    """Embed chunks and gold examples stored while the embedding service was down (keyword-only until then)."""
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/knowledge/reembed")
    finally:
        api.close()
    if out.get("embedded"):
        log.info("knowledge.reembedded", **out)
    return out


@app.task(name="tela_worker.tasks.knowledge.refresh_values", acks_late=True, autoretry_for=(OSError,),
          max_retries=3, retry_backoff=True)
def refresh_values(tenant_id: str, version_id: str) -> dict[str, Any]:
    """The dimension value index of an approved model version (US-173)."""
    api = InternalApi()
    try:
        return api.post(f"/api/v1/internal/knowledge/model-versions/{version_id}/values", {"tenant_id": tenant_id})
    except ApiRefused as exc:
        log.info("knowledge.values_refused", version_id=version_id, code=exc.code)
        return {"status": "refused"}
    finally:
        api.close()


@app.task(name="tela_worker.tasks.knowledge.refresh_values_nightly")
def refresh_values_nightly() -> dict[str, Any]:
    """Nightly: every current model version's value index, so new values in the source link the next day."""
    api = InternalApi()
    try:
        out = api.post("/api/v1/internal/knowledge/values/nightly")
    finally:
        api.close()
    log.info("knowledge.values_refreshed", **out)
    return out

