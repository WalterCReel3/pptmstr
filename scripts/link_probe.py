#!/usr/bin/env python3
"""
Draw a link-style control in variants, and in the two places it would replace
``widgets.small_button`` -- the tool block's footer and the ``(?)`` marks -- for
the operator to pick (planning/2026-10-16-link-variants.md).

Nothing here changes what the app draws. The sheets are drawn over the running
``--fake`` app under the current ``theme.py``; ``pptmstr/`` is not edited, and
the one swap this makes in it -- the block's affordance drawn as a link -- is
made for one ``tool_block.draw`` call and undone before it returns
(``_links_in_blocks``).

    scripts/link_probe.py --all                 # dark, light, high_contrast, each on its own Xvfb
    scripts/link_probe.py --render --theme dark # one theme, on the display there is
    scripts/link_probe.py --describe            # every variant's values, as text

Writes ``shots/link-variants/<theme>.png`` (the variants) and
``shots/link-variants/<theme>-context.png`` (footer, marks, baselines).

**``imgui.text_link`` decides less than the sheet asks for.** Read from the
installed binding (``text_link(label: str) -> bool``) and measured in
``tests/test_link_probe.py`` against Dear ImGui 1.92.9's own draw list:

- its ink is ``Col_.text_link``, which ``theme.apply_style`` does not set: the
  stock blue ``(0.26, 0.59, 0.98)`` on every theme;
- hovered or held it raises the ink's HSV value by 0.3 or 0.4 and turns its hue
  by 0.02; the underline is the ink's value less 0.2 (``inks``);
- it is underlined always, one pixel, ``floor(descent * 0.2)`` under its box;
- its item is its text's own size: there is no padding to tighten;
- it has no disabled look of its own: under ``begin_disabled`` the style's alpha
  is applied to its underline once and to its text twice (0.6 and 0.36).

An underline on hover only, or none, is not something it can be asked for, so
those cells are drawn by ``link``: ``TextLink`` line for line through
``imgui.internal`` (the same id, box, ``ButtonBehavior``, ink rule and draw
order), with the underline as a parameter. With ``Underline.ALWAYS`` it draws
the vertices ``text_link`` draws; the test holds that.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import enum
import functools
import json
import math
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

from imgui_bundle import imgui  # noqa: E402

from pptmstr import theme  # noqa: E402
from pptmstr.theme import Color, col, contrast_ratio  # noqa: E402

OUT = ROOT / "shots" / "link-variants"
SCENARIO = "link-variants"
SHEET_SIZE = (1560, 1240)
THEMES = ("dark", "light", "high_contrast")
SHEETS = ("", "-context")


# -- the control ----------------------------------------------------------------------


class Underline(enum.Enum):
    ALWAYS = "always"
    HOVER = "on hover only"
    NEVER = "never"


class Shown(enum.Enum):
    """A state a cell is painted in, whatever the pointer is doing."""

    NORMAL = "normal"
    HOVERED = "hovered"
    PRESSED = "pressed"
    DISABLED = "disabled"


RGBA = tuple[float, float, float, float]

# TextLink's ink rule, as Dear ImGui 1.92.9 writes it.
_HOVER_VALUE = 0.3
_HELD_VALUE = 0.4
_HOT_HUE = 0.02
_LINE_VALUE = 0.2
_LINE_DESCENT = 0.2


def inks(ink: RGBA, hovered: bool, held: bool) -> tuple[RGBA, RGBA]:
    """
    The label's and the underline's colours ``text_link`` derives from its ink:
    hovered or held, the value up by 0.3 or 0.4 and the hue on by 0.02; the
    underline 0.2 darker in value than the label. Alpha is the ink's.
    """
    h, s, v = imgui.color_convert_rgb_to_hsv(ink[0], ink[1], ink[2])
    if hovered or held:
        v = min(1.0, v + (_HELD_VALUE if held else _HOVER_VALUE))
        h = (h + _HOT_HUE) % 1.0
    text = (*imgui.color_convert_hsv_to_rgb(h, s, v), ink[3])
    v = max(0.0, v - _LINE_VALUE)
    line = (*imgui.color_convert_hsv_to_rgb(h, s, v), ink[3])
    return text, line  # type: ignore[return-value]


def link(
    label: str,
    ink: RGBA,
    underline: Underline = Underline.ALWAYS,
    shown: Shown | None = None,
) -> bool:
    """
    ``imgui.text_link`` at ``ink``, with its underline drawn as ``underline``
    says. True on the frame it is clicked. ``shown`` paints the cell hovered or
    pressed whatever the pointer does, for the sheet; it changes no answer.

    ``TextLink`` line for line: the box is the label's size at the line's text
    baseline offset, ``ItemSize(size, 0)``, ``ButtonBehavior`` on it, the hand
    cursor, the underline and then the label. ``begin_disabled`` reaches it as
    it reaches ``text_link``.
    """
    window = imgui.internal.get_current_window()
    if window.skip_items:
        return False
    ident = imgui.get_id(label)
    dc = window.dc
    pos = imgui.ImVec2(dc.cursor_pos.x, dc.cursor_pos.y + dc.curr_line_text_base_offset)
    size = imgui.calc_text_size(label, None, True)
    bb = imgui.internal.ImRect(pos, imgui.ImVec2(pos.x + size.x, pos.y + size.y))
    imgui.internal.item_size(size, 0.0)
    if not imgui.internal.item_add(bb, ident):
        return False
    pressed, hovered, held = imgui.internal.button_behavior(bb, ident, False, False)
    imgui.internal.render_nav_cursor(bb, ident)
    if hovered:
        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
    hot = hovered or shown in (Shown.HOVERED, Shown.PRESSED)
    down = held or shown is Shown.PRESSED
    text, line = inks(ink, hot, down)
    if underline is Underline.ALWAYS or (underline is Underline.HOVER and (hot or down)):
        descent = imgui.get_font_baked().descent * imgui.get_current_context().font_baked_scale
        y = bb.max.y + float(math.floor(descent * _LINE_DESCENT))
        window.draw_list.add_line(
            imgui.ImVec2(bb.min.x, y),
            imgui.ImVec2(bb.max.x, y),
            imgui.get_color_u32(imgui.ImVec4(*line)),
        )
    imgui.push_style_color(imgui.Col_.text, imgui.get_color_u32(imgui.ImVec4(*text)))
    imgui.internal.render_text(bb.min, label, None, True)
    imgui.pop_style_color()
    return bool(pressed)


def real_link(label: str, ink: RGBA) -> bool:
    """``imgui.text_link`` itself at ``ink``: the one push it needs, popped."""
    imgui.push_style_color(imgui.Col_.text_link, imgui.ImVec4(*ink))
    try:
        return bool(imgui.text_link(label))
    finally:
        imgui.pop_style_color()


# -- the variants ---------------------------------------------------------------------

COLOURS = ("accent", "text_dim", "text")
"""The palette roles a link is drawn in, one sheet row each."""


@dataclass(frozen=True, slots=True)
class Variant:
    colour: str
    underline: Underline

    @property
    def name(self) -> str:
        return f"{self.colour} · underline {self.underline.value}"


VARIANTS = tuple(Variant(c, u) for c in COLOURS for u in Underline)


def rgba(colour: Color) -> RGBA:
    v = colour.vec4
    return (float(v.x), float(v.y), float(v.z), float(v.w))


def ink_of(role: str, palette: theme.Palette | None = None) -> RGBA:
    used = theme.current() if palette is None else palette
    return rgba(getattr(used, role))


def _byte(v: float) -> int:
    return int(min(1.0, max(0.0, v)) * 255.0 + 0.5)


def hex_of(c: RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(_byte(v) for v in c[:3]))


def over(c: RGBA, ground: Color, alpha: float = 1.0) -> Color:
    """``c`` at its alpha times ``alpha``, composited on ``ground``, in 8 bits."""
    a = c[3] * alpha
    g = ground.rgb
    mixed = [c[i] * a + g[i] * (1.0 - a) for i in range(3)]
    return col((_byte(mixed[0]) << 16) | (_byte(mixed[1]) << 8) | _byte(mixed[2]))


# Under ``begin_disabled`` the style's alpha reaches the underline once and the
# label twice: TextLink pushes the label's colour through GetColorU32, which
# applies it, and RenderText applies it again.
DISABLED_LINE_PASSES = 1
DISABLED_TEXT_PASSES = 2


@dataclass(frozen=True, slots=True)
class Cell:
    """One cell's colours as drawn, and their contrast against ``ground``."""

    text: Color
    line: Color | None
    text_ratio: float
    line_ratio: float | None


