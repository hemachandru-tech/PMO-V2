import { ArrowUp, BarChart3, Check, Copy, Mic, MicOff, Table2 } from "lucide-react";
import { BrandLoader, BrandMark } from "../../components/BrandLoader";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState, type ComponentPropsWithoutRef, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";

import { Button } from "../../components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import { cn } from "../../lib/utils";

/**
 * The chat building blocks of Ask (US-261, ENH-029): a question bubble, an assistant message with its avatar, the
 * "thinking" line while a turn streams, icon actions with tooltips, follow-up chips and the composer. Layout only;
 * every figure still comes from the API.
 */

export function UserMessage({ children }: { children: ReactNode }) {
  return (
    <p data-ask-question className="max-w-[85%] justify-self-end whitespace-pre-wrap break-words rounded-[16px] rounded-br-[6px] bg-surface-sunken px-4 py-2.5 text-ink">
      {children}
    </p>
  );
}

/** The assistant's side of a turn: a small avatar and the content beside it, with no box around plain text. */
export function AssistantMessage({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div data-ask-reply className={cn("grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-start gap-3", className)}>
      <AssistantAvatar />
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3">{children}</div>
    </div>
  );
}

export function AssistantAvatar({ busy = false }: { busy?: boolean }) {
  // The brand logo; while the answer is being worked out it sits inside the turning brand ring.
  return busy
    ? <span data-ask-avatar aria-hidden className="mt-0.5 inline-flex"><BrandLoader size={32} /></span>
    : (
      <span data-ask-avatar aria-hidden
        className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-surface shadow-sm ring-1 ring-line">
        <BrandMark size={20} />
      </span>
    );
}

const STAGES: [string, string][] = [
  ["intent", "Understanding your question"], ["retrieval", "Finding the data"], ["plan", "Planning the answer"],
  ["validate", "Checking the plan"], ["execute", "Running the query"], ["viz", "Drawing the answer"],
];

