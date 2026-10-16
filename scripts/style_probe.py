#!/usr/bin/env python3
"""
Render the app under a few look-and-feel variants, on the same screens, for the
operator to pick one (planning/2026-10-15-ui-rework.md U9, U10).

Nothing here changes what the app draws. A variant is a set of ImGuiStyle
overrides laid over what ``theme.apply_style`` set, and it exists only in this
process: the probe wraps ``theme.apply_style`` at runtime so the variant lands
after it every time the app pushes its palette, and wraps ``app._draw_overlays``
to add the two drawings no style value can produce -- the inset tab shape and
the terminal-style tool block. ``theme.py`` and every module under ``pptmstr/``
are left as they are.

    scripts/style_probe.py --all                 # every variant, each on its own Xvfb
    scripts/style_probe.py --variant soft        # one variant, on the display there is

``--all`` runs ``scripts/xvfb_shot.sh -- <this script> --variant NAME`` once per
variant, so it works headless. Each run drives ``scripts/screenshot.py``'s
machinery -- its frame grab, its scratch configuration, its scenario loop -- with
one scenario registered here, and writes ``shots/style-probe/<variant>/<screen>.png``.

The variant table is data so that a test can hold it to its shape and so that the
planning record can quote exact values: ``VARIANTS``, ``apply`` and ``revert``
are what ``tests/test_style_probe.py`` exercises against a bare ImGui context.

**The terminal block is a mock-up.** It draws the intended look of U1/U5's tool
block from fixed text; nothing in it is read from a transcript.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import functools
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = ROOT / "shots" / "style-probe"
SCENARIO = "style-probe"

# The screens every variant is shot on, in the order the scenario reaches them.
SCREENS = ("triage", "board", "focus", "terminal-block", "settings", "launcher")


@dataclass(frozen=True, slots=True)
class Mix:
    """
    A colour as ``theme.mixed`` makes one: ``over`` at ``alpha`` on ``base``,
    opaque, both palette roles. Read from the palette in force when applied, so a
    variant follows a theme switch.
    """

    base: str
    over: str
    alpha: float

    def __str__(self) -> str:
        return f"mix({self.base}, {self.over}, {self.alpha:g})"


@dataclass(frozen=True, slots=True)
class Variant:
    """
    One look. ``scalars`` are ImGuiStyle attributes set as written (a pair is an
    ImVec2); ``colors`` are ``imgui.Col_`` slots by name. ``inset_tabs`` turns on
    the drawn tab shape, which no style value reaches (see ``draw_inset_tabs``).
    """

    name: str
    summary: str
    scalars: tuple[tuple[str, float | tuple[float, float]], ...] = ()
    colors: tuple[tuple[str, Mix], ...] = ()
    inset_tabs: bool = False
    # Round two's drawn-tab geometry; ``None`` with ``inset_tabs`` is round one's.
    tabs: TabSpec | None = None


@dataclass(frozen=True, slots=True)
class TabSpec:
    """
    A drawn tab's geometry, in pixels at the UI font: ``pad`` from the text to the
    tab's edge (x, and y above and below the text alike), ``gap`` between two
    tabs, ``inset`` from the lane's top and left edges to the first tab, and the
    text at ``scale`` times the UI font. A tab's foot is the pane's top edge.
    """

    pad: tuple[float, float]
    gap: float
    inset: float
    scale: float

    def lane(self, font_px: float) -> float:
        """The lane's height: the inset over one tab of text and its padding."""
        return self.inset + font_px * self.scale + 2.0 * self.pad[1]

    def label(self) -> str:
        return (
            f"pad {self.pad[0]:g}x{self.pad[1]:g} · gap {self.gap:g} · inset {self.inset:g}"
            f" · text {self.scale:.2f}×"
        )

    def on_dock(self, inner_x: float) -> tuple[float, float, float]:
        """
        What ``draw_inset_tabs`` draws on a dock, in pixels: tab to tab, lane's left
        edge to the first tab, lane's top edge to every tab. ImGui lays a dock's tab
        rectangles ``item_inner_spacing.x`` apart, and the drawn tab is its rectangle
        less ``gap / 2`` at each side, so the visible gap is ``gap + inner_x`` and the
        left inset is ``gap / 2``, whatever ``inset`` says; ``inset`` is the top's.
        """
        return self.gap + inner_x, self.gap * 0.5, self.inset

    def dock_label(self, inner_x: float) -> str:
        between, left, top = self.on_dock(inner_x)
        return (
            f"as drawn, on a dock: {between:g} px tab to tab (gap + item_inner_spacing.x"
            f" {inner_x:g}) · {left:g} px left of the first tab (gap/2) · {top:g} px above"
        )


CURRENT = Variant("current", "today's style, unmodified: the baseline")

TIDY = Variant(
    "tidy",
    "the smallest change that answers every note: same borders, a little more of everything",
    scalars=(
        ("scrollbar_size", 14.0),
        ("scrollbar_rounding", 7.0),
        ("scrollbar_padding", 2.0),
        ("grab_rounding", 3.0),
        ("frame_rounding", 3.0),
        # Tabs are frame-height in a dock node, so this is also what makes them
        # taller: 16 + 2*5 = 26 px against 24, +8 %.
        ("frame_padding", (8.0, 5.0)),
        ("tab_rounding", 3.0),
        ("popup_rounding", 3.0),
    ),
    colors=(
        # A button gets a fill of its own; the wells keep theirs. Borders stay.
        ("button", Mix("panel_alt", "accent", 0.16)),
        ("button_hovered", Mix("panel_alt", "accent", 0.32)),
        ("button_active", Mix("panel_alt", "accent", 0.48)),
    ),
)

SOFT = Variant(
    "soft",
    "flat rounded buttons, recessed wells, larger rounded scrollbars, inset lowercase tabs",
    scalars=(
        ("scrollbar_size", 16.0),
        ("scrollbar_rounding", 8.0),
        ("scrollbar_padding", 3.0),
        ("grab_rounding", 5.0),
        ("frame_rounding", 5.0),
        # 16 + 2*6 = 28 px against 24: +17 %, one pixel over the brief's 15 % and
        # the nearest whole-pixel padding to it.
        ("frame_padding", (9.0, 6.0)),
        ("frame_border_size", 0.0),
        ("item_spacing", (8.0, 6.0)),
        ("tab_rounding", 5.0),
        ("popup_rounding", 4.0),
    ),
    colors=(
        ("button", Mix("panel_alt", "accent", 0.20)),
        ("button_hovered", Mix("panel_alt", "accent", 0.36)),
        ("button_active", Mix("panel_alt", "accent", 0.52)),
        # With no border, a well is told from a button by being darker than the
        # surface it sits on, and a button by being lighter.
        ("frame_bg", Mix("bg", "panel", 0.35)),
        ("scrollbar_grab", Mix("bg", "text_dim", 0.45)),
    ),
    inset_tabs=True,
)

VARIANTS: tuple[Variant, ...] = (CURRENT, TIDY, SOFT)
BY_NAME = {v.name: v for v in VARIANTS}

# What ``apply`` returns and ``revert`` takes: every attribute and slot it wrote,
# with the value it held before.
Saved = tuple[
    tuple[tuple[str, float | tuple[float, float]], ...],
    tuple[tuple[int, tuple[float, float, float, float]], ...],
]


def _read(value: Any) -> float | tuple[float, float]:
    if hasattr(value, "x"):
        return (float(value.x), float(value.y))
    return float(value)


# The two colours a mix may name that are not palette roles: a well darker than a
# near-black pane has nowhere to go but toward black.
FIXED = {"black": 0x000000, "white": 0xFFFFFF}


def color_of(mix: Mix) -> Any:
    from pptmstr import theme

    palette = theme.current()

    def role(name: str) -> Any:
        return theme.col(FIXED[name]) if name in FIXED else getattr(palette, name)

    return theme.mixed(role(mix.base), role(mix.over), mix.alpha)


def apply(variant: Variant) -> Saved:
    """
    Lay ``variant`` over the style as it stands, and return what it replaced.
    Needs an ImGui context; no frame.
    """
    from imgui_bundle import imgui

    style = imgui.get_style()
    scalars = []
    for name, value in variant.scalars:
        scalars.append((name, _read(getattr(style, name))))
        setattr(style, name, imgui.ImVec2(*value) if isinstance(value, tuple) else value)
    colors = []
    for slot, mix in variant.colors:
        idx = int(getattr(imgui.Col_, slot))
        was = style.color_(idx)
        colors.append((idx, (was.x, was.y, was.z, was.w)))
        style.set_color_(idx, color_of(mix))
    return tuple(scalars), tuple(colors)


def revert(saved: Saved) -> None:
    """Put back what ``apply`` replaced, in reverse so a repeated name ends as it began."""
    from imgui_bundle import imgui

    style = imgui.get_style()
    scalars, colors = saved
    for name, value in reversed(scalars):
        setattr(style, name, imgui.ImVec2(*value) if isinstance(value, tuple) else value)
    for idx, rgba in reversed(colors):
        style.set_color_(idx, imgui.ImVec4(*rgba))


# -- the drawings no style value reaches ------------------------------------------


def _label(window: Any) -> str:
    """What a tab shows for ``window``: its name up to an ``##``, lowercased."""
    name = str(window.name)
    return name.split("##", 1)[0].lower()


