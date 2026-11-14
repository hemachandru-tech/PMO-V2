"""Report text blocks (ENH-007, US-158).

* ``content.report_items.item_kind`` accepts ``callout`` (title + text, info or warning) and ``footnote`` (text at the
  end of a report); ``text_title`` and ``text_tone`` hold a callout's title and level.
* ``content.dashboard_tabs.texts`` carries a starter tab's callouts and footnote from the pack.
* A detail table's columns, sort and export name live in the widget's ``spec.visual.detail`` (no new column).

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-27
"""

from migrations.helpers import run

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE content.report_items DROP CONSTRAINT report_items_item_kind_check;
        ALTER TABLE content.report_items ADD CONSTRAINT report_items_item_kind_check
          CHECK (item_kind IN ('widget', 'text_note', 'callout', 'footnote'));
        ALTER TABLE content.report_items
          ADD COLUMN text_title text CHECK (text_title IS NULL OR char_length(text_title) <= 200),
          ADD COLUMN text_tone text CHECK (text_tone IS NULL OR text_tone IN ('info', 'warning'));
        ALTER TABLE content.dashboard_tabs ADD COLUMN texts jsonb NOT NULL DEFAULT '[]';
        """
    )


def downgrade() -> None:
    run(
        """
        ALTER TABLE content.dashboard_tabs DROP COLUMN texts;
        UPDATE content.report_items SET item_kind = 'text_note' WHERE item_kind IN ('callout', 'footnote');
        ALTER TABLE content.report_items DROP COLUMN text_title, DROP COLUMN text_tone;
        ALTER TABLE content.report_items DROP CONSTRAINT report_items_item_kind_check;
        ALTER TABLE content.report_items ADD CONSTRAINT report_items_item_kind_check
          CHECK (item_kind IN ('widget', 'text_note'));
        """
    )
