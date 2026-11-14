import { Link, Outlet, useLocation } from "react-router-dom";

import { cn } from "../../lib/utils";
import { useMe } from "../me";
import { adminScreen, adminSections } from "./nav";

const pill = (active: boolean) => cn(
  "inline-flex h-control-sm items-center rounded-full px-3 text-small font-medium focus-visible:focus-ring",
  active ? "bg-accent-soft text-accent" : "text-ink-muted hover:bg-surface-sunken hover:text-ink",
);

/**
 * Admin sub-navigation (US-143): Overview, Data, Access, Content, Platform above every Admin and Govern screen. An area
 * links to its first screen; when the open area has more than one screen, they are listed after it.
 */
export function AdminNav() {
  const me = useMe().data;
  const { pathname } = useLocation();
  if (!me) return null;
  const sections = adminSections(me);
  if (sections.length === 0) return null;
  const here = adminScreen(sections, pathname);
  const onOverview = pathname === "/admin";
  return (
    <nav aria-label="Admin" className="tl mb-4 flex flex-wrap items-center gap-1 border-b border-line pb-3">
      <Link to="/admin" className={pill(onOverview)} aria-current={onOverview ? "page" : undefined}>Overview</Link>
      {sections.map((s) => {
        const active = here?.section.key === s.key;
        return (
          <Link key={s.key} to={s.screens[0]!.to} className={pill(active)} aria-current={active && s.screens.length === 1 ? "page" : undefined}>
            {s.label}
          </Link>
        );
      })}
      {here && here.section.screens.length > 1 && (
        <span className="ml-1 flex flex-wrap items-center gap-1 border-l border-line pl-2" aria-label={`${here.section.label} screens`} role="group">
          {here.section.screens.map((x) => {
            const active = x === here.screen;
            return (
              <Link key={x.to} to={x.to} aria-current={active ? "page" : undefined}
                className={cn("rounded-sm px-2 py-1 text-small focus-visible:focus-ring", active ? "text-ink underline decoration-accent decoration-2 underline-offset-4" : "text-ink-muted hover:text-ink")}>
                {x.label}
              </Link>
            );
          })}
        </span>
      )}
    </nav>
  );
}

/** Route layout for /admin and /govern: the sub-navigation, then the page. */
export function AdminLayout() {
  return (
    <>
      <AdminNav />
      <Outlet />
    </>
  );
}
