"""
Theming: semantic colour roles, three required palettes, and the active-palette proxy.

Panels reference *roles* -- ``P.state_awaiting``, ``P.diff_add`` -- never literals.
A theme is a table of roles, so adding one is filling in a table rather than
auditing every draw call. That is the whole reason this module exists before the
first panel does.

Two rules here are load-bearing rather than cosmetic (design §6.1):

1. **Hue is never the only channel.** Every agent state carries a glyph and a text
   label alongside its colour, and the context ring encodes headroom as fill as
   well as hue. ``high_contrast`` collapses the palette toward a few values, so any
   signal living only in hue disappears there -- and the same signal is already
   invisible to a colour-deficient operator in the other themes. Honouring this is
   what makes high_contrast a theme switch rather than a second UI.

2. **Colour lookup is on the per-frame build path.** ``P.accent`` is read hundreds
   of times a frame, so it must be an attribute read on an already-constructed
   object. Both the ``ImVec4`` and the packed ``ImU32`` form are computed once, at
   palette construction -- never per access, and never by arithmetic in a panel.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from types import ModuleType
from typing import cast

from imgui_bundle import icons_fontawesome_6 as fa
from imgui_bundle import imgui

from .model import AgentState, ContextPressure, ObligationKind


@dataclass(frozen=True, slots=True)
class Color:
    """
    One colour in both forms the API needs.

    imgui widget calls want an ``ImVec4``; draw-list calls want a packed ``ImU32``.
    Precomputing both is what keeps ``u32()`` conversions and ``ImVec4``
    allocations off the render path.
    """

    vec4: imgui.ImVec4
    u32: int

    @property
    def rgb(self) -> tuple[float, float, float]:
        return (self.vec4.x, self.vec4.y, self.vec4.z)


def col(hex_rgb: int, alpha: float = 1.0) -> Color:
    """Build a Color from 0xRRGGBB."""
    r = ((hex_rgb >> 16) & 0xFF) / 255.0
    g = ((hex_rgb >> 8) & 0xFF) / 255.0
    b = (hex_rgb & 0xFF) / 255.0
    return Color(
        vec4=imgui.ImVec4(r, g, b, alpha),
        u32=imgui.IM_COL32(
            (hex_rgb >> 16) & 0xFF, (hex_rgb >> 8) & 0xFF, hex_rgb & 0xFF, int(alpha * 255)
        ),
    )


_FADE_STEPS = 12
_FADE_CACHE: dict[tuple[int, int], int] = {}


def faded(colour: Color, alpha: float) -> int:
    """
    ``colour`` at a fraction of its own alpha, packed for the draw list.

    Colour arithmetic lives here rather than in a panel for the reason stated at the
    top of this module: lookups are on the per-frame build path. The only caller is
    an animation, where the alpha *is* the animation, so it cannot be precomputed at
    palette construction -- the compromise is to quantise to twelve steps and
    memoise, which is finer than the eye resolves on a three-pixel square and turns
    a per-cell-per-frame pack into a dict hit.

    Keyed on the packed form, so a theme switch neither invalidates the cache nor
    keeps the retired palette alive.
    """
    step = max(0, min(_FADE_STEPS, round(alpha * _FADE_STEPS)))
    key = (colour.u32, step)
    hit = _FADE_CACHE.get(key)
    if hit is None:
        v = colour.vec4
        hit = imgui.IM_COL32(
            int(v.x * 255.0),
            int(v.y * 255.0),
            int(v.z * 255.0),
            int(v.w * 255.0 * step / _FADE_STEPS),
        )
        _FADE_CACHE[key] = hit
    return hit


@dataclass(frozen=True, slots=True)
class Palette:
    """
    The role table. Every field is a role; none is named for the colour it holds.

    Adding a role here is a compile-time obligation on every palette below, which is
    the point -- a role that only some themes define is a role that renders as a
    crash or an invisible widget in the others.
    """

    name: str
    display_name: str
    is_dark: bool

    # surfaces
    bg: Color
    panel: Color
    panel_alt: Color
    # What a field the operator types or picks a value in is filled with where it
    # stands on ``panel_alt`` -- a modal's surface. On most palettes this is the
    # value of ``panel``, and that is a choice per palette and not a definition:
    # the two are held to different floors (tests/test_theme.py), and a palette
    # whose docks are not what its fields should look like says so here.
    well: Color
    # What a field is filled with where it stands on a dock's pane, ``bg``: a
    # step from the pane toward ``text`` -- 0.10 of the way, and 0.08 on light,
    # where 0.10 would take the hint under 4.5:1. So it is lighter than the pane
    # on a dark palette and darker on a light one, and it is the one thing that
    # tells a dock's field from its pane, since a frame has no outline but on
    # high_contrast. Not ``well``: a modal's fields stand on another surface.
    #
    # Two departures, each the least step that clears the floors
    # (tests/test_theme.py). turbo steps 0.19: its ``panel``, where a child
    # window's checkbox stands, is lighter than ``bg``, and every step from 0.11
    # to 0.18 passes through it. cde and win311 step toward white, 0.10 and
    # 0.11: their grounds are mid-tones with dark text, and a well darker than
    # the pane leaves typed text under 6.75:1 in it, under which no hint is
    # both 4.5:1 in the well and 1.5:1 off typed text.
    dock_well: Color
    border: Color
    selection: Color

    # text
    text: Color
    # Secondary text, and ImGui's ``text_disabled``: ``imgui.text_disabled`` draws
    # in it at about a hundred call sites, so it is held to read apart from
    # ``text`` and not raised to a field's floor. Placeholder text is ``hint``.
    text_dim: Color
    # The hint in an empty field on a dock: ``text_dim`` where it already reads at
    # 4.5:1 in ``dock_well``, and otherwise the smallest step of it toward
    # ``text`` that does (tests/test_theme.py). ``dialog_hint`` is the same in
    # ``well``, a modal's field: one colour for both would be the darker well's,
    # which on win311 put a placeholder darker than its own field's caption.
    # ImGui draws a hint in ``text_disabled``, so a field with one is drawn
    # inside ``hint_colour()``, told which of the two wells it stands in.
    hint: Color
    dialog_hint: Color
    text_strong: Color

    # general accents. Three jobs, one colour each: accent is structure and live
    # values, focus is selection only, warn is attention only. If everything
    # glows, nothing reads as important.
    accent: Color
    focus: Color
    warn: Color
    danger: Color
    ok: Color

    # agent state. Always drawn with STATE_GLYPH and STATE_LABEL beside them.
    state_spawning: Color
    state_thinking: Color
    state_calling_tool: Color
    state_awaiting: Color
    state_running: Color
    state_awaiting_input: Color
    state_supervising: Color
    state_done: Color
    state_failed: Color
    state_cancelled: Color
    state_rate_limited: Color

    # context pressure: the ring's fill colour, blue -> orange -> red
    pressure_nominal: Color
    pressure_nearing: Color
    pressure_compacted: Color
    ring_track: Color

    # approval gate
    diff_add: Color
    diff_remove: Color
    diff_context: Color

    # the tool block (``ui.tool_block``): a terminal's ground and its two inks. The
    # ground is the theme's, never a fixed near-black (the operator: "Theme
    # dictates the 'terminal' background"), and is derived from the pane by its
    # luminance (tests/test_theme.py holds each palette to it):
    #   - a dark pane goes deeper, mix(bg, black, 0.60), so the block keeps a
    #     terminal's look a step off the pane;
    #   - a pane already black has nowhere deeper to go: mix(bg, text, 0.10), and
    #     ``terminal_outline``;
    #   - a mid-tone pane (cde, win311) steps toward white, mix(bg, white, 0.20):
    #     on a step toward its dark text no ink of the palette but the text itself
    #     reads at 4.5:1. 0.20 is the largest step that stays within 1.3:1 of the
    #     pane and of ``panel``, the log the block is drawn in, so the inks
    #     ``terminal_ink`` moves are moved the least;
    #   - a light pane a soft slab, mix(bg, text, 0.06), under the dock well's
    #     step so it does not read as a field.
    # The inks are the palette's ``text`` and ``terminal_ink(text_dim)`` -- which is
    # ``text_dim`` itself but on kitty and win311, whose greys are 3.78 and 4.28:1
    # on their grounds; every
    # other role a block draws in goes through ``terminal_ink`` as it is drawn.
    terminal_bg: Color
    terminal_text: Color
    terminal_dim: Color
    # Whether the block is drawn with an outline in ``border``: on a black pane,
    # where its ground is the only other thing telling it from the pane, and the
    # theme keeps frames.
    terminal_outline: bool

    # Not a colour: how far a button at rest is from ``panel_alt`` toward
    # ``accent``. Hovered and held are each ``_BUTTON_STEP`` further. 0.20, and
    # 0.36 on light, whose buttons at 0.20 are 1.41:1 on the pane against 1.75
    # on dark.
    button_tint: float

    def state(self, state: AgentState) -> Color:
        return cast(Color, getattr(self, _STATE_ROLE[state]))

    def pressure(self, pressure: ContextPressure) -> Color:
        return cast(Color, getattr(self, _PRESSURE_ROLE[pressure]))

    def obligation(self, kind: ObligationKind) -> Color:
        return cast(Color, getattr(self, _OBLIGATION_ROLE[kind]))


_STATE_ROLE: dict[AgentState, str] = {
    AgentState.SPAWNING: "state_spawning",
    AgentState.THINKING: "state_thinking",
    AgentState.CALLING_TOOL: "state_calling_tool",
    AgentState.AWAITING_APPROVAL: "state_awaiting",
    AgentState.RUNNING_TOOL: "state_running",
    AgentState.AWAITING_INPUT: "state_awaiting_input",
    AgentState.SUPERVISING: "state_supervising",
    AgentState.DONE: "state_done",
    AgentState.FAILED: "state_failed",
    AgentState.CANCELLED: "state_cancelled",
    AgentState.RATE_LIMITED: "state_rate_limited",
}

_PRESSURE_ROLE: dict[ContextPressure, str] = {
    ContextPressure.NOMINAL: "pressure_nominal",
    ContextPressure.NEARING_COMPACTION: "pressure_nearing",
    ContextPressure.COMPACTED: "pressure_compacted",
}

# The non-hue channels. Theme-independent by design: a glyph that changed between
# themes would be a second thing to learn per theme, and the whole point of these
# is that they carry the signal when colour cannot.
STATE_GLYPH: dict[AgentState, str] = {
    AgentState.SPAWNING: fa.ICON_FA_HOURGLASS_START,
    AgentState.THINKING: fa.ICON_FA_BRAIN,
    AgentState.CALLING_TOOL: fa.ICON_FA_ARROW_RIGHT_LONG,
    AgentState.AWAITING_APPROVAL: fa.ICON_FA_HAND,
    AgentState.RUNNING_TOOL: fa.ICON_FA_GEARS,
    AgentState.AWAITING_INPUT: fa.ICON_FA_COMMENT_DOTS,
    AgentState.SUPERVISING: fa.ICON_FA_SITEMAP,
    AgentState.DONE: fa.ICON_FA_CHECK,
    AgentState.FAILED: fa.ICON_FA_TRIANGLE_EXCLAMATION,
    AgentState.CANCELLED: fa.ICON_FA_BAN,
    AgentState.RATE_LIMITED: fa.ICON_FA_CLOCK,
}

# Obligations borrow the glyph and hue of the state that produces them, so an
# inbox row and the card it came from read as the same thing rather than as two
# separate vocabularies the operator has to learn twice.
OBLIGATION_GLYPH: dict[ObligationKind, str] = {
    ObligationKind.APPROVAL: STATE_GLYPH[AgentState.AWAITING_APPROVAL],
    ObligationKind.QUESTION: STATE_GLYPH[AgentState.AWAITING_INPUT],
    ObligationKind.FAILURE: STATE_GLYPH[AgentState.FAILED],
    # No state produces a decision -- its asker can be in any of them -- so it has
    # a glyph of its own and borrows none.
    ObligationKind.DECISION: fa.ICON_FA_SCALE_BALANCED,
    # Nor a command, and it is not to be taken for an approval: the hand is a
    # call the CLI will run inside the sandbox, and this is a string pptmstr
    # will run outside it.
    ObligationKind.COMMAND: fa.ICON_FA_TERMINAL,
}

# Open/closed, keyed by whether the thing is open. FontAwesome rather than the
# Unicode triangles the shape suggests: the UI face is Inconsolata, whose cmap has
# no U+25B0 block at all, so `▸` and `▾` both render as the same fallback box --
# a disclosure that cannot say which way it points. The merged icon font is the
# only one of the two guaranteed to carry a caret.
DISCLOSURE_GLYPH: dict[bool, str] = {
    False: fa.ICON_FA_CARET_RIGHT,
    True: fa.ICON_FA_CARET_DOWN,
}

_OBLIGATION_ROLE: dict[ObligationKind, str] = {
    ObligationKind.APPROVAL: "state_awaiting",
    ObligationKind.QUESTION: "state_awaiting_input",
    ObligationKind.FAILURE: "state_failed",
    ObligationKind.DECISION: "warn",
    # The alarm role, and on purpose: of everything in the queue this is the row
    # where a yes runs a string as the operator, outside the sandbox. It shares
    # the hue of a failure on palettes that draw both alike; the glyph is what
    # tells them apart, as it is for every kind.
    ObligationKind.COMMAND: "danger",
}

STATE_LABEL: dict[AgentState, str] = {
    AgentState.SPAWNING: "spawning",
    AgentState.THINKING: "thinking",
    AgentState.CALLING_TOOL: "calling",
    AgentState.AWAITING_APPROVAL: "REVIEW",
    AgentState.RUNNING_TOOL: "running",
    AgentState.AWAITING_INPUT: "YOUR TURN",
    AgentState.SUPERVISING: "supervising",
    AgentState.DONE: "done",
    AgentState.FAILED: "failed",
    AgentState.CANCELLED: "cancelled",
    AgentState.RATE_LIMITED: "limited",
}


DARK = Palette(
    name="dark",
    display_name="Dark",
    is_dark=True,
    bg=col(0x14161C),
    panel=col(0x1A1D26),
    panel_alt=col(0x21242F),
    well=col(0x1A1D26),
    dock_well=col(0x26282F),
    border=col(0x323848),
    selection=col(0x2B3550),
    text=col(0xC9CEDC),
    text_dim=col(0x7C8397),
    hint=col(0x888EA1),
    dialog_hint=col(0x7D8497),
    text_strong=col(0xF0F3FA),
    accent=col(0x4FC1E9),
    focus=col(0xC77DFF),
    warn=col(0xE8B339),
    danger=col(0xE5484D),
    ok=col(0x4EC97E),
    state_spawning=col(0x7C8397),
    state_thinking=col(0x4FC1E9),
    state_calling_tool=col(0x8AB4F8),
    state_awaiting=col(0xE8B339),
    state_running=col(0x4EC97E),
    state_awaiting_input=col(0x8AB4F8),
    state_supervising=col(0xB39DFF),
    state_done=col(0x6E7688),
    state_failed=col(0xE5484D),
    state_cancelled=col(0x6E7688),
    state_rate_limited=col(0xE07B39),
    pressure_nominal=col(0x4FA3E9),
    pressure_nearing=col(0xE08A2E),
    pressure_compacted=col(0xE5484D),
    ring_track=col(0x39404F),
    diff_add=col(0x4EC97E),
    diff_remove=col(0xE5484D),
    diff_context=col(0x7C8397),
    terminal_bg=col(0x08090B),
    terminal_text=col(0xC9CEDC),
    terminal_dim=col(0x7C8397),
    terminal_outline=False,
    button_tint=0.20,
)

LIGHT = Palette(
    name="light",
    display_name="Light",
    is_dark=False,
    bg=col(0xF4F5F8),
    panel=col(0xFFFFFF),
    panel_alt=col(0xEBEDF2),
    well=col(0xFFFFFF),
    dock_well=col(0xE3E4E8),
    border=col(0xC4C9D4),
    selection=col(0xD3E2F7),
    text=col(0x1F232C),
    text_dim=col(0x5E6675),
    hint=col(0x5E6675),
    dialog_hint=col(0x5E6675),
    text_strong=col(0x0A0D14),
    accent=col(0x0C6E9E),
    focus=col(0x7A28C4),
    warn=col(0x8A5A00),
    danger=col(0xB3211F),
    ok=col(0x1A6E3C),
    state_spawning=col(0x5E6675),
    state_thinking=col(0x0C6E9E),
    state_calling_tool=col(0x2A4FA8),
    state_awaiting=col(0x8A5A00),
    state_running=col(0x1A6E3C),
    state_awaiting_input=col(0x2A4FA8),
    state_supervising=col(0x5B3FBF),
    state_done=col(0x6B7280),
    state_failed=col(0xB3211F),
    state_cancelled=col(0x6B7280),
    state_rate_limited=col(0x9A4A0A),
    pressure_nominal=col(0x1F78C1),
    pressure_nearing=col(0xB06A00),
    pressure_compacted=col(0x8E1512),
    ring_track=col(0xD2D6DF),
    diff_add=col(0x1A6E3C),
    diff_remove=col(0xB3211F),
    diff_context=col(0x5E6675),
    terminal_bg=col(0xE7E8EC),
    terminal_text=col(0x1F232C),
    terminal_dim=col(0x5E6675),
    terminal_outline=False,
    button_tint=0.36,
)

# Maximum separation, not a darker dark. Text is pure white on pure black (21:1),
# and the accents are chosen to stay distinguishable from each other by lightness
# as well as hue -- so the palette degrades gracefully rather than collapsing into
# "several bright things".
HIGH_CONTRAST = Palette(
    name="high_contrast",
    display_name="High contrast",
    is_dark=True,
    bg=col(0x000000),
    panel=col(0x000000),
    panel_alt=col(0x101010),
    well=col(0x000000),
    dock_well=col(0x1A1A1A),
    border=col(0xFFFFFF),
    selection=col(0x0050B3),
    text=col(0xFFFFFF),
    text_dim=col(0xC0C0C0),
    hint=col(0xC0C0C0),
    dialog_hint=col(0xC0C0C0),
    text_strong=col(0xFFFFFF),
    accent=col(0x00E5FF),
    focus=col(0xFF66FF),
    warn=col(0xFFD400),
    danger=col(0xFF6B6B),
    ok=col(0x4CFF88),
    state_spawning=col(0xC0C0C0),
    state_thinking=col(0x00E5FF),
    state_calling_tool=col(0x8FB8FF),
    state_awaiting=col(0xFFD400),
    state_running=col(0x4CFF88),
    state_awaiting_input=col(0x8FB8FF),
    state_supervising=col(0xD7A0FF),
    state_done=col(0xC0C0C0),
    state_failed=col(0xFF6B6B),
    state_cancelled=col(0xC0C0C0),
    state_rate_limited=col(0xFFA23E),
    pressure_nominal=col(0x62B9FF),
    pressure_nearing=col(0xFFA23E),
    pressure_compacted=col(0xFF6B6B),
    ring_track=col(0x6A6A6A),
    diff_add=col(0x4CFF88),
    diff_remove=col(0xFF6B6B),
    diff_context=col(0xC0C0C0),
    terminal_bg=col(0x1A1A1A),
    terminal_text=col(0xFFFFFF),
    terminal_dim=col(0xC0C0C0),
    terminal_outline=True,
    button_tint=0.20,
)

# The Ghost in the Shell one. Discretionary, and held to the same role table as the
# required three -- a "fun" theme that skipped roles would crash panels the others
# render fine.
GHOST = Palette(
    name="ghost",
    display_name="Ghost",
    is_dark=True,
    bg=col(0x0B1216),
    panel=col(0x0F1A20),
    panel_alt=col(0x14242C),
    well=col(0x0F1A20),
    dock_well=col(0x1B2629),
    border=col(0x1F3E49),
    selection=col(0x12333D),
    text=col(0xA8D8D0),
    text_dim=col(0x5A8079),
    hint=col(0x6A938B),
    dialog_hint=col(0x628981),
    text_strong=col(0xE4FFFA),
    accent=col(0x22D3B8),
    focus=col(0xFF4FA3),
    warn=col(0xE8C547),
    danger=col(0xFF5470),
    ok=col(0x22D3B8),
    state_spawning=col(0x5A8079),
    state_thinking=col(0x22D3B8),
    state_calling_tool=col(0x4FD6FF),
    state_awaiting=col(0xE8C547),
    state_running=col(0x5BE8A0),
    state_awaiting_input=col(0x4FD6FF),
    state_supervising=col(0xB89CFF),
    state_done=col(0x4A6B66),
    state_failed=col(0xFF5470),
    state_cancelled=col(0x4A6B66),
    state_rate_limited=col(0xFF9E5E),
    pressure_nominal=col(0x35B7E8),
    pressure_nearing=col(0xE8A33D),
    pressure_compacted=col(0xFF5470),
    ring_track=col(0x1F3E49),
    diff_add=col(0x22D3B8),
    diff_remove=col(0xFF5470),
    diff_context=col(0x5A8079),
    terminal_bg=col(0x040709),
    terminal_text=col(0xA8D8D0),
    terminal_dim=col(0x5A8079),
    terminal_outline=False,
    button_tint=0.20,
)

# -- the period pieces ---------------------------------------------------------
#
# Five themes that quote a specific machine. They are held to exactly the same
# role table and the same measured contrast floors as the required three, which
# is the interesting constraint: the originals were designed for a CRT and a
# fixed 16-entry hardware palette, and several of their signature colours miss
# WCAG AA outright. Where a quoted colour and a floor disagree the floor wins and
# the colour moves the smallest distance that clears it -- noted per palette. A
# theme that is authentic and unreadable is a screenshot, not a theme.

# Black on warm paper, and the eight ANSI colours as xterm shipped them --
# except that against paper xterm's green (#00CD00) and yellow (#CDCD00) read at
# 1.9:1 and 1.5:1, so those two are taken from the dark half of the ramp instead.
XTERM = Palette(
    name="xterm",
    display_name="Xterm",
    is_dark=False,
    bg=col(0xE8E6DC),
    panel=col(0xF2F0E6),
    panel_alt=col(0xDCD9CC),
    well=col(0xF2F0E6),
    dock_well=col(0xD4D2C8),
    border=col(0x9A9686),
    selection=col(0xC8C4B0),
    text=col(0x1C1B18),
    text_dim=col(0x56534A),
    hint=col(0x56534A),
    dialog_hint=col(0x56534A),
    text_strong=col(0x000000),
    accent=col(0x0000C4),
    focus=col(0x8B008B),
    warn=col(0x8A6D00),
    danger=col(0xCD0000),
    ok=col(0x006B00),
    state_spawning=col(0x56534A),
    state_thinking=col(0x0000C4),
    state_calling_tool=col(0x005F87),
    state_awaiting=col(0x8A6D00),
    state_running=col(0x006B00),
    state_awaiting_input=col(0x005F87),
    state_supervising=col(0x5C2D91),
    state_done=col(0x6B685E),
    state_failed=col(0xCD0000),
    state_cancelled=col(0x6B685E),
    state_rate_limited=col(0xA34B00),
    pressure_nominal=col(0x0000C4),
    pressure_nearing=col(0xA05A00),
    pressure_compacted=col(0xCD0000),
    ring_track=col(0xC4C0AE),
    diff_add=col(0x006B00),
    diff_remove=col(0xCD0000),
    diff_context=col(0x56534A),
    terminal_bg=col(0xDCDAD0),
    terminal_text=col(0x1C1B18),
    terminal_dim=col(0x56534A),
    terminal_outline=False,
    button_tint=0.20,
)

# CDE's bluish widget grey (#AEB2C3) over a darker backdrop of the same hue.
# Everything else follows from the surface: black text on a mid-grey panel
# leaves state colours a ceiling of about 0.11 relative luminance, so every
# accent here is a dark saturated one. That is not a stylistic choice -- it is
# what a mid-tone chassis costs, and it is why CDE itself looked like this.
CDE = Palette(
    name="cde",
    display_name="CDE",
    is_dark=False,
    bg=col(0x8F94AB),
    panel=col(0xAEB2C3),
    panel_alt=col(0xC2C6D4),
    well=col(0xAEB2C3),
    dock_well=col(0x9A9FB3),
    border=col(0x4E5065),
    selection=col(0x8892AD),
    text=col(0x0B0C12),
    text_dim=col(0x33364A),
    hint=col(0x33364A),
    dialog_hint=col(0x33364A),
    text_strong=col(0x000000),
    accent=col(0x1F4E8C),
    focus=col(0x5C2D91),
    warn=col(0x6E4A00),
    danger=col(0xA32020),
    ok=col(0x165C33),
    state_spawning=col(0x3E4152),
    state_thinking=col(0x1F4E8C),
    state_calling_tool=col(0x1F5490),
    state_awaiting=col(0x6E4A00),
    state_running=col(0x165C33),
    state_awaiting_input=col(0x1F5490),
    state_supervising=col(0x3B2A63),
    state_done=col(0x494D60),
    state_failed=col(0xA32020),
    state_cancelled=col(0x494D60),
    state_rate_limited=col(0x8A3A00),
    pressure_nominal=col(0x0E2E63),
    pressure_nearing=col(0x7A4800),
    pressure_compacted=col(0xA82020),
    ring_track=col(0x8892AD),
    diff_add=col(0x165C33),
    diff_remove=col(0xA32020),
    diff_context=col(0x3E4152),
    terminal_bg=col(0xA5A9BC),
    terminal_text=col(0x0B0C12),
    terminal_dim=col(0x33364A),
    terminal_outline=False,
    button_tint=0.20,
)

# #C0C0C0 chassis, VGA 16-colour accents, and the teal desktop behind it. Two
# departures, both forced: the desktop teal is lightened from #008080 (black on
# it is 4.4:1, just under the AA floor, and lifting it is cheaper than giving up
# black text), and the selection fill is a navy *tint* rather than #000080 --
# ImGui draws the same P.text over a selected row as an unselected one, so a
# true navy fill would swallow it. The well is the white an edit control had on
# that chassis, and not the chassis grey its docks are.
WIN311 = Palette(
    name="win311",
    display_name="Windows 3.11",
    is_dark=False,
    bg=col(0x74A0A0),
    panel=col(0xC0C0C0),
    panel_alt=col(0xCFCFCF),
    well=col(0xFFFFFF),
    dock_well=col(0x83AAAA),
    border=col(0x808080),
    selection=col(0xA8B4D8),
    text=col(0x000000),
    text_dim=col(0x44444A),
    hint=col(0x39393F),
    dialog_hint=col(0x44444A),
    text_strong=col(0x000000),
    accent=col(0x000080),
    focus=col(0x800080),
    warn=col(0x5E5E00),
    danger=col(0x900000),
    ok=col(0x006B00),
    state_spawning=col(0x4A4A4A),
    state_thinking=col(0x000080),
    state_calling_tool=col(0x1A32A8),
    state_awaiting=col(0x5E5E00),
    state_running=col(0x006B00),
    state_awaiting_input=col(0x1A32A8),
    state_supervising=col(0x4B0082),
    state_done=col(0x4A4A4A),
    state_failed=col(0x900000),
    state_cancelled=col(0x4A4A4A),
    state_rate_limited=col(0x8A3D00),
    pressure_nominal=col(0x000080),
    pressure_nearing=col(0x7A4A00),
    pressure_compacted=col(0x900000),
    ring_track=col(0x808080),
    diff_add=col(0x006B00),
    diff_remove=col(0x900000),
    diff_context=col(0x4A4A4A),
    terminal_bg=col(0x90B3B3),
    terminal_text=col(0x000000),
    terminal_dim=col(0x404046),
    terminal_outline=False,
    button_tint=0.20,
)

# Greetingz Kat. White face, red bow, yellow nose. The bow red carries `danger`
# unchanged, but the nose is #FFD800 and that is 1.3:1 on these surfaces, so
# `warn` is the same hue dragged down until it reads as text. The one theme here
# whose surfaces are tinted rather than neutral: with a white panel the pink is
# confined to borders and accents, and it reads as the light theme with trim.
KITTY = Palette(
    name="kitty",
    display_name="Greetingz Kat",
    is_dark=False,
    bg=col(0xFBDCE8),
    panel=col(0xFFF4F8),
    panel_alt=col(0xFFE7F1),
    well=col(0xFFF4F8),
    dock_well=col(0xE8CAD6),
    border=col(0xF2A8C4),
    selection=col(0xFFD0E2),
    text=col(0x3A2A33),
    text_dim=col(0x8A5E72),
    hint=col(0x755061),
    dialog_hint=col(0x8A5E72),
    text_strong=col(0x1F1219),
    accent=col(0xE0407A),
    focus=col(0x9B4DCA),
    warn=col(0xB07A00),
    danger=col(0xD62246),
    ok=col(0x1E8F5E),
    state_spawning=col(0x8A5E72),
    state_thinking=col(0xE0407A),
    state_calling_tool=col(0x5B6FD6),
    state_awaiting=col(0xB07A00),
    state_running=col(0x1E8F5E),
    state_awaiting_input=col(0x5B6FD6),
    state_supervising=col(0x7A3FBF),
    state_done=col(0x8C7A85),
    state_failed=col(0xD62246),
    state_cancelled=col(0x8C7A85),
    state_rate_limited=col(0xC85A1E),
    pressure_nominal=col(0x5B6FD6),
    pressure_nearing=col(0xB07A00),
    pressure_compacted=col(0xD62246),
    ring_track=col(0xF7CBDC),
    diff_add=col(0x1E8F5E),
    diff_remove=col(0xD62246),
    diff_context=col(0x8A5E72),
    terminal_bg=col(0xEFD1DD),
    terminal_text=col(0x3A2A33),
    terminal_dim=col(0x7A5465),
    terminal_outline=False,
    button_tint=0.20,
)

# Turbo Vision: yellow on #0000A8, cyan window frames, the bright half of the
# CGA palette for everything that has to be seen. The inverse of CDE -- a very
# dark surface puts a *floor* under the accents instead of a ceiling, which is
# why nothing here is a muted colour. Untouched by the contrast floors; sixteen
# hard-coded colours chosen for a CRT turn out to clear WCAG AA comfortably.
TURBO = Palette(
    name="turbo",
    display_name="Turbo Pascal",
    is_dark=True,
    bg=col(0x000078),
    panel=col(0x0000A8),
    panel_alt=col(0x0000C8),
    well=col(0x0000A8),
    dock_well=col(0x303071),
    border=col(0x00A8A8),
    selection=col(0x00728C),
    text=col(0xFFFF54),
    text_dim=col(0xA8A8A8),
    hint=col(0xA8A8A8),
    dialog_hint=col(0xA8A8A8),
    text_strong=col(0xFFFFFF),
    accent=col(0x54FCFC),
    focus=col(0xFF54FF),
    warn=col(0xFFFF54),
    danger=col(0xFF5454),
    ok=col(0x54FC54),
    state_spawning=col(0xA8A8A8),
    state_thinking=col(0x54FCFC),
    state_calling_tool=col(0x54A8FC),
    state_awaiting=col(0xFFFF54),
    state_running=col(0x54FC54),
    state_awaiting_input=col(0x54A8FC),
    state_supervising=col(0xD79FFF),
    state_done=col(0x8A8A8A),
    state_failed=col(0xFF5454),
    state_cancelled=col(0x8A8A8A),
    state_rate_limited=col(0xFFA854),
    pressure_nominal=col(0x54A8FC),
    pressure_nearing=col(0xFFA854),
    pressure_compacted=col(0xFF5454),
    ring_track=col(0x5555AA),
    diff_add=col(0x54FC54),
    diff_remove=col(0xFF5454),
    diff_context=col(0xA8A8A8),
    terminal_bg=col(0x000030),
    terminal_text=col(0xFFFF54),
    terminal_dim=col(0xA8A8A8),
    terminal_outline=False,
    button_tint=0.20,
)

THEMES: dict[str, Palette] = {
    p.name: p for p in (DARK, LIGHT, HIGH_CONTRAST, GHOST, XTERM, CDE, WIN311, KITTY, TURBO)
}
# The required three, in the order the menu shows them. GHOST is discretionary and
# deliberately not in this list: the three below are the ones §6.1 makes mandatory.
REQUIRED_THEMES = ("dark", "light", "high_contrast")

_CURRENT: Palette = DARK


class _PaletteProxy:
    """
    Indirection so ``P.accent`` always resolves to the *current* palette.

    The obvious implementation -- a module-level ``P: Palette`` that ``set_theme``
    rebinds -- does not work. Every panel does ``from .theme import P``, which copies
    the object *reference* into that module's namespace at import time; rebinding
    ``theme.P`` afterwards updates theme's namespace and nothing else. The failure is
    partial and quiet: modules imported after the switch get the new palette, the
    rest keep the old one, and the UI ends up wearing two themes at once.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> object:
        return getattr(_CURRENT, name)


