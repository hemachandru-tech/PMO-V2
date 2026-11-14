import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, CheckCheck } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { NotificationItem } from "../api/authoring";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { MotionList } from "./ui/motion";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";

/** In-app notifications (US-107; API-057; US-137 top bar): unread count, list, mark one or all as read. */
export function Notifications() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const n = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<{ items: NotificationItem[]; unread_count: number }>("/api/v1/notifications"),
    refetchInterval: 60_000,
  });
  const read = async (item: NotificationItem) => {
    if (!item.read_at) await api(`/api/v1/notifications/${item.id}/read`, { method: "POST" });
    void qc.invalidateQueries({ queryKey: ["notifications"] });
    setOpen(false);
    if (item.link) navigate(item.link);
  };
  const readAll = async () => {
    await api("/api/v1/notifications/read-all", { method: "POST" });
    void qc.invalidateQueries({ queryKey: ["notifications"] });
  };
  const unread = n.data?.unread_count ?? 0;
  const items = n.data?.items ?? [];
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="icon" className="relative" aria-label={`Notifications, ${unread} unread`}>
          <Bell aria-hidden className="size-4" />
          {unread > 0 && (
            <span aria-hidden className="absolute -right-0.5 -top-0.5 min-w-4 rounded-full bg-accent px-1 text-center font-mono text-[11px] leading-4 text-on-accent">
              {unread > 99 ? "99+" : unread}
            </span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[22rem] p-0" aria-label="Notifications">
        <header className="flex items-center justify-between border-b border-line px-4 py-2">
          <h2 className="text-heading">Notifications</h2>
          {unread > 0 && (
            <Button variant="link" size="sm" onClick={() => void readAll()}>
              <CheckCheck aria-hidden className="size-4" /> Mark all read
            </Button>
          )}
        </header>
        {items.length === 0 ? (
          <p className="px-4 py-6 text-center text-ink-muted">Nothing yet. Publishing, sharing and certification news will appear here.</p>
        ) : (
          <MotionList
            className="max-h-96 overflow-y-auto"
            itemClassName="overflow-hidden border-b border-line last:border-0"
            items={items.map((i) => ({
              key: i.id,
              content: (
                <button type="button" onClick={() => void read(i)}
                  className="grid w-full gap-0.5 px-4 py-2 text-left hover:bg-surface-sunken focus-visible:focus-ring">
                  <span className={cn("flex items-center gap-2", !i.read_at && "font-semibold")}>
                    {!i.read_at && <span aria-hidden className="size-2 shrink-0 rounded-full bg-accent" />}
                    {i.title}
                    {!i.read_at && <span className="sr-only">(unread)</span>}
                  </span>
                  <span className="text-small text-ink-muted">{i.body}</span>
                </button>
              ),
            }))}
          />
        )}
        <footer className="border-t border-line px-4 py-2">
          <Button variant="link" size="sm" onClick={() => { setOpen(false); navigate("/notifications"); }}>See all notifications</Button>
        </footer>
      </PopoverContent>
    </Popover>
  );
}
