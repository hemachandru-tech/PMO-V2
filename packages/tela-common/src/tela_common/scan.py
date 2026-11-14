"""Pre-send leak scanning and output safety (RULE-021; S02 §1, S27 RULE-005).

A match blocks the call before anything leaves the platform. Only the pattern
*class* is reported or logged, never the matched text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("google_key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("connection_string", re.compile(r"\b(postgres(ql)?|mysql|mongodb(\+srv)?|sqlserver|redis)://[^:\s/]+:[^@\s]+@")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<![\w.])\+\d[\d\s\-]{8,14}\d\b")),
    ("injection_marker", re.compile(
        r"(?i)\b(ignore|disregard|forget)\s+(all\s+|any\s+)?(previous|prior|above)\s+(instructions|rules|prompts?)\b")),
    ("injection_marker", re.compile(r"(?i)\b(you are now|system prompt:|<\s*/?\s*system\s*>)")),
]

_CARD_CANDIDATE = re.compile(r"\b(?:\d[ \-]?){13,19}\b")

_OUTPUT_BLOCKED = re.compile(
    r"(?i)(password\s*=|api[_-]?key\s*=|private key|ignore (all )?previous instructions|-----BEGIN)"
)


def luhn_valid(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(nums) <= 19:
        return False
    checksum = 0
    for i, n in enumerate(reversed(nums)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    reasons: tuple[str, ...] = ()


PII_CLASSES = frozenset({"email", "phone"})


def scan_text(text: str, *, include_pii: bool = True) -> ScanResult:
    """Scan for leak classes. ``include_pii=False`` checks only secrets and injection markers —
    used on the *raw* payload, before PII masking could hide part of a secret (e.g. the
    ``user:pass@host`` of a connection string looks like an e-mail address)."""
    reasons: list[str] = [
        name for name, pattern in _PATTERNS
        if (include_pii or name not in PII_CLASSES) and pattern.search(text)
    ]
    if any(luhn_valid(m.group(0)) for m in _CARD_CANDIDATE.finditer(text)):
        reasons.append("payment_card")
    unique = tuple(dict.fromkeys(reasons))
    return ScanResult(clean=not unique, reasons=unique)


def output_is_safe(text: str) -> bool:
    return _OUTPUT_BLOCKED.search(text) is None


def redact_pii(text: str) -> tuple[str, tuple[str, ...]]:
    """Replace e-mail addresses and phone numbers with placeholders; return the text and the classes found."""
    found: list[str] = []
    for name, pattern in _PATTERNS:
        if name in PII_CLASSES and pattern.search(text):
            found.append(name)
            text = pattern.sub(f"[{name} removed]", text)
    return text, tuple(dict.fromkeys(found))


def redact_cards(text: str) -> tuple[str, int]:
    """Replace Luhn-valid card numbers with a placeholder; return the text and how many were replaced."""
    count = 0

    def _sub(m: re.Match[str]) -> str:
        nonlocal count
        if luhn_valid(m.group(0)):
            count += 1
            return "[card number removed]"
        return m.group(0)

    return _CARD_CANDIDATE.sub(_sub, text), count
