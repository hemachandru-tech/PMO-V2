"""Packs and their knowledge stored per tenant (ENH-025, US-230; FEAT-075).

* ``model.starter_packs`` gets ``tenant_id`` (null = a system pack, loaded by ``tela-admin load-packs``),
  ``status`` (``draft`` / ``released`` / ``retired``), ``created_by`` and ``based_on_pack_id`` (the pack it was
  cloned from). Row-level security: a tenant reads the system packs and its own; the application role writes its
  own tenant's rows of status ``draft`` only. Release goes through the API (US-238); system rows stay owner-only.
  The unique key becomes (owner, key, version); a tenant pack may not reuse a system pack's key, so a key names one
  pack wherever it is read. A released or retired pack is frozen: only released → retired is allowed.
* ``model.pack_documents``: one validated JSON document per pack knowledge file (glossary, value synonyms,
  dashboard rules, chart patterns, analysis recipes, report blueprints, gold examples) with its SHA-256, under the
  same security as its pack and frozen with it. The body is ``json``, not ``jsonb``: jsonb re-sorts object keys,
  and knowledge chunk text follows the file's order (US-231 parity).
* ``model.f_pack_update_tenants()`` compares released versions of the same owner only.

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-05
"""

from alembic import context

from migrations.helpers import run

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None

KINDS = ("glossary", "value_synonyms", "dashboard_rules", "chart_patterns", "analysis_recipes",
         "report_blueprints", "gold_examples")
