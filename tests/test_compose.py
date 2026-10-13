"""
The send rule shared by the two reply composers.

Drawing needs a GL context, so what is pinned here is the decision the draw makes
from the two signals it has -- the widget's submit flag and the button -- plus the
draft pruning that keeps the state from growing a key per dead session.
"""

from __future__ import annotations

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
