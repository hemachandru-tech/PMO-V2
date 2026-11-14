import { useQuery } from "@tanstack/react-query";
import { Check, Eye, EyeOff } from "lucide-react";
import { useId, useState } from "react";

import { api, type ApiError } from "../../../api/client";
import type { PreviewResult, RoleRef } from "../../../api/authoring";
import { FormAlert } from "../../../components/ui/alert";
import { Button } from "../../../components/ui/button";
import { NativeSelect } from "../../../components/ui/native-select";
import { Popover, PopoverContent, PopoverTrigger } from "../../../components/ui/popover";
import { asApiError } from "../../../lib/api-errors";

/** Preview as a role (FEAT-040): which widgets that role would see on this report, resolved by the API. */
export function PreviewAsRole({ reportId }: { reportId: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<{ items: RoleRef[] }>("/api/v1/roles"), enabled: open });
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const preview = async (role: string) => {
    setError(null);
    setResult(null);
    try {
      setResult(await api<PreviewResult>(`/api/v1/reports/${reportId}/preview`, { method: "POST", body: { as: { role } } }));
    } catch (e) {
      setError(asApiError(e));
    }
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm"><Eye aria-hidden />Preview as role</Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="grid w-80 gap-3">
        <div className="grid gap-1.5">
          <label htmlFor={id} className="text-body-strong">Preview as</label>
          <NativeSelect id={id} defaultValue="" onChange={(e) => e.target.value && void preview(e.target.value)}>
            <option value="">Choose a role…</option>
            {(roles.data?.items ?? []).map((r) => <option key={r.id} value={r.key}>{r.name}</option>)}
          </NativeSelect>
        </div>
        <FormAlert error={error} />
        {result && (
          <div className="grid gap-2" aria-live="polite">
            <p className="text-small">As {result.as.name}: {result.scope_text}</p>
            {result.counts_note && <p className="text-small text-ink-muted">{result.counts_note}</p>}
            <ul className="grid gap-1">
              {result.widgets.map((w) => (
                <li key={w.item_id} className="flex gap-2 text-small">
                  {w.shown ? <Check aria-label="Shown" className="mt-0.5 size-4 shrink-0 text-status-ok" /> : <EyeOff aria-label="Hidden" className="mt-0.5 size-4 shrink-0 text-ink-muted" />}
                  <span>{w.title}{w.reason ? ` — ${w.reason}` : ""}{w.rows !== undefined ? ` (${w.rows} rows)` : ""}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
