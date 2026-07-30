"""Locks in design-01.md's contrast sweep: the repo-badge HSL background/text
pairs (`.badge.repo-badge` in `static/app.css`) must clear WCAG AA (4.5:1) at
every possible hue, since the hue is derived per-repo-name and never authored
by hand — a single bad hue would be a badge nobody chose to make unreadable.
"""

import colorsys

import pytest

# Mirrors static/app.css's `.badge.repo-badge` rule (light) and its
# `@media (prefers-color-scheme: dark)` override (dark) — (saturation%, lightness%)
# pairs for background and text, keep these in sync with the CSS by hand.
LIGHT = {"bg": (50, 92), "fg": (70, 22)}
DARK = {"bg": (45, 22), "fg": (70, 82)}

AA_NORMAL_TEXT = 4.5


def _srgb_channel_to_linear(channel: float) -> float:
    if channel <= 0.03928:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def _relative_luminance(hue_degrees: int, saturation_pct: int, lightness_pct: int) -> float:
    r, g, b = colorsys.hls_to_rgb(
        hue_degrees / 360, lightness_pct / 100, saturation_pct / 100
    )
    r, g, b = (_srgb_channel_to_linear(c) for c in (r, g, b))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(hue_degrees: int, pair: dict) -> float:
    bg_s, bg_l = pair["bg"]
    fg_s, fg_l = pair["fg"]
    l1 = _relative_luminance(hue_degrees, bg_s, bg_l)
    l2 = _relative_luminance(hue_degrees, fg_s, fg_l)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("theme,pair", [("light", LIGHT), ("dark", DARK)])
def test_every_hue_clears_wcag_aa_contrast(theme, pair):
    worst_hue = min(range(360), key=lambda hue: _contrast_ratio(hue, pair))
    worst_ratio = _contrast_ratio(worst_hue, pair)

    assert worst_ratio >= AA_NORMAL_TEXT, (
        f"{theme} repo-badge contrast at hue {worst_hue} is {worst_ratio:.2f}:1, "
        f"below WCAG AA ({AA_NORMAL_TEXT}:1)"
    )
