"""Shared helpers for API tests (constants, data reset, tenant bootstrap)."""

from __future__ import annotations

import os

import psycopg
import pytest

from tela_common.ids import new_id

ISSUER = "http://keycloak.test/realms/acme"
OTHER_ISSUER = "http://keycloak.test/realms/globex"
AUDIENCE = "tela-api"
KID = "test-key-1"



def _worker_database() -> None:
    """Under pytest-xdist (``-n``) each worker gets its own copy of the migrated test database, made from it as a
    template (``tela`` → ``tela_gw0`` …), so workers never share rows. The TELA_TEST_* URLs are pointed at the copy
    before anything reads them. The template must have no open sessions while the copies are made."""
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    owner, app = os.environ.get("TELA_TEST_OWNER_DATABASE_URL"), os.environ.get("TELA_TEST_APP_DATABASE_URL")
    if not (worker and owner and app) or os.environ.get("TELA_TEST_WORKER_DB") == worker:
        return
    import time
    from urllib.parse import urlsplit

    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    parts = conninfo_to_dict(owner)
    template = parts.get("dbname") or "tela"
    copy = f"{template}_{worker}"
    with psycopg.connect(make_conninfo(**{**parts, "dbname": "postgres"}), autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(copy)))
        for attempt in range(30):  # workers copy the same template at once; a copy in progress blocks the next
            try:
                admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(copy),
                                                                            sql.Identifier(template)))
                break
            except psycopg.errors.ObjectInUse:
                if attempt == 29:
                    raise
                time.sleep(0.5)
    # Keep the URL form (SQLAlchemy and alembic read these too): only the database name changes.
    os.environ["TELA_TEST_OWNER_DATABASE_URL"] = urlsplit(owner)._replace(path=f"/{copy}").geturl()
    os.environ["TELA_TEST_APP_DATABASE_URL"] = urlsplit(app)._replace(path=f"/{copy}").geturl()
    if os.environ.get("TELA_MIGRATE_DATABASE_URL"):
        os.environ["TELA_MIGRATE_DATABASE_URL"] = os.environ["TELA_TEST_OWNER_DATABASE_URL"]
    os.environ["TELA_TEST_WORKER_DB"] = worker


_worker_database()
OWNER_URL = os.environ.get("TELA_TEST_OWNER_DATABASE_URL")
APP_URL = os.environ.get("TELA_TEST_APP_DATABASE_URL")

needs_db = pytest.mark.skipif(not (OWNER_URL and APP_URL), reason="TELA_TEST_*_DATABASE_URL not set")

RESET_TABLES = (
    "rag.curation_evidence, rag.curation_proposals, rag.value_synonyms, rag.dimension_values, rag.eval_results, rag.eval_runs, rag.gold_examples, rag.knowledge_chunks, rag.knowledge_documents, "
    "rag.policies, gov.lookalike_findings, gov.certification_decisions, gov.certification_requests, gov.policies, "
    "gov.definition_embeddings, content.personal_fields, content.custom_sql_definitions, content.drafts_undo_log, content.publish_requests, content.shares, content.report_edit_previews, content.report_plans, "
    "content.dashboard_group_items, content.dashboard_groups, content.dashboard_reports, content.dashboard_versions, content.report_items, content.report_sections, "
    "content.reports, ops.idempotency_keys, content.publications, content.widgets, content.dashboard_tabs, content.dashboards, "
    "ops.agent_steps, ops.agent_runs, ops.agent_trace_settings, ops.agent_prompt_versions, ops.agent_skills, chat.turn_events, chat.turns, chat.conversations, model.model_patches, ops.llm_settings, "
    "model.pack_field_mappings, model.starter_pack_items, model.model_questions, model.dimensions, model.measures, "
    "model.model_links, model.model_fields, model.model_entities, model.model_versions, model.data_models, "
    "model.pack_attachments, model.pack_documents, model.starter_packs, ops.audit_events, ops.llm_invocations, ops.query_executions, ops.result_cache_index, "
    "ops.export_jobs, ops.outbox_events, ops.notifications, ops.provisioning_log, ops.provisioning_tasks, "
    "ops.provisioning_runs, ops.tenant_branding_history, ops.tenant_branding, iam.scim_tokens, iam.permission_requests, iam.permission_grants, iam.field_policies, iam.scope_policies, "
    "conn.file_sources, conn.connection_objects, conn.connections, iam.group_role_mappings, "
    "iam.idp_group_members, iam.idp_groups, iam.external_identities, iam.users"
).split(", ")
# Truncating all ~70 tables costs ~0.3 s per test; most are empty, so only the ones with rows are truncated.
_NON_EMPTY = " UNION ALL ".join(f"(SELECT '{t}' WHERE EXISTS (SELECT 1 FROM {t}))" for t in RESET_TABLES)


def reset_data(conn: psycopg.Connection) -> None:
    dirty = [row[0] for row in conn.execute(_NON_EMPTY).fetchall()]
    if dirty:
        conn.execute(f"TRUNCATE {', '.join(dirty)} CASCADE")
    conn.execute("DELETE FROM iam.roles WHERE tenant_id IS NOT NULL")
    conn.execute("DELETE FROM iam.tenants")


def bootstrap(conn: psycopg.Connection, slug: str, issuer: str, groups: dict[str, str]) -> str:
    """Create a tenant with role mappings {group_external_id: role_key}. Admin mappings are pre-approved."""
    tenant_id = new_id()
    conn.execute(
        "INSERT INTO iam.tenants (id, slug, name, oidc_issuer, deployment_profile) VALUES (%s, %s, %s, %s, %s)",
        (tenant_id, slug, slug.title(), issuer, "customer_hosted"),
    )
    for ext, role in groups.items():
        gid = new_id()
        conn.execute(
            "INSERT INTO iam.idp_groups (id, tenant_id, external_group_id, display_name, source) "
            "VALUES (%s, %s, %s, %s, 'admin') ON CONFLICT (tenant_id, external_group_id) DO NOTHING",
            (gid, tenant_id, ext, ext),
        )
        row = conn.execute(
            "SELECT id FROM iam.idp_groups WHERE tenant_id = %s AND external_group_id = %s", (tenant_id, ext)
        ).fetchone()
        assert row
        conn.execute(
            "INSERT INTO iam.group_role_mappings (id, tenant_id, group_id, role_id, approved_at) "
            "SELECT %s, %s, %s, id, now() FROM iam.roles WHERE tenant_id IS NULL AND key = %s",
            (new_id(), tenant_id, row[0], role),
        )
    return tenant_id


def add_connection(conn: psycopg.Connection, tenant_id: str, name: str = "TPG Postgres") -> str:
    cid = new_id()
    conn.execute(
        "INSERT INTO conn.connections (id, tenant_id, name, engine, descriptor, status) "
        "VALUES (%s, %s, %s, 'postgres', '{\"host\": \"db.internal\"}', 'draft')",
        (cid, tenant_id, name),
    )
    return cid
