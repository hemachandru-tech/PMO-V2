import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { useForm, type FieldValues, type Path, type Resolver, type UseFormReturn } from "react-hook-form";
import { toast } from "sonner";
import type { z } from "zod";

import { ApiError } from "../../../../api/client";
import {
  checkKnowledge, knowledgeKey, saveKnowledge,
  type KnowledgeChecks, type KnowledgeDocument, type KnowledgeItemCheck, type KnowledgeKindView, type KnowledgeProblem,
} from "../../../../api/packKnowledge";
import { packKey } from "../PackContext";
import { Alert } from "../../../../components/ui/alert";
import { Badge } from "../../../../components/ui/badge";
import { Button } from "../../../../components/ui/button";
import { DataTable, type DataColumn } from "../../../../components/ui/data-table";
import { Form } from "../../../../components/ui/form";
import { Label } from "../../../../components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../../components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../../../components/ui/tabs";
import { Textarea } from "../../../../components/ui/textarea";
import { asApiError, friendlyMessage } from "../../../../lib/api-errors";
import { fromYaml, toYaml } from "../yaml";

type Row<T> = T & { _index: number };

const blankValue = (v: unknown) => v === undefined || v === null || v === "" || v === false || (Array.isArray(v) && v.length === 0);

/** Drops empty optional values (and an empty `when`) so a saved item reads like a hand-written one. */
export function tidy<T extends Record<string, unknown>>(item: T): T {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(item)) {
    if (blankValue(v)) continue;
    if (k === "when" && v && typeof v === "object" && !Array.isArray(v)) {
      const when = tidy(v as Record<string, unknown>);
      if (Object.keys(when).length) out[k] = when;
      continue;
    }
    out[k] = v;
  }
  return out as T;
}

/** The document with the list at `at` read or replaced. */
export function listAt(doc: KnowledgeDocument, at: string[]): unknown[] {
  let cur: unknown = doc;
  for (const k of at) cur = cur && typeof cur === "object" ? (cur as Record<string, unknown>)[k] : undefined;
  return Array.isArray(cur) ? cur : [];
}

export function withList(doc: KnowledgeDocument, at: string[], items: unknown[]): KnowledgeDocument {
  const [head, ...rest] = at;
  if (!head) return doc;
  if (rest.length === 0) return { ...doc, [head]: items };
  const inner = (doc[head] && typeof doc[head] === "object" ? doc[head] : {}) as KnowledgeDocument;
  return { ...doc, [head]: withList(inner, rest, items) };
}

const under = (path: string, head: string) => path === head || path.startsWith(`${head}.`) || path.startsWith(`${head}[`);

/** The API's problems about item `index` of the list at `prefix` (`glossary.terms`), with the item's own form path. */
export function problemsOf(problems: KnowledgeProblem[], prefix: string, index: number): KnowledgeProblem[] {
  const head = `${prefix}[${index}]`;
  return problems.filter((p) => under(p.path, head))
    .map((p) => ({ path: p.path.slice(head.length).replace(/^\./, "").replace(/\[(\d+)\]/g, ".$1"), message: p.message }));
}

/** Checks with the Zod schema but keeps every key of the item (the form shows only some). */
function keepAll<T extends FieldValues>(schema: z.ZodType<T, FieldValues>): Resolver<T> {
  const check = zodResolver(schema as never) as unknown as Resolver<T>;
  return async (values, ctx, opts) => {
    const out = await check(values, ctx, opts);
    return Object.keys(out.errors).length ? out : { values, errors: {} };
  };
}

/** Saves a whole knowledge document on the revision it was loaded at; the cached view, overview and pack follow. */
export function useSaveKnowledge(view: KnowledgeKindView) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (doc: KnowledgeDocument) => saveKnowledge(view.pack.id, view.kind, view.etag, doc),
    onSuccess: (out) => {
      qc.setQueryData(knowledgeKey(view.pack.id, view.kind), out);
      void qc.invalidateQueries({ queryKey: knowledgeKey(view.pack.id), exact: true });
      void qc.invalidateQueries({ queryKey: packKey(view.pack.id) });
    },
  });
}

