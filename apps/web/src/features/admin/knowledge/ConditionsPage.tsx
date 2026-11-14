import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Calculator, Check } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";

import { api } from "../../../api/client";
import type { ConditionList, NamedCondition } from "../../../api/knowledge";
import { formatRelative, formatValue } from "../../../components/format";
import { FormAlert } from "../../../components/ui/alert";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";
import { asApiError } from "../../../lib/api-errors";
import { KnowledgeNav } from "./KnowledgeNav";

type Filter = "approved" | "proposed" | "all";

const SOURCES: Record<NamedCondition["source"], string> = {
  document: "Definition in a document",
  definition_sheet: "Business context sheet",
  pack_measure: "Certified measure",
};

function StatusBadge({ c }: { c: NamedCondition }) {
  if (c.status === "proposed") return <Badge variant="stale">Can't run on this model</Badge>;
  if (c.status === "retired") return <Badge variant="outline">Retired</Badge>;
  if (c.conflict && !c.prefer) return <Badge variant="stale">Differs from the measure</Badge>;
  return <Badge variant="certified">In use</Badge>;
}

/** Which rule Ask counts the term by now. */
function UsesCell({ c }: { c: NamedCondition }) {
  if (c.status !== "approved") return <span className="grid gap-0.5">{c.gaps.map((g) => <span key={g} className="text-small text-status-warn">{g}</span>)}</span>;
  const by = c.uses === "condition" ? "This condition" : c.measure?.label ?? "The certified measure";
  return (
    <span className="grid gap-0.5">
      <span>{by}</span>
      {c.prefer && c.decided_at && (
        <span className="text-small text-ink-muted">Chosen by {c.decided_by?.display_name ?? "an Admin"} · {formatRelative(c.decided_at)}</span>
      )}
      {c.conflict && !c.prefer && <span className="text-small text-ink-muted">Until you choose, Ask uses the measure and says the two differ.</span>}
    </span>
  );
}

/** Both rules and a row count each. */
function ConflictCell({ c }: { c: NamedCondition }) {
  const k = c.conflict;
  if (!k) return <span className="text-ink-muted">{c.measure && c.source !== "pack_measure" ? "Same rows as the measure" : "—"}</span>;
  return (
    <span className="grid gap-1">
      <span className="grid gap-0.5">
        <span className="text-small font-medium">This condition: {formatValue(k.condition.figure)} rows</span>
        <span className="text-small text-ink-muted">{k.condition.rule ?? c.rule_text}</span>
      </span>
      <span className="grid gap-0.5">
        <span className="text-small font-medium">{k.measure.label}: {formatValue(k.measure.figure)} rows</span>
        <span className="text-small text-ink-muted">{k.measure.rule}</span>
      </span>
    </span>
  );
}

/**
 * Admin → Knowledge → Conditions (US-252; FEAT-078 / FEAT-007; DEC-29): each business term stored once as a condition
 * Tela can run — from an approved definition, a business-context sheet row, or a certified count measure's own rule.
 * Ask reads an approved condition first, then the certified measure, then document text (a hint only). A condition
 * that selects other rows than the certified measure of the same term is listed with both rules and counts; until
 * the Admin chooses, Ask counts by the measure and says so.
 */
