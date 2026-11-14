import { useQuery } from "@tanstack/react-query";
import { Lightbulb, Loader2 } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "../api/client";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Skeleton } from "./ui/skeleton";

export interface ChartSuggestion {
  id: string;
  kind: "visual" | "measure" | "breakdown" | "top_n" | "comparison" | "sort";
  title: string;
  reason: string;
  ops: Record<string, unknown>[];
  visual: string;
  changes: string[];
  source: "rules" | "pattern" | "llm";
  pattern: string | null;
  cites: string[];
}

export interface SuggestionsOut {
  suggestions: ChartSuggestion[];
  message: string | null;
  source: "rules" | "llm";
  limited_context?: boolean;
  patterns?: { ref: string; title: string; pattern: string | null }[];
}

const SOURCE: Record<ChartSuggestion["source"], string | null> = { rules: null, pattern: "House chart pattern", llm: "Suggested by AI" };

/**
 * Chart suggestions (AG-07; US-104, FEAT-016): up to three changes with a reason, each already checked against the chart
 * rules. Nothing changes until Apply; Apply is a refinement (answer) or one report change (canvas), so Undo takes it back.
 */
export function SuggestionsButton({ url, label, onApply, compact = false }: {
  url: string;
  /** What the suggestions are for, for screen readers ("this answer", a widget title). */
  label: string;
  /** An icon button with its name in a tooltip (Ask's action row, US-261). */
  compact?: boolean;
  onApply: (s: ChartSuggestion) => Promise<unknown> | void;
}) {
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState<string[]>([]);
  const [applying, setApplying] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["suggestions", url], queryFn: () => api<SuggestionsOut>(url), enabled: open, staleTime: 30_000 });
  const shown = (q.data?.suggestions ?? []).filter((s) => !dismissed.includes(s.id));
  const apply = async (s: ChartSuggestion) => {
    setApplying(s.id);
    try {
      await onApply(s);
      setOpen(false);
    } finally {
      setApplying(null);
    }
  };
  return (
    <Popover open={open} onOpenChange={(o) => { setOpen(o); if (o) setDismissed([]); }}>
      <PopoverTrigger asChild>
        {compact ? (
          <Button variant="ghost" size="icon" aria-label={`Suggestions for ${label}`} title="Suggestions"
            className="size-8 rounded-lg text-ink-muted hover:text-ink"><Lightbulb aria-hidden /></Button>
        ) : (
          <Button variant="ghost" size="sm" aria-label={`Suggestions for ${label}`}><Lightbulb aria-hidden />Suggestions</Button>
        )}
      </PopoverTrigger>
      <PopoverContent align="start" className="grid w-96 max-w-[calc(100vw-2rem)] gap-3" aria-label={`Suggestions for ${label}`}>
        <h3 className="text-small font-medium text-ink-muted">Make this chart clearer</h3>
        {q.isLoading && <div className="grid gap-2" aria-label="Loading suggestions"><Skeleton className="h-14" /><Skeleton className="h-14" /></div>}
        {q.isError && <p className="text-small text-status-danger">{q.error instanceof ApiError ? q.error.message : "Suggestions are not available right now."}</p>}
        {q.data && shown.length === 0 && <p className="text-small">{q.data.message ?? "Looks good as is."}</p>}
        {shown.length > 0 && (
          <ul className="grid gap-2">
            {shown.map((s) => (
              <li key={s.id} className="grid gap-1.5 rounded-md border border-line p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{s.title}</span>
                  {SOURCE[s.source] && <Badge variant="outline">{SOURCE[s.source]}</Badge>}
                </div>
                <p className="text-small text-ink-muted">{s.reason}</p>
                {s.changes.length > 0 && <p className="text-small text-ink-muted">Changes: {s.changes.join("; ")}</p>}
                <div className="flex gap-2">
                  <Button size="sm" disabled={applying !== null} onClick={() => void apply(s)} aria-label={`Apply: ${s.title}`}>
                    {applying === s.id && <Loader2 aria-hidden className="animate-spin" />}Apply
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setDismissed((d) => [...d, s.id])} aria-label={`Dismiss: ${s.title}`}>Dismiss</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {q.data?.limited_context && <p className="text-small text-ink-muted">Limited context: chart patterns were searched by keyword only.</p>}
      </PopoverContent>
    </Popover>
  );
}
