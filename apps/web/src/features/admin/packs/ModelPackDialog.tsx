import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import {
  createPack, getModelContents, listModelSources,
  type CreatedPack, type ModelContents, type PackKnowledgeKind, type PackLeftOut,
} from "../../../api/packs";
import { Button } from "../../../components/ui/button";
import { Checkbox } from "../../../components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Form } from "../../../components/ui/form";
import { asApiError, friendlyMessage } from "../../../lib/api-errors";
import { keyFrom } from "./CreatePackDialog";
import type { CreateDialogProps } from "./createMethods";
import { SelectInput, TextInput } from "./fields";

const KNOWLEDGE_LABELS: Record<PackKnowledgeKind, string> = {
  glossary: "Glossary", dashboard_rules: "Dashboard rules", chart_patterns: "Chart patterns",
  analysis_recipes: "Analysis recipes", value_synonyms: "Value synonyms", gold_examples: "Gold examples",
};

const NewPack = z.object({
  connection_id: z.string().min(1, "Choose the connection whose model becomes the pack."),
  pack_key: z.string().regex(/^[a-z][a-z0-9_]{1,40}$/, "Use 2 to 41 lower-case letters, digits or _, starting with a letter."),
  title: z.string().min(1, "Enter a title.").max(120, "Keep the title to 120 characters or fewer."),
});
type NewPackValues = z.infer<typeof NewPack>;

/** One ticked set of ids, with the toggle a checkbox list needs. */
function useTicks(initial: string[] = []) {
  const [ticked, setTicked] = useState<Set<string>>(new Set(initial));
  const toggle = (id: string, on: boolean) => setTicked((s) => { const n = new Set(s); if (on) n.add(id); else n.delete(id); return n; });
  return { ticked, toggle, reset: (ids: string[]) => setTicked(new Set(ids)) };
}