export function ConditionsPage() {
  const qc = useQueryClient();
  const ids = { filter: useId(), conflicts: useId() };
  const [status, setStatus] = useState<Filter>("approved");
  const [conflicts, setConflicts] = useState(false);
  const q = useQuery({
    queryKey: ["conditions", status, conflicts],
    queryFn: () => api<ConditionList>(`/api/v1/knowledge/conditions?status=${status}${conflicts ? "&conflicts=true" : ""}`),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["conditions"] });
  const choose = useMutation({
    mutationFn: ({ c, use }: { c: NamedCondition; use: "condition" | "measure" }) =>
      api<NamedCondition>(`/api/v1/knowledge/conditions/${c.id}/choose`, { method: "POST", body: { use } }),
    onSuccess: (c) => { refresh(); toast.success(`${c.term}: Ask now counts by ${c.uses === "condition" ? "this condition" : c.measure?.label ?? "the measure"}`); },
    onError: (e) => toast.error(asApiError(e, "The choice could not be saved.").message),
  });
  const check = useMutation({
    mutationFn: (c: NamedCondition) => api<NamedCondition>(`/api/v1/knowledge/conditions/${c.id}/check`, { method: "POST", body: {} }),
    onSuccess: (c) => { refresh(); toast.success(c.conflict ? `${c.term}: differs from ${c.measure?.label ?? "the measure"}` : `${c.term}: counted`); },
    onError: (e) => toast.error(asApiError(e, "The condition could not be counted.").message),
  });

  const columns = useMemo<DataColumn<NamedCondition>[]>(() => [
    {
      id: "term", header: "Term", alwaysVisible: true, value: (c) => c.term,
      cell: (c) => (
        <span className="grid gap-0.5">
          <span className="font-medium">{c.term}</span>
          <span className="text-small text-ink-muted">“{c.name}” on {c.entity.replace(/_/g, " ")} · {c.connection.name}</span>
          {c.synonyms.length > 0 && <span className="text-small text-ink-muted">Also: {c.synonyms.join(", ")}</span>}
        </span>
      ),
    },
    { id: "rule", header: "Condition", sortable: false, value: (c) => c.rule_text ?? "", cell: (c) => <span>{c.rule_text ?? "—"}</span> },
    {
      id: "source", header: "From", value: (c) => SOURCES[c.source],
      cell: (c) => <span className="grid gap-0.5"><span>{SOURCES[c.source]}</span>{c.definition?.document.title && <span className="text-small text-ink-muted">{c.definition.document.title}</span>}</span>,
    },
    { id: "conflict", header: "Against the certified measure", sortable: false, value: (c) => (c.conflict ? "differs" : ""), cell: (c) => <ConflictCell c={c} /> },
    { id: "uses", header: "Ask counts by", sortable: false, value: (c) => c.uses ?? "", cell: (c) => <UsesCell c={c} /> },
    { id: "status", header: "Status", value: (c) => c.status, cell: (c) => <StatusBadge c={c} /> },
    {
      id: "actions", header: "Actions", alwaysVisible: true, sortable: false, value: () => "",
      cell: (c) => (
        <span className="flex flex-wrap gap-x-3">
          {c.status === "approved" && c.conflict && c.prefer !== "condition" && (
            <Button variant="link" size="sm" aria-label={`Use the condition for ${c.term}`} disabled={choose.isPending} onClick={() => choose.mutate({ c, use: "condition" })}><Check aria-hidden />Use the condition</Button>
          )}
          {c.status === "approved" && c.conflict && c.prefer !== "measure" && (
            <Button variant="link" size="sm" aria-label={`Use the measure for ${c.term}`} disabled={choose.isPending} onClick={() => choose.mutate({ c, use: "measure" })}><Check aria-hidden />Use the measure</Button>
          )}
          {c.status !== "retired" && c.source !== "pack_measure" && c.measure && (
            <Button variant="link" size="sm" aria-label={`Count ${c.term}`} disabled={check.isPending} onClick={() => check.mutate(c)}><Calculator aria-hidden />Count again</Button>
          )}
        </span>
      ),
    },
  ], [choose, check]);

  const counts = q.data?.counts;
  return (
    <div className="tl grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-title">Knowledge</h1>
        <p className="max-w-prose text-ink-muted">Each business term stored once, as a condition Ask can count by. Ask reads an approved condition first, then the certified measure, then your documents' text. When a condition and the certified measure of the same name count different rows, both are shown here: Ask uses the measure until you choose.</p>
      </div>
      <KnowledgeNav />
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="grid gap-1">
            <Label htmlFor={ids.filter}>Show</Label>
            <NativeSelect id={ids.filter} value={status} onChange={(e) => setStatus(e.target.value as Filter)}>
              <option value="approved">In use</option>
              <option value="proposed">Can't run on this model</option>
              <option value="all">All conditions</option>
            </NativeSelect>
          </div>
          <label htmlFor={ids.conflicts} className="flex items-center gap-2 pb-2 text-small">
            <input id={ids.conflicts} type="checkbox" checked={conflicts} onChange={(e) => setConflicts(e.target.checked)} />
            Only those that differ from a measure
          </label>
        </div>
        {counts && (
          <p className="text-small text-ink-muted" aria-label="Counts">
            {counts.approved} in use · {counts.conflicts} waiting for your choice · {counts.proposed} can't run
          </p>
        )}
      </div>
      <DataTable
        data={q.data?.items ?? []}
        columns={columns}
        label="Named conditions"
        getRowId={(c) => c.id}
        rowLabel={(c) => c.term}
        loading={q.isLoading}
        error={q.error && <FormAlert error={asApiError(q.error)} />}
        empty={{ title: "No conditions", body: "Conditions come from approved definitions and from the certified count measures of an approved model." }}
        search={(q.data?.items.length ?? 0) > 8}
      />
    </div>
  );
}
