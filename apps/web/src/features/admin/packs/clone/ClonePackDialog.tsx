import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api } from "../../../../api/client";
import type { PackMapping } from "../../../../api/app";
import { getPack, listPacks } from "../../../../api/packs";
import { Alert, FormAlert } from "../../../../components/ui/alert";
import { Button } from "../../../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../../components/ui/dialog";
import { Input } from "../../../../components/ui/input";
import { Label } from "../../../../components/ui/label";
import { NativeSelect } from "../../../../components/ui/native-select";
import { Skeleton } from "../../../../components/ui/skeleton";
import { Steps } from "../../../../components/ui/steps";
import { asApiError } from "../../../../lib/api-errors";
import { PackMappingStep, problemsOf } from "../../starter/PackMappingStep";
import type { CreateDialogProps } from "../createMethods";
import { keyFrom } from "../CreatePackDialog";
import { aiSuggestions, createClone, previewRepoint, type Renames, type UnmappedItem, type UnmappedKind } from "./api";
import { RenameEditor } from "./RenameEditor";

const STEPS = [{ key: "source", label: "Template and source" }, { key: "map", label: "Match fields" }, { key: "review", label: "Unmapped and names" }] as const;
type Step = (typeof STEPS)[number]["key"];
const KIND: Record<UnmappedKind, string> = { entity: "Tables", field: "Fields", measure: "Measures", dimension: "Dimensions", report: "Reports" };
const KEY = /^[a-z][a-z0-9_]{1,40}$/;
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * Create → Clone a pack (US-235): a template such as PMO, re-pointed to a connection's schema. The mapping step is
 * the starter pack's (`PackMappingStep`: names, value suggestions, AI suggestions on a released pack); the names the
 * Admin confirms are added to the clone's candidates so it matches this source by name next time. Items the source
 * can't serve are listed by kind with the reason: each is kept as Unavailable or removed (with what reads it).
 * Entity, field and measure keys and labels can be renamed on the way; every reference in the pack follows.
 */
