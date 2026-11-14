"""Dataverse sandbox check (US-117 nightly; US-058 evidence): the adapter against a *real* environment.

Runs only when the sandbox is configured (``.github/workflows/dataverse-nightly.yml`` passes the repository
secrets); otherwise skipped. Read-only throughout: sign-in, the read-only proof, metadata, one count.

    TELA_DATAVERSE_SANDBOX_URL=https://<org>.crm4.dynamics.com
    TELA_DATAVERSE_TENANT_ID=... TELA_DATAVERSE_CLIENT_ID=... TELA_DATAVERSE_CLIENT_SECRET=...
    TELA_DATAVERSE_TABLES=tpg_project,tpg_projectrequest   TELA_DATAVERSE_MODE=tds|webapi
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tela_runner.adapters.base import ConnectionInfo, Limits

URL = os.environ.get("TELA_DATAVERSE_SANDBOX_URL")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URL, reason="no Dataverse sandbox configured")]


def info() -> ConnectionInfo:
    from urllib.parse import urlsplit

    return ConnectionInfo("dataverse-sandbox", "dataverse", {
        "environment_url": URL, "host": urlsplit(URL or "").hostname, "tenant_id": os.environ["TELA_DATAVERSE_TENANT_ID"],
        "client_id": os.environ["TELA_DATAVERSE_CLIENT_ID"], "mode": os.environ.get("TELA_DATAVERSE_MODE", "tds"),
        "tables": os.environ.get("TELA_DATAVERSE_TABLES", "tpg_project").split(","), "approved_schemas": ["dbo"]},
        {"client_id": os.environ["TELA_DATAVERSE_CLIENT_ID"],
         "client_secret": os.environ["TELA_DATAVERSE_CLIENT_SECRET"]})


@pytest.fixture
async def adapter() -> Any:
    from tela_runner.adapters.dataverse import DataverseAdapter

    a = DataverseAdapter(allow_insecure_tls=False)
    yield a
    await a.close()


async def test_sandbox_is_reachable_read_only_and_described(adapter: Any) -> None:
    result = await adapter.test_connection(info())
    assert result.reachable and result.authenticated, result.error
    assert result.read_only.safe, result.read_only.reasons
    catalog = await adapter.introspect(info())
    assert catalog.objects and all(o.primary_key for o in catalog.objects)


async def test_sandbox_count(adapter: Any) -> None:
    table = os.environ.get("TELA_DATAVERSE_TABLES", "tpg_project").split(",")[0]
    if info().descriptor["mode"] == "tds":
        rows = await adapter.execute(info(), f"SELECT count(*) AS n FROM dbo.{table}", {}, Limits(), "sandbox")
    else:
        import json

        meta = await adapter._metadata(info(), table)
        rows = await adapter.execute(info(), json.dumps({
            "entity": f"dbo.{table}", "entity_set": meta.entity_set, "format": "odata",
            "query": {"$apply": f"filter({meta.primary_id} ne null)/aggregate($count as n)"}, "columns": ["n"],
            "sources": {"n": "n"}}), {}, Limits(), "sandbox")
    assert rows.rows and rows.rows[0][0] >= 0