P: Palette = cast(Palette, _PaletteProxy())


def current() -> Palette:
    """The active palette as a concrete object, for code that needs to pass it on."""
    return _CURRENT


def set_theme(name: str) -> Palette:
    """
    Switch palettes. Unknown names fall back to dark rather than raising -- this is
    reachable from a hand-edited settings file, and a bad string there should cost a
    default theme, not a startup.
    """
    global _CURRENT
    _CURRENT = THEMES.get(name, DARK)
    return _CURRENT


def role_names() -> tuple[str, ...]:
    """Every colour role. Used by the tests that check palettes for completeness."""
    return tuple(f.name for f in fields(Palette) if f.type == "Color")


def relative_luminance(color: Color) -> float:
    """
    WCAG 2.1 relative luminance.

    Here so the contrast rules in §6.1 can be *tested* rather than asserted -- see
    tests/test_theme.py. Colour choices drift as themes get tweaked, and a claim
    about legibility that nothing checks is a claim that quietly stops being true.
    """

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in color.rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: Color, b: Color) -> float:
    """WCAG contrast ratio, 1.0 (identical) to 21.0 (black on white)."""
    la, lb = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def apply_style() -> None:
    """
    Push the active palette into ImGuiStyle.

    ImGuiStyle is global mutable state that persists across frames, unlike almost
    everything else in immediate mode. Set it on theme change, never per frame.

    Requires an ImGui context, so this must run from a hello_imgui callback rather
    than from main().
    """
    style = imgui.get_style()
    c = imgui.Col_

    # The operator's pick (planning/2026-10-15-ui-style-probe.md): flat frames
    # at 9x6 with rounding 5, popups with no outline at rounding 4, and soft's
    # scrollbars, grabs, spacing and tab rounding, on which every sheet the pick
    # was made from was drawn.
    style.window_rounding = 0.0
    style.child_rounding = 0.0
    style.frame_rounding = 5.0
    style.grab_rounding = 5.0
    style.tab_rounding = 5.0
    style.popup_rounding = 4.0
    style.scrollbar_rounding = 8.0
    style.window_border_size = 1.0
    # High contrast leans on borders to separate regions that the other themes
    # separate by surface lightness -- with panel and bg both pure black, a
    # hairline border is the only thing that makes a panel edge visible. So it
    # alone keeps the frames' and popups' outline (its popup fill is 1.1:1 on
    # black); elsewhere a field is told from its pane by ``dock_well``, a button
    # by its tint, and a popup by its fill.
    high_contrast = _CURRENT.name == "high_contrast"
    style.popup_border_size = 1.0 if high_contrast else 0.0
    style.frame_border_size = 1.0 if high_contrast else 0.0
    style.child_border_size = 2.0 if high_contrast else 1.0
    style.window_padding = imgui.ImVec2(8, 8)
    style.frame_padding = imgui.ImVec2(9, 6)
    style.item_spacing = imgui.ImVec2(8, 6)
    style.cell_padding = imgui.ImVec2(6, 3)
    style.scrollbar_size = 16.0
    style.scrollbar_padding = 3.0
    style.grab_min_size = 10.0

    def dim(color: Color, alpha: float) -> imgui.ImVec4:
        v = color.vec4
        return imgui.ImVec4(v.x, v.y, v.z, alpha)

    s = style.set_color_
    s(c.text, P.text.vec4)
    s(c.text_disabled, P.text_dim.vec4)
    s(c.window_bg, P.bg.vec4)
    s(c.child_bg, P.panel.vec4)
    s(c.popup_bg, P.panel_alt.vec4)
    # A modal blocks every pane behind it, so it has to look like it does: a window
    # that swallows clicks while looking like an ordinary floating one reads as the
    # application having locked up.
    #
    # Black rather than a palette role, and the one colour here that is not. A dim
    # has to darken in every theme, and any role taken from the palette is the page
    # colour -- which on `light` and `high_contrast` would wash the background out
    # instead of pushing it back.
    s(c.modal_window_dim_bg, imgui.ImVec4(0, 0, 0, 0.45))
    s(c.border, P.border.vec4)
    s(c.border_shadow, imgui.ImVec4(0, 0, 0, 0))
    s(c.frame_bg, P.dock_well.vec4)
    s(c.frame_bg_hovered, dim(P.accent, 0.18))
    s(c.frame_bg_active, dim(P.accent, 0.28))
    s(c.title_bg, P.panel.vec4)
    s(c.title_bg_active, P.panel_alt.vec4)
    s(c.title_bg_collapsed, P.panel.vec4)
    s(c.menu_bar_bg, P.panel.vec4)
    track, grab = scrollbar_alphas()
    s(c.scrollbar_bg, dim(P.text, track))
    s(c.scrollbar_grab, dim(P.text_dim, grab))
    s(c.scrollbar_grab_hovered, dim(P.accent, 0.5))
    s(c.scrollbar_grab_active, P.accent.vec4)
    s(c.check_mark, P.accent.vec4)
    # ``imgui.text_link``'s ink, where anything calls it; ``widgets.link`` is what
    # pptmstr draws a link with, and takes its states from ``link_inks``.
    s(c.text_link, link_inks().rest.vec4)
    s(c.slider_grab, P.accent.vec4)
    s(c.slider_grab_active, P.focus.vec4)
    # Opaque and tinted, so a button reads as one with no outline. The same slot
    # is a combo's arrow.
    tint = P.button_tint
    s(c.button, mixed(P.panel_alt, P.accent, tint))
    s(c.button_hovered, mixed(P.panel_alt, P.accent, tint + _BUTTON_STEP))
    s(c.button_active, mixed(P.panel_alt, P.accent, tint + 2.0 * _BUTTON_STEP))
    s(c.header, P.selection.vec4)
    s(c.header_hovered, dim(P.accent, 0.28))
    s(c.header_active, dim(P.focus, 0.35))
    s(c.separator, P.border.vec4)
    s(c.separator_hovered, P.accent.vec4)
    s(c.separator_active, P.focus.vec4)
    s(c.resize_grip, dim(P.border, 0.6))
    s(c.resize_grip_hovered, P.accent.vec4)
    s(c.resize_grip_active, P.focus.vec4)
    s(c.tab, P.panel.vec4)
    s(c.tab_hovered, dim(P.accent, 0.3))
    s(c.tab_selected, P.panel_alt.vec4)
    s(c.tab_dimmed, P.panel.vec4)
    s(c.tab_dimmed_selected, P.panel_alt.vec4)
    s(c.table_header_bg, P.panel_alt.vec4)
    s(c.table_border_strong, P.border.vec4)
    s(c.table_border_light, dim(P.border, 0.5))
    s(c.table_row_bg, imgui.ImVec4(0, 0, 0, 0))
    s(c.table_row_bg_alt, dim(P.panel_alt, 0.35))
    s(c.text_selected_bg, dim(P.focus, 0.35))
    s(c.nav_cursor, P.focus.vec4)
    s(c.docking_preview, dim(P.accent, 0.5))
    s(c.docking_empty_bg, P.bg.vec4)


