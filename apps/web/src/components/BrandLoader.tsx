import defaultLogo from "../design/tela-logo.png";
import { cn } from "../lib/utils";

/**
 * The brand loader: the logo, fixed, inside a ring that turns and cycles through the brand colours. Used wherever the
 * app is thinking or loading (Ask answers, page loads, planning). With reduced motion the ring stays still.
 * ``size`` is the ring's width in pixels; the logo is two thirds of it.
 */
export function BrandLoader({ size = 48, label, className }: { size?: number; label?: string; className?: string }) {
  const ring = Math.max(2, Math.round(size / 12));
  return (
    <span role={label ? "status" : undefined} aria-label={label} aria-hidden={label ? undefined : true}
      className={cn("relative inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size }}>
      <span className="brand-ring absolute inset-0 rounded-full" style={{ borderWidth: ring }} />
      <img src={defaultLogo} alt="" className="relative object-contain" style={{ width: size * 0.62, height: size * 0.62 }} />
    </span>
  );
}

/** The logo alone (no ring), for an assistant avatar or a title. */
export function BrandMark({ size = 28, className }: { size?: number; className?: string }) {
  return <img src={defaultLogo} alt="" aria-hidden className={cn("shrink-0 object-contain", className)} style={{ width: size, height: size }} />;
}

/** A full loading state: the loader with a line of text under it, centred in its space. */
export function BrandLoading({ text = "Loading…", size = 56, className }: { text?: string; size?: number; className?: string }) {
  return (
    <div className={cn("grid place-items-center gap-3 py-16 text-ink-muted", className)} aria-busy="true">
      <BrandLoader size={size} label={text} />
      <span className="text-small">{text}</span>
    </div>
  );
}
