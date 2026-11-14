"""Database access for workers.

Workers connect as the application role. Cross-tenant system jobs (outbox
relay, partition maintenance) use narrow SECURITY DEFINER functions instead of
an RLS-bypassing role.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg


def dsn() -> str:
    path = os.environ.get("TELA_DATABASE_URL_FILE")
    url = Path(path).read_text().strip() if path else os.environ.get("TELA_DATABASE_URL", "")
    if not url:
        raise RuntimeError("TELA_DATABASE_URL or TELA_DATABASE_URL_FILE is required")
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        url = url.replace(prefix, "postgresql://")
    return url


def connect() -> psycopg.Connection:
    return psycopg.connect(dsn())
