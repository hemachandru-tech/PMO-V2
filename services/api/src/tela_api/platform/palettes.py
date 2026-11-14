"""Colour palettes built from a logo's colours (ENH-006, US-150).

The browser reads the logo's dominant colours (it can decode every format we accept) and sends them here as *seeds*;
this module turns seeds into 3 to 5 candidate palettes and checks each one against the US-132 rules:

* every text pair ≥ 4.5:1 and focus rings ≥ 3:1 on the surfaces they sit on, in light and dark;
* chart slots ≥ 3:1 on the chart surface, adjacent slots ΔE ≥ 15 with normal vision and with protanopia and
  deuteranopia (the order also keeps the first three slots apart where it can);
* no red or green as accent, series or diverging pole — they mean Off track and On track. Red and green logo colours
  are moved (red to orange, green to teal) and noted, so a logo with only red and green still gets safe palettes.

Lightness is adjusted until a palette passes; a candidate that still fails is dropped. A palette is fully determined by
its *spec* (accent, seeds, variant), so the server rebuilds and re-checks it from the spec when it is saved instead of
trusting colours sent by the browser.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from tela_api.platform.colour import contrast, cvd_delta_e, delta_e, from_oklch, hue_distance, oklch, parse_hex

Theme = Literal["light", "dark"]
THEMES: tuple[Theme, ...] = ("light", "dark")
Variant = Literal["natural", "deep", "vivid", "soft"]
VARIANT_NAMES: dict[str, str] = {"natural": "", "deep": " deep", "vivid": " vivid", "soft": " soft"}

# Fixed tokens the palette sits on (apps/web/src/design/tokens.json; test_palettes_unit checks they still match).
SURFACES: dict[Theme, dict[str, str]] = {
    "light": {"surface": "#f5f7f8", "surface-raised": "#ffffff", "surface-sunken": "#edf1f3", "ink": "#16202a",
              "div-mid": "#e3e8eb"},
    "dark": {"surface": "#0e1418", "surface-raised": "#161e23", "surface-sunken": "#0a0f12", "ink": "#e4eaee",
             "div-mid": "#2a353c"},
}
TEXT_MIN, GRAPHIC_MIN, DELTA_E_MIN = 4.5, 3.0, 15.0
GREY_CHROMA = 0.04  # below this a colour is a grey: it can't lead a palette
RAG_CHROMA = 0.06  # a colour this saturated in a red or green hue reads as a status colour
RED_HUES, GREEN_HUES = (8.0, 42.0), (118.0, 165.0)  # OKLCH hue ranges
RED_TO, GREEN_TO = 48.0, 175.0  # where red and green logo colours are moved: orange and teal
# The hues of the validated Tela chart palette (US-132): used to fill chart slots the logo has no colour for.
FILL_HUES = (228.0, 48.0, 345.0, 60.0, 320.0, 83.0, 290.0, 169.0)
EXTRA_HUES = (200.0, 255.0, 305.0, 100.0, 270.0, 0.0)  # more hues outside red and green, for logos that take Tela's
CHART_L: dict[Theme, float] = {"light": 0.62, "dark": 0.60}
MAX_SEEDS, MAX_CANDIDATES = 12, 5

_HUE_NAMES = ((8, "Rose"), (42, "Red"), (70, "Orange"), (100, "Gold"), (118, "Olive"), (165, "Green"), (205, "Teal"),
              (250, "Blue"), (285, "Indigo"), (318, "Violet"), (340, "Plum"), (360, "Rose"))


def hue_name(h: float) -> str:
    return next(name for top, name in _HUE_NAMES if h < top)


def rag_kind(hex_value: str) -> Literal["red", "green"] | None:
    _, c, h = oklch(hex_value)
    if c < RAG_CHROMA:
        return None
    if RED_HUES[0] <= h < RED_HUES[1]:
        return "red"
    return "green" if GREEN_HUES[0] <= h < GREEN_HUES[1] else None


def _in_rag(h: float, margin: float) -> bool:
    return any(lo - margin <= h < hi + margin for lo, hi in (RED_HUES, GREEN_HUES))


def safe_hue(hex_value: str) -> tuple[float, str | None]:
    """The hue to use for a colour: its own, or orange/teal for a red/green one (with the note saying so). Hues just
    outside the bands move too, so rounding to #rrggbb can't land a colour inside one, and so do muted ones, since
    the palette's chart colours and ramps are more saturated than the colour they come from."""
    _, _, h = oklch(hex_value)
    kind = None
    if _in_rag(h, 4):
        kind = "red" if RED_HUES[0] - 4 <= h < RED_HUES[1] + 4 else "green"
    if kind == "red":
        return RED_TO, f"The logo's red {hex_value} is used as orange: red means Off track."
    if kind == "green":
        return GREEN_TO, f"The logo's green {hex_value} is used as teal: green means On track."
    return h, None


