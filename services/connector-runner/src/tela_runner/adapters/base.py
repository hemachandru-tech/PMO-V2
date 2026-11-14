"""Connector adapter contract (US-024; ADR-002; technical_document §Adapter contract).

Every engine implements the same protocol so the query engine never changes
when an engine is added. Adapters are the only code that opens a source
connection. The connector contract test suite (``tests/test_contract_*``)
runs unchanged against every adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

SCHEMA_CHANGED_MESSAGE = ("The source structure changed since the model was approved. Ask your admin to "
                          "re-read the schema and approve the model again.")


@dataclass(frozen=True)
class Limits:
    max_rows: int = 10_000  # RULE-006
    timeout_ms: int = 20_000  # RULE-006
    max_bytes: int = 10 * 1024 * 1024


@dataclass(frozen=True)
class ConnectionInfo:
    """Everything needed to reach one source. Built from a signed envelope + the secrets store."""

    connection_id: str
    engine: str
    descriptor: dict[str, Any]
    credentials: dict[str, str]

    @property
    def schemas(self) -> list[str]:
        return list(self.descriptor.get("approved_schemas") or ["public"])


@dataclass
class ReadOnlyVerdict:
    verdict: str  # safe | unsafe
    checks: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return self.verdict == "safe"


@dataclass
class TestResult:
    reachable: bool
    authenticated: bool
    read_only: ReadOnlyVerdict
    latency_ms: int
    server_version: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. suggest_mode when Dataverse TDS is off (US-111)


@dataclass
class CatalogObject:
    qualified_name: str
    kind: str  # table | view
    columns: list[dict[str, Any]]  # {name, type, nullable, pk, fk_ref}
    primary_key: list[str]
    foreign_keys: list[dict[str, Any]]  # {columns, ref_object, ref_columns}
    row_estimate: int | None
    checksum: str
    readable: bool | None = None  # the account may read the rows (checked by engines whose schema lists more)


@dataclass
class Catalog:
    objects: list[CatalogObject]
    checksum: str
    complete: bool = True
    schemas: dict[str, int] = field(default_factory=dict)  # object count per schema (for A1)
    profile: dict[str, Any] = field(default_factory=dict)  # file sources: sheets, header rows, column flags


@dataclass
class ResultSet:
    columns: list[dict[str, str]]  # {name, type}
    rows: list[list[Any]]
    truncated: bool
    duration_ms: int


class ConnectorAdapter(Protocol):
    engine: str

    async def test_connection(self, info: ConnectionInfo) -> TestResult: ...
    async def verify_read_only(self, info: ConnectionInfo) -> ReadOnlyVerdict: ...
    async def introspect(self, info: ConnectionInfo, *, max_objects: int) -> Catalog: ...
    def capabilities(self) -> dict[str, Any]: ...
    async def object_columns(self, info: ConnectionInfo, names: list[str]) -> dict[str, list[tuple[str, str]]]: ...
    async def execute(self, info: ConnectionInfo, sql: str, params: dict[str, Any], limits: Limits,
                      execution_id: str) -> ResultSet: ...
    async def cancel(self, execution_id: str) -> bool: ...
    async def close(self) -> None: ...