# How far a well moves toward the accent under the pointer, and while it is held.
# The fractions ``apply_style`` gives the docks' frames, which are an accent drawn
# translucent over whatever is behind the frame. A well's are mixed here instead
# and drawn opaque: behind a well is the modal's surface and not the well, so a
# translucent accent there would be a tint of the surface, and a field would lose
# its fill at the moment it is pointed at.
_WELL_HOVERED = 0.18
_WELL_ACTIVE = 0.28
# How much further toward the accent a button is under the pointer, and again
# while it is held, than ``Palette.button_tint`` puts it at rest.
_BUTTON_STEP = 0.16

_DIALOG_COLORS: dict[str, tuple[tuple[int, imgui.ImVec4], ...]] = {}


def mixed(base: Color, over: Color, alpha: float) -> imgui.ImVec4:
    """
    What ``over`` at ``alpha`` shows as when it is drawn on ``base``, opaque.
    """
    b, o = base.vec4, over.vec4
    return imgui.ImVec4(
        b.x + (o.x - b.x) * alpha,
        b.y + (o.y - b.y) * alpha,
        b.z + (o.z - b.z) * alpha,
        1.0,
    )


@contextmanager
def hint_colour(gui: ModuleType, *, dialog: bool) -> Iterator[None]:
    """
    Draw what is inside with a field's hint in ``P.hint``, or ``P.dialog_hint``
    for a field in a modal (``dialog``). ImGui 1.92 has no hint slot:
    ``input_text_with_hint`` draws its placeholder in ``Col_.text_disabled``,
    which is ``text_dim`` everywhere else, so only the field is drawn under this.

    ``dialog`` is said at the call and not read from the style in force: a test
    that fakes a pane's ``imgui`` has no style to read, and which well a field
    stands in is a fact about where it is drawn, which its caller knows.

    ``gui`` is the caller's own ``imgui`` name, so the push goes where the field's
    draw goes, and a test that fakes a pane's ``imgui`` records it with the rest.
    """
    gui.push_style_color(gui.Col_.text_disabled, (P.dialog_hint if dialog else P.hint).vec4)
    try:
        yield
    finally:
        gui.pop_style_color()


