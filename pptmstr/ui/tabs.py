"""
Every dock's pane tabs drawn again over ImGui's own: inset into the lane, rounded
on all four corners, the label lowercase at ``TEXT_PX``.

The operator picked "pad 10x4 gap 4 inset 1" with the text at 15 px and the
default rasterisation (planning/2026-10-15-ui-style-probe.md, "Decisions
answered in the operator's pane"): 15 px is the size whose stems land whole
under the font's own hinting (notes/2026-10-16-imgui-text-and-the-pixel-grid.md).

**Painted over, not replaced.** ImGui gives a dock tab bar no draw hook, but it
keeps where each tab is (``TabBar.bar_rect``, ``TabItem.offset`` and ``width``),
so ``draw`` paints in the dock's host window's draw list after every window has
ended. That list is rendered in the host's z-order, so a modal drawn over the docks
still covers the tabs (the foreground list would not). Input is untouched: ImGui
hit-tests its own tabs, close buttons and scroll arrows, which lie exactly under
what is drawn here, and the close button is drawn again where ImGui put it.

**The label is drawn lowercase; the window keeps its name.** A ``"board###BOARD"``
label would have kept ImGui's window settings, but not hello_imgui's: it tests
whether a layout is saved by comparing the whole label to the ``[Window][...]``
names in the ini, and Dear ImGui writes those names without the part before
``###``. Measured (null backends, a copy of a saved ini): every launch rebuilt
both layouts from code. So the panes keep their upper-case names, which every
``focus_dockable_window`` call and the saved layouts use, and the case is a
matter of what this module draws. Inconsolata is monospace, so ImGui sizes a tab
for the same width either way. A floating pane's title bar and the View menu
still read the name as it is.

**Geometry on a dock.** The lane is ImGui's: the UI font plus
``2 * frame_padding.y`` (28 px under the theme's 16 px font and 9x6 padding).
The tab is its rectangle less ``GAP / 2`` at each side, ``INSET`` below the
lane's top and standing on the pane's top edge, so a 27 px box. ImGui lays the
rectangles ``item_inner_spacing.x`` apart, so the visible gap is ``GAP`` plus
that. 15 px text centred in 27 px is 6 px above and 6 below, the even split that
18 px (4.5 each, truncated to 4 and 5) could not give. The text starts
``PAD_X`` in from the drawn tab's left edge; ``PAD_Y`` is what the pick asked for
and the lane cannot give, since nothing drawn here makes ImGui's bar taller.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from imgui_bundle import imgui

from pptmstr import theme

PAD_X = 10.0
"""From the drawn tab's left edge to its text."""
PAD_Y = 4.0
"""The pick's vertical padding; on a dock the lane decides it (module docstring)."""
GAP = 4.0
"""Taken off a tab's rectangle, half at each side."""
INSET = 1.0
"""From the lane's top to every tab's top."""
TEXT_PX = 15.0
"""The size the label is bound and baked at: whole pixels only."""
ACCENT_RULE = 2.0
"""The focused tab's accent rule along its foot."""

State = Literal["focused", "selected", "hovered", "unselected"]

# A state's fill and ink, as ImGui colour slots, so a theme's tab colours are
# what is drawn. ``selected`` is the selected tab of a pane that is not focused,
# which is how every pane but one reads. An ink of ``None`` is ``theme.tab_ink()``,
# which no ImGui slot holds: ``text_disabled`` is under 4.5:1 on dark's and
# ghost's tab fill.
STATE_COLOURS: dict[State, tuple[str, str | None]] = {
    "focused": ("tab_selected", "text"),
    "selected": ("tab_dimmed_selected", "text"),
    "hovered": ("tab_hovered", "text"),
    "unselected": ("tab", None),
}

ELLIPSIS = "…"


