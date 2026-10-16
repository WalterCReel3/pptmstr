"""
SESSION's geometry: the scrollback sized from the composer under it, the log's side
margins, and a scrollbar that is seen only when there is something to scroll.

The arithmetic is pure and is tested as numbers. The rest is drawn in real frames on
the headless context ``tests/conftest.py`` builds, because what is being held is
where ImGui put things: whether the pane scrolled, how wide the text was given, how
far in it started.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from pptmstr import app
from pptmstr.intents import AgentSpawned, StateChanged
from pptmstr.model import AgentState, NodeId
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.transcript import SegmentKind, Transcript
from pptmstr.ui import transcript_pane, typeset
from pptmstr.ui.focus import OnNode
from pptmstr.ui.transcript_pane import RenderMode, TranscriptState

ROOT: NodeId = ("s1", None)
SUB: NodeId = ("s1", "agent-1")
PANE = (700.0, 500.0)
# A window of its own for each test: a child's scroll and content size are ImGui's,
# kept by id across frames, and a window reused would carry the last test's.
WINDOWS = itertools.count()


@pytest.fixture(autouse=True)
def seam_off_unless_asked(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """The seam off for every test that did not ask for it, as the pane's own tests do."""
    if "seam" not in request.fixturenames:
        monkeypatch.setattr(typeset, "enabled", lambda pane=None: False)


# -- the arithmetic ------------------------------------------------------------------


def test_the_scrollback_is_the_room_less_what_the_composer_took() -> None:
    assert app._scrollback_height(803.0, 163.0) == 640.0
    assert app._scrollback_height(803.0, 30.0) == 773.0, "a sub-agent's two lines take less"
    assert app._scrollback_height(150.0, 163.0) == app._SCROLLBACK_FLOOR, "a few lines at least"


@pytest.mark.parametrize(("text_px", "margin"), [(16.0, 8.0), (20.0, 10.0), (24.0, 12.0)])
def test_the_margin_is_an_en_dash_at_the_texts_size(text_px: float, margin: float) -> None:
    assert transcript_pane.margin_px(text_px) == margin


# -- the log, drawn --------------------------------------------------------------------


def session(transcript: Transcript, *, sub: bool = False) -> Store:
    store = Store()
    nodes = ((ROOT, None), (SUB, ROOT)) if sub else ((ROOT, None),)
    for node, parent in nodes:
        store.apply(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task="the session the test draws",
                model="claude-sonnet-5",
                started_at=0.0,
                cwd="/x",
                transcript=transcript,
            ),
            now=1.0,
        )
        store.apply(StateChanged(node, AgentState.AWAITING_INPUT), now=1.0)
    return store


def lines(count: int) -> Transcript:
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "".join(f"line {n}\n" for n in range(count)))
    return transcript


class Log:
    """
    The log in a window of its own, and what its child was like each frame: the
    width the text was given, where it started, and the scroll.
    """

    def __init__(self, im: Any, monkeypatch: pytest.MonkeyPatch, store: Store) -> None:
        self.im, self.store = im, store
        self.window = f"##geometry-log-{next(WINDOWS)}"
        self.state = TranscriptState()
        self.node = ROOT
        self.size = PANE
        self.seen: dict[str, Any] = {}
        real = transcript_pane._handle_scroll

        def handle_scroll(state: TranscriptState) -> None:
            real(state)
            window = im.internal.get_current_window()
            self.seen = {
                "left": window.pos.x,
                "right": window.pos.x + window.size.x,
                # Where a line starts: the padding and the indent past it.
                "text_left": window.pos.x + window.dc.indent.x,
                "text_right": window.content_region_rect.max.x,
                "scroll_max": im.get_scroll_max_y(),
                "scroll_max_x": im.get_scroll_max_x(),
                "scrollbar": window.scrollbar_y,
                "scrollbar_x": window.scrollbar_x,
                # The vertical bar's width, where it is drawn.
                "bar": window.scrollbar_sizes.x,
            }

        monkeypatch.setattr(transcript_pane, "_handle_scroll", handle_scroll)

    def frames(self, count: int) -> None:
        im = self.im
        for _ in range(count):
            typeset.pre_frame()
            im.new_frame()
            try:
                im.set_next_window_pos(im.ImVec2(10.0, 10.0))
                im.set_next_window_size(im.ImVec2(*self.size))
                im.begin(self.window)
                transcript_pane.draw(self.store.snapshot(), self.state, self.node)
                im.end()
            finally:
                im.render()