# --------------------------------------------------------------------------- building one palette


def _fit(h: float, c: float, start: float, step: float, ok: Callable[[str], bool], stop: float) -> str:
    """Walk lightness from ``start`` by ``step`` until ``ok(colour)``; the last colour tried if none passes."""
    L = start
    colour = from_oklch(L, c, h)
    while not ok(colour) and (L - stop) * (1 if step < 0 else -1) > 0:
        L = max(stop, L + step) if step < 0 else min(stop, L + step)
        colour = from_oklch(L, c, h)
    return colour


def _accent_tokens(h: float, c: float, seed_l: float, variant: Variant, theme: Theme) -> dict[str, str]:
    s = SURFACES[theme]
    if theme == "light":
        soft = from_oklch(0.955, min(c, 0.035), h)

        def text_ok(col: str) -> bool:
            return (all(contrast(col, s[k]) >= TEXT_MIN for k in ("surface", "surface-raised"))
                    and contrast(col, soft) >= TEXT_MIN)

        start = 0.64 if variant == "vivid" else max(0.3, min(seed_l, 0.64))
        accent = _fit(h, c, start, -0.005, text_ok, 0.25)
        al = oklch(accent)[0]
        if variant == "deep":
            al = max(0.28, al - 0.1)
            accent = from_oklch(al, c, h)
        strong = _fit(h, c, al - 0.09, -0.005, lambda col: contrast(col, "#ffffff") >= TEXT_MIN, 0.2) \
            if al - 0.09 >= 0.24 else from_oklch(al + 0.07, c, h)
        return {"accent": accent, "accent-strong": strong, "accent-soft": soft, "on-accent": "#ffffff",
                "focus": accent}
    soft = from_oklch(0.27, min(c, 0.05), h)
    on = from_oklch(0.17, min(c, 0.03), h)
    start = 0.6 if variant == "deep" else max(seed_l, 0.66)

    def dark_ok(col: str) -> bool:
        return (all(contrast(col, s[k]) >= TEXT_MIN for k in ("surface", "surface-raised"))
                and contrast(col, soft) >= TEXT_MIN and contrast(col, on) >= TEXT_MIN)

    accent = _fit(h, c, min(0.9, start), 0.005, dark_ok, 0.92)
    al = oklch(accent)[0]
    return {"accent": accent, "accent-strong": from_oklch(min(0.93, al + 0.1), c, h), "accent-soft": soft,
            "on-accent": on, "focus": from_oklch(min(0.92, al + 0.06), c, h)}


def _chart_colour(h: float, theme: Theme) -> str:
    raised = SURFACES[theme]["surface-raised"]
    step, stop = (-0.01, 0.4) if theme == "light" else (0.01, 0.8)
    return _fit(h, 0.2, CHART_L[theme], step, lambda col: contrast(col, raised) >= GRAPHIC_MIN, stop)


def _apart(a: str, b: str) -> float:
    return min(delta_e(parse_hex(a), parse_hex(b)), cvd_delta_e(a, b))


def _chart_hues(first: float, seed_hues: list[float]) -> list[float]:
    """Eight hues led by the accent, then the logo's colours, then others outside red and green: a path where every
    neighbour pair is ΔE ≥ 15 with normal vision and for colour-blind readers (both themes), hues at least 12° apart,
    the logo's colours as early as they can be, then the first three and all slots as far apart as they can be."""
    pool: list[float] = [first]
    for h in seed_hues:  # up to three more logo hues; shades of one hue get one slot
        if len(pool) < 4 and all(hue_distance(h, x) >= 25 for x in pool):
            pool.append(h)
    logo = set(range(1, len(pool)))
    for h in [*FILL_HUES, *EXTRA_HUES, *range(0, 360, 12)]:
        if not _in_rag(float(h), 0) and all(hue_distance(h, x) >= 15 for x in pool):
            pool.append(float(h))
    colours = {t: [_chart_colour(h, t) for h in pool] for t in THEMES}
    n = len(pool)
    apart = [[min(_apart(colours[t][i], colours[t][j]) for t in THEMES) if i != j else 0.0 for j in range(n)]
             for i in range(n)]
    best: tuple[tuple[float, ...], list[int]] | None = None
    budget = [6000]

    def score(path: list[int]) -> tuple[float, ...]:
        first3 = min(apart[a][b] for a, b in itertools.combinations(path[:3], 2))
        return (-sum(i for i, k in enumerate(path) if k in logo) - 8 * len(logo - set(path)),
                min(first3, DELTA_E_MIN), min(apart[a][b] for a, b in itertools.combinations(path, 2)),
                min(apart[a][b] for a, b in itertools.pairwise(path)))

    def walk(path: list[int]) -> None:
        nonlocal best
        if budget[0] <= 0:
            return
        budget[0] -= 1
        if len(path) == 8:
            sc = score(path)
            if best is None or sc > best[0]:
                best = (sc, list(path))
            return
        nxt = [k for k in range(n) if k not in path and apart[path[-1]][k] >= DELTA_E_MIN]
        for k in sorted(nxt, key=lambda k: (k not in logo, -apart[path[-1]][k]))[:6]:
            walk([*path, k])

    walk([0])
    if best is None:  # no safe path: keep the widest-apart order so the checks show what failed
        order = [0]
        while len(order) < 8:
            order.append(max((k for k in range(n) if k not in order), key=lambda k: apart[order[-1]][k]))
        return [pool[i] for i in order]
    return [pool[i] for i in best[1]]