def draw_inset_tabs(spec: TabSpec | None = None) -> None:
    """
    Every dock node's tab bar drawn again over itself: the lane flat, each tab a
    rounded rectangle inset into it on all four sides, its label lowercase and
    centred between equal top and bottom margins.

    ``spec`` is round two's: its gap, its top inset and its text size, the tab's
    bottom edge on the pane's top edge so the selected tab joins the pane. Its
    vertical padding is what the bar leaves -- ImGui sizes a dock tab bar at the
    UI font plus ``2 * frame_padding.y`` and nothing drawn here can make it
    taller. ``None`` is round one's shape, unchanged.

    ImGui gives a dock tab bar no draw hook and rounds a tab's upper corners only
    (``tab_rounding``). What it does keep is where each tab is
    (``TabBar.bar_rect``, ``TabItem.offset``/``width``), so this paints over the
    tab bar in the host window's own draw list, after every window has ended. That
    list is rendered in the host's z-order, so a modal over the docks still covers
    what is drawn here -- the foreground list would not. Input is untouched: ImGui
    hit-tests its own tabs, which are exactly under these.

    A prototype: a tab being dragged, a scrolled tab bar's arrows and the close
    button on hover are drawn over, not reproduced.
    """
    from imgui_bundle import imgui

    style = imgui.get_style()
    seen: set[int] = set()
    lane = imgui.get_color_u32(imgui.Col_.title_bg)
    font = imgui.get_font()
    size = imgui.get_font_size() if spec is None else style.font_size_base * spec.scale
    inset = 3.0 if spec is None else spec.inset
    gap = inset if spec is None else spec.gap
    floor = 1.0 if spec is None else 0.0
    for window in imgui.get_current_context().windows:
        node = window.dock_node
        if node is None or int(node.id_) in seen:
            continue
        seen.add(int(node.id_))
        bar = node.tab_bar
        host = node.host_window
        # ImGui's IsVisible and IsFocused are bitfields the binding does not reach;
        # the frame stamps beside them say the same of this frame.
        frame = imgui.get_frame_count()
        if bar is None or host is None or not len(bar.tabs) or node.last_frame_active != frame:
            continue
        if node.is_hidden_tab_bar() or node.is_no_tab_bar():
            continue
        lo, hi = bar.bar_rect.min, bar.bar_rect.max
        if hi.x <= lo.x or hi.y <= lo.y:
            continue
        dl = host.draw_list
        dl.push_clip_rect(imgui.ImVec2(lo.x, lo.y), imgui.ImVec2(hi.x, hi.y), False)
        dl.add_rect_filled(imgui.ImVec2(lo.x, lo.y), imgui.ImVec2(hi.x, hi.y), lane)
        for tab in bar.tabs:
            if tab.window is None:
                continue
            x0 = lo.x + tab.offset - bar.scrolling_anim
            selected = int(tab.id_) == int(bar.selected_tab_id)
            focused = selected and node.last_frame_focused == frame
            state = "focused" if focused else "selected" if selected else "unselected"
            a = imgui.ImVec2(x0 + gap * 0.5, lo.y + inset)
            b = imgui.ImVec2(x0 + tab.width - gap * 0.5, hi.y - floor)
            if spec is None:
                _paint_tab(
                    dl,
                    font,
                    size,
                    a,
                    b,
                    state,
                    _label(tab.window),
                    style.frame_padding.x - gap * 0.5,
                )
                continue
            # ImGui sized the tab for its own label at the UI font; a larger text
            # is cut at the tab's edge rather than drawn over its neighbour.
            dl.push_clip_rect(a, b, True)
            _paint_tab(dl, font, size, a, b, state, _label(tab.window), spec.pad[0])
            dl.pop_clip_rect()
        dl.pop_clip_rect()


# What each state of a drawn tab is filled with and written in. ``hovered`` is
# ImGui's translucent accent laid over the lane, as its own tab bar draws it.
_TAB_STATES = {
    "focused": ("tab_selected", "text"),
    "selected": ("tab_dimmed_selected", "text"),
    "unselected": ("tab", "text_disabled"),
    "hovered": ("tab_hovered", "text"),
}


def _paint_tab(
    dl: Any, font: Any, size: float, a: Any, b: Any, state: str, text: str, pad_x: float
) -> None:
    """
    One drawn tab between ``a`` and ``b``: four rounded corners, its text ``pad_x``
    in from the left and centred top to bottom, and on the focused tab a 2 px
    accent rule along its foot.
    """
    from imgui_bundle import imgui

    style = imgui.get_style()
    fill, ink = _TAB_STATES[state]
    dl.add_rect_filled(a, b, imgui.get_color_u32(getattr(imgui.Col_, fill)), style.tab_rounding)
    if state == "focused":
        dl.add_rect_filled(
            imgui.ImVec2(a.x + style.tab_rounding, b.y - 2.0),
            imgui.ImVec2(b.x - style.tab_rounding, b.y),
            imgui.get_color_u32(imgui.Col_.check_mark),
        )
    ty = a.y + ((b.y - a.y) - size) * 0.5
    dl.add_text(
        font,
        size,
        imgui.ImVec2(a.x + pad_x, ty),
        imgui.get_color_u32(getattr(imgui.Col_, ink)),
        text,
    )


# The mock-up's text. Fixed, and the same in every variant.
_BLOCKS = (
    (
        "List the package and its tests  ·  timeout 2 min",
        "tree tle tests",
        (
            "tle",
            "├── __init__.py",
            "├── offsets.py",
            "└── parse.py",
            "tests",
            "├── data",
            "│   └── catalog.html",
        ),
        "3 more lines",
        False,
    ),
    (
        "Run the parser's tests  ·  timeout 10 min  ·  exit 1",
        "pytest -q tests/test_parse.py",
        (
            "FAILED tests/test_parse.py::test_checksum_counts_minus - AssertionError: 4 != 5",
            "1 failed, 37 passed in 0.41s",
        ),
        None,
        True,
    ),
)


def _caption_font() -> Any:
    """
    A proportional face for the caption, if the text library put one on the atlas;
    the UI face otherwise. ImGui's own fonts are Inconsolata only.
    """
    from imgui_bundle import imgui

    for font in imgui.get_io().fonts.fonts:
        if "inconsolata" not in font.get_debug_name().lower() and "proggy" not in (
            font.get_debug_name().lower()
        ):
            return font
    return None


def draw_terminal_mockup() -> None:
    """
    The terminal-style tool block, drawn as a floating window over the session
    pane. A mock-up of U1/U5's look: near-black ground, grey monospace, a small
    variable-width caption over the command, the output cut short with an expand
    affordance. Its rounding and padding are the variant's, as the real one's
    would be.
    """
    from imgui_bundle import imgui

    from pptmstr import theme

    session = imgui.internal.find_window_by_name("SESSION")
    if session is None:
        return
    pos, size = session.pos, session.size
    imgui.set_next_window_pos(imgui.ImVec2(pos.x + 24.0, pos.y + 70.0))
    imgui.set_next_window_size(imgui.ImVec2(min(size.x - 48.0, 760.0), 0.0))
    flags = (
        imgui.WindowFlags_.no_decoration
        | imgui.WindowFlags_.no_saved_settings
        | imgui.WindowFlags_.always_auto_resize
        | imgui.WindowFlags_.no_focus_on_appearing
    )
    imgui.begin("##style-probe-terminal", None, flags)
    imgui.text_colored(
        theme.P.warn.vec4, "MOCK-UP -- drawn by scripts/style_probe.py, not by the app"
    )
    caption = _caption_font()
    near_black = imgui.ImVec4(0.043, 0.043, 0.047, 1.0)
    grey = imgui.ImVec4(0.78, 0.78, 0.78, 1.0)
    dim = imgui.ImVec4(0.55, 0.55, 0.57, 1.0)
    style = imgui.get_style()
    for n, (said, command, output, more, failed) in enumerate(_BLOCKS):
        imgui.push_style_color(imgui.Col_.child_bg, near_black)
        imgui.push_style_var(imgui.StyleVar_.child_rounding, style.frame_rounding)
        imgui.push_style_var(imgui.StyleVar_.window_padding, imgui.ImVec2(10.0, 6.0))
        imgui.begin_child(
            f"##block{n}",
            imgui.ImVec2(0.0, 0.0),
            imgui.ChildFlags_.always_use_window_padding | imgui.ChildFlags_.auto_resize_y,
        )
        if failed:
            lo = imgui.get_window_pos()
            hi = imgui.ImVec2(lo.x + 3.0, lo.y + imgui.get_window_height())
            imgui.get_window_draw_list().add_rect_filled(
                lo, hi, imgui.color_convert_float4_to_u32(theme.P.danger.vec4)
            )
        imgui.push_font(caption, imgui.get_font_size() * 0.8)
        imgui.text_colored(dim, said)
        imgui.pop_font()
        imgui.text_colored(grey, f"$ {command}")
        for line in output:
            imgui.text_colored(dim, line)
        if more is not None:
            imgui.small_button(f"expand  ({more})##more{n}")
        imgui.end_child()
        imgui.pop_style_var(2)
        imgui.pop_style_color()
    imgui.end()


# -- round two: specimen sheets over soft + wells -----------------------------------
#
# A sheet is one frame in which every variant of one dimension is drawn side by
# side on real widgets, each cell under its own style: ``apply`` before the cell's
# widgets and ``revert`` after, over the base every sheet shares. Every label is
# built from the values the cell applies, so a label cannot say one thing while
# the cell draws another.

OUT2 = ROOT / "shots" / "style-probe-2"
SHEETS = ("tabs", "buttons", "lines", "dropdowns")
# Taller than the app's 1500x900, so the buttons and lines sheets fit in one frame;
# the Xvfb screen is made big enough for it (``_on_xvfb``).
SHEET_SIZE = (1500, 1300)


def _with(
    scalars: tuple[tuple[str, float | tuple[float, float]], ...],
    **values: float | tuple[float, float],
) -> tuple[tuple[str, float | tuple[float, float]], ...]:
    """``scalars`` with each named value replaced in place, or appended if absent."""
    out = [(name, values.get(name, value)) for name, value in scalars]
    out += [(name, value) for name, value in values.items() if name not in dict(scalars)]
    return tuple(out)


# The base of every sheet: soft, with the frame outline it took off put back.
# ``frame_border_size`` is one value for every frame ImGui draws -- wells, check
# and radio boxes, combos and buttons alike -- so on its own it outlines soft's
# flat buttons too. The buttons sheet shows the alternative, a per-call push.
SOFT_WELLS = Variant(
    "soft-wells",
    "soft with frame_border_size 1 kept, so wells, check and radio boxes are outlined",
    scalars=_with(SOFT.scalars, frame_border_size=1.0),
    colors=SOFT.colors,
    inset_tabs=True,
)

# -- sheet 1: tabs. One dimension per row, the other two at their middle step.
TAB_PADS: tuple[tuple[float, float], ...] = ((8.0, 3.0), (10.0, 4.0), (12.0, 5.0))
TAB_MARGINS: tuple[tuple[float, float], ...] = ((2.0, 1.0), (4.0, 2.0), (6.0, 4.0))  # gap, inset
TAB_SCALES: tuple[float, ...] = (1.00, 1.10, 1.15)
TAB_MIDDLE = TabSpec(pad=TAB_PADS[1], gap=TAB_MARGINS[1][0], inset=TAB_MARGINS[1][1], scale=1.10)
TAB_ROWS: tuple[tuple[str, tuple[TabSpec, ...]], ...] = (
    (
        "interior padding (text to tab edge)",
        tuple(dataclasses.replace(TAB_MIDDLE, pad=p) for p in TAB_PADS),
    ),
    (
        "exterior margin (tab to tab, tab to lane)",
        tuple(dataclasses.replace(TAB_MIDDLE, gap=g, inset=i) for g, i in TAB_MARGINS),
    ),
    ("text size", tuple(dataclasses.replace(TAB_MIDDLE, scale=s) for s in TAB_SCALES)),
)
# The tabs every cell draws, and the state each is drawn in: a focused pane's bar,
# then an unfocused pane's, whose selected tab is how every other pane's reads.
TAB_SAMPLE = (("session", "focused"), ("board", "unselected"), ("detail", "hovered"))
TAB_SAMPLE_UNFOCUSED = (("fleet", "selected"), ("needs you", "unselected"))


# -- sheet 2: buttons. Three padding/rounding steps per size; the middle is soft's.
def _button_step(kind: str, pad: tuple[float, float], rounding: float) -> Variant:
    return Variant(
        f"{kind}-{pad[0]:g}x{pad[1]:g}-r{rounding:g}",
        f"{kind} at frame_padding {pad} and frame_rounding {rounding}",
        scalars=(("frame_padding", pad), ("frame_rounding", rounding)),
    )


