import {
  BadgeCheck,
  BookOpen,
  Boxes,
  Code2,
  Database,
  FileText,
  Gauge,
  History,
  House,
  LayoutDashboard,
  MessageSquare,
  Package,
  Palette,
  Send,
  Shapes,
  Sparkles,
  ShieldCheck,
  Users,
  type LucideIcon,
} from "lucide-react";

import type { Me } from "../../api/types";
import { can } from "../me";

export type NavGroup = "Explore" | "Build" | "Govern" | "Administer";

export interface NavItem {
  key: string;
  label: string;
  to: string;
  icon: LucideIcon;
  group: NavGroup;
  /** Shown only when this returns true. Hiding is a convenience; the API enforces every permission. */
  visible: (me: Me) => boolean;
  /** Other path prefixes that belong to this item (for the active state and breadcrumbs). */
  also?: string[];
}

export const GROUPS: NavGroup[] = ["Explore", "Build", "Govern", "Administer"];

const any = (me: Me, ...permissions: string[]) => permissions.some((p) => can(me, p));

/**
 * Main navigation (US-137; design system: Sidebar), grouped by what people do. Viewers see Explore; Creators add Build;
 * Admins add Govern and Administer. An item appears once its screen exists: Govern → Certification came with US-087 and
 * Publish requests with US-142, Administer → Overview with US-143, AI settings with US-146, Starter content with
 * US-099, Knowledge with EPIC-03 and Branding with US-149.
 */
export const NAV: NavItem[] = [
  { key: "home", label: "Home", to: "/", icon: House, group: "Explore", visible: () => true },
  { key: "ask", label: "Ask", to: "/ask", icon: MessageSquare, group: "Explore", visible: (me) => can(me, "ask.use") },
  { key: "shared", label: "Shared with me", to: "/shared", icon: Users, group: "Explore", visible: (me) => can(me, "dashboards.view") },
  { key: "reports", label: "Reports", to: "/reports", icon: FileText, group: "Build", visible: (me) => can(me, "reports.create") },
  {
    key: "dashboards", label: "Dashboards", to: "/dashboards", icon: LayoutDashboard, group: "Build",
    visible: (me) => any(me, "dashboards.create", "dashboards.publish"),
  },
  { key: "fields", label: "Fields", to: "/fields", icon: Shapes, group: "Build", visible: (me) => can(me, "fields.create") },
  { key: "sql", label: "Custom SQL", to: "/sql", icon: Code2, group: "Build", visible: (me) => can(me, "customsql.use") },
  {
    key: "certification", label: "Certification", to: "/govern/certification", icon: BadgeCheck, group: "Govern",
    visible: (me) => can(me, "certification.decide"),
  },
  {
    key: "publish-requests", label: "Publish requests", to: "/govern/publish-requests", icon: Send, group: "Govern",
    visible: (me) => can(me, "dashboards.publish"),
  },
  {
    key: "admin", label: "Overview", to: "/admin", icon: Gauge, group: "Administer",
    visible: (me) => adminSections(me).length > 0,
  },
  {
    key: "data", label: "Data", to: "/admin/connections", icon: Database, group: "Administer",
    visible: (me) => can(me, "connections.manage"), also: ["/admin/models", "/admin/ssh-keys"],
  },
  { key: "access", label: "Access", to: "/admin/access", icon: ShieldCheck, group: "Administer", visible: (me) => any(me, "mappings.manage", "policies.manage") },
  { key: "starter", label: "Starter content", to: "/admin/starter", icon: Package, group: "Administer", visible: (me) => can(me, "starter.manage") },
  { key: "packs", label: "Packs", to: "/admin/packs", icon: Boxes, group: "Administer", visible: (me) => can(me, "packs.author") },
  { key: "knowledge", label: "Knowledge", to: "/admin/knowledge", icon: BookOpen, group: "Administer", visible: (me) => can(me, "knowledge.manage") },
  { key: "audit", label: "Audit log", to: "/admin/audit", icon: History, group: "Administer", visible: (me) => can(me, "audit.read") },
  { key: "ai", label: "AI settings", to: "/admin/ai", icon: Sparkles, group: "Administer", visible: (me) => can(me, "settings.manage"), also: ["/admin/agent-runs", "/admin/agents"] },
  { key: "branding", label: "Branding", to: "/admin/branding", icon: Palette, group: "Administer", visible: (me) => can(me, "branding.manage") },
];

export interface AdminScreen {
  label: string;
  to: string;
  visible: (me: Me) => boolean;
  /** Other path prefixes that belong to this screen. */
  also?: string[];
}

export interface AdminSection {
  key: "data" | "access" | "content" | "platform";
  label: string;
  screens: AdminScreen[];
}

/**
 * Admin sub-navigation (US-143): the Admin console's four areas and the screens in each. Starter content (US-099)
 * joined Content with EPIC-11; AI settings (US-106) joined Platform with US-146, Knowledge (SCR-13) with EPIC-03.
 */