def cell(variant: Variant, shown: Shown, ground: Color, disabled_alpha: float) -> Cell:
    """What a cell draws, composited on ``ground`` as the screen shows it."""
    hot = shown in (Shown.HOVERED, Shown.PRESSED)
    text, line = inks(ink_of(variant.colour), hot, shown is Shown.PRESSED)
    off = shown is Shown.DISABLED
    text_c = over(text, ground, disabled_alpha**DISABLED_TEXT_PASSES if off else 1.0)
    drawn = variant.underline is Underline.ALWAYS or (variant.underline is Underline.HOVER and hot)
    line_c = (
        over(line, ground, disabled_alpha**DISABLED_LINE_PASSES if off else 1.0) if drawn else None
    )
    return Cell(
        text_c,
        line_c,
        contrast_ratio(text_c, ground),
        None if line_c is None else contrast_ratio(line_c, ground),
    )


def cell_label(c: Cell) -> list[str]:
    def h(colour: Color) -> str:
        return hex_of(rgba(colour))

    lines = [f"label {h(c.text)}  {c.text_ratio:.2f}:1"]
    if c.line is None or c.line_ratio is None:
        lines.append("underline none")
    else:
        lines.append(f"underline {h(c.line)}  {c.line_ratio:.2f}:1")
    return lines


