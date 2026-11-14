import { Check, Pencil, X } from "lucide-react";
import { useState } from "react";

import { checkAuthorItems, type AuthorProblem, type AuthorProposal } from "../../../../api/packAuthor";
import { Badge } from "../../../../components/ui/badge";
import { Button } from "../../../../components/ui/button";
import { StatusBadge } from "../../../../components/ui/status-badge";
import { Textarea } from "../../../../components/ui/textarea";
import { asApiError, friendlyMessage } from "../../../../lib/api-errors";
import { fromYaml, toYaml } from "../yaml";

const str = (v: unknown) => (typeof v === "string" ? v : "");
const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);

/** One line saying what the item is, in the words of its kind. */
export function summary(p: Pick<AuthorProposal, "kind" | "item">): string {
  const i = p.item;
  switch (p.kind) {
    case "entity": return `${list((i.object_candidates as unknown[]) ?? []).join(", ")} · ${(i.fields as unknown[] | undefined)?.length ?? 0} fields · key ${list(i.primary_key).join(", ")}`;
    case "link": return `${str(i.from_entity)}.${list(i.from_fields).join(", ")} → ${str(i.to_entity)}.${list(i.to_fields).join(", ")} (${str(i.cardinality).replace(/_/g, " ")})`;
    case "measure": return str(i.plain_rule);
    case "dimension": return `${str(i.entity)}.${str(i.field)}${i.is_temporal ? " · over time" : ""}${i.bands ? ` · ${(i.bands as unknown[]).length} bands` : ""}${list(i.answers).length ? ` · answers ${list(i.answers).join(", ").replace(/_/g, " ")}` : ""}`;
    case "synonyms": return list(i.synonyms).join(", ");
    case "glossary": return `${str(i.term)}: ${str(i.means)}`;
    case "value_synonyms": return `${str(i.value)} = ${list(i.synonyms).join(", ")}`;
    case "dashboard_rules": return str(i.rule) || `Don't say ${list(i.words).join(", ")}; say ${str(i.use)}`;
    default: return str(i.reason) || str(i.guidance) || str(i.question);
  }
}

function countText(p: Pick<AuthorProposal, "checks">): string | null {
  const { count, count_of } = p.checks;
  if (count === null || count === undefined) return null;
  const n = typeof count === "number" ? count.toLocaleString() : String(count);
  return count_of === "value" ? `On this source: ${n}` : `${n} ${count_of ?? ""}`.trim();
}

function Problems({ items, tone }: { items: AuthorProblem[]; tone: "warn" | "danger" }) {
  if (!items.length) return null;
  return (
    <ul className={`grid gap-0.5 text-small ${tone === "warn" ? "text-status-warn" : "text-status-danger"}`}>
      {items.map((w, i) => <li key={i}>{w.message}</li>)}
    </ul>
  );
}

/** A proposal: "AI proposed" or "Rules proposed", its checks and count; Accept, Edit (YAML, checked again before
 * it can be accepted) or Reject. */
export function ProposalCard({ packId, connectionId, proposal, accepted, busy, onAccept, onReject, onChange }: {
  packId: string;
  connectionId: string;
  proposal: AuthorProposal;
  accepted: boolean;
  busy: boolean;
  onAccept: (item: Record<string, unknown>) => void;
  onReject: () => void;
  /** The item as edited and checked again. */
  onChange?: (p: AuthorProposal) => void;
}) {
  const [shown, setShown] = useState(proposal);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [errors, setErrors] = useState<AuthorProblem[]>([]);
  const [checking, setChecking] = useState(false);

  async function check() {
    const parsed = fromYaml(text);
    if (!parsed.ok) { setErrors([{ path: "", message: parsed.error }]); return; }
    setChecking(true);
    try {
      const out = await checkAuthorItems(packId, connectionId, proposal.kind, [parsed.value]);
      const r = out.items[0]!;
      setErrors(r.errors);
      if (!r.errors.length) {
        const next = { ...shown, item: r.item, title: typeof r.item.label === "string" ? r.item.label : shown.title, checks: { errors: [], warnings: r.warnings, count: r.count, count_of: r.count_of } };
        setShown(next);
        onChange?.(next);
        setEditing(false);
      }
    } catch (e) {
      setErrors([{ path: "", message: friendlyMessage(asApiError(e, "The item could not be checked.")) }]);
    } finally {
      setChecking(false);
    }
  }

  const count = countText(shown);
  return (
    <article aria-label={shown.title || shown.key} className="grid gap-1.5 rounded-md border border-line p-3">
      <header className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{shown.title || shown.key}</span>
        <span className="font-mono text-small text-ink-muted">{shown.key}</span>
        <Badge variant={shown.source === "ai" ? "certified" : "neutral"}>{shown.source === "ai" ? "AI proposed" : "Rules proposed"}</Badge>
        {count && <span className="text-small text-ink-muted" data-testid="count">{count}</span>}
        {accepted && <StatusBadge status="ok">Accepted</StatusBadge>}
      </header>
      <p className="text-small">{summary(shown)}</p>
      {shown.reason && <p className="text-small text-ink-muted">{shown.reason}</p>}
      <Problems items={shown.checks.warnings} tone="warn" />
      {editing && (
        <div className="grid gap-1.5">
          <Textarea aria-label={`YAML of ${shown.title || shown.key}`} className="font-mono text-small" rows={10} value={text} onChange={(e) => setText(e.target.value)} />
          <Problems items={errors} tone="danger" />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => void check()} disabled={checking}>{checking ? "Checking…" : "Check"}</Button>
            <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setErrors([]); }}>Cancel</Button>
          </div>
        </div>
      )}
      {!accepted && !editing && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => onAccept(shown.item)} disabled={busy}><Check aria-hidden />Accept</Button>
          <Button size="sm" variant="secondary" onClick={() => { setText(toYaml(shown.item)); setErrors([]); setEditing(true); }}><Pencil aria-hidden />Edit</Button>
          <Button size="sm" variant="ghost" onClick={onReject}><X aria-hidden />Reject</Button>
        </div>
      )}
    </article>
  );
}