/** What an item names: measures, dimensions and fields, the missing ones flagged. */
export function RefList({ check }: { check: KnowledgeItemCheck | undefined }) {
  if (!check) return null;
  const all = [
    ...check.refs.measures.map((k) => ["measure", k] as const),
    ...check.refs.dimensions.map((k) => ["dimension", k] as const),
    ...check.refs.fields.map((k) => ["field", k] as const),
  ];
  if (all.length === 0) return <span className="text-ink-muted">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {all.map(([what, k]) => {
        const missing = check.missing.includes(`${what}:${k}`);
        return (
          <Badge key={`${what}:${k}`} variant={missing ? "stale" : "outline"} title={missing ? `The pack has no ${what} ${k}` : `${what} ${k}`}>
            <span className="font-mono">{k}</span>{missing && <span className="sr-only"> (missing {what})</span>}
          </Badge>
        );
      })}
    </span>
  );
}

function Checks({ errors, warnings }: { errors: number; warnings: number }) {
  if (errors) return <Badge variant="stale">{errors === 1 ? "1 error" : `${errors} errors`}</Badge>;
  if (warnings) return <Badge variant="stale">{warnings === 1 ? "1 missing name" : `${warnings} missing names`}</Badge>;
  return <Badge variant="certified">OK</Badge>;
}

export interface ListEditorProps<T extends FieldValues> {
  view: KnowledgeKindView;
  /** Where the items live in the document: `["terms"]`, `["wording", "forbidden"]`. */
  at: string[];
  /** "term" / "terms". */
  noun: [string, string];
  schema: z.ZodType<T, FieldValues>;
  blank: () => T;
  title: (item: T) => string;
  renderForm: (form: UseFormReturn<T>, readOnly: boolean) => ReactNode;
  columns?: DataColumn<Row<T>>[];
  intro?: ReactNode;
}

/**
 * One list of a pack knowledge document (US-233) as a table with a side panel, like the manifest sections (US-232):
 * each row shows what the item names and its checks; the panel edits it in a form (React Hook Form + Zod) or as YAML,
 * the same text as in the pack's file. Saving replaces the document on the revision this screen loaded.
 */
