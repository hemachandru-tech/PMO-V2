import { useMutation } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import type { Report } from "../api/authoring";
import { api } from "../api/client";
import { asApiError } from "../lib/api-errors";
import { Button } from "./ui/button";

/**
 * "Copy to my reports" for a starter report (REQ-114; FEAT-046): the copy is the person's own report, opened on the
 * canvas; later starter pack updates never touch it.
 */
export function CopyStarterButton({ reportId }: { reportId: string }) {
  const navigate = useNavigate();
  const copy = useMutation({
    mutationFn: () => api<Report>(`/api/v1/reports/${reportId}/copy`, { method: "POST" }),
    onSuccess: (r) => { toast.success(`${r.title} is in your reports`); navigate(`/reports/${r.id}`); },
    onError: (e) => toast.error(asApiError(e, "The report could not be copied.").message),
  });
  return (
    <Button variant="secondary" size="sm" disabled={copy.isPending} onClick={() => copy.mutate()}
      title="Make your own copy to change. Starter pack updates never touch it.">
      <Copy aria-hidden />Copy to my reports
    </Button>
  );
}
