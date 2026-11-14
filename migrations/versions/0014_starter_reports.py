"""Starter reports as content (EPIC-11 part 1; US-098; FEAT-046; DATA-023; RULE-017).

Until now a starter report existed only as a tab of a pack-owned starter dashboard. This makes each available
starter report a ``content.reports`` row (``origin = 'starter'``) that the pack owns, so that:

* Creators can put a starter report on their own dashboards (by reference) or copy it (``origin = 'copy'``,
  ``copied_from_id``); a copy is theirs and never follows the pack;
* deleting a starter (RULE-017) can find every dashboard that includes it and every user copy.

``owner_id`` becomes optional: a starter report has no owner, so nobody can edit it through the canvas.
``model.starter_pack_items.content_id`` points at the report. User copies are counted from ``copied_from_id``.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-21
"""

from migrations.helpers import run

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE content.reports ALTER COLUMN owner_id DROP NOT NULL;
        ALTER TABLE content.reports ADD CONSTRAINT ck_report_owner
          CHECK ((origin = 'starter') = (owner_id IS NULL));
        CREATE INDEX ix_reports_copied_from ON content.reports (copied_from_id) WHERE copied_from_id IS NOT NULL;
        CREATE INDEX ix_dashboard_reports_report ON content.dashboard_reports (report_id);
        CREATE INDEX ix_starter_items_content ON model.starter_pack_items (content_id) WHERE content_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    run(
        """
        DROP INDEX IF EXISTS model.ix_starter_items_content;
        DROP INDEX IF EXISTS content.ix_dashboard_reports_report;
        DROP INDEX IF EXISTS content.ix_reports_copied_from;
        ALTER TABLE content.reports DROP CONSTRAINT IF EXISTS ck_report_owner;
        UPDATE model.starter_pack_items SET content_id = NULL WHERE item_kind = 'report';
        DELETE FROM content.dashboard_reports WHERE report_id IN (
          SELECT id FROM content.reports WHERE origin = 'starter');
        UPDATE content.reports SET copied_from_id = NULL WHERE copied_from_id IN (
          SELECT id FROM content.reports WHERE origin = 'starter');
        DELETE FROM content.reports WHERE origin = 'starter';
        ALTER TABLE content.reports ALTER COLUMN owner_id SET NOT NULL;
        """
    )
