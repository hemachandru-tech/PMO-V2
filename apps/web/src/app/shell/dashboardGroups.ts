import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Briefcase,
  CalendarDays,
  Flag,
  Folder,
  Gauge,
  Landmark,
  Rocket,
  ShieldCheck,
  Users,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useState } from "react";

import type { MyDashboardGroup } from "../../api/authoring";
import { api } from "../../api/client";
import type { Me } from "../../api/types";
import { can } from "../me";

/** Icons a dashboard group can use (ENH-009); the API stores the key. Unknown or missing keys show a folder. */
export const GROUP_ICONS: Record<string, { label: string; icon: LucideIcon }> = {
  folder: { label: "Folder", icon: Folder },
  chart: { label: "Chart", icon: BarChart3 },
  gauge: { label: "Gauge", icon: Gauge },
  briefcase: { label: "Briefcase", icon: Briefcase },
  wallet: { label: "Wallet", icon: Wallet },
  landmark: { label: "Finance", icon: Landmark },
  flag: { label: "Flag", icon: Flag },
  rocket: { label: "Rocket", icon: Rocket },
  calendar: { label: "Calendar", icon: CalendarDays },
  users: { label: "People", icon: Users },
  shield: { label: "Shield", icon: ShieldCheck },
};

export const groupIcon = (key: string | null | undefined): LucideIcon => GROUP_ICONS[key ?? ""]?.icon ?? Folder;

/** Every query that shows groups starts with this key, so one invalidation refreshes the sidebar and the Groups tab. */
export const GROUPS_KEY = "dashboard-groups";

/** The sidebar's groups (US-171): only dashboards this person can open; the API leaves out empty groups. */
export function useMyDashboardGroups(me: Me | undefined, enabled = true) {
  return useQuery({
    queryKey: [GROUPS_KEY, "mine"],
    queryFn: () => api<{ items: MyDashboardGroup[] }>("/api/v1/me/dashboard-groups"),
    enabled: enabled && can(me, "dashboards.view"),
    staleTime: 60_000,
  });
}

const OPEN_KEY = "tela.sidebar.openGroups";

function readOpen(): string[] {
  try {
    const raw = window.localStorage.getItem(OPEN_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

/** Which groups are open in the sidebar, remembered in this browser; works (unremembered) when storage is blocked. */
export function useOpenGroups(): [Set<string>, (id: string, open: boolean) => void] {
  const [open, setOpen] = useState(() => new Set(readOpen()));
  const toggle = useCallback((id: string, value: boolean) => {
    setOpen((prev) => {
      const next = new Set(prev);
      if (value) next.add(id);
      else next.delete(id);
      try {
        window.localStorage.setItem(OPEN_KEY, JSON.stringify([...next]));
      } catch {
        // Storage blocked (private window, policy): the choice lasts for this page only.
      }
      return next;
    });
  }, []);
  return [open, toggle];
}
