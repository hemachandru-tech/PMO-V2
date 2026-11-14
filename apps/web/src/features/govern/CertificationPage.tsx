import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, CircleAlert, CircleCheck, Info, OctagonAlert } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { api, type ApiError } from "../../api/client";
import { KIND_LABEL, type CertDetail, type CertKind, type LookAlike, type QueueItem } from "../../api/govern";
import { DecisionIn } from "../../api/schemas";
import { ResultTable } from "../../components/ResultTable";
import { ContentBadge } from "../../components/ContentBadge";
import { formatAsOf, formatRelative, formatValue } from "../../components/format";
import { Alert, FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { DataTable, type DataColumn } from "../../components/ui/data-table";
import { EmptyState } from "../../components/ui/empty-state";
import { Field, Form, FormControl, FormField, FormItem, FormLabel, FormMessage } from "../../components/ui/form";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { NativeSelect } from "../../components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { Skeleton } from "../../components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { Textarea } from "../../components/ui/textarea";
import { applyServerError, asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";

const STATUSES = [["pending", "Waiting"], ["certified", "Certified"], ["merged", "Merged"], ["rejected", "Rejected"], ["changes_requested", "Changes asked"]] as const;

/**
 * Certification (FEAT-033 – 037; SCR-07; US-087): the queue of personal fields and custom SQL waiting for a decision
 * (blocks-a-publish first, then oldest), and the selected item's evidence — rule in words, results across the
 * reviewer's audited certification scope, where it is used, generated SQL, dependencies, look-alikes and semantic
 * checks — with the decision: Certify, Merge into a certified field, or Reject with a note.
 */
export function CertificationPage() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "pending";
  const selected = params.get("r");
  const [kind, setKind] = useState("");
  // In the URL so the Admin overview can link straight to the requests blocking a publish (US-143).
  const blocks = params.get("blocks") === "1";
  const setBlocks = (on: boolean) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (on) next.set("blocks", "1"); else next.delete("blocks");
    next.delete("r");
    return next;
  });
  const queue = useQuery({
    queryKey: ["cert-queue", status, kind, blocks],
    queryFn: () => api<{ items: QueueItem[] }>(`/api/v1/certification/queue?${new URLSearchParams({
      status, ...(kind ? { kind } : {}), ...(blocks ? { blocks_publish: "true" } : {}),
    }).toString()}`),
  });
  const items = useMemo(() => queue.data?.items ?? [], [queue.data]);
  const select = (id: string | null) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (id) next.set("r", id); else next.delete("r");
    return next;
  });
  useEffect(() => {
    if (!selected && items[0]) select(items[0].request_id);
  }, [items, selected]); // eslint-disable-line react-hooks/exhaustive-deps

  const columns = useMemo<DataColumn<QueueItem>[]>(() => [
    {
      id: "name", header: "Item", alwaysVisible: true, value: (i) => `${i.name} ${i.author.name} ${KIND_LABEL[i.kind]}`,
      cell: (i) => (
        <span className="grid gap-0.5">
          <span className="font-medium text-ink">{i.name}</span>
          <span className="text-small text-ink-muted">{KIND_LABEL[i.kind]} · {i.author.name}</span>
        </span>
      ),
    },
    { id: "age", header: "Age", numeric: true, value: (i) => i.age_days, cell: (i) => (i.age_days === 0 ? "today" : `${i.age_days} d`) },
    {
      id: "flags", header: "Flags", sortable: false, value: (i) => [i.blocks_publish && "blocks", i.lookalikes && "similar"].filter(Boolean).join(" "),
      cell: (i) => (
        <span className="flex flex-wrap gap-1">
          {i.blocks_publish && <Badge variant="stale" title={i.pending_publishes.join(", ") || i.reason}>Blocks a publish</Badge>}
          {i.lookalikes > 0 && <Badge variant="personal">Looks similar</Badge>}
          {i.dependency_pending && <Badge variant="outline">Waits on another</Badge>}
          {i.changed_since_requested && <Badge variant="outline">Changed</Badge>}
        </span>
      ),
    },
  ], []);

  return (
    <div className="tl grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-title">Certification</h1>
        <p className="text-ink-muted">Personal fields and custom SQL that people want on published dashboards. Certified definitions become official for everyone.</p>
      </div>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <section aria-label="Queue" className="grid content-start gap-3">
          <Tabs value={status} onValueChange={(v) => setParams({ status: v })} className="grid gap-3">
            <TabsList aria-label="Status">
              {STATUSES.map(([v, label]) => <TabsTrigger key={v} value={v}>{label}</TabsTrigger>)}
            </TabsList>
            {/* The filtered queue is the panel of the selected tab. */}
            <TabsContent value={status} className="grid gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-small">
                  <span className="text-ink-muted">Type</span>
                  <NativeSelect value={kind} onChange={(e) => setKind(e.target.value)} className="h-control-sm w-auto" aria-label="Type">
                    <option value="">All types</option>
                    {(Object.keys(KIND_LABEL) as CertKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
                  </NativeSelect>
                </label>
                <label className="flex items-center gap-2 text-small">
                  <Checkbox checked={blocks} onCheckedChange={(on) => setBlocks(on === true)} />
                  Blocks a publish
                </label>
              </div>
              <DataTable
                data={items}
                columns={columns}
                label="Certification queue"
                getRowId={(i) => i.request_id}
                loading={queue.isLoading}
                error={queue.error && <FormAlert error={asApiError(queue.error)} />}
                empty={{ title: status === "pending" ? "Nothing is waiting" : "Nothing here yet", body: status === "pending" ? "Requests appear when a Creator shares or publishes a personal field or custom SQL widget." : undefined }}
                search={items.length > 8}
                compact
                onRowActivate={(i) => select(i.request_id)}
                rowClassName={(i) => (i.request_id === selected ? "bg-accent-soft" : undefined)}
              />
            </TabsContent>
          </Tabs>
        </section>
        <section aria-label="Selected item" className="min-w-0">
          {selected ? <Detail key={selected} requestId={selected} onDecided={() => select(null)} />
            : !queue.isLoading && <EmptyState icon={BadgeCheck} title="Choose an item">Pick a request from the queue to see its evidence and decide.</EmptyState>}
        </section>
      </div>
    </div>
  );
}

function Detail({ requestId, onDecided }: { requestId: string; onDecided: () => void }) {
  const d = useQuery({ queryKey: ["cert-detail", requestId], queryFn: () => api<CertDetail>(`/api/v1/certification/requests/${requestId}`) });
  if (d.isLoading) return <div className="grid gap-4" aria-busy="true"><Skeleton className="h-40" /><Skeleton className="h-64" /></div>;
  if (d.error) return <FormAlert error={asApiError(d.error)} />;
  const c = d.data!;
  return (
    <div className="grid gap-4">
      <Card className="gap-3 p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="grid gap-1">
            <span className="text-label uppercase text-ink-muted">{KIND_LABEL[c.kind]} · requested by {c.author.name} · {formatRelative(c.submitted_at)}</span>
            <h2 className="text-title">{c.name}</h2>
          </div>
          <ContentBadge badge={c.kind === "custom_sql" ? "custom_sql" : c.status === "certified" ? "certified" : c.kind === "freehand" ? "pending_certification" : "personal"} />
        </div>
        {c.changed_since_requested && <Alert variant="warn"><p>The author changed this definition after requesting certification. Review the new version below.</p></Alert>}
        <p>{c.rule.plain}</p>
        {c.rule.rephrased && <p className="text-ink-muted"><span className="text-small font-medium">In other words (advisory): </span>{c.rule.rephrased}</p>}
        <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-1.5 text-body">
          {c.question && <Fact term="Question">“{c.question}”</Fact>}
          <Fact term="Blank values">{c.rule.blank_handling}</Fact>
          <Fact term="Why it's here">{c.kind === "freehand" ? "A free-hand answer added as a widget; it stays in the author's library until certified"
            : { publish_bound: "On a dashboard sent for publishing", shared: "Shared with colleagues", manual: "Requested by the author" }[c.trigger] ?? c.trigger}</Fact>
          <Fact term="Used in">{usageText(c.usage)}</Fact>
          {c.usage.dashboards.length > 0 && <Fact term="Dashboards">{c.usage.dashboards.map((x) => `${x.title}${x.live ? " (live)" : " (waiting)"}`).join(", ")}</Fact>}
          {c.note && <Fact term={`Note from ${c.author.name.split(" ")[0]}`}>“{c.note}”</Fact>}
          {c.dependencies.length > 0 && <Fact term="Depends on">{c.dependencies.map((x) => `${x.name} (${x.status})`).join(", ")}</Fact>}
        </dl>
        <DefinitionDetail c={c} />
      </Card>

      <Card className="gap-3 p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-heading">Results</h3>
          <span className="text-small text-ink-muted">{c.results_scope}{c.results.as_of ? ` · data as of ${formatAsOf(c.results.as_of)}` : ""}</span>
        </div>
        <Results c={c} />
        {c.results.sample?.rows && c.results.sample.columns && (
          <div className="grid gap-1">
            <h4 className="text-small font-medium text-ink-muted">Sample of records ({c.results.sample.rows.length} of {c.results.sample.total?.toLocaleString() ?? "?"})</h4>
            <ResultTable columns={c.results.sample.columns} rows={c.results.sample.rows} caption={`${c.name}: sample of records`} />
          </div>
        )}
        {c.results.sample?.error && <p className="text-small text-ink-muted">{c.results.sample.error}</p>}
        {c.generated && (
          <details className="grid gap-2">
            <summary className="cursor-pointer text-small text-link">{c.kind === "custom_sql" ? "The SQL" : "Generated SQL"}</summary>
            <pre className="max-h-64 overflow-auto rounded-sm bg-surface-sunken p-3 font-mono text-code">{c.generated}</pre>
          </details>
        )}
      </Card>

      {(c.lookalikes.length > 0 || c.lookalike_note) && <LookAlikes c={c} />}
      {c.semantic_checks.length > 0 && <SemanticChecks c={c} />}
      {c.status === "pending" ? (c.kind === "freehand" ? <FreehandDecision c={c} onDecided={onDecided} /> : <Decision c={c} onDecided={onDecided} />) : (
        <Alert><p>Decided: {STATUSES.find(([v]) => v === c.status)?.[1] ?? c.status}.</p></Alert>
      )}
    </div>
  );
}

function Fact({ term, children }: { term: ReactNode; children: ReactNode }) {
  return <><dt className="text-ink-muted">{term}</dt><dd className="min-w-0 break-words">{children}</dd></>;
}

function usageText(u: CertDetail["usage"]) {
  const parts = [`${u.widgets.length} ${u.widgets.length === 1 ? "widget" : "widgets"}`, `${u.reports.length} ${u.reports.length === 1 ? "report" : "reports"}`,
    `${u.dashboards.length} ${u.dashboards.length === 1 ? "dashboard" : "dashboards"}`];
  return `${parts.join(", ")}${u.shared ? "; shared with colleagues" : ""}`;
}

function DefinitionDetail({ c }: { c: CertDetail }) {
  const def = c.definition.definition as Record<string, unknown> | undefined;
  if (c.kind === "grouping" && def) {
    const groups = (def.groups as { label: string; values: unknown[] }[]) ?? [];
    return (
      <ul className="grid gap-1 text-body" aria-label="Groups">
        {groups.map((g) => <li key={g.label}><span className="font-medium">{g.label}</span>: <span className="text-ink-muted">{g.values.map(String).join(", ")}</span></li>)}
        <li className="text-ink-muted">Everything else → {String(def.catch_all ?? "Everything else")}; blank → {String(def.not_set ?? "Not set")}</li>
      </ul>
    );
  }
  if (c.kind === "band" && def) {
    const bands = (def.bands as { label: string; lower: number; upper: number | null }[]) ?? [];
    return (
      <ul className="flex flex-wrap gap-1.5" aria-label="Bands">
        {bands.map((b) => <li key={b.label}><Badge variant="outline">{b.label}: {formatValue(b.lower)}{b.upper === null ? "+" : ` – ${formatValue(b.upper)}`}</Badge></li>)}
      </ul>
    );
  }
  return null;
}

function Results({ c }: { c: CertDetail }) {
  const r = c.results;
  if (r.error) return <Alert variant="warn"><p>{r.error}</p></Alert>;
  if (r.columns && r.rows) {
    return (
      <>
        <ResultTable columns={r.columns} rows={r.rows} caption={`${c.name} results`} />
        {r.truncated && <p className="text-small text-ink-muted">Showing the first rows of {r.row_count?.toLocaleString()}.</p>}
      </>
    );
  }
  if (r.buckets) {
    return (
      <ul className="grid gap-1" aria-label="Counts per bucket">
        {r.buckets.map((b) => (
          <li key={b.label} className="flex justify-between gap-4 border-b border-line py-1 last:border-0">
            <span>{b.label}</span><span className="font-mono tabular-nums">{formatValue(b.count)}</span>
          </li>
        ))}
        {r.measure && <li className="pt-1 text-small text-ink-muted">Counted with {r.measure}.</li>}
      </ul>
    );
  }
  if (r.value !== undefined) {
    return (
      <div className="grid gap-1">
        <p className="font-mono text-figure-xl tabular-nums">{formatValue(r.value)}</p>
        {r.semantics && <p className="text-ink-muted">{r.semantics}</p>}
      </div>
    );
  }
  return <p className="text-ink-muted">No results.</p>;
}

function LookAlikes({ c }: { c: CertDetail }) {
  return (
    <Card className="gap-3 p-5" role="region" aria-label="Look-alikes">
      <div className="grid gap-0.5">
        <h3 className="text-heading">Looks like a certified field</h3>
        <p className="text-small text-ink-muted">Two certified answers to one question must not co-exist. Compare, then certify, merge or reject.</p>
      </div>
      {c.lookalike_note && <p className="text-small text-ink-muted">{c.lookalike_note}</p>}
      {c.lookalikes.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-body">
            <thead className="text-left text-small text-ink-muted">
              <tr><th className="py-1 pr-3 font-medium">Certified field</th><th className="py-1 pr-3 font-medium">Why similar</th><th className="py-1 pr-3 font-medium">Difference</th><th className="py-1 text-right font-medium">This vs certified</th></tr>
            </thead>
            <tbody>
              {c.lookalikes.map((l: LookAlike) => (
                <tr key={l.ref} className="border-t border-line align-top">
                  <td className="py-2 pr-3"><span className="font-medium">{l.ref.split(":")[1]}</span><br /><span className="text-small text-ink-muted">{Math.round(l.similarity * 100)}% similar</span></td>
                  <td className="py-2 pr-3 text-small">{l.reasons.join("; ")}</td>
                  <td className="py-2 pr-3">{l.difference}</td>
                  <td className="py-2 text-right font-mono tabular-nums">
                    {l.result_delta ? <>{formatValue(l.result_delta.item)} vs {formatValue(l.result_delta.certified)}{l.result_delta.differs && <Badge variant="stale" className="ml-1">Differs</Badge>}</> : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

const CHECK_ICON = {
  matches: [CircleCheck, "text-status-ok", "Matches"],
  differs: [CircleAlert, "text-status-warn", "Differs"],
  review_manually: [Info, "text-ink-muted", "Review manually"],
  info: [Info, "text-ink-muted", "Note"],
  fails: [OctagonAlert, "text-status-danger", "Fails"],
} as const;

function SemanticChecks({ c }: { c: CertDetail }) {
  return (
    <Card className="gap-3 p-5" role="region" aria-label="Semantic checks">
      <h3 className="text-heading">Compared with certified measures</h3>
      <ul className="grid gap-2">
        {c.semantic_checks.map((s) => {
          const [Icon, tone, word] = CHECK_ICON[s.status] ?? CHECK_ICON.info;
          return (
            <li key={s.check} className="flex items-start gap-2">
              <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", tone)} />
              <span><span className="font-medium">{word}.</span> {s.detail}
                {typeof s.row_difference === "number" && <span className="text-ink-muted"> Row difference: {formatValue(s.row_difference)}.</span>}
              </span>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

const decisionForm = z.object({
  decision: z.enum(["certify", "merge", "reject"]),
  merge_into: z.string(),
  note: z.string().max(1000, "Keep the note to 1,000 characters or fewer."),
  reviewed_changes: z.boolean(),
}).superRefine((v, ctx) => {
  if (v.decision === "reject" && !v.note.trim()) ctx.addIssue({ code: "custom", path: ["note"], message: "Say why it is rejected; the author will see the note." });
  if (v.decision === "merge" && !v.merge_into) ctx.addIssue({ code: "custom", path: ["merge_into"], message: "Choose the certified field to merge into." });
});
type DecisionForm = z.infer<typeof decisionForm>;

function Decision({ c, onDecided }: { c: CertDetail; onDecided: () => void }) {
  const qc = useQueryClient();
  const targets = c.lookalikes.filter((l) => l.ref.startsWith(c.kind === "grouping" || c.kind === "band" ? "dimension:" : "measure:"));
  const form = useForm<DecisionForm>({
    resolver: zodResolver(decisionForm),
    defaultValues: { decision: "certify", merge_into: targets[0]?.ref ?? "", note: "", reviewed_changes: false },
    mode: "onTouched",
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [confirmDifferent, setConfirmDifferent] = useState(false);
  const [key] = useState(() => crypto.randomUUID());
  const decision = form.watch("decision");
  const author = c.author.name.split(" ")[0];
  const send = useMutation({
    mutationFn: (v: DecisionForm & { confirm: boolean }) => api<{ status: string; widgets_changed: string[] }>(
      `/api/v1/certification/requests/${c.request_id}/decision`, {
        method: "POST",
        idempotencyKey: `${key}-${v.decision}-${v.confirm}`,
        body: {
          decision: v.decision, note: v.note.trim() || null, merge_into: v.decision === "merge" ? v.merge_into : null,
          confirm_different: v.confirm, reviewed_changes: v.reviewed_changes,
        } satisfies z.input<typeof DecisionIn>,
      }),
    onSuccess: (out, v) => {
      toast.success({ certify: `${c.name} is certified`, merge: `${c.name} was merged`, reject: `${c.name} was not certified` }[v.decision], {
        description: out.widgets_changed.length ? `Widgets changed: ${out.widgets_changed.join(", ")}.` : `${author} has been notified.`,
      });
      void qc.invalidateQueries({ queryKey: ["cert-queue"] });
      onDecided();
    },
  });
  const submit = async (v: DecisionForm) => {
    setError(null);
    try {
      await send.mutateAsync({ ...v, confirm: confirmDifferent });
    } catch (e) {
      const err = applyServerError(form, e, { fields: { merge_into: "merge_into", note: "note" } });
      if (err?.details.result_delta) setConfirmDifferent(true);
      setError(err);
    }
  };
  const blocked = !c.can_decide.allowed;
  const verb = { certify: "Certify", merge: "Merge", reject: "Reject" }[decision];
  return (
    <Card className="gap-4 p-5" role="region" aria-label="Decision">
      <h3 className="text-heading">Decision</h3>
      {blocked && <Alert variant="warn"><p>{c.can_decide.reason}</p></Alert>}
      <Form {...form}>
        <form className="grid gap-4" onSubmit={form.handleSubmit(submit)} noValidate>
          <FormField control={form.control} name="decision" render={({ field }) => (
            <FormItem>
              <FormControl>
                <RadioGroup aria-label="Decision" value={field.value} onValueChange={(v) => { field.onChange(v); setConfirmDifferent(false); setError(null); }} disabled={blocked} className="gap-3">
                  <Choice value="certify" title="Certify as a new field" help="Becomes official and available on role dashboards; badges update everywhere." />
                  <Choice value="merge" title="Merge into a certified field" disabled={targets.length === 0}
                    help={targets.length ? `${author}'s widgets switch to the certified field.` : "No similar certified field was found."} />
                  <Choice value="reject" title="Reject" help={`Stays personal and can't be published; ${author} sees your note.`} />
                </RadioGroup>
              </FormControl>
            </FormItem>
          )} />
          {decision === "merge" && targets.length > 0 && (
            <Field control={form.control} name="merge_into" label="Merge into" render={(f) => (
              <NativeSelect {...f}>{targets.map((t) => <option key={t.ref} value={t.ref}>{t.ref.split(":")[1]} — {t.difference}</option>)}</NativeSelect>
            )} />
          )}
          <Field control={form.control} name="note" label={`Note to ${author}${decision === "reject" ? "" : " (optional)"}`}
            render={(f) => <Textarea {...f} rows={3} maxLength={1000} disabled={blocked} />} />
          {c.changed_since_requested && (
            <FormField control={form.control} name="reviewed_changes" render={({ field }) => (
              <FormItem>
                <div className="flex items-center gap-2">
                  <FormControl><Checkbox checked={field.value} onCheckedChange={(on) => field.onChange(on === true)} disabled={blocked} /></FormControl>
                  <FormLabel className="font-normal">I reviewed the changes made since the request</FormLabel>
                </div>
                <FormMessage />
              </FormItem>
            )} />
          )}
          <FormAlert error={error}>
            {confirmDifferent && <p className="text-small">Submit again to merge anyway.</p>}
          </FormAlert>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant={decision === "reject" ? "danger" : "primary"} disabled={blocked || send.isPending}>
              {confirmDifferent && decision === "merge" ? "Merge anyway" : `${verb} and notify ${author}`}
            </Button>
          </div>
        </form>
      </Form>
    </Card>
  );
}

const freehandForm = z.object({
  decision: z.enum(["certify", "request_changes", "reject"]),
  label: z.string().max(120, "Keep the name to 120 characters or fewer."),
  key: z.string(),
  definition: z.string().max(300, "Keep the definition to 300 characters or fewer."),
  plain_rule: z.string().max(500, "Keep the rule to 500 characters or fewer."),
  note: z.string().max(1000, "Keep the note to 1,000 characters or fewer."),
  use_instead: z.string(),
}).superRefine((v, ctx) => {
  if (v.decision === "certify") {
    if (v.label.trim().length < 2) ctx.addIssue({ code: "custom", path: ["label"], message: "Give the measure a name." });
    if (!/^[a-z][a-z0-9_]{1,62}$/.test(v.key)) ctx.addIssue({ code: "custom", path: ["key"], message: "Use lower-case letters, digits and underscores, starting with a letter." });
    if (v.definition.trim().length < 3) ctx.addIssue({ code: "custom", path: ["definition"], message: "Say in one line what it shows." });
    if (v.plain_rule.trim().length < 3) ctx.addIssue({ code: "custom", path: ["plain_rule"], message: "Write the rule in plain words." });
  }
  if (v.decision !== "certify" && !v.note.trim()) {
    ctx.addIssue({ code: "custom", path: ["note"], message: v.decision === "reject" ? "Say why it is rejected; the author will see the note." : "Say what should change; the author will see the note." });
  }
});
type FreehandForm = z.infer<typeof freehandForm>;

function keyOf(label: string) {
  const k = label.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^(\d)/, "m_$1");
  return k.slice(0, 63);
}

/**
 * The decision on a free-hand rule (US-166): Approve names the certified measure it becomes (never a certified
 * measure's name, D1); the model gets a new version, the question becomes a synonym and the widget is certified (D2).
 * Ask for changes and Reject need a note; Reject may point to an existing measure.
 */
function FreehandDecision({ c, onDecided }: { c: CertDetail; onDecided: () => void }) {
  const qc = useQueryClient();
  const measures = c.lookalikes.filter((l) => l.ref.startsWith("measure:"));
  const form = useForm<FreehandForm>({
    resolver: zodResolver(freehandForm),
    defaultValues: { decision: "certify", label: c.name, key: keyOf(c.name), definition: "", plain_rule: c.rule.plain, note: "", use_instead: "" },
    mode: "onTouched",
  });
  const [error, setError] = useState<ApiError | null>(null);
  const [key] = useState(() => crypto.randomUUID());
  const [keyTouched, setKeyTouched] = useState(false);
  const decision = form.watch("decision");
  const author = c.author.name.split(" ")[0];
  const send = useMutation({
    mutationFn: (v: FreehandForm) => api<{ status: string; widgets_changed: string[]; model_version?: number }>(
      `/api/v1/certification/requests/${c.request_id}/decision`, {
        method: "POST",
        idempotencyKey: `${key}-${v.decision}`,
        body: {
          decision: v.decision, note: v.note.trim() || null,
          merge_into: v.decision === "reject" && v.use_instead ? v.use_instead : null,
          measure: v.decision === "certify" ? { key: v.key, label: v.label.trim(), definition: v.definition.trim(), plain_rule: v.plain_rule.trim() } : null,
        } satisfies z.input<typeof DecisionIn>,
      }),
    onSuccess: (out, v) => {
      toast.success({ certify: `${v.label.trim()} is certified`, request_changes: `Changes asked of ${author}`, reject: `${c.name} was not certified` }[v.decision], {
        description: v.decision === "certify" && out.model_version ? `Added to the model as version ${out.model_version}. ${author} has been notified.` : `${author} has been notified.`,
      });
      void qc.invalidateQueries({ queryKey: ["cert-queue"] });
      onDecided();
    },
  });
  const submit = async (v: FreehandForm) => {
    setError(null);
    try {
      await send.mutateAsync(v);
    } catch (e) {
      setError(applyServerError(form, e, { fields: { "measure.label": "label", "measure.key": "key", "measure.definition": "definition", "measure.plain_rule": "plain_rule", note: "note" } }));
    }
  };
  const blocked = !c.can_decide.allowed;
  const verb = { certify: "Approve", request_changes: "Ask for changes", reject: "Reject" }[decision];
  const fc = form.control;
  return (
    <Card className="gap-4 p-5" role="region" aria-label="Decision">
      <h3 className="text-heading">Decision</h3>
      {blocked && <Alert variant="warn"><p>{c.can_decide.reason}</p></Alert>}
      <Form {...form}>
        <form className="grid gap-4" onSubmit={form.handleSubmit(submit)} noValidate>
          <FormField control={fc} name="decision" render={({ field }) => (
            <FormItem>
              <FormControl>
                <RadioGroup aria-label="Decision" value={field.value} onValueChange={(v) => { field.onChange(v); setError(null); }} disabled={blocked} className="gap-3">
                  <Choice value="certify" title="Approve as a certified measure" help="Adds the measure to the model as a new version; everyone's Ask can use it, and the widget is certified." />
                  <Choice value="request_changes" title="Ask for changes" help={`The widget stays in ${author}'s library; ${author} sees your note and can ask again.`} />
                  <Choice value="reject" title="Reject" help={`The widget stays out of reports; you can point ${author} to an existing measure.`} />
                </RadioGroup>
              </FormControl>
            </FormItem>
          )} />
          {decision === "certify" && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field control={fc} name="label" label="Measure name" render={(f) => (
                <Input {...f} maxLength={120} disabled={blocked} onChange={(e) => { f.onChange(e); if (!keyTouched) form.setValue("key", keyOf(e.target.value)); }} />
              )} />
              <Field control={fc} name="key" label="Key" render={(f) => (
                <Input {...f} maxLength={63} disabled={blocked} className="font-mono" onChange={(e) => { setKeyTouched(true); f.onChange(e); }} />
              )} />
              <Field control={fc} name="definition" label="Definition (one line shown with the figure)" className="sm:col-span-2"
                render={(f) => <Input {...f} maxLength={300} disabled={blocked} />} />
              <Field control={fc} name="plain_rule" label="Rule in plain words" className="sm:col-span-2"
                render={(f) => <Textarea {...f} rows={2} maxLength={500} disabled={blocked} />} />
              {measures.length > 0 && (
                <p className="text-small text-ink-muted sm:col-span-2">Don't reuse a certified name: {measures.map((m) => m.label ?? m.ref.split(":")[1]).join(", ")} already exist.</p>
              )}
            </div>
          )}
          {decision === "reject" && measures.length > 0 && (
            <Field control={fc} name="use_instead" label="Point to an existing measure (optional)" render={(f) => (
              <NativeSelect {...f}>
                <option value="">None</option>
                {measures.map((m) => <option key={m.ref} value={m.ref}>{m.label ?? m.ref.split(":")[1]}</option>)}
              </NativeSelect>
            )} />
          )}
          <Field control={fc} name="note" label={`Note to ${author}${decision === "certify" ? " (optional)" : ""}`}
            render={(f) => <Textarea {...f} rows={3} maxLength={1000} disabled={blocked} />} />
          <FormAlert error={error} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant={decision === "reject" ? "danger" : "primary"} disabled={blocked || send.isPending}>{`${verb} and notify ${author}`}</Button>
          </div>
        </form>
      </Form>
    </Card>
  );
}

function Choice({ value, title, help, disabled }: { value: string; title: string; help: string; disabled?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <RadioGroupItem id={`decision-${value}`} value={value} disabled={disabled} aria-describedby={`decision-${value}-help`} className="mt-0.5" />
      <Label htmlFor={`decision-${value}`} className={cn("grid gap-0.5 font-normal", disabled && "text-ink-disabled")}>
        <span className="text-body-strong">{title}</span>
        <span id={`decision-${value}-help`} className="text-small text-ink-muted">{help}</span>
      </Label>
    </div>
  );
}
