"""
The pure decisions behind the launcher modal.

Only the parts checkable without a GL context: what an open request does to the
state machine, what a draft turns into when it is handed to ``_launch``, and the
readiness rule that decides whether Enter does anything at all. The drawing itself
needs pixels.
"""

from __future__ import annotations

import pytest

from pptmstr.ui.launcher import MODELS, LauncherState


def test_starts_closed_with_no_request() -> None:
    state = LauncherState()
    assert not state.is_open
    assert not state._open_requested


def test_request_open_is_deferred_not_immediate() -> None:
    """
    The flag is set now and consumed by the draw. ``is_open`` must not flip here:
    the layout key handler reads it before any drawing, and a modal that claimed to
    be open a frame early would swallow an Esc meant for the layout.
    """
    state = LauncherState()
    state.request_open()
    assert state._open_requested
    assert not state.is_open


def test_request_open_while_open_is_a_no_op() -> None:
    """
    Ctrl+N pressed with the modal already up must not re-issue ``open_popup``.
    Reopening resets the window position, which would yank a half-typed task box
    back to the centre of the viewport.
    """
    state = LauncherState(is_open=True)
    state.request_open()
    assert not state._open_requested


@pytest.mark.parametrize(
    "task,ready",
    [
        ("", False),
        ("   ", False),
        ("\n\t ", False),
        ("do the thing", True),
        ("  do the thing  ", True),
    ],
)
def test_ready_ignores_whitespace_only_drafts(task: str, ready: bool) -> None:
    assert LauncherState(task=task).ready is ready


def test_spec_strips_task_and_resolves_model() -> None:
    state = LauncherState(task="  audit the parser  ", cwd="/tmp/x", model_index=1)
    assert state.spec() == ("audit the parser", MODELS[1], "/tmp/x")


def test_spec_defaults_blank_cwd_to_repo_root() -> None:
    """
    An empty directory field means "here", not "". The value reaches
    ``AgentSession`` and is the FLEET rail's grouping key, so a blank string would
    file the session under a project whose name is the empty string.
    """
    assert LauncherState(task="t", cwd="   ").spec()[2] == "."


def test_spec_strips_cwd_whitespace() -> None:
    assert LauncherState(task="t", cwd="  /srv/repo \n").spec()[2] == "/srv/repo"


def test_default_model_is_first_in_the_list() -> None:
    assert LauncherState(task="t").spec()[1] == MODELS[0]


def test_every_model_index_is_addressable() -> None:
    """Guards against a combo whose index outruns the tuple it is drawn from."""
    for i in range(len(MODELS)):
        assert LauncherState(task="t", model_index=i).spec()[1] == MODELS[i]
