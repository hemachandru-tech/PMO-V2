import { ChevronRight } from "lucide-react";
import { useId } from "react";
import { NavLink } from "react-router-dom";

import type { DashboardGroupLink, MyDashboardGroup } from "../../api/authoring";
import type { Me } from "../../api/types";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { cn } from "../../lib/utils";
import { groupIcon, useMyDashboardGroups, useOpenGroups } from "./dashboardGroups";

const linkClass = "flex h-control-sm min-w-0 items-center rounded-md px-2 text-body text-ink-muted hover:bg-(--nav-hover) hover:text-ink focus-visible:focus-ring";
const activeClass = "bg-(--nav-current-bg) font-medium text-(--nav-current-ink) hover:bg-(--nav-current-bg) hover:text-(--nav-current-ink)";

/** The dashboard a path shows, if it is a dashboard page (published view or draft preview). */
export function dashboardIdOf(path: string): string | undefined {
  return /^\/dashboards\/([^/]+)(?:\/draft)?$/.exec(path)?.[1];
}

/**
 * Dashboard groups in the sidebar (ENH-009, US-171), for every role: one disclosure per group listing the dashboards
 * this person can open (the API leaves out the rest and any empty group). Open groups are remembered per browser; the
 * group holding the open dashboard is always open. Collapsed, each group is an icon that opens its list in a popover.
 */
export function SidebarGroups({ me, pathname, collapsed, onNavigate }: {
  me: Me;
  pathname: string;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const groups = useMyDashboardGroups(me).data?.items ?? [];
  const [open, setOpen] = useOpenGroups();
  if (groups.length === 0) return null;
  const current = dashboardIdOf(pathname);
  return (
    <div className="grid gap-0.5" role="group" aria-labelledby="nav-Dashboards">
      <span id="nav-Dashboards" className={cn("px-2 pb-1 text-label uppercase tracking-wider text-ink-muted", collapsed && "sr-only")}>Dashboards</span>
      {groups.map((g, i) => {
        const holdsCurrent = !!current && g.dashboards.some((d) => d.id === current);
        // Aurora (ENH-027): each group's icon on a chip of its own colour, after the Explore screens' colours.
        const tone = ((i + 3) % 8) + 1;
        return collapsed
          ? <CollapsedGroup key={g.id} group={g} current={current} holdsCurrent={holdsCurrent} onNavigate={onNavigate} tone={tone} />
          : <OpenableGroup key={g.id} group={g} current={current} holdsCurrent={holdsCurrent} open={open.has(g.id) || holdsCurrent}
              onOpenChange={(v) => setOpen(g.id, v)} onNavigate={onNavigate} tone={tone} />;
      })}
    </div>
  );
}

function DashboardLinks({ dashboards, current, onNavigate, className }: {
  dashboards: DashboardGroupLink[];
  current: string | undefined;
  onNavigate?: () => void;
  className?: string;
}) {
  return (
    <ul className={cn("grid max-h-72 gap-0.5 overflow-y-auto", className)}>
      {dashboards.map((d) => {
        const isCurrent = d.id === current;
        return (
          <li key={d.id} className="min-w-0">
            <NavLink data-nav-sub to={d.link} onClick={onNavigate} title={d.title} aria-current={isCurrent ? "page" : undefined}
              className={cn(linkClass, isCurrent && activeClass)}>
              <span className="truncate">{d.title}</span>
            </NavLink>
          </li>
        );
      })}
    </ul>
  );
}

function OpenableGroup({ group, current, holdsCurrent, open, onOpenChange, onNavigate, tone }: {
  tone: number;
  group: MyDashboardGroup;
  current: string | undefined;
  holdsCurrent: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onNavigate?: () => void;
}) {
  const listId = useId();
  const Icon = groupIcon(group.icon);
  return (
    <div className="grid gap-0.5" data-nav-tone={tone}>
      <button type="button" data-nav-item aria-expanded={open} aria-controls={listId} onClick={() => onOpenChange(!open)} title={group.name}
        className={cn(
          "flex h-control-md min-w-0 items-center gap-2 rounded-md px-2 text-left text-body text-ink-muted hover:bg-(--nav-hover) hover:text-ink focus-visible:focus-ring",
          "transition-colors duration-150 motion-reduce:transition-none",
          holdsCurrent && "font-medium text-ink",
        )}>
        <span data-nav-icon={tone} className="inline-flex shrink-0 items-center justify-center"><Icon aria-hidden className="size-4 shrink-0" /></span>
        <span className="min-w-0 flex-1 truncate">{group.name}</span>
        <ChevronRight aria-hidden className={cn("size-4 shrink-0 transition-transform duration-150 motion-reduce:transition-none", open && "rotate-90")} />
      </button>
      <div id={listId} hidden={!open}>
        <DashboardLinks dashboards={group.dashboards} current={current} onNavigate={onNavigate} className="ml-4 border-l border-line pl-2" />
      </div>
    </div>
  );
}

function CollapsedGroup({ group, current, holdsCurrent, onNavigate, tone }: {
  tone: number;
  group: MyDashboardGroup;
  current: string | undefined;
  holdsCurrent: boolean;
  onNavigate?: () => void;
}) {
  const Icon = groupIcon(group.icon);
  return (
    <Popover>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <button type="button" aria-label={group.name} data-nav-item data-nav-tone={tone} aria-current={holdsCurrent ? "page" : undefined}
              className={cn("flex h-control-md items-center justify-center rounded-md text-ink-muted hover:bg-(--nav-hover) hover:text-ink focus-visible:focus-ring",
                holdsCurrent && activeClass)}>
              <span data-nav-icon={tone} className="inline-flex shrink-0 items-center justify-center"><Icon aria-hidden className="size-4 shrink-0" /></span>
            </button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="right">{group.name}</TooltipContent>
      </Tooltip>
      <PopoverContent side="right" align="start" className="w-64 p-2">
        <p className="px-2 pb-1 text-label uppercase tracking-wider text-ink-muted">{group.name}</p>
        <nav aria-label={group.name}>
          <DashboardLinks dashboards={group.dashboards} current={current} onNavigate={onNavigate} />
        </nav>
      </PopoverContent>
    </Popover>
  );
}
