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
            }

        monkeypatch.setattr(transcript_pane, "_handle_scroll", handle_scroll)
        self.pushed: list[tuple[int, float]] = []
        real_push = im.push_style_color

        def push_style_color(colour: Any, value: Any) -> None:
            self.pushed.append((int(colour), float(value.w)))
            real_push(colour, value)

        monkeypatch.setattr(im, "push_style_color", push_style_color)

    def frames(self, count: int) -> None:
        im = self.im
        for _ in range(count):
            self.pushed.clear()
            typeset.pre_frame()
            im.new_frame()
            try:
                im.set_next_window_pos(im.ImVec2(10.0, 10.0))
                im.set_next_window_size(im.ImVec2(*PANE))
                im.begin(self.window)
                transcript_pane.draw(self.store.snapshot(), self.state, ROOT)
                im.end()
            finally:
                im.render()

    @property
    def bar_unseen(self) -> bool:
        scrollbar_bg = int(self.im.Col_.scrollbar_bg)
        return (scrollbar_bg, 0.0) in self.pushed


@pytest.fixture
def log(imgui: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    yield lambda store: Log(imgui, monkeypatch, store)


def test_with_nothing_to_scroll_the_bar_is_unseen_and_its_gutter_kept(log: Any) -> None:
    """
    The property: the text is given the same width whether or not there is a bar
    to see, so nothing is set again the frame the text first overflows.
    """
    short = log(session(lines(3)))
    short.frames(3)
    assert short.seen["scroll_max"] == 0.0 and short.bar_unseen
    assert short.seen["scrollbar"], "the gutter is still there"

    long = log(session(lines(300)))
    long.frames(3)
    assert long.seen["scroll_max"] > 0.0 and not long.bar_unseen, "a bar to scroll by"
    width = short.seen["text_right"] - short.seen["text_left"]
    assert long.seen["text_right"] - long.seen["text_left"] == width


def test_the_bar_is_seen_once_the_text_overflows_and_the_width_never_moves(log: Any) -> None:
    """
    ImGui knows a window can scroll from the frame after its content outgrew it,
    and the bar's ink is that answer a frame later again: two frames in all, over
    which the text keeps its width.
    """
    transcript = lines(3)
    view = log(session(transcript))
    view.frames(2)
    assert view.bar_unseen
    width = view.seen["text_right"] - view.seen["text_left"]
    transcript.append(SegmentKind.OUTPUT, "".join(f"more {n}\n" for n in range(300)))
    unseen = []
    for _ in range(3):
        view.frames(1)
        unseen.append((view.bar_unseen, view.seen["scroll_max"]))
        assert view.seen["text_right"] - view.seen["text_left"] == width
    assert [each for each, _ in unseen] == [True, True, False]


def test_the_sides_are_the_margin_or_the_gutter_whichever_is_wider() -> None:
    assert transcript_pane.log_sides(16.0, 12.0) == (12.0, 0.0), "the gutter is the margin"
    assert transcript_pane.log_sides(32.0, 12.0) == (16.0, 4.0), "padded past the gutter"
    assert transcript_pane.log_sides(24.0, 12.0) == (12.0, 0.0)


def margins(view: Any) -> tuple[float, float]:
    """The margin seen either side of the text: the gutter is part of the right."""
    seen = view.seen
    return seen["text_left"] - seen["left"], seen["right"] - seen["text_right"]


@pytest.mark.parametrize("lines_drawn", [3, 300], ids=["fits", "overflows"])
def test_the_wrapped_log_has_the_same_margin_either_side(
    imgui: Any, log: Any, lines_drawn: int
) -> None:
    view = log(session(lines(lines_drawn)))
    view.frames(3)
    left, right = margins(view)
    gutter = imgui.get_style().scrollbar_size
    assert left == right == max(transcript_pane.margin_px(imgui.get_font_size()), gutter)


def test_the_typeset_log_has_the_margin_at_its_text_size_and_keeps_its_width(
    seam: Any, log: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Larger than the UI font, so the margin is told apart from one at the chrome's
    # size; and a gutter narrower than either, which would otherwise be both.
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.25, panes={})
    assert typeset.body_px() == 20.0
    monkeypatch.setattr(seam.get_style(), "scrollbar_size", 6.0)
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A paragraph that is set by the library.\n")
    short = log(session(transcript))
    short.state.mode = RenderMode.TYPESET
    short.frames(3)
    assert short.bar_unseen and short.seen["scrollbar"]
    assert margins(short) == (transcript_pane.margin_px(20.0),) * 2 == (10.0, 10.0)

    long = Transcript()
    long.append(SegmentKind.OUTPUT, "".join(f"Paragraph {n}.\n\n" for n in range(120)))
    tall = log(session(long))
    tall.state.mode = RenderMode.TYPESET
    tall.frames(3)
    assert tall.seen["scroll_max"] > 0.0 and not tall.bar_unseen
    assert (
        tall.seen["text_right"] - tall.seen["text_left"]
        == short.seen["text_right"] - short.seen["text_left"]
    )


def test_a_line_wider_than_a_short_typeset_log_has_a_bar_to_reach_it_by(
    seam: Any, log: Any
) -> None:
    """
    Literal runs are never broken, so a short log can still be scrolled sideways.
    The bar it is scrolled by is drawn in the colours a log with nothing to scroll
    hides, and is seen.
    """
    transcript = Transcript()
    transcript.append(SegmentKind.OUTPUT, "A short log.\n")
    transcript.append(SegmentKind.TOOL_RESULT, "Read -> " + "x" * 400 + "\n")
    wide = log(session(transcript))
    wide.state.mode = RenderMode.TYPESET
    wide.frames(4)
    assert wide.seen["scroll_max"] == 0.0 and wide.seen["scroll_max_x"] > 0.0
    assert not wide.bar_unseen


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
    take its width from the scrollback, and the log's text keeps its width.
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
    assert len(set(widths)) == 1, widths


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
