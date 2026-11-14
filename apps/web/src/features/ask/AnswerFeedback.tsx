import { MessageSquareWarning, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { toast } from "sonner";

import { api, ApiError } from "../../api/client";
import type { FeedbackReason, MyFeedback } from "../../api/knowledge";
import { Button } from "../../components/ui/button";
import { Label } from "../../components/ui/label";
import { RadioGroup, RadioGroupItem } from "../../components/ui/radio-group";
import { Textarea } from "../../components/ui/textarea";
import { ActionDivider, IconAction } from "./ChatParts";

const REASONS: { value: FeedbackReason; label: string }[] = [
  { value: "wrong", label: "The answer looks wrong" },
  { value: "not_what_i_asked", label: "This isn't what I asked" },
];

/**
 * Thumbs up/down and "This isn't what I asked" on an answer (US-183, FEAT-018). A down-vote sends the question and the
 * plan — never the rows — to the Admin queue (Admin → Knowledge → Flagged answers), where fixing it makes a gold
 * example. Clicking the same thumb again takes the vote back while no Admin has looked at it. US-261: icon buttons in
 * the answer's action row; `before` and `children` are the other actions on either side of the rating.
 */
export function AnswerFeedback({ turnId, before, children }: { turnId: string; before?: ReactNode; children?: ReactNode }) {
  const url = `/api/v1/ask/turns/${turnId}/feedback`;
  const ids = { form: useId(), comment: useId(), reason: useId() };
  const [mine, setMine] = useState<MyFeedback | null>(null);
  const [asking, setAsking] = useState<FeedbackReason | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    api<{ feedback: MyFeedback | null }>(url).then((out) => { if (live) setMine(out.feedback); }).catch(() => undefined);
    return () => { live = false; };
  }, [url]);

  const resolved = mine?.status === "fixed" || mine?.status === "dismissed";
  const send = async (body: { vote: "up" | "down"; reason?: FeedbackReason; comment?: string } | null) => {
    setBusy(true);
    try {
      if (body === null) {
        await api(url, { method: "DELETE" });
        setMine(null);
      } else {
        const out = await api<{ feedback: MyFeedback }>(url, { method: "PUT", body });
        setMine(out.feedback);
        if (body.vote === "down") toast.success("Thanks — an Admin will look at this answer.");
      }
      setAsking(null);
      setComment("");
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Your feedback could not be saved.");
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (asking) void send({ vote: "down", reason: asking, ...(comment.trim() ? { comment: comment.trim() } : {}) });
  };

  return (
    <div className="grid gap-2">
      <div role="toolbar" aria-label="Answer actions" className="-ml-1.5 flex flex-wrap items-center gap-0.5">
        {before}
        <div role="group" aria-label="Rate this answer" className="flex flex-wrap items-center gap-0.5">
          <IconAction label="Helpful" icon={<ThumbsUp aria-hidden />} aria-pressed={mine?.vote === "up"} disabled={busy || resolved}
            onClick={() => void send(mine?.vote === "up" ? null : { vote: "up" })} />
          <IconAction label="Not helpful" icon={<ThumbsDown aria-hidden />} aria-pressed={mine?.vote === "down"} aria-expanded={asking !== null}
            aria-controls={ids.form} disabled={busy || resolved}
            onClick={() => (mine?.vote === "down" ? void send(null) : setAsking(asking ? null : "wrong"))} />
          <IconAction label="This isn't what I asked" icon={<MessageSquareWarning aria-hidden />} disabled={busy || resolved}
            onClick={() => setAsking("not_what_i_asked")} />
          {mine?.vote === "down" && !resolved && <span role="status" className="px-1 text-small text-ink-muted">Sent for review</span>}
          {mine?.status === "fixed" && <span role="status" className="px-1 text-small text-ink-muted">An Admin corrected this answer; Ask learns from the correction.</span>}
          {mine?.status === "dismissed" && <span role="status" className="px-1 text-small text-ink-muted">An Admin reviewed this answer.</span>}
        </div>
        {children && <><ActionDivider />{children}</>}
      </div>
      {asking && (
        <form id={ids.form} aria-label="What was wrong" className="grid max-w-xl gap-2 rounded-[16px] border border-line bg-surface-raised p-4" onSubmit={submit}>
          <RadioGroup aria-labelledby={ids.reason} value={asking} onValueChange={(v) => setAsking(v as FeedbackReason)}>
            <p id={ids.reason} className="text-small font-medium">What was wrong?</p>
            {REASONS.map((r) => (
              <div key={r.value} className="flex items-center gap-2">
                <RadioGroupItem id={`${ids.reason}-${r.value}`} value={r.value} />
                <Label htmlFor={`${ids.reason}-${r.value}`} className="font-normal">{r.label}</Label>
              </div>
            ))}
          </RadioGroup>
          <div className="grid gap-1">
            <Label htmlFor={ids.comment}>What did you expect? <span className="text-ink-muted">(optional)</span></Label>
            <Textarea id={ids.comment} value={comment} maxLength={500} onChange={(e) => setComment(e.target.value)} />
          </div>
          <p className="text-small text-ink-muted">Your question and how it was read go to an Admin; the numbers in the answer don't.</p>
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={busy}>Send</Button>
            <Button type="button" variant="secondary" size="sm" onClick={() => setAsking(null)}>Cancel</Button>
          </div>
        </form>
      )}
    </div>
  );
}