@contextlib.contextmanager
def disabled_if(off: bool) -> Iterator[None]:
    if off:
        imgui.begin_disabled()
    try:
        yield
    finally:
        if off:
            imgui.end_disabled()


def draw_variant(variant: Variant, shown: Shown, label: str) -> bool:
    """
    One cell's control: the real ``text_link`` wherever it can draw the cell --
    always underlined, and not painted in a state the pointer is not in -- and
    ``link`` otherwise.
    """
    ink = ink_of(variant.colour)
    with disabled_if(shown is Shown.DISABLED):
        if variant.underline is Underline.ALWAYS and shown in (Shown.NORMAL, Shown.DISABLED):
            return real_link(label, ink)
        painted = shown if shown in (Shown.HOVERED, Shown.PRESSED) else None
        return link(label, ink, variant.underline, painted)


def describe() -> Iterator[str]:
    ground = theme.current().bg
    alpha = imgui.get_style().disabled_alpha if imgui.get_current_context() else 0.6
    for variant in VARIANTS:
        yield variant.name
        for shown in Shown:
            yield f"    {shown.value}: " + " · ".join(
                cell_label(cell(variant, shown, ground, alpha))
            )


# -- measured on the line ------------------------------------------------------------


def _ascent(px: float | None = None) -> float:
    if px is not None:
        imgui.push_font(None, px)
    ascent = float(imgui.get_font_baked().ascent)
    if px is not None:
        imgui.pop_font()
    return ascent


def _text_baseline() -> float:
    """The last item's, for text or a link: each is laid with its top at the box's."""
    return float(imgui.get_item_rect_min().y) + _ascent()


def _button_baseline() -> float:
    """The last item's, for ``widgets.small_button``: its label is ``pad.y`` under its top."""
    from pptmstr.ui import widgets

    top = float(imgui.get_item_rect_min().y)
    return top + widgets.SMALL_BUTTON_PADDING[1] + _ascent(widgets.SMALL_BUTTON_TEXT_PX)


BASELINE_CASES = (
    "after plain text",
    "after frame-aligned text",
    "after a field",
    "after a small_button",
    "small_button after it",
)


def baselines(ink: RGBA) -> dict[str, float]:
    """
    A link's label baseline minus its line's baseline, in each case of
    ``BASELINE_CASES``, drawn at the cursor (one line each). The last case is the
    other way round: the button's label baseline minus the link's.
    """
    from pptmstr.ui import widgets

    out: dict[str, float] = {}

    imgui.text("plain text")
    before = _text_baseline()
    imgui.same_line()
    link("show all##b1", ink)
    out["after plain text"] = _text_baseline() - before

    imgui.align_text_to_frame_padding()
    imgui.text("frame-aligned text")
    before = _text_baseline()
    imgui.same_line()
    link("show all##b2", ink)
    out["after frame-aligned text"] = _text_baseline() - before

    imgui.set_next_item_width(160.0)
    imgui.input_text("##link-field", "a field")
    before = float(imgui.get_item_rect_min().y) + imgui.get_style().frame_padding.y + _ascent()
    imgui.same_line()
    link("show all##b3", ink)
    out["after a field"] = _text_baseline() - before

    widgets.small_button("copy##b4")
    before = _button_baseline()
    imgui.same_line()
    link("show all##b4", ink)
    out["after a small_button"] = _text_baseline() - before

    link("show all##b5", ink)
    before = _text_baseline()
    imgui.same_line()
    widgets.small_button("copy##b5")
    out["small_button after it"] = _button_baseline() - before
    return out