def _ramps(h: float, c: float, pole: tuple[float, float], theme: Theme) -> dict[str, str]:
    cc = min(c, 0.14)
    ph, dl = pole  # the other pole's hue, and how much lighter or darker its strong end is (to stay apart)
    if theme == "light":
        seq = [(0.93, 0.03), (0.83, 0.06), (0.69, 0.1), (0.54, cc), (0.39, cc * 0.8)]
        div = {"div-neg-2": (0.56 + dl, 0.15, ph), "div-neg-1": (0.81, 0.07, ph), "div-pos-1": (0.8, 0.06, h),
               "div-pos-2": (0.54, cc, h)}
    else:
        seq = [(0.29, 0.04), (0.41, 0.07), (0.55, 0.1), (0.7, cc), (0.86, cc * 0.6)]
        div = {"div-neg-2": (0.68 - dl, 0.15, ph), "div-neg-1": (0.42, 0.08, ph), "div-pos-1": (0.42, 0.07, h),
               "div-pos-2": (0.7, cc, h)}
    out = {f"seq-{i}": from_oklch(L, cs, h) for i, (L, cs) in enumerate(seq, 1)}
    out.update({k: from_oklch(L, cs, hh) for k, (L, cs, hh) in div.items()})
    out["div-mid"] = SURFACES[theme]["div-mid"]
    return out


def _pole(h: float, c: float, seed_hues: list[float]) -> tuple[float, float]:
    """The diverging ramp's other pole: the logo colour that sits furthest from the accent's end for colour-blind
    readers, else the hue outside red and green that does; its strong end a little lighter or darker if that is what
    it takes to stay ΔE ≥ 15 apart."""
    def gap(p: tuple[float, float]) -> float:
        return min(cvd_delta_e((r := _ramps(h, c, p, t))["div-neg-2"], r["div-pos-2"]) for t in THEMES)

    for dl in (0.0, 0.08, -0.08, 0.14):
        logo = [(x, dl) for x in seed_hues if hue_distance(x, h) >= 60]
        if logo and gap(best := max(logo, key=gap)) >= DELTA_E_MIN + 3:
            return best
        best = max(((float(x), dl) for x in range(0, 360, 10) if not _in_rag(float(x), 4)), key=gap)
        if gap(best) >= DELTA_E_MIN + 1:
            return best
    return best


@dataclass(frozen=True)
class Spec:
    accent: str
    seeds: tuple[str, ...] = ()
    variant: Variant = "natural"
    source: Literal["logo", "custom"] = "logo"

    def as_dict(self) -> dict[str, Any]:
        return {"accent": self.accent, "seeds": list(self.seeds), "variant": self.variant, "source": self.source}


def build(spec: Spec) -> dict[str, Any]:
    """A full palette (tokens for both themes, brand colours, checks) from its spec."""
    notes: list[str] = []
    h, note = safe_hue(spec.accent)
    if note:
        notes.append(note)
    seed_l, c, _ = oklch(spec.accent)
    c = {"soft": 0.04, "vivid": 0.32}.get(spec.variant, max(c, 0.05))
    seed_hues: list[float] = []
    for s in spec.seeds:
        if s.lower() == spec.accent.lower() or oklch(s)[1] < GREY_CHROMA:
            continue
        sh, sn = safe_hue(s)
        seed_hues.append(sh)
        if sn and sn not in notes:
            notes.append(sn)
    pole = _pole(h, c, seed_hues)
    hues = _chart_hues(h, seed_hues)
    tokens: dict[str, dict[str, str]] = {}
    for theme in THEMES:
        t = _accent_tokens(h, c, seed_l, spec.variant, theme)
        t.update({f"chart-{i}": _chart_colour(x, theme) for i, x in enumerate(hues, 1)})
        t.update(_ramps(h, c, pole, theme))
        tokens[theme] = t
    if (spec.source == "custom" or spec.variant == "natural") and tokens["light"]["accent"] != spec.accent.lower():
        notes.append(f"{spec.accent} is adjusted to {tokens['light']['accent']} in light theme and "
                     f"{tokens['dark']['accent']} in dark theme so text on it and in it can be read.")
    checks = validate(tokens)
    name = hue_name(h) + VARIANT_NAMES[spec.variant]
    digest = hashlib.sha256(json.dumps(spec.as_dict(), sort_keys=True).encode()).hexdigest()[:10]
    return {"id": f"p-{digest}", "name": name, "spec": spec.as_dict(), "tokens": tokens,
            "brand": list(dict.fromkeys((spec.accent, *spec.seeds)))[:6], "notes": notes, "checks": checks,
            "ok": all(ch["ok"] for ch in checks)}