export function ListEditor<T extends FieldValues>(props: ListEditorProps<T>) {
  const { view, at, noun } = props;
  const readOnly = view.pack.read_only;
  const [open, setOpen] = useState<number | "new" | null>(null);
  const items = useMemo(() => listAt(view.document, at) as T[], [view.document, at]);
  const prefix = `${view.kind}.${at.join(".")}`;
  const mainList = at.length === 1 && at[0] === view.list;
  const checkOf = (i: number) => (mainList ? view.checks.items[i] : undefined);
  const rows = useMemo(() => items.map((it, i) => ({ ...it, _index: i }) as Row<T>), [items]);
  const flagged = view.checks.warnings.filter((w) => w.path.startsWith(`${prefix}[`)).length;
  const columns: DataColumn<Row<T>>[] = [
    { id: "title", header: noun[0][0]!.toUpperCase() + noun[0].slice(1), alwaysVisible: true, value: (r) => props.title(r), cell: (r) => <span className="font-medium">{props.title(r)}</span> },
    ...(props.columns ?? []),
    ...(mainList ? [{ id: "refs", header: "Refers to", value: (r: Row<T>) => { const c = checkOf(r._index); return c ? [...c.refs.measures, ...c.refs.dimensions, ...c.refs.fields].join(" ") : ""; }, cell: (r: Row<T>) => <RefList check={checkOf(r._index)} /> }] : []),
    { id: "checks", header: "Checks", filter: "select", value: (r) => {
      const e = problemsOf(view.checks.errors, prefix, r._index).length;
      const w = problemsOf(view.checks.warnings, prefix, r._index).length;
      return e ? "Errors" : w ? "Missing names" : "OK";
    }, cell: (r) => <Checks errors={problemsOf(view.checks.errors, prefix, r._index).length} warnings={problemsOf(view.checks.warnings, prefix, r._index).length} /> },
  ];
  return (
    <div className="grid gap-3">
      {props.intro}
      {flagged > 0 && (
        <Alert>{flagged === 1 ? "1 name doesn't" : `${flagged} names don't`} resolve in this pack: {view.checks.warnings.filter((w) => w.path.startsWith(`${prefix}[`)).slice(0, 3).map((w) => w.message).join(" ")}</Alert>
      )}
      <DataTable
        label={noun[1][0]!.toUpperCase() + noun[1].slice(1)}
        data={rows}
        columns={columns}
        getRowId={(r) => String(r._index)}
        rowLabel={(r) => props.title(r)}
        onRowActivate={(r) => setOpen(r._index)}
        empty={{ title: `No ${noun[1]} yet`, body: readOnly ? undefined : `Add the first ${noun[0]}.` }}
        toolbar={!readOnly && <Button size="sm" variant="secondary" onClick={() => setOpen("new")}><Plus aria-hidden />Add {noun[0]}</Button>}
      />
      <Sheet open={open !== null} onOpenChange={(o) => { if (!o) setOpen(null); }}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
          {open !== null && (
            <ItemPanel {...props} items={items} prefix={prefix} index={open === "new" ? items.length : open} isNew={open === "new"}
                       item={open === "new" ? props.blank() : items[open]!} check={open === "new" ? undefined : checkOf(open)}
                       onDone={() => setOpen(null)} />
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function ItemPanel<T extends FieldValues>({ view, at, noun, schema, title, renderForm, items, prefix, item, index, isNew, check, onDone }:
  ListEditorProps<T> & { items: T[]; prefix: string; item: T; index: number; isNew: boolean; check: KnowledgeItemCheck | undefined; onDone: () => void }) {
  const readOnly = view.pack.read_only;
  const save = useSaveKnowledge(view);
  const form = useForm<T>({ resolver: keepAll(schema), defaultValues: item as never });
  const [tab, setTab] = useState("form");
  const [yaml, setYaml] = useState(() => toYaml(item));
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [others, setOthers] = useState<KnowledgeProblem[]>([]);
  const [checked, setChecked] = useState<{ check?: KnowledgeItemCheck; problems: KnowledgeProblem[] } | null>(null);
  const yamlId = useId();
  useEffect(() => { if (tab === "yaml") setYaml(toYaml(tidy(form.getValues() as Record<string, unknown>))); }, [tab, form]);
  const mine = (list: KnowledgeProblem[]) => problemsOf(list, prefix, index);
  const shown = checked ?? { check, problems: [...mine(view.checks.errors), ...mine(view.checks.warnings)] };

  const applyYaml = (): boolean => {
    const parsed = fromYaml<T>(yaml);
    if (!parsed.ok) { setYamlError(parsed.error); return false; }
    const ok = schema.safeParse(parsed.value);
    if (!ok.success) {
      setYamlError(ok.error.issues.map((i) => `${i.path.join(".") || "item"}: ${i.message}`).join(" "));
      return false;
    }
    setYamlError(null);
    form.reset(parsed.value as never);
    return true;
  };

  const docWith = (next: T[]) => withList(view.document, at, next);
  const persist = async (next: T[]) => {
    setOthers([]);
    try {
      await save.mutateAsync(docWith(next));
      const cap = noun[0][0]!.toUpperCase() + noun[0].slice(1);
      toast.success(next.length >= items.length ? `${cap} saved` : `${cap} removed`);
      onDone();
    } catch (e) {
      const err = asApiError(e, "The pack could not be saved.");
      const details = err instanceof ApiError ? (err.details as Partial<KnowledgeChecks>) : {};
      if (err.code === "validation_error" && details.errors) {
        const own = mine(details.errors);
        for (const m of own) if (m.path) form.setError(m.path as Path<T>, { message: m.message });
        setOthers(details.errors.filter((p) => !under(p.path, `${prefix}[${index}]`)));
        if (tab === "yaml") setYamlError(own.map((m) => `${m.path || "item"}: ${m.message}`).join(" ") || null);
      } else {
        toast.error(err instanceof ApiError && err.status === 412 ? "Someone changed this document since you opened it. Reload, then make your change again." : friendlyMessage(err));
      }
    }
  };

  const current = (): T => tidy(form.getValues() as Record<string, unknown>) as T;
  const onSubmit = form.handleSubmit(async () => {
    const next = [...items];
    next[index] = current();
    await persist(next);
  });
  const runCheck = async () => {
    if (tab === "yaml" && !applyYaml()) return;
    const next = [...items];
    next[index] = current();
    try {
      const out = await checkKnowledge(view.pack.id, view.kind, docWith(next));
      const problems = [...mine(out.errors), ...mine(out.warnings)];
      setChecked({ check: at.length === 1 && at[0] === view.list ? out.items[index] : undefined, problems });
    } catch (e) {
      toast.error(friendlyMessage(asApiError(e, "The item could not be checked.")));
    }
  };

  return (
    <div className="grid gap-4">
      <SheetHeader>
        <SheetTitle>{isNew ? `New ${noun[0]}` : title(item)}</SheetTitle>
        <SheetDescription>{readOnly ? "Read-only: clone the pack to change it." : "Saved to the draft when you choose Save; the document is checked again."}</SheetDescription>
      </SheetHeader>
      {(shown.check || shown.problems.length > 0) && (
        <section aria-label="Checks" className="grid gap-2 rounded-md border border-line p-3 text-small">
          {shown.check && <div className="flex flex-wrap items-center gap-2"><span className="text-ink-muted">Refers to</span><RefList check={shown.check} /></div>}
          {shown.problems.length > 0 ? (
            <ul className="grid gap-1">{shown.problems.map((p) => <li key={`${p.path}:${p.message}`} className="text-status-warn">{p.path ? `${p.path}: ` : ""}{p.message}</li>)}</ul>
          ) : <p className="text-ink-muted">Every name it uses is in the pack.</p>}
        </section>
      )}
      <Tabs value={tab} onValueChange={(v) => { if (tab === "yaml" && v === "form" && !readOnly && !applyYaml()) return; setTab(v); }}>
        <TabsList aria-label={`${noun[0]} editor`}>
          <TabsTrigger value="form">Form</TabsTrigger>
          <TabsTrigger value="yaml">YAML</TabsTrigger>
        </TabsList>
        <TabsContent value="form">
          <Form {...form}>
            <form className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void onSubmit(); }} noValidate>
              <fieldset disabled={readOnly} className="grid gap-3">{renderForm(form, readOnly)}</fieldset>
            </form>
          </Form>
        </TabsContent>
        <TabsContent value="yaml" className="grid gap-2">
          <Label htmlFor={yamlId}>The {noun[0]} as written in {view.kind}.yaml</Label>
          <Textarea id={yamlId} value={yaml} readOnly={readOnly} spellCheck={false} rows={16} className="font-mono text-small"
                    aria-invalid={!!yamlError} onChange={(e) => setYaml(e.target.value)} />
          {yamlError && <p role="alert" className="text-small text-status-danger">{yamlError}</p>}
          {!readOnly && <Button type="button" size="sm" variant="secondary" className="w-fit" onClick={() => { if (applyYaml()) setTab("form"); }}>Use this YAML</Button>}
        </TabsContent>
      </Tabs>
      {others.length > 0 && (
        <Alert>Other items of this document need fixing first: {others.slice(0, 4).map((p) => `${p.path}: ${p.message}`).join(" ")}</Alert>
      )}
      {!readOnly && (
        <div className="flex flex-wrap justify-between gap-2">
          <span className="flex flex-wrap gap-2">
            <Button type="button" disabled={save.isPending} onClick={() => { if (tab === "yaml" && !applyYaml()) return; void onSubmit(); }}>Save {noun[0]}</Button>
            <Button type="button" variant="secondary" onClick={() => void runCheck()}><CheckCheck aria-hidden />Check</Button>
          </span>
          {!isNew && <Button type="button" variant="ghost" disabled={save.isPending} onClick={() => void persist(items.filter((_, i) => i !== index))}><Trash2 aria-hidden />Remove</Button>}
        </div>
      )}
    </div>
  );
}