BUTTON_STEPS: dict[str, tuple[Variant, ...]] = {
    # small_button draws with frame_padding.y forced to 0; y stays soft's 6 for the
    # well beside it, which is a frame like any other.
    "small_button": tuple(
        _button_step("small_button", (x, 6.0), r) for x, r in ((6.0, 3.0), (9.0, 5.0), (12.0, 8.0))
    ),
    "button": tuple(
        _button_step("button", p, r)
        for p, r in (((7.0, 4.0), 3.0), ((9.0, 6.0), 5.0), ((12.0, 7.0), 8.0))
    ),
}
BUTTON_LABELS = {
    "small_button": ("copy", "1,204 earlier lines", "amend", "back to latest"),
    "button": ("launch", "interrupt", "fork", "close session"),
}
BUTTON_STATES = ("normal", "hovered", "active", "disabled")

# -- sheet 3: line contrast. Col_.border and Col_.separator at four levels.
LINE_LEVELS: tuple[Mix, ...] = (
    Mix("bg", "border", 1.0),  # the palette's border: today's, and soft + wells'
    Mix("bg", "text_dim", 0.5),
    Mix("bg", "text_dim", 0.72),
    Mix("bg", "text_dim", 0.9),
)
LINE_CELLS: tuple[Variant, ...] = tuple(
    Variant(
        f"lines-{n}",
        f"Col_.border and Col_.separator at {mix}",
        colors=(("border", mix), ("separator", mix)),
    )
    for n, mix in enumerate(LINE_LEVELS, 1)
)
# The level the full screens use: the first at or over WCAG 2.1's 3:1 for
# non-text contrast (SC 1.4.11), which the test holds it to.
LINE_MIDDLE = LINE_LEVELS[2]
WELL_OPTIONS: tuple[Variant, ...] = (
    Variant(
        "well-border-only",
        "border only (pane's fill)",
        scalars=(("frame_border_size", 1.0),),
        colors=(("frame_bg", Mix("bg", "bg", 1.0)),),
    ),
    Variant(
        "well-fill-only",
        "soft's fill only (round one)",
        scalars=(("frame_border_size", 0.0),),
        colors=(("frame_bg", Mix("bg", "panel", 0.35)),),
    ),
    Variant(
        "well-darker-fill",
        "darker fill only",
        scalars=(("frame_border_size", 0.0),),
        colors=(("frame_bg", Mix("bg", "black", 0.5)),),
    ),
    Variant(
        "well-both",
        "both: border + darker fill",
        scalars=(("frame_border_size", 1.0),),
        colors=(("frame_bg", Mix("bg", "black", 0.5)),),
    ),
    Variant(
        "well-base",
        "the base: border + soft's fill",
        scalars=(("frame_border_size", 1.0),),
        colors=(("frame_bg", Mix("bg", "panel", 0.35)),),
    ),
)
# The line levels the wells are drawn at: the base's, and the screens' 3:1.
WELL_LEVELS: tuple[int, ...] = (0, 2)

# -- sheet 4: dropdowns. The middle is the base as it stands.
DROPDOWNS: tuple[Variant, ...] = (
    Variant(
        "dropdown-flat",
        "flat: no outline on the field or the popup, square popup, tinted arrow",
        scalars=(
            ("frame_border_size", 0.0),
            ("popup_border_size", 0.0),
            ("popup_rounding", 0.0),
        ),
    ),
    Variant(
        "dropdown-framed",
        "framed: outlined field and popup, popup rounding 4, tinted arrow (soft + wells)",
        scalars=(
            ("frame_border_size", 1.0),
            ("popup_border_size", 1.0),
            ("popup_rounding", 4.0),
        ),
    ),
    Variant(
        "dropdown-lifted",
        "lifted: outlined field, arrow in the field's fill,"
        " borderless rounder popup on a lighter fill",
        scalars=(
            ("frame_border_size", 1.0),
            ("popup_border_size", 0.0),
            ("popup_rounding", 8.0),
        ),
        colors=(
            ("button", Mix("bg", "panel", 0.35)),
            ("popup_bg", Mix("panel_alt", "text", 0.08)),
        ),
    ),
)

# The full screens: soft + wells with the middle step of each dimension. Buttons
# and dropdowns' middles are the base's own values, so only the lines and the
# tab geometry differ from SOFT_WELLS.
MIDDLE = Variant(
    "soft-wells-middle",
    "soft + wells, lines at the 3:1 level, round two's middle tab geometry",
    scalars=SOFT_WELLS.scalars,
    colors=SOFT_WELLS.colors + (("border", LINE_MIDDLE), ("separator", LINE_MIDDLE)),
    inset_tabs=True,
    tabs=TAB_MIDDLE,
)

ROUND_TWO: tuple[Variant, ...] = (SOFT_WELLS, MIDDLE)
# Every variant a sheet cell lays over SOFT_WELLS.
CELLS: tuple[Variant, ...] = (
    *BUTTON_STEPS["small_button"],
    *BUTTON_STEPS["button"],
    *LINE_CELLS,
    *WELL_OPTIONS,
    *DROPDOWNS,
)


# What a value reaches in the app when it is set as a style value, as theme.py would
# set it, rather than pushed around one cell as the sheets do. A cell that sets a value
# beyond its sheet's dimension says so with this; a test holds every cell's values to
# having an entry. Read from imgui_widgets.cpp / imgui.cpp (1.92): every frame is drawn
# with frame_padding, frame_rounding, frame_border_size and Col_.border; a combo's arrow
# is Col_.button (Col_.button_hovered when open or hovered), no slot of its own.
REACH: dict[str, str] = {
    "frame_padding": "every frame's padding: buttons, wells, combos, check and radio boxes",
    "frame_rounding": "every frame's corners: buttons, wells, combos, check and radio boxes",
    "frame_border_size": "every frame's outline: buttons, wells, combos, check and radio boxes",
    "popup_border_size": "every popup's outline: combo lists, menus, context menus",
    "popup_rounding": "every popup's corners: combo lists, menus, context menus",
    "Col_.button": "every button and small_button's fill, and every combo's arrow",
    "Col_.popup_bg": "every popup's fill: combo lists, menus, context menus",
    "Col_.frame_bg": "every well, combo field, check and radio box's fill",
    "Col_.border": "every outline: wells, check and radio boxes, buttons, children,"
    " popups, and the 2 px pane edge",
    "Col_.separator": "every separator and separator_text",
}


def reach(variant: Variant) -> list[str]:
    """What setting ``variant``'s values as style values would change, one line per value."""
    names = [name for name, _ in variant.scalars]
    names += [f"Col_.{slot}" for slot, _ in variant.colors]
    return [f"as a style value, {name} is {REACH[name]}" for name in names]


# The values the dropdowns sheet varies that stay within dropdowns.
DROPDOWN_DIMENSION = ("popup_border_size", "popup_rounding", "Col_.popup_bg")


def changes_beyond(variant: Variant, within: tuple[str, ...]) -> list[str]:
    """
    ``reach`` for the values ``variant`` sets that differ from the style in force and
    are not ``within`` the sheet's dimension: what choosing the cell would change
    elsewhere in the app. Needs an ImGui context.
    """
    from imgui_bundle import imgui

    style = imgui.get_style()
    changed = [
        name
        for name, value in variant.scalars
        if _read(getattr(style, name)) != (value if isinstance(value, tuple) else float(value))
    ]
    for slot, mix in variant.colors:
        now, want = style.color_(int(getattr(imgui.Col_, slot))), color_of(mix)
        if rgb8(now) != rgb8(want):
            changed.append(f"Col_.{slot}")
    return [f"as a style value, {name} is {REACH[name]}" for name in changed if name not in within]


def rgb8(colour: Any) -> tuple[int, int, int]:
    """
    A float colour as the 8-bit triple ImGui packs it into (``IM_F32_TO_INT8_SAT``:
    ``int(v * 255 + 0.5)``), which is what reaches the framebuffer.
    """
    return tuple(  # type: ignore[return-value]
        int(min(1.0, max(0.0, v)) * 255.0 + 0.5) for v in (colour.x, colour.y, colour.z)
    )


def luminance(rgb: tuple[int, int, int]) -> float:
    """WCAG 2.1 relative luminance of an 8-bit sRGB triple."""

    def channel(byte: int) -> float:
        v = byte / 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """WCAG 2.1 contrast ratio of two 8-bit sRGB triples: 1 (same) to 21 (black on white)."""
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def hex8(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _slot(name: str) -> tuple[int, int, int]:
    from imgui_bundle import imgui

    return rgb8(imgui.get_style().color_(int(getattr(imgui.Col_, name))))


def ratio_on_pane(slot: str) -> str:
    """``slot``'s colour in force and its contrast on ``window_bg``, the pane's fill."""
    ink, ground = _slot(slot), _slot("window_bg")
    return f"{hex8(ink)} {contrast(ink, ground):.2f}:1 on {hex8(ground)}"


def cell_label(variant: Variant) -> str:
    """The values ``variant`` sets, as a cell's caption: ImGui's own names."""
    parts = []
    for name, value in variant.scalars:
        shown = f"{value[0]:g}x{value[1]:g}" if isinstance(value, tuple) else f"{value:g}"
        parts.append(f"{name} {shown}")
    parts += [f"Col_.{slot} = {mix}" for slot, mix in variant.colors]
    return " · ".join(parts) or "(the base)"


@contextlib.contextmanager
def laid_over(variant: Variant) -> Iterator[None]:
    """``variant`` in force for the body, and taken off after it, however it ends."""
    saved = apply(variant)
    try:
        yield
    finally:
        revert(saved)


def _caption(text: str, *, dim: bool = True) -> None:
    from imgui_bundle import imgui

    if dim:
        imgui.push_style_color(
            imgui.Col_.text, imgui.get_style().color_(int(imgui.Col_.text_disabled))
        )
    imgui.text_wrapped(text)
    if dim:
        imgui.pop_style_color()


def _warn_wrapped(text: str) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    imgui.push_style_color(imgui.Col_.text, theme.P.warn.vec4)
    imgui.text_wrapped(text)
    imgui.pop_style_color()


def _sheet_header(title: str, varies: str) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    style = imgui.get_style()
    imgui.text(f"style probe, round two -- {title}")
    _caption(
        f"base: soft + wells (soft with frame_border_size 1) · theme {theme.current().name}"
        f" · UI font {style.font_size_base:g} px · varies: {varies}"
    )
    imgui.spacing()


def _sheet_tabs() -> None:
    from imgui_bundle import imgui

    style = imgui.get_style()
    base = style.font_size_base
    bar = base + 2.0 * style.frame_padding.y
    _sheet_header(
        "tabs (drawn inset tabs, lowercase)",
        "one dimension per row, the other two at the middle step (pad 10x4, gap 4, inset 2,"
        " text 1.10×)",
    )
    _caption(
        "focused pane: session = selected and focused (tab_selected + 2 px accent rule) ·"
        " board = unselected (tab) · detail = hovered (tab_hovered over the lane).  Unfocused"
        " pane under it: fleet = selected, not focused (tab_dimmed_selected), which is how"
        " every pane but the focused one reads · lane = title_bg · pane = window_bg with"
        " Col_.border round it"
    )
    _caption(
        f"a dock's tab bar is the UI font + 2 x frame_padding.y = {bar:g} px tall in"
        " soft + wells; a lane taller than that cannot be drawn in a dock without a"
        " larger frame_padding.y, which every frame pays"
    )
    _caption(
        "drawn as a dock draws them: ImGui lays tab rectangles item_inner_spacing.x apart"
        " (measured, diagnostics/tab-gap.png), so tab to tab is gap + that and the first tab"
        " is gap/2 from the lane's left; inset is the top's only. A dock also sizes each tab"
        " itself (its label at the UI font, frame_padding.x and close-button room, shrunk when"
        " the bar is crowded), so on a dock pad.x moves the text and does not widen the tab;"
        " here the tab is sized from pad."
    )
    width = (imgui.get_content_region_avail().x - 2.0 * style.item_spacing.x) / 3.0
    for title, specs in TAB_ROWS:
        imgui.separator_text(title)
        for n, spec in enumerate(specs):
            if n:
                imgui.same_line()
            imgui.begin_group()
            _tab_cell(spec, width, bar)
            imgui.end_group()


def _tab_cell(spec: TabSpec, width: float, bar: float) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    style = imgui.get_style()
    base = style.font_size_base
    lane = spec.lane(base)
    inner_x = style.item_inner_spacing.x
    imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + width)
    _caption(spec.label(), dim=False)
    _caption(spec.dock_label(inner_x))
    room = f"text {base * spec.scale:g} px · lane {lane:g} px"
    if lane <= bar:
        _caption(f"{room} · fits the {bar:g} px dock bar")
    else:
        need = (lane - base) / 2.0
        imgui.push_style_color(imgui.Col_.text, theme.P.warn.vec4)
        imgui.text_wrapped(f"{room} · over the {bar:g} px dock bar: needs frame_padding.y {need:g}")
        imgui.pop_style_color()
    imgui.pop_text_wrap_pos()
    for sample, pane_h, lines in (
        (TAB_SAMPLE, 52.0, ("> chase a flaky test", "Working out where to start.")),
        (TAB_SAMPLE_UNFOCUSED, 30.0, ("PPTMSTR  3 sessions",)),
    ):
        _tab_pane(spec, width, lane, sample, pane_h, lines)
    imgui.spacing()


