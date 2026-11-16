import { useEffect, useMemo, useState, type CSSProperties } from "react";

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

const BURST_COLOURS = ["#0057a8", "#78c845", "#ffb91c", "#f68121", "#002d62", "#ffd76a"];
const STREAKS = 16;
export const BURST_MS = 1200;

/** True when the person asked the system for less motion (then nothing bursts). */
export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/**
 * The firecracker that plays once when loading finishes: the ring pops outward, a gold flash, streaks in the brand
 * colours flying out and gold sparks twinkling where they end. Purely decorative and laid over its parent (which must be
 * ``relative``); ``size`` is the ring's width in pixels.
 */
export function BrandBurst({ size = 32 }: { size?: number }) {
  const parts = useMemo(() => Array.from({ length: STREAKS }, (_, i) => {
    const long = i % 2 === 0;
    return {
      angle: i * (360 / STREAKS) + (long ? 0 : 7),
      reach: size * (long ? 1.2 : 0.9),
      colour: BURST_COLOURS[i % BURST_COLOURS.length]!,
      width: long ? Math.max(10, size / 3) : Math.max(4, size / 8),
      height: long ? Math.max(2.5, size / 12) : Math.max(4, size / 8),
      delay: (i % 3) * 35,
    };
  }), [size]);
  return (
    <span aria-hidden data-brand-burst className="brand-burst pointer-events-none absolute inset-0">
      <span className="brand-burst-flash absolute inset-0 rounded-full" />
      <span className="brand-burst-ring absolute inset-0 rounded-full" style={{ borderWidth: Math.max(2, Math.round(size / 12)) }} />
      {parts.map((p, i) => (
        <span key={`s${i}`} className="brand-spark"
          style={{ "--a": `${p.angle}deg`, "--d": `${p.reach}px`, width: p.width, height: p.height, marginTop: -p.height / 2,
            background: p.colour, color: p.colour, animationDelay: `${p.delay}ms` } as CSSProperties} />
      ))}
      {parts.filter((_, i) => i % 2 === 0).map((p, i) => (
        <span key={`t${i}`} className="brand-twinkle"
          style={{ "--a": `${p.angle + 11}deg`, "--d": `${p.reach * 1.1}px`, width: 4, height: 4, margin: -2,
            animationDelay: `${380 + (i % 4) * 45}ms` } as CSSProperties} />
      ))}
    </span>
  );
}

/** Whether a burst asked for on mount is still playing; it ends by itself after ``BURST_MS``. */
export function useBurst(wanted: boolean): boolean {
  const [on, setOn] = useState(() => wanted && !prefersReducedMotion());
  useEffect(() => {
    if (!on) return;
    const t = window.setTimeout(() => setOn(false), BURST_MS);
    return () => window.clearTimeout(t);
  }, [on]);
  return on;
}
