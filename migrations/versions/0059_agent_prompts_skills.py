"""Agents screen: editable prompts and skillsets (ENH-030, US-264 / US-265; FEAT-080).

* ``ops.agent_prompt_versions`` — every save of an agent prompt is a new version: the text, a note, who and when. The
  newest version of a key is the one in use; a version with no text (``body IS NULL``) means "back to the code
  default". Keys come from the agent catalogue in code (``tela_api.agents.catalog``).
* ``ops.agent_skills`` — skill notes (Markdown) an Admin gives an agent; those that are on are added to every system
  prompt of that agent.

Revision ID: 0059
Revises: 0058
Create Date: 2026-11-13
"""

from alembic import context

from migrations.helpers import run

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

AGENT_CHECK = "agent ~ '^AG-(0[1-9]|10)$'"


def _app_role() -> str:
    role = context.get_x_argument(as_dictionary=True).get("tela.app_role", "tela_app")
    if not role.replace("_", "").isalnum():
        raise ValueError("invalid app role name")
    return role


def upgrade() -> None:
    run(
        f"""
        CREATE TABLE ops.agent_prompt_versions (
          id          uuid PRIMARY KEY,
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          prompt_key  text NOT NULL CHECK (prompt_key ~ '^[a-z_]+\\.[a-z_]+$'),
          version     integer NOT NULL CHECK (version >= 1),
          body        text CHECK (body IS NULL OR length(body) BETWEEN 1 AND 20000),
          note        text NOT NULL DEFAULT '' CHECK (length(note) <= 500),
          created_by  uuid,
          created_at  timestamptz NOT NULL DEFAULT now(),
          UNIQUE (tenant_id, prompt_key, version)
        );

        CREATE TABLE ops.agent_skills (
          id          uuid PRIMARY KEY,
          tenant_id   uuid NOT NULL REFERENCES iam.tenants(id) ON DELETE RESTRICT,
          agent       text NOT NULL CHECK ({AGENT_CHECK}),
          name        text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
          body        text NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
          enabled     boolean NOT NULL DEFAULT true,
          position    integer NOT NULL DEFAULT 0,
          created_by  uuid,
          updated_by  uuid,
          created_at  timestamptz NOT NULL DEFAULT now(),
          updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE UNIQUE INDEX ux_agent_skills_name ON ops.agent_skills (tenant_id, agent, lower(name));
        """
    )
    for table in ("ops.agent_prompt_versions", "ops.agent_skills"):
        run(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        run(f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = util.current_tenant()) WITH CHECK (tenant_id = util.current_tenant())")
    role = _app_role()
    run(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON ops.agent_prompt_versions, ops.agent_skills TO {role};
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP TABLE IF EXISTS ops.agent_skills;
        DROP TABLE IF EXISTS ops.agent_prompt_versions;
        """
    )
