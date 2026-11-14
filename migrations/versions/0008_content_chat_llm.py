"""Content (read side), chat, model patches and LLM settings (P1 continued).

* ``content`` — the read side of dashboards needed by the viewer (FEAT-042): dashboards with tabs,
  widgets (spec + model version, never numbers, DATA-026) and publications to roles. P1 fills them only
  with **starter dashboards** built from the installed PMO pack on model approval; authoring, drafts and
  versions (DATA-027..035) arrive with EPIC-06/09 on the same tables.
* ``chat`` — conversations, turns and turn events (DATA-039..041). Rows are never stored on turns.
* ``model.model_patches`` — model assistant proposals and corrections (AG-04) with before/after
  snapshots for Apply and Undo (FEAT-007, FEAT-008).
* ``ops.llm_settings`` — per-tenant provider, models per role, egress toggles, budget (FEAT-049).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21
"""

from alembic import context

from migrations.helpers import run

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "content.dashboards", "content.dashboard_tabs", "content.widgets", "content.publications",
    "chat.conversations", "chat.turns", "chat.turn_events", "model.model_patches", "ops.llm_settings",
]


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run("CREATE SCHEMA IF NOT EXISTS content")
    run("CREATE SCHEMA IF NOT EXISTS chat")
    run(
        """
        CREATE TABLE content.dashboards (
          id                    uuid PRIMARY KEY,
          tenant_id             uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          title                 text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
          kind                  text NOT NULL CHECK (kind IN ('starter', 'user')),
          layout                text NOT NULL DEFAULT 'tabs' CHECK (layout IN ('tabs', 'grouped')),
          connection_id         uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id      uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE RESTRICT,
          starter_key           text,
          shared_filters        jsonb NOT NULL DEFAULT '[]',
          status                text NOT NULL DEFAULT 'published' CHECK (status IN ('draft', 'published', 'deleted')),
          version_no            integer NOT NULL DEFAULT 1,
          owner_id              uuid,
          created_at            timestamptz NOT NULL DEFAULT now(),
          updated_at            timestamptz NOT NULL DEFAULT now(),
          created_by            uuid,
          CONSTRAINT uq_starter_dashboard UNIQUE (tenant_id, connection_id, starter_key)
        );

        CREATE TABLE content.dashboard_tabs (
          id            uuid PRIMARY KEY,
          tenant_id     uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          dashboard_id  uuid NOT NULL REFERENCES content.dashboards(id) ON DELETE CASCADE,
          position      integer NOT NULL,
          title         text NOT NULL,
          report_key    text,
          CONSTRAINT uq_tab_position UNIQUE (dashboard_id, position)
        );

        CREATE TABLE content.widgets (
          id                   uuid PRIMARY KEY,
          tenant_id            uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          dashboard_tab_id     uuid REFERENCES content.dashboard_tabs(id) ON DELETE CASCADE,
          position             integer NOT NULL DEFAULT 0,
          title                text NOT NULL,
          spec                 jsonb NOT NULL,
          visual               text NOT NULL,
          placement            jsonb NOT NULL DEFAULT '{}',
          follow_page_filters  boolean NOT NULL DEFAULT true,
          model_version_id     uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE RESTRICT,
          connection_id        uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          certification_state  text NOT NULL DEFAULT 'certified'
                                 CHECK (certification_state IN ('certified', 'personal', 'exploratory')),
          owner_id             uuid,
          created_at           timestamptz NOT NULL DEFAULT now(),
          updated_at           timestamptz NOT NULL DEFAULT now(),
          created_by           uuid
        );
        CREATE INDEX ix_widgets_tab ON content.widgets (dashboard_tab_id, position);

        -- Published to every member of a role; all_roles publishes to everyone in the tenant (starters).
        CREATE TABLE content.publications (
          id             uuid PRIMARY KEY,
          tenant_id      uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          dashboard_id   uuid NOT NULL REFERENCES content.dashboards(id) ON DELETE CASCADE,
          role_id        uuid REFERENCES iam.roles(id) ON DELETE CASCADE,
          all_roles      boolean NOT NULL DEFAULT false,
          published_by   uuid,
          published_at   timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_publication_target CHECK (all_roles OR role_id IS NOT NULL)
        );
        CREATE INDEX ix_publications_dashboard ON content.publications (dashboard_id);

        CREATE TABLE chat.conversations (
          id          uuid PRIMARY KEY,
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          user_id     uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          title       text,
          memory      jsonb NOT NULL DEFAULT '{}',
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_conversations_user ON chat.conversations (user_id, updated_at DESC);

        CREATE TABLE chat.turns (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          conversation_id   uuid NOT NULL REFERENCES chat.conversations(id) ON DELETE CASCADE,
          user_id           uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          question          text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 2000),
          context           jsonb NOT NULL DEFAULT '{}',
          status            text NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'running', 'awaiting_clarification', 'complete', 'error')),
          intent            text,
          connection_id     uuid,
          model_version_id  uuid,
          spec              jsonb,
          chips             jsonb NOT NULL DEFAULT '[]',
          visual            text,
          narrative         jsonb,
          clarification     jsonb,
          error             jsonb,
          planner           text,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_turns_conversation ON chat.turns (conversation_id, created_at);

        CREATE TABLE chat.turn_events (
          id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          turn_id     uuid NOT NULL REFERENCES chat.turns(id) ON DELETE CASCADE,
          event       text NOT NULL,
          data        jsonb NOT NULL,
          created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX ix_turn_events_turn ON chat.turn_events (turn_id, id);

        CREATE TABLE model.model_patches (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          data_model_id     uuid NOT NULL REFERENCES model.data_models(id) ON DELETE CASCADE,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          kind              text NOT NULL CHECK (kind IN ('proposal', 'correction')),
          message_hash      text NOT NULL,
          source            text NOT NULL CHECK (source IN ('llm', 'rules')),
          ops               jsonb NOT NULL,
          questions         jsonb NOT NULL DEFAULT '[]',
          status            text NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'applied', 'rejected', 'undone')),
          before_definition jsonb,
          after_revision    integer,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now(),
          created_by        uuid
        );
        CREATE INDEX ix_model_patches_model ON model.model_patches (data_model_id, created_at DESC);

        CREATE TABLE ops.llm_settings (
          tenant_id             uuid PRIMARY KEY REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          provider              text,
          models                jsonb NOT NULL DEFAULT '{}',
          allow_samples         boolean NOT NULL DEFAULT false,
          allow_aggregates      boolean NOT NULL DEFAULT false,
          monthly_token_budget  integer NOT NULL DEFAULT 0 CHECK (monthly_token_budget >= 0),
          rates                 jsonb NOT NULL DEFAULT '{}',
          secret_ref            text,
          last_test             jsonb,
          updated_at            timestamptz NOT NULL DEFAULT now(),
          updated_by            uuid
        );
        """
    )
    for table in ("content.dashboards", "content.widgets", "chat.conversations", "chat.turns",
                  "model.model_patches", "ops.llm_settings"):
        name = table.split(".")[1]
        run(f"CREATE TRIGGER trg_{name}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION util.set_updated_at()")
    for table in TENANT_TABLES:
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())")
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT USAGE ON SCHEMA content, chat TO {role};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA content, chat TO {role};
            GRANT SELECT, INSERT, UPDATE, DELETE ON model.model_patches, ops.llm_settings TO {role};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA chat TO {role};
            REVOKE UPDATE, DELETE ON chat.turn_events FROM {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            REVOKE ALL ON ALL TABLES IN SCHEMA content, chat FROM {role};
            REVOKE ALL ON ALL SEQUENCES IN SCHEMA chat FROM {role};
            REVOKE USAGE ON SCHEMA content, chat FROM {role};
          END IF;
        END $$;
        """
    )
    run("DROP TABLE IF EXISTS ops.llm_settings CASCADE")
    run("DROP TABLE IF EXISTS model.model_patches CASCADE")
    run("DROP SCHEMA IF EXISTS chat CASCADE")
    run("DROP SCHEMA IF EXISTS content CASCADE")
