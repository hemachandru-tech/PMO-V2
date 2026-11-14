"""UUID v7 identifiers (time-ordered, index friendly) — db_scheme §10."""

from __future__ import annotations

import os
import time
import uuid

_last_ms = 0
_seq = 0


def uuid7() -> uuid.UUID:
    """Return an RFC 9562 UUID version 7.

    48-bit Unix millisecond timestamp, then version and variant bits, with a
    12-bit counter in ``rand_a`` so ids created in the same millisecond stay
    monotonic within this process.
    """
    global _last_ms, _seq
    ms = time.time_ns() // 1_000_000
    if ms <= _last_ms:
        ms = _last_ms
        _seq = (_seq + 1) & 0xFFF
        if _seq == 0:  # counter overflow: move to the next millisecond
            ms += 1
    else:
        _seq = int.from_bytes(os.urandom(2), "big") & 0x3FF
    _last_ms = ms
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= _seq << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


def new_id() -> str:
    """UUID v7 as a string, the API's id format."""
    return str(uuid7())