TAB_INK_FLOOR = 4.5
"""What an unselected pane tab's label reads at on the tab's fill, ``panel``."""

_TAB_INK_STEPS = 100
_TAB_INKS: dict[tuple[int, int, int], Color] = {}


def tab_ink(palette: Palette | None = None) -> Color:
    """
    The label of an unselected pane tab (``ui.tabs``) on ``palette`` (the live one
    by default): ``text_dim`` where it reads at ``TAB_INK_FLOOR`` on ``panel``,
    which ``Col_.tab`` is, and otherwise the smallest step of it toward ``text``
    that does, so it stays as far from the selected tab's ``text`` as the floor
    lets it. Not ``text_disabled``: that is ``text_dim`` at a hundred call sites,
    held apart from body text, and 4.45:1 on dark's panel and 4.04 on ghost's.
    """
    used = current() if palette is None else palette
    ground, text = used.panel, used.text
    key = (used.text_dim.u32, ground.u32, text.u32)
    hit = _TAB_INKS.get(key)
    if hit is None:
        hit = text
        for step in range(_TAB_INK_STEPS + 1):
            tried = _opaque8(mixed(used.text_dim, text, step / _TAB_INK_STEPS))
            if contrast_ratio(tried, ground) >= TAB_INK_FLOOR:
                hit = tried
                break
        _TAB_INKS[key] = hit
    return hit


