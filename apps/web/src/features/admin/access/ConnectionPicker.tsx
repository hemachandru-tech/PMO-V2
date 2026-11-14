import { useQuery } from "@tanstack/react-query";
import { useEffect, useId } from "react";
import { useSearchParams } from "react-router-dom";

import { api } from "../../../api/client";
import type { Connection } from "../../../api/types";
import { Label } from "../../../components/ui/label";
import { NativeSelect } from "../../../components/ui/native-select";

export function useConnections() {
  return useQuery({
    queryKey: ["connections"],
    queryFn: () => api<{ items: Connection[] }>("/api/v1/connections"),
  });
}

/**
 * The connection the Access screens work on, kept in the URL (`?connection=`) so it survives switching between
 * scope and field policies and the Effective access panel. With a single connection it is chosen for you.
 */
export function useConnectionParam(): [string, (id: string) => void] {
  const [params, setParams] = useSearchParams();
  const { data } = useConnections();
  const value = params.get("connection") ?? "";
  const set = (id: string) => setParams((p) => { const n = new URLSearchParams(p); if (id) n.set("connection", id); else n.delete("connection"); return n; }, { replace: true });
  const only = data?.items.length === 1 ? data.items[0]!.id : "";
  useEffect(() => { if (!value && only) set(only); }, [value, only]); // eslint-disable-line react-hooks/exhaustive-deps
  return [value || only, set];
}

export function ConnectionPicker({ value, onChange, id: given }: { value: string; onChange: (id: string) => void; id?: string }) {
  const { data, isLoading } = useConnections();
  const generated = useId();
  const id = given ?? generated;
  const items = data?.items ?? [];
  if (!isLoading && items.length === 0) {
    return <p className="text-ink-muted">No connections yet. Policies are set per connection, so add a data source first.</p>;
  }
  return (
    <div className="grid max-w-sm gap-1">
      <Label htmlFor={id}>Connection</Label>
      <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose a connection…</option>
        {items.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name} ({c.engine})
          </option>
        ))}
      </NativeSelect>
    </div>
  );
}