@dataclass(frozen=True, slots=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def contains(self, x: float, y: float) -> bool:
        return self.x0 <= x < self.x1 and self.y0 <= y < self.y1


def display_label(name: str) -> str:
    """
    What a tab shows for a window named ``name``: the part ImGui shows (up to
    ``##``), lowercased.
    """
    return name.split("##", 1)[0].lower()


def tab_x(bar: Rect, offset: float, scroll: float) -> float:
    """
    The left edge of ImGui's rectangle for the tab at ``offset`` in a bar scrolled
    by ``scroll`` (``TabBarGetTabPos``): ``offset - scroll`` truncated toward zero,
    as ``IM_TRUNC`` does, so a scroll animation's fractional step moves the drawn
    tab with ImGui's hit box and not a fraction beside it.
    """
    return bar.x0 + float(int(offset - scroll))


def tab_box(bar: Rect, offset: float, width: float, scroll: float) -> Rect:
    """
    The drawn tab for ImGui's tab at ``offset``/``width`` in a bar scrolled by
    ``scroll``: its rectangle less ``GAP / 2`` at each side, ``INSET`` below the
    lane's top, and on the lane's foot.
    """
    x = tab_x(bar, offset, scroll)
    return Rect(x + GAP * 0.5, bar.y0 + INSET, x + width - GAP * 0.5, bar.y1)


def close_button(
    bar: Rect,
    offset: float,
    width: float,
    scroll: float,
    frame_pad: tuple[float, float],
    font_px: float,
) -> Rect:
    """
    Where ImGui puts a tab's close button (``TabItemLabelAndCloseButton``): a square
    the UI font's size, ``frame_pad.x`` in from the rectangle's right edge, at its
    left edge when the tab is narrower than that, and ``frame_pad.y`` down.
    """
    x0 = tab_x(bar, offset, scroll)
    x1 = x0 + width
    bx = max(x0, x1 - frame_pad[0] - font_px)
    by = bar.y0 + frame_pad[1]
    return Rect(bx, by, bx + font_px, by + font_px)


def shows_close(
    has_close: bool,
    visible: bool,
    hovered: bool,
    min_selected: float,
    min_unselected: float,
    width: float,
    font_px: float,
) -> bool:
    """
    Whether ImGui draws a tab's close button this frame: on the tab whose pane is
    shown when ``tab_close_button_min_width_selected`` is negative, otherwise when
    the tab is hovered and wide enough (ImGui's ``close_button_visible``).
    """
    if not has_close:
        return False
    least = min_selected if visible else min_unselected
    if least < 0.0:
        return True
    return hovered and width >= max(font_px, least)


def text_origin(box: Rect, text_px: float) -> tuple[float, float]:
    """
    Where the label's pen starts: ``PAD_X`` in, and centred top to bottom, both on
    whole pixels so the bake is drawn one to one.
    """
    return float(int(box.x0 + PAD_X)), float(int(box.y0 + (box.h - text_px) * 0.5))


def fit(text: str, room: float, measure: Callable[[str], float]) -> str:
    """
    ``text`` as it fits in ``room`` px: whole, or cut with an ellipsis where ImGui
    has shrunk a crowded bar's tabs below their labels. Nothing at all where not
    even the ellipsis fits.
    """
    if measure(text) <= room:
        return text
    for n in range(len(text) - 1, 0, -1):
        cut = text[:n] + ELLIPSIS
        if measure(cut) <= room:
            return cut
    return ELLIPSIS if measure(ELLIPSIS) <= room else ""


def state_of(selected: bool, focused: bool, hovered: bool) -> State:
    """A tab's look: hover shows on any tab but the focused pane's own."""
    if selected and focused:
        return "focused"
    if hovered:
        return "hovered"
    return "selected" if selected else "unselected"


@dataclass(frozen=True, slots=True)
class Tab:
    """One tab as it is drawn: what ImGui's bar holds, read into plain values."""

    box: Rect
    label: str
    state: State
    close: Rect | None
    close_hovered: bool


def _rect(r: Any) -> Rect:
    return Rect(float(r.min.x), float(r.min.y), float(r.max.x), float(r.max.y))


def _col(slot: str) -> int:
    return int(imgui.get_color_u32(getattr(imgui.Col_, slot)))


def _ink(slot: str | None) -> int:
    return theme.tab_ink().u32 if slot is None else _col(slot)


def _paint(dl: Any, tab: Tab, text_px: float, font: Any) -> None:
    style = imgui.get_style()
    fill, slot = STATE_COLOURS[tab.state]
    ink = _ink(slot)
    a, b = imgui.ImVec2(tab.box.x0, tab.box.y0), imgui.ImVec2(tab.box.x1, tab.box.y1)
    dl.add_rect_filled(a, b, _col(fill), style.tab_rounding)
    if tab.state == "focused":
        dl.add_rect_filled(
            imgui.ImVec2(tab.box.x0 + style.tab_rounding, tab.box.y1 - ACCENT_RULE),
            imgui.ImVec2(tab.box.x1 - style.tab_rounding, tab.box.y1),
            _col("check_mark"),
        )
    right = tab.close.x0 if tab.close is not None else tab.box.x1 - PAD_X * 0.5
    x, y = text_origin(tab.box, text_px)
    # Measured at the size pushed by ``draw``, which is the size drawn.
    text = fit(tab.label, right - x, lambda s: float(imgui.calc_text_size(s).x))
    if text:
        dl.push_clip_rect(a, imgui.ImVec2(max(right, a.x), b.y), True)
        try:
            dl.add_text(font, text_px, imgui.ImVec2(x, y), ink, text)
        finally:
            dl.pop_clip_rect()
    if tab.close is not None:
        _paint_close(dl, tab.close, tab.close_hovered, ink)


def _paint_close(dl: Any, r: Rect, hovered: bool, ink: int) -> None:
    """ImGui's ``CloseButton`` cross, and its hover disc, at ImGui's own place."""
    cx, cy = r.x0 + r.w * 0.5, r.y0 + r.h * 0.5
    if hovered:
        dl.add_circle_filled(imgui.ImVec2(cx, cy), max(2.0, r.w * 0.5), _col("button_hovered"))
    e = r.w * 0.5 * 0.7071 - 1.0
    dl.add_line(imgui.ImVec2(cx + e, cy + e), imgui.ImVec2(cx - e, cy - e), ink, 1.0)
    dl.add_line(imgui.ImVec2(cx + e, cy - e), imgui.ImVec2(cx - e, cy + e), ink, 1.0)


def close_id(window_id: int) -> int:
    """
    The id ImGui gives a docked pane's tab close button: ``"#CLOSE"`` hashed with
    the docked window's id as the seed. Measured on imgui 1.92.9; a later ImGui
    that seeds it otherwise fails ``test_a_hovered_unselected_tab_*``.
    """
    return int(imgui.internal.im_hash_str("#CLOSE", 0, window_id))


def bar_tabs(node: Any, frame: int, hovered_id: int, active_id: int, ui_px: float) -> list[Tab]:
    """
    The tabs of one dock node's bar as they are drawn this frame, read from ImGui's
    tab bar. ``hovered_id`` and ``active_id`` are ImGui's own for this frame, so a
    tab looks hovered, and its close button shows, exactly when ImGui's
    ``TabItemEx`` and ``TabItemLabelAndCloseButton`` decide so: not through a
    modal, not while another item is held, and over the close button too. ``ui_px``
    is the font size ImGui drew the bar at, which sizes its close button.
    """
    bar = node.tab_bar
    style = imgui.get_style()
    lane = _rect(bar.bar_rect)
    pad = (float(bar.frame_padding.x), float(bar.frame_padding.y))
    font_px = ui_px
    focused_node = node.last_frame_focused == frame
    scroll = float(bar.scrolling_anim)
    out: list[Tab] = []
    for item in bar.tabs:
        window = item.window
        if window is None:
            continue
        offset, width = float(item.offset), float(item.width)
        tab_id, close = int(item.id_), close_id(int(window.id_))
        # ImGui's tab colour: ``hovered || held`` from the tab's ButtonBehavior.
        hovered = tab_id in (hovered_id, active_id)
        # ImGui's ``is_hovered`` for the close button: the tab or its button.
        armed = hovered or close in (hovered_id, active_id)
        selected = tab_id == int(bar.selected_tab_id)
        visible = tab_id == int(bar.visible_tab_id)
        button = None
        if shows_close(
            bool(window.has_close_button),
            visible,
            armed,
            float(style.tab_close_button_min_width_selected),
            float(style.tab_close_button_min_width_unselected),
            width,
            font_px,
        ):
            button = close_button(lane, offset, width, scroll, pad, font_px)
        out.append(
            Tab(
                tab_box(lane, offset, width, scroll),
                display_label(str(window.name)),
                state_of(selected, focused_node, hovered),
                button,
                button is not None and close == hovered_id,
            )
        )
    return out


def text_px() -> float:
    """
    The size the label is drawn at: what ImGui binds for ``push_font(None,
    TEXT_PX)``, read back with ``get_font_size``. Needs a frame. ImGui binds and
    bakes whole pixels, and ``add_text`` at a size with no bake of its own would
    draw a neighbouring bake scaled.
    """
    imgui.push_font(None, TEXT_PX)
    try:
        return float(imgui.get_font_size())
    finally:
        imgui.pop_font()


def draw() -> None:
    """
    Paint every visible dock tab bar of this frame. Call after every window has
    ended (``post_render_dockable_windows``).
    """
    ctx = imgui.get_current_context()
    frame = imgui.get_frame_count()
    ui_px = float(imgui.get_font_size())
    imgui.push_font(None, TEXT_PX)
    try:
        _draw_bars(ctx, frame, ui_px, float(imgui.get_font_size()), imgui.get_font())
    finally:
        imgui.pop_font()


def _draw_bars(ctx: Any, frame: int, ui_px: float, px: float, font: Any) -> None:
    hovered_id, active_id = int(ctx.hovered_id), int(ctx.active_id)
    seen: set[int] = set()
    for window in ctx.windows:
        node = window.dock_node
        if node is None or int(node.id_) in seen:
            continue
        seen.add(int(node.id_))
        bar = node.tab_bar
        host = node.host_window
        # ImGui's IsVisible is a bitfield the binding does not reach; the frame
        # stamp beside it says the same of this frame.
        if bar is None or host is None or not len(bar.tabs) or node.last_frame_active != frame:
            continue
        if node.is_hidden_tab_bar() or node.is_no_tab_bar():
            continue
        lane = _rect(bar.bar_rect)
        if lane.w <= 0.0 or lane.h <= 0.0:
            continue
        tabs = bar_tabs(node, frame, hovered_id, active_id, ui_px)
        dl = host.draw_list
        # ``bar_rect`` stops short of the scroll arrows once ImGui shows them, so
        # they are left as ImGui drew them.
        dl.push_clip_rect(imgui.ImVec2(lane.x0, lane.y0), imgui.ImVec2(lane.x1, lane.y1), True)
        try:
            dl.add_rect_filled(
                imgui.ImVec2(lane.x0, lane.y0), imgui.ImVec2(lane.x1, lane.y1), _col("title_bg")
            )
            for tab in tabs:
                _paint(dl, tab, px, font)
        finally:
            dl.pop_clip_rect()