TERMINAL_FLOOR = 4.5
"""
What every ink a tool block draws text in reads at on ``terminal_bg``: WCAG AA,
which is what the operator's sign-off asked of a block's text on every theme.
"""

_TERMINAL_STEPS = 50
_TERMINAL_INKS: dict[tuple[int, int, int], Color] = {}


def terminal_ink(ink: Color, palette: Palette | None = None) -> Color:
    """
    ``ink`` as a tool block draws text in it on ``palette``'s terminal (the live
    one by default): the role's own colour where it reads at ``TERMINAL_FLOOR`` on
    the ground, and otherwise the smallest step of it toward ``terminal_text``
    that does. A step keeps the role's hue as far as the floor lets it -- kitty's
    accent goes about a third of the way, xterm's warn a fifth -- where a swap to
    the text colour would make every role one.

    Memoised by the three colours, so a theme switch needs nothing cleared and a
    frame pays a dict lookup.
    """
    used = current() if palette is None else palette
    ground, text = used.terminal_bg, used.terminal_text
    key = (ink.u32, ground.u32, text.u32)
    hit = _TERMINAL_INKS.get(key)
    if hit is None:
        hit = text
        for step in range(_TERMINAL_STEPS + 1):
            tried = _opaque8(mixed(ink, text, step / _TERMINAL_STEPS))
            if contrast_ratio(tried, ground) >= TERMINAL_FLOOR:
                hit = tried
                break
        _TERMINAL_INKS[key] = hit
    return hit


