import { useMemo, type ReactNode } from "react";

import type { Column } from "../api/app";
import { formatValue } from "./format";
import { columnRanges, HeatCell, ToneCell, type Tones } from "./tableColour";
import { Button } from "./ui/button";
import { DataTable, type DataColumn } from "./ui/data-table";

/**
 * Query results as a table (the table alternative of every chart, and drill records): DataTable with columns from the
 * result's fields, figures in their field format. Measures become buttons that drill to records when allowed. With
 * `tones` the table is in colour (ENH-020): status words as RAG badges, figures tinted by size within their column.
 */
export function ResultTable({ columns, rows, onMark, caption, serverPaged, footer, onRowActivate, tones }: {
  columns: Column[];
  rows: unknown[][];
  onMark?: (m: Record<string, unknown>) => void;
  caption?: string;
  serverPaged?: boolean;
  footer?: ReactNode;
  onRowActivate?: (row: unknown[]) => void;
  tones?: Tones;
}) {
  const dims = useMemo(() => columns.filter((c) => c.kind !== "measure"), [columns]);
  const ranges = useMemo(() => (tones ? columnRanges(rows, columns.flatMap((c, j) => (c.kind === "measure" ? [j] : []))) : null),
    [tones, rows, columns]);
  const defs = useMemo<DataColumn<unknown[]>[]>(() => columns.map((c, j) => {
    const measure = c.kind === "measure";
    const numeric = measure || ["integer", "number", "numeric", "decimal", "float"].includes(c.data_type);
    const heat = (r: unknown[], body: ReactNode) => <HeatCell value={r[j]} range={ranges?.get(j)}>{body}</HeatCell>;
    return {
      id: c.name,
      header: c.label,
      value: (r: unknown[]) => r[j],
      numeric,
      format: measure ? c.format : null,
      date: c.data_type === "date" || c.data_type === "timestamp",
      cell: measure && onMark && dims.length > 0
        ? (r: unknown[]) => heat(r, (
          <Button variant="link" size="sm" className="h-auto font-mono tabular-nums"
            onClick={() => onMark(Object.fromEntries(dims.map((d) => [d.name, r[columns.indexOf(d)]])))}>
            {formatValue(r[j], c.format)}
          </Button>
        ))
        : measure && ranges ? (r: unknown[]) => heat(r, formatValue(r[j], c.format))
        : !measure && tones ? (r: unknown[]) => (r[j] == null || r[j] === "" ? "—" : <ToneCell text={formatValue(r[j])} tone={tones[String(r[j])]} />)
        : undefined,
    };
  }), [columns, dims, onMark, ranges, tones]);
  return (
    <DataTable data={rows} columns={defs} label={caption ?? "Results"} compact banded search={rows.length > 10} columnMenu={columns.length > 6}
      serverPaged={serverPaged} footer={footer} onRowActivate={onRowActivate} />
  );
}