export const ADMIN_SECTIONS: AdminSection[] = [
  { key: "data", label: "Data", screens: [
    { label: "Connections", to: "/admin/connections", visible: (me) => can(me, "connections.manage"), also: ["/admin/models"] },
    { label: "SSH keys", to: "/admin/ssh-keys", visible: (me) => can(me, "connections.manage") },
  ] },
  { key: "access", label: "Access", screens: [
    { label: "Access", to: "/admin/access", visible: (me) => any(me, "mappings.manage", "policies.manage") },
  ] },
  { key: "content", label: "Content", screens: [
    { label: "Starter content", to: "/admin/starter", visible: (me) => can(me, "starter.manage") },
    { label: "Packs", to: "/admin/packs", visible: (me) => can(me, "packs.author") },
    { label: "Certification", to: "/govern/certification", visible: (me) => can(me, "certification.decide") },
    { label: "Publish requests", to: "/govern/publish-requests", visible: (me) => can(me, "dashboards.publish") },
  ] },
  { key: "platform", label: "Platform", screens: [
    { label: "Knowledge", to: "/admin/knowledge", visible: (me) => can(me, "knowledge.manage") },
    { label: "Audit log", to: "/admin/audit", visible: (me) => can(me, "audit.read") },
    { label: "AI settings", to: "/admin/ai", visible: (me) => can(me, "settings.manage") },
    { label: "Agents", to: "/admin/agents", visible: (me) => can(me, "settings.manage") },
    { label: "Agent runs", to: "/admin/agent-runs", visible: (me) => can(me, "settings.manage") },
    { label: "Branding", to: "/admin/branding", visible: (me) => can(me, "branding.manage") },
  ] },
];

/** The Admin areas this person can open, each with only the screens they can open. */
export function adminSections(me: Me): AdminSection[] {
  return ADMIN_SECTIONS.map((s) => ({ ...s, screens: s.screens.filter((x) => x.visible(me)) })).filter((s) => s.screens.length > 0);
}

/** The screen a path belongs to, if any. */
export function adminScreen(sections: AdminSection[], path: string): { section: AdminSection; screen: AdminScreen } | undefined {
  for (const section of sections) {
    for (const screen of section.screens) {
      if ([screen.to, ...(screen.also ?? [])].some((p) => under(path, p))) return { section, screen };
    }
  }
  return undefined;
}

export function visibleNav(me: Me): NavItem[] {
  return NAV.filter((item) => item.visible(me));
}

const prefixes = (item: NavItem) => [item.to, ...(item.also ?? [])];
const under = (path: string, prefix: string) => (prefix === "/" ? path === "/" : path === prefix || path.startsWith(`${prefix}/`));

/** The item a path belongs to: the longest matching prefix among the items this person can see. */
export function activeItem(items: NavItem[], path: string): NavItem | undefined {
  let best: { item: NavItem; length: number } | undefined;
  for (const item of items) {
    for (const p of prefixes(item)) {
      if (under(path, p) && (!best || p.length > best.length)) best = { item, length: p.length };
    }
  }
  return best?.item;
}

/** Names for pages below a navigation item. */
const LEAVES: [RegExp, string][] = [
  [/^\/reports\/new$/, "New report"],
  [/^\/reports\/[^/]+$/, "Report"],
  [/^\/dashboards\/new$/, "New dashboard"],
  [/^\/dashboards\/[^/]+\/edit$/, "Composer"],
  [/^\/dashboards\/[^/]+\/draft$/, "Draft preview"],
  [/^\/dashboards\/[^/]+\/publish$/, "Publish"],
  [/^\/dashboards\/[^/]+\/versions$/, "Version history"],
  [/^\/dashboards\/[^/]+$/, "Dashboard"],
  [/^\/sql\/new$/, "New custom SQL"],
  [/^\/sql\/[^/]+$/, "Editor"],
  [/^\/fields\/new$/, "New field"],
  [/^\/fields\/[^/]+$/, "Field"],
  [/^\/admin\/connections\/new$/, "Add a connection"],
  [/^\/admin\/models\/[^/]+$/, "Model studio"],
  [/^\/admin\/models\/[^/]+\/approve$/, "Approve model"],
  [/^\/admin\/access\/mappings$/, "Group mappings"],
  [/^\/admin\/access\/scope$/, "Scope policies"],
  [/^\/admin\/access\/fields$/, "Field policies"],
  [/^\/admin\/access\/provisioning$/, "Provisioning"],
  [/^\/admin\/access\/freehand$/, "Free-hand query"],
  [/^\/admin\/knowledge\/gold$/, "Gold examples"],
  [/^\/notifications$/, "Notifications"],
  [/^\/dev\/ui$/, "Component catalogue"],
];

export interface Crumb {
  label: string;
  to?: string;
}

/** Breadcrumbs: group › item › page. Pages outside the person's menu (a shared dashboard) sit under Explore. */
export function breadcrumbs(items: NavItem[], path: string): Crumb[] {
  const item = activeItem(items, path);
  const leaf = LEAVES.find(([re]) => re.test(path))?.[1];
  if (!item) return [{ label: "Explore" }, { label: leaf ?? "Page not found" }];
  const crumbs: Crumb[] = [{ label: item.group }];
  crumbs.push(leaf && path !== item.to ? { label: item.label, to: item.to } : { label: item.label });
  if (leaf && path !== item.to) crumbs.push({ label: leaf });
  return crumbs;
}