SCROLLBAR_TRACK_FLOOR = 1.2
"""
What a scrollbar's track reads at against every surface a bar stands on: enough
to be seen as a track of its own, where the ground's own colour (``bg``) cut into
a child's ``panel`` read as the pane stopping short of its edge.
"""

SCROLLBAR_GRAB_FLOOR = 1.9
"""
What the grab reads at against the track it is drawn over on every surface, and
against the surface itself where a modal draws no track.
"""

_SCROLLBAR_STEPS = 100
_SCROLLBAR_ALPHAS: dict[str, tuple[float, float]] = {}


def scrollbar_surfaces(palette: Palette) -> tuple[Color, ...]:
    """
    Every colour a scrollbar is drawn over: a dock's ground, a child's fill, a
    popup's, a text box's in a dock and in a modal, and a tool block's terminal.
    """
    return (
        palette.bg,
        palette.panel,
        palette.panel_alt,
        palette.dock_well,
        palette.well,
        palette.terminal_bg,
    )


def scrollbar_alphas(palette: Palette | None = None) -> tuple[float, float]:
    """
    The alphas of ``palette``'s scrollbar track (``text`` over the surface) and grab
    (``text_dim`` over the track, or over the surface where there is no track),
    each the smallest step that clears its floor on every surface
    (``scrollbar_surfaces``).

    Translucent and not a colour, because the bar stands on six surfaces and no
    one opaque track is a step off all of them: ``bg`` is the dock's ground
    itself, and 1.07:1 off dark's ``panel``. ImGui draws the track over the
    window's fill and the grab over the track, which is what ``mixed`` computes,
    so what is measured here is what is drawn. Memoised by palette name.
    """
    used = current() if palette is None else palette
    hit = _SCROLLBAR_ALPHAS.get(used.name)
    if hit is None:

        def least(over: Color, grounds: tuple[Color, ...], floor: float) -> float:
            for step in range(_SCROLLBAR_STEPS + 1):
                alpha = step / _SCROLLBAR_STEPS
                if all(
                    contrast_ratio(_opaque8(mixed(ground, over, alpha)), ground) >= floor
                    for ground in grounds
                ):
                    return alpha
            return 1.0

        surfaces = scrollbar_surfaces(used)
        track = least(used.text, surfaces, SCROLLBAR_TRACK_FLOOR)
        tracks = tuple(_opaque8(mixed(ground, used.text, track)) for ground in surfaces)
        # And over the bare surfaces: a modal draws no track (``dialog_colors``).
        hit = (track, least(used.text_dim, tracks + surfaces, SCROLLBAR_GRAB_FLOOR))
        _SCROLLBAR_ALPHAS[used.name] = hit
    return hit


