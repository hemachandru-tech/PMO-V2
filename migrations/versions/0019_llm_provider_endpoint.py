"""More model providers under DEC-01 (US-011, US-042; FEAT-049).

* ``ops.llm_settings.endpoint`` — the tenant's Azure OpenAI resource (``https://<resource>.openai.azure.com``),
  which also fixes the region. Not a secret, so it is shown to Admins; the API key stays in the secrets store.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-23
"""

from migrations.helpers import run

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE ops.llm_settings ADD COLUMN endpoint text
          CHECK (endpoint IS NULL OR (endpoint ~ '^https://[A-Za-z0-9.\\-]+(:[0-9]+)?/?$' AND length(endpoint) <= 200));
        """
    )


def downgrade() -> None:
    run("ALTER TABLE ops.llm_settings DROP COLUMN IF EXISTS endpoint;")
