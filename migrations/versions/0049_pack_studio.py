"""Pack Studio (ENH-025, US-232; FEAT-075; DEC-26).

* New permission ``packs.author`` for the Admin system role (DEC-26 point 1): author the tenant's packs in
  Admin > Packs. Release (US-238) stays four eyes and is a separate step.
* ``model.starter_packs.released_at`` may be empty while a pack is a draft (a draft has not been released); a
  released or retired pack still has it. ``updated_at`` records the last edit of a draft (shown on the Versions tab).

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-06
"""

from migrations.helpers import run

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

ADMIN_ROLE = "00000000-0000-7000-8000-00000000a001"


def upgrade() -> None:
    run(
        """
        ALTER TABLE model.starter_packs
          ALTER COLUMN released_at DROP NOT NULL,
          ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now(),
          ADD CONSTRAINT ck_starter_pack_released CHECK (status = 'draft' OR released_at IS NOT NULL);
        CREATE TRIGGER trg_starter_packs_updated_at BEFORE UPDATE ON model.starter_packs
          FOR EACH ROW EXECUTE FUNCTION util.set_updated_at();
        """
    )
    run(
        f"INSERT INTO iam.role_permissions (role_id, permission_key) VALUES ('{ADMIN_ROLE}', 'packs.author') "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    run("DELETE FROM iam.role_permissions WHERE permission_key = 'packs.author'")
    run(
        """
        DROP TRIGGER IF EXISTS trg_starter_packs_updated_at ON model.starter_packs;
        UPDATE model.starter_packs SET released_at = created_at WHERE released_at IS NULL;
        ALTER TABLE model.starter_packs
          DROP CONSTRAINT IF EXISTS ck_starter_pack_released,
          DROP COLUMN IF EXISTS updated_at,
          ALTER COLUMN released_at SET NOT NULL;
        """
    )
