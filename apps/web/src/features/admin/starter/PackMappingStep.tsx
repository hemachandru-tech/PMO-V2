import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";

import { api } from "../../../api/client";
import type { GuardWarning, PackAiSuggestion, PackFoundBy, PackMapping, PackMappingEntity, PackMappingField, PackPreview } from "../../../api/app";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "../../../components/ui/command";
import { Input } from "../../../components/ui/input";
import { NativeSelect } from "../../../components/ui/native-select";
import { Popover, PopoverContent, PopoverTrigger } from "../../../components/ui/popover";

export const FOUND_BY: Record<PackFoundBy, string> = {
  exact: "Same name",
  dataverse_label: "Dataverse label column",
  model_label: "Your model's label",
  similar_name: "Similar name",
  mapped: "Your choice",
  not_in_source: "Not in this source",
  missing: "Not found",
  column_taken: "Its column is mapped to another field",
};
const NOT_IN_SOURCE = "\u0000none";
// One column template for the table, heading and field rows, so pack field, type, how found and column line up.
const COLS = "sm:grid-cols-[minmax(0,1fr)_6rem_9rem_minmax(12rem,18rem)]";
const BLANK = "";

/** The kind of a source column type, as the API reads it (`packs/mapping.py` `family`). */
function family(type: string | null): string | null {
  const t = (type ?? "").toLowerCase();
  if (!t) return null;
  if (t.includes("uuid") || t.includes("uniqueidentifier")) return "uuid";
  if (t.includes("bool") || t === "bit") return "boolean";
  if (t.includes("date") || t.includes("time")) return "temporal";
  if (["int", "numeric", "decimal", "double", "float", "real", "money", "number"].some((k) => t.includes(k))) return "number";
  if (["char", "text", "string", "clob"].some((k) => t.includes(k))) return "text";
  return null;
}

/** Whether a source column can stand for a pack field (the API checks the same on save). */
export function fits(f: Pick<PackMappingField, "data_type" | "semantic_type">, type: string | null): boolean {
  const fam = family(type);
  if (fam === null) return true;
  if (f.data_type === "text") return fam === "text" || (fam === "uuid" && f.semantic_type === "identifier");
  if (f.data_type === "integer" || f.data_type === "decimal") return fam === "number";
  if (f.data_type === "date" || f.data_type === "datetime") return fam === "temporal";
  if (f.data_type === "boolean") return fam === "boolean";
  return true;
}

/** Whether the Admin may pick a column for a pack field (`packs/mapping.py` `can_map`, BUG-021): any column for a text
 * field — a yes/no, number or date column is read as text — and a fitting one for the others. */
export function canMap(f: Pick<PackMappingField, "data_type" | "semantic_type">, type: string | null): boolean {
  return f.data_type === "text" || fits(f, type);
}

