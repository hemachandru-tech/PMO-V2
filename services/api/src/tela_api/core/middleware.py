"""Request id / correlation id propagation and request metrics (NFR-017)."""

from __future__ import annotations

import re
import time

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from tela_api.core.metrics import REQUEST_DURATION
from tela_common.ids import new_id
from tela_common.logging import get_logger

log = get_logger(__name__)
_SAFE_ID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _SAFE_ID.match(incoming) else new_id()
        request.state.request_id = request_id
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        REQUEST_DURATION.labels(method=request.method, route=path, status=str(response.status_code)).observe(elapsed)
        response.headers["x-request-id"] = request_id
        if path not in ("/healthz", "/readyz", "/metrics"):
            log.info("http.request", method=request.method, route=path, status=response.status_code,
                     duration_ms=round(elapsed * 1000, 1))
        return response
