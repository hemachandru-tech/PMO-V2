"""Runner-side statement guard: the shared checks live in ``tela_common.sql_guard`` (also used by the API's
custom SQL validator, FEAT-031), so both layers refuse exactly the same statements."""

from tela_common.sql_guard import FORBIDDEN_FUNCTIONS, FORBIDDEN_NODES, SYSTEM_SCHEMAS, check_statement

__all__ = ["FORBIDDEN_FUNCTIONS", "FORBIDDEN_NODES", "SYSTEM_SCHEMAS", "check_statement"]
