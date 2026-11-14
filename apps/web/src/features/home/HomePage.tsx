import { useQuery } from "@tanstack/react-query";
import { FileText, Inbox, LayoutDashboard, MessageSquare, Search, Sparkles, type LucideIcon } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../../api/client";
import type { ConversationItem, Home, Tile } from "../../api/app";
import { can, useMe } from "../../app/me";
import { formatAsOf, formatRelative } from "../../components/format";
import { FormAlert } from "../../components/ui/alert";
import { ContentBadge } from "../../components/ContentBadge";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { Input } from "../../components/ui/input";
import { Skeleton } from "../../components/ui/skeleton";
import { asApiError } from "../../lib/api-errors";

type ShelfKey = keyof Home["shelves"];

const SHELVES: { key: ShelfKey; title: string; icon: LucideIcon; next: string }[] = [
  { key: "published_to_my_role", title: "Published to my role", icon: LayoutDashboard, next: "Your PMO Admin publishes dashboards to roles; they appear here when one reaches yours." },
  { key: "starter", title: "Starter", icon: Sparkles, next: "Starter dashboards arrive when an Admin installs a content pack with a data source." },
  { key: "shared_with_me", title: "Shared with me", icon: Inbox, next: "When a colleague shares a dashboard or report with you, it appears here." },
  { key: "mine", title: "Mine", icon: FileText, next: "Start with Build with chat, or New report, and your drafts appear here." },
];
/** Cards shown per shelf before "Show all". */
const SHELF_LIMIT = 6;

/**
 * Home (FEAT-017; SCR-01; US-138): a question first, then the person's content. The ask box sends to Ask; suggested
 * questions come from certified measures (server-side). Shelves are cards with type, badges, owner and freshness.
 * No figures run here, so the entry chunk stays free of chart code.
 */
export function HomePage() {
  const me = useMe().data;
  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home") });
  const canAsk = can(me, "ask.use");
  if (home.isLoading) return <HomeSkeleton />;
  if (home.error) return <div className="tl"><FormAlert error={asApiError(home.error)} /></div>;
  const h = home.data!;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return (
    <div className="tl grid gap-6">
      <Card className="min-w-0 gap-4 p-6 lg:p-8">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-display">{greeting}, {h.greeting.name.split(" ")[0]}</h1>
          <p className="text-small text-ink-muted">Data as of {formatAsOf(h.greeting.as_of)}</p>
        </div>
        {h.greeting.scopes.length === 0 ? (
          <p className="text-ink-muted">No data sources are set up for your role yet.</p>
        ) : (
          <ul className="flex flex-wrap gap-2" aria-label="Your data scope">
            {h.greeting.scopes.map((s) => (
              <li key={s.connection_id} className="min-w-0 max-w-full">
                <Badge variant="outline" className="max-w-full whitespace-normal wrap-break-word" title="This is your data scope from your Microsoft groups. Filters can narrow it but not widen it.">
                  {s.connection_name}: {s.text}
                </Badge>
              </li>
            ))}
          </ul>
        )}
        {canAsk && <HomeAsk suggestions={h.suggested_questions ?? []} />}
        {h.can_create && (
          <div className="flex flex-wrap gap-2">
            <Button asChild variant={canAsk ? "secondary" : "primary"}><Link to="/reports/new"><MessageSquare aria-hidden />Build with chat</Link></Button>
            <Button asChild variant="secondary"><Link to="/reports">New report</Link></Button>
          </div>
        )}
      </Card>
      <div className="grid gap-6 lg:grid-cols-12">
        <div className="grid min-w-0 content-start gap-6 lg:col-span-8">
          {SHELVES.filter((s) => h.shelves[s.key] !== undefined).map((s) => (
            <Shelf key={s.key} shelf={s} tiles={h.shelves[s.key]!} emptyText={h.empty_text[s.key]} />
          ))}
        </div>
        {canAsk && <div className="min-w-0 lg:col-span-4"><RecentQuestions /></div>}
      </div>
    </div>
  );
}

