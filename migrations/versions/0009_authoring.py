"""Authoring and publishing (P2: EPIC-05 part 2, EPIC-06, EPIC-09; DATA-026 to DATA-035).

Expands the P1 read-side content tables (expand → contract: nothing existing is dropped):

* ``content.widgets`` — a widget can now live in a Creator's library (no tab) with the originating
  question, ``def_hash`` for deduplication (MSG-023), ``own_filters`` and a soft delete. Starter widgets
  keep their tab.
* ``content.reports``, ``report_sections``, ``report_items`` — the 12-column canvas (DATA-027..029),
  with ``draft_revision`` for If-Match and ``published_snapshot`` for "Undo all".
* ``content.report_plans`` and ``content.report_edit_previews`` — AG-05 plans and AG-06 previews.
* ``content.dashboards`` — user dashboards: several reports (``dashboard_reports``, DATA-031), possibly
  from different connections, so ``connection_id`` and ``model_version_id`` become optional; the live
  version lives in ``dashboard_versions`` (DATA-032, immutable frozen content, one live per dashboard).
* ``content.publications`` — gains ``dashboard_version_id``; rows always describe the live version.
* ``content.shares`` (DATA-034), ``content.drafts_undo_log`` (DATA-035).
* ``ops.idempotency_keys`` — replay of creating POSTs within 24 h (api_catalog §Conventions).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21
"""

from alembic import context

from migrations.helpers import run

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

