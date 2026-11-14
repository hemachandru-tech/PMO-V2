import { useQuery } from "@tanstack/react-query";
import { UserSearch } from "lucide-react";
import { NavLink, Navigate, Route, Routes, useSearchParams } from "react-router-dom";

import { api } from "../../../api/client";
import type { AccessRequest, MappingsResponse } from "../../../api/types";
import { can, useMe } from "../../../app/me";
import { Button } from "../../../components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "../../../components/ui/sheet";
import { cn } from "../../../lib/utils";
import { useConnectionParam } from "./ConnectionPicker";
import { EffectiveAccess } from "./EffectiveAccess";
import { FieldPoliciesPage } from "./FieldPoliciesPage";
import { FreehandPage } from "./FreehandPage";
import { GroupMappingsPage } from "./GroupMappingsPage";
import { ProvisioningPage } from "./ProvisioningPage";
import { ScopePoliciesPage } from "./ScopePoliciesPage";

const TABS = [
  { to: "mappings", label: "Group mappings", perm: "mappings.manage" },
  { to: "scope", label: "Scope policies", perm: "policies.manage" },
  { to: "fields", label: "Field policies", perm: "policies.manage" },
  { to: "provisioning", label: "Provisioning", perm: "mappings.manage" },
  { to: "freehand", label: "Free-hand query", perm: "mappings.manage" },
];

/**
 * SCR-12 Admin access (FEAT-002, FEAT-003; US-145; board Access): who can sign in (group mappings), which rows
 * (scope policies) and which fields (field policies) each role sees, and an Effective access panel that shows what
 * the server resolves for a person or a role. Provisioning (US-118, US-119) shows Entra's SCIM sync and Reconcile.
 * Free-hand query (US-162) gives, extends and removes per-person free-hand access and handles requests from Ask.
 * The connection and the open panel live in the URL.
 */
export function AccessPage() {
  const { data: me } = useMe();
  const [params, setParams] = useSearchParams();
  const [connectionId, setConnectionId] = useConnectionParam();
  const mappings = useQuery({
    queryKey: ["admin", "mappings"],
    queryFn: () => api<MappingsResponse>("/api/v1/admin/group-mappings"),
    enabled: can(me, "mappings.manage"),
  });
  // Pending free-hand requests show as a count on their tab.
  const requests = useQuery({
    queryKey: ["admin", "permission-requests"],
    queryFn: () => api<{ items: AccessRequest[] }>("/api/v1/admin/permission-requests"),
    enabled: can(me, "mappings.manage"),
  });
  if (!can(me, "mappings.manage") && !can(me, "policies.manage")) {
    return <p role="alert">You don't have permission to manage access.</p>;
  }
  const panel = params.get("panel") === "effective";
  const setPanel = (open: boolean) => setParams((p) => { const n = new URLSearchParams(p); if (open) n.set("panel", "effective"); else n.delete("panel"); return n; });
  const tabs = TABS.filter((t) => can(me, t.perm));
  const pending = requests.data?.items.filter((r) => r.status === "pending").length;
  const counts: Record<string, number | undefined> = { mappings: mappings.data?.mappings.length, freehand: pending || undefined };
  // Tabs keep the chosen connection.
  const search = connectionId ? `?connection=${connectionId}` : "";
  return (
    <section className="tl grid gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1">
          <h1 className="text-title">Access</h1>
          <p className="text-ink-muted">Who can sign in, what they can do, and which rows and fields they see.</p>
        </div>
        {can(me, "policies.manage") && (
          <Button variant="secondary" onClick={() => setPanel(true)}><UserSearch aria-hidden />Effective access</Button>
        )}
      </div>
      <nav className="flex gap-1 border-b border-line" aria-label="Access sections">
        {tabs.map((t) => (
          <NavLink key={t.to} to={{ pathname: t.to, search }} className={({ isActive }) => cn(
            "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-body-strong focus-visible:focus-ring",
            isActive ? "border-accent text-accent" : "border-transparent text-ink-muted hover:text-ink",
          )}>
            {t.label}
            {counts[t.to] !== undefined && <span className="rounded-sm bg-surface-sunken px-1.5 font-mono text-small tabular-nums text-ink-muted">{counts[t.to]}</span>}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to={{ pathname: tabs[0]!.to, search }} replace />} />
        <Route path="mappings" element={<GroupMappingsPage />} />
        <Route path="scope" element={<ScopePoliciesPage connectionId={connectionId} onConnection={setConnectionId} />} />
        <Route path="fields" element={<FieldPoliciesPage connectionId={connectionId} onConnection={setConnectionId} />} />
        <Route path="provisioning" element={<ProvisioningPage />} />
        <Route path="freehand" element={<FreehandPage />} />
      </Routes>
      <Sheet open={panel} onOpenChange={setPanel}>
        <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>Effective access</SheetTitle>
            <SheetDescription>What a person or a role can see on a connection, as the server works it out.</SheetDescription>
          </SheetHeader>
          <div className="px-4 pb-4">
            <EffectiveAccess connectionId={connectionId} onConnection={setConnectionId} />
          </div>
        </SheetContent>
      </Sheet>
    </section>
  );
}