def _tab_pane(
    spec: TabSpec,
    width: float,
    lane: float,
    sample: tuple[tuple[str, str], ...],
    pane_h: float,
    lines: tuple[str, ...],
) -> None:
    """One lane of drawn tabs at the geometry a dock gives them, over a pane."""
    from imgui_bundle import imgui

    style = imgui.get_style()
    base = style.font_size_base
    between, left, _ = spec.on_dock(style.item_inner_spacing.x)
    dl = imgui.get_window_draw_list()
    font = imgui.get_font()
    top = imgui.get_cursor_screen_pos()
    right = top.x + width
    dl.add_rect_filled(
        top, imgui.ImVec2(right, top.y + lane), imgui.get_color_u32(imgui.Col_.title_bg)
    )
    x = top.x + left
    for text, state in sample:
        imgui.push_font(None, base * spec.scale)
        size = imgui.get_font_size()
        text_w = imgui.calc_text_size(text).x
        imgui.pop_font()
        a = imgui.ImVec2(x, top.y + spec.inset)
        b = imgui.ImVec2(x + text_w + 2.0 * spec.pad[0], top.y + lane)
        _paint_tab(dl, font, size, a, b, state, text, spec.pad[0])
        x = b.x + between
    pane_top = top.y + lane
    dl.add_rect(
        imgui.ImVec2(top.x, pane_top),
        imgui.ImVec2(right, pane_top + pane_h),
        imgui.get_color_u32(imgui.Col_.border),
    )
    for n, line in enumerate(lines):
        ink = "text_disabled" if n == 0 else "text"
        dl.add_text(
            imgui.ImVec2(top.x + style.window_padding.x, pane_top + 6.0 + n * (base + 4.0)),
            imgui.get_color_u32(getattr(imgui.Col_, ink)),
            line,
        )
    imgui.dummy(imgui.ImVec2(width, lane + pane_h + 4.0))


def _sheet_buttons() -> None:
    from imgui_bundle import imgui

    _sheet_header(
        "buttons (small_button and button)",
        "frame_padding and frame_rounding, three steps per size (the middle is soft's),"
        " with and without the outline, and the outline at the 3:1 line level",
    )
    _caption(
        "each step is pushed around the buttons only; the well and checkboxes beside them"
        " are drawn on the base (soft + wells) in every row. Set in theme.py instead, a step"
        " is global: its frame_padding and frame_rounding also pad and round every well,"
        " combo, check and radio box, as each row's yellow line says."
    )
    _caption(
        "small_button and button cannot be styled apart by style values: both read the one"
        " frame_padding.x and frame_rounding, and small_button forces frame_padding.y to 0"
        " (measured: its height is the font's). Choosing a step per size needs code: a"
        " widgets.small_button / widgets.button wrapper pushing its own frame_padding and"
        " frame_rounding per call, at 42 and 39 call sites"
    )
    _caption(
        "outline 1 = frame_border_size as soft + wells sets it for every frame; outline 0"
        " = frame_border_size pushed to 0 around each button only, the same wrapper's cost."
        " hovered / active = Col_.button_hovered / Col_.button_active pushed as Col_.button,"
        " the colour ImGui picks in that state; disabled = begin_disabled (alpha 0.6)"
    )
    _caption(
        "in pptmstr/ui today: small_button at 42 call sites in 11 modules (pane rows: copy,"
        " earlier lines, filter chips, amend/cancel, help marks); button at 39 in 13"
        " (dialog footers, launcher, health actions, composer, approvals)"
    )
    flags = imgui.TableFlags_.sizing_fixed_fit | imgui.TableFlags_.borders_inner_h
    middle = BUTTON_STEPS["small_button"][1], BUTTON_STEPS["button"][1]
    for kind in ("small_button", "button"):
        imgui.separator_text(f"imgui.{kind}")
        if not imgui.begin_table(f"##{kind}", 7, flags):
            continue
        imgui.table_setup_column("values", imgui.TableColumnFlags_.width_fixed, 470.0)
        for head in (*BUTTON_STATES, "well beside it (base)", "checkboxes (base)"):
            imgui.table_setup_column(head)
        imgui.table_headers_row()
        for step in BUTTON_STEPS[kind]:
            # What the step changes from the base, set globally: the frames beside it.
            cost = bool(changes_beyond(step, ()))
            _button_row(kind, step, "1", cost)
            if step in middle:
                _button_row(kind, step, "3:1", cost)
            _button_row(kind, step, "0", cost)
        imgui.end_table()


def _button_row(kind: str, step: Variant, outline: str, global_cost: bool) -> None:
    """
    One step in four states. ``outline`` is "1" (the base's), "3:1" (frame outline at
    line level 3) or "0" (pushed off around each button). The step, and the level,
    are in force for the values and the buttons only.
    """
    from imgui_bundle import imgui

    key = f"{step.name}-{outline}"
    lines = LINE_CELLS[2] if outline == "3:1" else Variant("base-lines", "")
    with laid_over(step), laid_over(lines):
        style = imgui.get_style()
        imgui.table_next_row()
        imgui.table_next_column()
        pad = style.frame_padding
        if kind == "small_button":
            values = f"frame_padding.x {pad.x:g} (y forced 0) · rounding {style.frame_rounding:g}"
        else:
            values = f"frame_padding {pad.x:g}x{pad.y:g} · rounding {style.frame_rounding:g}"
        imgui.text(values)
        if outline == "0":
            _caption("outline 0: frame_border_size 0 pushed per call (code)")
        else:
            ink, fill = _slot("border"), _slot("button")
            level = "line level 3" if outline == "3:1" else "line level 1 (base)"
            _caption(
                f"outline 1, {level}: {hex8(ink)} on {hex8(fill)} = {contrast(ink, fill):.2f}:1"
            )
        if outline == "3:1":
            _warn_wrapped(f"global: Col_.border = {LINE_MIDDLE} is every outline (sheet 3)")
        if global_cost:
            _warn_wrapped("global: also pads/rounds every well, combo, check/radio")
        press = imgui.small_button if kind == "small_button" else imgui.button
        for state, label in zip(BUTTON_STATES, BUTTON_LABELS[kind], strict=True):
            imgui.table_next_column()
            pushed = 0
            if outline == "0":
                imgui.push_style_var(imgui.StyleVar_.frame_border_size, 0.0)
            if state in ("hovered", "active"):
                slot = imgui.Col_.button_hovered if state == "hovered" else imgui.Col_.button_active
                imgui.push_style_color(imgui.Col_.button, style.color_(int(slot)))
                pushed = 1
            imgui.begin_disabled(state == "disabled")
            press(f"{label}##{key}-{state}")
            imgui.end_disabled()
            imgui.pop_style_color(pushed)
            if outline == "0":
                imgui.pop_style_var()
    imgui.table_next_column()
    imgui.set_next_item_width(170.0)
    imgui.input_text(f"##well-{key}", "chase a flaky test")
    imgui.table_next_column()
    imgui.checkbox(f"follow##{key}", False)
    imgui.same_line()
    imgui.checkbox(f"reasoning##{key}", True)


# Which slot draws the 2 px line between two docked panes. Measured on the full
# screens: ``_screens_scenario`` draws one frame under DIAGNOSTIC, and the pixels
# either side of the FLEET/SESSION and SESSION/DETAIL edges in
# diagnostics/pane-edge.png are 2 px of ``ok`` (Col_.border), not ``danger``.
PANE_EDGE_SLOT = "border"


def _sheet_lines() -> None:
    from imgui_bundle import imgui

    _sheet_header(
        "line contrast (dividers and outlines)",
        "Col_.border and Col_.separator together, four levels; contrast is WCAG 2.1's,"
        " from the 8-bit colours drawn, against window_bg (the pane)",
    )
    _caption(
        "Col_.separator draws separator and separator_text; Col_.border draws child and"
        " popup outlines, frame outlines (wells, check and radio boxes, every button's,"
        f" sheet 2) and, measured,"
        f" the pane edge (Col_.{PANE_EDGE_SLOT}). The two are set together here."
    )
    flags = imgui.TableFlags_.sizing_stretch_same | imgui.TableFlags_.borders_inner_v
    if imgui.begin_table("##lines", 1 + len(LINE_CELLS), flags):
        imgui.table_setup_column("element", imgui.TableColumnFlags_.width_fixed, 150.0)
        for n in range(len(LINE_CELLS)):
            imgui.table_setup_column(f"level {n + 1}")
        imgui.table_headers_row()
        rows = (
            ("value", _line_value),
            ("separator", _line_separator),
            ("separator_text", lambda key: imgui.separator_text("gate")),
            ("child outline", _line_child),
            ("pane edge", _line_pane_edge),
            ("input well", _line_well),
            ("checkbox", _line_checks),
            ("radio", _line_radios),
            ("popup outline", _line_popup),
        )
        for head, draw in rows:
            imgui.table_next_row()
            imgui.table_next_column()
            imgui.text(head)
            for cell in LINE_CELLS:
                imgui.table_next_column()
                with laid_over(cell):
                    draw(cell.name)
        imgui.end_table()
    for level in WELL_LEVELS:
        lines = LINE_CELLS[level]
        imgui.spacing()
        imgui.separator_text(
            f"wells: outline, fill, or both -- outlines at line level {level + 1},"
            f" {lines.colors[0][1]}"
        )
        if imgui.begin_table(f"##wells-{level}", 1 + len(WELL_OPTIONS), flags):
            imgui.table_setup_column("element", imgui.TableColumnFlags_.width_fixed, 150.0)
            for option in WELL_OPTIONS:
                imgui.table_setup_column(option.summary)
            imgui.table_headers_row()
            well_rows = (
                ("value", _well_value),
                ("input well", _line_well),
                ("checkbox", _line_checks),
                ("radio", _line_radios),
                ("combo", _well_combo),
            )
            for head, draw in well_rows:
                imgui.table_next_row()
                imgui.table_next_column()
                imgui.text(head)
                for option in WELL_OPTIONS:
                    imgui.table_next_column()
                    with laid_over(lines):
                        beyond = changes_beyond(option, ("Col_.frame_bg",))
                        with laid_over(option):
                            draw(f"{option.name}-{level}")
                    if head == "value":
                        for line in beyond:
                            _warn_wrapped(line)
            imgui.end_table()


