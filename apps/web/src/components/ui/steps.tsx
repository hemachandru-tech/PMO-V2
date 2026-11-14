import { Check } from "lucide-react";

import { cn } from "@/lib/utils";

export interface StepItem<K extends string> {
  key: K;
  label: string;
}

/**
 * Step pills for full-page flows (publish, add connection, approve a model): numbered, done steps ticked, the current
 * one marked with `aria-current="step"`. Steps the person cannot reach yet are disabled.
 */
export function Steps<K extends string>({ steps, current, reachable, onSelect, label }: {
  steps: readonly StepItem<K>[];
  current: K;
  reachable: (index: number) => boolean;
  onSelect: (key: K) => void;
  /** Accessible name of the step list, for example "Publish steps". */
  label: string;
}) {
  const index = steps.findIndex((s) => s.key === current);
  return (
    <nav aria-label={label} data-slot="steps">
      <ol className="flex flex-wrap gap-2">
        {steps.map((s, i) => {
          const state = i < index ? "done" : i === index ? "current" : "todo";
          return (
            <li key={s.key}>
              <button type="button" disabled={!reachable(i)} onClick={() => onSelect(s.key)} aria-current={state === "current" ? "step" : undefined}
                className={cn("flex items-center gap-2 rounded-full border px-3 py-1 text-body focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60",
                  state === "current" ? "border-accent bg-accent-soft text-accent" : "border-line text-ink-muted hover:bg-surface-sunken")}>
                <span className={cn("flex size-5 items-center justify-center rounded-full text-small", state === "done" ? "bg-accent text-on-accent" : "bg-surface-sunken")}>
                  {state === "done" ? <Check aria-hidden className="size-3" /> : i + 1}
                </span>
                {s.label}
                {state === "done" && <span className="sr-only">(done)</span>}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
