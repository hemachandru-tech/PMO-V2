import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, ListChecks, Upload } from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";

import { ApiError } from "../../../api/client";
import { packReportsKey } from "../../../api/packReports";
import { checkBundle, getBundle, importBundle, type BundleCheck, type BundleMode, type BundlePart } from "../../../api/packs";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { Label } from "../../../components/ui/label";
import { RadioGroup, RadioGroupItem } from "../../../components/ui/radio-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../../components/ui/table";
import { Textarea } from "../../../components/ui/textarea";
import { asApiError } from "../../../lib/api-errors";
import { packKey, usePack } from "./PackContext";

const PARTS: [BundlePart, string][] = [["measures", "Measures"], ["dimensions", "Dimensions"], ["reports", "Report blueprints"], ["tones", "Figure tones"], ["dashboards", "Starter dashboards"]];

export const TEMPLATE = `# Every part is optional; each item is written as in the pack folder.
# measures and dimensions: manifest.yaml, matched by key
measures:
  - key: gulf_projects
    label: Projects in the Gulf
    entity: projects
    plain_rule: Projects in KSA or the UAE.
    expression: {agg: count, where: {field: region, op: in, values: [KSA, GCC - UAE]}}
dimensions:
  - key: region
    label: Region
    entity: projects
    field: region
# reports: report blueprints (report_blueprints.yaml), matched by id
reports:
  - id: gulf_overview
    title: Gulf overview
    tab: Gulf
    dashboard: gulf
    question: How are the Gulf's projects doing?
    population: Projects in KSA and the UAE.
    triggers: [gulf overview, gulf projects]
    kpis: [gulf_projects]
    footnote: Counts every project in KSA or the UAE.
# dashboards: manifest.yaml; reports sets the tabs in order, filters are dimension keys
dashboards:
  gulf:
    title: Gulf
    reports: [gulf_overview]
    filters: [region]
`;