def _line_value(key: str) -> None:
    from imgui_bundle import imgui

    cell = next(c for c in LINE_CELLS if c.name == key)
    imgui.text_wrapped(str(cell.colors[0][1]))
    imgui.text(ratio_on_pane("border"))
    ink = _slot("border")
    for ground in ("child_bg", "frame_bg"):
        fill = _slot(ground)
        _caption(f"{contrast(ink, fill):.2f}:1 on {ground} {hex8(fill)}")


def _line_separator(key: str) -> None:
    from imgui_bundle import imgui

    # A table row starts flush on the one above; the gap puts the line in its own row.
    imgui.dummy(imgui.ImVec2(0.0, 6.0))
    imgui.separator()
    imgui.dummy(imgui.ImVec2(0.0, 2.0))


def _line_child(key: str) -> None:
    from imgui_bundle import imgui

    imgui.begin_child(f"##child-{key}", imgui.ImVec2(0.0, 40.0), imgui.ChildFlags_.borders)
    imgui.text_disabled("sub-agents")
    imgui.end_child()


def _line_pane_edge(key: str) -> None:
    """Two panes meeting as two docked windows do: 2 px of the measured slot."""
    from imgui_bundle import imgui

    dl = imgui.get_window_draw_list()
    top = imgui.get_cursor_screen_pos()
    width = imgui.get_content_region_avail().x
    mid = top.x + width * 0.5
    ink = imgui.get_color_u32(getattr(imgui.Col_, PANE_EDGE_SLOT))
    dl.add_rect_filled(imgui.ImVec2(mid - 1.0, top.y), imgui.ImVec2(mid + 1.0, top.y + 36.0), ink)
    dl.add_text(
        imgui.ImVec2(top.x + 4.0, top.y + 10.0),
        imgui.get_color_u32(imgui.Col_.text_disabled),
        "fleet",
    )
    dl.add_text(
        imgui.ImVec2(mid + 8.0, top.y + 10.0),
        imgui.get_color_u32(imgui.Col_.text_disabled),
        "session",
    )
    imgui.dummy(imgui.ImVec2(width, 36.0))


def _line_well(key: str) -> None:
    from imgui_bundle import imgui

    imgui.set_next_item_width(-1.0)
    imgui.input_text(f"##well-{key}", "filter")


def _line_checks(key: str) -> None:
    from imgui_bundle import imgui

    imgui.checkbox(f"follow##{key}", False)
    imgui.same_line()
    imgui.checkbox(f"reasoning##{key}", True)


def _line_radios(key: str) -> None:
    from imgui_bundle import imgui

    imgui.radio_button(f"wrap##{key}", False)
    imgui.same_line()
    imgui.radio_button(f"typeset##{key}", True)


def _line_popup(key: str) -> None:
    from imgui_bundle import imgui

    def body() -> None:
        imgui.menu_item(f"Interrupt##{key}", "", False, True)
        imgui.menu_item(f"Fork##{key}", "", False, False)

    _popup_replica(f"##popup-{key}", -1.0, body)


def _well_value(key: str) -> None:
    from imgui_bundle import imgui

    option = next(o for o in WELL_OPTIONS if key.startswith(f"{o.name}-"))
    style = imgui.get_style()
    imgui.text_wrapped(cell_label(option))
    fill, ground = _slot("frame_bg"), _slot("window_bg")
    imgui.text(f"fill {hex8(fill)} {contrast(fill, ground):.2f}:1")
    if style.frame_border_size > 0.0:
        imgui.text_wrapped(f"outline {ratio_on_pane('border')}")
    else:
        imgui.text("no outline")


def _well_combo(key: str) -> None:
    from imgui_bundle import imgui

    imgui.set_next_item_width(-1.0)
    if imgui.begin_combo(f"##combo-{key}", "claude-sonnet-5"):
        imgui.end_combo()


def _popup_replica(child_id: str, width: float, body: Any, pad: Any = None) -> None:
    """
    A popup drawn in place, as a child with the values ImGui gives a popup window:
    ``popup_bg`` for its fill, ``popup_rounding`` and ``popup_border_size`` for its
    corners and outline (``Col_.border``). One real popup can be open at a time, so
    every variant side by side is a replica; the dropdowns sheet puts one real
    combo popup beside them to check the replica against.
    """
    from imgui_bundle import imgui

    style = imgui.get_style()
    imgui.push_style_color(imgui.Col_.child_bg, style.color_(int(imgui.Col_.popup_bg)))
    imgui.push_style_var(imgui.StyleVar_.child_rounding, style.popup_rounding)
    imgui.push_style_var(imgui.StyleVar_.child_border_size, style.popup_border_size)
    imgui.push_style_var(imgui.StyleVar_.window_padding, pad or style.window_padding)
    imgui.begin_child(
        child_id,
        imgui.ImVec2(width, 0.0),
        imgui.ChildFlags_.borders
        | imgui.ChildFlags_.always_use_window_padding
        | imgui.ChildFlags_.auto_resize_y,
    )
    body()
    imgui.end_child()
    imgui.pop_style_var(3)
    imgui.pop_style_color()


def _sheet_dropdowns() -> None:
    from imgui_bundle import imgui

    from pptmstr import model_catalog, theme

    _sheet_header(
        "dropdowns (combo and menu popup)",
        "field outline, arrow fill, popup outline, popup rounding and fill",
    )
    _caption(
        "combo: closed, then open (popup replica under its field, arrow in"
        " Col_.button_hovered as when open) · menu: the bar with Session open, then"
        " its popup replica. The last column is a real combo popup on the framed"
        " variant, to check the replicas against."
    )
    _caption(
        "ImGui 1.92 has no slot for the combo arrow: it is Col_.button, and"
        " Col_.button_hovered while the combo is open or hovered, so an arrow colour is"
        " every button's colour too, and an open combo's arrow is tinted in every variant."
        " Lines in yellow are what a column's values change outside dropdowns."
    )
    flags = imgui.TableFlags_.sizing_stretch_same | imgui.TableFlags_.borders_inner_v
    if not imgui.begin_table("##dropdowns", len(DROPDOWNS) + 1, flags):
        return
    for variant in DROPDOWNS:
        imgui.table_setup_column(variant.name)
    imgui.table_setup_column("real combo popup (framed)")
    imgui.table_headers_row()
    themes = [palette.display_name for palette in theme.THEMES.values()]
    models = list(model_catalog.FALLBACK_MODELS)
    imgui.table_next_row()
    for variant in DROPDOWNS:
        imgui.table_next_column()
        beyond = changes_beyond(variant, DROPDOWN_DIMENSION)
        with laid_over(variant):
            imgui.text_wrapped(variant.summary)
            _caption(cell_label(variant))
            for line in beyond:
                _warn_wrapped(line)
            if not beyond:
                _caption("changes nothing outside dropdowns")
            imgui.text("theme")
            imgui.set_next_item_width(-1.0)
            if imgui.begin_combo(f"##theme-{variant.name}", themes[0]):
                imgui.end_combo()
            imgui.text("buttons, same values")
            imgui.button(f"launch##{variant.name}")
            imgui.same_line()
            imgui.small_button(f"copy##{variant.name}")
            imgui.spacing()
            imgui.text("model (open)")
            _open_combo_replica(variant.name, models)
            imgui.spacing()
            imgui.text("Session menu (open)")
            _menu_replica(variant.name)
    imgui.table_next_column()
    with laid_over(DROPDOWNS[1]):
        imgui.text_wrapped("the framed variant, drawn by ImGui's own begin_combo")
        _caption("opened with internal.open_popup_ex on the combo's popup id")
        imgui.text("model (open)")
        _real_combo(models)
    imgui.end_table()


def _open_combo_replica(key: str, models: list[str]) -> None:
    from imgui_bundle import imgui

    style = imgui.get_style()
    imgui.push_style_color(imgui.Col_.button, style.color_(int(imgui.Col_.button_hovered)))
    imgui.set_next_item_width(-1.0)
    if imgui.begin_combo(f"##model-{key}", "claude-sonnet-5"):
        imgui.end_combo()
    imgui.pop_style_color()
    # ImGui's combo popup takes the frame's horizontal padding for its own.
    pad = imgui.ImVec2(style.frame_padding.x, style.window_padding.y)

    def body() -> None:
        for model in models:
            imgui.selectable(f"{model}##{key}", model == "claude-sonnet-5")

    imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() - style.item_spacing.y)
    _popup_replica(f"##model-popup-{key}", -1.0, body, pad)


def _menu_replica(key: str) -> None:
    """The menu bar with Session open over its popup, both drawn in place."""
    from imgui_bundle import imgui

    style = imgui.get_style()
    dl = imgui.get_window_draw_list()
    top = imgui.get_cursor_screen_pos()
    width = imgui.get_content_region_avail().x
    height = imgui.get_frame_height()
    dl.add_rect_filled(
        top,
        imgui.ImVec2(top.x + width, top.y + height),
        imgui.get_color_u32(imgui.Col_.menu_bar_bg),
    )
    x = top.x + style.item_spacing.x
    session_x = x
    for label in ("View", "Session", "Theme", "Text"):
        w = imgui.calc_text_size(label).x
        if label == "Session":
            session_x = x - style.item_spacing.x
            dl.add_rect_filled(
                imgui.ImVec2(session_x, top.y),
                imgui.ImVec2(x + w + style.item_spacing.x, top.y + height),
                imgui.get_color_u32(imgui.Col_.header),
            )
        dl.add_text(
            imgui.ImVec2(x, top.y + style.frame_padding.y),
            imgui.get_color_u32(imgui.Col_.text),
            label,
        )
        x += w + 2.0 * style.item_spacing.x
    imgui.dummy(imgui.ImVec2(width, height))
    imgui.set_cursor_screen_pos(imgui.ImVec2(session_x, imgui.get_cursor_screen_pos().y - 4.0))

    def body() -> None:
        imgui.separator_text("chase a flaky test")
        imgui.menu_item(f"Interrupt##{key}", "", False, True)
        imgui.menu_item(f"Fork##{key}", "", False, False)
        imgui.menu_item(f"Details##{key}", "", False, True)
        imgui.menu_item(f"Health##{key}", "", False, True)
        imgui.menu_item(f"Close##{key}", "", False, True)
        imgui.separator_text("Gate")
        imgui.text_disabled("strict · 3 rules")
        imgui.separator()
        imgui.menu_item(f"Show ended sub-agents##{key}", "", True, True)

    _popup_replica(f"##menu-popup-{key}", 250.0, body)