function CheckList({ legend, items, ticks, empty }: {
  legend: string;
  items: { id: string; label: ReactNode; hint?: string | null; disabled?: boolean }[];
  ticks: ReturnType<typeof useTicks>;
  empty: string;
}) {
  return (
    <fieldset className="grid gap-1.5">
      <legend className="mb-1 text-small font-medium">{legend}</legend>
      {items.length === 0 && <p className="text-small text-ink-muted">{empty}</p>}
      <div className="grid max-h-40 gap-1.5 overflow-y-auto">
        {items.map((i) => (
          <label key={i.id} className="flex items-start gap-2 text-small">
            <Checkbox className="mt-0.5" checked={ticks.ticked.has(i.id)} disabled={i.disabled}
                      onCheckedChange={(c) => ticks.toggle(i.id, c === true)} />
            <span>{i.label}{i.hint && <span className="block text-ink-muted">{i.hint}</span>}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function Contents({ c, measures, knowledge, reports, dashboards }: {
  c: ModelContents;
  measures: ReturnType<typeof useTicks>;
  knowledge: ReturnType<typeof useTicks>;
  reports: ReturnType<typeof useTicks>;
  dashboards: ReturnType<typeof useTicks>;
}) {
  const certified = c.measures.filter((m) => m.status === "certified").length;
  const uncertified = c.measures.filter((m) => m.status !== "certified");
  return (
    <div className="grid gap-3 rounded-md border border-line p-3">
      <p className="text-small" data-testid="model-summary">
        Model v{c.version_no ?? "?"}: {c.counts.entities} entities, {c.counts.fields} fields, {c.counts.links} accepted links, {certified} certified measures, {c.counts.dimensions} dimensions.
        {c.counts.personal_fields > 0 && ` ${c.counts.personal_fields} personal or confidential fields keep their tags.`}
        {c.counts.links_not_accepted > 0 && ` ${c.counts.links_not_accepted} links not accepted are left out.`}
      </p>
      {uncertified.length > 0 && (
        <CheckList legend="Uncertified measures (left out unless ticked)" ticks={measures} empty=""
                   items={uncertified.map((m) => ({ id: m.key, label: m.label, hint: `${m.entity} · ${m.status}` }))} />
      )}
      <CheckList legend="Knowledge" ticks={knowledge} empty="No knowledge."
                 items={c.knowledge.map((k) => ({ id: k.kind, disabled: !k.available,
                   label: `${KNOWLEDGE_LABELS[k.kind]}${k.available ? ` (${k.count})` : ""}`, hint: k.reason }))} />
      <CheckList legend="Reports (each becomes a blueprint)" ticks={reports} empty="No reports on this connection that you can read."
                 items={c.reports.map((r) => ({ id: r.id, label: r.title, hint: r.origin === "starter" ? "Starter report" : null }))} />
      <CheckList legend="Dashboards (each becomes a starter dashboard)" ticks={dashboards} empty="No dashboards on this connection that you can read."
                 items={c.dashboards.map((d) => ({ id: d.id, label: d.title, hint: `${d.tabs} ${d.tabs === 1 ? "tab" : "tabs"}` }))} />
    </div>
  );
}

function LeftOut({ items }: { items: PackLeftOut[] }) {
  return (
    <ul aria-label="Left out" className="grid max-h-64 gap-1 overflow-y-auto text-small">
      {items.map((x, i) => (
        <li key={`${x.kind}-${x.key}-${i}`}>
          <span className="font-medium">{x.key}</span> <span className="text-ink-muted">({x.kind.replace(/_/g, " ")}{x.report ? `, ${x.report}` : ""})</span>: {x.reason}
        </li>
      ))}
    </ul>
  );
}

/** Create → From an approved model (US-236): the model's entities, fields, links, certified measures and
 * dimensions, its approved knowledge and the chosen reports and dashboards become a pack draft. */
export function ModelPackDialog({ open, onOpenChange, onCreated }: CreateDialogProps) {
  const form = useForm<NewPackValues>({ resolver: zodResolver(NewPack), defaultValues: { connection_id: "", pack_key: "", title: "" } });
  const sources = useQuery({ queryKey: ["packs", "from-model"], queryFn: listModelSources, enabled: open });
  const connectionId = form.watch("connection_id");
  const contents = useQuery({ queryKey: ["packs", "from-model", connectionId], queryFn: () => getModelContents(connectionId), enabled: open && !!connectionId });
  const measures = useTicks();
  const knowledge = useTicks();
  const reports = useTicks();
  const dashboards = useTicks();
  const [made, setMade] = useState<CreatedPack | null>(null);

  useEffect(() => {
    if (open) { form.reset({ connection_id: "", pack_key: "", title: "" }); setMade(null); }
  }, [open, form]);
  useEffect(() => { // a newly chosen model: uncertified off, every available knowledge kind on, nothing chosen
    const c = contents.data;
    measures.reset([]);
    knowledge.reset(c ? c.knowledge.filter((k) => k.available).map((k) => k.kind) : []);
    reports.reset([]);
    dashboards.reset([]);
    if (c && !form.getValues("title")) {
      form.setValue("title", `${c.name} pack`);
      form.setValue("pack_key", keyFrom(`${c.name} pack`));
    }
  }, [contents.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const create = useMutation({
    mutationFn: (v: NewPackValues) => createPack({
      method: "model", pack_key: v.pack_key, title: v.title,
      options: { connection_id: v.connection_id, measures: [...measures.ticked], knowledge: [...knowledge.ticked],
                 report_ids: [...reports.ticked], dashboard_ids: [...dashboards.ticked] },
    }),
    onSuccess: (out) => { if (out.left_out?.length) setMade(out); else onCreated(out.id); },
    onError: (e) => {
      const err = asApiError(e, "The pack could not be created.");
      if ((err.details as { field?: string }).field === "pack_key") form.setError("pack_key", { message: err.message });
      else form.setError("root", { message: friendlyMessage(err) });
    },
  });

  const items = sources.data?.items ?? [];
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{made ? `${made.title} is ready` : "New pack from an approved model"}</DialogTitle>
          <DialogDescription>
            {made ? "The draft was created. These parts of the model could not go into it:"
                  : "The model's entities, fields (source names as candidates), accepted links, certified measures and dimensions, its approved knowledge and the reports and dashboards you choose become a pack draft."}
          </DialogDescription>
        </DialogHeader>
        {made ? (
          <>
            <LeftOut items={made.left_out ?? []} />
            <DialogFooter><Button onClick={() => onCreated(made.id)}>Open the pack</Button></DialogFooter>
          </>
        ) : (
          <Form {...form}>
            <form className="grid gap-3" noValidate onSubmit={form.handleSubmit((v) => create.mutate(v))}>
              {sources.isSuccess && items.length === 0
                ? <p role="status" className="text-small text-ink-muted">No connection has an approved model yet. Approve a model under Data first.</p>
                : <SelectInput form={form} name="connection_id" label="Approved model" allowEmpty="Choose a connection"
                               options={items.map((m) => [m.connection_id, `${m.name} — model v${m.version_no ?? "?"} (${m.counts.measures} measures)`] as [string, string])} />}
              {contents.isError && <p role="alert" className="text-small text-status-danger">{friendlyMessage(asApiError(contents.error, "The model could not be read."))}</p>}
              {contents.data && <Contents c={contents.data} measures={measures} knowledge={knowledge} reports={reports} dashboards={dashboards} />}
              <TextInput form={form} name="title" label="Title" />
              <TextInput form={form} name="pack_key" label="Key" description="Names the pack everywhere and can't change later." />
              {form.formState.errors.root && <p role="alert" className="text-small text-status-danger">{form.formState.errors.root.message}</p>}
              <DialogFooter>
                <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
                <Button type="submit" disabled={create.isPending || !contents.data}>Create pack</Button>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  );
}
