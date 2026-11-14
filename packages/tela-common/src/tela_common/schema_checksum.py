"""Column-level checksums for schema drift detection (US-023, US-030).

A model version records, per source object, a checksum over the columns it
actually uses. The runner recomputes it from the live catalogue before each
execution; a renamed, removed or retyped column changes it, while unrelated
new columns do not.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


def columns_checksum(columns: Iterable[tuple[str, str]]) -> str:
    parts = sorted(f"{name.lower()}:{typ.lower()}" for name, typ in columns)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()
