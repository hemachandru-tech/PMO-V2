import { useEffect, useState } from "react";
import { toast } from "sonner";

import type { Column } from "../../../api/app";
import { api, type ApiError } from "../../../api/client";
import type { Branding } from "../../../app/branding";
import { Chart } from "../../../components/Chart";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { RadioGroup, RadioGroupItem } from "../../../components/ui/radio-group";
import { CHART_LIBRARIES, type ChartLibrary } from "../../../design/chartLibrary";
import { asApiError } from "../../../lib/api-errors";

const col = (name: string, label: string, kind: Column["kind"], data_type: string, format: string | null): Column =>
  ({ name, label, kind, ref: name, data_type, format });
const STATUS = [col("status", "Status", "dimension", "text", null), col("projects", "Projects", "measure", "integer", "integer")];
const STATUS_ROWS = [["Active", 279], ["On Hold", 79], ["Project Created", 73], ["Closed", 41]];
const MONTHS = [col("month", "Month", "period", "text", null), col("opened", "Opened", "measure", "integer", "integer"), col("closed", "Closed", "measure", "integer", "integer")];
const MONTH_ROWS = [["2026-05", 18, 9], ["2026-06", 22, 14], ["2026-07", 17, 19], ["2026-08", 25, 16], ["2026-09", 21, 23], ["2026-10", 28, 20]];

/**
 * Chart library (ENH-018, US-206): which library draws the standard charts for everyone — ECharts, shadcn/ui Charts
 * on Recharts, or Observable Plot. The preview draws the same two charts with the library picked, before saving.
 * The visual catalogue (waterfall, Gantt, sankey …) keeps its own drawing whatever the choice.
 */
export function ChartLibraryCard({ branding, onSaved }: { branding: Branding; onSaved: (b: Branding) => void }) {
  const stored = branding.chart_library ?? "echarts";
  const [library, setLibrary] = useState<ChartLibrary>(stored);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setLibrary(stored), [branding.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setError(null);
    setBusy(true);
    try {
      const out = await api<Branding>("/api/v1/admin/branding/chart-library", { method: "PUT", body: { library, expected_version: branding.version } });
      onSaved(out);
      toast.success("Chart library saved — everyone sees it now");
    } catch (e) {
      setError(asApiError(e, "The chart library could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Chart library</CardTitle>
        <CardDescription>Which library draws bar, column, line and donut charts on dashboards, in Ask and in PDF exports. Special visuals such as waterfalls and timelines keep their own drawing.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        <form className="grid content-start gap-4" aria-label="Chart library" onSubmit={(e) => { e.preventDefault(); if (library !== stored) void save(); }}>
          <RadioGroup value={library} onValueChange={(v) => setLibrary(v as ChartLibrary)} aria-label="Chart library" className="gap-3">
            {CHART_LIBRARIES.map((l) => (
              <label key={l.id} htmlFor={`chart-library-${l.id}`} className="flex cursor-pointer items-start gap-3 rounded-md border border-line p-3 has-[[data-state=checked]]:border-accent">
                <RadioGroupItem id={`chart-library-${l.id}`} value={l.id} className="mt-0.5" />
                <span className="grid gap-0.5">
                  <span className="font-medium">{l.label}{l.id === "echarts" && <span className="font-normal text-ink-muted"> · default</span>}</span>
                  <span className="text-small text-ink-muted">{l.note}</span>
                </span>
              </label>
            ))}
          </RadioGroup>
          {error && <FormAlert error={error} />}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || library === stored}>{busy ? "Saving…" : "Save chart library"}</Button>
            {library !== stored && <Button type="button" variant="ghost" onClick={() => setLibrary(stored)}>Discard</Button>}
          </div>
        </form>
        <div className="grid content-start gap-3" aria-label="Chart library preview" data-preview-library={library}>
          <span className="text-label uppercase tracking-wider text-ink-muted">Preview</span>
          <div className="grid gap-3 xl:grid-cols-2">
            <div className="min-w-0 rounded-md border border-line bg-surface-raised p-3">
              <span className="text-small font-medium">Open projects by status</span>
              <Chart spec={{ type: "columns", x: "status", series: ["projects"] }} columns={STATUS} rows={STATUS_ROWS} title="Open projects by status" view="chart" library={library} />
            </div>
            <div className="min-w-0 rounded-md border border-line bg-surface-raised p-3">
              <span className="text-small font-medium">Projects opened and closed per month</span>
              <Chart spec={{ type: "trend", x: "month", series: ["opened", "closed"] }} columns={MONTHS} rows={MONTH_ROWS} title="Projects opened and closed per month" view="chart" library={library} />
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
