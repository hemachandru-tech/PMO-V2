"""Payload masking applied before scanning (RULE-019, RULE-020).

Context packs built by the API should already exclude row values, samples and
hidden fields. As a second layer, the gateway masks personal identifiers it
can recognise so that only *unexpected* leaks are left for the scanner to
block. Masking is deterministic: the same value gets the same placeholder
within one request, so the model can still refer to it consistently.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-]+)@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
_PHONE = re.compile(r"(?<![\w.])\+\d[\d\s\-]{8,14}\d\b")


class Masker:
    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def _placeholder(self, kind: str, value: str) -> str:
        if value not in self._map:
            self._map[value] = f"<{kind}_{len(self._map) + 1}>"
        return self._map[value]

    def mask(self, text: str, *, known_names: list[str] | None = None) -> str:
        out = _EMAIL.sub(lambda m: self._placeholder("email", m.group(0)), text)
        out = _PHONE.sub(lambda m: self._placeholder("phone", m.group(0)), out)
        for name in sorted(known_names or [], key=len, reverse=True):
            if len(name) >= 3:
                out = re.sub(re.escape(name), self._placeholder("person", name), out)
        return out