@pytest.fixture
def log(imgui: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    yield lambda store: Log(imgui, monkeypatch, store)


def test_with_nothing_to_scroll_there_is_no_bar_and_nothing_kept_for_one(log: Any) -> None:
    """
    No strip is reserved: a log that fits draws no bar, and its text is the log's
    width less a margin either side.
    """
    short = log(session(lines(3)))
    short.frames(3)
    assert short.seen["scroll_max"] == 0.0 and not short.seen["scrollbar"]
    assert short.seen["bar"] == 0.0
    side = transcript_pane.margin_px(short.im.get_font_size())
    width = short.seen["right"] - short.seen["left"]
    assert short.seen["text_right"] - short.seen["text_left"] == width - 2 * side


def test_the_bar_comes_once_the_text_overflows_and_the_text_rewraps_once(log: Any) -> None:
    """
    ImGui shows a window's bar the frame after its content outgrew it, and takes
    the bar's width from the text then: one change of width, and no more.
    """
    transcript = lines(3)
    view = log(session(transcript))
    view.frames(2)
    wide = view.seen["text_right"] - view.seen["text_left"]
    transcript.append(SegmentKind.OUTPUT, "".join(f"more {n}\n" for n in range(300)))
    widths, bars = [], []
    for _ in range(4):
        view.frames(1)
        widths.append(view.seen["text_right"] - view.seen["text_left"])
        bars.append(bool(view.seen["scrollbar"]))
    gutter = view.im.get_style().scrollbar_size
    assert bars == [False, True, True, True]
    assert widths == [wide] + [wide - gutter] * 3


def margins(view: Any) -> tuple[float, float]:
    """
    The margin seen either side of the text: from the log's left edge, and from
    the bar's left edge where there is a bar, or the log's right edge.
    """
    seen = view.seen
    return seen["text_left"] - seen["left"], seen["right"] - seen["bar"] - seen["text_right"]


@pytest.mark.parametrize("lines_drawn", [3, 300], ids=["fits", "overflows"])
def test_the_wrapped_log_has_the_same_margin_either_side(
    imgui: Any, log: Any, lines_drawn: int
) -> None:
    view = log(session(lines(lines_drawn)))
    view.frames(3)
    assert bool(view.seen["scrollbar"]) is (lines_drawn == 300)
    left, right = margins(view)
    assert left == right == transcript_pane.margin_px(imgui.get_font_size()) == 8.0


def ink(drawn: Any, view: Any) -> tuple[float, float]:
    """
    How far in from the log's left edge, and from the bar or the log's right
    edge, the first and last ink of what ``drawn`` holds is.
    """
    seen = view.seen
    low = min(rect[0] for rect in drawn)
    high = max(rect[1] for rect in drawn)
    return low - seen["left"], seen["right"] - seen["bar"] - high


@pytest.fixture
def block_rects(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    """Every tool block drawn this frame, as its left and right x."""
    from imgui_bundle import imgui

    from pptmstr.ui import tool_block

    seen: list[tuple[float, float]] = []
    real = tool_block.draw

    def spy(block: Any, **given: Any) -> bool:
        pressed = real(block, **given)
        seen.append((imgui.get_item_rect_min().x, imgui.get_item_rect_max().x))
        return pressed

    monkeypatch.setattr(tool_block, "draw", spy)
    return seen


@pytest.mark.parametrize("before", [0, 300], ids=["bar hidden", "bar shown"])
def test_a_tool_block_has_the_same_margin_either_side(
    imgui: Any, log: Any, block_rects: list[tuple[float, float]], before: int
) -> None:
    transcript = lines(before) if before else Transcript()
    transcript.append_tool_call("a", "Bash", {"command": "echo a"})
    transcript.append_tool_result("a", "Bash", "one", is_error=False)
    view = log(session(transcript))
    view.frames(3)
    block_rects.clear()
    view.frames(1)
    assert bool(view.seen["scrollbar"]) is bool(before)
    assert block_rects, "the block was drawn"
    side = transcript_pane.margin_px(imgui.get_font_size())
    assert ink(block_rects[-1:], view) == (side, side)


def test_the_text_runs_to_the_margin_and_no_further(
    imgui: Any, log: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Wrapped text, measured by what ImGui laid out: a line long enough to wrap
    starts at the left margin and breaks short of the right one.
    """
    from imgui_bundle import imgui as im

    rects: list[tuple[float, float]] = []
    real = im.text_wrapped

    def spy(text: str) -> None:
        real(text)
        rects.append((im.get_item_rect_min().x, im.get_item_rect_max().x))

    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, " ".join(["word"] * 400) + "\n" * 300)
    view = log(session(transcript))
    view.frames(3)
    assert view.seen["scrollbar"]
    monkeypatch.setattr(im, "text_wrapped", spy)
    view.frames(1)
    side = transcript_pane.margin_px(imgui.get_font_size())
    left, right = ink(rects, view)
    assert left == side
    # A line breaks at a word, so it ends within a word's width of the margin.
    assert side <= right < side + im.calc_text_size("word ").x


@pytest.mark.parametrize("mode", [None, RenderMode.TYPESET], ids=["wrap", "typeset"])
def test_the_row_over_the_log_starts_where_the_logs_text_does(
    request: pytest.FixtureRequest, log: Any, monkeypatch: pytest.MonkeyPatch, mode: Any
) -> None:
    from imgui_bundle import imgui as im

    from pptmstr.ui import widgets

    if mode is not None:
        request.getfixturevalue("seam")
        # Larger than the chrome, so the two margins are told apart.
        typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.25, panes={})
    starts: list[float] = []
    real = widgets.small_button

    def small_button(label: str, *args: Any, **kwargs: Any) -> bool:
        pressed = real(label, *args, **kwargs)
        if label == "copy":
            starts.append(im.get_item_rect_min().x)
        return bool(pressed)

    monkeypatch.setattr(widgets, "small_button", small_button)
    view = log(session(lines(300)))
    view.state.mode = mode
    view.frames(3)
    assert starts and starts[-1] == view.seen["text_left"]


def test_the_typeset_log_has_the_margin_at_its_text_size(
    seam: Any, log: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Larger than the UI font, so the margin is told apart from one at the chrome's
    # size.
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.25, panes={})
    assert typeset.body_px() == 20.0
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A paragraph that is set by the library.\n")
    short = log(session(transcript))
    short.state.mode = RenderMode.TYPESET
    short.frames(3)
    assert not short.seen["scrollbar"]
    assert margins(short) == (transcript_pane.margin_px(20.0),) * 2 == (10.0, 10.0)

    long = Transcript()
    long.append(SegmentKind.OUTPUT, "".join(f"Paragraph {n}.\n\n" for n in range(120)))
    tall = log(session(long))
    tall.state.mode = RenderMode.TYPESET
    tall.frames(3)
    assert tall.seen["scroll_max"] > 0.0 and tall.seen["scrollbar"]
    assert margins(tall) == (10.0, 10.0)


def test_a_line_wider_than_a_short_typeset_log_has_a_bar_to_reach_it_by(
    seam: Any, log: Any
) -> None:
    """
    Literal runs are never broken, so a short log can still be scrolled sideways,
    by the bar along its foot.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A short log.\n")
    transcript.append(SegmentKind.TOOL_RESULT, "Read -> " + "x" * 400 + "\n")
    wide = log(session(transcript))
    wide.state.mode = RenderMode.TYPESET
    wide.frames(4)
    assert wide.seen["scroll_max"] == 0.0 and wide.seen["scroll_max_x"] > 0.0
    assert wide.seen["scrollbar_x"] and not wide.seen["scrollbar"]


# -- the bar held: no flipping at the edge ----------------------------------------------


def flips(bars: list[bool]) -> int:
    """How many times the bar came or went over the frames ``bars`` records."""
    return sum(1 for a, b in itertools.pairwise(bars) if a != b)


def test_a_bar_is_taken_when_shown_and_let_go_only_when_the_log_moved_and_fits() -> None:
    holds = transcript_pane.holds_bar
    assert holds(False, True, False, 900.0, 500.0, 20.0), "shown: taken"
    assert not holds(False, False, True, 100.0, 500.0, 20.0), "never shown: not held"
    assert holds(True, True, False, 100.0, 500.0, 20.0), "nothing moved: kept, though it fits"
    assert holds(True, True, True, 490.0, 500.0, 20.0), "moved, but within the slack: kept"
    assert not holds(True, True, True, 480.0, 500.0, 20.0), "moved and a line short: let go"
    assert holds(True, True, True, 600.0, 500.0, 20.0), "moved and still over: kept"


def test_a_log_growing_across_the_edge_takes_the_bar_once(log: Any) -> None:
    """
    A line at a time across the height of the log: the bar comes once, and the
    re-wrap it causes never sends it away again.
    """
    transcript = Transcript()
    transcript.append(
        SegmentKind.OUTPUT, "".join(f"a line that wraps a little {n}\n" for n in range(5))
    )
    view = log(session(transcript))
    bars = []
    for n in range(40):
        transcript.append(SegmentKind.OUTPUT, f"one more line, number {n}, of some length\n")
        view.frames(1)
        bars.append(bool(view.seen["scrollbar"]))
    assert bars[0] is False and bars[-1] is True and flips(bars) == 1, bars


def test_a_log_that_overflows_only_while_wide_does_not_flip_the_bar_every_frame(
    log: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The edge the guard is for: content that is taller at the wider measure (a
    layout that sets itself one way when wide), sized so that without the bar it
    overflows and with it it fits. Nothing else moves, so the bar, once taken,
    stays: one flip, not one a frame.
    """
    from imgui_bundle import imgui as im

    real = im.text_wrapped
    view = log(session(lines(3)))
    view.frames(1)
    # The measure without a bar.
    wide = view.seen["text_right"] - view.seen["text_left"]

    def text_wrapped(text: str) -> None:
        real(text)
        if im.get_content_region_avail().x >= wide:
            # Wide: a block taller than the log.
            im.dummy(im.ImVec2(1.0, PANE[1] * 2))

    monkeypatch.setattr(im, "text_wrapped", text_wrapped)
    bars = []
    for _ in range(20):
        view.frames(1)
        bars.append(bool(view.seen["scrollbar"]))
    assert flips(bars) == 1 and bars[-1], bars

    # Something of the log's own moving lets it go, once.
    view.store.snapshot().nodes[ROOT].transcript.append(SegmentKind.OUTPUT, "more\n")
    bars = []
    for _ in range(20):
        view.frames(1)
        bars.append(bool(view.seen["scrollbar"]))
    assert flips(bars) <= 2, bars


def test_a_held_bar_is_let_go_when_the_pane_grows_to_fit_the_log(log: Any) -> None:
    view = log(session(lines(30)))
    view.frames(3)
    assert view.seen["scrollbar"]
    view.size = (PANE[0], PANE[1] * 2)
    view.frames(3)
    assert not view.seen["scrollbar"]


def test_a_held_bar_is_let_go_when_a_short_log_is_selected(log: Any) -> None:
    store = session(lines(300))
    other: NodeId = ("s2", None)
    store.apply(
        AgentSpawned(
            node_id=other,
            parent=None,
            task="a short one",
            model="claude-sonnet-5",
            started_at=0.0,
            cwd="/x",
            transcript=lines(3),
        ),
        now=1.0,
    )
    view = log(store)
    view.frames(3)
    assert view.seen["scrollbar"]
    view.node = other
    view.frames(3)
    assert not view.seen["scrollbar"]


def test_a_bar_shown_for_a_windows_earlier_content_is_not_held(log: Any) -> None:
    """
    ImGui shows a window's bar from the content it had the frame before, which a
    pane handed a fresh state over an old window did not draw. That bar goes with
    the frame after, as ImGui's own would, and is not held for a log that fits.
    """
    long = log(session(lines(300)))
    long.frames(3)
    assert long.seen["scrollbar"]
    short = log(session(lines(3)))
    short.window = long.window
    short.frames(1)
    assert short.seen["scrollbar"], "the window's last content, not the log's"
    short.frames(2)
    assert not short.seen["scrollbar"]


# -- SESSION, as the application draws it ----------------------------------------------


def app_state(store: Store, node: NodeId | None = ROOT) -> app.AppState:
    state = app.AppState(store=store, bridge=MagicMock(), settings=Settings())
    state.frame_snap = store.snapshot()
    if node is not None:
        state.focus.target = OnNode(node, pinned=True)
    return state


def session_frames(im: Any, state: app.AppState, count: int, name: str | None = None) -> Any:
    """
    ``count`` frames of SESSION in a pane its size, and the pane's window: a new
    window, or the one ``name``d, which keeps its scroll from the frames before.
    """
    panels = app._panels(state)
    pane = panels["SESSION"]
    # The flags FOCUS gives SESSION's window, as the runner hands them to Begin.
    (docked,) = [w for w in app._focus_layout(panels).dockable_windows if w.label == "SESSION"]
    name = name or f"SESSION##geometry-{next(WINDOWS)}"
    for _ in range(count):
        typeset.pre_frame()
        im.new_frame()
        try:
            im.set_next_window_pos(im.ImVec2(10.0, 10.0))
            im.set_next_window_size(im.ImVec2(*PANE))
            im.begin(name, None, docked.imgui_window_flags)
            pane()
            window = im.internal.get_current_window()
            im.end()
        finally:
            im.render()
    return window


@pytest.mark.parametrize("selected", [True, False], ids=["a session", "nothing selected"])
def test_the_session_pane_does_not_overflow_whatever_its_composer_holds(
    imgui: Any, selected: bool
) -> None:
    """
    A fixed reserve under the composer's height is what made SESSION scroll: 150 px
    for 152 of composer and 11 of separator. Measured, the pane fits, for the full
    composer of a session and for the one line drawn with none selected, which
    leaves the scrollback the rest.
    """
    state = app_state(session(lines(300)), ROOT if selected else None)
    window = session_frames(imgui, state, 4)
    assert window.scroll_max.y == 0.0 and not window.scrollbar_y
    if selected:
        assert state.composer_room > 150.0, state.composer_room
    else:
        assert state.composer_room < 50.0, state.composer_room


def test_a_sub_agents_session_pane_has_its_sessions_composer_and_fits(imgui: Any) -> None:
    state = app_state(session(lines(300), sub=True), SUB)
    window = session_frames(imgui, state, 4)
    assert window.scroll_max.y == 0.0 and state.composer_room > 150.0


def test_a_composer_that_grows_neither_scrolls_the_pane_nor_moves_the_text(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The composer is measured a frame late, so on the frame it grows the pane holds
    more than it has room for. In one window over six frames, the composer 40 px
    taller from the third: SESSION never shows a scrollbar of its own, which would
    take its width from the scrollback, and the log's text keeps the width its own
    bar left it.
    """
    from pptmstr.ui import compose

    state = app_state(session(lines(300)))
    name = f"SESSION##geometry-{next(WINDOWS)}"
    real_compose, real_scroll = compose.draw_conversation, transcript_pane._handle_scroll
    taller = False
    widths: list[float] = []

    def draw_conversation(*args: Any, **kwargs: Any) -> None:
        real_compose(*args, **kwargs)
        if taller:
            imgui.dummy(imgui.ImVec2(1.0, 40.0))

    def handle_scroll(log: TranscriptState) -> None:
        real_scroll(log)
        rect = imgui.internal.get_current_window().content_region_rect
        widths.append(rect.max.x - rect.min.x)

    monkeypatch.setattr(compose, "draw_conversation", draw_conversation)
    monkeypatch.setattr(transcript_pane, "_handle_scroll", handle_scroll)
    settled = None
    bars, rooms = [], []
    for frame in range(6):
        taller = frame >= 2
        if frame == 2:
            settled = state.composer_room
        window = session_frames(imgui, state, 1, name)
        bars.append(bool(window.scrollbar_y))
        rooms.append(state.composer_room)
    assert settled is not None and rooms[-1] > settled, "the growth was taken up"
    assert bars == [False] * 6, bars
    # The log's own bar comes on the second frame, and the text re-wraps once for
    # it; the composer's growth moves it no more.
    assert widths[0] - widths[1] == imgui.get_style().scrollbar_size
    assert len(set(widths[1:])) == 1, widths


@pytest.mark.parametrize("pane", ["CONTEXT", "SESSION"])
def test_the_reasoning_setting_reaches_the_pane_from_both_places_it_is_drawn(
    monkeypatch: pytest.MonkeyPatch, pane: str
) -> None:
    from imgui_bundle import imgui

    from pptmstr.ui import compose

    told: list[Any] = []
    monkeypatch.setattr(
        transcript_pane, "draw", lambda *a, **kw: told.append(kw.get("show_reasoning"))
    )
    monkeypatch.setattr(compose, "draw_conversation", lambda *a, **kw: None)
    for name in ("begin_child", "end_child", "separator"):
        monkeypatch.setattr(imgui, name, lambda *a, **kw: True)
    monkeypatch.setattr(imgui, "get_content_region_avail", lambda: imgui.ImVec2(1, 400))
    state = app_state(session(lines(3)))
    drawn = app._panels(state)[pane]

    drawn()
    state.settings = Settings(show_reasoning=False)
    drawn()
    assert told == [True, False]
