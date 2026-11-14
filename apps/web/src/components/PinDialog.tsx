import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import { z } from "zod";

import type { ApiError } from "../api/client";
import { api } from "../api/client";
import { VISUAL_NAMES, type PinResult, type Report, type ReportListItem, type VisualOptions } from "../api/authoring";
import type { QuerySpec } from "../api/querySpec";
import { PinIn } from "../api/schemas";
import { applyServerError } from "../lib/api-errors";
import { FormAlert } from "./ui/alert";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "./ui/dialog";
import { Field, Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "./ui/form";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { NativeSelect } from "./ui/native-select";
import { RadioGroup, RadioGroupItem } from "./ui/radio-group";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

const pinForm = z.object({
  title: PinIn.shape.title,
  visual: PinIn.shape.visual,
  reportId: z.string(),
  newTitle: z.string().max(200, "Keep the report title to 200 characters or fewer."),
  sectionId: z.string(),
  follow: z.boolean(),
});
type PinForm = z.infer<typeof pinForm>;

/**
 * Pin an answer to a report (FEAT-021, US-063): only suitable visuals can be chosen; unsuitable ones are greyed
 * out with the reason on hover. The widget stores the question's spec and model version, never the numbers.
 */
export function PinDialog({ turnId, spec, defaultTitle, defaultVisual, defaultReportId, onClose }: {
  turnId: string;
  spec: QuerySpec;
  defaultTitle: string;
  defaultVisual?: string | null;
  defaultReportId?: string | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const options = useQuery({
    queryKey: ["suitable", spec],
    queryFn: () => api<VisualOptions>("/api/v1/visuals/suitable", { method: "POST", body: { spec } }),
  });
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api<{ items: ReportListItem[] }>("/api/v1/reports") });
  const form = useForm<PinForm>({
    resolver: zodResolver(pinForm),
    mode: "onTouched",
    defaultValues: { title: defaultTitle.slice(0, 200), visual: "", reportId: defaultReportId ?? "", newTitle: "", sectionId: "", follow: true },
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [done, setDone] = useState<PinResult | null>(null);
  const [reportId, follow] = form.watch(["reportId", "follow"]);
  const report = useQuery({
    queryKey: ["report", reportId],
    queryFn: () => api<Report>(`/api/v1/reports/${reportId}`),
    enabled: !!reportId && reportId !== "new",
  });
  const mine = (reports.data?.items ?? []).filter((r) => r.mine);
  // A free-hand answer (US-166) becomes a widget in the library only, waiting for an Admin to certify its rule.
  const freehand = !!spec.measure.adhoc;

  // Start on the answer's own visual when it is suitable, else the recommended one.
  useEffect(() => {
    const o = options.data;
    if (!o || form.getValues("visual")) return;
    const initial = defaultVisual && o.suitable.includes(defaultVisual) ? defaultVisual : o.recommended ?? "table";
    form.setValue("visual", initial);
  }, [options.data, defaultVisual, form]);

  // A single figure has no categories or dates: a chart that needs them is offered with the breakdown it adds, and
  // a table of one number breaks the figure down first.
  const fixFor = (visual: string) => {
    const o = options.data;
    if (!o?.fixes?.[visual]) return undefined;
    return o.disabled.some((d) => d.visual === visual) || (visual === "table" && o.recommended === "figure") ? o.fixes[visual] : undefined;
  };

  const submit = async (v: PinForm) => {
    setError(null);
    try {
      let target: { report_id: string; section_id?: string } | undefined;
      if (freehand) {
        target = undefined;
      } else if (v.reportId === "new") {
        const created = await api<Report>("/api/v1/reports", { method: "POST", body: { title: v.newTitle.trim() || v.title.trim() } });
        target = { report_id: created.id };
      } else if (v.reportId) {
        target = { report_id: v.reportId, ...(v.sectionId ? { section_id: v.sectionId } : {}) };
      }
      const fix = fixFor(v.visual);
      const body: z.input<typeof PinIn> = { from_turn_id: turnId, title: v.title.trim(), visual: v.visual, target, follow_page_filters: v.follow, ...(fix ? { ops: fix.ops } : {}) };
      const out = await api<PinResult>("/api/v1/widgets", { method: "POST", body });
      setDone(out);
      void qc.invalidateQueries({ queryKey: ["reports"] });
      if (out.report) void qc.invalidateQueries({ queryKey: ["report", out.report.id] });
    } catch (e) {
      setError(applyServerError(form, e, { fields: { "target.report_id": "reportId", follow_page_filters: "follow" } }));
    }
  };

  const visuals = options.data
    ? [...options.data.suitable.map((v) => ({ v, reason: null as string | null })), ...options.data.disabled.map((d) => ({ v: d.visual, reason: options.data?.fixes?.[d.visual] ? null : d.reason }))]
    : [];
  const alternative = typeof error?.details.alternative === "string" ? error.details.alternative : null;
  const c = form.control;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{freehand ? "Add as widget" : "Pin to a report"}</DialogTitle>
          <DialogDescription>
            {freehand
              ? "This answer is not certified. The widget waits in your library while an Admin reviews the rule; until it is certified only you can see it, and it can't go on a report, be shared or be exported."
              : "The widget stays live: it keeps the question, not today's numbers."}
          </DialogDescription>
        </DialogHeader>
        {done ? (
          <>
            <p>
              {freehand && !done.reused
                ? "Added to your library as Pending certification. An Admin will review the rule, and you'll be notified of the decision."
                : done.reused ? "This definition was already in your library; it was reused." : "Pinned as a live widget."}
              {done.report && <> It is on <Link className="text-link underline-offset-4 hover:underline" to={`/reports/${done.report.id}`} onClick={onClose}>{done.report.title}</Link>.</>}
            </p>
            <DialogFooter><Button onClick={onClose}>Done</Button></DialogFooter>
          </>
        ) : (
          <Form {...form}>
            <form className="grid gap-4" onSubmit={form.handleSubmit(submit)} noValidate>
              <Field control={c} name="title" label="Title" render={(f) => <Input {...f} maxLength={200} />} />
              <FormField control={c} name="visual" render={({ field }) => (
                <FormItem>
                  <span className="text-body-strong" aria-hidden>Visual</span>
                  {options.isLoading && <p className="text-small text-ink-muted">Checking which visuals suit this answer…</p>}
                  <FormControl>
                    <RadioGroup aria-label="Visual" value={field.value} onValueChange={field.onChange} className="grid-cols-2 sm:grid-cols-3">
                      {visuals.map(({ v, reason }) => {
                        const item = (
                          <span className="flex items-center gap-2">
                            <RadioGroupItem id={`visual-${v}`} value={v} disabled={!!reason} aria-describedby={reason ? `why-${v}` : undefined} />
                            <Label htmlFor={`visual-${v}`} className={reason ? "font-normal text-ink-disabled" : "font-normal"}>{VISUAL_NAMES[v] ?? v}{fixFor(v) && <span className="ml-1 text-small text-ink-muted">{fixFor(v)!.label}</span>}</Label>
                            {reason && <span id={`why-${v}`} className="sr-only">Not available: {reason}</span>}
                          </span>
                        );
                        return reason ? (
                          <Tooltip key={v}>
                            <TooltipTrigger asChild>{item}</TooltipTrigger>
                            <TooltipContent>{reason}</TooltipContent>
                          </Tooltip>
                        ) : <span key={v}>{item}</span>;
                      })}
                    </RadioGroup>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )} />
              {!freehand && <div className="grid gap-4 sm:grid-cols-2">
                <Field control={c} name="reportId" label="Report" render={(f) => (
                  <NativeSelect {...f} onChange={(e) => { f.onChange(e); form.setValue("sectionId", ""); }}>
                    <option value="">Library only</option>
                    {mine.map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
                    <option value="new">New report…</option>
                  </NativeSelect>
                )} />
                {reportId === "new" && (
                  <Field control={c} name="newTitle" label="New report title" render={(f) => <Input {...f} maxLength={200} placeholder={form.getValues("title")} />} />
                )}
                {report.data && report.data.sections.length > 1 && (
                  <Field control={c} name="sectionId" label="Section" render={(f) => (
                    <NativeSelect {...f}>
                      <option value="">First section</option>
                      {report.data!.sections.map((s) => <option key={s.id} value={s.id}>{s.title}</option>)}
                    </NativeSelect>
                  )} />
                )}
              </div>}
              <FormField control={c} name="follow" render={({ field }) => (
                <FormItem>
                  <div className="flex items-center gap-2">
                    <FormControl><Checkbox checked={field.value} onCheckedChange={(on) => field.onChange(on === true)} /></FormControl>
                    <FormLabel className="font-normal">Follows page filters</FormLabel>
                  </div>
                  {!follow && <p className="text-small text-ink-muted">The widget will show a filter badge and ignore the page's filter bar.</p>}
                </FormItem>
              )} />
              <FormAlert error={error}>
                {alternative && (
                  <div>
                    <Button type="button" size="sm" variant="secondary" onClick={() => { form.setValue("visual", alternative); setError(null); }}>
                      Use {typeof error?.details.alternative_label === "string" ? error.details.alternative_label : VISUAL_NAMES[alternative] ?? alternative} instead
                    </Button>
                  </div>
                )}
              </FormAlert>
              <DialogFooter>
                <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
                <Button type="submit" disabled={form.formState.isSubmitting || options.isLoading}>{freehand ? "Add as widget" : "Pin"}</Button>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  );
}
