import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { toast } from "sonner";

import { api, ApiError } from "../api/client";
import { publishedDashboardQuery } from "../api/app";
import type { Me } from "../api/types";
import { Button } from "../components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "../components/ui/sheet";
import { applyUserLayout, useLayout } from "../design/layout";
import { useTheme, type ThemePreference } from "../design/theme";
import { cn } from "../lib/utils";
import { useAuth } from "./auth";
import { BrandLogo, useBranding } from "./branding";
import { useMe } from "./me";
import { CommandPalette } from "./shell/CommandPalette";
import { Sidebar } from "./shell/Sidebar";
import { Topbar } from "./shell/Topbar";

/**
 * App shell (US-137): skip link, role-aware sidebar (a drawer below 1024 px), top bar with breadcrumbs, the persistent
 * ask bar (REQ-042), notifications and the account menu, and the command palette (⌘K / Ctrl+K).
 * Old screens render inside `main` unchanged; the shell itself is on the new design system (`.tl`). The layout
 * (ENH-021) picks the frame: Classic the sidebar, Executive the navigation in a brand-coloured top bar, Command centre
 * a dark icon rail, Aurora (ENH-027) the sidebar floating as a card and the top bar on the page. Aurora's sidebar takes
 * the company's sidebar colour (US-209); its top bar stays on the canvas design. The drawer below 1024 px is the same in all.
 */
export function AppShell() {
  const me = useMe();
  const { signOut } = useAuth();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [palette, setPalette] = useState(false);
  useThemePreference(me.data);
  const layout = useLayout();
  const saved = me.data?.preferences?.layout;
  useEffect(() => applyUserLayout(saved), [saved]);
  usePrefetchDashboard(pathname);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => setDrawer(false), [pathname]);

  if (me.isLoading) return <Centered><p aria-busy="true" className="text-ink-muted">Loading…</p></Centered>;
  if (me.error) {
    if (me.error instanceof ApiError && me.error.code === "no_access") return <NoAccess message={me.error.message} />;
    return (
      <Centered>
        <p role="alert">{me.error.message}</p>
        {me.error.requestId && <p className="text-small break-all text-ink-muted">Request id: {me.error.requestId}</p>}
      </Centered>
    );
  }
  const user = me.data!;
  const signOutNow = () => void signOut();

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="tl sr-only z-50 rounded-md bg-surface-raised px-3 py-2 text-ink shadow-overlay focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus-visible:focus-ring">
        Skip to content
      </a>
      {layout !== "executive" && (
        <aside data-shell-aside className={cn("tl sticky top-0 hidden h-screen shrink-0 lg:block", layout !== "aurora" && "border-r border-line")}>
          {layout === "command"
            ? <Sidebar me={user} collapsed navStyle="dark" />
            : <Sidebar me={user} collapsed={collapsed} onToggle={() => setCollapsed(!collapsed)} />}
        </aside>
      )}
      <Sheet open={drawer} onOpenChange={setDrawer}>
        <SheetContent side="left" className="w-auto p-0 sm:max-w-none">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <SheetDescription className="sr-only">Screens you can open</SheetDescription>
          <Sidebar me={user} onNavigate={() => setDrawer(false)} />
        </SheetContent>
      </Sheet>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="tl">
          <Topbar me={user} onMenu={() => setDrawer(true)} onPalette={() => setPalette(true)} onSignOut={signOutNow}
            {...(layout === "executive" ? { withNav: true, navStyle: "brand" as const } : layout === "aurora" ? { navStyle: "standard" as const } : {})} />
        </div>
        <main id="main" tabIndex={-1} className="w-full min-w-0 flex-1 px-4 pb-16 pt-6 outline-none lg:px-6">
          <Outlet />
        </main>
      </div>
      <div className="tl"><CommandPalette me={user} open={palette} onOpenChange={setPalette} /></div>
    </div>
  );
}

/** Applies the signed-in user's saved theme and saves changes to it (US-134). */
function useThemePreference(me: Me | undefined) {
  const { applyPreference, registerSaver } = useTheme();
  const qc = useQueryClient();
  const saved = me?.preferences?.theme;
  useEffect(() => {
    if (saved) applyPreference(saved);
  }, [saved, applyPreference]);
  useEffect(() => {
    if (!me) return;
    registerSaver((theme: ThemePreference) => {
      api<Me["preferences"]>("/api/v1/me/preferences", { method: "PUT", body: { theme } })
        .then((prefs) => qc.setQueryData<Me>(["me"], (old) => (old ? { ...old, preferences: prefs } : old)))
        .catch((err: Error) => toast.error("Your theme could not be saved", { description: err.message }));
    });
    return () => registerSaver(null);
  }, [me, qc, registerSaver]);
}

function Centered({ children }: { children: React.ReactNode }) {
  const brand = useBranding();
  return (
    <main className="tl grid min-h-screen place-items-center p-6">
      <div className="grid max-w-md justify-items-center gap-3 text-center">
        <BrandLogo branding={brand} place="sign_in" maxWidth={320} />
        {children}
      </div>
    </main>
  );
}

export function NoAccess({ message }: { message: string }) {
  const { signOut } = useAuth();
  const brand = useBranding();
  return (
    <Centered>
      <h1 className="text-title">{brand.app_name}</h1>
      <p role="alert">{message}</p>
      <Button variant="secondary" onClick={() => void signOut()}>Sign out</Button>
    </Centered>
  );
}

/** On first load of a dashboard link, fetch the dashboard while `/me` is still loading (the viewer reuses it). */
function usePrefetchDashboard(pathname: string) {
  const qc = useQueryClient();
  const [first] = useState(pathname);
  useEffect(() => {
    const id = /^\/dashboards\/([^/]+)\/?$/.exec(first)?.[1];
    if (id && id !== "new") void qc.prefetchQuery(publishedDashboardQuery(id));
  }, [first, qc]);
}