# --------------------------------------------------------------------------- checks

_TEXT_PAIRS = (("accent", "surface"), ("accent", "surface-raised"), ("accent", "accent-soft"),
               ("accent-strong", "surface-raised"), ("on-accent", "accent"), ("on-accent", "accent-strong"),
               ("ink", "accent-soft"))
_FOCUS_PAIRS = (("focus", "surface"), ("focus", "surface-raised"))


def validate(tokens: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """Every US-132 rule this palette is subject to, per theme, with the measured value and the minimum."""
    checks: list[dict[str, Any]] = []

    def add(cid: str, label: str, theme: Theme, value: float, minimum: float, kind: str) -> None:
        checks.append({"id": f"{theme}:{cid}", "label": label, "theme": theme, "kind": kind,
                       "value": round(value, 2), "min": minimum, "ok": value >= minimum})

    for theme in THEMES:
        t = {**SURFACES[theme], **tokens[theme]}
        for fg, bg in _TEXT_PAIRS:
            add(f"{fg}/{bg}", f"{fg} text on {bg}", theme, contrast(t[fg], t[bg]), TEXT_MIN, "contrast")
        for fg, bg in _FOCUS_PAIRS:
            add(f"{fg}/{bg}", f"{fg} ring on {bg}", theme, contrast(t[fg], t[bg]), GRAPHIC_MIN, "contrast")
        series = [t[f"chart-{i}"] for i in range(1, 9)]
        add("chart/surface-raised", "chart colours on the chart surface", theme,
            min(contrast(s, t["surface-raised"]) for s in series), GRAPHIC_MIN, "contrast")
        add("chart/adjacent", "neighbouring chart colours apart", theme,
            min(delta_e(parse_hex(a), parse_hex(b)) for a, b in itertools.pairwise(series)), DELTA_E_MIN, "delta_e")
        add("chart/adjacent-cvd", "neighbouring chart colours apart for colour-blind readers", theme,
            min(cvd_delta_e(a, b) for a, b in itertools.pairwise(series)), DELTA_E_MIN, "delta_e")
        add("diverging/poles", "diverging ends apart for colour-blind readers", theme,
            cvd_delta_e(t["div-neg-2"], t["div-pos-2"]), DELTA_E_MIN, "delta_e")
        rag = [k for k in ("accent", "accent-strong", *(f"chart-{i}" for i in range(1, 9)), "div-neg-2", "div-pos-2")
               if rag_kind(t[k])]
        checks.append({"id": f"{theme}:no-rag", "label": "no red or green accent or series", "theme": theme,
                       "kind": "rag", "value": len(rag), "min": 0, "ok": not rag, "tokens": rag})
    return checks


# --------------------------------------------------------------------------- candidates


def candidates(seeds: list[str]) -> dict[str, Any]:
    """Up to five passing palettes for a logo's colours (most prominent first)."""
    seeds = list(dict.fromkeys(s.lower() for s in seeds))[:MAX_SEEDS]
    chromatic = [s for s in seeds if oklch(s)[1] >= GREY_CHROMA]
    leads: list[str] = []
    for s in chromatic:  # one lead per distinct hue (after moving red and green)
        if all(hue_distance(safe_hue(s)[0], safe_hue(x)[0]) >= 25 for x in leads):
            leads.append(s)
    specs = [Spec(accent=s, seeds=tuple(seeds), variant="natural") for s in leads[:4]]
    for s in leads[:2]:  # lighter, darker and calmer versions, so a one- or two-colour logo still gets a choice
        specs += [Spec(accent=s, seeds=tuple(seeds), variant=v) for v in ("deep", "soft", "vivid")]
    built: list[dict[str, Any]] = []
    dropped = 0
    for spec in specs:
        p = build(spec)
        if not p["ok"]:
            dropped += 1
            continue
        a = parse_hex(p["tokens"]["light"]["accent"])
        if any(delta_e(a, parse_hex(q["tokens"]["light"]["accent"])) < 5 for q in built):
            continue  # looks the same as one we already have
        built.append(p)
        if len(built) == MAX_CANDIDATES:
            break
    return {"candidates": built, "dropped": dropped, "greys_only": bool(seeds) and not chromatic}
