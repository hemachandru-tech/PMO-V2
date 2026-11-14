/** Display formatting for figures (RULE-037 rounding: percentages 2 dp, currency whole units). */
export function formatValue(value: unknown, format?: string | null): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return formatDate(value) ?? value;
  if (typeof value !== "number") return String(value);
  if (format === "currency") return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  if (format === "percent") return `${value.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}%`;
  if (Number.isInteger(value)) return value.toLocaleString();
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

/** A short figure for chart labels and ticks ("$2.45M", "12.5K"); the tooltip and the table keep the exact value. */
export function formatCompact(value: unknown, format?: string | null): string {
  if (typeof value !== "number") return formatValue(value, format);
  if (format === "percent" || Math.abs(value) < 10_000) return formatValue(value, format);
  const short = value.toLocaleString(undefined, { notation: "compact", maximumFractionDigits: 2 });
  return format === "currency" ? `${value < 0 ? "-" : ""}$${short.replace("-", "")}` : short;
}

export function formatAsOf(iso: string | null | undefined): string {
  if (!iso) return "not read yet";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/** A reporting date (YYYY-MM-DD, no time zone) in words: "30 Sep 2026" (US-158 "Data as of"). */
export function formatReportingDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  // Fixed words, the same as the PDF and Excel exports (the en-GB locale says "Sept" on some engines).
  return `${d} ${MONTHS[(m ?? 1) - 1] ?? ""} ${y}`;
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/;

/**
 * A date or date-time from the data in words (BUG-023): "2025-01-13T00:00:00" → "Jan 13 2025", with the time only
 * when it is not midnight ("Jan 13 2025, 14:30"). Read as written, never shifted to the browser's time zone, so a
 * date stays on its day. Anything that is not an ISO date gives null.
 */
export function formatDate(value: string): string | null {
  const m = ISO_DATE.exec(value);
  if (!m) return null;
  const [, y, mo, d, hh, mm] = m;
  const month = MONTHS[Number(mo) - 1];
  if (!month || Number(d) < 1 || Number(d) > 31) return null;
  const day = `${month} ${Number(d)} ${y}`;
  return hh !== undefined && (hh !== "00" || mm !== "00") ? `${day}, ${hh}:${mm}` : day;
}
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const RELATIVE: [Intl.RelativeTimeFormatUnit, number][] = [["year", 31_536_000], ["month", 2_592_000], ["week", 604_800], ["day", 86_400], ["hour", 3_600], ["minute", 60]];

/** "3 days ago", "yesterday", "just now": freshness on cards and lists (US-138). */
export function formatRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const seconds = (new Date(iso).getTime() - now.getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of RELATIVE) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return "just now";
}
