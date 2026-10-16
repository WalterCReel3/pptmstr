"""
The send rule shared by the two reply composers.

Most of what is pinned here is the decision the draw makes from the two signals it
has -- the widget's submit flag and the button -- plus the draft pruning that keeps
the state from growing a key per dead session. The last section draws the composer
in a frame of a bare context, which needs no GL.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from pptmstr.model import AgentState, NodeId
from pptmstr.ui.compose import ComposeState, wants_send


def test_ctrl_enter_sends() -> None:
    assert wants_send(submitted=True, clicked=False, draft="ship it")


def test_button_sends() -> None:
    assert wants_send(submitted=False, clicked=True, draft="ship it")


def test_idle_frame_sends_nothing() -> None:
    """The common case: the box is being typed into and neither signal fired."""
    assert not wants_send(submitted=False, clicked=False, draft="half a th")


@pytest.mark.parametrize("draft", ["", "   ", "\n", "\n\n\n", " \t\n "])
def test_whitespace_drafts_are_not_sent(draft: str) -> None:
    """
    Enter inserts a newline in these boxes, so a draft of nothing but newlines is
    what an idle box accumulates when it is leaned on. Sending it would spend a
    session turn on no instruction.
    """
    assert not wants_send(submitted=True, clicked=True, draft=draft)


def test_prune_drops_drafts_for_vanished_nodes() -> None:
    class _Snap:
        nodes: dict[NodeId, object] = {("alive", None): object()}

    state = ComposeState(replies={("alive", None): "keep", ("gone", None): "drop"})
    state.prune(_Snap())  # type: ignore[arg-type]
    assert state.replies == {("alive", None): "keep"}


def test_awaiting_input_is_not_terminal() -> None:
    """
    The composer disables itself on ``is_terminal`` and nothing else. If
    AWAITING_INPUT ever joined that set the box would be dead in the exact state it
    exists to serve.
    """
    assert not AgentState.AWAITING_INPUT.is_terminal


# -- a session recalled from its file -----------------------------------------------


def _conversation(monkeypatch, snap, node):  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    from pptmstr.ui import compose
    from tests.test_ui_text import said_by

    acts = {name: MagicMock() for name in ("send", "interrupt", "close")}
    said = said_by(
        monkeypatch,
        lambda: compose.draw_conversation(snap, ComposeState(), node, **acts),
        compose,
    )
    return said, acts


def test_a_closed_session_is_sent_to_resume_and_given_no_composer(monkeypatch) -> None:
    """
    "session done - it can no longer be messaged" is two false things about a
    recalled session: nothing here saw it finish, and it can be messaged, by
    resuming it. The buttons under it act on a process, and there is none.
    """
    from tests.test_ui_text import CLOSED_ROOT, recalled_snapshot

    said, _ = _conversation(monkeypatch, recalled_snapshot(), CLOSED_ROOT)

    assert said.strings == ["closed — Resume… to continue"]
    assert "button" not in said.calls and "begin_disabled" not in said.calls


def test_a_session_that_finished_here_keeps_its_composers_own_words(monkeypatch) -> None:
    from pptmstr.intents import AgentFinished
    from pptmstr.store import Store
    from tests.test_ui_text import RAN_ROOT, run

    store = Store()
    run(store)
    store.apply(AgentFinished(RAN_ROOT, AgentState.DONE, ended_at=4.0), now=4.0)
    said, _ = _conversation(monkeypatch, store.snapshot(), RAN_ROOT)

    assert "session done - it can no longer be messaged" in said.strings
    assert {"interrupt", "close session"} <= set(said.strings)
    assert not said.has("closed —")


# -- the reply box, drawn ------------------------------------------------------------


@pytest.mark.parametrize("wrap", [True, False])
def test_the_reply_box_asks_this_imgui_to_wrap_a_draft_unless_told_not_to(
    imgui: Any, monkeypatch: pytest.MonkeyPatch, wrap: bool
) -> None:
    """
    A prompt being written wraps as it will in the log once sent. Drawn in a real
    frame, so the flag is one this ImGui has and accepts on a multi-line box:
    ImGui asserts on ``word_wrap`` anywhere else. ``wrap`` is
    ``settings.wrap_inputs``, on by default, and SESSION passes it.
    """
    from pptmstr import settings
    from pptmstr.store import Store
    from pptmstr.ui import compose
    from tests import test_typeset_views as rig
    from tests.test_ui_text import RAN_ROOT, run

    store = Store()
    run(store)
    flags: list[int] = []
    real = imgui.input_text_multiline

    def boxed(label: str, value: str, size: Any, given: int = 0) -> Any:
        flags.append(given)
        return real(label, value, size, given)

    monkeypatch.setattr(imgui, "input_text_multiline", boxed)
    state = ComposeState(replies={RAN_ROOT: "glob **/*.py for its callers, " * 20})
    rig.frame(
        imgui,
        lambda: compose.draw_conversation(
            store.snapshot(),
            state,
            RAN_ROOT,
            send=lambda *_: None,
            interrupt=lambda *_: None,
            close=lambda *_: None,
            wrap=wrap,
        ),
    )
    word_wrap = int(imgui.InputTextFlags_.word_wrap)
    assert len(flags) == 1 and bool(flags[0] & word_wrap) is wrap
    assert settings.Settings().wrap_inputs is True


# -- the composer between the log's text edges ---------------------------------------

# SESSION's window width in FOCUS at each app window size, as
# scripts/probe_pane_layout.py measured it (shots/composer-margins/probe-before):
# the scrollback runs 324-1181 at 1500x900 and 324-961 at 1280x800, inside the
# pane's 8 px padding.
_PANE_WIDTHS = {"1500x900": 873.0, "1280x800": 653.0}

# Larger than the UI font, so the typeset log's margin is told apart from the one
# the UI font would give.
_TYPESET_SCALE = 1.25


def _composer_frames(
    im: Any,
    monkeypatch: pytest.MonkeyPatch,
    width: float,
    lines_drawn: int,
    *,
    typeset_on: bool = False,
    closed: bool = False,
) -> dict[str, Any]:
    """
    SESSION drawn as the application draws it, in a pane ``width`` wide, for a few
    frames: where the log's child and its text were, where the reply box was, and
    the right edge of every line the composer wrote. ``typeset_on`` draws the log
    in TYPESET; the caller has the seam up.
    """
    from pptmstr import app
    from pptmstr.settings import Settings
    from pptmstr.ui import compose, transcript_pane, typeset, widgets
    from pptmstr.ui.focus import OnNode
    from pptmstr.ui.transcript_pane import RenderMode
    from tests.test_session_pane_geometry import ROOT, lines, session

    if not typeset_on:
        monkeypatch.setattr(typeset, "enabled", lambda pane=None: False)
    state = app.AppState(store=session(lines(lines_drawn)), bridge=MagicMock(), settings=Settings())
    if closed:
        monkeypatch.setattr(widgets, "closed_line", lambda *a, **kw: "closed")
    if typeset_on:
        state.transcripts.mode = RenderMode.TYPESET
    state.frame_snap = state.store.snapshot()
    state.focus.target = OnNode(ROOT, pinned=True)
    seen: dict[str, Any] = {}
    real_scroll = transcript_pane._handle_scroll

    def handle_scroll(log: Any) -> None:
        real_scroll(log)
        window = im.internal.get_current_window()
        seen["log"] = (window.pos.x, window.pos.x + window.size.x)
        seen["text"] = (window.pos.x + window.dc.indent.x, window.content_region_rect.max.x)
        seen["bar"] = window.scrollbar_sizes.x
        seen["padding"] = window.window_padding.x

    real_box = compose.widgets.multiline_input

    def box(*args: Any, **kwargs: Any) -> Any:
        out = real_box(*args, **kwargs)
        seen["box"] = (im.get_item_rect_min().x, im.get_item_rect_max().x)
        return out

    real_compose = compose.draw_conversation

    def draw_conversation(*args: Any, **kwargs: Any) -> None:
        seen["handed"] = kwargs.get("log")
        seen["pane"] = (im.get_cursor_screen_pos().x, im.get_content_region_avail().x)
        real_compose(*args, **kwargs)

    class Spy:
        def __getattr__(self, name: str) -> Any:
            return getattr(im, name)

        def text_disabled(self, text: str) -> None:
            im.text_disabled(text)
            seen.setdefault("lines", {})[text] = im.get_item_rect_max().x
            seen.setdefault("starts", {})[text] = im.get_item_rect_min().x

        def text_colored(self, colour: Any, text: str) -> None:
            im.text_colored(colour, text)
            seen.setdefault("lines", {})[text] = im.get_item_rect_max().x
            seen.setdefault("starts", {})[text] = im.get_item_rect_min().x

    monkeypatch.setattr(transcript_pane, "_handle_scroll", handle_scroll)
    monkeypatch.setattr(compose.widgets, "multiline_input", box)
    monkeypatch.setattr(compose, "imgui", Spy())
    monkeypatch.setattr(compose, "draw_conversation", draw_conversation)
    pane = app._panels(state)["SESSION"]
    name = f"SESSION##composer-{width}-{lines_drawn}-{typeset_on}-{closed}"
    for _ in range(4):
        typeset.pre_frame()
        im.new_frame()
        try:
            im.set_next_window_pos(im.ImVec2(10.0, 10.0))
            im.set_next_window_size(im.ImVec2(width, 500.0))
            im.begin(name)
            pane()
            im.end()
        finally:
            im.render()
    return seen


def _assert_on_the_text(seen: dict[str, Any]) -> None:
    """
    The reply box spans exactly the log's text, and SESSION handed the composer the
    edges the log's own window had.
    """
    (log_left, log_right), (text_left, text_right) = seen["log"], seen["text"]
    box_left, box_right = seen["box"]
    side = seen["padding"]
    assert text_left == log_left + side
    assert text_right == log_right - seen["bar"] - side
    assert (seen["handed"].left, seen["handed"].right) == (text_left, text_right)
    assert (box_left, box_right) == (text_left, text_right)


@pytest.mark.parametrize("size", sorted(_PANE_WIDTHS))
@pytest.mark.parametrize("lines_drawn", [3, 300], ids=["no bar", "bar"])
def test_the_reply_box_spans_the_logs_text(
    imgui: Any, monkeypatch: pytest.MonkeyPatch, size: str, lines_drawn: int
) -> None:
    """
    The box starts where the log's text does and stops where it stops: at the
    log's margin without a bar, and that margin short of the bar with one, which
    the composer itself does not have.
    """
    from pptmstr.ui import transcript_pane

    seen = _composer_frames(imgui, monkeypatch, _PANE_WIDTHS[size], lines_drawn)
    assert bool(seen["bar"]) is (lines_drawn == 300)
    assert seen["padding"] == transcript_pane.margin_px(imgui.get_font_size())
    _assert_on_the_text(seen)


@pytest.mark.parametrize("size", sorted(_PANE_WIDTHS))
@pytest.mark.parametrize("lines_drawn", [3, 300], ids=["no bar", "bar"])
def test_the_reply_box_spans_a_typeset_logs_text_at_its_own_scale(
    seam: Any, monkeypatch: pytest.MonkeyPatch, size: str, lines_drawn: int
) -> None:
    """
    Under the seam the log's margin is set by the typeset text's size, not the UI
    font's, and the box follows the log's.
    """
    from pptmstr.ui import transcript_pane, typeset

    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=_TYPESET_SCALE, panes={})
    seen = _composer_frames(seam, monkeypatch, _PANE_WIDTHS[size], lines_drawn, typeset_on=True)
    assert bool(seen["bar"]) is (lines_drawn == 300)
    assert seen["padding"] == transcript_pane.margin_px(typeset.body_px())
    assert seen["padding"] != transcript_pane.margin_px(seam.get_font_size())
    _assert_on_the_text(seen)


def test_with_no_log_drawn_the_composer_keeps_the_ui_fonts_margin(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A session that is not loaded draws no log, so there are no edges to hand
    over; the composer's own line keeps the UI font's margin from the pane's edge.
    """
    from pptmstr.ui import transcript_pane

    seen = _composer_frames(imgui, monkeypatch, _PANE_WIDTHS["1280x800"], 3, closed=True)
    assert "log" not in seen and seen["handed"] is None
    origin, _ = seen["pane"]
    side = transcript_pane.margin_px(imgui.get_font_size())
    assert seen["starts"]["closed"] == origin + side


