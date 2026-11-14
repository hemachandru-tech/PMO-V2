import { useQuery } from "@tanstack/react-query";
import { Info } from "lucide-react";
import { useState, type ReactNode } from "react";

import { api } from "../api/client";
import type { DefinitionPopover as Def } from "../api/app";
import type { QuerySpec } from "../api/querySpec";
import { asApiError } from "../lib/api-errors";
import { FormAlert } from "./ui/alert";
import { ContentBadge } from "./ContentBadge";
import { Button } from "./ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./ui/popover";
import { Skeleton } from "./ui/skeleton";

export interface DefinitionProps {
  refId: string;
  connectionId: string;
  modelVersionId?: string;
  /** The filters in force, in words, so the definition says what the figure covers. */
  filtersText?: string;
  /** Lets Creators and Admins see the query (API decides through `can_show_query`). */
  spec?: QuerySpec;
}

/**
 * Every number explains itself (FEAT-043; design system: DefinitionPopover): rule, fields, blanks, certification,
 * scope and filters; "Show query" for Creators and Admins. The body is shared by the popover and the drill sheet.
 */
export function DefinitionBody({ refId, connectionId, modelVersionId, filtersText, spec }: DefinitionProps) {
  const def = useQuery({
    queryKey: ["definition", refId, connectionId, modelVersionId],
    queryFn: () => api<Def>(`/api/v1/definitions/${encodeURIComponent(refId)}?connection_id=${connectionId}${modelVersionId ? `&model_version_id=${modelVersionId}` : ""}`),
  });
  const [sql, setSql] = useState<{ sql: string; scope: { entity: string; predicate: string }[] } | null>(null);
  const d = def.data;
  if (def.isLoading) return <div className="grid gap-2" aria-busy="true"><Skeleton className="h-5 w-2/3" /><Skeleton className="h-12" /></div>;
  if (def.error) return <FormAlert error={asApiError(def.error)} />;
  if (!d) return null;
  return (
    <div className="grid gap-3 text-body">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-heading">{d.label}</p>
        <ContentBadge badge="certified" />
      </div>
      <p>{d.rule_text}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-small">
        <Fact term="Fields used">{d.fields_used.join(", ")}</Fact>
        <Fact term="Blanks">{d.blank_handling}</Fact>
        {d.rule_ref && <Fact term="Rule">{d.rule_ref}</Fact>}
        <Fact term="Certified">{d.badge_text}</Fact>
        <Fact term="Your scope">{d.in_scope_text}</Fact>
        {filtersText && <Fact term="Filters">{filtersText}</Fact>}
      </dl>
      {d.can_show_query && spec && !sql && (
        <Button variant="link" size="sm" className="justify-self-start"
          onClick={() => void api<typeof sql>("/api/v1/query/explain", { method: "POST", body: { spec } }).then(setSql)}>
          Show query
        </Button>
      )}
      {sql && (
        <div className="grid gap-1">
          <pre className="max-h-48 overflow-auto rounded-sm bg-surface-sunken p-2 font-mono text-code">{sql.sql}</pre>
          <p className="text-small text-ink-muted">Scope part: {sql.scope.map((s) => `${s.entity}: ${s.predicate}`).join("; ")}</p>
        </div>
      )}
    </div>
  );
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-muted">{term}</dt>
      <dd>{children}</dd>
    </>
  );
}

/** The (i) button that opens the definition next to a figure; or wrap any trigger with `children`. */
export function DefinitionButton({ title, children, ...props }: DefinitionProps & { title: string; children?: ReactNode }) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        {children ?? (
          <Button variant="ghost" size="icon" className="size-control-sm" aria-label={`How ${title} is defined`} title="How is this defined?">
            <Info aria-hidden />
          </Button>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80" aria-label={`Definition of ${title}`}>
        <DefinitionBody {...props} />
      </PopoverContent>
    </Popover>
  );
}
