import { zodResolver } from "@hookform/resolvers/zod";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { useForm, type FieldValues, type Path, type Resolver, type UseFormReturn } from "react-hook-form";
import { toast } from "sonner";
import type { z } from "zod";

import { ApiError } from "../../../api/client";
import type { PackCheck, PackSection } from "../../../api/packs";
import { Alert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Form } from "../../../components/ui/form";
import { Label } from "../../../components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../../components/ui/tabs";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError, friendlyMessage } from "../../../lib/api-errors";
import { usePack } from "./PackContext";
import { compact, fromYaml, toYaml } from "./yaml";

export type Row<T> = T & { _index: number };

export interface SectionEditorProps<T extends FieldValues> {
  section: Extract<PackSection, "entities" | "links" | "measures" | "dimensions">;
  /** "measure" / "measures". */
  noun: [string, string];
  items: T[];
  columns: DataColumn<Row<T>>[];
  schema: z.ZodType<T, FieldValues>;
  blank: () => T;
  title: (item: T) => string;
  /** The form's fields (React Hook Form controls). */
  renderForm: (form: UseFormReturn<T>, readOnly: boolean) => ReactNode;
  /** Shown above the form for an existing item (the measure's rule tree, "Try on a connection"). */
  renderExtra?: (item: T) => ReactNode;
  intro?: ReactNode;
}

/** Checks with the Zod schema but submits the values as they are: the form shows only some of an item's keys (bands,
 * a field's format …) and a parsed object would drop the rest. */
function keepAll<T extends FieldValues>(schema: z.ZodType<T, FieldValues>): Resolver<T> {
  const check = zodResolver(schema as never) as unknown as Resolver<T>;
  return async (values, ctx, opts) => {
    const out = await check(values, ctx, opts);
    return Object.keys(out.errors).length ? out : { values, errors: {} };
  };
}

const under = (path: string, head: string) => path === head || path.startsWith(`${head}.`) || path.startsWith(`${head}[`);

/** API check paths of one item (`measures[3].expression.agg`) → that item's form path (`expression.agg`). */
export function itemErrors(checks: PackCheck[], section: string, index: number): { path: string; message: string }[] {
  const head = `${section}[${index}]`;
  return checks.filter((c) => under(c.path, head))
    .map((c) => ({ path: c.path.slice(head.length).replace(/^\./, "").replace(/\[(\d+)\]/g, ".$1"), message: c.message }));
}

/**
 * One manifest section as a table with a side panel (US-232): the panel edits an item in a form (React Hook Form +
 * Zod, the schemas the API's item models are checked against) or as YAML, the same text as in `manifest.yaml`. Saving
 * replaces the whole section on the loaded revision; the API's errors come back on the item's fields.
 */
