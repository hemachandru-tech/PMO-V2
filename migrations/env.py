"""Alembic environment for the Tela metadata database.

Migrations are written as explicit SQL (no autogenerate) so the DDL reviewed in
db_scheme.md is exactly what runs. They execute as the schema *owner* role; the
application connects as a separate non-owner role without BYPASSRLS (US-006).
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool


def _database_url() -> str:
    file_path = os.environ.get("TELA_MIGRATE_DATABASE_URL_FILE")
    if file_path:
        return Path(file_path).read_text().strip()
    url = os.environ.get("TELA_MIGRATE_DATABASE_URL")
    if not url:
        raise SystemExit("Set TELA_MIGRATE_DATABASE_URL or TELA_MIGRATE_DATABASE_URL_FILE")
    return url


def _sync_url(url: str) -> str:
    # Accept asyncpg-style URLs and run migrations with psycopg 3.
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://").replace(
        "postgresql://", "postgresql+psycopg://", 1
    ) if "+psycopg" not in url else url


def run_migrations_offline() -> None:
    context.configure(url=_sync_url(_database_url()), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url(_database_url()), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transaction_per_migration=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
