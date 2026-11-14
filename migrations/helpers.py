"""Helpers shared by migration scripts."""

from __future__ import annotations

from alembic import op


def run(statement: str) -> None:
    """Execute raw DDL through the driver, with no bind-parameter parsing.

    ``op.execute`` would treat ``:name`` (e.g. inside JSON defaults) and ``%``
    (e.g. in ``format('%I')``) as parameter markers.
    """
    # SQLAlchemy passes an empty parameter set, so psycopg still scans for "%"
    # placeholders; doubling "%" makes it emit a literal "%".
    op.get_bind().exec_driver_sql(statement.replace("%", "%%"))
