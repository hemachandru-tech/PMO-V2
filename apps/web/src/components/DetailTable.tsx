import { useMemo, useState } from "react";

import type { Column, DetailRun } from "../api/app";
import { formatValue } from "./format";
import { columnRanges, HeatCell, ToneCell, type Tones } from "./tableColour";
import { Button } from "./ui/button";
import { DataTable, type DataColumn } from "./ui/data-table";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";

const NUMERIC = ["integer", "number", "numeric", "decimal", "float"];

/**
 * A detail table (US-158): the records of a report's population with the catalogue's columns, in the widget's default
 * sort (from the API), sortable and searchable here. The key columns stay in the Columns menu. A row opens the record
 * drawer with every listed field; "See all records" pages through up to 1,000 in the drill sheet. With `tones` it is in
 * colour (ENH-020): status words as RAG badges, numbers tinted by size within their column.
 */
export function DetailTable({ title, columns, rows, detail, total, onSeeAll, tones }: {
  title: string;
  columns: Column[];
  rows: unknown[][];
  detail: DetailRun;
  total: number;
  onSeeAll?: () => void;
  tones?: Tones;
}) {
  const [record, setRecord] = useState<unknown[] | null>(null);
  const shown = useMemo(() => new Set(detail.columns), [detail.columns]);
  const ranges = useMemo(() => (tones ? columnRanges(rows, columns.flatMap((c, j) => (NUMERIC.includes(c.data_type) ? [j] : []))) : null),
    [tones, rows, columns]);
  const defs = useMemo<DataColumn<unknown[]>[]>(() => columns.map((c, j) => ({
    id: c.name,
    header: c.label,
    value: (r: unknown[]) => r[j],
    numeric: NUMERIC.includes(c.data_type),
    date: c.data_type === "date" || c.data_type === "timestamp",
    hidden: !shown.has(c.name),
    cell: (r: unknown[]) => (r[j] === null || r[j] === undefined || r[j] === "" ? <span className="text-ink-muted">Not set</span>
      : NUMERIC.includes(c.data_type) ? <HeatCell value={r[j]} range={ranges?.get(j)}>{formatValue(r[j], c.format)}</HeatCell>
      : tones ? <ToneCell text={formatValue(r[j])} tone={tones[String(r[j])]} />
      : formatValue(r[j])),
  })), [columns, shown, ranges, tones]);
  const label = (r: unknown[]) => detail.columns.slice(0, 2).map((name) => r[columns.findIndex((c) => c.name === name)]).filter((v) => v != null).join(" · ")
    || String(r[0] ?? "Record");
  return (
    <>
      <DataTable data={rows} columns={defs} label={title} compact banded search={rows.length > 10} columnMenu rowLabel={label}
        onRowActivate={setRecord}
        empty={{ title: "No records", body: "No records match the current scope and filters." }}
        footer={total > rows.length ? (
          <span className="flex flex-wrap items-center gap-2">
            <span>First {rows.length.toLocaleString()} of {total.toLocaleString()} records{detail.export_name ? ` — the Excel export names this table “${detail.export_name}”` : ""}.</span>
            {onSeeAll && <Button variant="link" size="sm" className="h-auto p-0" onClick={onSeeAll}>See all records</Button>}
          </span>
        ) : undefined} />
      <Sheet open={record !== null} onOpenChange={(open) => !open && setRecord(null)}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          {record && (
            <>
              <SheetHeader>
                <span className="text-label uppercase text-ink-muted">{title}</span>
                <SheetTitle>{label(record)}</SheetTitle>
                <SheetDescription>Read-only, within your data scope.</SheetDescription>
              </SheetHeader>
              <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-2 px-4 pb-6 text-body">
                {columns.map((c, i) => (
                  <div key={c.name} className="contents">
                    <dt className="text-ink-muted">{c.label}</dt>
                    <dd className="break-words">{record[i] === null || record[i] === undefined ? "Not set" : NUMERIC.includes(c.data_type) ? formatValue(record[i], c.format) : String(record[i])}</dd>
                  </div>
                ))}
              </dl>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
