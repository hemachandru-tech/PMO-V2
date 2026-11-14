"""Free-hand answer → widget → certification (ENH-008, US-166; FEAT-060 / EPIC-08).

* ``content.personal_fields.kind`` accepts ``freehand``: a person's own rule (an ad-hoc measure, US-163) kept with
  the question it came from, so it can go through the certification queue like other personal definitions.
* ``content.widgets.certification_state`` accepts ``pending_certification`` (the owner's library only, never on a
  report, shared or exported) while an Admin decides.
* ``gov.certification_requests`` accepts ``freehand`` items and the ``changes_requested`` status;
  ``gov.certification_decisions`` accepts ``request_changes``, which needs a note like ``reject``.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-28
"""

from migrations.helpers import run

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run(
        """
        ALTER TABLE content.personal_fields DROP CONSTRAINT personal_fields_kind_check;
        ALTER TABLE content.personal_fields ADD CONSTRAINT personal_fields_kind_check
          CHECK (kind IN ('grouping', 'band', 'join_measure', 'freehand'));

        ALTER TABLE content.widgets DROP CONSTRAINT widgets_certification_state_check;
        ALTER TABLE content.widgets ADD CONSTRAINT widgets_certification_state_check
          CHECK (certification_state IN ('certified', 'personal', 'exploratory', 'custom_sql',
                                         'pending_certification'));

        ALTER TABLE gov.certification_requests DROP CONSTRAINT certification_requests_item_type_check;
        ALTER TABLE gov.certification_requests ADD CONSTRAINT certification_requests_item_type_check
          CHECK (item_type IN ('grouping', 'band', 'join_measure', 'custom_sql', 'freehand'));
        ALTER TABLE gov.certification_requests DROP CONSTRAINT certification_requests_status_check;
        ALTER TABLE gov.certification_requests ADD CONSTRAINT certification_requests_status_check
          CHECK (status IN ('pending', 'certified', 'merged', 'rejected', 'withdrawn', 'changes_requested'));

        ALTER TABLE gov.certification_decisions DROP CONSTRAINT certification_decisions_decision_check;
        ALTER TABLE gov.certification_decisions ADD CONSTRAINT certification_decisions_decision_check
          CHECK (decision IN ('certify', 'merge', 'reject', 'request_changes'));
        ALTER TABLE gov.certification_decisions DROP CONSTRAINT ck_reject_note;
        ALTER TABLE gov.certification_decisions ADD CONSTRAINT ck_reject_note
          CHECK (decision NOT IN ('reject', 'request_changes') OR coalesce(char_length(note), 0) > 0);
        """
    )


def downgrade() -> None:
    run(
        """
        ALTER TABLE gov.certification_decisions DROP CONSTRAINT ck_reject_note;
        ALTER TABLE gov.certification_decisions ADD CONSTRAINT ck_reject_note
          CHECK (decision <> 'reject' OR coalesce(char_length(note), 0) > 0);
        -- Decisions are insert-only (a trigger refuses DELETE): a downgrade keeps request_changes rows readable.
        ALTER TABLE gov.certification_decisions DROP CONSTRAINT certification_decisions_decision_check;
        ALTER TABLE gov.certification_decisions ADD CONSTRAINT certification_decisions_decision_check
          CHECK (decision IN ('certify', 'merge', 'reject', 'request_changes'));

        UPDATE gov.certification_requests SET status = 'withdrawn' WHERE status = 'changes_requested';
        ALTER TABLE gov.certification_requests DROP CONSTRAINT certification_requests_status_check;
        ALTER TABLE gov.certification_requests ADD CONSTRAINT certification_requests_status_check
          CHECK (status IN ('pending', 'certified', 'merged', 'rejected', 'withdrawn'));
        ALTER TABLE gov.certification_requests DROP CONSTRAINT certification_requests_item_type_check;
        ALTER TABLE gov.certification_requests ADD CONSTRAINT certification_requests_item_type_check
          CHECK (item_type IN ('grouping', 'band', 'join_measure', 'custom_sql', 'freehand'));

        UPDATE content.widgets SET certification_state = 'exploratory'
          WHERE certification_state = 'pending_certification';
        ALTER TABLE content.widgets DROP CONSTRAINT widgets_certification_state_check;
        ALTER TABLE content.widgets ADD CONSTRAINT widgets_certification_state_check
          CHECK (certification_state IN ('certified', 'personal', 'exploratory', 'custom_sql'));
        """
    )