LINK_FLOOR = 4.5
"""What a link's text reads at on its surface, in every state (``link_inks``)."""

_LINK_RISE = 1.15
_LINK_STEPS = 1000


@dataclass(frozen=True, slots=True)
class LinkInks:
    """
    A link's text in each of its states (``widgets.link``). The underline is the
    text's own ink; a disabled link has none.
    """

    rest: Color
    hovered: Color
    pressed: Color
    disabled: Color


_LINK_INKS: dict[tuple[int, ...], LinkInks] = {}


def _toward_text(ink: Color, text: Color, grounds: tuple[Color, ...], floor: float) -> Color:
    """``ink``, or the least step of it toward ``text`` that reads at ``floor`` on every ground."""
    for step in range(_LINK_STEPS + 1):
        tried = _opaque8(mixed(ink, text, step / _LINK_STEPS))
        if min(contrast_ratio(tried, ground) for ground in grounds) >= floor:
            return tried
    return text


def link_inks(
    ink: Color | None = None,
    grounds: tuple[Color, ...] | None = None,
    text: Color | None = None,
    dim: Color | None = None,
) -> LinkInks:
    """
    A link's inks on ``grounds``: by default the accent on the surfaces a pane's
    link stands on (a dock's ``bg``, a child's ``panel``, a popup's ``panel_alt``),
    moving toward the pane's ``text``; a tool block passes its own (``tool_block``).

    - **rest**: ``ink``, or the least step of it toward ``text`` that reads at
      ``LINK_FLOOR`` on every ground;
    - **hovered** and **pressed**: each the least further step toward ``text``
      that is ``_LINK_RISE`` times the one before off the ground, so the state
      moves away from the surface and never toward it, as ``imgui.text_link``'s
      brightening did on a light one (5.15:1 to 3.32 and 2.85 on light). Where
      ``text`` is reached first, the state is ``text``;
    - **disabled**: ``dim``, or the least step of it toward ``text`` at the floor.
      It is told from the others by its ink and by having no underline.

    Memoised by the colours it is made from, so a theme switch needs nothing
    cleared.
    """
    used = current()
    ink = used.accent if ink is None else ink
    grounds = (used.bg, used.panel, used.panel_alt) if grounds is None else grounds
    text = used.text if text is None else text
    dim = used.text_dim if dim is None else dim
    key = (ink.u32, text.u32, dim.u32, *(ground.u32 for ground in grounds))
    hit = _LINK_INKS.get(key)
    if hit is None:

        def off(colour: Color) -> float:
            return min(contrast_ratio(colour, ground) for ground in grounds)

        rest = _toward_text(ink, text, grounds, LINK_FLOOR)
        hovered = _toward_text(rest, text, grounds, off(rest) * _LINK_RISE)
        pressed = _toward_text(hovered, text, grounds, off(hovered) * _LINK_RISE)
        disabled = _toward_text(dim, text, grounds, LINK_FLOOR)
        hit = _LINK_INKS[key] = LinkInks(rest, hovered, pressed, disabled)
    return hit


def _opaque8(colour: imgui.ImVec4) -> Color:
    """An opaque ``ImVec4`` as the 8-bit ``Color`` a palette literal would hold."""
    r, g, b = (round(channel * 255.0) for channel in (colour.x, colour.y, colour.z))
    return col((r << 16) | (g << 8) | b)


def dialog_colors() -> tuple[tuple[int, imgui.ImVec4], ...]:
    """
    The colours a modal form is drawn under where they are not the docks', as
    ``(slot, colour)`` to push: one surface, and wells in a second colour.

    A child window is given no fill, and nor is the track of a scrollbar, so what
    shows through both is the popup they are in and a scrolling body is not a
    second surface. Every frame -- a text box, a
    combo, the field of a stepper -- is a well. A button is not a frame and is
    left as the docks draw it: the surface's own colour inside a border, which is
    what tells it from a well, and the same in every modal because the surface
    is.

    Built once per palette and not per frame, for the reason at the top of this
    module.
    """
    palette = _CURRENT
    found = _DIALOG_COLORS.get(palette.name)
    if found is None:
        c = imgui.Col_
        found = (
            (int(c.child_bg), imgui.ImVec4(0.0, 0.0, 0.0, 0.0)),
            (int(c.scrollbar_bg), imgui.ImVec4(0.0, 0.0, 0.0, 0.0)),
            (int(c.frame_bg), palette.well.vec4),
            (int(c.frame_bg_hovered), mixed(palette.well, palette.accent, _WELL_HOVERED)),
            (int(c.frame_bg_active), mixed(palette.well, palette.accent, _WELL_ACTIVE)),
        )
        _DIALOG_COLORS[palette.name] = found
    return found


class Face(enum.Enum):
    """
    The faces a renderer can ask for by name.

    There is no ITALIC, and there cannot be one: Inconsolata has no italic upstream
    -- not "not bundled", it does not exist -- and ImGui will not synthesise an
    oblique. Emphasis is therefore a colour shift, which is the terminal convention
    (planning/archive/2026-08-10-transcript-markdown.md).
    """

    BODY = "body"
    BOLD = "bold"


# Only one size is loaded per face. Since ImGui 1.92 a face rasterises on demand at
# whatever size is pushed, so a second ImFont at another size buys nothing.
_FONT_SIZE = 16.0
# Inside the package, not at the repo root: the wheel ships ``packages = ["pptmstr"]``,
# so a root-level assets folder would be absent from an installed copy and the bold
# face would silently degrade for everyone who did not clone the repo.
_ASSETS = Path(__file__).resolve().parent / "assets"
_FACES: dict[Face, imgui.ImFont | None] = {}


def face(which: Face) -> imgui.ImFont | None:
    """
    The loaded face, or ``None`` when it is not available.

    ``None`` is a usable answer rather than an error: ``push_font(None, size)`` means
    "keep the current face, change the size", so a caller pushing a missing face gets
    body text at the right size instead of a crash. Faces therefore degrade one at a
    time, and a renderer works with none of them loaded.

    Callers must ``pop_font()`` before running anything that pushes a size with a
    ``None`` face -- the small-text helpers in ``rail``, ``health`` and ``inbox`` all
    do -- or that text inherits whichever face is still live.
    """
    return _FACES.get(which)