export function ClonePackDialog({ open, onOpenChange, onCreated }: CreateDialogProps) {
  const [step, setStep] = useState<Step>("source");
  const [packId, setPackId] = useState("");
  const [connectionId, setConnectionId] = useState("");
  const [title, setTitle] = useState("");
  const [key, setKey] = useState("");
  const [mapping, setMapping] = useState<PackMapping | null>(null); // null: the mapping saved for this pack and connection
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  const [renames, setRenames] = useState<Renames>({ entities: {}, fields: {}, measures: {} });

  const packs = useQuery({ queryKey: ["packs"], queryFn: listPacks, enabled: open });
  const connections = useQuery({ queryKey: ["connections"], queryFn: () => api<{ items: { id: string; name: string }[] }>("/api/v1/connections"), enabled: open });
  const templates = (packs.data?.items ?? []).flatMap((p) => p.versions.length ? p.versions : [p.latest]).filter((v) => v.counts.entities > 0);
  const template = templates.find((v) => v.id === packId);
  const preview = useQuery({
    queryKey: ["pack-repoint", packId, connectionId, mapping], queryFn: () => previewRepoint(packId, connectionId, mapping),
    enabled: open && step !== "source" && !!packId && !!connectionId, placeholderData: keepPreviousData, retry: false, gcTime: 0,
  });
  const detail = useQuery({ queryKey: ["pack", packId], queryFn: () => getPack(packId), enabled: open && step === "review" && !!packId });
  const p = preview.data;
  const ai = useMutation({ mutationFn: () => aiSuggestions(packId, connectionId, mapping ?? p!.mapping) });
  const create = useMutation({
    mutationFn: () => createClone({ pack_key: key, title, from_pack_id: packId, connection_id: connectionId, mapping: mapping ?? p!.mapping, remove: [...removed], renames }),
    onSuccess: (out) => onCreated(out.id),
  });
  const blocking = p?.checks.blocking ?? [];
  const grouped = useMemo(() => {
    const out = new Map<UnmappedKind, UnmappedItem[]>();
    for (const u of p?.unmapped ?? []) out.set(u.kind, [...(out.get(u.kind) ?? []), u]);
    return out;
  }, [p]);
  // What the removals take with them, so the rename list doesn't offer it.
  const gone = useMemo(() => new Set((p?.unmapped ?? []).filter((u) => removed.has(u.ref)).flatMap((u) => [u.ref, ...(u.removes_also ?? [])])), [p, removed]);

  const sourceDone = !!packId && !!connectionId && !!title.trim() && KEY.test(key);
  const remap = (m: PackMapping) => { ai.reset(); setMapping(m); setRemoved(new Set()); };
  const pickTemplate = (id: string) => {
    const v = templates.find((t) => t.id === id);
    setPackId(id); setMapping(null); setRemoved(new Set()); setRenames({ entities: {}, fields: {}, measures: {} });
    if (v && !title) { setTitle(`${v.title} for my source`); setKey(keyFrom(`${v.pack_key}_mine`)); }
  };
  const reachable = (i: number) => i === 0 || (sourceDone && (i === 1 || (!!p && blocking.length === 0)));
  const toggle = (ref: string, remove: boolean) => setRemoved((s) => { const n = new Set(s); if (remove) n.add(ref); else n.delete(ref); return n; });
  const aiNote = ai.error ? `The AI could not be asked: ${asApiError(ai.error).message}` : ai.data && !ai.data.available ? ai.data.message : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col sm:max-w-[min(80rem,calc(100%-4rem))]">
        <DialogHeader>
          <DialogTitle>Clone a pack onto your source</DialogTitle>
          <DialogDescription>Measures, reports and dashboards come ready-made; you check how the pack's fields match your tables. The clone is a draft you can change.</DialogDescription>
        </DialogHeader>
        <Steps label="Clone steps" steps={STEPS} current={step} reachable={reachable} onSelect={setStep} />
        <div className="grid min-h-0 flex-1 content-start gap-3 overflow-auto pr-1" aria-busy={preview.isFetching}>
          {step === "source" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1">
                <Label htmlFor="clone-template">Template</Label>
                <NativeSelect id="clone-template" value={packId} onChange={(e) => pickTemplate(e.target.value)}>
                  <option value="">Choose a pack</option>
                  {templates.map((v) => <option key={v.id} value={v.id}>{v.title} {v.version}{v.status === "draft" ? " (draft)" : ""}</option>)}
                </NativeSelect>
              </div>
              <div className="grid gap-1">
                <Label htmlFor="clone-connection">Your source</Label>
                <NativeSelect id="clone-connection" value={connectionId} onChange={(e) => { setConnectionId(e.target.value); setMapping(null); setRemoved(new Set()); }}>
                  <option value="">Choose a connection</option>
                  {(connections.data?.items ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </NativeSelect>
              </div>
              <div className="grid gap-1">
                <Label htmlFor="clone-title">Title</Label>
                <Input id="clone-title" value={title} maxLength={120} onChange={(e) => setTitle(e.target.value)} />
              </div>
              <div className="grid gap-1">
                <Label htmlFor="clone-key">Key</Label>
                <Input id="clone-key" className="font-mono" value={key} onChange={(e) => setKey(e.target.value)} aria-invalid={!!key && !KEY.test(key)} />
                <p className="text-small text-ink-muted">{key && !KEY.test(key) ? "Use 2 to 41 lower-case letters, digits or _, starting with a letter." : "Names the pack everywhere and can't change later."}</p>
              </div>
            </div>
          )}
          {step !== "source" && preview.error && <FormAlert error={asApiError(preview.error)} />}
          {step !== "source" && preview.isLoading && <Skeleton className="h-32" />}
          {step === "map" && p && (
            <>
              <p><strong>{p.title} {p.version}</strong> · {Math.round(p.confidence * 100)}% of its fields found on this source{p.candidates.length ? ` · ${plural(p.candidates.length, "name")} of yours will be added to the clone` : ""}</p>
              {!p.matched && <Alert><p>This source uses other names than the pack. Choose a column for each required field, or mark it as not in this source.</p></Alert>}
              {blocking.length > 0 && (
                <Alert variant="danger" data-testid="clone-blocking">
                  <p className="text-body-strong">Fix the mapping before going on</p>
                  <ul className="list-disc pl-5 text-small">{blocking.map((b) => <li key={b.ref + b.message}>{b.message}</li>)}</ul>
                </Alert>
              )}
              <PackMappingStep connectionId={connectionId} objects={p.mapping_step.objects} entities={p.mapping_step.entities}
                mapping={mapping ?? p.mapping} problems={problemsOf(p.checks)} onChange={remap}
                ai={{ available: !!p.checks.ai_available, pending: ai.isPending, suggestions: ai.data?.suggestions ?? {}, note: aiNote, onAsk: () => ai.mutate() }} />
            </>
          )}
          {step === "review" && p && (
            <>
              <section className="grid gap-2" aria-labelledby="clone-unmapped" data-testid="clone-unmapped">
                <h3 id="clone-unmapped" className="text-body-strong">Not on this source</h3>
                {p.unmapped.length === 0 ? <p className="text-small text-ink-muted">Everything in the pack is found on this source.</p> : (
                  <p className="text-small text-ink-muted">Keep an item and it stays in the pack, shown as unavailable where the data is missing; remove it and it leaves the clone with what reads it.</p>
                )}
                {[...grouped].map(([kind, items]) => (
                  <details key={kind} open={kind === "entity" || kind === "field"} className="rounded-md border border-line p-2">
                    <summary className="cursor-pointer text-body-strong">{KIND[kind]} ({items.length})</summary>
                    <ul className="mt-2 grid gap-2">
                      {items.map((u) => {
                        const swept = !removed.has(u.ref) && gone.has(u.ref);
                        return (
                          <li key={u.ref} className="grid gap-1 sm:grid-cols-[1fr_13rem] sm:items-center" data-testid={`unmapped-${u.ref}`}>
                            <div className="text-small">
                              <p className="text-body-strong">{u.title}</p>
                              <p className="text-ink-muted">{u.reason}</p>
                              {removed.has(u.ref) && !!u.removes_also?.length && <p className="text-status-warn">Also removes {plural(u.removes_also.length, "item")}: {u.removes_also.map((r) => r.split(":")[1]).join(", ")}.</p>}
                            </div>
                            {swept ? <p className="text-small text-ink-muted">Removed with another item</p> : (
                              <NativeSelect aria-label={`What to do with ${u.title}`} value={removed.has(u.ref) ? "remove" : "keep"} onChange={(e) => toggle(u.ref, e.target.value === "remove")}>
                                <option value="keep">Keep as unavailable</option>
                                <option value="remove">Remove from the clone</option>
                              </NativeSelect>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  </details>
                ))}
              </section>
              {detail.data && <RenameEditor manifest={detail.data.pack.manifest} gone={gone} renames={renames} onChange={setRenames} />}
            </>
          )}
        </div>
        {create.error && <FormAlert error={asApiError(create.error)} />}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          {step === "source" && <Button disabled={!sourceDone} onClick={() => setStep("map")}>Match fields</Button>}
          {step === "map" && <Button disabled={!p || blocking.length > 0 || preview.isFetching} onClick={() => setStep("review")}>Next</Button>}
          {step === "review" && <Button disabled={!p || create.isPending || !template} onClick={() => create.mutate()}>{create.isPending ? "Cloning…" : "Create the clone"}</Button>}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
