import { useEffect, useState } from "react";
import { toast } from "sonner";

import { api, type ApiError } from "../../../api/client";
import type { Branding } from "../../../app/branding";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../../components/ui/card";
import { RadioGroup, RadioGroupItem } from "../../../components/ui/radio-group";
import { DEFAULT_LAYOUT, LAYOUTS, type Layout } from "../../../design/layout";
import { cn } from "../../../lib/utils";
import { asApiError } from "../../../lib/api-errors";

/**
 * Default layout (ENH-021, US-212): the frame and dashboard look everyone gets unless they pick their own in the
 * account menu. Each choice shows a small sketch of its frame.
 */
export function LayoutCard({ branding, onSaved }: { branding: Branding; onSaved: (b: Branding) => void }) {
  const stored = branding.layout ?? DEFAULT_LAYOUT;
  const [layout, setLayout] = useState<Layout>(stored);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setLayout(stored), [branding.version]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setError(null);
    setBusy(true);
    try {
      const out = await api<Branding>("/api/v1/admin/branding/layout", { method: "PUT", body: { layout, expected_version: branding.version } });
      onSaved(out);
      toast.success("Default layout saved. People who picked their own keep it.");
    } catch (e) {
      setError(asApiError(e, "The default layout could not be saved."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Default layout</CardTitle>
        <CardDescription>How the app is framed and how dashboards look for everyone. Each person can still pick their own under Account menu › Layout.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-4" aria-label="Default layout" onSubmit={(e) => { e.preventDefault(); if (layout !== stored) void save(); }}>
          <RadioGroup value={layout} onValueChange={(v) => setLayout(v as Layout)} aria-label="Default layout" className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {LAYOUTS.map((l) => (
              <label key={l.id} htmlFor={`layout-${l.id}`} className="grid cursor-pointer gap-3 rounded-md border border-line p-3 has-[[data-state=checked]]:border-accent">
                <Sketch layout={l.id} />
                <span className="flex items-start gap-3">
                  <RadioGroupItem id={`layout-${l.id}`} value={l.id} className="mt-0.5" />
                  <span className="grid gap-0.5">
                    <span className="font-medium">{l.label}{l.id === DEFAULT_LAYOUT && <span className="font-normal text-ink-muted"> · default</span>}</span>
                    <span className="text-small text-ink-muted">{l.note}</span>
                  </span>
                </span>
              </label>
            ))}
          </RadioGroup>
          {error && <FormAlert error={error} />}
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={busy || layout === stored}>{busy ? "Saving…" : "Save default layout"}</Button>
            {layout !== stored && <Button type="button" variant="ghost" onClick={() => setLayout(stored)}>Discard</Button>}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

/** A wireframe of the layout's frame: where the navigation sits and how the figure cards look. */
function Sketch({ layout }: { layout: Layout }) {
  if (layout === "aurora") return <AuroraSketch />;
  const tile = cn("h-6 rounded-sm", layout === "command" ? "rounded-md bg-accent-soft" : "border border-line bg-surface-raised");
  return (
    <div aria-hidden className="flex h-28 overflow-hidden rounded-md border border-line bg-surface">
      {layout === "classic" && <div className="w-12 shrink-0 border-r border-line bg-surface-sunken" />}
      {layout === "command" && <div className="w-4 shrink-0 bg-[#101a23]" />}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className={cn("h-4 shrink-0 border-b border-line", layout === "executive" ? "bg-accent-strong" : "bg-surface-raised")} />
        {layout === "executive" && <div className="h-8 shrink-0 bg-accent-strong" />}
        <div className={cn("grid grid-cols-3 gap-1.5 p-2", layout === "executive" && "-mt-4")}>
          <div className={tile} /><div className={tile} /><div className={tile} />
          <div className="col-span-3 h-8 rounded-sm border border-line bg-surface-raised" />
        </div>
      </div>
    </div>
  );
}

/** Aurora (ENH-027): a floating sidebar card, a hero band and rounded tiles with coloured edges. */
function AuroraSketch() {
  const edges = ["border-t-vivid-1", "border-t-vivid-4", "border-t-vivid-2"];
  return (
    <div aria-hidden className="flex h-28 gap-1.5 overflow-hidden rounded-md border border-line bg-surface p-1.5">
      <div className="grid w-10 shrink-0 content-start gap-1 rounded-lg bg-surface-raised p-1 shadow-sm">
        <span className="h-2 rounded-sm bg-vivid-1" /><span className="h-2 rounded-sm bg-vivid-4" /><span className="h-2 rounded-sm bg-vivid-2" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="h-8 shrink-0 rounded-lg bg-gradient-to-br from-accent-strong to-vivid-4" />
        <div className="grid grid-cols-3 gap-1.5">
          {edges.map((e) => <div key={e} className={cn("h-6 rounded-lg border-t-4 bg-surface-raised shadow-sm", e)} />)}
          <div className="col-span-3 h-8 rounded-lg bg-surface-raised shadow-sm" />
        </div>
      </div>
    </div>
  );
}
