"""Palettes from a logo's colours (ENH-006, US-150): colour maths, the US-132 checks and candidate generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tela_api.platform.colour import contrast, cvd_delta_e, delta_e, from_oklch, oklch, parse_hex, simulate, to_hex
from tela_api.platform.palettes import SURFACES, THEMES, Spec, build, candidates, rag_kind, safe_hue, validate

TOKENS = json.loads((Path(__file__).parents[3] / "apps/web/src/design/tokens.json").read_text())
TELA = {t["name"]: t["value"] for t in TOKENS["color"]["tokens"]}
TELA_LOGO = ["#287a9a", "#215174", "#31a7c2", "#297739", "#e46e22", "#f7bd3c"]
ACCENT_KEYS = ("accent", "accent-strong", "accent-soft", "on-accent", "focus")
CHART = [f"chart-{i}" for i in range(1, 9)]


def _tela_tokens() -> dict[str, dict[str, str]]:
    keys = (*ACCENT_KEYS, *CHART, "div-neg-2", "div-pos-2")
    return {t: {k: TELA[k][t] for k in keys} for t in THEMES}


def _failed(p: dict) -> list[tuple[str, float]]:
    return [(c["id"], c["value"]) for c in p["checks"] if not c["ok"]]


# --------------------------------------------------------------------------- colour maths


def test_contrast_matches_wcag_reference_values() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0)
    assert contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)  # the classic 4.5:1 grey


def test_oklch_round_trips_and_clamps_to_the_gamut() -> None:
    for hex_value in ("#236f8d", "#e46e22", "#a11d6c", "#0e1418"):
        assert from_oklch(*oklch(hex_value)) == hex_value
    out = from_oklch(0.62, 0.5, 228)  # far outside sRGB: chroma is reduced, lightness and hue kept
    L, _, h = oklch(out)  # noqa: N806
    assert L == pytest.approx(0.62, abs=0.01) and h == pytest.approx(228, abs=2)
    assert to_hex(parse_hex("#ABCDEF")) == "#abcdef"
    with pytest.raises(ValueError, match="rrggbb"):
        parse_hex("red")


def test_delta_e_and_cvd_reproduce_the_recorded_us132_figures() -> None:
    """The checks are the ones the Tela palette was validated with: EXEC-010 recorded adjacent CVD ΔE 16.6 (light)
    and 16.2 (dark), normal vision 19.3 and 19.1."""
    for theme, cvd, normal in (("light", 16.6, 19.3), ("dark", 16.2, 19.1)):
        s = [TELA[k][theme] for k in CHART]
        assert min(cvd_delta_e(a, b) for a, b in zip(s, s[1:], strict=False)) == pytest.approx(cvd, abs=0.05)
        assert min(delta_e(parse_hex(a), parse_hex(b)) for a, b in zip(s, s[1:], strict=False)) == \
            pytest.approx(normal, abs=0.05)


def test_red_and_green_look_alike_to_colour_blind_readers() -> None:
    red, green = parse_hex("#d62828"), parse_hex("#2a9d48")
    assert delta_e(red, green) > 30
    assert delta_e(simulate(red, "deutan"), simulate(green, "deutan")) < delta_e(red, green) / 2


# --------------------------------------------------------------------------- the checks


def test_surfaces_match_the_design_tokens() -> None:
    for theme in THEMES:
        for name, value in SURFACES[theme].items():
            assert TELA[name][theme] == value, name


def test_the_tela_palette_passes_every_check() -> None:
    checks = validate(_tela_tokens())
    assert {c["theme"] for c in checks} == {"light", "dark"}
    assert not [c for c in checks if not c["ok"]]


def test_validator_catches_each_rule() -> None:
    tokens = _tela_tokens()
    tokens["light"]["accent"] = "#7fb8cc"  # too light for text
    tokens["dark"]["focus"] = "#1f2a30"  # a focus ring that disappears
    tokens["dark"]["chart-3"] = tokens["dark"]["chart-2"]  # two neighbours alike
    tokens["light"]["chart-5"] = "#d62828"  # red as a series
    failed = {c["id"] for c in validate(tokens) if not c["ok"]}
    assert {"light:accent/surface-raised", "light:on-accent/accent", "dark:focus/surface-raised",
            "dark:chart/adjacent", "dark:chart/adjacent-cvd", "light:no-rag"} <= failed
    rag = next(c for c in validate(tokens) if c["id"] == "light:no-rag")
    assert rag["tokens"] == ["chart-5"]


def test_red_and_green_are_recognised_and_moved() -> None:
    assert rag_kind("#d62828") == "red" and rag_kind("#00a651") == "green"
    assert rag_kind("#e46e22") is None and rag_kind("#a11d6c") is None and rag_kind("#777777") is None
    h, note = safe_hue("#d62828")
    assert rag_kind(from_oklch(0.6, 0.15, h)) is None and note and "Off track" in note
    h, note = safe_hue("#2a9d48")
    assert rag_kind(from_oklch(0.6, 0.15, h)) is None and note and "On track" in note
    assert safe_hue("#236f8d")[1] is None


# --------------------------------------------------------------------------- building palettes


def test_a_built_palette_has_every_token_for_both_themes() -> None:
    p = build(Spec(accent="#287a9a", seeds=tuple(TELA_LOGO)))
    keys = {*ACCENT_KEYS, *CHART, *(f"seq-{i}" for i in range(1, 6)),
            "div-neg-2", "div-neg-1", "div-mid", "div-pos-1", "div-pos-2"}
    for theme in THEMES:
        assert set(p["tokens"][theme]) == keys
    assert p["ok"], _failed(p)
    assert p["name"] == "Blue" and p["brand"][0] == "#287a9a"
    seq = [oklch(p["tokens"]["light"][f"seq-{i}"])[0] for i in range(1, 6)]
    assert seq == sorted(seq, reverse=True)  # light ramp darkens with magnitude
    seq = [oklch(p["tokens"]["dark"][f"seq-{i}"])[0] for i in range(1, 6)]
    assert seq == sorted(seq)  # dark ramp lightens with magnitude
    assert build(Spec(accent="#287a9a", seeds=tuple(TELA_LOGO))) == p  # the spec is enough to rebuild it


def test_lightness_is_adjusted_until_text_passes() -> None:
    p = build(Spec(accent="#ffcc00", source="custom"))  # yellow: unreadable as text on white
    assert p["ok"], _failed(p)
    assert contrast(p["tokens"]["light"]["accent"], "#ffffff") >= 4.5
    assert any("adjusted" in n for n in p["notes"])


@pytest.mark.parametrize("hue", range(0, 360, 15))
@pytest.mark.parametrize(("lightness", "chroma"), [(0.3, 0.15), (0.55, 0.25), (0.85, 0.06), (0.6, 0.02)])
def test_any_accent_gives_a_passing_palette(hue: int, lightness: float, chroma: float) -> None:
    accent = from_oklch(lightness, chroma, hue)
    p = build(Spec(accent=accent, seeds=(accent,)))
    assert p["ok"], (accent, _failed(p))


def test_the_tela_logo_gives_several_safe_palettes_led_by_its_colours() -> None:
    out = candidates(TELA_LOGO)
    names = [p["name"] for p in out["candidates"]]
    assert 3 <= len(names) <= 5 and names[0] == "Blue"
    assert all(p["ok"] for p in out["candidates"])
    assert any("green #297739" in n for n in out["candidates"][0]["notes"])  # the logo's green became teal
    first = out["candidates"][0]["tokens"]["light"]
    assert first["chart-1"] == from_oklch(*oklch(first["chart-1"]))
    assert hue_close(oklch(first["chart-2"])[2], 48)  # the logo's orange comes second


def hue_close(a: float, b: float, tolerance: float = 6) -> bool:
    return min(abs(a - b), 360 - abs(a - b)) <= tolerance


@pytest.mark.parametrize("seeds", [["#d62828", "#2a9d48"], ["#e30613"], ["#00a651"], ["#c62828", "#2e7d32", "#ffffff"]])
def test_a_logo_with_only_red_and_green_gets_safe_palettes(seeds: list[str]) -> None:
    out = candidates(seeds)
    assert len(out["candidates"]) >= 3
    for p in out["candidates"]:
        assert p["ok"], _failed(p)
        for theme in THEMES:
            t = p["tokens"][theme]
            assert not [k for k in ("accent", "accent-strong", *CHART, "div-neg-2", "div-pos-2") if rag_kind(t[k])]
        assert any("means" in n for n in p["notes"])


def test_greys_only_gives_no_candidates_and_says_why() -> None:
    out = candidates(["#777777", "#111111", "#eeeeee"])
    assert out == {"candidates": [], "dropped": 0, "greys_only": True}
    assert candidates([]) == {"candidates": [], "dropped": 0, "greys_only": False}


def test_candidates_are_distinct_and_capped() -> None:
    out = candidates(["#6a1b9a", "#ff9800", "#03a9f4", "#e91e63", "#795548", "#3f51b5", "#009688"])
    accents = [parse_hex(p["tokens"]["light"]["accent"]) for p in out["candidates"]]
    assert 3 <= len(accents) <= 5
    assert all(delta_e(a, b) >= 5 for i, a in enumerate(accents) for b in accents[i + 1:])
