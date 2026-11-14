import { AnimatePresence, LazyMotion, m, MotionConfig } from "motion/react";
import type { ReactNode } from "react";

/**
 * Motion presets (design system; US-147). Every movement is short and eases out, and none of it runs when the OS asks
 * for reduced motion: Motion honours it here (`reducedMotion="user"`), and `design/motion.css` stops the CSS
 * animations of dialogs, sheets, popovers, menus, tooltips and toasts. CSS durations are the `--tl-motion-*` variables.
 */
export const MOTION = {
  /** Hover, press, tooltips, chips. */
  fast: 0.12,
  /** Dialogs, popovers, menus, toasts, list changes. */
  base: 0.18,
  /** Side sheets, which travel further. */
  panel: 0.2,
  ease: [0.2, 0, 0, 1] as const,
};
export const DURATION = MOTION.base;

// Components use `m.*` inside LazyMotion, whose features load in their own chunk: `motion.*` would put the whole
// library in the entry (US-148 bundle budget). `strict` makes a stray `motion.*` fail loudly.
const loadFeatures = () => import("./motion-features").then((mod) => mod.default);

export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user" transition={{ duration: MOTION.base, ease: MOTION.ease }}>
        {children}
      </MotionConfig>
    </LazyMotion>
  );
}

/** List insert and remove: items fade and slide in from above, and collapse as they leave, closing the gap. */
export const listItem = {
  initial: { opacity: 0, y: -4 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, height: 0, transition: { duration: MOTION.fast } },
};

/** Animates items entering and leaving a list (notifications, filter chips). */
export function MotionList({
  items,
  className,
  itemClassName,
  as = "ul",
  label,
}: {
  items: { key: string; content: ReactNode }[];
  className?: string;
  itemClassName?: string;
  /** "div" for a row of chips, which is not a list to a screen reader. */
  as?: "ul" | "div";
  label?: string;
}) {
  const Item = as === "ul" ? m.li : m.div;
  const children = (
    <AnimatePresence initial={false}>
      {items.map((item) => (
        <Item key={item.key} className={itemClassName} {...listItem}>
          {item.content}
        </Item>
      ))}
    </AnimatePresence>
  );
  return as === "ul" ? (
    <ul className={className} aria-label={label}>{children}</ul>
  ) : (
    <div className={className} aria-label={label}>{children}</div>
  );
}
