"""
HEALTH for a session recalled from its file.

The pane's ordinary reading is of a record this process filled in: a state, a
duration on the store's clock, a context reading, usage. A recalled record has
the defaults of all four, and what is pinned here is that none of them is drawn
as though it were measured -- and that the pane reads no clock of its own.

Drawing is exercised through ``tests/test_ui_text.said_by``, with ``imgui``
replaced: it says what was drawn and which controls, not what it looked like.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock

import pytest

from pptmstr.approval import Policy
from pptmstr.model import EndedHow, LaunchSpec, NodeId, Recalled, Snapshot
from pptmstr.store import Store
from pptmstr.ui import health, session_menu
from tests.test_ui_text import (
    CLOSED_ROOT,
    FROM_FILE,
    RAN_ROOT,
    WALL_NOW,
    Said,
    recalled_snapshot,
    run,
    said_by,
)


def _drawn(
    monkeypatch: pytest.MonkeyPatch,
    snap: Snapshot,
    node: NodeId,
    *,
    now_wall: float | None = WALL_NOW,
    policy: Policy | None = None,
    press: str | None = None,
) -> tuple[Said, MagicMock]:
    fork = MagicMock()
    actions = health.HealthActions(
        interrupt=MagicMock(), close=MagicMock(), fork=fork, revoke_policy=MagicMock()
    )
    cell = MagicMock()
    monkeypatch.setattr(health, "context_cell", cell)

    def draw() -> None:
        if press is not None:
            health.imgui.button.side_effect = lambda label, *a: label == press
        health.draw(snap, node, actions, 9_000.0, policy, now_wall)

    said = said_by(monkeypatch, draw, health)
    said.calls.extend("context_cell" for _ in cell.mock_calls)
    return said, fork


def test_a_closed_session_reads_what_its_file_said_under_its_own_names(monkeypatch) -> None:
    said, _ = _drawn(monkeypatch, recalled_snapshot(), CLOSED_ROOT)

    # A task typed on two lines is a title on one.
    assert said.strings[0] == "port the parser"
    assert said.strings[2:] == [
        "closed — not loaded; Resume… to continue",
        "last active: 3h02m ago",
        "its last run ended when it was closed",
        "claude-opus-5",
        "/w/project",
        "gate: permissive, as the session file last recorded",
        "$1.25 as the session file recorded, an estimate; it may double-count across resumes",
        "fork",
    ]
    assert "context_cell" not in said.calls


def test_a_file_that_said_nothing_is_drawn_as_not_known_and_never_as_a_default(
    monkeypatch,
) -> None:
    """
    ``Recalled()`` is every field at "the file did not say". The record beside it
    reads STRICT, zero cost and a start at zero, and none of those is a fact.
    """
    snap = recalled_snapshot(Recalled())
    assert snap.nodes[CLOSED_ROOT].policy is Policy.STRICT

    said, _ = _drawn(monkeypatch, snap, CLOSED_ROOT)

    assert "last active: not known" in said.strings
    assert "gate: not known" in said.strings and not said.has("strict")
    assert not said.has("$") and not said.has("estimate")
    assert said.has("not recorded how its last run ended")


def test_the_age_comes_from_the_wall_clock_it_is_handed_and_no_other(monkeypatch) -> None:
    """
    9000 seconds is the store's clock in ``_drawn``. A duration drawn from it, or
    from a clock the pane read for itself, is the time since boot wearing a
    session's name.
    """
    import time

    monkeypatch.setattr(time, "time", lambda: pytest.fail("the pane read a clock"))
    snap = recalled_snapshot()

    later, _ = _drawn(monkeypatch, snap, CLOSED_ROOT, now_wall=WALL_NOW + 3600.0)
    unknown, _ = _drawn(monkeypatch, snap, CLOSED_ROOT, now_wall=None)

    assert "last active: 4h02m ago" in later.strings
    assert "last active: not known" in unknown.strings
    assert not later.has("2h30m") and not unknown.has("2h30m")


def test_a_closed_session_that_had_failed_says_what_it_said(monkeypatch) -> None:
    failed = dataclasses.replace(FROM_FILE, ended_how=EndedHow.FAILED, error="the API\nsaid\x00 no")
    said, _ = _drawn(monkeypatch, recalled_snapshot(failed), CLOSED_ROOT)

    assert "its last run ended when it failed" in said.strings
    assert "it said: the API said<NUL> no" in said.strings

    # Closed after it failed: the file's end reads closed and keeps the text.
    # Both are said, or the pane calls a session that died an ordinary close.
    closed = dataclasses.replace(failed, ended_how=EndedHow.CLOSED)
    said, _ = _drawn(monkeypatch, recalled_snapshot(closed), CLOSED_ROOT)
    assert "its last run ended when it was closed" in said.strings
    assert "it had failed before it was closed: the API said<NUL> no" in said.strings
    assert not said.has("it said")

    # An error beside an end that is neither is not that run's failure.
    for how in (EndedHow.SHUTDOWN, None):
        other = dataclasses.replace(failed, ended_how=how)
        said, _ = _drawn(monkeypatch, recalled_snapshot(other), CLOSED_ROOT)
        assert not said.has("it said") and not said.has("it had failed")


def test_fork_is_the_only_lever_on_a_closed_session_and_it_works(monkeypatch) -> None:
    snap = recalled_snapshot()
    said, fork = _drawn(monkeypatch, snap, CLOSED_ROOT, press="fork")

    labels = [s for s in said.strings if s in {"interrupt", "close", "fork", "narrow to strict"}]
    assert labels == ["fork"]
    fork.assert_called_once_with(LaunchSpec.from_record(snap.nodes[CLOSED_ROOT]))
    assert fork.call_args.args[0].template == "feature"


def test_a_file_with_no_task_offers_no_fork_and_says_why(monkeypatch) -> None:
    snap = recalled_snapshot(task="  ")
    said, fork = _drawn(monkeypatch, snap, CLOSED_ROOT, press="fork")

    assert said.strings[0] == health.NO_TASK
    assert session_menu.TASK_NOT_RECORDED in said.strings and "begin_disabled" in said.calls
    fork.assert_not_called()


def test_a_title_that_is_only_the_clis_label_is_not_forked_from_and_says_why(monkeypatch) -> None:
    """
    A session first run under an older build has no task on file, and its card is
    titled with the CLI's summary of the conversation. Fork launches the record's
    task as a prompt in one press, so the button is greyed before the press, with
    the card menu's own reason -- not left live for the app to refuse afterwards.
    """
    labelled = dataclasses.replace(FROM_FILE, task_recorded=False)
    snap = recalled_snapshot(labelled)
    root = snap.nodes[CLOSED_ROOT]
    assert root.task.strip() and root.model.strip(), "a title and a model are both there"

    said, fork = _drawn(monkeypatch, snap, CLOSED_ROOT, press="fork")

    assert "begin_disabled" in said.calls and "end_disabled" in said.calls
    assert session_menu.TASK_NOT_RECORDED in said.strings
    fork.assert_not_called()

    # The control: the same file with its task recorded forks.
    recorded = recalled_snapshot(dataclasses.replace(FROM_FILE, task_recorded=True))
    said, fork = _drawn(monkeypatch, recorded, CLOSED_ROOT, press="fork")
    assert "begin_disabled" not in said.calls and not said.has("not recorded")
    fork.assert_called_once()


def test_health_and_the_card_menu_grey_fork_on_the_same_closed_cards(monkeypatch) -> None:
    """
    One predicate, asked by both. Wherever the menu refuses Fork on a closed card,
    HEALTH refuses it in the same words; and HEALTH's one refusal of its own, a file
    with no model, is said as that and not as a missing task.
    """
    for recalled, file in (
        (FROM_FILE, {}),
        (dataclasses.replace(FROM_FILE, task_recorded=False), {}),
        (dataclasses.replace(FROM_FILE, task_recorded=True), {}),
        (FROM_FILE, {"task": "  "}),
        (Recalled(), {}),
    ):
        root = recalled_snapshot(recalled, **file).nodes[CLOSED_ROOT]
        item = session_menu.find(session_menu.entries(root, None), session_menu.Action.FORK)
        assert item is not None
        assert health.fork_refusal(root) == item.refused, (recalled, file)
        assert (item.refused is not None) == session_menu.task_missing(root)

    no_model = recalled_snapshot(model="").nodes[CLOSED_ROOT]
    assert not session_menu.task_missing(no_model)
    assert health.fork_refusal(no_model) == health.NO_MODEL
    said, fork = _drawn(monkeypatch, recalled_snapshot(model=""), CLOSED_ROOT, press="fork")
    assert health.NO_MODEL in said.strings and "begin_disabled" in said.calls
    fork.assert_not_called()


def test_a_closed_sessions_cost_is_said_with_the_caveat_details_gives_it(monkeypatch) -> None:
    """
    The file's figure is summed across runs and a resume can count a turn twice.
    Details says so beside the number; the same number here said less.
    """
    from pptmstr.ui import session_details

    said, _ = _drawn(monkeypatch, recalled_snapshot(), CLOSED_ROOT)
    (cost,) = [s for s in said.strings if s.startswith("$1.25")]
    caveat = "it may double-count across resumes"
    assert caveat in cost and "an estimate" in cost

    rows = session_details._spend_rows(recalled_snapshot().nodes[CLOSED_ROOT])
    assert any(caveat in str(row) for row in rows), "the words are Details' words"


def test_a_session_that_ran_here_is_drawn_as_it_always_was(monkeypatch) -> None:
    """The control: the closed reading is the recalled record's and nobody else's."""
    store = Store()
    run(store, cost=2.0)
    said, _ = _drawn(monkeypatch, store.snapshot(), RAN_ROOT, policy=Policy.STRICT)

    assert "$2.00" in said.strings and said.has(" in · ") and "gate: strict" in said.strings
    assert "context_cell" in said.calls
    assert {"interrupt", "close", "fork"} <= set(said.strings)
    assert not said.has("closed —") and not said.has("session file")
    assert health.recalled_lines(store.snapshot().nodes[RAN_ROOT], WALL_NOW) == ()


def test_healths_narrow_says_what_the_card_menus_row_for_it_says(monkeypatch) -> None:
    """
    One lever on two surfaces. The menu's row ends on the part a button's label
    cannot carry -- the agent's briefing still describes the rung it started
    under -- and HEALTH's control said nothing at all.
    """
    from pptmstr.ui import session_menu

    store = Store()
    run(store, cost=2.0)
    snap = store.snapshot()
    root = snap.nodes[RAN_ROOT]

    for policy in (Policy.PERMISSIVE, Policy.AUTONOMOUS):
        row = session_menu.find(
            session_menu.entries(root, policy), session_menu.Action.SET_GATE, Policy.STRICT
        )
        assert row is not None and row.label == "Narrow to strict"
        note = health.narrow_note(policy)
        assert note == row.note
        assert note is not None and note.endswith(
            "The agent's briefing still describes the rung it started under."
        )
        said, _ = _drawn(monkeypatch, snap, RAN_ROOT, policy=policy)
        assert "narrow to strict" in said.strings and note in said.strings

    # Nothing to narrow, no control and no sentence about one.
    assert health.narrow_note(Policy.STRICT) is None and health.narrow_note(None) is None
    said, _ = _drawn(monkeypatch, snap, RAN_ROOT, policy=Policy.STRICT)
    assert "narrow to strict" not in said.strings and not said.has("briefing")