# -- in context: the tool block's footer ---------------------------------------------

FOOTER_LINES = 47


def footer_entry() -> Any:
    """A Read of ``FOOTER_LINES`` lines, as the driver's translator writes one."""
    from pptmstr.transcript import Transcript

    t = Transcript()
    body = "\n".join(f"{n:>6}\tline {n} of the file" for n in range(1, FOOTER_LINES + 1))
    t.append_tool_call("toolu_link", "Read", {"file_path": "/src/pptmstr/pptmstr/ui/widgets.py"})
    t.append_tool_result("toolu_link", "Read", body, is_error=False)
    entry = t.tool_entry("toolu_link")
    assert entry is not None
    return entry


SHOW_ALL = "show all"
COLLAPSE = "collapse"


@contextlib.contextmanager
def _links_in_blocks(ink: RGBA, underline: Underline) -> Iterator[None]:
    """
    For the ``tool_block.draw`` inside it, the block's affordance is a link: its
    count in the block's dim ink, then `` · show all`` (or ``collapse``) as the
    link, on the button's id. Undone on the way out, however it is left.
    """
    from pptmstr.theme import P
    from pptmstr.ui import tool_block, widgets

    real = widgets.small_button

    def affordance(label: str) -> bool:
        shown, _, ident = label.partition("##")
        if shown == tool_block.COLLAPSE:
            return link(f"{COLLAPSE}##{ident}", ink, underline)
        if shown != tool_block.SHOW_ALL:
            imgui.text_colored(P.terminal_dim.vec4, f"{shown} ·")
            imgui.same_line()
        return link(f"{SHOW_ALL}##{ident}", ink, underline)

    widgets.small_button = affordance  # type: ignore[assignment]
    try:
        yield
    finally:
        widgets.small_button = real  # type: ignore[assignment]


def footer_ink() -> RGBA:
    """The picked link ink inside a block: the accent at 4.5:1 on its ground, as its links are."""
    from pptmstr.theme import P, terminal_ink

    return rgba(terminal_ink(P.accent))


def draw_footer(faces: Any, *, as_link: bool, expanded: bool, key: str) -> float:
    """One real block, footer as today or as a link; answers the block's height."""
    from pptmstr.ui import tool_block

    entry = footer_entry()
    block = tool_block.layout(entry, live=False)
    with _links_in_blocks(footer_ink(), Underline.ALWAYS) if as_link else contextlib.nullcontext():
        tool_block.draw(
            block,
            key=key,
            faces=faces,
            expanded=expanded,
            viewer=lambda: tool_block.viewer_rows(entry)[:4],
        )
    return float(imgui.get_item_rect_size().y)


def plain_faces() -> Any:
    from pptmstr.ui import tool_block

    return tool_block.Faces(
        mono=contextlib.nullcontext, prose=contextlib.nullcontext, caption=contextlib.nullcontext
    )


def app_faces(px: float = 16.0) -> Any:
    """The faces the log hands a block, as ``transcript_pane`` builds them."""
    from pptmstr.ui import tool_block, typeset

    small = float(round(px * 0.8))
    return tool_block.Faces(
        mono=lambda: typeset.font(typeset.Weight.CODE, px),
        prose=lambda: typeset.font(typeset.Weight.REGULAR, px),
        caption=lambda: typeset.font(typeset.Weight.REGULAR, small),
    )


# -- in context: the marks ------------------------------------------------------------

MARK_LABELS = ("(?)", "why?")
NOTICE_LINE = "nothing runs unattended"


def mark_line(mark: str, ink: RGBA, key: str) -> float:
    """
    A quiet notice and its mark as a link: the line as ``widgets.notice`` draws
    it, less ``small_button_line`` -- a link sits on the text's own baseline, so
    there is nothing to set down. Answers the row's height.
    """
    top = imgui.get_cursor_screen_pos().y
    imgui.text_disabled(NOTICE_LINE)
    imgui.same_line()
    link(f"{mark}##{key}", ink)
    return float(imgui.get_cursor_screen_pos().y - top - imgui.get_style().item_spacing.y)


