import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../../api/client";
import type { PublishRequest } from "../../api/authoring";
import { formatRelative } from "../../components/format";
import { FormAlert } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { DataTable, type DataColumn } from "../../components/ui/data-table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { asApiError } from "../../lib/api-errors";

const STATUSES = [["open", "Waiting"], ["decided", "Decided"]] as const;
const link = (r: PublishRequest) => `/dashboards/${r.dashboard.id}/publish?request=${r.id}`;

/**
 * Govern → Publish requests (US-142; DEC-05): Creators' requests to publish a dashboard to roles, oldest first. Opening
 * one starts the publish steps with the requested roles; publishing or returning it with a note closes it.
 */
export function PublishRequestsPage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const status = params.get("status") === "decided" ? "decided" : "open";
  const q = useQuery({
    queryKey: ["publish-requests", status],
    queryFn: () => api<{ items: PublishRequest[] }>(`/api/v1/publish-requests?status=${status}`),
  });
  const columns = useMemo<DataColumn<PublishRequest>[]>(() => [
    {
      id: "dashboard", header: "Dashboard", alwaysVisible: true, value: (r) => `${r.dashboard.title} ${r.requested_by.name}`,
      cell: (r) => (
        <span className="grid gap-0.5">
          {status === "open" ? <Link to={link(r)} className="font-medium">{r.dashboard.title}</Link> : <Link to={`/dashboards/${r.dashboard.id}/versions`} className="font-medium">{r.dashboard.title}</Link>}
          {r.note && <span className="line-clamp-2 text-small text-ink-muted">“{r.note}”</span>}
        </span>
      ),
    },
    { id: "by", header: "Requested by", value: (r) => r.requested_by.name ?? "" },
    { id: "roles", header: "Roles", value: (r) => r.roles.map((x) => x.name).join(", "), cell: (r) => <span className="flex flex-wrap gap-1">{r.roles.map((x) => <Badge key={x.key}>{x.name}</Badge>)}</span> },
    { id: "asked", header: "Asked", date: true, value: (r) => r.created_at, cell: (r) => formatRelative(r.created_at) },
    ...(status === "decided" ? [{
      id: "outcome", header: "Outcome", value: (r: PublishRequest) => r.status,
      cell: (r: PublishRequest) => (
        <span className="grid gap-0.5">
          <span>{r.status === "published" ? <Badge variant="certified">Published{r.version ? ` as version ${r.version}` : ""}</Badge> : <Badge variant="stale">Returned</Badge>}</span>
          <span className="text-small text-ink-muted">{r.decided_by} · {formatRelative(r.decided_at)}{r.decision_note ? ` · “${r.decision_note}”` : ""}</span>
        </span>
      ),
    } satisfies DataColumn<PublishRequest>] : []),
  ], [status]);
  const items = q.data?.items ?? [];

  return (
    <div className="tl grid gap-4">
      <div className="grid gap-1">
        <h1 className="text-title">Publish requests</h1>
        <p className="text-ink-muted">Creators ask an Admin to publish a dashboard to roles. Open a request to preview it, run the checks and publish, or return it with a note.</p>
      </div>
      <Tabs value={status} onValueChange={(v) => setParams({ status: v })} className="grid gap-4">
        <TabsList aria-label="Status">{STATUSES.map(([v, label]) => <TabsTrigger key={v} value={v}>{label}</TabsTrigger>)}</TabsList>
        {/* The filtered list is the panel of the selected tab. */}
        <TabsContent value={status}>
          <DataTable
            data={items}
            columns={columns}
            label="Publish requests"
            getRowId={(r) => r.id}
            loading={q.isLoading}
            error={q.error && <FormAlert error={asApiError(q.error)} />}
            empty={{ title: status === "open" ? "Nothing is waiting" : "Nothing decided yet", body: status === "open" ? "Requests appear when a Creator asks to publish a dashboard." : undefined }}
            search={items.length > 8}
            onRowActivate={status === "open" ? (r) => navigate(link(r)) : undefined}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