function save(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/yaml" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

const checksOf = (e: unknown): BundleCheck | null => {
  const d = e instanceof ApiError ? (e.details as Partial<BundleCheck>) : null;
  return d && Array.isArray(d.errors) ? { valid: false, errors: d.errors, warnings: d.warnings ?? [], changes: d.changes ?? {} } : null;
};

/**
 * Import YAML (Pack Studio): the draft's measures, dimensions, report blueprints and starter dashboards in one file.
 * Merge adds new items and replaces those with the same key; Replace makes each part in the file the whole part.
 * Check shows what changes in each part before anything is saved; an import with any error saves nothing.
 */
export function ImportYamlDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { pack, etag, readOnly } = usePack();
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [mode, setMode] = useState<BundleMode>("merge");
  const [checked, setChecked] = useState<{ for: string; out: BundleCheck } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const textId = useId();
  const current = `${mode}\n${text}`;
  const edit = (t: string) => { setText(t); setChecked(null); };

  const load = useMutation({ mutationFn: () => getBundle(pack.id), onSuccess: (out) => edit(out.yaml) });
  const download = useMutation({ mutationFn: () => getBundle(pack.id), onSuccess: (out) => save(out.file_name, out.yaml) });
  const check = useMutation({
    mutationFn: () => checkBundle(pack.id, text, mode),
    onSuccess: (out) => setChecked({ for: current, out }),
    onError: (e) => { const c = checksOf(e); setChecked(c ? { for: current, out: c } : null); },
  });
  const run = useMutation({
    mutationFn: () => importBundle(pack.id, etag, text, mode),
    onSuccess: (out) => {
      qc.setQueryData(packKey(pack.id), { pack: { ...pack, ...out.pack }, etag: out.etag });
      void qc.invalidateQueries({ queryKey: ["packs"] });
      void qc.invalidateQueries({ queryKey: packReportsKey(pack.id) });
      toast.success("The YAML is imported into the draft.");
      edit("");
      onOpenChange(false);
    },
    onError: (e) => { const c = checksOf(e); if (c) setChecked({ for: current, out: c }); },
  });

  const out = checked?.for === current ? checked.out : null;
  const failure = (check.error && !checksOf(check.error) ? check.error : null) ?? (run.error && !checksOf(run.error) ? run.error : null) ?? load.error ?? download.error;
  const rows = out ? PARTS.filter(([p]) => out.changes[p]) : [];
  const nothing = rows.every(([p]) => { const c = out!.changes[p]!; return !c.added.length && !c.updated.length && !c.removed.length; });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{readOnly ? "Pack YAML" : "Import YAML"}</DialogTitle>
          <DialogDescription>
            Measures, dimensions, report blueprints and starter dashboards in one file, each written as in the pack folder.
            {readOnly ? " This version can't change: download the file to use it in another pack." : " Check it to see what changes, then import it into the draft."}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap gap-2">
          {!readOnly && <Button size="sm" variant="secondary" disabled={load.isPending} onClick={() => load.mutate()}>Start from this pack</Button>}
          {!readOnly && <Button size="sm" variant="secondary" onClick={() => edit(TEMPLATE)}>Start from the template</Button>}
          {!readOnly && <Button size="sm" variant="secondary" onClick={() => file.current?.click()}><FileUp aria-hidden />Choose a file</Button>}
          <Button size="sm" variant="ghost" disabled={download.isPending} onClick={() => download.mutate()}><Download aria-hidden />Download this pack's YAML</Button>
          <input ref={file} type="file" accept=".yaml,.yml,text/yaml,application/yaml" className="hidden" aria-label="YAML file"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) void f.text().then(edit); e.target.value = ""; }} />
        </div>
        {!readOnly && (
          <>
            <div className="grid gap-1">
              <Label htmlFor={textId}>YAML</Label>
              <Textarea id={textId} value={text} spellCheck={false} rows={16} className="font-mono text-small"
                        placeholder="measures: …  dimensions: …  reports: …  dashboards: …" onChange={(e) => edit(e.target.value)} />
            </div>
            <RadioGroup aria-label="How the file is applied" value={mode} onValueChange={(v) => { setMode(v as BundleMode); setChecked(null); }}>
              <Label className="flex items-start gap-2 font-normal"><RadioGroupItem value="merge" className="mt-1" />
                <span><span className="font-medium">Merge</span>: add new items and replace those with the same key; the rest stay.</span></Label>
              <Label className="flex items-start gap-2 font-normal"><RadioGroupItem value="replace" className="mt-1" />
                <span><span className="font-medium">Replace</span>: each part in the file becomes the whole part; parts left out of the file stay.</span></Label>
            </RadioGroup>
            {failure && <FormAlert error={asApiError(failure)} />}
            {out && (
              <section aria-label="Check result" className="grid gap-2">
                {rows.length > 0 && (
                  <Table aria-label="What changes">
                    <TableHeader><TableRow><TableHead>Part</TableHead><TableHead>Added</TableHead><TableHead>Replaced</TableHead><TableHead>Removed</TableHead><TableHead>Same</TableHead></TableRow></TableHeader>
                    <TableBody>
                      {rows.map(([p, label]) => {
                        const c = out.changes[p]!;
                        return (
                          <TableRow key={p}>
                            <TableCell className="font-medium">{label}</TableCell>
                            <TableCell title={c.added.join(", ")}>{c.added.length}</TableCell>
                            <TableCell title={c.updated.join(", ")}>{c.updated.length}</TableCell>
                            <TableCell title={c.removed.join(", ")} className={c.removed.length ? "text-status-danger" : undefined}>{c.removed.length}</TableCell>
                            <TableCell>{c.unchanged}</TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                )}
                {out.errors.length > 0 ? (
                  <Alert>
                    <span className="font-medium">{out.errors.length === 1 ? "1 error" : `${out.errors.length} errors`}: nothing is imported until they are fixed.</span>
                    <ul aria-label="Errors" className="mt-1 grid gap-0.5 text-small">
                      {out.errors.slice(0, 20).map((e, i) => <li key={i}><span className="font-mono">{e.path || "file"}</span>: {e.message}</li>)}
                    </ul>
                  </Alert>
                ) : (
                  <p role="status" className="text-small">
                    {nothing ? "The file changes nothing." : "Ready to import."}
                    {out.warnings.length > 0 && ` ${out.warnings.length === 1 ? "1 warning" : `${out.warnings.length} warnings`}: the import goes ahead.`}
                  </p>
                )}
                {out.warnings.length > 0 && (
                  <ul aria-label="Warnings" className="grid gap-0.5 text-small text-ink-muted">
                    {out.warnings.slice(0, 20).map((w, i) => <li key={i}><span className="font-mono">{w.path || "file"}</span>: {w.message}</li>)}
                    {out.warnings.length > 20 && <li>…and {out.warnings.length - 20} more (see Overview after importing).</li>}
                  </ul>
                )}
              </section>
            )}
          </>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
          {!readOnly && (
            <>
              <Button variant="secondary" disabled={!text.trim() || check.isPending} onClick={() => check.mutate()}><ListChecks aria-hidden />{check.isPending ? "Checking…" : "Check"}</Button>
              <Button disabled={!out?.valid || nothing || run.isPending} onClick={() => run.mutate()}><Upload aria-hidden />{run.isPending ? "Importing…" : "Import"}</Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
