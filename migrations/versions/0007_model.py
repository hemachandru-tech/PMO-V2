"""Data model, versions and starter packs (DATA-014..023; US-031, US-035, US-036, US-037).

Design notes (deviations recorded in task.md):

* A model version's full definition is a JSON snapshot (``definition``) in the
  shape of ``tela_query_spec.ModelSnapshot``. The single draft is edited as a
  document with an ETag (``draft_revision``). On approval the snapshot is frozen
  and also materialised into the normalised tables (entities, fields, links,
  measures, dimensions) so later epics can reference definitions by row.
* Entities reference their source object by qualified name rather than a
  foreign key to ``conn.connection_objects``, so re-introspection can remove
  objects without rewriting immutable versions (removed objects make the
  model ``broken`` instead, A3).
* Approved versions are immutable: a trigger allows only approved → superseded;
  materialised rows can never be updated or deleted.
* ``model.starter_packs`` is a global catalogue loaded by the owner role
  (``tela-admin load-packs``); the application role can only read it.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21
"""

from alembic import context

from migrations.helpers import run

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "model.data_models", "model.model_versions", "model.model_entities", "model.model_fields", "model.model_links",
    "model.measures", "model.dimensions", "model.model_questions", "model.starter_pack_items",
]
FROZEN_CHILDREN = ["model_entities", "model_fields", "model_links", "measures", "dimensions"]


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    # Introspection detail on catalogue objects (US-030): per-object checksum for drift checks (US-023),
    # keys, row estimate, and a removal marker instead of deleting rows that approved versions name.
    run(
        """
        ALTER TABLE conn.connection_objects
          ADD COLUMN object_checksum text,
          ADD COLUMN keys jsonb NOT NULL DEFAULT '{}',
          ADD COLUMN row_estimate bigint,
          ADD COLUMN removed_at timestamptz;
        """
    )
    run("CREATE SCHEMA IF NOT EXISTS model")
    run(
        """
        CREATE TABLE model.starter_packs (
          id                   uuid PRIMARY KEY,
          pack_key             text NOT NULL CHECK (pack_key ~ '^[a-z][a-z0-9_]{1,40}$'),
          pack_version         text NOT NULL CHECK (pack_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'),
          title                text NOT NULL,
          detection_signature  jsonb NOT NULL,
          manifest             jsonb NOT NULL,
          manifest_sha256      text NOT NULL,
          released_at          timestamptz NOT NULL,
          created_at           timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_starter_pack UNIQUE (pack_key, pack_version)
        );

        CREATE TABLE model.data_models (
          id                  uuid PRIMARY KEY,
          tenant_id           uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          connection_id       uuid NOT NULL REFERENCES conn.connections(id) ON DELETE RESTRICT,
          current_version_id  uuid,
          draft_version_id    uuid,
          installed_pack_id   uuid REFERENCES model.starter_packs(id) ON DELETE RESTRICT,
          status              text NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'broken')),
          broken_reasons      jsonb NOT NULL DEFAULT '[]',
          created_at          timestamptz NOT NULL DEFAULT now(),
          updated_at          timestamptz NOT NULL DEFAULT now(),
          created_by          uuid,
          CONSTRAINT uq_model_per_connection UNIQUE (connection_id)
        );

        CREATE TABLE model.model_versions (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          data_model_id    uuid NOT NULL REFERENCES model.data_models(id) ON DELETE CASCADE,
          version_no       integer CHECK (version_no IS NULL OR version_no > 0),
          status           text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'superseded')),
          definition       jsonb NOT NULL,
          draft_revision   integer NOT NULL DEFAULT 1,
          schema_checksum  text,
          change_summary   text,
          approved_by      uuid REFERENCES iam.users(id),
          approved_at      timestamptz,
          created_at       timestamptz NOT NULL DEFAULT now(),
          updated_at       timestamptz NOT NULL DEFAULT now(),
          created_by       uuid,
          CONSTRAINT uq_model_version_no UNIQUE (data_model_id, version_no),
          CONSTRAINT ck_version_no_when_approved CHECK ((status = 'draft') = (version_no IS NULL))
        );
        CREATE UNIQUE INDEX uq_one_draft ON model.model_versions (data_model_id) WHERE status = 'draft';

        ALTER TABLE model.data_models
          ADD CONSTRAINT fk_current_version FOREIGN KEY (current_version_id)
            REFERENCES model.model_versions(id) DEFERRABLE INITIALLY DEFERRED,
          ADD CONSTRAINT fk_draft_version FOREIGN KEY (draft_version_id)
            REFERENCES model.model_versions(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED;

        CREATE TABLE model.model_entities (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          key               text NOT NULL,
          label             text NOT NULL,
          source_object     text NOT NULL,
          primary_key       text[] NOT NULL,
          description       text,
          grain             text,
          CONSTRAINT uq_entity_key UNIQUE (model_version_id, key)
        );

        CREATE TABLE model.model_fields (
          id               uuid PRIMARY KEY,
          tenant_id        uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          model_entity_id  uuid NOT NULL REFERENCES model.model_entities(id) ON DELETE CASCADE,
          key              text NOT NULL,
          source_column    text NOT NULL,
          label            text NOT NULL,
          description      text,
          data_type        text NOT NULL CHECK (data_type IN ('text','integer','decimal','date','datetime','boolean','json')),
          semantic_type    text,
          format           jsonb NOT NULL DEFAULT '{}',
          sensitivity      text NOT NULL DEFAULT 'internal'
                             CHECK (sensitivity IN ('public','internal','confidential','personal','sensitive')),
          is_queryable     boolean NOT NULL DEFAULT true,
          blank_handling   text,
          CONSTRAINT uq_field_key UNIQUE (model_entity_id, key)
        );

        CREATE TABLE model.model_links (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          from_entity_id    uuid NOT NULL REFERENCES model.model_entities(id) ON DELETE CASCADE,
          to_entity_id      uuid NOT NULL REFERENCES model.model_entities(id) ON DELETE CASCADE,
          from_field_keys   text[] NOT NULL,
          to_field_keys     text[] NOT NULL,
          cardinality       text NOT NULL CHECK (cardinality IN ('one_to_one','many_to_one','one_to_many')),
          origin            text NOT NULL CHECK (origin IN ('foreign_key','proposed','admin','pack')),
          CONSTRAINT ck_link_arity CHECK (cardinality(from_field_keys) = cardinality(to_field_keys)),
          CONSTRAINT uq_link UNIQUE (model_version_id, from_entity_id, to_entity_id, from_field_keys)
        );

        CREATE TABLE model.measures (
          id                  uuid PRIMARY KEY,
          tenant_id           uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          model_version_id    uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          key                 text NOT NULL,
          label               text NOT NULL,
          entity_key          text NOT NULL,
          aggregation         text NOT NULL
                                CHECK (aggregation IN ('count','count_distinct','sum','avg','min','max','ratio','custom')),
          expression          jsonb NOT NULL,
          plain_rule          text NOT NULL,
          blank_handling      text NOT NULL,
          format              text NOT NULL DEFAULT 'integer',
          currency_field_key  text,
          rule_ref            text,
          synonyms            text[] NOT NULL DEFAULT '{}',
          status              text NOT NULL DEFAULT 'certified' CHECK (status IN ('certified','deprecated')),
          CONSTRAINT uq_measure_key UNIQUE (model_version_id, key)
        );

        CREATE TABLE model.dimensions (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          key               text NOT NULL,
          model_field_id    uuid NOT NULL REFERENCES model.model_fields(id) ON DELETE CASCADE,
          label             text NOT NULL,
          is_temporal       boolean NOT NULL DEFAULT false,
          is_status         boolean NOT NULL DEFAULT false,
          sort_order        jsonb,
          value_labels      jsonb NOT NULL DEFAULT '{}',
          synonyms          text[] NOT NULL DEFAULT '{}',
          CONSTRAINT uq_dimension_key UNIQUE (model_version_id, key)
        );

        CREATE TABLE model.model_questions (
          id                uuid PRIMARY KEY,
          tenant_id         uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          model_version_id  uuid NOT NULL REFERENCES model.model_versions(id) ON DELETE CASCADE,
          question          text NOT NULL,
          target_ref        jsonb,
          options           jsonb NOT NULL DEFAULT '[]',
          answer            text,
          blocking          boolean NOT NULL DEFAULT true,
          status            text NOT NULL DEFAULT 'open' CHECK (status IN ('open','answered','dismissed')),
          answered_by       uuid,
          created_at        timestamptz NOT NULL DEFAULT now(),
          updated_at        timestamptz NOT NULL DEFAULT now(),
          created_by        uuid
        );
        CREATE INDEX ix_model_questions_version ON model.model_questions (model_version_id, status);

        CREATE TABLE model.starter_pack_items (
          id                    uuid PRIMARY KEY,
          tenant_id             uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          starter_pack_id       uuid NOT NULL REFERENCES model.starter_packs(id) ON DELETE RESTRICT,
          connection_id         uuid NOT NULL REFERENCES conn.connections(id) ON DELETE CASCADE,
          item_kind             text NOT NULL CHECK (item_kind IN ('report','dashboard','measure','dimension')),
          item_key              text NOT NULL,
          title                 text NOT NULL,
          content_id            uuid,
          availability          text NOT NULL DEFAULT 'available' CHECK (availability IN ('available','unavailable')),
          gaps                  jsonb NOT NULL DEFAULT '[]',
          state                 text NOT NULL DEFAULT 'active' CHECK (state IN ('active','deleted')),
          hidden_from_role_ids  uuid[] NOT NULL DEFAULT '{}',
          copy_count            integer NOT NULL DEFAULT 0,
          created_at            timestamptz NOT NULL DEFAULT now(),
          updated_at            timestamptz NOT NULL DEFAULT now(),
          created_by            uuid,
          CONSTRAINT uq_pack_item UNIQUE (tenant_id, connection_id, starter_pack_id, item_kind, item_key)
        );

        -- Approved versions are immutable; the only allowed change is approved -> superseded.
        CREATE OR REPLACE FUNCTION model.guard_version_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.status <> 'draft' THEN
              RAISE EXCEPTION 'approved model versions cannot be deleted';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.status = 'draft' THEN
            RETURN NEW;
          END IF;
          IF OLD.status = 'approved' AND NEW.status = 'superseded'
             AND NEW.definition = OLD.definition AND NEW.version_no = OLD.version_no
             AND NEW.data_model_id = OLD.data_model_id AND NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'model version % is % and immutable', OLD.id, OLD.status;
        END $$;
        CREATE TRIGGER trg_model_versions_immutable BEFORE UPDATE OR DELETE ON model.model_versions
          FOR EACH ROW EXECUTE FUNCTION model.guard_version_update();
        """
    )
    for table in FROZEN_CHILDREN:
        run(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE ON model.{table} "
            "FOR EACH ROW EXECUTE FUNCTION util.forbid_update()"
        )
    for table in ("data_models", "model_versions", "model_questions", "starter_pack_items"):
        run(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON model.{table} "
            "FOR EACH ROW EXECUTE FUNCTION util.set_updated_at()"
        )
    for table in TENANT_TABLES:
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())"
        )

    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT USAGE ON SCHEMA model TO {role};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA model TO {role};
            REVOKE INSERT, UPDATE, DELETE ON model.starter_packs FROM {role};
            REVOKE DELETE ON model.model_entities, model.model_fields, model.model_links, model.measures,
                             model.dimensions FROM {role};
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
            REVOKE ALL ON ALL TABLES IN SCHEMA model FROM {role};
            REVOKE USAGE ON SCHEMA model FROM {role};
          END IF;
        END $$;
        """
    )
    run("DROP SCHEMA IF EXISTS model CASCADE")
    run(
        "ALTER TABLE conn.connection_objects DROP COLUMN IF EXISTS object_checksum, DROP COLUMN IF EXISTS keys, "
        "DROP COLUMN IF EXISTS row_estimate, DROP COLUMN IF EXISTS removed_at"
    )
