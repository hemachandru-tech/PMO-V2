"""Colour maths for brand palettes (US-150): sRGB, OKLCH, OKLab ΔE, WCAG contrast and CVD simulation.

Pure functions, no dependencies. Colours are ``#rrggbb`` strings at the edges and ``(r, g, b)`` floats in 0-1 inside.
CVD simulation uses the Machado, Oliveira & Fernandes (2009) matrices at full severity on linear RGB — the same
model as the dataviz checks the US-132 palette was validated with (protanopia and deuteranopia; that check gives the
recorded adjacent ΔE of 16.6 light / 16.2 dark for the Tela palette).
"""

from __future__ import annotations

import math
import re

RGB = tuple[float, float, float]
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex(value: str) -> RGB:
    m = _HEX.match(value.strip())
    if not m:
        raise ValueError(f"Not a #rrggbb colour: {value!r}")
    h = m.group(1)
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


def to_hex(rgb: RGB) -> str:
    return "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02x}" for c in rgb)


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _gam(c: float) -> float:
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def linear(rgb: RGB) -> RGB:
    return (_lin(rgb[0]), _lin(rgb[1]), _lin(rgb[2]))


def from_linear(rgb: RGB) -> RGB:
    return (_gam(rgb[0]), _gam(rgb[1]), _gam(rgb[2]))


# --------------------------------------------------------------------------- contrast (WCAG 2.x)


def luminance(hex_or_rgb: str | RGB) -> float:
    r, g, b = linear(parse_hex(hex_or_rgb) if isinstance(hex_or_rgb, str) else hex_or_rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# --------------------------------------------------------------------------- OKLab / OKLCH


def oklab(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = linear(rgb)
    lc = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    mc = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    sc = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * lc + 0.7936177850 * mc - 0.0040720468 * sc,
            1.9779984951 * lc - 2.4285922050 * mc + 0.4505937099 * sc,
            0.0259040371 * lc + 0.7827717662 * mc - 0.8086757660 * sc)


def _oklab_to_linear(lab: tuple[float, float, float]) -> RGB:
    L, a, b = lab
    lc = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    mc = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    sc = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
            -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
            -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc)


def oklch(hex_value: str) -> tuple[float, float, float]:
    """``(L 0-1, C, h degrees)``."""
    L, a, b = oklab(parse_hex(hex_value))
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360)


def from_oklch(L: float, C: float, h: float) -> str:
    """The sRGB colour for an OKLCH value, reducing chroma (keeping L and h) until it fits the gamut."""
    lo, hi = 0.0, max(0.0, C)
    rad = math.radians(h)

    def fits(c: float) -> RGB | None:
        rgb = _oklab_to_linear((L, c * math.cos(rad), c * math.sin(rad)))
        return rgb if all(-1e-6 <= v <= 1 + 1e-6 for v in rgb) else None

    if (rgb := fits(hi)) is not None:
        return to_hex(from_linear(rgb))
    for _ in range(24):
        mid = (lo + hi) / 2
        if fits(mid) is not None:
            lo = mid
        else:
            hi = mid
    return to_hex(from_linear(fits(lo) or (L, L, L)))


def hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def delta_e(c1: RGB, c2: RGB) -> float:
    """ΔE as the US-132 palette was checked: Euclidean distance in OKLab, times 100."""
    return 100 * math.dist(oklab(c1), oklab(c2))


# --------------------------------------------------------------------------- colour-vision deficiency

_MACHADO = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
}
CVD = tuple(_MACHADO)


def simulate(rgb: RGB, kind: str) -> RGB:
    m = _MACHADO[kind]
    lin = linear(rgb)
    out = tuple(min(1.0, max(0.0, sum(m[i][j] * lin[j] for j in range(3)))) for i in range(3))
    return from_linear(out)  # type: ignore[arg-type]


def cvd_delta_e(a: str, b: str) -> float:
    """The smallest ΔE between two colours as seen with protanopia or deuteranopia."""
    ra, rb = parse_hex(a), parse_hex(b)
    return min(delta_e(simulate(ra, k), simulate(rb, k)) for k in CVD)
