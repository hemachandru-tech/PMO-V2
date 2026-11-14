import { BadgeCheck, BookOpen, Code2, FlaskConical, PencilRuler, ShieldAlert, User, type LucideIcon } from "lucide-react";

import { cn } from "../lib/utils";
import { Badge } from "./ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

type Variant = "certified" | "personal" | "stale" | "outline" | "neutral";

/** [label, colour, what it means, icon]. Badges with an icon show as the icon; the words are in its tooltip and accessible name. */
const LABELS: Record<string, [string, Variant, string, LucideIcon?]> = {
  certified: ["Certified", "certified", "Every measure used is certified", BadgeCheck],
  personal: ["Personal", "personal", "Uses a personal field; not certified", User],
  exploratory: ["Exploratory", "stale", "Not certified; for exploration only", FlaskConical],
  custom_sql: ["Custom SQL", "personal", "Written in SQL by a Creator; checked for safety, not certified", Code2],
  direct_sql: ["Custom SQL · not certified", "personal", "The model wrote this query for your question (Direct SQL); Tela checked it, limited it to the rows you may see and ran it", Code2],
  not_certified: ["Not certified · built from your question", "stale", "A free-hand answer: Ask built this rule from your words; no Admin has certified it", ShieldAlert],
  your_definition: ["Counted by your definition", "stale", "Counted by the criteria in your question instead of the certified measure's rule", PencilRuler],
  knowledge_definition: ["Approved definition", "outline", "Counted by an approved business definition instead of the certified measure's rule", BookOpen],
  definition_conflict: ["Not certified · differs from a definition", "stale", "An approved business definition of a term in your question counts this differently", ShieldAlert],
  filter_left_out: ["Not certified · a filter was left out", "stale", "With every filter nothing matched, so Ask left out the one it was least sure of; check the reading", ShieldAlert],
  pending_certification: ["Pending certification", "stale", "A free-hand rule waiting for an Admin; only you can see it"],
  starter: ["Starter", "outline", "From a starter content pack"],
  draft: ["Draft", "stale", "Not published"],
  deleted: ["Deleted", "stale", "An Admin deleted this starter report; republish the dashboard to remove it"],
};

const ICON_TONE: Record<Variant, string> = {
  certified: "text-accent",
  personal: "text-personal",
  stale: "text-status-warn",
  outline: "text-ink-muted",
  neutral: "text-ink-muted",
};

/**
 * Certified, personal, custom SQL and draft badges stay next to every figure (NFR-021). Certification badges show as an
 * icon (shape, not colour alone) whose name and meaning are in a tooltip and in its accessible name; `text` keeps the
 * words on the page (print, where nothing can be hovered).
 */
export function ContentBadge({ badge, text = false }: { badge: string; text?: boolean }) {
  const [label, variant, title, Icon] = LABELS[badge] ?? [badge, "neutral" as const, badge];
  if (!Icon || text) return <Badge variant={variant} title={title}>{label}</Badge>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} data-badge={badge} aria-label={`${label}: ${title}`}
          className={cn("inline-flex size-6 shrink-0 items-center justify-center rounded-full focus-visible:focus-ring", ICON_TONE[variant])}>
          <Icon aria-hidden className="size-4" />
          <span className="sr-only">{label}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">
        <span className="font-semibold">{label}</span>
        <span className="block opacity-90">{title}</span>
      </TooltipContent>
    </Tooltip>
  );
}