NEW_TABLES = [
    "content.reports", "content.report_sections", "content.report_items", "content.report_plans",
    "content.report_edit_previews", "content.dashboard_reports", "content.dashboard_versions",
    "content.shares", "content.drafts_undo_log", "ops.idempotency_keys",
]


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        """
        ALTER TABLE content.widgets
          ADD COLUMN question      text CHECK (question IS NULL OR char_length(question) <= 2000),
          ADD COLUMN source_kind   text NOT NULL DEFAULT 'spec' CHECK (source_kind IN ('spec', 'custom_sql')),
          ADD COLUMN def_hash      text,
          ADD COLUMN own_filters   jsonb NOT NULL DEFAULT '[]',
          ADD COLUMN from_turn_id  uuid,
          ADD COLUMN deleted_at    timestamptz;
        ALTER TABLE content.widgets ADD CONSTRAINT ck_widgets_visual CHECK (visual IN
          ('figure', 'ranked_bar', 'columns', 'trend', 'paired_bars', 'stacked_status', 'donut', 'table'));
        CREATE UNIQUE INDEX uq_widgets_owner_def ON content.widgets (owner_id, def_hash)
          WHERE deleted_at IS NULL AND owner_id IS NOT NULL AND def_hash IS NOT NULL;
        CREATE INDEX ix_widgets_connection ON content.widgets (tenant_id, connection_id);

        CREATE TABLE content.reports (
          id                  uuid PRIMARY KEY,
          tenant_id           uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          owner_id            uuid NOT NULL REFERENCES iam.users(id) ON DELETE RESTRICT,
          title               text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
          page_filters        jsonb NOT NULL DEFAULT '[]',
          origin              text NOT NULL DEFAULT 'user' CHECK (origin IN ('user', 'starter', 'copy')),
          copied_from_id      uuid REFERENCES content.reports(id) ON DELETE SET NULL,
          plan_id             uuid,
          draft_revision      integer NOT NULL DEFAULT 0,
          published_snapshot  jsonb,
          deleted_at          timestamptz,
          created_at          timestamptz NOT NULL DEFAULT now(),
          updated_at          timestamptz NOT NULL DEFAULT now(),
          created_by          uuid
        );
        CREATE INDEX ix_reports_owner ON content.reports (owner_id, updated_at DESC) WHERE deleted_at IS NULL;

        CREATE TABLE content.report_sections (
          id          uuid PRIMARY KEY,
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          report_id   uuid NOT NULL REFERENCES content.reports(id) ON DELETE CASCADE,
          title       text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 120),
          kind        text NOT NULL DEFAULT 'normal' CHECK (kind IN ('headline', 'normal')),
          position    integer NOT NULL,
          collapsed   boolean NOT NULL DEFAULT false,
          CONSTRAINT uq_section_position UNIQUE (report_id, position) DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE content.report_items (
          id            uuid PRIMARY KEY,
          tenant_id     uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          report_id     uuid NOT NULL REFERENCES content.reports(id) ON DELETE CASCADE,
          section_id    uuid NOT NULL REFERENCES content.report_sections(id) ON DELETE CASCADE,
          item_kind     text NOT NULL DEFAULT 'widget' CHECK (item_kind IN ('widget', 'text_note')),
          widget_id     uuid REFERENCES content.widgets(id) ON DELETE RESTRICT,
          text_content  text CHECK (text_content IS NULL OR char_length(text_content) <= 4000),
          grid_x        smallint NOT NULL CHECK (grid_x BETWEEN 0 AND 11),
          grid_y        smallint NOT NULL CHECK (grid_y >= 0),
          grid_w        smallint NOT NULL CHECK (grid_w BETWEEN 2 AND 12),
          grid_h        smallint NOT NULL CHECK (grid_h BETWEEN 2 AND 24),
          -- Pre-publish fixes (business_rules §4.3): "Mark empty is expected" and "Hide for role".
          empty_expected     boolean NOT NULL DEFAULT false,
          hidden_for_roles   uuid[] NOT NULL DEFAULT '{}',
          CONSTRAINT ck_item_fits CHECK (grid_x + grid_w <= 12),
          CONSTRAINT ck_item_kind CHECK ((item_kind = 'widget') = (widget_id IS NOT NULL))
        );
        CREATE INDEX ix_report_items_report ON content.report_items (report_id, section_id);

        CREATE TABLE content.report_plans (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          owner_id          uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          request           text NOT NULL CHECK (char_length(request) BETWEEN 1 AND 2000),
          connection_id     uuid REFERENCES conn.connections(id) ON DELETE CASCADE,
          model_version_id  uuid REFERENCES model.model_versions(id) ON DELETE RESTRICT,
          plan              jsonb NOT NULL,
          source            text NOT NULL DEFAULT 'rules' CHECK (source IN ('rules', 'llm')),
          status            text NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'built')),
          report_id         uuid REFERENCES content.reports(id) ON DELETE SET NULL,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now()
        );

        CREATE TABLE content.report_edit_previews (
          id              uuid PRIMARY KEY,
          tenant_id       uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          report_id       uuid NOT NULL REFERENCES content.reports(id) ON DELETE CASCADE,
          owner_id        uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          instruction     text NOT NULL,
          ops             jsonb NOT NULL,
          changes         jsonb NOT NULL,
          base_revision   integer NOT NULL,
          status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'applied', 'cancelled')),
          created_at      timestamptz NOT NULL DEFAULT now()
        );

        ALTER TABLE content.dashboards
          ALTER COLUMN connection_id DROP NOT NULL,
          ALTER COLUMN model_version_id DROP NOT NULL,
          ADD COLUMN live_version_id  uuid,
          ADD COLUMN draft_revision   integer NOT NULL DEFAULT 0,
          ADD COLUMN deleted_at       timestamptz;
        ALTER TABLE content.dashboards ADD CONSTRAINT ck_dashboard_source
          CHECK (kind = 'user' OR (connection_id IS NOT NULL AND model_version_id IS NOT NULL));

        CREATE TABLE content.dashboard_reports (
          tenant_id     uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          dashboard_id  uuid NOT NULL REFERENCES content.dashboards(id) ON DELETE CASCADE,
          report_id     uuid NOT NULL REFERENCES content.reports(id) ON DELETE RESTRICT,
          position      integer NOT NULL,
          group_title   text,
          PRIMARY KEY (dashboard_id, report_id),
          CONSTRAINT uq_dashboard_report_position UNIQUE (dashboard_id, position) DEFERRABLE INITIALLY DEFERRED
        );

        CREATE TABLE content.dashboard_versions (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          dashboard_id      uuid NOT NULL REFERENCES content.dashboards(id) ON DELETE RESTRICT,
          version_no        integer NOT NULL,
          frozen_content    jsonb NOT NULL,
          precheck_result   jsonb NOT NULL,
          target_role_ids   uuid[] NOT NULL,
          note              text CHECK (note IS NULL OR char_length(note) <= 500),
          change_summary    text,
          state             text NOT NULL DEFAULT 'live' CHECK (state IN ('live', 'superseded', 'withdrawn')),
          restored_from_id  uuid REFERENCES content.dashboard_versions(id),
          published_by      uuid NOT NULL REFERENCES iam.users(id),
          published_at      timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_dashboard_version UNIQUE (dashboard_id, version_no)
        );
        CREATE UNIQUE INDEX uq_dashboard_live ON content.dashboard_versions (dashboard_id) WHERE state = 'live';
        ALTER TABLE content.dashboards ADD CONSTRAINT fk_dashboard_live_version
          FOREIGN KEY (live_version_id) REFERENCES content.dashboard_versions(id) ON DELETE SET NULL;

        -- frozen_content and the version number never change after publish; only state moves.
        CREATE FUNCTION content.dashboard_version_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.frozen_content IS DISTINCT FROM OLD.frozen_content OR NEW.version_no <> OLD.version_no
             OR NEW.dashboard_id <> OLD.dashboard_id THEN
            RAISE EXCEPTION 'dashboard versions are immutable';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_dashboard_version_frozen BEFORE UPDATE ON content.dashboard_versions
          FOR EACH ROW EXECUTE FUNCTION content.dashboard_version_frozen();

        ALTER TABLE content.publications
          ADD COLUMN dashboard_version_id uuid REFERENCES content.dashboard_versions(id) ON DELETE CASCADE;
        CREATE INDEX ix_publications_role ON content.publications (tenant_id, role_id);

        CREATE TABLE content.shares (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          object_type      text NOT NULL CHECK (object_type IN ('widget', 'report', 'dashboard')),
          object_id        uuid NOT NULL,
          grantee_user_id  uuid NOT NULL REFERENCES iam.users(id) ON DELETE CASCADE,
          permission       text NOT NULL DEFAULT 'view' CHECK (permission IN ('view', 'edit')),
          shared_by        uuid NOT NULL REFERENCES iam.users(id),
          created_at       timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_share UNIQUE (object_type, object_id, grantee_user_id)
        );
        CREATE INDEX ix_shares_grantee ON content.shares (grantee_user_id);

        CREATE TABLE content.drafts_undo_log (
          id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          tenant_id      uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          object_type    text NOT NULL CHECK (object_type IN ('widget', 'report', 'dashboard', 'turn_spec')),
          object_id      uuid NOT NULL,
          revision       integer NOT NULL,
          change_source  text NOT NULL CHECK (change_source IN ('chat', 'canvas', 'refine', 'system')),
          forward_patch  jsonb NOT NULL,
          inverse_patch  jsonb NOT NULL,
          summary        text NOT NULL,
          undone         boolean NOT NULL DEFAULT false,
          actor_id       uuid NOT NULL REFERENCES iam.users(id),
          created_at     timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_undo_revision UNIQUE (object_type, object_id, revision)
        );

        CREATE TABLE ops.idempotency_keys (
          tenant_id    uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          user_id      uuid NOT NULL,
          key          text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 200),
          route        text NOT NULL,
          status_code  integer NOT NULL,
          response     jsonb NOT NULL,
          created_at   timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, user_id, route, key)
        );
        """
    )
    for table in ("content.reports", "content.report_plans"):
        name = table.split(".")[1]
        run(f"CREATE TRIGGER trg_{name}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION util.set_updated_at()")
    for table in NEW_TABLES:
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())")
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA content TO {role};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA content TO {role};
            REVOKE DELETE ON content.dashboard_versions FROM {role};
            GRANT SELECT, INSERT, DELETE ON ops.idempotency_keys TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP TABLE IF EXISTS ops.idempotency_keys;
        DROP TABLE IF EXISTS content.drafts_undo_log;
        DROP TABLE IF EXISTS content.shares;
        DROP INDEX IF EXISTS content.ix_publications_role;
        ALTER TABLE content.publications DROP COLUMN IF EXISTS dashboard_version_id;
        ALTER TABLE content.dashboards DROP CONSTRAINT IF EXISTS fk_dashboard_live_version;
        DROP TABLE IF EXISTS content.dashboard_versions;
        DROP FUNCTION IF EXISTS content.dashboard_version_frozen();
        DROP TABLE IF EXISTS content.dashboard_reports;
        DELETE FROM content.dashboards WHERE kind = 'user';
        ALTER TABLE content.dashboards DROP CONSTRAINT IF EXISTS ck_dashboard_source,
          DROP COLUMN IF EXISTS live_version_id, DROP COLUMN IF EXISTS draft_revision,
          DROP COLUMN IF EXISTS deleted_at,
          ALTER COLUMN connection_id SET NOT NULL, ALTER COLUMN model_version_id SET NOT NULL;
        DROP TABLE IF EXISTS content.report_edit_previews;
        DROP TABLE IF EXISTS content.report_plans;
        DROP TABLE IF EXISTS content.report_items;
        DROP TABLE IF EXISTS content.report_sections;
        DROP TABLE IF EXISTS content.reports;
        DELETE FROM content.widgets WHERE dashboard_tab_id IS NULL;
        DROP INDEX IF EXISTS content.uq_widgets_owner_def;
        DROP INDEX IF EXISTS content.ix_widgets_connection;
        ALTER TABLE content.widgets DROP CONSTRAINT IF EXISTS ck_widgets_visual,
          DROP COLUMN IF EXISTS question, DROP COLUMN IF EXISTS source_kind, DROP COLUMN IF EXISTS def_hash,
          DROP COLUMN IF EXISTS own_filters, DROP COLUMN IF EXISTS from_turn_id, DROP COLUMN IF EXISTS deleted_at;
        """
    )