def _real_combo(models: list[str]) -> None:
    from imgui_bundle import imgui

    label = "##model-real"
    combo = imgui.get_id(label)
    imgui.internal.open_popup_ex(imgui.internal.im_hash_str("##ComboPopup", 0, combo))
    imgui.set_next_item_width(-1.0)
    if imgui.begin_combo(label, "claude-sonnet-5"):
        for model in models:
            imgui.selectable(f"{model}##real", model == "claude-sonnet-5")
        imgui.end_combo()


_SHEET_DRAWERS = {
    "tabs": _sheet_tabs,
    "buttons": _sheet_buttons,
    "lines": _sheet_lines,
    "dropdowns": _sheet_dropdowns,
}


def draw_sheet(name: str, first: bool) -> None:
    """One sheet over the whole viewport, brought to the front on its first frame."""
    from imgui_bundle import imgui

    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.pos)
    imgui.set_next_window_size(viewport.size)
    if first:
        imgui.set_next_window_focus()
    flags = (
        imgui.WindowFlags_.no_decoration
        | imgui.WindowFlags_.no_saved_settings
        | imgui.WindowFlags_.no_move
        | imgui.WindowFlags_.no_docking
    )
    imgui.begin("##style-probe-sheet", None, flags)
    _SHEET_DRAWERS[name]()
    imgui.end()


# -- the preview: the operator's pick on every theme ----------------------------------
#
# The operator picked one cell per round-two sheet (planning record, "The operator's
# pick"). ``PICKED`` is those cells laid over the base they were drawn on, as the
# style values theme.py would set. Three of the picks are the one value
# ``frame_border_size 0``: the buttons' "no outline", the lines' "soft's fill only"
# and the dropdown's "no outline" on its field. They agree, so nothing had to be
# chosen between them; what that one value does to every frame is stated on the
# image (``PICKED_SHARED``).

OUT3 = ROOT / "shots" / "style-preview"

# "pad 10x4 gap 4 inset 1 text 1.10x": inset 1 is between the sheet's steps 1 and 2.
PICKED_TABS = TabSpec(pad=(10.0, 4.0), gap=4.0, inset=1.0, scale=1.10)

PICKED = Variant(
    "picked",
    "the operator's pick from round two: soft's buttons with no outline, soft's fill-only"
    " wells, lines at level 1, a flat dropdown with popup rounding 4, drawn tabs"
    " pad 10x4 gap 4 inset 1 text 1.10x",
    # soft's values: frame_padding 9x6, frame_rounding 5, frame_border_size 0 and
    # popup_rounding 4 are all four of the picks that soft already held. The
    # dropdown's "no outline" adds the popup's own outline, which soft left at 1.
    scalars=_with(SOFT.scalars, popup_border_size=0.0),
    # soft's colours: the tinted Col_.button is the button fill and the combo arrow
    # ("tinted arrow"), frame_bg is soft's fill. Lines at level 1 are the palette's
    # border, written out so the image and the record can quote them.
    colors=SOFT.colors + (("border", LINE_LEVELS[0]), ("separator", LINE_LEVELS[0])),
    inset_tabs=True,
    tabs=PICKED_TABS,
)

# What each value the picks share does to the app, set as a style value. One line
# each, drawn on every picked image.
PICKED_SHARED: tuple[str, ...] = (
    "frame_border_size 0 is ONE value: buttons' 'no outline' = lines' 'soft's fill only'"
    " = dropdown field 'no outline' -> no frame anywhere has an outline (wells, check/radio"
    " boxes, combos, buttons)",
    "Col_.button (soft's tint) is every button's fill AND the combo arrow ('tinted arrow');"
    " popup_border_size 0 + popup_rounding 4 is every popup: combo lists, menus, context"
    " menus",
    "frame_padding 9x6 also pads every well/combo/checkbox and sets the dock tab bar to"
    " 28 px; small_button keeps y 0 (16 px). Not picked, kept from soft: scrollbar 16 r8,"
    " grab_rounding 5, item_spacing 8x6",
)


def picked_caption(theme_name: str, inner_x: float) -> tuple[str, ...]:
    """
    The lines a picked screen carries: its values, then what the shared ones do.
    ``inner_x`` is ``item_inner_spacing.x``, which spaces a dock's tabs.
    """
    tabs = PICKED_TABS
    between, left, top = tabs.on_dock(inner_x)
    head = (
        f"PREVIEW of the pick · theme {theme_name} · buttons frame_padding 9x6 rounding 5"
        f" border 0 · lines level 1 ({LINE_LEVELS[0]}) · dropdown popup border 0 rounding 4"
        f" · tabs {tabs.label()} (dock: {between:g} px apart, {left:g} left, {top:g} top)"
    )
    return (head, *PICKED_SHARED)


def before_caption(theme_name: str) -> tuple[str, ...]:
    return (
        f"BEFORE · theme {theme_name} · today's theme.apply_style, no overrides"
        " (frame_padding 7x4, rounding 2, frame_border_size 1, ImGui's tabs)",
    )


def draw_caption(lines: tuple[str, ...]) -> None:
    """
    ``lines`` on the foreground list, yellow on black, the first right-aligned in the menu bar
    and the rest in the status bar, where the app draws nothing on the right. The
    size is the largest from 13 px down to 10 that fits the width; below that a
    line is cut by the viewport.
    """
    from imgui_bundle import imgui

    viewport = imgui.get_main_viewport()
    dl = imgui.get_foreground_draw_list()
    font = imgui.get_font()
    # Fixed, not the palette's: the caption has to read the same on every theme,
    # and warn on win311's grey does not.
    ink = imgui.color_convert_float4_to_u32(imgui.ImVec4(1.0, 0.84, 0.37, 1.0))
    ground = imgui.color_convert_float4_to_u32(imgui.ImVec4(0.0, 0.0, 0.0, 1.0))
    lo, size = viewport.pos, viewport.size

    def width(text: str, px: float) -> float:
        imgui.push_font(None, px)
        w = float(imgui.calc_text_size(text).x)
        imgui.pop_font()
        return w

    def fit(text: str, room: float) -> float:
        for px in (13.0, 12.0, 11.0, 10.0):
            if width(text, px) <= room:
                return px
        return 10.0

    bar = imgui.get_frame_height()
    head, *rest = lines
    px = fit(head, size.x * 0.82)
    w = width(head, px)
    x = lo.x + size.x - w - 8.0
    dl.add_rect_filled(imgui.ImVec2(x - 6.0, lo.y), imgui.ImVec2(lo.x + size.x, lo.y + bar), ground)
    dl.add_text(font, px, imgui.ImVec2(x, lo.y + (bar - px) * 0.5), ink, head)
    if not rest:
        return
    pitch = 12.0
    px = min(fit(line, size.x * 0.62) for line in rest)
    widest = max(width(line, px) for line in rest)
    x = lo.x + size.x - widest - 8.0
    y = lo.y + size.y - pitch * len(rest) - 4.0
    dl.add_rect_filled(
        imgui.ImVec2(x - 6.0, y - 2.0), imgui.ImVec2(lo.x + size.x, lo.y + size.y), ground
    )
    for n, line in enumerate(rest):
        dl.add_text(font, min(px, pitch), imgui.ImVec2(x, y + n * pitch), ink, line)


# -- the small_button wrapper sheet --------------------------------------------------
#
# ImGui's small_button forces frame_padding.y to 0, so the operator's "crowded" is
# not answerable by a style value. A wrapper that draws imgui.button under its own
# pushed padding, rounding and font size is; these are four of it.


@dataclass(frozen=True, slots=True)
class Micro:
    """
    A hypothetical ``widgets.small_button``: ``imgui.button`` with ``pad`` and
    ``rounding`` pushed, and its text at ``scale`` times the UI font.
    """

    name: str
    pad: tuple[float, float]
    rounding: float
    scale: float = 1.0

    def height(self, font_px: float) -> float:
        """The button's height: its text and the vertical padding above and below."""
        return font_px * self.scale + 2.0 * self.pad[1]

    def label(self, font_px: float) -> str:
        return (
            f"{self.name}: frame_padding {self.pad[0]:g}x{self.pad[1]:g} · rounding"
            f" {self.rounding:g} · text {self.scale:g}x ({font_px * self.scale:g} px)"
            f" -> {self.height(font_px):g} px"
        )


# Two heights (20 and 22 px) at two text sizes (16 and 14 px). 14 px is 0.875 of
# the UI font: a whole pixel, so the text is not put on half pixels.
MICROS: tuple[Micro, ...] = (
    Micro("A", (6.0, 2.0), 4.0, 1.0),
    Micro("B", (8.0, 3.0), 5.0, 1.0),
    Micro("C", (6.0, 3.0), 4.0, 0.875),
    Micro("D", (8.0, 4.0), 5.0, 0.875),
)


def micro_button(label: str, micro: Micro) -> bool:
    """What the wrapper would be: ``imgui.button`` under ``micro``'s values."""
    from imgui_bundle import imgui

    style = imgui.get_style()
    scaled = micro.scale != 1.0
    if scaled:
        imgui.push_font(None, style.font_size_base * micro.scale)
    imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(*micro.pad))
    imgui.push_style_var(imgui.StyleVar_.frame_rounding, micro.rounding)
    pressed = bool(imgui.button(label))
    imgui.pop_style_var(2)
    if scaled:
        imgui.pop_font()
    return pressed


# The rows each kind is drawn in. ``None`` is today's small_button under the pick,
# ``"button"`` the 9x6 button beside it.
MicroKind = Micro | str | None
MICRO_ROWS: tuple[MicroKind, ...] = (None, *MICROS, "button")


def _press_for(kind: MicroKind) -> Any:
    from imgui_bundle import imgui

    if kind is None:
        return imgui.small_button
    if kind == "button":
        return imgui.button
    assert isinstance(kind, Micro)
    return lambda label: micro_button(label, kind)


def expected_height(kind: MicroKind, font_px: float, pad_y: float) -> float:
    """The height a row kind's button is labelled with, before it is measured."""
    if kind is None:
        return font_px
    if kind == "button":
        return font_px + 2.0 * pad_y
    assert isinstance(kind, Micro)
    return kind.height(font_px)