def button_line(key: str) -> float:
    """The same notice, with today's ``(?)``: ``widgets.notice`` itself."""
    from pptmstr.ui import widgets

    top = imgui.get_cursor_screen_pos().y
    widgets.notice(
        widgets.Notice(
            NOTICE_LINE, widgets.Tone.QUIET, widgets.Help(key, "the gate", ("explained",))
        )
    )
    return float(imgui.get_cursor_screen_pos().y - top - imgui.get_style().item_spacing.y)


def launcher_caption(mark: str | None, ink: RGBA, key: str) -> float:
    """
    The launcher's gate dial and its caption under it, in a dialog's style: the
    caption with today's small_button mark when ``mark`` is None, else with
    ``mark`` as a link. Answers the caption row's height.
    """
    from pptmstr.ui import widgets

    imgui.set_next_item_width(240.0)
    imgui.input_text(f"gate##{key}", "contained")
    widgets.dialog_caption()
    if mark is None:
        return button_line(f"cap-{key}")
    return mark_line(mark, ink, f"cap-{key}")


# -- the sheets -------------------------------------------------------------------


class _Shown:
    sheet: str | None = None
    first = False
    theme = "dark"
    measured: dict[str, Any] = {}


def _say(text: str, colour: Color | None = None) -> None:
    if colour is None:
        imgui.text(text)
    else:
        imgui.text_colored(colour.vec4, text)


def _sheet_window(title: str) -> bool:
    from pptmstr.theme import P

    imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
    imgui.set_next_window_size(imgui.ImVec2(*SHEET_SIZE))
    imgui.push_style_color(imgui.Col_.window_bg, P.bg.vec4)
    opened, _ = imgui.begin(
        f"{title}###link-sheet",
        None,
        imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_saved_settings,
    )
    imgui.pop_style_color()
    return opened


CELL_W = 260.0
LABEL_COL = 240.0


def draw_variants_sheet() -> None:
    from pptmstr.theme import P

    if not _sheet_window("link variants"):
        imgui.end()
        return
    style = imgui.get_style()
    alpha = float(style.disabled_alpha)
    ground = P.bg
    size = imgui.calc_text_size("show all")
    imgui.push_text_wrap_pos(SHEET_SIZE[0] - 16.0)
    _say(
        f"link variants · theme {_Shown.theme} · on the pane ground bg {hex_of(rgba(ground))}"
        f" · Dear ImGui {imgui.get_version()} imgui.text_link",
        P.text_strong,
    )
    _say(
        f"Every cell is 'show all' at the UI face's {imgui.get_font_size():g} px. Cells marked T"
        " are imgui.text_link itself; cells marked W are link_probe.link (TextLink through"
        " imgui.internal, underline as a parameter).",
        P.text_dim,
    )
    _say(
        f"hovered: HSV value +{_HOVER_VALUE}, hue +{_HOT_HUE}; pressed: value +{_HELD_VALUE};"
        f" underline: value -{_LINE_VALUE}, 1 px at floor(descent x {_LINE_DESCENT}) under the"
        f" box. disabled: begin_disabled, alpha {alpha:g} on the underline, {alpha**2:.2f}"
        " on the label (text_link has no disabled look of its own).",
        P.text_dim,
    )
    _say(
        f"padding: none -- the item is the label's own box, {size.x:g} x {size.y:g} px; so there"
        " is no tighter-padding cell. Contrast is the composited colour against bg.",
        P.text_dim,
    )
    imgui.pop_text_wrap_pos()
    imgui.dummy(imgui.ImVec2(0.0, 6.0))
    left = imgui.get_cursor_pos_x()
    for n, shown in enumerate(Shown):
        if n:
            imgui.same_line(left + LABEL_COL + n * CELL_W)
        else:
            imgui.set_cursor_pos_x(left + LABEL_COL)
        _say(shown.value, P.text_strong)
    imgui.separator()
    for variant in VARIANTS:
        row_top = imgui.get_cursor_pos_y()
        imgui.set_cursor_pos(imgui.ImVec2(left, row_top + 4.0))
        _say(variant.colour, P.text_strong)
        imgui.set_cursor_pos_x(left)
        _say(f"underline {variant.underline.value}", P.text_dim)
        imgui.set_cursor_pos_x(left)
        _say(f"ink {hex_of(ink_of(variant.colour))}", P.text_dim)
        bottom = imgui.get_cursor_pos_y()
        for n, shown in enumerate(Shown):
            x = left + LABEL_COL + n * CELL_W
            imgui.set_cursor_pos(imgui.ImVec2(x, row_top + 4.0))
            real = variant.underline is Underline.ALWAYS and shown in (
                Shown.NORMAL,
                Shown.DISABLED,
            )
            draw_variant(variant, shown, f"show all##{variant.name}{shown.value}")
            imgui.same_line()
            _say("T" if real else "W", P.hint)
            for line in cell_label(cell(variant, shown, ground, alpha)):
                imgui.set_cursor_pos_x(x)
                _say(line, P.text_dim)
            bottom = max(bottom, imgui.get_cursor_pos_y())
        imgui.set_cursor_pos_y(bottom + 2.0)
        imgui.separator()
    imgui.end()


