"""Context documents attached to a pack (ENH-025, US-233; FEAT-075).

* ``model.pack_attachments``: a Markdown, PDF or Word file attached to a pack, stored like a US-102 knowledge
  document (the bytes in the object store under the tenant's ``knowledge`` class, the row here with its hash). On
  install each one is copied to the connection's knowledge as ``pending_review``, so the US-102 review gate and the
  US-203 definition proposals still apply. Same security as the pack's documents: a tenant reads system packs' and
  its own, and changes only its own drafts' (a released pack's attachments are frozen with it).

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-06
"""

from alembic import context

from migrations.helpers import run

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        """
        CREATE TABLE model.pack_attachments (
          id                 uuid PRIMARY KEY,
          pack_id            uuid NOT NULL REFERENCES model.starter_packs(id) ON DELETE CASCADE,
          tenant_id          uuid REFERENCES iam.tenants(id) ON DELETE CASCADE,
          title              text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
          original_filename  text NOT NULL,
          content_type       text NOT NULL,
          size_bytes         bigint NOT NULL CHECK (size_bytes > 0),
          content_hash       text NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
          object_key         text NOT NULL,
          created_by         uuid,
          created_at         timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT uq_pack_attachment UNIQUE (pack_id, content_hash)
        );
        CREATE INDEX ix_pack_attachments_object ON model.pack_attachments (object_key);

        -- An attachment carries its pack's owner; a released pack's attachments never change.
        CREATE FUNCTION model.guard_pack_attachment() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = model, pg_temp AS $$
        DECLARE p record;
        BEGIN
          SELECT tenant_id, status INTO p FROM model.starter_packs WHERE id = NEW.pack_id;
          IF NEW.tenant_id IS DISTINCT FROM p.tenant_id THEN
            RAISE EXCEPTION 'a pack attachment belongs to its pack''s owner';
          END IF;
          IF TG_OP = 'UPDATE' AND (p.status <> 'draft' OR NEW.pack_id <> OLD.pack_id) THEN
            RAISE EXCEPTION 'the attachments of a % pack are frozen', p.status;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_pack_attachments_guard BEFORE INSERT OR UPDATE ON model.pack_attachments
          FOR EACH ROW EXECUTE FUNCTION model.guard_pack_attachment();
        REVOKE ALL ON FUNCTION model.guard_pack_attachment() FROM PUBLIC;

        ALTER TABLE model.pack_attachments ENABLE ROW LEVEL SECURITY;
        CREATE POLICY pack_attachment_read ON model.pack_attachments FOR SELECT
          USING (tenant_id IS NULL OR tenant_id = util.current_tenant());
        CREATE POLICY pack_attachment_write ON model.pack_attachments FOR ALL
          USING (tenant_id = util.current_tenant() AND EXISTS (
                   SELECT 1 FROM model.starter_packs p WHERE p.id = pack_id AND p.status = 'draft'))
          WITH CHECK (tenant_id = util.current_tenant() AND EXISTS (
                   SELECT 1 FROM model.starter_packs p WHERE p.id = pack_id AND p.status = 'draft'));
        """
    )
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON model.pack_attachments TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP TABLE IF EXISTS model.pack_attachments;
        DROP FUNCTION IF EXISTS model.guard_pack_attachment();
        """
    )
