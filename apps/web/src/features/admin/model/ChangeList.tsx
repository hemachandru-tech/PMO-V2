import type { DraftImpact } from "../../../api/app";
import { cn } from "../../../lib/utils";

type Changes = DraftImpact["changes"];

const KINDS: { key: keyof Changes; one: string; many: string }[] = [
  { key: "measures", one: "measure", many: "measures" },
  { key: "fields", one: "field", many: "fields" },
  { key: "dimensions", one: "dimension", many: "dimensions" },
  { key: "entities", one: "entity", many: "entities" },
  { key: "links", one: "link", many: "links" },
];

const WHAT: Record<string, string> = {
  rule: "rule", blanks: "blank handling", name: "name", description: "description", label: "name", data_type: "type",
  sensitivity: "sensitivity", blank_handling: "blank handling",
};

const show = (v: unknown) => (v === null || v === undefined || v === "" ? "—" : String(v));

/**
 * A draft's changes against the approved version (US-144), grouped by kind: added, removed and changed, with the
 * before and after of what changed. `compact` lists names only (studio side panel).
 */
export function ChangeList({ changes, compact, empty }: { changes: Changes; compact?: boolean; empty: string }) {
  const groups = KINDS.map((k) => {
    const c = changes[k.key] as { added: { key: string; label: string }[]; removed: { key: string; label: string }[]; changed?: { key: string; label: string; what: string[]; before?: unknown; after?: unknown }[] };
    return { ...k, added: c.added, removed: c.removed, changed: c.changed ?? [] };
  }).filter((g) => g.added.length + g.removed.length + g.changed.length > 0);
  if (groups.length === 0) return <p className="text-small text-ink-muted">{empty}</p>;
  return (
    <ul className="grid gap-3">
      {groups.map((g) => (
        <li key={g.key} className="grid gap-1">
          <h3 className="text-label uppercase text-ink-muted">{g.many}</h3>
          <ul className="grid gap-1 text-small">
            {g.changed.map((c) => (
              <li key={`c-${c.key}`} className="grid gap-0.5">
                <span><Mark kind="changed" /> <span className="text-body-strong">{c.label}</span>: {c.what.map((w) => WHAT[w] ?? w).join(", ")} changed</span>
                {!compact && c.before !== undefined && (
                  <BeforeAfter before={c.before} after={c.after} />
                )}
              </li>
            ))}
            {g.added.map((c) => <li key={`a-${c.key}`}><Mark kind="added" /> {c.label}</li>)}
            {g.removed.map((c) => <li key={`r-${c.key}`}><Mark kind="removed" /> {c.label}</li>)}
          </ul>
        </li>
      ))}
    </ul>
  );
}

function BeforeAfter({ before, after }: { before: unknown; after: unknown }) {
  if (before && typeof before === "object") {
    const b = before as Record<string, unknown>;
    const a = (after ?? {}) as Record<string, unknown>;
    return (
      <span className="grid gap-0.5 pl-5">
        {Object.keys(b).map((k) => (
          <span key={k}>{WHAT[k] ?? k}: <del className="rounded-sm bg-status-danger-soft px-1 text-status-danger">{show(b[k])}</del> → <ins className="rounded-sm bg-status-ok-soft px-1 text-status-ok no-underline">{show(a[k])}</ins></span>
        ))}
      </span>
    );
  }
  return (
    <span className="pl-5">
      <del className="rounded-sm bg-status-danger-soft px-1 text-status-danger">{show(before)}</del> → <ins className="rounded-sm bg-status-ok-soft px-1 text-status-ok no-underline">{show(after)}</ins>
    </span>
  );
}

/** Added, removed or changed, as a word (never colour alone). */
function Mark({ kind }: { kind: "added" | "removed" | "changed" }) {
  return (
    <span className={cn("inline-block min-w-16 rounded-sm px-1 text-center text-small font-medium",
      kind === "added" ? "bg-status-ok-soft text-status-ok" : kind === "removed" ? "bg-status-danger-soft text-status-danger" : "bg-status-warn-soft text-status-warn")}>
      {kind === "added" ? "Added" : kind === "removed" ? "Removed" : "Changed"}
    </span>
  );
}
