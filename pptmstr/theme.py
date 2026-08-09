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

from dataclasses import dataclass, fields
from typing import cast

from imgui_bundle import icons_fontawesome_6 as fa
from imgui_bundle import imgui

from .model import AgentState, ContextPressure


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
    border: Color
    selection: Color

    # text
    text: Color
    text_dim: Color
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

    def state(self, state: AgentState) -> Color:
        return cast(Color, getattr(self, _STATE_ROLE[state]))

    def pressure(self, pressure: ContextPressure) -> Color:
        return cast(Color, getattr(self, _PRESSURE_ROLE[pressure]))


_STATE_ROLE: dict[AgentState, str] = {
    AgentState.SPAWNING: "state_spawning",
    AgentState.THINKING: "state_thinking",
    AgentState.CALLING_TOOL: "state_calling_tool",
    AgentState.AWAITING_APPROVAL: "state_awaiting",
    AgentState.RUNNING_TOOL: "state_running",
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
    AgentState.DONE: fa.ICON_FA_CHECK,
    AgentState.FAILED: fa.ICON_FA_TRIANGLE_EXCLAMATION,
    AgentState.CANCELLED: fa.ICON_FA_BAN,
    AgentState.RATE_LIMITED: fa.ICON_FA_CLOCK,
}

STATE_LABEL: dict[AgentState, str] = {
    AgentState.SPAWNING: "spawning",
    AgentState.THINKING: "thinking",
    AgentState.CALLING_TOOL: "calling",
    AgentState.AWAITING_APPROVAL: "REVIEW",
    AgentState.RUNNING_TOOL: "running",
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
    border=col(0x323848),
    selection=col(0x2B3550),
    text=col(0xC9CEDC),
    text_dim=col(0x7C8397),
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
)

LIGHT = Palette(
    name="light",
    display_name="Light",
    is_dark=False,
    bg=col(0xF4F5F8),
    panel=col(0xFFFFFF),
    panel_alt=col(0xEBEDF2),
    border=col(0xC4C9D4),
    selection=col(0xD3E2F7),
    text=col(0x1F232C),
    text_dim=col(0x5E6675),
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
    border=col(0xFFFFFF),
    selection=col(0x0050B3),
    text=col(0xFFFFFF),
    text_dim=col(0xC0C0C0),
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
    border=col(0x1F3E49),
    selection=col(0x12333D),
    text=col(0xA8D8D0),
    text_dim=col(0x5A8079),
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
)

THEMES: dict[str, Palette] = {p.name: p for p in (DARK, LIGHT, HIGH_CONTRAST, GHOST)}
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

    style.window_rounding = 0.0
    style.child_rounding = 0.0
    style.frame_rounding = 2.0
    style.grab_rounding = 2.0
    style.tab_rounding = 0.0
    style.scrollbar_rounding = 0.0
    style.window_border_size = 1.0
    style.frame_border_size = 1.0
    # High contrast leans on borders to separate regions that the other themes
    # separate by surface lightness -- with panel and bg both pure black, a
    # hairline border is the only thing that makes a panel edge visible.
    style.child_border_size = 2.0 if _CURRENT.name == "high_contrast" else 1.0
    style.window_padding = imgui.ImVec2(8, 8)
    style.frame_padding = imgui.ImVec2(7, 4)
    style.item_spacing = imgui.ImVec2(8, 5)
    style.cell_padding = imgui.ImVec2(6, 3)
    style.scrollbar_size = 12.0
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
    s(c.border, P.border.vec4)
    s(c.border_shadow, imgui.ImVec4(0, 0, 0, 0))
    s(c.frame_bg, P.panel_alt.vec4)
    s(c.frame_bg_hovered, dim(P.accent, 0.18))
    s(c.frame_bg_active, dim(P.accent, 0.28))
    s(c.title_bg, P.panel.vec4)
    s(c.title_bg_active, P.panel_alt.vec4)
    s(c.title_bg_collapsed, P.panel.vec4)
    s(c.menu_bar_bg, P.panel.vec4)
    s(c.scrollbar_bg, P.bg.vec4)
    s(c.scrollbar_grab, P.border.vec4)
    s(c.scrollbar_grab_hovered, dim(P.accent, 0.5))
    s(c.scrollbar_grab_active, P.accent.vec4)
    s(c.check_mark, P.accent.vec4)
    s(c.slider_grab, P.accent.vec4)
    s(c.slider_grab_active, P.focus.vec4)
    s(c.button, P.panel_alt.vec4)
    s(c.button_hovered, dim(P.accent, 0.28))
    s(c.button_active, dim(P.accent, 0.45))
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


FONT_MONO: imgui.ImFont | None = None
FONT_SMALL: imgui.ImFont | None = None


def load_fonts() -> None:
    """
    Load the UI font merged with FontAwesome glyphs.

    Called from a hello_imgui callback, not from main(): fonts must be built after
    the backend exists but before the first frame, and the runner owns that window.
    hello_imgui applies the DPI factor, which is why nothing here hard-codes 2x.

    A missing asset must not be fatal -- ImGui's built-in font is ugly but renders.
    It has no FontAwesome glyphs, though, so STATE_GLYPH degrades to tofu while
    STATE_LABEL keeps carrying the meaning. That is the redundancy earning itself.
    """
    global FONT_MONO, FONT_SMALL
    from imgui_bundle import hello_imgui

    try:
        FONT_MONO = hello_imgui.load_font_ttf_with_font_awesome_icons(
            "fonts/Inconsolata-Medium.ttf", 16.0
        )
        FONT_SMALL = hello_imgui.load_font_ttf_with_font_awesome_icons(
            "fonts/Inconsolata-Medium.ttf", 13.0
        )
    except Exception:
        FONT_MONO = FONT_SMALL = None
