import { CircleAlert, Info } from "lucide-react";
import type { CSSProperties } from "react";

import type { CalloutLevel, TextKind } from "../api/app";
import { cn } from "../lib/utils";
import { TextNote } from "./TextNote";
import { Card } from "./ui/card";

export interface TextBlockProps {
  kind: TextKind;
  text: string;
  title?: string | null;
  level?: CalloutLevel;
  className?: string;
  style?: CSSProperties;
}

/**
 * A report's text blocks (US-158): a text note (a card), a callout (title and text, Note or Warning shown as icon and
 * word, never colour alone) and a footnote (small muted text saying what the report counts and its data limits).
 * Text is markdown-lite through TextNote, never HTML.
 */
export function TextBlock({ kind, text, title, level = "info", className, style }: TextBlockProps) {
  if (kind === "footnote") {
    return (
      <div role="note" aria-label="Footnote" className={cn("text-small text-ink-muted", className)} style={style}>
        <TextNote text={text} />
      </div>
    );
  }
  if (kind === "callout") {
    const warning = level === "warning";
    const Icon = warning ? CircleAlert : Info;
    const word = warning ? "Warning" : "Note";
    return (
      <aside aria-label={title ? `${word}: ${title}` : word} data-level={level} style={style}
        className={cn("grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2 gap-y-1 rounded-md border px-4 py-3 text-body text-ink",
          warning ? "border-status-warn/40 bg-status-warn-soft" : "border-line bg-surface-sunken", className)}>
        <Icon aria-hidden className={cn("mt-0.5 size-4", warning ? "text-status-warn" : "text-ink-muted")} />
        <div className="grid min-w-0 gap-1">
          <p className="text-body-strong">
            <span className={cn("mr-1.5 text-label uppercase", warning ? "text-ink" : "text-ink-muted")}>{word}</span>
            {title}
          </p>
          <TextNote text={text} />
        </div>
      </aside>
    );
  }
  return <Card className={cn("p-4 text-body", className)} style={style}><TextNote text={text} /></Card>;
}
