"""Pack release with four eyes (ENH-025, US-238; FEAT-075; DEC-26 point 5).

* ``model.starter_packs`` gets the review of a tenant draft: ``submitted_by`` / ``submitted_at`` /
  ``submitted_revision`` (the author submits a checked revision for review), ``review`` (what was submitted: the check
  report, the reason given for each warning, the test connection, a note), ``returned_by`` / ``returned_at`` /
  ``returned_reason`` (a reviewer sent it back) and ``released_by``.
* The application role may now move its own tenant's draft to ``released`` (release goes through the API, which runs
  the checks); a released or retired row stays frozen (migration 0048's trigger).
* The trigger also holds the four eyes: a tenant draft becomes released only when it was submitted, names who
  released it and when, and that person is neither its author (``created_by``) nor the one who submitted it.

Revision ID: 0052
Revises: 0051
Create Date: 2026-11-06
"""

from migrations.helpers import run

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None

_GUARD = """
        CREATE OR REPLACE FUNCTION model.guard_starter_pack() RETURNS trigger
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
{release}
          END IF;
          RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION model.guard_starter_pack() FROM PUBLIC;
"""

_FOUR_EYES = """
            IF OLD.status = 'draft' AND NEW.status = 'released' AND NEW.tenant_id IS NOT NULL AND (
                 NEW.submitted_at IS NULL OR NEW.submitted_by IS NULL OR NEW.released_by IS NULL
                 OR NEW.released_at IS NULL OR NEW.released_by = NEW.submitted_by
                 OR NEW.released_by IS NOT DISTINCT FROM NEW.created_by) THEN
              RAISE EXCEPTION 'pack % % needs a review by someone other than its author', NEW.pack_key,
                NEW.pack_version USING ERRCODE = 'check_violation';
            END IF;
"""


def upgrade() -> None:
    run(
        """
        ALTER TABLE model.starter_packs
          ADD COLUMN submitted_by uuid,
          ADD COLUMN submitted_at timestamptz,
          ADD COLUMN submitted_revision text,
          ADD COLUMN review jsonb,
          ADD COLUMN returned_by uuid,
          ADD COLUMN returned_at timestamptz,
          ADD COLUMN returned_reason text,
          ADD COLUMN released_by uuid;

        DROP POLICY IF EXISTS pack_update ON model.starter_packs;
        CREATE POLICY pack_update ON model.starter_packs FOR UPDATE
          USING (tenant_id = util.current_tenant() AND status = 'draft')
          WITH CHECK (tenant_id = util.current_tenant() AND status IN ('draft', 'released'));
        """
        + _GUARD.replace("{release}", _FOUR_EYES)
    )


def downgrade() -> None:
    run(
        """
        DROP POLICY IF EXISTS pack_update ON model.starter_packs;
        CREATE POLICY pack_update ON model.starter_packs FOR UPDATE
          USING (tenant_id = util.current_tenant() AND status = 'draft')
          WITH CHECK (tenant_id = util.current_tenant() AND status = 'draft');
        """
        + _GUARD.replace("{release}", "")
        + """
        ALTER TABLE model.starter_packs
          DROP COLUMN IF EXISTS submitted_by, DROP COLUMN IF EXISTS submitted_at,
          DROP COLUMN IF EXISTS submitted_revision, DROP COLUMN IF EXISTS review,
          DROP COLUMN IF EXISTS returned_by, DROP COLUMN IF EXISTS returned_at,
          DROP COLUMN IF EXISTS returned_reason, DROP COLUMN IF EXISTS released_by;
        """
    )