function ColumnPicker({ field, columns, value, onPick }: {
  field: PackMappingField; columns: PackMappingEntity["columns"]; value: string | null; onPick: (column: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  // Every column is listed (BUG-021); one that can't stand for this field says why instead of being hidden.
  const sorted = [...columns].sort((a, b) => Number(canMap(field, b.type)) - Number(canMap(field, a.type)));
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="secondary" size="sm" className="w-full justify-start font-mono" aria-label={`Column for ${field.label}`}>
          {value ?? (field.how === "not_in_source" ? "Not in this source" : "Choose a column")}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="end">
        <Command label={`Columns for ${field.label}`}>
          <CommandInput placeholder="Search columns" />
          <CommandList className="max-h-64">
            <CommandEmpty>No column matches.</CommandEmpty>
            <CommandItem value={NOT_IN_SOURCE} keywords={["not in this source", "none"]} onSelect={() => { onPick(null); setOpen(false); }}>
              Not in this source
            </CommandItem>
            {sorted.map((c) => {
              const ok = canMap(field, c.type);
              const asText = ok && field.data_type === "text" && !fits(field, c.type);
              return (
                <CommandItem key={c.name} value={c.name} disabled={!ok} onSelect={() => { onPick(c.name); setOpen(false); }}
                  title={ok ? asText ? `Read as text${family(c.type) === "boolean" ? " (Yes / No)" : ""}` : undefined : `${field.label} needs a ${field.data_type} column`}>
                  <span className="font-mono">{c.name}</span>
                  <span className="ml-auto text-small text-ink-muted">{ok ? asText ? `${c.type} → text` : c.type : `${c.type} · not ${field.data_type}`}</span>
                </CommandItem>
              );
            })}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function ValueMap({ connectionId, field, map, onChange }: {
  connectionId: string; field: PackMappingField; map: Record<string, string>; onChange: (map: Record<string, string>) => void;
}) {
  const listId = useId();
  const [source, setSource] = useState("");
  const [target, setTarget] = useState(field.values?.[0] ?? "");
  const ref = field.source_values_ref;
  // Suggestions from the approved model's field on the same column, when there is one.
  const suggestions = useQuery({
    queryKey: ["pack-source-values", connectionId, ref?.ref],
    enabled: !!ref,
    retry: false,
    queryFn: () => api<{ values: unknown[] }>("/api/v1/query/filter-values", {
      method: "POST", body: { connection_id: connectionId, model_version_id: ref!.model_version_id, ref: ref!.ref, limit: 50 },
    }),
  });
  const targets = [...(field.values ?? []), "Not set"];
  const add = () => { onChange({ ...map, [source.trim()]: target }); setSource(""); };
  return (
    <div className="grid gap-2 rounded-md bg-surface-sunken p-2 text-small" data-testid={`values-${field.key}`}>
      <p className="text-ink-muted">The pack's rules use {(field.values ?? []).join(", ")}. Map the values this source uses instead (leave the source value empty for blanks).</p>
      {Object.entries(map).length > 0 && (
        <ul className="grid gap-1">
          {Object.entries(map).map(([from, to]) => (
            <li key={from} className="flex items-center gap-2">
              <span className="font-mono">{from === BLANK ? "(empty)" : from}</span> → <span>{to}</span>
              <Button variant="ghost" size="sm" className="ml-auto" aria-label={`Remove ${from || "empty"} → ${to}`}
                onClick={() => { const next = { ...map }; delete next[from]; onChange(next); }}>Remove</Button>
            </li>
          ))}
        </ul>
      )}
      <div className="grid gap-2 sm:grid-cols-[1fr_auto_10rem_auto] sm:items-center">
        <Input aria-label={`Source value for ${field.label}`} placeholder="Source value, e.g. Yellow" value={source} list={listId}
          onChange={(e) => setSource(e.target.value)} />
        <datalist id={listId}>{(suggestions.data?.values ?? []).map((v) => <option key={String(v)} value={String(v)} />)}</datalist>
        <span aria-hidden>→</span>
        <NativeSelect aria-label={`Pack value for ${field.label}`} value={target} onChange={(e) => setTarget(e.target.value)}>
          {targets.map((v) => <option key={v} value={v}>{v}</option>)}
        </NativeSelect>
        <Button variant="secondary" size="sm" disabled={source.trim() in map} onClick={add}>Add</Button>
      </div>
    </div>
  );
}

/** The pack's values beside the column's (US-199), with the pack's values the column lacks. */
function ColumnValues({ field, missing }: { field: PackMappingField; missing?: string[] }) {
  const shown = (field.source_values ?? []).filter((v) => v.value !== null).slice(0, 8);
  if (!field.source_values) return null;
  return (
    <p className="text-small text-ink-muted" data-testid={`column-values-${field.key}`}>
      Pack: {(field.values ?? []).join(", ") || "—"} · <span className="font-mono">{field.column}</span>: {shown.length ? shown.map((v) => `${v.value} (${v.rows.toLocaleString()})`).join(", ") : "no values"}
      {(field.source_values.length > shown.length) && " …"}
      {missing && missing.length > 0 && <span className="text-status-warn"> · not found: {missing.join(", ")}</span>}
    </p>
  );
}

/** A column suggested by its values (US-200), with the evidence and a confidence; the Admin uses it or picks another. */
function Suggestion({ field, onUse }: { field: PackMappingField; onUse: (column: string) => void }) {
  const s = field.suggestion;
  if (!s) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md bg-surface-sunken p-2 text-small" data-testid={`suggestion-${field.key}`}>
      <span>
        {s.reason === "values_dont_fit" ? "Its values don't fit. " : ""}Suggested by values: <span className="font-mono">{s.column}</span>
        <Badge variant="outline" className="ml-2">{Math.round(s.confidence * 100)}% confidence</Badge>
      </span>
      <span className="text-ink-muted">{s.evidence}</span>
      <Button variant="secondary" size="sm" className="ml-auto" onClick={() => onUse(s.column)}>Use {s.column}</Button>
    </div>
  );
}

/**
 * A column the tenant's model suggests (US-201), marked "Suggested by AI" with its reason and confidence, and what
 * US-199's guards would refuse; "none" is shown with its reason. Hidden once the field reads that column.
 */
function AiSuggestion({ field, suggestion: s, onUse }: { field: PackMappingField; suggestion?: PackAiSuggestion; onUse: (column: string) => void }) {
  if (!s || (s.column !== null && s.column === field.column)) return null;
  return (
    <div className="grid gap-1 rounded-md bg-surface-sunken p-2 text-small" data-testid={`ai-suggestion-${field.key}`}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge variant="outline">Suggested by AI</Badge>
        {s.column ? <span className="font-mono">{s.column}</span> : <span>No column found</span>}
        <span className="text-ink-muted">{Math.round(s.confidence * 100)}% confidence</span>
        {s.column && <Button variant="secondary" size="sm" className="ml-auto" onClick={() => onUse(s.column!)}>Use {s.column}</Button>}
      </div>
      <p className="text-ink-muted">{s.reason}</p>
      {s.flags.length > 0 && (
        <ul className="list-disc pl-5 text-status-warn">{s.flags.map((f) => <li key={f}>{f}</li>)}</ul>
      )}
    </div>
  );
}

function FieldRow({ connectionId, entity, field, mapping, problems, ai, onChange }: {
  connectionId: string; entity: PackMappingEntity; field: PackMappingField; mapping: PackMapping; problems: Problems;
  ai?: PackAiSuggestion; onChange: (m: PackMapping) => void;
}) {
  const ref = `${entity.key}.${field.key}`;
  const [showValues, setShowValues] = useState(false);
  const map = mapping.values[ref] ?? {};
  const blocking = problems.blocking[ref];
  const warning = problems.warnings[ref];
  return (
    <li className="grid gap-1 border-b border-line py-2 last:border-0" data-testid={`field-${ref}`}>
      <div className={`grid gap-x-3 gap-y-1 ${COLS} sm:items-center`}>
        <p className="text-body-strong">{field.label}{field.required && <Badge variant="outline" className="ml-2">Required</Badge>}</p>
        <p className="text-small text-ink-muted"><span className="sm:hidden">Type: </span>{field.data_type}</p>
        <p className={`text-small ${field.column ? "text-ink-muted" : "text-status-warn"}`}>{FOUND_BY[field.how]}</p>
        <ColumnPicker field={field} columns={entity.columns} value={field.column}
          onPick={(column) => onChange({ ...mapping, fields: { ...mapping.fields, [ref]: column } })} />
      </div>
      {blocking && <p className="text-small text-status-danger" role="alert">{blocking}</p>}
      <Suggestion field={field} onUse={(column) => onChange({ ...mapping, fields: { ...mapping.fields, [ref]: column } })} />
      <AiSuggestion field={field} suggestion={ai} onUse={(column) => onChange({ ...mapping, fields: { ...mapping.fields, [ref]: column } })} />
      {field.column && <ColumnValues field={field} missing={warning?.missing} />}
      {field.values && field.column && (
        <div className="grid gap-1">
          <Button variant="link" size="sm" className="justify-self-start px-0" aria-expanded={showValues} onClick={() => setShowValues((v) => !v)}>
            {Object.keys(map).length ? `${Object.keys(map).length} value${Object.keys(map).length === 1 ? "" : "s"} mapped` : "Map values"}
          </Button>
          {showValues && <ValueMap connectionId={connectionId} field={field} map={map}
            onChange={(next) => {
              const values = { ...mapping.values };
              if (Object.keys(next).length) values[ref] = next; else delete values[ref];
              onChange({ ...mapping, values });
            }} />}
        </div>
      )}
    </li>
  );
}

/** Guard findings by `entity.field` (US-199): a blocking message, or the pack values missing from the column. */
export interface Problems { blocking: Record<string, string>; warnings: Record<string, GuardWarning> }

export function problemsOf(checks?: PackPreview["checks"]): Problems {
  const key = (ref: string) => ref.replace(/^field:/, "");
  return {
    blocking: Object.fromEntries((checks?.blocking ?? []).map((b) => [key(b.ref), b.message])),
    warnings: Object.fromEntries((checks?.warnings ?? []).filter((w) => w.ref.startsWith("field:")).map((w) => [key(w.ref), w])),
  };
}

/**
 * Map the pack to this source's names (US-154): for each pack table and field, how it was found, and a choice of
 * table or column (searchable; every column is listed, a text field takes any, BUG-021) or "Not in this source". Status-like fields take a
 * value map (source "Yellow" → pack "Amber") and show the column's values beside the pack's (US-199). A field with
 * a guard finding (sharing a column, values not found) or a column suggested by its values (US-200) is listed with
 * the open ones. With a model set up, "Suggest with AI" asks it about what names and values leave open (US-201); its
 * suggestions show on the rows, never applied until the Admin uses one. The parent re-runs the preview
 * with the mapping after each change.
 */
export function PackMappingStep({ connectionId, objects, entities, mapping, problems = { blocking: {}, warnings: {} }, ai, onChange }: {
  connectionId: string; objects: string[]; entities: PackMappingEntity[]; mapping: PackMapping; problems?: Problems;
  ai?: { available: boolean; pending: boolean; suggestions: Record<string, PackAiSuggestion>; note: string | null; onAsk: () => void };
  onChange: (m: PackMapping) => void;
}) {
  const total = entities.reduce((n, e) => n + e.fields.length, 0);
  const found = entities.reduce((n, e) => n + e.fields.filter((f) => f.column).length, 0);
  const required = entities.flatMap((e) => (e.object || e.primary ? e.fields.filter((f) => f.required && !f.column) : []));
  return (
    <section className="grid gap-2" aria-labelledby="pack-mapping-title" data-testid="pack-mapping">
      <div>
        <h3 id="pack-mapping-title" className="text-body-strong">Match the pack to your fields</h3>
        <p className="text-small text-ink-muted">
          {found} of {total} pack fields found{required.length ? ` · ${required.length} required ${required.length === 1 ? "field needs" : "fields need"} a column` : ""}.
          Your choices are saved for this connection and reused when the pack is applied again or updated.
        </p>
      </div>
      {ai?.available && (
        <div className="flex flex-wrap items-center gap-2" data-testid="pack-ai">
          <Button size="sm" variant="secondary" disabled={ai.pending} onClick={ai.onAsk}>{ai.pending ? "Asking the AI…" : "Suggest with AI"}</Button>
          <p className="text-small text-ink-muted">{ai.note ?? "Asks your AI model about the fields names and values leave open. It sees column names, types and a few values, never rows; nothing is applied until you use a suggestion."}</p>
        </div>
      )}
      <div className="grid gap-3">
        {entities.map((e) => {
          const flagged = (f: PackMappingField) => `${e.key}.${f.key}` in problems.blocking || `${e.key}.${f.key}` in problems.warnings || !!f.suggestion
            || `${e.key}.${f.key}` in (ai?.suggestions ?? {});
          const open = e.fields.filter((f) => !f.column || f.how === "mapped" || f.how === "not_in_source" || flagged(f));
          const auto = e.fields.filter((f) => !open.includes(f));
          return (
            <div key={e.key} className="grid gap-2 rounded-md border border-line p-2">
              <div className={`grid gap-x-3 gap-y-1 ${COLS} sm:items-center`}>
                <p className="text-body-strong sm:col-span-3">{e.label} <span className="text-small font-normal text-ink-muted">· {Math.round(e.confidence * 100)}% of fields found · {FOUND_BY[e.how]}</span></p>
                <NativeSelect aria-label={`Table for ${e.label}`} value={e.object ?? NOT_IN_SOURCE}
                  onChange={(ev) => onChange({ ...mapping, entities: { ...mapping.entities, [e.key]: ev.target.value === NOT_IN_SOURCE ? null : ev.target.value } })}>
                  <option value={NOT_IN_SOURCE}>Not in this source</option>
                  {objects.map((o) => <option key={o} value={o}>{o}</option>)}
                </NativeSelect>
              </div>
              {e.object && (
                <>
                  <div className={`hidden gap-x-3 border-b border-line pb-1 text-small text-ink-muted sm:grid ${COLS}`} aria-hidden>
                    <span>Pack field</span><span>Type</span><span>How found</span><span>Source column</span>
                  </div>
                  {open.length > 0 && <ul className="grid">{open.map((f) => <FieldRow key={f.key} connectionId={connectionId} entity={e} field={f} mapping={mapping} problems={problems} ai={ai?.suggestions[`${e.key}.${f.key}`]} onChange={onChange} />)}</ul>}
                  {auto.length > 0 && (
                    <details className="text-small">
                      <summary className="cursor-pointer">{auto.length} found automatically</summary>
                      <ul className="grid">{auto.map((f) => <FieldRow key={f.key} connectionId={connectionId} entity={e} field={f} mapping={mapping} problems={problems} ai={ai?.suggestions[`${e.key}.${f.key}`]} onChange={onChange} />)}</ul>
                    </details>
                  )}
                </>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