_NIL = "'00000000-0000-0000-0000-000000000000'::uuid"


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    kinds = ", ".join(f"'{k}'" for k in KINDS)
    run(
        f"""
        ALTER TABLE model.starter_packs
          ADD COLUMN tenant_id uuid REFERENCES iam.tenants(id) ON DELETE CASCADE,
          ADD COLUMN status text NOT NULL DEFAULT 'released' CHECK (status IN ('draft', 'released', 'retired')),
          ADD COLUMN created_by uuid,
          ADD COLUMN based_on_pack_id uuid REFERENCES model.starter_packs(id) ON DELETE SET NULL,
          DROP CONSTRAINT uq_starter_pack;
        CREATE UNIQUE INDEX uq_starter_pack ON model.starter_packs (COALESCE(tenant_id, {_NIL}), pack_key, pack_version);
        CREATE INDEX ix_starter_packs_tenant ON model.starter_packs (tenant_id) WHERE tenant_id IS NOT NULL;

        CREATE TABLE model.pack_documents (
          id          uuid PRIMARY KEY,
          pack_id     uuid NOT NULL REFERENCES model.starter_packs(id) ON DELETE CASCADE,
          tenant_id   uuid REFERENCES iam.tenants(id) ON DELETE CASCADE,
          kind        text NOT NULL CHECK (kind IN ({kinds})),
          body        json NOT NULL,  -- json, not jsonb: keeps the file's key order (chunk text depends on it)
          sha256      text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_pack_document UNIQUE (pack_id, kind)
        );

        -- A key names one pack: a tenant pack may not take a system pack's key, nor a system pack a tenant's.
        -- A released or retired pack is frozen (only released -> retired); its owner and key never change.
        CREATE FUNCTION model.guard_starter_pack() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = model, pg_temp AS $$
        BEGIN
          IF TG_OP = 'INSERT' OR NEW.pack_key <> OLD.pack_key THEN
            IF EXISTS (SELECT 1 FROM model.starter_packs p WHERE p.pack_key = NEW.pack_key AND p.id <> NEW.id
                       AND (p.tenant_id IS NULL) <> (NEW.tenant_id IS NULL)) THEN
              RAISE EXCEPTION 'pack key % is taken', NEW.pack_key USING ERRCODE = 'unique_violation';
            END IF;
          END IF;
          IF TG_OP = 'UPDATE' THEN
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
              RAISE EXCEPTION 'a pack keeps its owner';
            END IF;
            IF OLD.status <> 'draft' AND NOT (
                 OLD.status = 'released' AND NEW.status = 'retired' AND NEW.manifest = OLD.manifest
                 AND NEW.manifest_sha256 = OLD.manifest_sha256 AND NEW.pack_key = OLD.pack_key
                 AND NEW.pack_version = OLD.pack_version) THEN
              RAISE EXCEPTION 'pack % % is % and frozen', OLD.pack_key, OLD.pack_version, OLD.status;
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_starter_packs_guard BEFORE INSERT OR UPDATE ON model.starter_packs
          FOR EACH ROW EXECUTE FUNCTION model.guard_starter_pack();

        -- A document carries its pack's owner and is frozen once its pack is released.
        CREATE FUNCTION model.guard_pack_document() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = model, pg_temp AS $$
        DECLARE p record;
        BEGIN
          SELECT tenant_id, status INTO p FROM model.starter_packs WHERE id = NEW.pack_id;
          IF NEW.tenant_id IS DISTINCT FROM p.tenant_id THEN
            RAISE EXCEPTION 'a pack document belongs to its pack''s owner';
          END IF;
          IF TG_OP = 'UPDATE' AND (p.status <> 'draft' OR NEW.pack_id <> OLD.pack_id OR NEW.kind <> OLD.kind) THEN
            RAISE EXCEPTION 'the documents of a % pack are frozen', p.status;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_pack_documents_guard BEFORE INSERT OR UPDATE ON model.pack_documents
          FOR EACH ROW EXECUTE FUNCTION model.guard_pack_document();
        CREATE TRIGGER trg_pack_documents_updated_at BEFORE UPDATE ON model.pack_documents
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();

        ALTER TABLE model.starter_packs ENABLE ROW LEVEL SECURITY;
        CREATE POLICY pack_read ON model.starter_packs FOR SELECT
          USING (tenant_id IS NULL OR tenant_id = util.current_tenant());
        CREATE POLICY pack_insert ON model.starter_packs FOR INSERT
          WITH CHECK (tenant_id = util.current_tenant() AND status = 'draft');
        CREATE POLICY pack_update ON model.starter_packs FOR UPDATE
          USING (tenant_id = util.current_tenant() AND status = 'draft')
          WITH CHECK (tenant_id = util.current_tenant() AND status = 'draft');
        CREATE POLICY pack_delete ON model.starter_packs FOR DELETE
          USING (tenant_id = util.current_tenant() AND status = 'draft');

        ALTER TABLE model.pack_documents ENABLE ROW LEVEL SECURITY;
        CREATE POLICY pack_document_read ON model.pack_documents FOR SELECT
          USING (tenant_id IS NULL OR tenant_id = util.current_tenant());
        CREATE POLICY pack_document_write ON model.pack_documents FOR ALL
          USING (tenant_id = util.current_tenant() AND EXISTS (
                   SELECT 1 FROM model.starter_packs p WHERE p.id = pack_id AND p.status = 'draft'))
          WITH CHECK (tenant_id = util.current_tenant() AND EXISTS (
                   SELECT 1 FROM model.starter_packs p WHERE p.id = pack_id AND p.status = 'draft'));

        CREATE OR REPLACE FUNCTION model.f_pack_update_tenants() RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = model, pg_temp AS $$
          SELECT DISTINCT m.tenant_id
          FROM model.data_models m
          JOIN model.starter_packs i ON i.id = m.installed_pack_id
          JOIN model.starter_packs n ON n.pack_key = i.pack_key AND n.status = 'released'
            AND n.tenant_id IS NOT DISTINCT FROM i.tenant_id
            AND string_to_array(n.pack_version, '.')::int[] > string_to_array(i.pack_version, '.')::int[]
          WHERE m.pack_update_notified_id IS DISTINCT FROM n.id
        $$;
        REVOKE ALL ON FUNCTION model.f_pack_update_tenants() FROM PUBLIC;
        REVOKE ALL ON FUNCTION model.guard_starter_pack() FROM PUBLIC;
        REVOKE ALL ON FUNCTION model.guard_pack_document() FROM PUBLIC;
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON model.starter_packs, model.pack_documents TO {role};
            GRANT EXECUTE ON FUNCTION model.f_pack_update_tenants() TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    role = _app_role()
    run(
        f"""
        DELETE FROM model.starter_packs WHERE tenant_id IS NOT NULL;
        DROP TABLE IF EXISTS model.pack_documents;
        DROP POLICY IF EXISTS pack_read ON model.starter_packs;
        DROP POLICY IF EXISTS pack_insert ON model.starter_packs;
        DROP POLICY IF EXISTS pack_update ON model.starter_packs;
        DROP POLICY IF EXISTS pack_delete ON model.starter_packs;
        ALTER TABLE model.starter_packs DISABLE ROW LEVEL SECURITY;
        DROP TRIGGER IF EXISTS trg_starter_packs_guard ON model.starter_packs;
        DROP FUNCTION IF EXISTS model.guard_starter_pack();
        DROP FUNCTION IF EXISTS model.guard_pack_document();
        DROP INDEX IF EXISTS model.uq_starter_pack;
        DROP INDEX IF EXISTS model.ix_starter_packs_tenant;
        ALTER TABLE model.starter_packs
          DROP COLUMN IF EXISTS based_on_pack_id, DROP COLUMN IF EXISTS created_by,
          DROP COLUMN IF EXISTS status, DROP COLUMN IF EXISTS tenant_id,
          ADD CONSTRAINT uq_starter_pack UNIQUE (pack_key, pack_version);

        CREATE OR REPLACE FUNCTION model.f_pack_update_tenants() RETURNS TABLE (tenant_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = model, pg_temp AS $$
          SELECT DISTINCT m.tenant_id
          FROM model.data_models m
          JOIN model.starter_packs i ON i.id = m.installed_pack_id
          JOIN model.starter_packs n ON n.pack_key = i.pack_key
            AND string_to_array(n.pack_version, '.')::int[] > string_to_array(i.pack_version, '.')::int[]
          WHERE m.pack_update_notified_id IS DISTINCT FROM n.id
        $$;
        REVOKE ALL ON FUNCTION model.f_pack_update_tenants() FROM PUBLIC;
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            REVOKE INSERT, UPDATE, DELETE ON model.starter_packs FROM {role};
            GRANT EXECUTE ON FUNCTION model.f_pack_update_tenants() TO {role};
          END IF;
        END $$;
        """
    )
