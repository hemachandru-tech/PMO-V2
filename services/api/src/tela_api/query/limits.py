"""Row and time limits (US-021; RULE-006; US-177 window limits).

Defaults are 10,000 rows and 20 s; a connection may set lower values. The
compiled query asks for ``cap + 1`` rows so truncation can be detected; the
runner enforces the same cap and a 10 MiB payload limit as a backstop.

Window operation limits (US-177): top_n_per_group n ≤ 50, moving_avg window
≤ 24, NTILE buckets ≤ 10. These are enforced at parse time by the spec model;
the limits module validates that the total result rows after a window
operation stay within the connection row cap.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

DEFAULT_MAX_ROWS = 10_000
DEFAULT_TIMEOUT_MS = 20_000
MAX_PAYLOAD_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Limits:
    max_rows: int = DEFAULT_MAX_ROWS
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    max_bytes: int = MAX_PAYLOAD_BYTES

    @classmethod
    def for_connection(cls, row_cap: int | None, timeout_ms: int | None) -> Limits:
        return cls(
            max_rows=min(row_cap or DEFAULT_MAX_ROWS, DEFAULT_MAX_ROWS),
            timeout_ms=min(timeout_ms or DEFAULT_TIMEOUT_MS, DEFAULT_TIMEOUT_MS),
        )

    def as_dict(self) -> dict[str, int]:
        return {"max_rows": self.max_rows, "timeout_ms": self.timeout_ms, "max_bytes": self.max_bytes}


def apply_limit(tree: exp.Select, limits: Limits, requested: int | None) -> tuple[exp.Select, int, bool]:
    """Return (tree, rows_to_return, detect_truncation).

    A spec limit (top N) is honoured as is. Otherwise the query fetches cap + 1
    rows; if cap + 1 come back the result is cut to cap and flagged truncated.
    Exactly cap rows is not truncated (RULE-006 boundary).
    """
    if requested is not None:
        n = min(requested, limits.max_rows)
        return tree.limit(n, copy=False), n, False
    return tree.limit(limits.max_rows + 1, copy=False), limits.max_rows, True
