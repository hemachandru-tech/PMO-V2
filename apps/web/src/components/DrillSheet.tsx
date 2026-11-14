import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "../api/client";
import type { PageFilter, Records } from "../api/app";
import type { QuerySpec } from "../api/querySpec";
import { asApiError } from "../lib/api-errors";
import { ResultTable } from "./ResultTable";
import { DefinitionBody, type DefinitionProps } from "./Definition";
import { FormAlert } from "./ui/alert";
import { Button } from "./ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";
import { Skeleton } from "./ui/skeleton";

export interface DrillTarget {
  title: string;
  mark: Record<string, unknown>;
  widgetId?: string;
  versionId?: string | null;
  spec?: QuerySpec;
  pageFilters?: PageFilter[];
  /** What the reader picked in the widget's own filters (US-221). */
  widgetFilters?: PageFilter[];
  definition?: DefinitionProps;
}

/**
 * Drill to records as a side sheet (FEAT-044; US-139): the definition first, then the records behind the mark in the
 * virtualised DataTable — scoped, hidden fields removed, 100 per page, capped at 1,000 by the API. Enter on a row
 * opens the read-only record below the table.
 */
export function DrillSheet({ target, onClose }: { target: DrillTarget | null; onClose: () => void }) {
  return (
    <Sheet open={target !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-3xl">
        {target && <DrillBody target={target} />}
      </SheetContent>
    </Sheet>
  );
}

function DrillBody({ target }: { target: DrillTarget }) {
  const { widgetId, versionId, spec, mark, pageFilters, widgetFilters } = target;
  const [offset, setOffset] = useState(0);
  const [record, setRecord] = useState<unknown[] | null>(null);
  const q = useQuery({
    queryKey: ["drill", widgetId, versionId, spec, mark, pageFilters, widgetFilters, offset],
    queryFn: () =>
      widgetId
        ? api<Records>(`/api/v1/widgets/${widgetId}/drill`, {
          method: "POST", body: { mark, page_filters: pageFilters ?? [], widget_filters: widgetFilters ?? [], offset, context: { dashboard_version_id: versionId ?? null } },
        })
        : api<Records>("/api/v1/query/records", { method: "POST", body: { spec, mark, offset } }),
  });
  const data = q.data;
  const label = Object.entries(mark).map(([k, v]) => `${k}: ${v ?? "Not set"}`).join(", ") || "All records";
  return (
    <>
      <SheetHeader>
        <span className="text-label uppercase text-ink-muted">Drill to records</span>
        <SheetTitle>{target.title}{data && !data.hidden ? ` · ${data.total.toLocaleString()} records` : ""}</SheetTitle>
        <SheetDescription>{label}. Read-only, within your data scope.</SheetDescription>
      </SheetHeader>
      <div className="grid gap-4 px-4 pb-6">
        {target.definition && (
          <section aria-label="Definition" className="rounded-md border border-line p-3">
            <DefinitionBody {...target.definition} />
          </section>
        )}
        {q.isLoading && <Skeleton className="h-48" />}
        {q.error && <FormAlert error={asApiError(q.error)} />}
        {data?.hidden && <p className="text-ink-muted">{data.message}</p>}
        {data && !data.hidden && (
          <>
            {data.notice && <p className="text-small text-ink-muted">{data.notice}</p>}
            <ResultTable columns={data.columns} rows={data.rows} caption="Records" serverPaged onRowActivate={setRecord}
              footer={
                <span className="flex items-center gap-2">
                  <Button variant="secondary" size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>Previous</Button>
                  <span>{data.rows.length ? `${offset + 1}–${offset + data.rows.length}` : "0"} of {data.total.toLocaleString()}</span>
                  <Button variant="secondary" size="sm" disabled={data.next_offset === null} onClick={() => setOffset(data.next_offset ?? offset)}>Next</Button>
                </span>
              } />
            {record && (
              <section aria-label="Selected record" className="grid gap-2 rounded-md border border-line p-3">
                <div className="flex items-baseline justify-between gap-2">
                  <h3 className="text-heading">Record</h3>
                  <Button variant="link" size="sm" onClick={() => setRecord(null)}>Close record</Button>
                </div>
                <dl className="grid grid-cols-[minmax(8rem,auto)_1fr] gap-x-3 gap-y-1 text-body">
                  {data.columns.map((c, i) => (
                    <div key={c.name} className="contents">
                      <dt className="text-ink-muted">{c.label}</dt>
                      <dd className="break-words">{String(record[i] ?? "—")}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}
          </>
        )}
      </div>
    </>
  );
}
