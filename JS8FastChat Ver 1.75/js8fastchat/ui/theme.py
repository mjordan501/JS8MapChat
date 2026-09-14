from __future__ import annotations

from dataclasses import dataclass

from .design_tokens import JS8MAP_DARK, JS8MAP_LIGHT


@dataclass(frozen=True)
class Palette:
    bg: str
    panel: str
    panel2: str
    header: str
    header_text: str
    text: str
    muted: str
    entry_bg: str
    entry_fg: str
    border: str
    button: str
    button_active: str
    danger: str
    green: str
    gold: str
    selected: str
    tree_bg: str
    tree_fg: str
    tree_heading: str


# Phase 5 cohesion: STRUCTURAL tokens in the DARK theme (panel / panel2 /
# border / muted) are pulled from JS8Map's canonical palette in
# design_tokens.py so FastChat and JS8Map read as one app. FastChat's dark
# backgrounds already matched JS8Map exactly; this additionally aligns the
# card surface, border (previously a near-white #d6dde6 that read harshly),
# and muted text to JS8Map's values.
#
# The LIGHT theme now ALSO shares JS8Map's structural tokens (3.5_7). It
# moves from FastChat's old flat single-gray surface (#d6d9df everywhere) to
# JS8Map's layered light look: a light blue-grey page (bg), white panels
# (surface), and light-tinted cards (card). entry_bg stays white (matches
# JS8Map surface). This was deferred in 3.5_5 because the layered look could
# not be render-verified headlessly; it is applied now for full cohesion and
# should be visually confirmed by the operator/testers.
#
# FUNCTION-SIGNAL accents (green=SEND, danger=HALT, gold, selected) and 'text'
# are kept at FastChat's brighter/readable values in BOTH themes for button
# legibility and dense-console readability -- see design_tokens.py docstring
# for why JS8Map's muted map accents are not forced onto FastChat's action
# buttons.

LIGHT = Palette(
    bg=JS8MAP_LIGHT.bg, panel=JS8MAP_LIGHT.surface, panel2=JS8MAP_LIGHT.card,
    header="#17324f", header_text="#ffffff",
    text="#0f172a", muted=JS8MAP_LIGHT.muted, entry_bg="#ffffff", entry_fg="#0f172a",
    border=JS8MAP_LIGHT.border,
    button="#d0d4da", button_active="#bcc4ce", danger="#9b1c1c", green="#33ff77", gold="#c98200",
    selected="#b6f2c8", tree_bg="#e7eaee", tree_fg="#0f172a", tree_heading="#d0d4da",
)

DARK = Palette(
    bg=JS8MAP_DARK.bg, panel=JS8MAP_DARK.surface, panel2=JS8MAP_DARK.card,
    header="#0b1220", header_text="#ffffff",
    text="#ffffff", muted=JS8MAP_DARK.muted, entry_bg="#111827", entry_fg="#ffffff",
    border=JS8MAP_DARK.border,
    button="#374151", button_active="#4b5563", danger="#9b1c1c", green="#33ff77", gold="#ffd700",
    selected="#2f8f57", tree_bg="#0f172a", tree_fg="#ffffff", tree_heading="#374151",
)
