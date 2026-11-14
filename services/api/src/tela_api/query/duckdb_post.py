"""DuckDB post-step for window operations on MongoDB and Dataverse Web API results (US-177).

When the source is a document store or a Web API that cannot evaluate window functions, the grouped
rows are fetched first, then a DuckDB in-memory table applies the window SQL on the local result.
The post-step SQL is built from the validated spec's window operations and run with ``duckdb``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import duckdb

from tela_api.query.compiler import ColumnMeta
from tela_api.query.validator import ValidatedSpec
from tela_query_spec.spec import PERCENTILE_VALUES

ORDER_COL = "__tela_ord"


def build_post_sql(vs: ValidatedSpec, columns: list[ColumnMeta]) -> tuple[str, list[ColumnMeta]] | None:
    """Build DuckDB SQL that applies window operations over a temporary ``tela_rows`` table whose columns
    match the pre-window result. Returns (sql, additional_columns) or None when there are no window ops."""
    if not vs.window_ops:
        return None
    col_names = [c.name for c in columns]
    selects = [f'"{c}"' for c in col_names]
    extra_columns: list[ColumnMeta] = []
    filters: list[str] = []
    rn_name: str | None = None
    rn_limit: int | None = None

    breakdowns = [c.name for c in columns if c.kind != "measure"]

    def ties(wo: Any, *skip: str) -> str:
        return "".join(f', "{b}"' for b in breakdowns if b != wo.partition_ref and b not in skip)

    def along(wo: Any) -> str:  # the time grain, empty periods last, then the other breakdowns
        return f'"{wo.params["along"]}" ASC NULLS LAST{ties(wo, wo.params["along"])}'

    for wo in vs.window_ops:
        partition = f'PARTITION BY "{wo.partition_ref}"' if wo.partition_ref else ""
        mcol = f'"{wo.measure_key}"'

        if wo.kind == "top_n_per_group":
            order = "DESC" if wo.params["order"] == "desc" else "ASC"
            rn_name = f"{wo.measure_key}__rn"
            rn_limit = wo.params["n"]
            selects.append(f'ROW_NUMBER() OVER ({partition} ORDER BY {mcol} {order} NULLS LAST{ties(wo)}) '
                           f'AS "{rn_name}"')

        elif wo.kind == "running_total":
            partition = partition or _groups(wo.params["groups"])
            rt_name = f"{wo.measure_key}__cumsum"
            selects.append(f'SUM({mcol}) OVER ({partition} ORDER BY {along(wo)} '
                           f'ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS "{rt_name}"')
            m_obj = next((m for m in vs.measures if m.key == wo.measure_key), None)
            extra_columns.append(ColumnMeta(rt_name, "measure",
                                            f"{m_obj.label if m_obj else wo.measure_key} (running total)",
                                            f"window:running_total:{wo.measure_key}", "decimal",
                                            m_obj.format if m_obj else None, of=wo.measure_key))

        elif wo.kind == "moving_avg":
            window_k = wo.params["window"]
            partition = partition or _groups(wo.params["groups"])
            ma_name = f"{wo.measure_key}__mavg{window_k}"
            selects.append(f'AVG({mcol}) OVER ({partition} ORDER BY {along(wo)} '
                           f'ROWS BETWEEN {window_k - 1} PRECEDING AND CURRENT ROW) AS "{ma_name}"')
            m_obj = next((m for m in vs.measures if m.key == wo.measure_key), None)
            extra_columns.append(ColumnMeta(ma_name, "measure",
                                            f"{m_obj.label if m_obj else wo.measure_key} ({window_k}-period avg)",
                                            f"window:moving_avg:{wo.measure_key}", "decimal",
                                            m_obj.format if m_obj else None, of=wo.measure_key))

        elif wo.kind == "rank":
            order = "DESC" if wo.params["order"] == "desc" else "ASC"
            rk_name = f"{wo.measure_key}__rank"
            selects.append(f'RANK() OVER ({partition} ORDER BY {mcol} {order} NULLS LAST) AS "{rk_name}"')
            extra_columns.append(ColumnMeta(rk_name, "measure", "Rank",
                                            f"window:rank:{wo.measure_key}", "integer", of=wo.measure_key))

        elif wo.kind == "bucket":
            k, order = wo.params["k"], "DESC" if wo.params["order"] == "desc" else "ASC"
            labels = wo.params.get("labels", [])
            bk_name = f"{wo.measure_key}__bucket"
            if labels:
                cases = " ".join(f"WHEN {i + 1} THEN '{lbl}'" for i, lbl in enumerate(labels))
                selects.append(f'CASE NTILE({k}) OVER ({partition} ORDER BY {mcol} {order} NULLS LAST{ties(wo)}) '
                               f'{cases} END AS "{bk_name}"')
            else:
                selects.append(f'NTILE({k}) OVER ({partition} ORDER BY {mcol} {order} NULLS LAST{ties(wo)}) '
                               f'AS "{bk_name}"')
            extra_columns.append(ColumnMeta(bk_name, "dimension", "Bucket",
                                            f"window:bucket:{wo.measure_key}", "text"))

        elif wo.kind == "percentile":
            q_val = PERCENTILE_VALUES[wo.params["p"]]
            p_name = wo.params["p"]
            pct_name = f"{wo.measure_key}__{p_name}"
            selects.append(f'quantile_cont(CAST({mcol} AS DOUBLE), {q_val}) OVER ({partition}) AS "{pct_name}"')
            m_obj = next((m for m in vs.measures if m.key == wo.measure_key), None)
            extra_columns.append(ColumnMeta(pct_name, "measure",
                                            f"{m_obj.label if m_obj else wo.measure_key} ({p_name.upper()})",
                                            f"window:percentile:{wo.measure_key}", "decimal",
                                            m_obj.format if m_obj else None, of=wo.measure_key))

        elif wo.kind == "having":
            op, value = wo.params["op"], wo.params["value"]
            filters.append(f'{mcol} {op} {value}')

    selects.append(f'"{ORDER_COL}"')
    sql = "SELECT " + ", ".join(selects) + " FROM tela_rows"  # noqa: S608
    if filters:
        sql += " WHERE " + " AND ".join(filters)

    final_cols = [f'"{c}"' for c in col_names] + [f'"{c.name}"' for c in extra_columns]
    keep = f' WHERE "{rn_name}" <= {rn_limit}' if rn_name and rn_limit is not None else ""
    # the source's order (its sort) is kept
    sql = f'SELECT {", ".join(final_cols)} FROM ({sql}){keep} ORDER BY "{ORDER_COL}"'  # noqa: S608

    return sql, extra_columns


def apply_post(rows: list[list[Any]], columns: list[ColumnMeta], sql: str,
               extra_columns: list[ColumnMeta]) -> tuple[list[list[Any]], list[ColumnMeta]]:
    """Run the post-step DuckDB SQL over the pre-window rows. Returns (new_rows, all_columns)."""
    if not rows:
        return rows, columns + extra_columns
    conn = duckdb.connect()
    try:
        col_defs = ", ".join(f'"{c.name}" {_duck_type(c, [r[i] for r in rows])}' for i, c in enumerate(columns))
        conn.execute(f'CREATE TABLE tela_rows ({col_defs}, "{ORDER_COL}" INTEGER)')
        placeholders = ", ".join("?" * (len(columns) + 1))
        conn.executemany(f"INSERT INTO tela_rows VALUES ({placeholders})",  # noqa: S608
                         [[*r, i] for i, r in enumerate(rows)])
        result = conn.execute(sql).fetchall()
        new_rows = [list(r) for r in result]
    finally:
        conn.close()
    return new_rows, columns + extra_columns


def _groups(outputs: list[str]) -> str:
    return "PARTITION BY " + ", ".join(f'"{g}"' for g in outputs) if outputs else ""


def _duck_type(c: ColumnMeta, values: list[Any]) -> str:
    """The column type from the values the source returned, so they come back as they went in (MongoDB gives
    periods as ISO text, which sorts in date order); the column's data type when every value is empty."""
    seen = {type(v) for v in values if v is not None}
    if seen and seen <= {bool, int}:
        return "BIGINT"
    if seen and seen <= {bool, int, float, Decimal}:
        return "DOUBLE"
    if seen == {date}:
        return "DATE"
    if seen and seen <= {date, datetime}:
        return "TIMESTAMP"
    if seen:
        return "VARCHAR"
    return {"integer": "BIGINT", "decimal": "DOUBLE", "currency": "DOUBLE"}.get(c.data_type, "VARCHAR")