@pytest.mark.parametrize("size", sorted(_PANE_WIDTHS))
def test_no_line_of_the_composer_runs_past_the_logs_text(
    imgui: Any, monkeypatch: pytest.MonkeyPatch, size: str
) -> None:
    seen = _composer_frames(imgui, monkeypatch, _PANE_WIDTHS[size], 300)
    limit = seen["text"][1]
    hint = "interrupt stops this turn; close ends the session"
    assert hint in seen["lines"]
    assert all(right <= limit for right in seen["lines"].values()), (limit, seen["lines"])


def test_the_hint_wraps_in_a_pane_narrower_than_it(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _composer_frames(imgui, monkeypatch, 260.0, 3)
    hint = "interrupt stops this turn; close ends the session"
    assert imgui.calc_text_size(hint).x > 260.0
    assert seen["lines"][hint] <= seen["text"][1]


@pytest.mark.parametrize("pressed", ["send", "interrupt", "close session"])
def test_each_button_still_does_its_one_thing(monkeypatch, pressed: str) -> None:  # type: ignore[no-untyped-def]
    from unittest.mock import MagicMock

    from pptmstr.store import Store
    from pptmstr.ui import compose, widgets
    from tests.test_ui_text import RAN_ROOT, run

    store = Store()
    run(store)
    fake = MagicMock()
    fake.get_font_size.return_value = 16.0
    fake.get_cursor_pos_x.return_value = 8.0
    fake.get_cursor_screen_pos.return_value.x = 18.0
    fake.get_content_region_avail.return_value.x = 600.0
    fake.button.side_effect = lambda label: label == pressed
    monkeypatch.setattr(compose, "imgui", fake)
    monkeypatch.setattr(widgets, "multiline_input", lambda label, text, *a, **k: (False, text))
    acts = {name: MagicMock() for name in ("send", "interrupt", "close")}
    state = ComposeState(replies={RAN_ROOT: "  go on  "})

    compose.draw_conversation(store.snapshot(), state, RAN_ROOT, **acts)

    called = {name for name, act in acts.items() if act.called}
    assert called == {{"send": "send", "interrupt": "interrupt", "close session": "close"}[pressed]}
    if pressed == "send":
        acts["send"].assert_called_once_with(RAN_ROOT, "go on")
        assert state.replies[RAN_ROOT] == "" and state.focus_reply
    else:
        acts[{"interrupt": "interrupt", "close session": "close"}[pressed]].assert_called_once_with(
            RAN_ROOT
        )
    fake.indent.assert_called_once_with(8.0)
    fake.unindent.assert_called_once_with(8.0)
