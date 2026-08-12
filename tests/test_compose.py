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