def draw_context_sheet() -> None:
    from pptmstr.theme import P
    from pptmstr.ui import widgets

    if not _sheet_window("link in context"):
        imgui.end()
        return
    ink = footer_ink()
    pane_ink = ink_of("accent")
    measured = _Shown.measured
    _say(
        f"in context · theme {_Shown.theme} · picked set: accent, underline always"
        " · in a block the accent as terminal_ink holds it (4.5:1 at least) on the block"
        f" ground ({hex_of(ink)},"
        f" {contrast_ratio(col(int(hex_of(ink)[1:], 16)), P.terminal_bg):.2f}:1 on"
        f" {hex_of(rgba(P.terminal_bg))})",
        P.text_strong,
    )
    imgui.dummy(imgui.ImVec2(0.0, 4.0))
    faces = app_faces()
    column = 740.0
    heights: dict[str, float] = {}
    top = imgui.get_cursor_pos_y()
    for n, (expanded, title) in enumerate(((False, "collapsed"), (True, "expanded"))):
        for m, as_link in enumerate((False, True)):
            imgui.set_cursor_pos(imgui.ImVec2(8.0 + m * column, top))
            imgui.begin_group()
            _say(
                f"(a) footer {title} · {'link' if as_link else 'today: small_button'}",
                P.text_strong,
            )
            imgui.begin_child(
                f"##col{n}{m}",
                imgui.ImVec2(column - 24.0, 0.0),
                imgui.ChildFlags_.auto_resize_y,
            )
            heights[f"{title} {'link' if as_link else 'button'}"] = draw_footer(
                faces, as_link=as_link, expanded=expanded, key=f"blk{n}{m}"
            )
            imgui.end_child()
            imgui.end_group()
        top = imgui.get_cursor_pos_y() + 4.0
        imgui.set_cursor_pos_y(top)
        saved = heights[f"{title} button"] - heights[f"{title} link"]
        _say(
            f"    block height {title}: small_button {heights[f'{title} button']:g} px,"
            f" link {heights[f'{title} link']:g} px -- saved {saved:g} px",
            P.text,
        )
        top = imgui.get_cursor_pos_y() + 6.0
    measured["footer"] = heights
    imgui.set_cursor_pos(imgui.ImVec2(8.0, top))
    imgui.separator()
    _say("(b) the marks, on the pane", P.text_strong)
    rows: dict[str, float] = {}
    rows["notice, today (?)"] = button_line("today")
    for mark in MARK_LABELS:
        rows[f"notice, link {mark}"] = mark_line(mark, pane_ink, f"n{mark}")
    for name, h in rows.items():
        _say(f"    {name}: row {h:g} px", P.text_dim)
    imgui.dummy(imgui.ImVec2(0.0, 4.0))
    _say("    launcher caption under the gate dial, in a dialog's style:", P.text_dim)
    caption: dict[str, float] = {}
    with widgets.dialog_style():
        imgui.push_style_color(imgui.Col_.child_bg, P.panel.vec4)
        imgui.begin_child(
            "##dialog",
            imgui.ImVec2(1100.0, 0.0),
            imgui.ChildFlags_.auto_resize_y | imgui.ChildFlags_.always_use_window_padding,
        )
        caption["caption, today (?)"] = launcher_caption(None, pane_ink, "today")
        for mark in MARK_LABELS:
            caption[f"caption, link {mark}"] = launcher_caption(mark, pane_ink, f"c{mark}")
        imgui.end_child()
        imgui.pop_style_color()
    for name, h in caption.items():
        _say(f"    {name}: row {h:g} px", P.text_dim)
    measured["marks"] = {**rows, **caption}
    imgui.separator()
    _say(
        "(c) baseline: link label baseline minus its line's (last row: the button's minus the"
        " link's)",
        P.text_strong,
    )
    imgui.begin_child("##baselines", imgui.ImVec2(700.0, 0.0), imgui.ChildFlags_.auto_resize_y)
    found = baselines(pane_ink)
    imgui.end_child()
    imgui.same_line()
    imgui.begin_group()
    for case in BASELINE_CASES:
        _say(f"{case}: {found[case]:+g} px", P.text)
    imgui.end_group()
    measured["baselines"] = found
    imgui.end()


