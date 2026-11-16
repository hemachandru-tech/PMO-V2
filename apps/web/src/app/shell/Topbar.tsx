import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, LayoutTemplate, LogOut, Menu, Monitor, Moon, Search, Sun } from "lucide-react";
import { Fragment, useEffect, useState, type FormEvent } from "react";
import { Link, NavLink, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { api } from "../../api/client";
import type { Me } from "../../api/types";
import { Notifications } from "../../components/Notifications";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { applyUserLayout, LAYOUTS, useUserLayout, type LayoutPreference } from "../../design/layout";
import type { NavStyle } from "../../design/navColors";
import { cn } from "../../lib/utils";
import { useTheme, type ThemePreference } from "../../design/theme";
import { BrandLogo, pageTitle, useBranding } from "../branding";
import { activeItem, breadcrumbs, GROUPS, visibleNav } from "./nav";
import { navAttrs, NavTheme, useNavStyle } from "./NavScope";
import { levelLabel } from "./Sidebar";

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
export const PALETTE_SHORTCUT = isMac ? "⌘K" : "Ctrl K";

/**
 * Top bar (US-137): menu button below 1024 px, breadcrumbs, the persistent ask bar (REQ-042), notifications, account.
 * Coloured as the tenant's branding says (US-209); `navStyle` overrides it for the Branding preview. In the Executive
 * layout (ENH-021) it also carries the brand and the main navigation, as there is no sidebar.
 */
export function Topbar({ me, onMenu, onPalette, onSignOut, navStyle, withNav = false }: {
  me: Me; onMenu: () => void; onPalette: () => void; onSignOut: () => void; navStyle?: NavStyle; withNav?: boolean;
}) {
  const style = useNavStyle("topbar", navStyle);
  return (
    <NavTheme style={style}>
    <header {...navAttrs("topbar", style)}
      className="sticky top-0 z-30 flex min-h-topbar-height flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-(--nav-bg) px-4 py-2 lg:px-6">
      <Button variant="ghost" size="icon" className="lg:hidden" onClick={onMenu} aria-label="Open navigation">
        <Menu aria-hidden className="size-5" />
      </Button>
      {withNav ? <TopNav me={me} logoTheme={style === "brand" ? "dark" : undefined} /> : <Breadcrumbs me={me} />}
      <AskBar onPalette={onPalette} />
      <div className="flex items-center gap-1">
        <Notifications />
        <AccountMenu me={me} onSignOut={onSignOut} />
      </div>
    </header>
    </NavTheme>
  );
}

/**
 * The main navigation across the top (Executive layout, ENH-021): the brand, Explore's screens as links and each other
 * group as a menu. Below 1024 px the menu button opens the drawer instead. The page title still names the screen.
 */
function TopNav({ me, logoTheme }: { me: Me; logoTheme?: "dark" }) {
  const { pathname } = useLocation();
  const items = visibleNav(me);
  const current = activeItem(items, pathname);
  const brand = useBranding();
  useEffect(() => {
    document.title = pageTitle(breadcrumbs(items, pathname).at(-1)!.label, brand);
  }, [items, pathname, brand]);
  const link = (active: boolean) => cn(
    "inline-flex h-control-md items-center gap-1.5 rounded-full px-3 text-body text-ink-muted hover:bg-(--nav-hover) hover:text-ink focus-visible:focus-ring",
    active && "bg-(--nav-current-bg) font-medium text-(--nav-current-ink) hover:bg-(--nav-current-bg) hover:text-(--nav-current-ink)",
  );
  return (
    <div className="flex min-w-0 flex-[1_1_24rem] items-center gap-4">
      <Link to="/" className="flex shrink-0 items-center gap-2 rounded-md text-ink focus-visible:focus-ring">
        <BrandLogo branding={brand} theme={logoTheme} place="sidebar" maxWidth={140} className="shrink-0" />
        <span className="text-[17px] font-semibold tracking-tight">{brand.app_name}</span>
      </Link>
      <nav aria-label="Main" className="hidden min-w-0 flex-wrap items-center gap-1 lg:flex">
        {GROUPS.map((group) => {
          const groupItems = items.filter((i) => i.group === group);
          if (groupItems.length === 0) return null;
          if (group === "Explore") {
            return groupItems.map((item) => (
              <NavLink key={item.key} to={item.to} end={item.to === "/"} aria-current={current?.key === item.key ? "page" : undefined}
                className={link(current?.key === item.key)}>{item.label}</NavLink>
            ));
          }
          const inGroup = groupItems.some((i) => i.key === current?.key);
          return (
            <DropdownMenu key={group}>
              <DropdownMenuTrigger className={link(inGroup)}>{group}<ChevronDown aria-hidden className="size-3.5" /></DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-56">
                {groupItems.map((item) => (
                  <DropdownMenuItem key={item.key} asChild>
                    <Link to={item.to} aria-current={current?.key === item.key ? "page" : undefined}><item.icon aria-hidden />{item.label}</Link>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          );
        })}
      </nav>
    </div>
  );
}

function Breadcrumbs({ me }: { me: Me }) {
  const { pathname } = useLocation();
  const crumbs = breadcrumbs(visibleNav(me), pathname);
  const title = crumbs[crumbs.length - 1]!.label;
  const brand = useBranding();
  useEffect(() => {
    document.title = pageTitle(title, brand);
  }, [title, brand]);
  return (
    <nav aria-label="Breadcrumb" className="hidden min-w-0 flex-[1_1_12rem] sm:block">
      <ol className="flex items-center gap-1.5 text-ink-muted">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <Fragment key={`${c.label}-${i}`}>
              {i > 0 && <li aria-hidden>/</li>}
              <li className={last ? "truncate font-medium text-ink" : "whitespace-nowrap"} aria-current={last ? "page" : undefined}>
                {c.to && !last ? <Link className="hover:text-ink hover:underline focus-visible:focus-ring" to={c.to}>{c.label}</Link> : c.label}
              </li>
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

/** The persistent ask bar (REQ-042): Enter asks; the shortcut chip opens the command palette. */
function AskBar({ onPalette }: { onPalette: () => void }) {
  const [params] = useSearchParams();
  const { pathname } = useLocation();
  const [question, setQuestion] = useState("");
  const navigate = useNavigate();
  // Clear the bar once the question has been handed to Ask.
  useEffect(() => {
    if (pathname === "/ask" && params.get("q")) setQuestion("");
  }, [pathname, params]);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (question.trim()) navigate(`/ask?q=${encodeURIComponent(question.trim())}`);
  };
  return (
    <form role="search" onSubmit={submit} data-ask-bar
      className="flex h-control-lg min-w-0 flex-[3_1_20rem] items-center gap-2 rounded-md border border-line-strong bg-surface pl-3 pr-1 focus-within:focus-ring lg:max-w-[560px]">
      <Search aria-hidden className="size-4 shrink-0 text-ink-muted" />
      <label htmlFor="ask" className="sr-only">Ask anything about your data</label>
      <input
        id="ask"
        type="search"
        placeholder="Ask anything about your data"
        value={question}
        maxLength={2000}
        onChange={(e) => setQuestion(e.target.value)}
        className="h-full min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-ink-disabled"
      />
      <button type="button" onClick={onPalette} aria-label="Open command palette"
        className="hidden rounded-sm border border-line px-1 font-mono text-[11px] text-ink-muted hover:text-ink focus-visible:focus-ring sm:block">
        {PALETTE_SHORTCUT}
      </button>
      <Button type="submit" size="sm" variant="ghost">Ask</Button>
    </form>
  );
}

const THEMES: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "Match my system", icon: Monitor },
];

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("") || "?";

/** User menu: name and level, theme (saved per user, US-134), layout (ENH-021) and sign out. */
function AccountMenu({ me, onSignOut }: { me: Me; onSignOut: () => void }) {
  const { preference, setPreference } = useTheme();
  const layout = useUserLayout();
  const qc = useQueryClient();
  const saveLayout = (value: LayoutPreference) => {
    const before = layout;
    applyUserLayout(value);
    api<Me["preferences"]>("/api/v1/me/preferences", { method: "PUT", body: { layout: value } })
      .then((prefs) => qc.setQueryData<Me>(["me"], (old) => (old ? { ...old, preferences: prefs } : old)))
      .catch((err: Error) => {
        applyUserLayout(before);
        toast.error("Your layout could not be saved", { description: err.message });
      });
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="h-9 gap-2.5 rounded-full px-2 hover:bg-surface-sunken" aria-label="Account menu">
          <span aria-hidden className="grid size-7 place-items-center rounded-full bg-[#2B59FF] text-xs font-semibold text-white shadow-sm">
            {initials(me.display_name)}
          </span>
          <div className="hidden sm:flex flex-col text-left text-xs leading-none">
            <span className="font-semibold text-ink">{me.display_name}</span>
            <span className="text-[10px] text-ink-muted mt-0.5">{levelLabel(me)}</span>
          </div>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <div className="grid px-2 py-1.5">
          <span className="font-medium text-ink">{me.display_name}</span>
          <span className="text-small text-ink-muted">{levelLabel(me)}</span>
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={preference} onValueChange={(v) => setPreference(v as ThemePreference)}>
          {THEMES.map((t) => (
            <DropdownMenuRadioItem key={t.value} value={t.value}>
              <t.icon aria-hidden />
              {t.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Layout</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={layout} onValueChange={(v) => saveLayout(v as LayoutPreference)}>
          <DropdownMenuRadioItem value="default"><LayoutTemplate aria-hidden />Company default</DropdownMenuRadioItem>
          {LAYOUTS.map((l) => (
            <DropdownMenuRadioItem key={l.id} value={l.id} title={l.note}>
              <LayoutTemplate aria-hidden />
              {l.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onSignOut}>
          <LogOut aria-hidden />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
