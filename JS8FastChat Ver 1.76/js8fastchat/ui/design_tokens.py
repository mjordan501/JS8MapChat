from __future__ import annotations

"""design_tokens.py — Phase 5 of the JS8FastChat <-> JS8Map integration.

Single source of truth for the structural design tokens shared between
JS8FastChat and JS8Map, so the two apps read as one product.

These values are copied directly from JS8Map's app.css :root blocks
(JS8Map v1.83) -- the dark theme from the default :root, the light theme
from body.light-theme. Keeping them here, named the same way JS8Map names
them, means a future change on either side has an obvious place to stay in
sync.

SCOPE (deliberate -- see the session notes in the README):
  - STRUCTURAL tokens (bg, surface, card, border, text, muted, popup bg/
    border) ARE shared and matched to JS8Map exactly. These are what make a
    theme read as "the same app," and they are what must flip correctly
    between light and dark. FastChat's Palette pulls these so its light and
    dark modes belong to the same family as the map.
  - FUNCTION-SIGNAL accents (FastChat's bright SEND green, HALT/danger red)
    are intentionally NOT taken from JS8Map. They are usability signals that
    need high contrast in BOTH themes -- JS8Map's muted map-friendly green
    (#26a869) would weaken the SEND button, especially on the light theme's
    white background. FastChat keeps its brighter accents; only the
    structure is shared. JS8Map's accent values are still recorded below for
    reference/cohesion, just not forced onto FastChat's function buttons.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StructuralTokens:
    """The theme-defining tokens shared with JS8Map (values are JS8Map's)."""
    bg: str
    surface: str        # JS8Map --surface  (FastChat 'panel')
    card: str           # JS8Map --card     (FastChat 'panel2')
    border: str
    text: str
    muted: str
    popup_bg: str
    popup_border: str


# Dark theme -- JS8Map app.css default :root
JS8MAP_DARK = StructuralTokens(
    bg="#080c10",
    surface="#0e1520",
    card="#131d2b",
    border="#1e2d40",
    text="#d0dce8",
    muted="#b8d4ee",
    popup_bg="#0e1a28",
    popup_border="#2a3f55",
)

# Light theme -- JS8Map app.css body.light-theme
JS8MAP_LIGHT = StructuralTokens(
    bg="#f0f4f8",
    surface="#ffffff",
    card="#e8edf3",
    border="#c8d4e0",
    text="#1a2a3a",
    muted="#6080a0",
    popup_bg="#ffffff",
    popup_border="#c0d0e0",
)


# JS8Map's accent/semantic values, recorded for reference and cross-app
# cohesion. FastChat does NOT force these onto its function-signal buttons
# (see module docstring), but they are the canonical map-side values.
JS8MAP_ACCENTS = {
    "blue": "#2979ff",
    "green": "#26a869",     # map-friendly muted green (NOT used for FastChat SEND)
    "orange": "#f5a623",
    "red": "#e84060",       # map red (NOT used for FastChat HALT/danger)
    "accent": "#1565c0",
    "gold": "#FFD700",      # legend gold (dark); light theme uses #8B6400
    "gold_light": "#8B6400",
}

# JS8Map's semantic relationship buckets (heard / hearing-me / both / watched
# / relay / hb). Recorded so FastChat's tile/group coloring can be aligned to
# the map's meaning over time. Not wired into FastChat's GROUP_COLORS yet --
# that is a content decision (group names) distinct from these relationship
# buckets, and is left for a follow-on rather than guessed at here.
JS8MAP_BUCKETS = {
    "heard": "#29b6f6",
    "hearing_me": "#26a869",
    "both": "#f5a623",
    "watched": "#FFD700",
    "relay": "#29b6f6",
    "hb": "#FFD700",
}
