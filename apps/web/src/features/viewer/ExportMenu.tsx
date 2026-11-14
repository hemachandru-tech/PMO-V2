import { useQuery } from "@tanstack/react-query";
import { Download, FileSpreadsheet, FileText, LoaderCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import type { PageFilter } from "../../api/app";
import { api } from "../../api/client";
import { getConfig } from "../../app/config";
import { Alert, FormAlert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "../../components/ui/dropdown-menu";
import { asApiError } from "../../lib/api-errors";

export type ExportFormat = "pdf" | "xlsx";

export interface ExportJob {
  job_id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "expired";
  format: ExportFormat;
  dashboard_id: string;
  file_name: string | null;
  size_bytes: number | null;
  row_count: number | null;
  excluded_widgets: number | null;
  download_url: string | null;
  expires_at: string | null;
  file_expires_at: string | null;
  error: { code: string | null; message: string } | null;
}

const LABEL: Record<ExportFormat, string> = { pdf: "PDF", xlsx: "Excel" };
const DONE = new Set(["succeeded", "failed", "expired"]);

/**
 * Export to PDF or Excel (FEAT-045; US-095): the job runs in the background with the reader's current filters and is
 * re-checked against their access when it runs. The dialog follows the job and offers the signed download link
 * (15 minutes). A notification link (`?export=<job>`) reopens the dialog for that job with a fresh link.
 */
export function ExportMenu({ dashboardId, versionId, filters }: { dashboardId: string; versionId: string | null; filters: PageFilter[] }) {
  const [params, setParams] = useSearchParams();
  const [jobId, setJobId] = useState<string | null>(() => params.get("export"));
  const [starting, setStarting] = useState<ExportFormat | null>(null);
  const [error, setError] = useState<unknown>(null);

  const start = async (format: ExportFormat) => {
    setStarting(format);
    setError(null);
    try {
      const job = await api<ExportJob>("/api/v1/exports", {
        method: "POST",
        body: { dashboard_id: dashboardId, dashboard_version_id: versionId, format, page_filters: filters },
        idempotencyKey: crypto.randomUUID(),
      });
      setJobId(job.job_id);
    } catch (e) {
      setError(e);
    } finally {
      setStarting(null);
    }
  };
  const close = () => {
    setJobId(null);
    setError(null);
    if (params.has("export")) {
      params.delete("export");
      setParams(params, { replace: true });
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="secondary" disabled={starting !== null}>
            {starting ? <LoaderCircle aria-hidden className="animate-spin" /> : <Download aria-hidden />}Export
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => void start("pdf")}><FileText aria-hidden />PDF — every tab</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void start("xlsx")}><FileSpreadsheet aria-hidden />Excel — a sheet per widget</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ExportDialog jobId={jobId} error={error} onClose={close} />
    </>
  );
}

function ExportDialog({ jobId, error, onClose }: { jobId: string | null; error: unknown; onClose: () => void }) {
  const job = useQuery({
    queryKey: ["export", jobId],
    queryFn: () => api<ExportJob>(`/api/v1/exports/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (q) => (q.state.data && DONE.has(q.state.data.status) ? false : 1500),
    retry: false,
  });
  const j = job.data;
  const open = jobId !== null || error !== null;
  const [announce, setAnnounce] = useState("");
  useEffect(() => {
    if (j?.status === "succeeded") setAnnounce(`Your ${LABEL[j.format]} export is ready to download.`);
    else if (j?.status === "failed") setAnnounce("The export didn't finish.");
  }, [j?.status, j?.format]);

  const title = j ? `${LABEL[j.format]} export` : "Export";
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>Built from your data scope and the filters you set. Widgets hidden for your role are left out.</DialogDescription>
        </DialogHeader>
        <p className="sr-only" aria-live="polite">{announce}</p>
        {error ? (
          <FormAlert error={asApiError(error)} />
        ) : job.error ? (
          <FormAlert error={asApiError(job.error)} />
        ) : !j || j.status === "queued" || j.status === "running" ? (
          <p className="flex items-center gap-2 text-ink-muted" aria-busy="true">
            <LoaderCircle aria-hidden className="size-4 animate-spin" />
            {j?.status === "running" ? "Building your file…" : "Waiting to start…"} You can close this; you'll get a notification when it's ready.
          </p>
        ) : j.status === "failed" ? (
          <Alert variant="danger"><p className="text-body-strong">{j.error?.message ?? "The export didn't finish."}</p></Alert>
        ) : j.status === "expired" || !j.download_url ? (
          <Alert variant="warn"><p>This file is no longer kept. Export the dashboard again.</p></Alert>
        ) : (
          <div className="grid gap-2">
            <p className="text-body-strong">{j.file_name}</p>
            <p className="text-small text-ink-muted">
              {j.size_bytes !== null && `${formatBytes(j.size_bytes)} · `}
              {j.row_count !== null && `${j.row_count.toLocaleString()} ${j.row_count === 1 ? "row" : "rows"} · `}
              The link works for 15 minutes; the file is kept for 24 hours.
            </p>
            {j.excluded_widgets ? (
              <p className="text-small text-ink-muted">
                {j.excluded_widgets} {j.excluded_widgets === 1 ? "widget is" : "widgets are"} hidden for your role and not included.
              </p>
            ) : null}
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onClose}>Close</Button>
          {j?.status === "succeeded" && j.download_url && (
            <Button asChild><a href={`${getConfig().apiBaseUrl}${j.download_url}`} download={j.file_name ?? undefined}><Download aria-hidden />Download</a></Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
