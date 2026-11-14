import { useState } from "react";

import type { PackManifest } from "../../../../api/packs";
import { Button } from "../../../../components/ui/button";
import { Input } from "../../../../components/ui/input";
import { NativeSelect } from "../../../../components/ui/native-select";
import type { Renames } from "./api";

type Kind = "entities" | "fields" | "measures";
const KINDS: { key: Kind; label: string; ref: string }[] = [
  { key: "entities", label: "Table", ref: "entity" }, { key: "fields", label: "Field", ref: "field" }, { key: "measures", label: "Measure", ref: "measure" },
];
const KEY = /^[a-z][a-z0-9_]{1,40}$/;

/** The pack's items of a kind as [key, label], keyed as the API renames them (fields by `entity.field`). */
function itemsOf(manifest: PackManifest, kind: Kind): [string, string][] {
  if (kind === "entities") return manifest.entities.map((e) => [e.key, e.label]);
  if (kind === "measures") return manifest.measures.map((m) => [m.key, m.label]);
  return manifest.entities.flatMap((e) => e.fields.map((f): [string, string] => [`${e.key}.${f.key}`, `${e.label}: ${f.label}`]));
}

/**
 * Rename on the way (US-235): a table, field or measure gets a new key and/or label in the clone; the API rewrites every
 * reference (measures, dimensions, links, blueprints, patterns, recipes, glossary). Items being removed aren't offered.
 */
export function RenameEditor({ manifest, gone, renames, onChange }: {
  manifest: PackManifest; gone: Set<string>; renames: Renames; onChange: (r: Renames) => void;
}) {
  const [kind, setKind] = useState<Kind>("measures");
  const [item, setItem] = useState("");
  const [key, setKey] = useState("");
  const [label, setLabel] = useState("");
  const ref = KINDS.find((k) => k.key === kind)!.ref;
  const options = itemsOf(manifest, kind).filter(([k]) => !gone.has(`${ref}:${k}`) && !(kind === "fields" && gone.has(`entity:${k.split(".")[0]}`)));
  const current = options.find(([k]) => k === item);
  const ownKey = kind === "fields" ? item.split(".")[1] : item;
  const keyOk = !key || (KEY.test(key) && key !== ownKey);
  const add = () => {
    onChange({ ...renames, [kind]: { ...renames[kind], [item]: { ...(key ? { key } : {}), ...(label.trim() ? { label: label.trim() } : {}) } } });
    setItem(""); setKey(""); setLabel("");
  };
  const pending = KINDS.flatMap((k) => Object.entries(renames[k.key]).map(([from, to]) => ({ kind: k, from, to })));
  return (
    <section className="grid gap-2" aria-labelledby="clone-renames" data-testid="clone-renames">
      <h3 id="clone-renames" className="text-body-strong">Rename (optional)</h3>
      <p className="text-small text-ink-muted">Use your own names for tables, fields and measures. Every measure, dimension, link, report, pattern, recipe and glossary term that names one follows.</p>
      <div className="grid gap-2 sm:grid-cols-[8rem_minmax(0,1fr)_12rem_12rem_auto] sm:items-center">
        <NativeSelect aria-label="What to rename" value={kind} onChange={(e) => { setKind(e.target.value as Kind); setItem(""); }}>
          {KINDS.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Item to rename" value={item} onChange={(e) => setItem(e.target.value)}>
          <option value="">Choose…</option>
          {options.map(([k, l]) => <option key={k} value={k}>{l} ({k})</option>)}
        </NativeSelect>
        <Input aria-label="New key" className="font-mono" placeholder={current ? ownKey : "New key"} value={key} onChange={(e) => setKey(e.target.value)} aria-invalid={!keyOk} />
        <Input aria-label="New label" placeholder={current?.[1] ?? "New label"} value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} />
        <Button variant="secondary" size="sm" disabled={!item || !keyOk || (!key && !label.trim())} onClick={add}>Add</Button>
      </div>
      {!keyOk && <p className="text-small text-status-danger">Use 2 to 41 lower-case letters, digits or _, starting with a letter.</p>}
      {pending.length > 0 && (
        <ul className="grid gap-1 text-small">
          {pending.map(({ kind: k, from, to }) => (
            <li key={`${k.key}:${from}`} className="flex flex-wrap items-center gap-2">
              <span>{k.label}</span><span className="font-mono">{from}</span>→
              {to.key && <span className="font-mono">{k.key === "fields" ? `${from.split(".")[0]}.${to.key}` : to.key}</span>}
              {to.label && <span>“{to.label}”</span>}
              <Button variant="ghost" size="sm" className="ml-auto" aria-label={`Undo rename of ${from}`}
                onClick={() => { const next = { ...renames[k.key] }; delete next[from]; onChange({ ...renames, [k.key]: next }); }}>Undo</Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
