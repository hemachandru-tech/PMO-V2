import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "../../../api/client";
import type { AskMode, AskModeChoice, AskModeInfo, AskModeScore } from "../../../api/app";
import { formatAsOf } from "../../../components/format";
import { Alert, FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../../components/ui/dialog";
import { RadioGroup, RadioGroupItem } from "../../../components/ui/radio-group";
import { Skeleton } from "../../../components/ui/skeleton";
import { asApiError } from "../../../lib/api-errors";
import { ASK_MODE_LABELS } from "../../ask/askModes";

export { ASK_MODE_LABELS };

/**
 * Connections › connection › Ask (ENH-028, US-256; DEC-29): how Ask answers questions about this source. Four
 * choices with one line each and each one's latest gold score; a mode that can't be chosen says why.
 */
export function AskModeDialog({ connectionId, connectionName, open, onClose }: {
  connectionId: string; connectionName: string; open: boolean; onClose: () => void;
}) {
  const qc = useQueryClient();
  const key = ["connections", connectionId, "ask-mode"];
  const info = useQuery({ queryKey: key, queryFn: () => api<AskModeInfo>(`/api/v1/connections/${connectionId}/ask-mode`), enabled: open });
  const [mode, setMode] = useState<AskMode | null>(null);
  useEffect(() => { if (open && info.data) setMode(info.data.mode); }, [open, info.data]);
  const save = useMutation({
    mutationFn: (m: AskMode) => api<AskModeInfo>(`/api/v1/connections/${connectionId}/ask-mode`, { method: "PUT", body: { mode: m } }),
    onSuccess: (got) => {
      qc.setQueryData(key, got);
      void qc.invalidateQueries({ queryKey: ["connections"], exact: true });
      onClose();
    },
  });
  const stored = info.data?.mode;
  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) { save.reset(); onClose(); } }}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>How Ask answers for {connectionName}</DialogTitle>
          <DialogDescription>Choose the method Ask uses for questions about this source. Each choice shows its latest test on the gold questions.</DialogDescription>
        </DialogHeader>
        {info.isLoading && <Skeleton className="h-48" />}
        {info.error && <FormAlert error={asApiError(info.error)} />}
        {info.data && (
          <form id="ask-mode-form" className="grid gap-3" onSubmit={(e) => { e.preventDefault(); if (mode) save.mutate(mode); }}>
            {info.data.fallback_reason && <Alert><p>{info.data.fallback_reason}</p></Alert>}
            <RadioGroup value={mode ?? undefined} onValueChange={(v) => setMode(v as AskMode)} aria-label="Ask answer mode" className="grid gap-2">
              {info.data.choices.map((c) => <Choice key={c.mode} c={c} current={c.mode === stored} />)}
            </RadioGroup>
            {mode === "direct_sql" && stored !== "direct_sql" && (
              <Alert><p>Direct SQL answers are never certified: each one shows its SQL and the Custom SQL badge, and can't go on a published dashboard until certified.</p></Alert>
            )}
            <FormAlert error={save.error ? asApiError(save.error) : null} />
          </form>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button type="submit" form="ask-mode-form" disabled={!mode || mode === stored || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Choice({ c, current }: { c: AskModeChoice; current: boolean }) {
  const id = `ask-mode-${c.mode}`;
  const off = c.unavailable !== null;
  return (
    <label htmlFor={id} className={off ? "grid gap-1 rounded-md border border-line p-3 opacity-70" : "grid cursor-pointer gap-1 rounded-md border border-line p-3 has-[[data-state=checked]]:border-accent"}>
      <span className="flex items-start gap-3">
        <RadioGroupItem id={id} value={c.mode} disabled={off} className="mt-0.5" />
        <span className="grid gap-0.5">
          <span className="font-medium">
            {c.label}
            {c.default && <span className="font-normal text-ink-muted"> · default</span>}
            {current && <span className="font-normal text-ink-muted"> · in use</span>}
          </span>
          <span className="text-small text-ink-muted">{c.note}</span>
          {off && <span className="text-small">{c.unavailable}</span>}
          <Score s={c.score} />
        </span>
      </span>
    </label>
  );
}

function Score({ s }: { s: AskModeScore | null }) {
  if (!s) return <span className="text-small text-ink-muted">Not tested on the gold questions yet.</span>;
  const secs = s.median_ms === null ? null : (s.median_ms / 1000).toFixed(1);
  return (
    <span className="text-small" data-testid="ask-mode-score">
      {s.right} of {s.total} right · {s.confident_wrong} confidently wrong · {s.clarified} asked back · {s.refused} refused
      {secs !== null && <> · median {secs} s</>}
      <span className="text-ink-muted"> · tested {formatAsOf(s.run_at)}</span>
    </span>
  );
}
