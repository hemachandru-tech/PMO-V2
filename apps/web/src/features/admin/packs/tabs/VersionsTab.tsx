import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { listVersions, type PackReviewState, type PackVersion } from "../../../../api/packs";
import { formatRelative } from "../../../../components/format";
import { Badge } from "../../../../components/ui/badge";
import { DataTable } from "../../../../components/ui/data-table";
import { usePack } from "../PackContext";
import { ReleasePanel } from "../release/ReleasePanel";
import type { PackTab } from "./registry";

export function PackStatusBadge({ status, review }: { status: PackVersion["status"]; review?: PackReviewState | null }) {
  if (status === "released") return <Badge variant="certified">Released</Badge>;
  if (review?.state === "in_review") return <Badge variant="neutral">In review</Badge>;
  if (review?.state === "returned") return <Badge variant="stale">Sent back</Badge>;
  if (status === "retired") return <Badge variant="outline">Retired</Badge>;
  return <Badge variant="stale">Draft</Badge>;
}

/** Versions (US-232): every version of this pack, its status and where it is installed; release and review of the open
 * version (US-238). */
function VersionsTab() {
  const { pack } = usePack();
  const q = useQuery({ queryKey: ["pack-versions", pack.id], queryFn: () => listVersions(pack.id) });
  return (
    <div className="grid gap-4">
      <ReleasePanel />
      <DataTable<PackVersion>
        label="Versions" data={q.data?.items ?? []} loading={q.isLoading} search={false}
        getRowId={(v) => v.id}
        columns={[
          { id: "version", header: "Version", alwaysVisible: true, value: (v) => v.version,
            cell: (v) => v.id === pack.id ? <span className="font-mono font-medium">{v.version} (open)</span> : <Link className="font-mono underline" to={`/admin/packs/${v.id}`}>{v.version}</Link> },
          { id: "status", header: "Status", value: (v) => v.status, cell: (v) => <PackStatusBadge status={v.status} review={v.review} /> },
          { id: "changed", header: "Changed", date: true, value: (v) => v.updated_at, cell: (v) => (v.updated_at ? formatRelative(v.updated_at) : "") },
          { id: "installed", header: "Installed on", value: (v) => v.installed_on.map((c) => c.name).join(", "),
            cell: (v) => v.installed_on.length ? v.installed_on.map((c) => c.name).join(", ") : <span className="text-ink-muted">Not installed</span> },
        ]}
      />
    </div>
  );
}

export const versionsTab: PackTab = { key: "versions", label: "Versions", Component: VersionsTab };
