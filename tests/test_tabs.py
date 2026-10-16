"""
The drawn pane tabs (``pptmstr.ui.tabs``): their geometry, the size their text is
bound at, and that the pane names every lookup uses are left as they were.

The saved-layout tests run hello_imgui on its null backends in a child process,
twice over one ini, because what is at stake is what the next launch reads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr import app
from pptmstr.ui import tabs

ROOT = Path(__file__).resolve().parent.parent


def _panels() -> dict[str, Any]:
    names = ("FLEET", "NEEDS YOU", "CONTEXT", "DETAIL", "BOARD", "DIGEST", "BRIEF")
    return {name: (lambda: None) for name in (*names, "SESSION", "HEALTH", "LOG")}


def _layouts() -> list[Any]:
    return [app._triage_layout(_panels()), app._focus_layout(_panels())]


# -- geometry ------------------------------------------------------------------------


BAR = tabs.Rect(100.0, 30.0, 400.0, 58.0)  # a 28 px lane: 16 px font, frame_padding.y 6


def test_a_tab_is_its_rectangle_less_half_the_gap_each_side_inset_from_the_top() -> None:
    box = tabs.tab_box(BAR, 20.0, 78.0, 0.0)
    assert box == tabs.Rect(122.0, 31.0, 196.0, 58.0)
    assert box.h == 27.0
    # Scrolling moves every tab left by what the bar is scrolled.
    assert tabs.tab_box(BAR, 20.0, 78.0, 15.0).x0 == 107.0


def test_a_tab_mid_scroll_sits_where_imgui_truncates_it() -> None:
    """``IM_TRUNC(offset - scroll)``: toward zero, so a tab scrolled off left goes up."""
    assert tabs.tab_x(BAR, 20.0, 10.5) == 109.0
    assert tabs.tab_x(BAR, 5.0, 10.5) == 95.0
    assert tabs.tab_box(BAR, 20.0, 78.0, 10.5).x0 == 111.0
    assert tabs.close_button(BAR, 20.0, 78.0, 10.5, (9.0, 6.0), 16.0).x0 == 162.0


def test_two_neighbouring_tabs_are_gap_plus_imgui_inner_spacing_apart() -> None:
    inner = 4.0  # item_inner_spacing.x, which ImGui leaves between a dock's tab rectangles
    first = tabs.tab_box(BAR, 0.0, 78.0, 0.0)
    second = tabs.tab_box(BAR, 78.0 + inner, 94.0, 0.0)
    assert second.x0 - first.x1 == tabs.GAP + inner == 8.0


def test_15px_text_sits_with_equal_margins_in_the_27px_box() -> None:
    box = tabs.tab_box(BAR, 0.0, 78.0, 0.0)
    x, y = tabs.text_origin(box, 15.0)
    assert x == box.x0 + tabs.PAD_X
    assert y - box.y0 == 6.0
    assert box.y1 - (y + 15.0) == 6.0


def test_the_text_origin_is_on_whole_pixels() -> None:
    box = tabs.Rect(10.5, 20.25, 90.5, 47.75)
    x, y = tabs.text_origin(box, 15.0)
    assert x.is_integer() and y.is_integer()


def test_the_close_button_is_where_imgui_puts_it() -> None:
    close = tabs.close_button(BAR, 20.0, 78.0, 0.0, (9.0, 6.0), 16.0)
    # Rectangle 120..198: 9 in from the right, 16 wide, 6 down.
    assert close == tabs.Rect(173.0, 36.0, 189.0, 52.0)
    # Narrower than the padding and the button: at the rectangle's left edge.
    assert tabs.close_button(BAR, 20.0, 10.0, 0.0, (9.0, 6.0), 16.0).x0 == 120.0


@pytest.mark.parametrize(
    ("has", "visible", "hovered", "least_sel", "least_unsel", "width", "shown"),
    [
        (True, True, False, -1.0, 0.0, 78.0, True),  # the shown pane's tab: always
        (True, False, False, -1.0, 0.0, 78.0, False),  # another tab: not at rest
        (True, False, True, -1.0, 0.0, 78.0, True),  # another tab: on hover
        (True, False, True, -1.0, 0.0, 10.0, False),  # hovered, narrower than the button
        (False, True, True, -1.0, 0.0, 78.0, False),  # a pane that cannot be closed
        (True, True, False, 0.0, 0.0, 78.0, False),  # a style that hides it at rest
    ],
)
def test_the_close_button_shows_when_imgui_shows_it(
    has: bool,
    visible: bool,
    hovered: bool,
    least_sel: float,
    least_unsel: float,
    width: float,
    shown: bool,
) -> None:
    assert tabs.shows_close(has, visible, hovered, least_sel, least_unsel, width, 16.0) is shown


def test_a_label_that_does_not_fit_is_cut_with_an_ellipsis() -> None:
    def measure(s: str) -> float:
        return 7.0 * len(s)

    assert tabs.fit("board", 35.0, measure) == "board"
    assert tabs.fit("board", 34.0, measure) == "boa" + tabs.ELLIPSIS
    assert tabs.fit("board", 7.0, measure) == tabs.ELLIPSIS
    assert tabs.fit("board", 6.0, measure) == ""


@pytest.mark.parametrize(
    ("selected", "focused", "hovered", "state"),
    [
        (True, True, False, "focused"),
        (True, True, True, "focused"),
        (True, False, False, "selected"),
        (True, False, True, "hovered"),
        (False, True, True, "hovered"),
        (False, False, False, "unselected"),
    ],
)
def test_state_of(selected: bool, focused: bool, hovered: bool, state: str) -> None:
    assert tabs.state_of(selected, focused, hovered) == state


def test_every_state_names_imgui_colour_slots(imgui: Any) -> None:
    for fill, ink in tabs.STATE_COLOURS.values():
        assert hasattr(imgui.Col_, fill) and (ink is None or hasattr(imgui.Col_, ink))


def test_an_unselected_tab_is_labelled_in_the_tab_ink_and_not_text_disabled() -> None:
    from pptmstr import theme

    assert tabs.STATE_COLOURS["unselected"][1] is None
    assert tabs._ink(None) == theme.tab_ink().u32


# -- labels: lowercase on the tab, the name unchanged ---------------------------------


def test_the_tab_shows_the_name_lowercased_up_to_a_double_hash() -> None:
    assert tabs.display_label("NEEDS YOU") == "needs you"
    assert tabs.display_label("BOARD") == "board"
    assert tabs.display_label("Debug##Default") == "debug"


def test_no_pane_is_named_with_a_triple_hash_id() -> None:
    """
    hello_imgui compares a pane's whole label to the names Dear ImGui writes, which
    drop everything before ``###``: such a label rebuilds the layout every launch
    (``test_a_triple_hash_label_loses_a_dragged_pane_on_the_next_launch``).
    """
    for layout in _layouts():
        for window in layout.dockable_windows:
            assert "##" not in window.label
            assert window.label == window.label.upper()


def test_every_name_the_app_looks_a_pane_up_by_is_a_declared_pane() -> None:
    labels = {w.label for layout in _layouts() for w in layout.dockable_windows}
    assert set(app._REVIEW_PANES) <= labels
    assert set(app._LOG_PANES) <= labels
    assert set(_panels()) == labels


def test_every_focus_call_finds_its_pane() -> None:
    """
    ``begin_frame`` hands ``pending_pane`` to ``focus_dockable_window``, which
    matches the whole label; HEALTH is the one name set, and it is FOCUS's.
    """
    triage, focus = _layouts()
    assert focus.dockable_window_of_name("HEALTH") is not None
    for layout in (triage, focus):
        for window in layout.dockable_windows:
            assert layout.dockable_window_of_name(window.label) is not None


def test_app_sets_no_pending_pane_but_health() -> None:
    source = (ROOT / "pptmstr" / "app.py").read_text(encoding="utf-8")
    sets = [line.strip() for line in source.splitlines() if "pending_pane = " in line]
    assert sets == [
        "pane, state.pending_pane = state.pending_pane, None",
        'state.pending_pane = "HEALTH"',
    ]


# -- drawn in a frame -----------------------------------------------------------------


def _dock_frames(im: Any, at_last: Any, names: tuple[str, ...] = ("SESSION", "BOARD")) -> None:
    io = im.get_io()
    flags = io.config_flags
    io.config_flags |= im.ConfigFlags_.docking_enable.value
    try:
        for frame in range(4):
            im.new_frame()
            dock = im.dock_space_over_viewport()
            for name in names:
                im.set_next_window_dock_id(dock)
                im.begin(name, True)
                im.end()
            if frame == 3:
                at_last()
            im.render()
    finally:
        io.config_flags = flags


def test_the_label_is_bound_and_drawn_at_exactly_15px(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawn: list[tuple[float, float, str, bool]] = []

    def paint(dl: Any, tab: tabs.Tab, px: float, font: Any) -> None:
        drawn.append((px, float(imgui.get_font_size()), tab.label, tab.close is not None))

    monkeypatch.setattr(tabs, "_paint", paint)
    _dock_frames(imgui, tabs.draw)
    assert sorted(label for _, _, label, _ in drawn) == ["board", "session"]
    # The size handed to add_text is the size bound while it draws, and 15.
    assert {(px, bound) for px, bound, _, _ in drawn} == {(15.0, 15.0)}
    # The shown pane's tab carries its close button; the other does not at rest.
    assert sorted(close for *_, close in drawn) == [False, True]


def test_imgui_bakes_the_tab_font_at_15px(imgui: Any) -> None:
    sizes: list[float] = []

    def at_last() -> None:
        sizes.append(tabs.text_px())
        imgui.push_font(None, tabs.TEXT_PX)
        sizes.append(float(imgui.get_font().get_font_baked(imgui.get_font_size()).size))
        imgui.pop_font()
        sizes.append(float(imgui.get_font_size()))

    _dock_frames(imgui, at_last)
    # Bound at 15, a bake of exactly 15, and the size popped back afterwards.
    assert sizes == [15.0, 15.0, 16.0]


def test_drawing_leaves_the_font_size_as_it_found_it(imgui: Any) -> None:
    sizes: list[float] = []

    def at_last() -> None:
        sizes.append(float(imgui.get_font_size()))
        tabs.draw()
        sizes.append(float(imgui.get_font_size()))

    _dock_frames(imgui, at_last)
    assert sizes == [16.0, 16.0]


def test_the_label_glyphs_land_on_whole_pixels(imgui: Any) -> None:
    out: list[tuple[float, float]] = []

    def at_last() -> None:
        dl = imgui.get_foreground_draw_list()
        imgui.push_font(None, tabs.TEXT_PX)
        try:
            tab = tabs.Tab(tabs.Rect(10.5, 21.0, 90.5, 48.0), "session", "selected", None, False)
            start = dl.vtx_buffer.size()
            tabs._paint(dl, tab, float(imgui.get_font_size()), imgui.get_font())
            rect = dl.vtx_buffer.size()
            dl.add_rect_filled(imgui.ImVec2(10.5, 21.0), imgui.ImVec2(90.5, 48.0), 0xFF000000, 5.0)
            rect = dl.vtx_buffer.size() - rect
            end = start + rect + 4 * len("session")
            out.extend(
                (dl.vtx_buffer[i].pos.x, dl.vtx_buffer[i].pos.y) for i in range(start + rect, end)
            )
        finally:
            imgui.pop_font()

    old = imgui.get_style().tab_rounding
    imgui.get_style().tab_rounding = 5.0
    try:
        _dock_frames(imgui, at_last)
    finally:
        imgui.get_style().tab_rounding = old
    assert out
    assert all(float(x).is_integer() and float(y).is_integer() for x, y in out)


@pytest.mark.parametrize("dpi", [1.0, 1.25, 1.5])
def test_the_label_is_bound_at_15px_times_the_display_scale_on_whole_pixels(
    imgui: Any, monkeypatch: pytest.MonkeyPatch, dpi: float
) -> None:
    """
    ``push_font(None, 15)`` binds ImGui's ``IM_ROUND(15 × scale)``: 15, 19 and 23
    (22.5 rounds up, as ImGui rounds, where Python's ``round`` gives 22). The
    operator's 15 px judgement was made at scale 1.
    """
    from pptmstr.ui.typeset import gate

    drawn: list[tuple[float, float, float]] = []

    def paint(dl: Any, tab: tabs.Tab, px: float, font: Any) -> None:
        bound = float(imgui.get_font_size())
        drawn.append((px, bound, float(imgui.get_font().get_font_baked(bound).size)))

    monkeypatch.setattr(tabs, "_paint", paint)
    style = imgui.get_style()
    old = style.font_scale_dpi
    style.font_scale_dpi = dpi
    try:
        _dock_frames(imgui, tabs.draw)
    finally:
        style.font_scale_dpi = old
    want = {1.0: 15.0, 1.25: 19.0, 1.5: 23.0}[dpi]
    assert gate.drawn_size(tabs.TEXT_PX, 1.0, dpi) == want
    assert set(drawn) == {(want, want, want)}


# -- what a click on a drawn tab does -------------------------------------------------


class _Bar:
    """
    SESSION and BOARD docked together, driven a frame at a time with real mouse
    events, recording what ``tabs.draw`` would paint and what ImGui hovers.
    """

    def __init__(self, im: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.im = im
        self.open = {"SESSION": True, "BOARD": True}
        self.drawn: dict[str, tabs.Tab] = {}
        self.hovered_id = 0
        self.ids: dict[str, tuple[int, int]] = {}
        self.selected = ""
        self.hovered_window = ""
        self.host = ""
        monkeypatch.setattr(tabs, "_paint", lambda dl, tab, px, font: self._paint(tab))
        for _ in range(3):
            self.frame()

    def _paint(self, tab: tabs.Tab) -> None:
        self.drawn[tab.label] = tab

    def frame(self, mouse: tuple[float, float] | None = None, down: bool | None = None) -> None:
        im = self.im
        io = im.get_io()
        if mouse is not None:
            io.add_mouse_pos_event(*mouse)
        if down is not None:
            io.add_mouse_button_event(0, down)
        flags = io.config_flags
        io.config_flags |= im.ConfigFlags_.docking_enable.value
        try:
            im.new_frame()
            dock = im.dock_space_over_viewport()
            for name in ("SESSION", "BOARD"):
                if self.open[name]:
                    im.set_next_window_dock_id(dock)
                    self.open[name] = bool(im.begin(name, True)[1])
                    im.end()
            self.drawn = {}
            tabs.draw()
            ctx = im.get_current_context()
            self.hovered_id = int(ctx.hovered_id)
            self.hovered_window = "" if ctx.hovered_window is None else ctx.hovered_window.name
            node = next(w.dock_node for w in ctx.windows if w.dock_node is not None)
            self.host = node.host_window.name
            self.ids = {
                str(t.window.name): (int(t.id_), tabs.close_id(int(t.window.id_)))
                for t in node.tab_bar.tabs
            }
            self.selected = next(
                (
                    str(t.window.name)
                    for t in node.tab_bar.tabs
                    if int(t.id_) == int(node.tab_bar.selected_tab_id)
                ),
                "",
            )
            im.render()
        finally:
            io.config_flags = flags

    def click(self, at: tuple[float, float]) -> None:
        self.frame(at)
        self.frame(at)
        self.frame(down=True)
        self.frame(down=False)
        self.frame()
        self.frame()


def _middle(box: tabs.Rect) -> tuple[float, float]:
    return (box.x0 + box.w * 0.5, box.y0 + box.h * 0.5)


@pytest.fixture
def bar(imgui: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Bar]:
    driven = _Bar(imgui, monkeypatch)
    yield driven
    # Off the bar, and every pane back, so the next test starts from the same two.
    imgui.get_io().add_mouse_pos_event(-100.0, -100.0)
    driven.open = {"SESSION": True, "BOARD": True}
    for _ in range(3):
        driven.frame()


def _unselected(bar: _Bar) -> tuple[str, tabs.Tab]:
    """The pane whose tab is not selected (which one is the ini's), and its tab."""
    (name,) = {"SESSION", "BOARD"} - {bar.selected}
    return name, bar.drawn[name.lower()]


def test_a_hovered_unselected_tab_looks_hovered_and_shows_the_close_imgui_arms(
    bar: _Bar,
) -> None:
    name, tab = _unselected(bar)
    assert tab.state == "unselected" and tab.close is None
    bar.frame(_middle(tab.box))
    bar.frame()
    tab = bar.drawn[name.lower()]
    # Over a dock's tab bar the hovered window is the dock's host, not the pane.
    assert bar.hovered_window == bar.host
    assert bar.hovered_id == bar.ids[name][0]
    assert tab.state == "hovered"
    # ImGui shows (and arms) an unselected tab's close button on hover: so is it drawn.
    assert tab.close is not None and not tab.close_hovered
    bar.frame(_middle(tab.close))
    bar.frame()
    assert bar.hovered_id == bar.ids[name][1]
    tab = bar.drawn[name.lower()]
    assert tab.close is not None and tab.close_hovered


def test_wherever_imgui_would_take_a_click_as_a_close_a_cross_is_drawn_there(
    bar: _Bar,
) -> None:
    """Swept a pixel at a time along the lane, across both tabs."""
    boxes = [tab.box for tab in bar.drawn.values()]
    y = _middle(boxes[0])[1]
    unselected, _ = _unselected(bar)
    closes: set[str] = set()
    for x in range(int(min(b.x0 for b in boxes)) - 4, int(max(b.x1 for b in boxes)) + 4):
        bar.frame((x + 0.5, y))
        for name, (tab_id, close) in bar.ids.items():
            tab = bar.drawn[name.lower()]
            if bar.hovered_id == close:
                closes.add(name)
                assert tab.close is not None and tab.close.contains(x + 0.5, y), (name, x)
                assert tab.close_hovered
            if bar.hovered_id == tab_id and name == unselected:
                assert tab.state == "hovered", x
    assert closes == {"SESSION", "BOARD"}


def test_a_click_in_the_middle_of_an_unselected_tab_selects_it_and_closes_nothing(
    bar: _Bar,
) -> None:
    name, tab = _unselected(bar)
    bar.click(_middle(tab.box))
    assert bar.open == {"SESSION": True, "BOARD": True}
    assert bar.selected == name


def test_a_click_on_an_unselected_tabs_drawn_cross_closes_that_pane(bar: _Bar) -> None:
    name, tab = _unselected(bar)
    bar.frame(_middle(tab.box))
    bar.frame()
    close = bar.drawn[name.lower()].close
    assert close is not None
    bar.click(_middle(close))
    assert bar.open == {"SESSION": True, "BOARD": True, name: False}


def test_a_tab_mid_scroll_is_drawn_on_imgui_s_hit_box(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A crowded bar held at a fractional scroll: the edge of ImGui's rectangle for a
    tab is where the drawn one puts it, to the quarter pixel either side.
    """
    names = tuple(f"PANE{n}" for n in range(8))
    io = imgui.get_io()
    size, flags = io.display_size, io.config_flags
    drawn: dict[str, tabs.Tab] = {}
    monkeypatch.setattr(tabs, "_paint", lambda dl, tab, px, font: drawn.__setitem__(tab.label, tab))

    def frame(mouse: tuple[float, float] | None = None) -> tuple[float, int, int]:
        if mouse is not None:
            io.add_mouse_pos_event(*mouse)
        imgui.new_frame()
        dock = imgui.dock_space_over_viewport()
        for name in names:
            imgui.set_next_window_dock_id(dock)
            imgui.begin(name, True)
            imgui.end()
        ctx = imgui.get_current_context()
        node = next(w.dock_node for w in ctx.windows if w.dock_node is not None)
        tabs.draw()
        bar = node.tab_bar
        used = float(bar.scrolling_anim)
        tab = next(t for t in bar.tabs if t.window.name == "PANE3")
        # Held for the next frame's layout, which is what ImGui hit-tests against.
        bar.scrolling_anim = bar.scrolling_target = 10.5
        imgui.render()
        return used, int(ctx.hovered_id), int(tab.id_)

    io.display_size = imgui.ImVec2(300.0, 400.0)
    io.config_flags |= imgui.ConfigFlags_.docking_enable.value
    try:
        for _ in range(6):
            frame()
        edge = drawn["pane3"].box.x0 - tabs.GAP * 0.5
        y = _middle(drawn["pane3"].box)[1]
        frame((edge - 0.25, y))
        used, before, tab_id = frame()
        frame((edge + 0.25, y))
        _, after, _ = frame()
    finally:
        io.display_size, io.config_flags = size, flags
        io.add_mouse_pos_event(-100.0, -100.0)
    assert used == 10.5
    assert before != tab_id
    assert after == tab_id


def test_a_painter_that_raises_leaves_every_overlay_drawn(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_draw_overlays`` guards the tabs on their own: the launcher still comes next."""
    from pptmstr.bridge import Bridge
    from pptmstr.settings import Settings
    from pptmstr.store import Store
    from pptmstr.ui import launcher

    class Reached(Exception):
        pass

    def broken() -> None:
        raise RuntimeError("painter")

    def stop(state: object) -> None:
        raise Reached

    monkeypatch.setattr(tabs, "draw", broken)
    monkeypatch.setattr(launcher, "handle_shortcut", stop)
    monkeypatch.setattr(app, "_PANEL_ERRORS", {})
    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    reached: list[bool] = []

    def at_last() -> None:
        try:
            app._draw_overlays(state)
        except Reached:
            reached.append(True)

    _dock_frames(imgui, at_last)
    assert reached == [True]
    assert app._PANEL_ERRORS == {"pane tabs": "RuntimeError: painter"}


# -- the saved layout, across a launch ------------------------------------------------


def _child(ini: str, relabel: bool, move: bool, layout: str) -> None:
    """
    One launch over ``ini`` in ``layout``; prints where BOARD and FLEET are docked.
    Both layouts put BOARD in a node of its own and FLEET in the rail.
    """
    from imgui_bundle import hello_imgui, imgui

    layouts = _layouts()
    if layout == app.FOCUS:
        layouts.reverse()
    if relabel:
        for layout in layouts:
            for window in layout.dockable_windows:
                window.label = f"{window.label.lower()}###{window.label}"
    name = (lambda n: f"{n.lower()}###{n}") if relabel else (lambda n: n)
    runner = hello_imgui.RunnerParams()
    runner.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    )
    runner.docking_params = layouts[0]
    runner.alternative_docking_layouts = layouts[1:]
    runner.remember_selected_alternative_layout = True
    runner.ini_folder_type = hello_imgui.IniFolderType.absolute_path
    runner.ini_filename = ini
    runner.platform_backend_type = hello_imgui.PlatformBackendType.null
    runner.renderer_backend_type = hello_imgui.RendererBackendType.null
    runner.fps_idling.enable_idling = False
    frame = [0]
    seen: dict[str, int] = {}

    def fonts() -> None:
        imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures

    def dock_of(n: str) -> int:
        window = imgui.internal.find_window_by_name(name(n))
        return -1 if window is None else int(window.dock_id)

    def pre() -> None:
        frame[0] += 1
        if frame[0] == 10 and move:
            imgui.internal.dock_builder_dock_window(name("BOARD"), dock_of("FLEET"))
        if frame[0] == 20:
            seen.update(BOARD=dock_of("BOARD"), FLEET=dock_of("FLEET"))
            seen.update(focus=int(runner.docking_params.layout_name == app.FOCUS))
        if frame[0] == 25:
            runner.app_shall_exit = True

    runner.callbacks.load_additional_fonts = fonts
    runner.callbacks.pre_new_frame = pre
    hello_imgui.run(runner)
    print(json.dumps(seen))


def _launch(ini: Path, relabel: bool, move: bool, layout: str) -> dict[str, int]:
    done = subprocess.run(
        [sys.executable, __file__, str(ini), str(int(relabel)), str(int(move)), layout],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    seen: dict[str, int] = json.loads(done.stdout.strip().splitlines()[-1])
    return seen


def _dragged_then_relaunched(
    tmp_path: Path, relabel: bool, layout: str = app.TRIAGE
) -> tuple[dict[str, int], dict[str, int]]:
    pytest.importorskip("imgui_bundle")
    ini = tmp_path / "pptmstr.ini"
    dragged = _launch(ini, relabel, move=True, layout=layout)
    return dragged, _launch(ini, relabel, move=False, layout=layout)


@pytest.mark.parametrize("layout", [app.TRIAGE, app.FOCUS])
def test_a_dragged_pane_stays_where_it_was_dragged_on_the_next_launch(
    tmp_path: Path, layout: str
) -> None:
    dragged, next_launch = _dragged_then_relaunched(tmp_path, relabel=False, layout=layout)
    assert dragged["focus"] == int(layout == app.FOCUS)
    assert dragged["BOARD"] == dragged["FLEET"] > 0
    assert next_launch == dragged


def test_a_triple_hash_label_loses_a_dragged_pane_on_the_next_launch(tmp_path: Path) -> None:
    """
    Why the panes keep their names: the same drag under "board###BOARD" labels is
    rebuilt from code on the next launch. If this starts failing, hello_imgui
    has begun to match a label by its id and the relabel is open again.
    """
    dragged, next_launch = _dragged_then_relaunched(tmp_path, relabel=True)
    assert dragged["BOARD"] == dragged["FLEET"] > 0
    assert next_launch["BOARD"] != next_launch["FLEET"]


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    _child(sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1", sys.argv[4])
