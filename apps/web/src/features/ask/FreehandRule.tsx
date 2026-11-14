import { useQuery } from "@tanstack/react-query";
import { KeyRound, Pencil, Scale } from "lucide-react";
import { useState, type FormEvent } from "react";
import { toast } from "sonner";

import { api, ApiError } from "../../api/client";
import type { BesideAnswer, FreehandRule, RuleChip } from "../../api/app";
import { formatValue } from "../../components/format";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { NativeSelect } from "../../components/ui/native-select";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";

interface CatalogField { key: string; label: string; data_type: string }
interface Catalog { entities: { key: string; label: string; fields: CatalogField[] }[] }

const DATE_TYPES = ["date", "datetime"];
const NUMBER_TYPES = ["integer", "decimal"];
/** Conditions by field type, in words (the API takes the operator). */
const CONDITIONS: { op: string; label: string; types: "any" | "date" | "number" | "text"; needs: "none" | "value" | "today" | "values" }[] = [
  { op: "is_null", label: "is blank", types: "any", needs: "none" },
  { op: "not_null", label: "is not blank", types: "any", needs: "none" },
  { op: "<", label: "is before today", types: "date", needs: "today" },
  { op: ">", label: "is after today", types: "date", needs: "today" },
  { op: "<=", label: "is on or before", types: "date", needs: "value" },
  { op: ">=", label: "is on or after", types: "date", needs: "value" },
  { op: "=", label: "is", types: "text", needs: "value" },
  { op: "!=", label: "is not", types: "text", needs: "value" },
  { op: "in", label: "is one of", types: "text", needs: "values" },
  { op: ">", label: "is above", types: "number", needs: "value" },
  { op: "<", label: "is below", types: "number", needs: "value" },
  { op: ">=", label: "is at least", types: "number", needs: "value" },
  { op: "<=", label: "is at most", types: "number", needs: "value" },
];

function family(type: string | null | undefined): "date" | "number" | "text" {
  if (type && DATE_TYPES.includes(type)) return "date";
  if (type && NUMBER_TYPES.includes(type)) return "number";
  return "text";
}

function conditionsFor(type: string | null | undefined) {
  const f = family(type);
  return CONDITIONS.filter((c) => c.types === "any" || c.types === f);
}

function isToday(v: unknown): boolean {
  return typeof v === "object" && v !== null && (v as { today?: boolean }).today === true;
}

/** The condition a chip shows, as the editor's choice: its index in ``conditionsFor``. */
function initialCondition(chip: RuleChip): number {
  const list = conditionsFor(chip.field_type);
  const today = isToday(chip.value);
  const i = list.findIndex((c) => c.op === chip.op && (c.needs === "today") === today);
  return i >= 0 ? i : 0;
}

/**
 * "Not certified · built from your question" (ENH-008 US-165): the rule in words and one chip per condition. A chip
 * opens a small editor (field, condition, value); applying it reruns the question with the changed rule.
 */
export function RuleCard({ rule, connectionId, onChange }: {
  rule: FreehandRule;
  connectionId: string;
  onChange: (chip: RuleChip, change: { field: string; operator: string; value?: unknown; values?: unknown[] }, label: string) => void;
}) {
  return (
    <section aria-label="Your rule" className="grid gap-2 rounded-md border border-dashed border-line-strong bg-surface-sunken p-3">
      <p className="text-small"><span className="font-medium">Rule:</span> {rule.plain_rule}</p>
      {rule.chips.length > 0 && (
        <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Conditions you can change">
          {rule.chips.map((chip) => <ConditionChip key={chip.path} chip={chip} entity={rule.entity} connectionId={connectionId} onChange={onChange} />)}
        </div>
      )}
    </section>
  );
}