export function SectionEditor<T extends FieldValues>(props: SectionEditorProps<T>) {
  const { section, noun, items, columns } = props;
  const { pack, readOnly } = usePack();
  const [open, setOpen] = useState<number | "new" | null>(null);
  const rows = useMemo(() => items.map((it, i) => ({ ...it, _index: i }) as Row<T>), [items]);
  const warnings = pack.checks.warnings.filter((w) => w.path.startsWith(`${section}[`));
  return (
    <div className="grid gap-3">
      {props.intro}
      {warnings.length > 0 && (
        <Alert>{warnings.length === 1 ? "1 reference doesn't resolve" : `${warnings.length} references don't resolve`}: {warnings.slice(0, 3).map((w) => w.message).join(" ")}</Alert>
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
            <ItemPanel {...props} index={open === "new" ? items.length : open} isNew={open === "new"}
                       item={open === "new" ? props.blank() : items[open]!} onDone={() => setOpen(null)} />
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function ItemPanel<T extends FieldValues>({ section, noun, items, schema, title, renderForm, renderExtra, item, index, isNew, onDone }:
  SectionEditorProps<T> & { item: T; index: number; isNew: boolean; onDone: () => void }) {
  const { save, saving, readOnly } = usePack();
  const form = useForm<T>({ resolver: keepAll(schema), defaultValues: item as never });
  const [tab, setTab] = useState("form");
  const [yaml, setYaml] = useState(() => toYaml(item));
  const [yamlError, setYamlError] = useState<string | null>(null);
  const [problems, setProblems] = useState<PackCheck[]>([]);
  const yamlId = useId();
  useEffect(() => { if (tab === "yaml") setYaml(toYaml(compact(form.getValues() as Record<string, unknown>))); }, [tab, form]);

  const applyYaml = (): boolean => {
    const parsed = fromYaml<T>(yaml);
    if (!parsed.ok) { setYamlError(parsed.error); return false; }
    const checked = schema.safeParse(parsed.value);
    if (!checked.success) {
      setYamlError(checked.error.issues.map((i) => `${i.path.join(".") || "item"}: ${i.message}`).join(" "));
      return false;
    }
    setYamlError(null);
    form.reset(parsed.value as never);
    return true;
  };

  const persist = async (next: T[] | null) => {
    setProblems([]);
    try {
      await save({ [section]: next ?? items.filter((_, i) => i !== index) });
      toast.success(next ? `${noun[0][0]!.toUpperCase()}${noun[0].slice(1)} saved` : `${noun[0][0]!.toUpperCase()}${noun[0].slice(1)} removed`);
      onDone();
    } catch (e) {
      const err = asApiError(e, "The pack could not be saved.");
      const details = err instanceof ApiError ? (err.details as { errors?: PackCheck[] }) : {};
      if (err.code === "validation_error" && details.errors) {
        const mine = itemErrors(details.errors, section, index);
        for (const m of mine) if (m.path) form.setError(m.path as Path<T>, { message: m.message });
        setProblems(details.errors.filter((c) => !under(c.path, `${section}[${index}]`)));
        if (tab === "yaml") setYamlError(mine.map((m) => `${m.path || "item"}: ${m.message}`).join(" ") || null);
      } else {
        toast.error(friendlyMessage(err));
      }
    }
  };

  const onSubmit = form.handleSubmit(async (values) => {
    const clean = compact(values as Record<string, unknown>) as T;
    const next = [...items];
    next[index] = clean;
    await persist(next);
  });

  return (
    <div className="grid gap-4">
      <SheetHeader>
        <SheetTitle>{isNew ? `New ${noun[0]}` : title(item)}</SheetTitle>
        <SheetDescription>{readOnly ? "Read-only: clone the pack to change it." : "Saved to the draft when you choose Save; the pack is checked again."}</SheetDescription>
      </SheetHeader>
      {!isNew && renderExtra?.(item)}
      <Tabs value={tab} onValueChange={(v) => { if (tab === "yaml" && v === "form" && !readOnly && !applyYaml()) return; setTab(v); }}>
        <TabsList aria-label={`${noun[0]} editor`}>
          <TabsTrigger value="form">Form</TabsTrigger>
          <TabsTrigger value="yaml">YAML</TabsTrigger>
        </TabsList>
        <TabsContent value="form">
          <Form {...form}>
            <form id={`${section}-item`} className="grid gap-3" onSubmit={(e) => { e.preventDefault(); void onSubmit(); }} noValidate>
              <fieldset disabled={readOnly} className="grid gap-3">{renderForm(form, readOnly)}</fieldset>
            </form>
          </Form>
        </TabsContent>
        <TabsContent value="yaml" className="grid gap-2">
          <Label htmlFor={yamlId}>The {noun[0]} as written in manifest.yaml</Label>
          <Textarea id={yamlId} value={yaml} readOnly={readOnly} spellCheck={false} rows={18} className="font-mono text-small"
                    aria-invalid={!!yamlError} onChange={(e) => setYaml(e.target.value)} />
          {yamlError && <p role="alert" className="text-small text-status-danger">{yamlError}</p>}
          {!readOnly && <Button type="button" size="sm" variant="secondary" className="w-fit" onClick={() => { if (applyYaml()) setTab("form"); }}>Use this YAML</Button>}
        </TabsContent>
      </Tabs>
      {problems.length > 0 && (
        <Alert>Other parts of the pack need fixing first: {problems.slice(0, 4).map((p) => `${p.path}: ${p.message}`).join(" ")}</Alert>
      )}
      {!readOnly && (
        <div className="flex flex-wrap justify-between gap-2">
          <Button type="button" disabled={saving} onClick={() => { if (tab === "yaml" && !applyYaml()) return; void onSubmit(); }}>Save {noun[0]}</Button>
          {!isNew && <Button type="button" variant="ghost" disabled={saving} onClick={() => void persist(null)}><Trash2 aria-hidden />Remove</Button>}
        </div>
      )}
    </div>
  );
}