def draw_sheet(name: str) -> None:
    if name == "":
        draw_variants_sheet()
    else:
        draw_context_sheet()


# -- the run ------------------------------------------------------------------------


def _scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    import screenshot
    from imgui_bundle import hello_imgui

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
    for suffix in SHEETS:
        _Shown.sheet = suffix
        yield from screenshot._frames(10)
        yield f"{_Shown.theme}{suffix}"
    _Shown.sheet = None
    print(json.dumps({_Shown.theme: _Shown.measured}, indent=1, default=str), flush=True)


def _render(out_dir: Path, theme_name: str) -> int:
    import screenshot

    from pptmstr import app as pptmstr_app
    from pptmstr import settings as settings_mod

    real_read = settings_mod.read

    @functools.wraps(real_read)
    def read(path: Path | None = None) -> Any:
        found = real_read(path)
        return dataclasses.replace(
            found, settings=dataclasses.replace(found.settings, theme=theme_name)
        )

    settings_mod.read = read  # type: ignore[assignment]
    real_overlays = pptmstr_app._draw_overlays

    def overlays(state: Any) -> None:
        real_overlays(state)
        if _Shown.sheet is not None:
            draw_sheet(_Shown.sheet)

    pptmstr_app._draw_overlays = overlays  # type: ignore[assignment]
    _Shown.theme = theme_name
    screenshot.SCENARIOS[SCENARIO] = _scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.argv = [
        "screenshot.py",
        "--scenario",
        SCENARIO,
        "--out-dir",
        str(out_dir),
        "--argv",
        "--fake",
    ]
    return int(screenshot.main())


def _on_xvfb(out_dir: Path, themes: tuple[str, ...], root: Path) -> int:
    worst = 0
    for name in themes:
        env = dict(os.environ)
        env["XVFB_SHOT_SCREEN"] = f"{SHEET_SIZE[0] + 100}x{SHEET_SIZE[1] + 100}x24"
        started = time.monotonic()
        done = subprocess.run(
            [
                str(root / "scripts" / "xvfb_shot.sh"),
                "--",
                str(root / ".venv" / "bin" / "python"),
                str(root / "scripts" / "link_probe.py"),
                "--render",
                "--theme",
                name,
                "--out-dir",
                str(out_dir),
            ],
            cwd=root,
            env=env,
        )
        print(f"{name}: exit {done.returncode} in {time.monotonic() - started:.0f}s", flush=True)
        missing = [s for s in SHEETS if not (out_dir / f"{name}{s}.png").is_file()]
        if missing or done.returncode:
            print(f"{name}: missing {missing}", file=sys.stderr)
            worst = worst or done.returncode or 1
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="every theme, each on its own Xvfb")
    which.add_argument("--render", action="store_true", help="one theme, on the display there is")
    which.add_argument("--describe", action="store_true", help="print every variant's values")
    ap.add_argument("--out-dir", type=Path, default=OUT)
    ap.add_argument("--theme", action="append", choices=THEMES)
    ap.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="the tree whose app and scripts --all runs (a `git archive` export)",
    )
    args = ap.parse_args()
    themes = tuple(args.theme or THEMES)
    if args.describe:
        for name in themes:
            theme.set_theme(name)
            print(name)
            for line in describe():
                print(f"  {line}")
        return 0
    if args.all:
        return _on_xvfb(args.out_dir.resolve(), themes, args.root.resolve())
    return _render(args.out_dir, themes[0])


if __name__ == "__main__":
    raise SystemExit(main())