function ConditionChip({ chip, entity, connectionId, onChange }: {
  chip: RuleChip;
  entity: string;
  connectionId: string;
  onChange: (chip: RuleChip, change: { field: string; operator: string; value?: unknown; values?: unknown[] }, label: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const catalog = useQuery({
    queryKey: ["catalog", connectionId], enabled: open,
    queryFn: () => api<Catalog>(`/api/v1/query/catalog?connection_id=${encodeURIComponent(connectionId)}`),
  });
  const fields = catalog.data?.entities.find((e) => e.key === entity)?.fields ?? [{ key: chip.field, label: chip.field_label, data_type: chip.field_type ?? "text" }];
  const [field, setField] = useState(chip.field);
  const type = fields.find((f) => f.key === field)?.data_type ?? chip.field_type;
  const [condition, setCondition] = useState(initialCondition(chip));
  const [value, setValue] = useState(isToday(chip.value) || chip.value == null ? "" : Array.isArray(chip.value) ? chip.value.join(", ") : String(chip.value));
  const list = conditionsFor(type);
  const cond = list[Math.min(condition, list.length - 1)]!;
  const fieldLabel = fields.find((f) => f.key === field)?.label ?? chip.field_label;

  const apply = (e: FormEvent) => {
    e.preventDefault();
    const change: { field: string; operator: string; value?: unknown; values?: unknown[] } = { field, operator: cond.op };
    if (cond.needs === "today") change.value = { today: true };
    if (cond.needs === "value") change.value = family(type) === "number" ? Number(value) : value.trim();
    if (cond.needs === "values") change.values = value.split(",").map((v) => v.trim()).filter(Boolean);
    const shown = cond.needs === "value" || cond.needs === "values" ? ` ${value.trim()}` : "";
    setOpen(false);
    onChange(chip, change, `${fieldLabel} ${cond.label}${shown}`);
  };
  const needsValue = cond.needs === "value" || cond.needs === "values";
  const id = `cond-${chip.path.replace(/\./g, "-")}`;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button type="button" aria-label={`Change condition: ${chip.label}`}
          className="inline-flex h-control-sm items-center gap-1 rounded-full border border-line-strong bg-surface-raised px-3 text-small text-ink hover:border-accent hover:bg-accent-soft focus-visible:focus-ring">
          {chip.label}<Pencil aria-hidden className="size-3 text-ink-muted" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80">
        <form className="grid gap-3" onSubmit={apply} aria-label="Change this condition">
          <div className="grid gap-1">
            <Label htmlFor={`${id}-field`}>Field</Label>
            <NativeSelect id={`${id}-field`} value={field} onChange={(e) => { setField(e.target.value); setCondition(0); }}>
              {fields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor={`${id}-op`}>Condition</Label>
            <NativeSelect id={`${id}-op`} value={String(Math.min(condition, list.length - 1))} onChange={(e) => setCondition(Number(e.target.value))}>
              {list.map((c, i) => <option key={`${c.op}-${c.label}`} value={i}>{c.label}</option>)}
            </NativeSelect>
          </div>
          {needsValue && (
            <div className="grid gap-1">
              <Label htmlFor={`${id}-value`}>{cond.needs === "values" ? "Values (comma-separated)" : "Value"}</Label>
              <Input id={`${id}-value`} required value={value} onChange={(e) => setValue(e.target.value)}
                type={family(type) === "date" ? "date" : family(type) === "number" ? "number" : "text"} />
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" size="sm">Apply and rerun</Button>
          </div>
        </form>
      </PopoverContent>
    </Popover>
  );
}

/** "Compare with certified" (US-165, D1): both numbers and both rules, side by side, never under one name. */
export function BesidePanel({ beside, main }: {
  beside: BesideAnswer;
  main: { label: string; plain_rule: string; value: unknown; format?: string | null; certified: boolean; tag?: string };
}) {
  const sides: { label: string; plain_rule: string; value: unknown; format?: string | null; certified: boolean; tag?: string }[] =
    [main, { label: beside.label, plain_rule: beside.plain_rule, value: beside.value, format: beside.format, certified: beside.kind === "certified" }];
  const ordered = sides[0]!.certified ? sides : [sides[1]!, sides[0]!];
  return (
    <section aria-label="Compare with certified" className="grid gap-2 border-t border-line pt-3">
      <h3 className="flex items-center gap-1.5 text-small font-medium text-ink-muted"><Scale aria-hidden className="size-3.5" />
        {beside.kind === "freehand" ? "Your rule, beside the certified measure" : "Compare with certified"}</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        {ordered.map((s) => (
          <div key={s.certified ? "certified" : "freehand"} className="grid gap-1 rounded-md border border-line p-3">
            <p className="text-label uppercase text-ink-muted">{s.certified ? "Certified" : s.tag ?? "Not certified · built from your question"}</p>
            <p className="font-medium">{s.label}</p>
            <p className="font-mono text-figure tabular-nums">{s.value === undefined || s.value === null ? "—" : formatValue(s.value, s.format ?? undefined)}</p>
            <p className="text-small text-ink-muted">{s.plain_rule}</p>
          </div>
        ))}
      </div>
      {beside.error && <p className="text-small text-ink-muted">{beside.error}</p>}
    </section>
  );
}

/** "Request free-hand access" on a refusal (US-165 → US-162's queue). */
export function RequestAccessButton({ connectionId, connection, question }: { connectionId: string; connection: string; question: string }) {
  const [sent, setSent] = useState(false);
  const request = async () => {
    try {
      await api("/api/v1/me/permission-requests", {
        method: "POST", body: { connection_id: connectionId, note: `Asked in Ask: “${question.slice(0, 400)}”` },
      });
      setSent(true);
      toast.success(`Free-hand access requested for ${connection}. An Admin will review it.`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "The request could not be sent.");
    }
  };
  return (
    <Button variant="secondary" size="sm" disabled={sent} onClick={() => void request()}>
      <KeyRound aria-hidden />{sent ? "Free-hand access requested" : "Request free-hand access"}
    </Button>
  );
}