def _kind_title(kind: MicroKind, font_px: float, pad: Any, rounding: float) -> str:
    if kind is None:
        return (
            f"small_button today: frame_padding.x {pad.x:g} (y forced 0) · rounding"
            f" {rounding:g} · text 1x -> {font_px:g} px"
        )
    if kind == "button":
        return (
            f"button (the pick): frame_padding {pad.x:g}x{pad.y:g} · rounding {rounding:g}"
            f" -> {font_px + 2.0 * pad.y:g} px"
        )
    assert isinstance(kind, Micro)
    return kind.label(font_px)


# Pane rows from pptmstr/ui, each drawn with a text line above and below so the
# pitch a taller button forces is visible: (title, draw(press, key)).
def _row_copy(press: Any, key: str) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    imgui.text_colored(theme.P.warn.vec4, "wants approval")
    imgui.same_line()
    imgui.text_disabled("· 0s")
    imgui.same_line()
    press(f"copy##{key}")


def _row_earlier(press: Any, key: str) -> None:
    press(f"1,204 earlier lines##{key}")


def _row_chips(press: Any, key: str) -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    for at, chip in enumerate(("open", "held", "waiting", "done")):
        if at:
            imgui.same_line()
        hidden = chip == "done"
        if hidden:
            imgui.push_style_color(imgui.Col_.text, theme.P.text_dim.vec4)
            imgui.push_style_color(imgui.Col_.button, imgui.ImVec4(0.0, 0.0, 0.0, 0.0))
        press(f"{chip}##{key}")
        if hidden:
            imgui.pop_style_color(2)


def _row_card(press: Any, key: str) -> None:
    from imgui_bundle import imgui

    press(f"amend##{key}")
    imgui.same_line()
    press(f"close...##{key}")


MICRO_CONTEXTS = (
    ("detail: copy", "chase a flaky test", _row_copy, "pptmstr / code-reviewer"),
    ("transcript: earlier lines", "> chase a flaky test", _row_earlier, "Working out where."),
    ("board: filter chips", "show", _row_chips, "style-preview  held"),
    ("board card: actions", "Spec (says: read):", _row_card, "result (none yet)"),
)


def measured_row(draw: Any, press: Any, key: str) -> float:
    """``draw``'s row in a group, and the group's height: what the row takes."""
    from imgui_bundle import imgui

    imgui.begin_group()
    draw(press, key)
    imgui.end_group()
    return float(imgui.get_item_rect_size().y)


def _sheet_small_button() -> None:
    from imgui_bundle import imgui

    from pptmstr import theme

    style = imgui.get_style()
    font_px = style.font_size_base
    imgui.text("style preview -- a small_button wrapper, four variants")
    _caption(
        f"base: the pick (theme {theme.current().name}, frame_border_size 0, buttons 9x6"
        f" rounding 5) · UI font {font_px:g} px · text line {font_px:g} px · item_spacing.y"
        f" {style.item_spacing.y:g}"
    )
    _caption(
        "ImGui's small_button forces frame_padding.y to 0, so its height is the font's"
        " whatever the style says. Each variant below is a hypothetical widgets.small_button"
        " that draws imgui.button with its own frame_padding (y > 0), frame_rounding and"
        " optionally a smaller font, pushed per call: code at 42 call sites, not a style"
        " value. 'h' is measured from the drawn item; the label's figure is"
        " font x scale + 2 x pad.y."
    )
    _caption(
        "A row holding one of these is as tall as the button, not the text line: the row"
        " figure is the measured height of the whole row (a group), and the pitch to the next"
        " line adds item_spacing.y. Text before the button is top-aligned, as the app draws it"
        " (no align_text_to_frame_padding)."
    )
    _warn_wrapped(
        "hovered / active = Col_.button_hovered / _active pushed as Col_.button (the colour"
        " ImGui picks in that state); disabled = begin_disabled"
    )
    flags = imgui.TableFlags_.sizing_fixed_fit | imgui.TableFlags_.borders_inner_h
    imgui.separator_text("states")
    if imgui.begin_table("##micro-states", 6, flags):
        imgui.table_setup_column("variant", imgui.TableColumnFlags_.width_fixed, 560.0)
        for head in (*BUTTON_STATES, "measured"):
            imgui.table_setup_column(head)
        imgui.table_headers_row()
        for n, kind in enumerate(MICRO_ROWS):
            _micro_states_row(kind, n)
        imgui.end_table()
    imgui.separator_text("in pane rows (a text line above and below each)")
    if imgui.begin_table("##micro-rows", 1 + len(MICRO_CONTEXTS), flags):
        imgui.table_setup_column("variant", imgui.TableColumnFlags_.width_fixed, 150.0)
        for title, *_ in MICRO_CONTEXTS:
            imgui.table_setup_column(title)
        imgui.table_headers_row()
        for n, kind in enumerate(MICRO_ROWS):
            imgui.table_next_row()
            imgui.table_next_column()
            name = "small_button" if kind is None else "button" if kind == "button" else ""
            if isinstance(kind, Micro):
                name = f"{kind.name} -> {kind.height(font_px):g} px"
            imgui.text(name)
            press = _press_for(kind)
            for title, above, draw, below in MICRO_CONTEXTS:
                imgui.table_next_column()
                imgui.text_disabled(above)
                row = measured_row(draw, press, f"{title}-{n}")
                imgui.text_disabled(below)
                grows = row - font_px
                more = f" (+{grows:g} over the text line)" if grows > 0 else " (= the text line)"
                _caption(f"row {row:g} px{more} · pitch {row + style.item_spacing.y:g}")
        imgui.end_table()


def _micro_states_row(kind: MicroKind, n: int) -> None:
    from imgui_bundle import imgui

    style = imgui.get_style()
    font_px = style.font_size_base
    imgui.table_next_row()
    imgui.table_next_column()
    imgui.text_wrapped(_kind_title(kind, font_px, style.frame_padding, style.frame_rounding))
    press = _press_for(kind)
    heights = []
    for state, label in zip(
        BUTTON_STATES, ("copy", "1,204 earlier lines", "held", "amend"), strict=True
    ):
        imgui.table_next_column()
        pushed = 0
        if state in ("hovered", "active"):
            slot = imgui.Col_.button_hovered if state == "hovered" else imgui.Col_.button_active
            imgui.push_style_color(imgui.Col_.button, style.color_(int(slot)))
            pushed = 1
        imgui.begin_disabled(state == "disabled")
        press(f"{label}##micro-{n}-{state}")
        heights.append(float(imgui.get_item_rect_size().y))
        imgui.end_disabled()
        imgui.pop_style_color(pushed)
    imgui.table_next_column()
    want = expected_height(kind, font_px, style.frame_padding.y)
    seen = sorted(set(heights))
    shown = " / ".join(f"{h:g}" for h in seen)
    if seen == [want]:
        imgui.text(f"h {shown} px = label")
    else:
        _warn_wrapped(f"h {shown} px, label says {want:g}")


_SHEET_DRAWERS["small-button"] = _sheet_small_button


# -- the run ------------------------------------------------------------------------


class _Shown:
    """What the overlay hook draws this frame, set by the scenario."""

    terminal = False
    sheet: str | None = None
    sheet_first = False
    # The preview's caption: "picked", "before", or nothing.
    caption: str | None = None


def _scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    """
    The same screens for every variant: triage, the board in triage, focus on the
    session whose transcript holds the fake report's tool calls, the terminal-block
    mock-up over it, the settings dialog, the launcher.
    """
    import screenshot
    from imgui_bundle import hello_imgui, imgui

    from pptmstr.ui.focus import OnNode

    del fixtures
    yield from screenshot._until(
        "the fake fleet",
        lambda: state.driver is not None
        and "report" in state.driver.cast
        and state.frame_snap is not None,
    )
    yield from screenshot._frames(30)
    print(f"en dash at the UI font: {imgui.calc_text_size(chr(0x2013)).x:g} px", flush=True)
    yield "triage"

    # The board is a team's: the cursor on the fake fleet's one team, its tab in front.
    yield from screenshot._until("the fake team", lambda: screenshot._team(state) is not None)
    state.focus.target = OnNode(screenshot._team(state), pinned=True)
    docking = hello_imgui.get_runner_params().docking_params
    docking.focus_dockable_window("BOARD")
    yield from screenshot._frames()
    yield "board"

    state.pending_layout = "FOCUS"
    yield from screenshot._frames(3)
    # What a click on a card with nothing waiting does: the session itself, pinned,
    # rather than the oldest obligation it answers for, which may be a sub-agent's.
    state.focus.target = OnNode(state.driver.cast["report"], pinned=True)
    docking.focus_dockable_window("SESSION")
    yield from screenshot._frames(20)
    # The fake report's tool calls are at the top of its transcript and the pane
    # follows the bottom, so the wheel is turned over it as an operator would.
    session = screenshot._window("SESSION")
    io = imgui.get_io()
    io.add_mouse_pos_event(session.pos.x + session.size.x * 0.5, session.pos.y + 200.0)
    for _ in range(30):
        io.add_mouse_wheel_event(0.0, 20.0)
        yield None
    io.add_mouse_pos_event(-1.0, -1.0)
    yield from screenshot._frames(3)
    yield "focus"

    _Shown.terminal = True
    yield from screenshot._frames()
    yield "terminal-block"
    _Shown.terminal = False

    state.settings_dialog.request_open()
    yield from screenshot._until("settings to open", lambda: state.settings_dialog.is_open)
    yield from screenshot._frames()
    yield "settings"
    state.settings_dialog.request_close()
    yield from screenshot._until("settings to close", lambda: not state.settings_dialog.is_open)
    yield from screenshot._frames(3)

    yield from screenshot._open_launcher(state)
    yield "launcher"
    yield from screenshot._close_launcher(state)


def _sheets_scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    """Each sheet over the running app, one frame each, once the fonts are in."""
    import screenshot
    from imgui_bundle import hello_imgui, imgui

    del fixtures
    yield from screenshot._until(
        "the fake fleet", lambda: state.driver is not None and state.frame_snap is not None
    )
    hello_imgui.change_window_size(SHEET_SIZE)
    yield from screenshot._until(
        f"the window at {SHEET_SIZE}",
        lambda: tuple(int(v) for v in imgui.get_io().display_size) == SHEET_SIZE,
    )
    yield from screenshot._frames(30)
    for name in SHEETS:
        _Shown.sheet, _Shown.sheet_first = name, True
        yield from screenshot._frames(10)
        yield name
    _Shown.sheet = None


# Each line slot in a colour of its own, so a pixel on the full screens says which
# slot drew it.
DIAGNOSTIC = Variant(
    "diagnostic",
    "Col_.separator in danger and Col_.border in ok, to tell which draws the pane edge",
    colors=(("separator", Mix("bg", "danger", 1.0)), ("border", Mix("bg", "ok", 1.0))),
)


# Every drawn tab state in one colour and the lane in another, so a pixel row across
# a dock's tab bar measures the gap between drawn tabs.
TAB_DIAGNOSTIC = Variant(
    "tab-diagnostic",
    "every tab fill in warn and the lane in bg, to measure the gap between drawn tabs",
    colors=(
        ("title_bg", Mix("bg", "bg", 1.0)),
        ("tab", Mix("bg", "warn", 1.0)),
        ("tab_hovered", Mix("bg", "warn", 1.0)),
        ("tab_selected", Mix("bg", "warn", 1.0)),
        ("tab_dimmed_selected", Mix("bg", "warn", 1.0)),
    ),
)


