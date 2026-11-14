import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Plus, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { importPack, listPacks, type PackListItem } from "../../../api/packs";
import { formatRelative } from "../../../components/format";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { DataTable, type DataColumn } from "../../../components/ui/data-table";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger } from "../../../components/ui/dropdown-menu";
import { asApiError } from "../../../lib/api-errors";
import { CREATE_METHODS } from "./createMethods";
import { PackStatusBadge } from "./tabs/VersionsTab";

/**
 * Admin › Packs (US-232; FEAT-075): the system packs (read-only, clone to edit) and the tenant's own, each with its
 * versions and where it is installed. Create starts a draft (blank today; clone, from a model and AI drafts join the
 * menu with their stories); Import takes a pack folder, zipped.
 */
export function PacksPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["packs"], queryFn: listPacks });
  const [creating, setCreating] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: (f: File) => importPack(f),
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: ["packs"] });
      toast.success(`Imported ${out.title} ${out.version} as a draft${out.skipped.length ? ` (skipped ${out.skipped.join(", ")})` : ""}`);
      nav(`/admin/packs/${out.id}`);
    },
    onError: (e) => toast.error(asApiError(e, "The pack could not be imported.").message),
  });
  const columns = useMemo<DataColumn<PackListItem>[]>(() => [
    { id: "title", header: "Pack", alwaysVisible: true, value: (p) => p.title, cell: (p) => (
      <span className="grid gap-0.5"><span className="font-medium">{p.title}</span><span className="font-mono text-small text-ink-muted">{p.pack_key}</span></span>
    ) },
    { id: "owner", header: "Owner", filter: "select", value: (p) => (p.owner === "system" ? "System" : "Your organisation"),
      cell: (p) => p.owner === "system" ? <Badge variant="outline">System · read-only</Badge> : <Badge>Your organisation</Badge> },
    { id: "version", header: "Latest version", value: (p) => p.latest.version, cell: (p) => (
      <span className="flex items-center gap-2"><span className="font-mono">{p.latest.version}</span><PackStatusBadge status={p.latest.status} review={p.latest.review} /></span>
    ) },
    { id: "contents", header: "Contents", value: (p) => p.latest.counts.measures,
      cell: (p) => `${p.latest.counts.entities} entities, ${p.latest.counts.measures} measures, ${p.latest.counts.dimensions} dimensions` },
    { id: "installed", header: "Installed on", numeric: true, value: (p) => p.installed },
    { id: "changed", header: "Changed", date: true, value: (p) => p.latest.updated_at, cell: (p) => (p.latest.updated_at ? formatRelative(p.latest.updated_at) : "") },
  ], []);
  const method = CREATE_METHODS.find((m) => m.key === creating);
  const Dialog = method?.Dialog;
  return (
    <div className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Packs</h1>
          <p className="max-w-prose text-ink-muted">A pack is a ready-made model: entities, measures, reports and the business knowledge Ask uses. System packs are read-only; clone one to make it yours.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <input ref={file} type="file" accept=".zip,application/zip" className="sr-only" aria-label="Pack zip to import" tabIndex={-1}
                 onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
          <Button variant="secondary" disabled={upload.isPending} onClick={() => file.current?.click()}><Upload aria-hidden />Import</Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button><Plus aria-hidden />Create<ChevronDown aria-hidden /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-72">
              <DropdownMenuLabel>Start a pack</DropdownMenuLabel>
              {CREATE_METHODS.map((m) => (
                <DropdownMenuItem key={m.key} disabled={!m.Dialog} onSelect={() => setCreating(m.key)} className="items-start">
                  <m.icon aria-hidden className="mt-0.5" />
                  <span className="grid gap-0.5">
                    <span>{m.label}{!m.Dialog && m.story && <span className="text-ink-muted"> — comes with {m.story}</span>}</span>
                    <span className="text-small text-ink-muted">{m.description}</span>
                  </span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <DataTable label="Packs" data={q.data?.items ?? []} columns={columns} loading={q.isLoading}
                 error={q.isError ? asApiError(q.error, "Packs could not be loaded.").message : undefined}
                 getRowId={(p) => p.latest.id} rowLabel={(p) => p.title} onRowActivate={(p) => nav(`/admin/packs/${p.latest.id}`)}
                 empty={{ title: "No packs yet", body: "Load the system packs with tela-admin load-packs, or create one." }} />
      {Dialog && <Dialog open onOpenChange={(o) => { if (!o) setCreating(null); }}
                         onCreated={(id, search) => { setCreating(null); void qc.invalidateQueries({ queryKey: ["packs"] }); nav(`/admin/packs/${id}${search ?? ""}`); }} />}
    </div>
  );
}
