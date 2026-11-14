import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Fragment } from "react";
import { NavLink, useLocation } from "react-router-dom";

import type { Me } from "../../api/types";
import { Button } from "../../components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import type { NavStyle } from "../../design/navColors";
import { cn } from "../../lib/utils";
import { BrandLogo, useBranding } from "../branding";
import { activeItem, GROUPS, visibleNav } from "./nav";
import { navAttrs, NavTheme, useNavStyle } from "./NavScope";
import { SidebarGroups } from "./SidebarGroups";

export function levelLabel(me: Me): string {
  const level = me.level[0]!.toUpperCase() + me.level.slice(1);
  return me.audience_roles.length ? `${level} · ${me.audience_roles.join(", ")}` : level;
}

/**
 * Sidebar (design system: Sidebar; US-137): brand, navigation grouped as Explore, Build, Govern and Administer,
 * the dashboard groups between Explore and Build for every role (ENH-009, US-171), and who is signed in. Collapses to icons with tooltips; below 1024 px it is shown in a drawer instead.
 * Coloured as the tenant's branding says (US-209); `navStyle` overrides it for the Branding preview.
 */
export function Sidebar({ me, collapsed = false, onToggle, onNavigate, navStyle }: {
  me: Me;
  collapsed?: boolean;
  onToggle?: () => void;
  onNavigate?: () => void;
  navStyle?: NavStyle;
}) {
  const { pathname } = useLocation();
  const items = visibleNav(me);
  const current = activeItem(items, pathname);
  const brand = useBranding();
  const style = useNavStyle("sidebar", navStyle);
  // A bar filled with the brand colour is dark in the light theme too, so it shows the logo made for dark backgrounds.
  const logoTheme = style === "brand" ? "dark" : undefined;
  return (
    <NavTheme style={style}>
    <nav aria-label="Main" {...navAttrs("sidebar", style)}
      className={cn("flex h-full flex-col gap-4 bg-(--nav-bg) px-2 py-3", collapsed ? "w-14" : "w-sidebar-width")}>
      <NavLink to="/" onClick={onNavigate} className={cn("flex items-center gap-2 rounded-md px-2 py-1 text-ink focus-visible:focus-ring", collapsed && "justify-center px-0")}>
        {collapsed ? <BrandLogo branding={brand} theme={logoTheme} className="size-7 shrink-0" />
          : <BrandLogo branding={brand} theme={logoTheme} place="sidebar" maxWidth={160} className="shrink-0" />}
        {!collapsed && (
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 leading-tight">
            <span className="break-words text-[18px] font-semibold tracking-tight">{brand.app_name}</span>
            {brand.tagline && <span className="break-words text-small text-ink-muted">{brand.tagline}</span>}
          </span>
        )}
        {collapsed && <span className="sr-only">{brand.app_name} home</span>}
      </NavLink>

      <div className="grid gap-4 overflow-y-auto">
        {GROUPS.map((group) => {
          const groupItems = items.filter((i) => i.group === group);
          if (groupItems.length === 0) return null;
          return (
            <Fragment key={group}>
              <div className="grid gap-0.5" role="group" aria-labelledby={`nav-${group}`}>
                <span id={`nav-${group}`} className={cn("px-2 pb-1 text-label uppercase tracking-wider text-ink-muted", collapsed && "sr-only")}>{group}</span>
                {groupItems.map((item) => {
                  const isCurrent = current?.key === item.key;
                  const link = (
                    <NavLink
                      data-nav-item
                      data-nav-tone={(items.indexOf(item) % 8) + 1}
                      to={item.to}
                      end={item.to === "/"}
                      onClick={onNavigate}
                      aria-current={isCurrent ? "page" : undefined}
                      className={cn(
                        "flex h-control-md items-center gap-2 rounded-md px-2 text-body text-ink-muted hover:bg-(--nav-hover) hover:text-ink focus-visible:focus-ring",
                        "transition-colors duration-150 motion-reduce:transition-none",
                        isCurrent && "bg-(--nav-current-bg) font-medium text-(--nav-current-ink) hover:bg-(--nav-current-bg) hover:text-(--nav-current-ink)",
                        collapsed && "justify-center px-0",
                      )}
                    >
                      {/* Aurora (ENH-027) puts each screen's icon on a chip of its own colour (index.css). */}
                      <span data-nav-icon={(items.indexOf(item) % 8) + 1} className="inline-flex shrink-0 items-center justify-center">
                        <item.icon aria-hidden className="size-4 shrink-0" />
                      </span>
                      {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
                    </NavLink>
                  );
                  return collapsed ? (
                    <Tooltip key={item.key}>
                      <TooltipTrigger asChild>{link}</TooltipTrigger>
                      <TooltipContent side="right">{item.label}</TooltipContent>
                    </Tooltip>
                  ) : <span key={item.key} className="contents">{link}</span>;
                })}
              </div>
              {group === "Explore" && <SidebarGroups me={me} pathname={pathname} collapsed={collapsed} onNavigate={onNavigate} />}
            </Fragment>
          );
        })}
      </div>

      <div className={cn("mt-auto flex items-center gap-2 border-t border-line px-2 pt-3", collapsed && "flex-col px-0")}>
        {!collapsed && (
          <div className="grid min-w-0 flex-1">
            <span className="truncate font-medium text-ink">{me.display_name}</span>
            <span className="truncate text-small text-ink-muted">{levelLabel(me)}</span>
          </div>
        )}
        {onToggle && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon" onClick={onToggle} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-expanded={!collapsed}>
                {collapsed ? <PanelLeftOpen aria-hidden className="size-4" /> : <PanelLeftClose aria-hidden className="size-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">{collapsed ? "Expand sidebar" : "Collapse sidebar"}</TooltipContent>
          </Tooltip>
        )}
      </div>
    </nav>
    </NavTheme>
  );
}
