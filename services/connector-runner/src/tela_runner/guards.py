"""Result masking before rows leave the runner (US-025; RULE-020).

* secret-like column names → ``[REDACTED]``;
* e-mail addresses → first character + ``***@domain``;
* phone numbers → keep the last 4 digits;
* card-like digit runs passing a Luhn check → ``[REDACTED_PAYMENT_DATA]``.

Model field tags override the name and value heuristics: a column tagged
``identifier`` or ``public`` is left alone; ``email`` / ``phone`` are always
masked; ``secret`` is always redacted.
"""

from __future__ import annotations

import re
from typing import Any

SECRET_NAMES = re.compile(r"(password|passwd|pwd|otp|token|secret|(^|_)pin($|_)|cvv|api_?key|credential|private_?key)",
                          re.IGNORECASE)
EMAIL = re.compile(r"^([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})$")
PHONE = re.compile(r"^\+?[\d\s\-().]{7,20}$")
CARD = re.compile(r"^[\d \-]{13,23}$")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mask_email(value: str) -> str:
    m = EMAIL.match(value)
    return f"{m.group(1)}***@{m.group(2)}" if m else value


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 7:
        return value
    prefix = "+" if value.strip().startswith("+") else ""
    return prefix + "*" * (len(digits) - 4) + digits[-4:]


def _mask_text(value: str) -> str:
    v = value.strip()
    if EMAIL.match(v):
        return mask_email(v)
    if CARD.match(v):
        digits = re.sub(r"\D", "", v)
        if 13 <= len(digits) <= 19 and luhn_ok(digits):
            return "[REDACTED_PAYMENT_DATA]"
    if PHONE.match(v) and sum(c.isdigit() for c in v) >= 9 and not re.match(r"^\d{4}-\d{2}-\d{2}", v):
        return mask_phone(v)
    return value


def mask_rows(columns: list[str], rows: list[list[Any]], tags: dict[str, dict[str, str]]) -> int:
    """Mask in place; returns the number of masked cells (a count, never the values)."""
    plans: list[str] = []
    for name in columns:
        tag = tags.get(name, {})
        semantic, sensitivity = tag.get("semantic_type"), tag.get("sensitivity")
        # Tags first (they override heuristics), then the secret-name heuristic, then value heuristics.
        if semantic == "secret":
            plans.append("redact")
        elif semantic == "email":
            plans.append("email")
        elif semantic == "phone":
            plans.append("phone")
        elif semantic == "identifier" or sensitivity == "public":
            plans.append("none")
        elif SECRET_NAMES.search(name):
            plans.append("redact")
        else:
            plans.append("heuristic")
    masked = 0
    for row in rows:
        for i, plan in enumerate(plans):
            v = row[i]
            if v is None or plan == "none":
                continue
            if plan == "redact":
                row[i] = "[REDACTED]"
            elif not isinstance(v, str):
                continue
            elif plan == "email":
                row[i] = mask_email(v)
            elif plan == "phone":
                row[i] = mask_phone(v)
            else:
                row[i] = _mask_text(v)
            masked += row[i] != v
    return masked
