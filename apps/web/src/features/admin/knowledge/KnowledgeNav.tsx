import { NavLink } from "react-router-dom";

import { cn } from "../../../lib/utils";

const TABS = [
  { to: "/admin/knowledge", label: "Documents & Knowledge", end: true },
  { to: "/admin/knowledge/definitions", label: "Glossary & Definitions", end: false },
  { to: "/admin/knowledge/conditions", label: "Rules & Conditions", end: false },
];

/** Knowledge has seven pages (SCR-13): reference documents, the business definitions read from them (US-203), the
 * named conditions every Ask mode counts by (US-252), the gold
 * examples that measure answer accuracy, the value synonyms Ask links people's words with (US-173), the answers people
 * flagged (US-183), and the proposals made from Ask's misses (US-198). */
export function KnowledgeNav() {
  return (
    <nav aria-label="Knowledge" className="flex gap-1 border-b border-line">
      {TABS.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end} className={({ isActive }) => cn(
          "-mb-px border-b-2 px-3 py-2 text-small font-medium focus-visible:focus-ring",
          isActive ? "border-accent text-ink" : "border-transparent text-ink-muted hover:text-ink",
        )}>{t.label}</NavLink>
      ))}
    </nav>
  );
}
