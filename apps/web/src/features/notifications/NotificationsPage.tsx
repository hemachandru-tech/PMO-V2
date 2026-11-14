import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCheck } from "lucide-react";
import { useMemo } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../../api/client";
import type { NotificationItem, NotificationsPage as Page } from "../../api/authoring";
import { formatRelative } from "../../components/format";
import { FormAlert } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { EmptyState } from "../../components/ui/empty-state";
import { Label } from "../../components/ui/label";
import { MotionList } from "../../components/ui/motion";
import { NativeSelect } from "../../components/ui/native-select";
import { Skeleton } from "../../components/ui/skeleton";
import { asApiError } from "../../lib/api-errors";
import { cn } from "../../lib/utils";

/** What each kind of notification is about, for the type filter. Unknown kinds show their key in words. */
export const KIND_LABELS: Record<string, string> = {
  "dashboard.published": "Dashboards published to you",
  "share.received": "Shared with you",
  "publish.requested": "Publish requests",
  "publish.approved": "Publish requests approved",
  "publish.declined": "Publish requests returned",
  "certification.requested": "Certification requests",
  "certification.changed": "Certification decisions",
  "certification.certified": "Certified",
  "certification.rejected": "Certification declined",
  "definition.broken": "Broken definitions",
};
const kindLabel = (k: string) => KIND_LABELS[k] ?? k.replace(/[._]/g, " ").replace(/^./, (c) => c.toUpperCase());

/**
 * Notifications centre (US-107; API-057; US-146; board Notifications): everything the bell shows, with All / Unread,
 * a type filter (both in the URL), today and earlier, mark one or all as read, and more on demand.
 */
export function NotificationsPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const unread = params.get("show") === "unread";
  const kind = params.get("kind") ?? "";
  const set = (k: string, v: string) => setParams((p) => { const n = new URLSearchParams(p); if (v) n.set(k, v); else n.delete(k); return n; }, { replace: true });
  const list = useInfiniteQuery({
    queryKey: ["notifications", "page", unread, kind],
    queryFn: ({ pageParam }) => {
      const q = new URLSearchParams({ limit: "50", ...(unread ? { unread: "true" } : {}), ...(kind ? { kind } : {}), ...(pageParam ? { cursor: pageParam } : {}) });
      return api<Page>(`/api/v1/notifications?${q.toString()}`);
    },
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const first = list.data?.pages[0];
  const items = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["notifications"] });
  const markRead = useMutation({ mutationFn: (id: string) => api(`/api/v1/notifications/${id}/read`, { method: "POST" }), onSuccess: refresh });
  const markAll = useMutation({ mutationFn: () => api("/api/v1/notifications/read-all", { method: "POST" }), onSuccess: refresh });
  const open = async (n: NotificationItem) => {
    if (!n.read_at) await markRead.mutateAsync(n.id);
    if (n.link) navigate(n.link);
  };
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const today = items.filter((n) => new Date(n.created_at) >= startOfToday);
  const earlier = items.filter((n) => new Date(n.created_at) < startOfToday);
  const unreadCount = first?.unread_count ?? 0;

  return (
    <section className="tl grid w-full gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h1 className="text-title">Notifications</h1>
        {unreadCount > 0 && (
          <Button variant="secondary" onClick={() => markAll.mutate()} disabled={markAll.isPending}><CheckCheck aria-hidden />Mark all as read</Button>
        )}
      </div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex gap-1 border-b border-line" role="tablist" aria-label="Show">
          {[{ v: "", label: "All" }, { v: "unread", label: "Unread", count: unreadCount }].map((t) => (
            <button key={t.label} type="button" role="tab" aria-selected={(t.v === "unread") === unread} onClick={() => set("show", t.v)}
              className={cn("-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-body-strong focus-visible:focus-ring",
                (t.v === "unread") === unread ? "border-accent text-accent" : "border-transparent text-ink-muted hover:text-ink")}>
              {t.label}
              {t.count !== undefined && <span className="rounded-sm bg-surface-sunken px-1.5 font-mono text-small tabular-nums text-ink-muted">{t.count}</span>}
            </button>
          ))}
        </div>
        <div className="grid min-w-56 gap-1">
          <Label htmlFor="notification-kind">Type</Label>
          <NativeSelect id="notification-kind" value={kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="">All types</option>
            {(first?.kinds ?? []).map((k) => <option key={k.kind} value={k.kind}>{kindLabel(k.kind)} ({k.unread ? `${k.unread} unread` : k.count})</option>)}
            {kind && !(first?.kinds ?? []).some((k) => k.kind === kind) && <option value={kind}>{kindLabel(kind)}</option>}
          </NativeSelect>
        </div>
      </div>
      <FormAlert error={list.error ? asApiError(list.error) : markAll.error ? asApiError(markAll.error) : null} />
      {list.isLoading ? <Skeleton className="h-64" /> : items.length === 0 ? (
        <EmptyState title={unread ? "You're all caught up" : "Nothing yet"}>
          {unread ? "No unread notifications." : "Publishing, sharing and certification news will appear here."}
        </EmptyState>
      ) : (
        <>
          {today.length > 0 && <Group title="Today" items={today} onOpen={(n) => void open(n)} onRead={(id) => markRead.mutate(id)} />}
          {earlier.length > 0 && <Group title="Earlier" items={earlier} onOpen={(n) => void open(n)} onRead={(id) => markRead.mutate(id)} />}
          {list.hasNextPage && (
            <div><Button variant="secondary" onClick={() => void list.fetchNextPage()} disabled={list.isFetchingNextPage}>{list.isFetchingNextPage ? "Loading…" : "Show more"}</Button></div>
          )}
        </>
      )}
    </section>
  );
}

function Group({ title, items, onOpen, onRead }: { title: string; items: NotificationItem[]; onOpen: (n: NotificationItem) => void; onRead: (id: string) => void }) {
  return (
    <section className="grid gap-2" aria-label={title}>
      <h2 className="text-label uppercase text-ink-muted">{title}</h2>
      <MotionList
        className="divide-y divide-line rounded-lg border border-line bg-surface-raised"
        itemClassName="flex items-start gap-3 overflow-hidden px-4 py-3"
        items={items.map((n) => ({
          key: n.id,
          content: (
            <>
              <span aria-hidden className={cn("mt-2 size-2 shrink-0 rounded-full", n.read_at ? "bg-transparent" : "bg-accent")} />
              <button type="button" onClick={() => onOpen(n)} className="grid min-w-0 flex-1 gap-0.5 rounded-sm text-left focus-visible:focus-ring">
                <span className={cn(!n.read_at && "font-semibold")}>{n.title}{!n.read_at && <span className="sr-only"> (unread)</span>}</span>
                <span className="text-small text-ink-muted">{n.body} · {formatRelative(n.created_at)}</span>
              </button>
              {!n.read_at && <Button variant="ghost" size="sm" onClick={() => onRead(n.id)}>Mark as read</Button>}
            </>
          ),
        }))}
      />
    </section>
  );
}
