"""Execution client for the connector runner (US-022).

Only ``tela_api.query.service`` may import this module; a CI test fails the
build otherwise, so every surface reaches sources through one scoped path.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from tela_api.runner_transport import RunnerTransport


def encode_params(params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, v in params.items():
        if v is None:
            out[name] = {"t": "null", "v": None}
        elif isinstance(v, bool):
            out[name] = {"t": "bool", "v": v}
        elif isinstance(v, int):
            out[name] = {"t": "int", "v": v}
        elif isinstance(v, float):
            out[name] = {"t": "float", "v": v}
        elif isinstance(v, Decimal):
            out[name] = {"t": "decimal", "v": str(v)}
        elif isinstance(v, datetime):
            out[name] = {"t": "datetime", "v": v.isoformat()}
        elif isinstance(v, date):
            out[name] = {"t": "date", "v": v.isoformat()}
        else:
            out[name] = {"t": "str", "v": str(v)}
    return out


async def execute(transport: RunnerTransport, *, payload: dict[str, Any], statement: str,
                  timeout_s: float) -> dict[str, Any]:
    return await transport.post("execute", payload, statement=statement, timeout_s=timeout_s)
