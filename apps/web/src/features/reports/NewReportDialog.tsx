import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type ApiError } from "../../api/client";
import type { Report } from "../../api/authoring";
import { FormAlert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { asApiError } from "../../lib/api-errors";

/** A blank report: name it, then it opens on the canvas with one empty section. */
export function NewReportDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [title, setTitle] = useState("New report");
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<Report>("/api/v1/reports", { method: "POST", body: { title: title.trim() } });
      void qc.invalidateQueries({ queryKey: ["reports"] });
      onOpenChange(false);
      navigate(`/reports/${r.id}`);
    } catch (e) {
      setError(asApiError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form className="grid gap-4" onSubmit={(e) => { e.preventDefault(); if (title.trim()) void create(); }}>
          <DialogHeader>
            <DialogTitle>New blank report</DialogTitle>
            <DialogDescription>A draft only you can see. Add widgets from your library, certified measures or Ask.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="new-report-title">Report title</Label>
            <Input id="new-report-title" value={title} maxLength={200} autoFocus onChange={(e) => setTitle(e.target.value)} />
          </div>
          <FormAlert error={error} />
          <DialogFooter>
            <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={busy || !title.trim()}>Create report</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
