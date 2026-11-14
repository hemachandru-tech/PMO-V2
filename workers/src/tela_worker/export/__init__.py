"""Export renderers (US-096 PDF, US-097 Excel). Each takes the API's payload and returns the file's bytes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

Renderer = Callable[[dict[str, Any]], bytes]
CONTENT_TYPES = {"pdf": "application/pdf",
                 "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
RENDERERS: dict[str, Renderer] = {}


def renderer(fmt: str) -> Callable[[Renderer], Renderer]:
    def _register(fn: Renderer) -> Renderer:
        RENDERERS[fmt] = fn
        return fn

    return _register