def load_fonts() -> None:
    """
    Load the UI font merged with FontAwesome glyphs, plus the bold face.

    Called from a hello_imgui callback, not from main(): fonts must be built after
    the backend exists but before the first frame, and the runner owns that window.
    hello_imgui applies the DPI factor, which is why nothing here hard-codes 2x.

    A missing asset must not be fatal -- ImGui's built-in font is ugly but renders.
    It has no FontAwesome glyphs, though, so STATE_GLYPH degrades to tofu while
    STATE_LABEL keeps carrying the meaning. That is the redundancy earning itself.
    """
    from imgui_bundle import hello_imgui

    # Inconsolata-Bold is vendored; Medium and FontAwesome come from the imgui-bundle
    # wheel. Search paths are additive and consulted after the main folder, so this
    # reaches the vendored face without shadowing anything the bundle ships.
    hello_imgui.add_assets_search_path(str(_ASSETS))

    # STATE_GLYPH uses FontAwesome 6 codepoints, and this merges FontAwesome 4
    # unless told otherwise. FA4 stops around U+F2E0, so ICON_FA_BRAIN (U+F5DC)
    # and ICON_FA_COMMENT_DOTS (U+F4AD) render as tofu without this.
    hello_imgui.get_runner_params().callbacks.default_icon_font = (
        hello_imgui.DefaultIconFont.font_awesome6
    )

    # Fonts[0] is the application default at NewFrame, so whichever face loads first
    # becomes the UI font everywhere. Inconsolata must stay first: adding any load
    # above this line silently reskins the whole application.
    _FACES[Face.BODY] = _load_face("fonts/Inconsolata-Medium.ttf", icons=True)
    # No icons merged into bold: FontAwesome codepoints only ever reach the UI
    # through STATE_GLYPH, which draws in the body face.
    _FACES[Face.BOLD] = _load_face("fonts/Inconsolata-Bold.ttf", icons=False)
    # Only behind a UI font that loaded. With none, the first of these would be
    # Fonts[0] and every widget in the application would be set in Roboto.
    if _FACES[Face.BODY] is not None:
        _load_typeset_faces()


def _load_typeset_faces() -> None:
    """
    Give the text library its faces, after the two above.

    After, and last in hello_imgui's ``load_additional_fonts``, because of the
    rule in ``load_fonts``: the library puts a proportional face and a second copy
    of Inconsolata without the icons on the atlas, and either one as Fonts[0]
    would become the font of every widget. Whatever ``Settings.typeset`` says:
    ``ui.typeset.gate`` has the measurement.

    Imported here rather than at the top: ``ui.typeset`` imports this module for
    its colours. ``typeset.load_fonts`` answers false for a library that is
    absent, refused or failing and keeps the sentence for
    ``typeset.unavailable_reason()``; it is not meant to raise. This is
    hello_imgui's callback all the same, where an exception that escapes takes
    the application down, so one that does is handed to the gate as the reason:
    the same non-fatal degradation as a missing face above, and said.
    """
    from .ui import typeset

    try:
        typeset.load_fonts(_ASSETS / "fonts" / "Inconsolata-Bold.ttf")
    except Exception as failure:
        typeset.gate.failed(failure)


def _load_face(asset: str, *, icons: bool) -> imgui.ImFont | None:
    from imgui_bundle import hello_imgui

    try:
        if icons:
            return hello_imgui.load_font_ttf_with_font_awesome_icons(asset, _FONT_SIZE)
        return hello_imgui.load_font_ttf(asset, _FONT_SIZE)
    except Exception:
        return None


_NO_DENSITY = 0.0
"""
A rasteriser density no baked size has (ImGui asserts one is positive): asked for
with it, the closest-match lookup has no size of "the same density" to prefer and
answers among all of them.
"""


@dataclass(slots=True)
class _Kept:
    """
    What the baked sizes on the atlas were drawn under, as ``keep_baked_sizes`` last saw it.
    """

    under: object | None = None


_KEPT = _Kept()


def baked_sizes(atlas: imgui.ImFontAtlas, font: imgui.ImFont) -> list[tuple[float, float]]:
    """
    The baked sizes the atlas holds for ``font``, as (pixel height, rasteriser
    density), largest first, and none made or marked used.

    ``ImFont.get_font_baked`` makes the size it is asked for and marks it used,
    so it cannot be what finds them; the closest-match lookup does neither, and
    is the only way the bindings have into ImGui's pool of them. Asked for just
    under a size that exists it answers the next one down, or one that is not
    smaller when there is none.

    **One answer a pixel height.** Two sizes equal but for their density are
    answered as the one made first: the lookup has no way to be asked for the
    other without knowing its density. ``keep_baked_sizes`` lets go of every
    size when the display's density changes, which is what would make such a
    pair.
    """
    closest = imgui.internal.im_font_atlas_baked_get_closest_match
    out: list[tuple[float, float]] = []
    ask = 1.0e6
    while True:
        baked = closest(atlas, font, ask, _NO_DENSITY)
        if baked is None or float(baked.size) >= ask:
            return out
        out.append((float(baked.size), float(baked.rasterizer_density)))
        ask = out[-1][0] - 0.01


def keep_baked_sizes(generation: object = None) -> int:
    """
    Mark every baked size on the atlas used, so that ImGui discards none. Call
    it before each ``new_frame``. How many were marked.

    **Why.** Dear ImGui rasterises a glyph the first time it is drawn at a size,
    and when one does not fit the atlas texture it discards every baked size no
    text used in the last two frames and, at the next frame, closes the gaps in
    its pool of them by moving the ones behind a gap down. Its FreeType loader
    remembers the size it last made current for a font file by that size's
    address in the pool, and skips making a size current when the address is
    the same. A size moved to the address the loader remembers is then
    rasterised at the scale of the size that was there: its new glyphs have the
    bitmap and the advance of another size, in this size's cells, for as long
    as the size lives. Seen as letters of another size inside a heading; any
    font drawn at two sizes can have it, the chrome's included
    (``tests/test_baked_sizes.py`` makes it happen, and
    ``scripts/measure_heading_glyphs.py`` reads it in a window). Nothing is
    moved while nothing is discarded, and ImGui discards only what was not
    used, so every size is marked used: ``ImFont.get_font_baked`` of a size
    that exists finds it and does that.

    **The set is the atlas's own**, read each frame (``baked_sizes``), and not a
    list kept beside it: every font ``io.fonts`` has, the typesetting library's
    among them, at every size anything drew or measured at, whoever it was. So
    no reference to a font is held from one frame to the next, and a font the
    library left behind when it loaded its faces again is on the list like any
    other and is not touched once it is off it.

    **What bounds it.** Kept for ever, the sizes of every text scale the
    operator tried would stay on the texture. ``generation`` is whatever the
    faces and sizes of text follow (``typeset.gate.epoch``), and the UI font's
    size and the display's density are read beside it: when any of them is not
    what it was at the last call, every baked size of every font is discarded
    here, in one go, and the frame that follows rasterises what it draws. That
    is safe where ImGui's own discard is not: a size made afterwards is at an
    address the loader was told when it was made, and none is moved after
    that. So the atlas holds the sizes drawn since the last change and no
    others, and the fonts of a family no longer in use hold none. The first
    call discards nothing: the sizes the font load made are the ones the first
    frames draw.

    Does nothing before the first frame, when the atlas has not been built, and
    with no ImGui context.
    """
    if imgui.get_current_context() is None or imgui.get_frame_count() < 1:
        return 0
    io = imgui.get_io()
    atlas = io.fonts
    display = (float(io.display_framebuffer_scale.x), float(io.display_framebuffer_scale.y))
    under = (generation, float(imgui.get_font_size()), display)
    stale = _KEPT.under is not None and _KEPT.under != under
    _KEPT.under = under
    if stale:
        for font in atlas.fonts:
            imgui.internal.im_font_atlas_font_discard_bakes(atlas, font, 0)
        return 0
    kept = 0
    for font in atlas.fonts:
        for size, density in baked_sizes(atlas, font):
            font.get_font_baked(size, density)
            kept += 1
    return kept
