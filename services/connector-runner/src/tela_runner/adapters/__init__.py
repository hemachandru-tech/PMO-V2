"""Connector adapters (ADR-002): PostgreSQL (P1); MySQL, SQL Server, Excel/CSV, MongoDB and Dataverse (P3)."""

from __future__ import annotations

from typing import Any

from tela_runner.adapters.base import ConnectorAdapter
from tela_runner.adapters.dataverse import DataverseAdapter
from tela_runner.adapters.files import FileAdapter
from tela_runner.adapters.mongodb import MongoAdapter
from tela_runner.adapters.mysql import MySqlAdapter
from tela_runner.adapters.postgres import PostgresAdapter
from tela_runner.adapters.sqlserver import SqlServerAdapter

ADAPTERS: dict[str, type[Any]] = {"postgres": PostgresAdapter, "mysql": MySqlAdapter, "sqlserver": SqlServerAdapter,
                                  "mongodb": MongoAdapter, "dataverse": DataverseAdapter}


def build_adapters(**kwargs: object) -> dict[str, ConnectorAdapter]:
    built: dict[str, ConnectorAdapter] = {engine: cls(**kwargs) for engine, cls in ADAPTERS.items()}
    files = FileAdapter(**kwargs)  # type: ignore[arg-type]
    built["excel"] = built["csv"] = files  # one converted-file cache for both
    return built
