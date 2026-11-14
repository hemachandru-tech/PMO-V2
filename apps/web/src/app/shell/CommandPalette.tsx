import { useQuery } from "@tanstack/react-query";
import { FileText, LayoutDashboard, MessageSquare } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import type { Home, Tile } from "../../api/app";
import { api } from "../../api/client";
import type { Me } from "../../api/types";
import { CommandDialog, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "../../components/ui/command";
import { tileLink } from "../../features/home/HomePage";
import { groupIcon, useMyDashboardGroups } from "./dashboardGroups";
import { visibleNav } from "./nav";

/**
 * Command palette (US-137; ⌘K / Ctrl+K): go to any screen, dashboard or report the person can open, or send the
 * text to Ask. Content comes from Home's shelves, which the API already limits to what this person may open, and the
 * dashboard groups (ENH-009, US-171), listed under their group's name.
 */
export function CommandPalette({ me, open, onOpenChange }: { me: Me; open: boolean; onOpenChange: (open: boolean) => void }) {
  const navigate = useNavigate();
  const [text, setText] = useState("");
  const home = useQuery({ queryKey: ["home"], queryFn: () => api<Home>("/api/v1/home"), enabled: open });
  const groups = useMyDashboardGroups(me, open).data?.items ?? [];
  const go = (to: string) => {
    onOpenChange(false);
    setText("");
    navigate(to);
  };
  const content = contentItems(home.data);
  const canAsk = me.permissions.includes("ask.use");
  return (
    <CommandDialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) setText(""); }}
      title="Command palette" description="Go to a screen, dashboard or report, or ask a question.">
      <CommandInput placeholder={canAsk ? "Go to… or ask a question" : "Go to…"} value={text} onValueChange={setText} />
      <CommandList>
        <CommandEmpty>No matches.</CommandEmpty>
        <CommandGroup heading="Go to">
          {visibleNav(me).map((item) => (
            <CommandItem key={item.key} value={item.label} keywords={[item.group]} onSelect={() => go(item.to)}>
              <item.icon aria-hidden />
              {item.label}
              <span className="ml-auto text-small text-ink-muted">{item.group}</span>
            </CommandItem>
          ))}
        </CommandGroup>
        {content.length > 0 && (
          <CommandGroup heading="Dashboards and reports">
            {content.map(({ tile, to }) => (
              <CommandItem key={`${tile.type}-${tile.id}`} value={`${tile.title}\u0000${tile.type}-${tile.id}`} keywords={[tile.type]} onSelect={() => go(to)}>
                {tile.type === "report" ? <FileText aria-hidden /> : <LayoutDashboard aria-hidden />}
                {tile.title}
                <span className="ml-auto text-small text-ink-muted">{tile.type === "report" ? "Report" : "Dashboard"}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {groups.map((g) => {
          const Icon = groupIcon(g.icon);
          return (
            <CommandGroup key={g.id} heading={g.name}>
              {g.dashboards.map((d) => (
                <CommandItem key={d.id} value={`${d.title}\u0000group-${g.id}-${d.id}`} keywords={[g.name, "dashboard"]} onSelect={() => go(d.link)}>
                  <Icon aria-hidden />
                  {d.title}
                  <span className="ml-auto text-small text-ink-muted">{g.name}</span>
                </CommandItem>
              ))}
            </CommandGroup>
          );
        })}
        {/* Always offered, and last, so Enter opens a matching screen first and asks only when nothing matches. */}
        {canAsk && text.trim() && (
          <CommandGroup heading="Ask" forceMount>
            <CommandItem value={`ask ${text}`} forceMount onSelect={() => go(`/ask?q=${encodeURIComponent(text.trim())}`)}>
              <MessageSquare aria-hidden />
              Ask “{text.trim()}”
            </CommandItem>
          </CommandGroup>
        )}
      </CommandList>
    </CommandDialog>
  );
}

function contentItems(home: Home | undefined): { tile: Tile; to: string }[] {
  if (!home) return [];
  const seen = new Set<string>();
  const out: { tile: Tile; to: string }[] = [];
  for (const shelf of ["mine", "published_to_my_role", "shared_with_me", "starter"] as const) {
    for (const tile of home.shelves[shelf] ?? []) {
      const key = `${tile.type}-${tile.id}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ tile, to: tileLink(tile, shelf) });
    }
  }
  return out;
}