function HomeAsk({ suggestions }: { suggestions: string[] }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const ask = (question: string) => navigate(`/ask?${new URLSearchParams({ q: question }).toString()}`);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (q.trim()) ask(q.trim());
  };
  return (
    <div className="grid gap-3">
      <form role="search" aria-label="Ask a question" onSubmit={submit} className="flex flex-wrap gap-2">
        <label htmlFor="home-ask" className="sr-only">Ask a question about your data</label>
        <div className="relative min-w-0 flex-[1_1_20rem]">
          <Search aria-hidden className="pointer-events-none absolute left-4 top-1/2 size-4 -translate-y-1/2 text-ink-muted" />
          <Input id="home-ask" type="search" value={q} maxLength={2000} onChange={(e) => setQ(e.target.value)}
            placeholder="Ask a question about your projects, budgets or requests" className="h-control-lg pl-10 text-heading font-normal" />
        </div>
        <Button type="submit" size="lg" disabled={!q.trim()}>Ask</Button>
      </form>
      {suggestions.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-small text-ink-muted" id="home-try">Try</span>
          <ul className="contents" aria-labelledby="home-try">
            {suggestions.map((s) => (
              <li key={s} className="contents">
                <button type="button" onClick={() => ask(s)}
                  className="inline-flex h-control-sm items-center rounded-full border border-line-strong bg-surface-raised px-3 text-small text-ink hover:border-accent hover:bg-accent-soft focus-visible:focus-ring">
                  {s}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Shelf({ shelf, tiles, emptyText }: { shelf: (typeof SHELVES)[number]; tiles: Tile[]; emptyText?: string }) {
  const id = `shelf-${shelf.key}`;
  const [all, setAll] = useState(false);
  const shown = all ? tiles : tiles.slice(0, SHELF_LIMIT);
  return (
    <section aria-labelledby={id} className="grid gap-3">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id={id} className="text-title">{shelf.title}</h2>
        {tiles.length > 0 && <span className="text-small text-ink-muted">{tiles.length} {tiles.length === 1 ? "item" : "items"}</span>}
      </div>
      {tiles.length === 0 ? (
        <EmptyState icon={shelf.icon} title={emptyText ?? "Nothing here yet"} className="py-6">{shelf.next}</EmptyState>
      ) : (
        <>
          <ul className="grid gap-2 sm:grid-cols-2">
            {shown.map((t) => <li key={`${t.type}-${t.id}`} className="min-w-0"><TileCard tile={t} shelf={shelf.key} /></li>)}
          </ul>
          {tiles.length > SHELF_LIMIT && (
            <Button variant="link" size="sm" className="justify-self-start" aria-expanded={all} onClick={() => setAll(!all)}>
              {all ? "Show fewer" : `Show all ${tiles.length}`}
            </Button>
          )}
        </>
      )}
    </section>
  );
}

export function tileLink(tile: Tile, shelf: string): string {
  if (tile.type === "report") return `/reports/${tile.id}`;
  if (shelf === "mine") return tile.published ? `/dashboards/${tile.id}` : `/dashboards/${tile.id}/edit`;
  if (shelf === "shared_with_me") return `/dashboards/${tile.id}/draft`;
  return `/dashboards/${tile.id}`;
}

function TileCard({ tile, shelf }: { tile: Tile; shelf: ShelfKey }) {
  const Icon = tile.type === "report" ? FileText : LayoutDashboard;
  const parts = [
    tile.type === "report" ? "Report" : "Dashboard",
    tile.count.tabs !== undefined ? `${tile.count.tabs} tabs` : null,
    `${tile.count.widgets ?? 0} widgets`,
    tile.version ? `v${tile.version}` : null,
  ].filter(Boolean);
  const edited = formatRelative(tile.last_edited_at);
  return (
    <div className="relative flex h-full gap-3 rounded-md border border-line bg-surface-raised p-3 hover:border-line-strong focus-within:border-accent">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-md bg-accent-soft text-accent"><Icon aria-hidden className="size-5" /></span>
      <div className="grid min-w-0 flex-1 gap-1">
        <Link to={tileLink(tile, shelf)} className="truncate font-medium text-ink after:absolute after:inset-0 after:rounded-md focus-visible:outline-none focus-visible:after:focus-ring">
          {tile.title}
        </Link>
        <span className="text-small text-ink-muted">{parts.join(" · ")}</span>
        <span className="text-small text-ink-muted">
          {[tile.owner, edited && `updated ${edited}`].filter(Boolean).join(" · ")}
        </span>
        <span className="flex flex-wrap gap-1">
          {tile.badges.map((b) => <TileBadge key={b} badge={b} />)}
          {shelf === "mine" && tile.type === "dashboard" && tile.published && (
            <Link to={`/dashboards/${tile.id}/edit`} className="relative z-10 text-small text-link hover:underline focus-visible:focus-ring">Edit draft</Link>
          )}
        </span>
      </div>
    </div>
  );
}

function TileBadge({ badge }: { badge: string }) {
  if (badge === "certified") return <ContentBadge badge="certified" />;
  if (badge === "draft") return <Badge variant="stale">Draft</Badge>;
  if (badge === "starter") return <Badge variant="outline">Starter</Badge>;
  return <Badge>{badge}</Badge>;
}

function RecentQuestions() {
  const list = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api<{ items: ConversationItem[] }>("/api/v1/ask/conversations"),
  });
  const items = (list.data?.items ?? []).slice(0, 6);
  return (
    <section aria-labelledby="recent-questions" className="grid grid-cols-[minmax(0,1fr)] gap-3">
      <h2 id="recent-questions" className="text-title">Recent questions</h2>
      {list.isLoading ? <Skeleton className="h-32" /> : list.error ? <FormAlert error={asApiError(list.error)} /> : items.length === 0 ? (
        <EmptyState icon={MessageSquare} title="No questions yet" className="py-6">Ask anything above; your conversations are kept here so you can pick them up again.</EmptyState>
      ) : (
        <Card>
          <ul className="divide-y divide-line">
            {items.map((c) => (
              <li key={c.id} className="flex items-baseline justify-between gap-3 px-4 py-2.5">
                <Link to={`/ask?c=${c.id}`} className="min-w-0 truncate text-link hover:underline focus-visible:focus-ring">{c.title ?? "Untitled conversation"}</Link>
                <span className="shrink-0 text-small text-ink-muted">{formatRelative(c.updated_at)}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </section>
  );
}

function HomeSkeleton() {
  return (
    <div className="tl grid gap-6" aria-busy="true" aria-label="Loading">
      <Skeleton className="h-48" />
      <div className="grid gap-2 sm:grid-cols-2"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
    </div>
  );
}