/** While a turn streams: one line naming the current step, and the steps as dots (their names for screen readers). */
export function Thinking({ stages }: { stages: string[] }) {
  const current = STAGES.findIndex(([key]) => !stages.includes(key));
  const label = current === -1 ? "Writing the answer" : STAGES[current]![1];
  return (
    <div data-ask-reply className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-3">
      <AssistantAvatar busy />
      <div className="flex min-w-0 flex-wrap items-center gap-3">
        <span aria-live="polite" className="ask-shimmer text-body text-ink-muted">{label}…</span>
        <ol aria-label="Progress" className="flex items-center gap-1">
          {STAGES.map(([key, name], i) => {
            const done = stages.includes(key);
            return (
              <li key={key} title={name}
                className={cn("size-1.5 rounded-full transition-colors", done ? "bg-accent" : i === current ? "bg-accent/50" : "bg-line-strong")}>
                <span className="sr-only">{name}{done ? " (done)" : ""}</span>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

/** An icon-only action with its name in a tooltip (and as its accessible name). */
export const IconAction = forwardRef<HTMLButtonElement, ComponentPropsWithoutRef<typeof Button> & { label: string; icon: ReactNode }>(
  function IconAction({ label, icon, className, ...props }, ref) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button ref={ref} variant="ghost" size="icon" aria-label={label}
            className={cn("size-8 rounded-lg text-ink-muted hover:text-ink aria-pressed:text-accent", className)} {...props}>
            {icon}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{label}</TooltipContent>
      </Tooltip>
    );
  },
);

/** Copies an answer's words (headline and sentences) to the clipboard. */
export function CopyAction({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch {
      toast.error("The answer could not be copied.");
    }
  };
  return <IconAction label={done ? "Copied" : "Copy answer"} icon={done ? <Check aria-hidden /> : <Copy aria-hidden />} onClick={() => void copy()} />;
}

/** A row of icon actions under an answer. */
export function ActionBar({ children }: { children: ReactNode }) {
  return <div role="toolbar" aria-label="Answer actions" className="-ml-1.5 flex flex-wrap items-center gap-0.5">{children}</div>;
}

export function ActionDivider() {
  return <span aria-hidden className="mx-1 h-4 w-px bg-line" />;
}

/** A follow-up or refinement offered as a pill. */
export function FollowChip({ children, className, ...props }: ComponentPropsWithoutRef<"button">) {
  return (
    <button type="button" data-ask-chip
      className={cn("inline-flex h-control-sm items-center gap-1.5 rounded-full border border-line-strong bg-surface-raised px-3 text-small text-ink transition-colors hover:border-accent hover:bg-accent-soft focus-visible:focus-ring [&_svg]:size-3.5",
        className)} {...props}>
      {children}
    </button>
  );
}

export interface ComposerHandle { focus: () => void }

/**
 * The message box: grows with the text, Enter sends and Shift+Enter starts a new line; the send button is round.
 * `children` sit in the box's lower row, left of the send button (context pills, settings).
 */
export const Composer = forwardRef<ComposerHandle, {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  placeholder: string;
  submitLabel?: string;
  children?: ReactNode;
  footer?: ReactNode;
  className?: string;
  maxLength?: number;
}>(function Composer({ id, label, value, onChange, onSubmit, placeholder, submitLabel = "Ask", children, footer, className, maxLength = 2000 }, ref) {
  const box = useRef<HTMLTextAreaElement>(null);
  const [listening, setListening] = useState(false);
  const recognitionRef = useRef<any>(null);

  useImperativeHandle(ref, () => ({ focus: () => box.current?.focus() }), []);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);

  const send = (e?: FormEvent) => {
    e?.preventDefault();
    if (value.trim()) onSubmit(value.trim());
  };

  const toggleVoice = () => {
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      toast.error("Voice input is not supported in this browser. Please use Chrome, Edge, or Safari.");
      return;
    }

    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }

    try {
      const recognition = new SpeechRecognition();
      recognitionRef.current = recognition;
      recognition.continuous = false;
      recognition.interimResults = true;
      recognition.lang = "en-US";

      recognition.onstart = () => {
        setListening(true);
        toast.info("Listening... speak your question now.");
      };

      recognition.onresult = (event: any) => {
        let interimTranscript = "";
        let finalTranscript = "";
        for (let i = event.resultIndex; i < event.results.length; ++i) {
          if (event.results[i].isFinal) {
            finalTranscript += event.results[i][0].transcript;
          } else {
            interimTranscript += event.results[i][0].transcript;
          }
        }
        const text = (finalTranscript || interimTranscript).trim();
        if (text) {
          onChange(text);
        }
      };

      recognition.onerror = (event: any) => {
        console.error("Speech recognition error:", event.error);
        setListening(false);
        if (event.error !== "no-speech") {
          toast.error(`Voice error: ${event.error}`);
        }
      };

      recognition.onend = () => {
        setListening(false);
        if (box.current?.value?.trim()) {
          const query = box.current.value.trim();
          toast.success(`Voice captured: "${query}"`);
          onSubmit(query);
        }
      };

      recognition.start();
    } catch (err: any) {
      console.error("SpeechRecognition start failed", err);
      setListening(false);
      toast.error("Could not start voice recognition.");
    }
  };

  return (
    <form onSubmit={send} data-ask-form className={cn("grid gap-1.5", className)}>
      <div data-ask-composer
        className="grid gap-1 rounded-[24px] border border-line-strong bg-surface-raised px-4 pb-2 pt-3 shadow-sm transition-shadow focus-within:border-accent focus-within:shadow-md">
        <label htmlFor={id} className="sr-only">{label}</label>
        <textarea ref={box} id={id} rows={1} value={value} maxLength={maxLength} placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) send(e); }}
          className="max-h-[200px] min-h-6 w-full resize-none bg-transparent text-body text-ink outline-none placeholder:text-ink-muted" />
        <div className="flex min-w-0 items-center justify-between gap-2">
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">{children}</div>
          <div className="flex items-center gap-1.5 shrink-0">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  size="icon"
                  variant={listening ? "danger" : "ghost"}
                  onClick={toggleVoice}
                  aria-label={listening ? "Stop listening" : "Voice assistant (Speak to search)"}
                  className={cn(
                    "size-8 rounded-full transition-all",
                    listening
                      ? "animate-pulse bg-red-600 text-white hover:bg-red-700"
                      : "text-ink-muted hover:bg-surface-sunken hover:text-ink"
                  )}
                >
                  {listening ? <MicOff className="size-4" /> : <Mic className="size-4" />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{listening ? "Listening... click to stop" : "Voice assistant (Speak to search)"}</TooltipContent>
            </Tooltip>
            <Button type="submit" size="icon" aria-label={submitLabel} disabled={!value.trim()}
              className="size-8 shrink-0 rounded-full">
              <ArrowUp aria-hidden />
            </Button>
          </div>
        </div>
      </div>
      {footer && <p className="px-4 text-center text-small text-ink-muted">{footer}</p>}
    </form>
  );
});

/** A quiet line of facts about an answer: its badge, then "Data as of …", the source and how it was read. */
export function AnswerMeta({ badge, items }: { badge?: ReactNode; items: { text: string; title?: string }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-small text-ink-muted">
      {badge}
      {items.map((it, i) => (
        <span key={i} className="inline-flex items-center gap-2" title={it.title}>
          {(i > 0 || badge) && <span aria-hidden>·</span>}
          {it.text}
        </span>
      ))}
    </div>
  );
}

/** Chart or table, as a two-button switch in the chart frame's corner. */
export function ViewSwitch({ view, onChange }: { view: "chart" | "table"; onChange: (v: "chart" | "table") => void }) {
  return (
    <div className="inline-flex rounded-lg bg-surface-sunken p-0.5" role="group" aria-label="Chart or table">
      {([["chart", "Show as chart", BarChart3], ["table", "Show as table", Table2]] as const).map(([v, label, Icon]) => (
        <button key={v} type="button" aria-label={label} aria-pressed={view === v} title={label} onClick={() => onChange(v)}
          className={cn("inline-flex size-7 items-center justify-center rounded-md text-ink-muted transition-colors focus-visible:focus-ring",
            view === v ? "bg-surface-raised text-ink shadow-sm" : "hover:text-ink")}>
          <Icon aria-hidden className="size-4" />
        </button>
      ))}
    </div>
  );
}