def _print_tab_bars() -> None:
    """Every visible dock tab bar's height, against the frame height that sizes it."""
    from imgui_bundle import imgui

    seen: set[int] = set()
    for window in imgui.get_current_context().windows:
        node = window.dock_node
        if node is None or int(node.id_) in seen or node.tab_bar is None:
            continue
        seen.add(int(node.id_))
        bar = node.tab_bar
        rect = bar.bar_rect
        tabs = sorted((float(t.offset), float(t.width)) for t in bar.tabs)
        gaps = [nxt[0] - (off + w) for (off, w), nxt in zip(tabs, tabs[1:], strict=False)]
        print(
            f"tab bar of {window.name}: {rect.max.y - rect.min.y:g} px tall"
            f" (frame height {imgui.get_frame_height():g}); x {rect.min.x:g};"
            f" tabs (offset, width) {tabs}; between rectangles {gaps}"
            f" (item_inner_spacing.x {imgui.get_style().item_inner_spacing.x:g})",
            flush=True,
        )


def _screens_scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    """Round one's six screens, then the pane-edge diagnostic on the last of them."""
    import screenshot

    yield from _scenario(state, fixtures)
    yield from screenshot._frames(3)
    _print_tab_bars()
    with laid_over(DIAGNOSTIC):
        yield from screenshot._frames(3)
        yield "diagnostics/pane-edge"
    yield from screenshot._frames(2)
    with laid_over(TAB_DIAGNOSTIC):
        yield from screenshot._frames(3)
        yield "diagnostics/tab-gap"
    yield from screenshot._frames(2)


def _caption_lines(kind: str) -> tuple[str, ...]:
    from imgui_bundle import imgui

    from pptmstr import theme

    name = theme.current().name
    if kind == "before":
        return before_caption(name)
    return picked_caption(name, imgui.get_style().item_inner_spacing.x)


def _run_one(
    variant: Variant,
    out_dir: Path,
    scenario: Any = None,
    theme_name: str | None = None,
    caption: str | None = None,
) -> int:
    """
    One app run under ``variant`` with screenshot.py's machinery. Everything the
    variant changes is patched into this process before ``app.main`` runs.
    ``theme_name`` is put in the settings the app reads at start, as if the
    operator's file named it; ``caption`` says which preview caption to draw.
    """
    import screenshot

    from pptmstr import app as pptmstr_app
    from pptmstr import settings as settings_mod
    from pptmstr import theme

    if theme_name is not None:
        real_read = settings_mod.read

        @functools.wraps(real_read)
        def read(path: Path | None = None) -> Any:
            found = real_read(path)
            return dataclasses.replace(
                found, settings=dataclasses.replace(found.settings, theme=theme_name)
            )

        settings_mod.read = read  # type: ignore[assignment]
    _Shown.caption = caption

    real_apply = theme.apply_style

    @functools.wraps(real_apply)
    def apply_style() -> None:
        real_apply()
        apply(variant)

    theme.apply_style = apply_style  # type: ignore[assignment]

    real_overlays = pptmstr_app._draw_overlays

    def overlays(state: Any) -> None:
        if variant.inset_tabs:
            draw_inset_tabs(variant.tabs)
        if _Shown.terminal:
            draw_terminal_mockup()
        if _Shown.sheet is not None:
            draw_sheet(_Shown.sheet, _Shown.sheet_first)
            _Shown.sheet_first = False
        if _Shown.caption is not None:
            draw_caption(_caption_lines(_Shown.caption))
        real_overlays(state)

    pptmstr_app._draw_overlays = overlays  # type: ignore[assignment]
    screenshot.SCENARIOS[SCENARIO] = scenario or _scenario
    sys.argv = [
        "screenshot.py",
        "--scenario",
        SCENARIO,
        "--out-dir",
        str(out_dir),
        "--argv",
        "--fake",
    ]
    print(f"variant {variant.name}: {variant.summary}", flush=True)
    return int(screenshot.main())


def _on_xvfb(args: list[str], screen: str | None = None) -> int:
    """This script with ``args`` on its own Xvfb, its screen ``screen`` if given."""
    env = dict(os.environ)
    if screen is not None:
        env["XVFB_SHOT_SCREEN"] = screen
    done = subprocess.run(
        [
            str(ROOT / "scripts" / "xvfb_shot.sh"),
            "--",
            str(ROOT / ".venv" / "bin" / "python"),
            str(Path(__file__).resolve()),
            *args,
        ],
        cwd=ROOT,
        env=env,
    )
    return done.returncode


def _missing(out_dir: Path, names: tuple[str, ...]) -> int:
    missing = [s for s in names if not (out_dir / f"{s}.png").is_file()]
    if missing:
        print(f"{out_dir}: no image for {', '.join(missing)}", file=sys.stderr)
        return 1
    return 0


def _run_all(names: list[str]) -> int:
    """Each variant on its own Xvfb, one after another; the first failure ends it."""
    for name in names:
        out_dir = OUT / name
        started = time.monotonic()
        code = _on_xvfb(["--variant", name, "--out-dir", str(out_dir)])
        print(f"{name}: exit {code} in {time.monotonic() - started:.0f}s", flush=True)
        if code != 0:
            return code
        if _missing(out_dir, SCREENS):
            return 1
    return 0


def _run_round_two() -> int:
    """The four sheets, then the six screens on the middle values, each on its own Xvfb."""
    started = time.monotonic()
    code = _on_xvfb(
        ["--sheets", "--out-dir", str(OUT2)], f"{SHEET_SIZE[0] + 100}x{SHEET_SIZE[1] + 100}x24"
    )
    print(f"sheets: exit {code} in {time.monotonic() - started:.0f}s", flush=True)
    if code != 0 or _missing(OUT2, SHEETS):
        return code or 1
    started = time.monotonic()
    code = _on_xvfb(["--screens", "--out-dir", str(OUT2 / "screens")])
    print(f"screens: exit {code} in {time.monotonic() - started:.0f}s", flush=True)
    return code or _missing(OUT2 / "screens", SCREENS)


def _preview_scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    """Round one's six screens, each written under the name the preview gives it."""
    prefix = "before-" if _Shown.caption == "before" else ""
    for step in _scenario(state, fixtures):
        yield None if step is None else f"{prefix}{step}"


def _micro_scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    """The small_button wrapper sheet, at the sheets' size."""
    import screenshot
    from imgui_bundle import hello_imgui, imgui

    del fixtures
    yield from screenshot._until(
        "the fake fleet", lambda: state.driver is not None and state.frame_snap is not None
    )
    hello_imgui.change_window_size(SHEET_SIZE)
    yield from screenshot._until(
        f"the window at {SHEET_SIZE}",
        lambda: tuple(int(v) for v in imgui.get_io().display_size) == SHEET_SIZE,
    )
    yield from screenshot._frames(30)
    _Shown.sheet, _Shown.sheet_first = "small-button", True
    yield from screenshot._frames(10)
    yield "small-button"
    _Shown.sheet = None


def preview_themes() -> tuple[str, ...]:
    from pptmstr import theme

    return tuple(theme.THEMES)


def _run_preview(themes: tuple[str, ...]) -> int:
    """
    The wrapper sheet, then for each theme today's six screens and the pick's, each
    run on its own Xvfb. The first failure ends it.
    """
    started = time.monotonic()
    code = _on_xvfb(
        ["--micro", "--out-dir", str(OUT3)], f"{SHEET_SIZE[0] + 100}x{SHEET_SIZE[1] + 100}x24"
    )
    print(f"small-button: exit {code} in {time.monotonic() - started:.0f}s", flush=True)
    if code != 0 or _missing(OUT3, ("small-button",)):
        return code or 1
    for name in themes:
        for kind, prefix in (("before", "before-"), ("picked", "")):
            started = time.monotonic()
            out_dir = OUT3 / name
            code = _on_xvfb(["--preview", kind, "--theme", name, "--out-dir", str(out_dir)])
            print(f"{name} {kind}: exit {code} in {time.monotonic() - started:.0f}s", flush=True)
            if code != 0:
                return code
            if _missing(out_dir, tuple(f"{prefix}{s}" for s in SCREENS)):
                return 1
    return 0


def describe(variant: Variant) -> list[str]:
    """The variant's exact values, one per line, as the planning record quotes them."""
    lines = [f"{name} = {value}" for name, value in variant.scalars]
    lines += [f"Col_.{slot} = {mix}" for slot, mix in variant.colors]
    if variant.inset_tabs:
        shape = "" if variant.tabs is None else f", {variant.tabs.label()}"
        lines.append(
            f"inset tabs drawn by draw_inset_tabs (lowercase, four rounded corners{shape})"
        )
    return lines or ["(no overrides)"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--variant", choices=sorted(BY_NAME))
    which.add_argument("--all", action="store_true", help="every variant, each on its own Xvfb")
    which.add_argument("--describe", action="store_true", help="print every variant's values")
    which.add_argument(
        "--round-two", action="store_true", help="the sheets and the screens, each on its own Xvfb"
    )
    which.add_argument("--sheets", action="store_true", help="the sheets, on the display there is")
    which.add_argument(
        "--screens", action="store_true", help="the middle values' screens, likewise"
    )
    which.add_argument(
        "--style-preview",
        action="store_true",
        help="the pick and today's style on every theme, and the wrapper sheet, each on an Xvfb",
    )
    which.add_argument(
        "--preview",
        choices=("before", "picked"),
        help="one theme's six screens, on the display there is",
    )
    which.add_argument("--micro", action="store_true", help="the wrapper sheet, likewise")
    ap.add_argument("--theme", default=None, help="with --preview: the theme to run under")
    ap.add_argument(
        "--themes", nargs="+", default=None, help="with --style-preview: these themes only"
    )
    ap.add_argument("--out-dir", type=Path, default=None)
    args = ap.parse_args()
    if args.describe:
        for variant in (*VARIANTS, *ROUND_TWO, *CELLS, PICKED):
            print(f"{variant.name}: {variant.summary}")
            for line in describe(variant):
                print(f"    {line}")
        return 0
    if args.all:
        return _run_all([v.name for v in VARIANTS])
    if args.round_two:
        return _run_round_two()
    if args.style_preview:
        return _run_preview(tuple(args.themes or preview_themes()))
    if args.preview:
        name = args.theme or "dark"
        variant = PICKED if args.preview == "picked" else CURRENT
        return _run_one(variant, args.out_dir or OUT3 / name, _preview_scenario, name, args.preview)
    if args.micro:
        return _run_one(PICKED, args.out_dir or OUT3, _micro_scenario, "dark")
    if args.sheets:
        return _run_one(SOFT_WELLS, args.out_dir or OUT2, _sheets_scenario)
    if args.screens:
        return _run_one(MIDDLE, args.out_dir or OUT2 / "screens", _screens_scenario)
    return _run_one(BY_NAME[args.variant], args.out_dir or OUT / args.variant)


if __name__ == "__main__":
    raise SystemExit(main())
