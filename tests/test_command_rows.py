"""
A break-glass command as a row the operator owes: in the queue, in DETAIL, on its
session's card, in the digest, and in the fold of ended sub-agents.

The snapshots here are built by the real reducer, so the row under test is the
one the store composes. What a pane draws is read off a stub or off Dear ImGui
itself in a context with no backend: nothing here was rendered, and no pixel of
any of it has been looked at.

**No pane acts on a command.** The three presses are callbacks the application
hands in, and only the application builds the intents behind them; what those
then do is in ``tests/test_app.py``.
"""

from __future__ import annotations

import dataclasses
import inspect
import os
import random
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui as real_imgui

from pptmstr import approval as policy
from pptmstr import bus, commandbus, commandfile, commandrun, commandtext, digest, theme
from pptmstr.bridge import Bridge
from pptmstr.command import (
    MAX_OPEN_PER_ASKER,
    Command,
    CommandState,
    End,
    EndUnknown,
    Exited,
    Lapsed,
    Refused,
    Run,
    Survivor,
)
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandRequested,
    CommandsRead,
    CommandsRestored,
    CommandStopAsked,
    CommandWithdrawn,
    Intent,
    StateChanged,
)
from pptmstr.model import (
    AgentState,
    AskerFate,
    CommandOwed,
    DetachRefusal,
    NodeId,
    ObligationKind,
    Snapshot,
)
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.theme import OBLIGATION_GLYPH, STATE_GLYPH
from pptmstr.ui import command_view, detail, fleet_view, inbox, rail, review, widgets
from pptmstr.ui.command_view import Actions, CommandViewState, FrozenRun
from pptmstr.ui.focus import FocusState, OnObligation, Scope
from tests.test_inbox_rail import _collect, _row_text, _stub_imgui, _stub_row_imgui, approval

S = "s-1"
ROOT: NodeId = (S, None)
CHILD: NodeId = (S, "agent-a")
PEER: NodeId = (S, "agent-b")

# An epoch reading, as ``time.time`` gives: larger than any monotonic stamp here.
WALL = 1_790_000_000.0

# In every text an agent writes on a command. No line of this application's has it.
MARK = "ZQX"
ASKED = f"make {MARK}-install PREFIX=/usr/local && \\\n  echo {MARK} done"
REASON = f"{MARK}-reason: Bash  git push --force   (approved)"
EXPECTS = f"{MARK}-expects: this runs as you"
OUTPUT = f"{MARK}-output line one\n| not the asker\n> not the operator\n"


def cid(n: int) -> str:
    return f"cmd-20261013T12000{n % 10}Z-{n:012x}"


def asked(n: int, node: NodeId = CHILD, text: str = ASKED) -> Command:
    return Command(id=cid(n), asker=node, asked=text, reason=REASON, expects=EXPECTS, asked_at=WALL)


def team(*, at: float = 1.0) -> Store:
    store = Store()
    for node, parent in ((ROOT, None), (CHILD, ROOT), (PEER, ROOT)):
        store.apply(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task="rotate the signing key",
                model="claude-opus-5",
                started_at=0.0,
                agent_type=None if parent is None else "builder",
                cwd="/work/tree" if parent is None else None,
            ),
            now=at,
        )
        store.apply(StateChanged(node, AgentState.THINKING), now=at)
    return store


def request(store: Store, n: int, node: NodeId = CHILD, *, now: float, text: str = ASKED) -> None:
    store.apply(CommandRequested(node, asked(n, node, text), None, WALL + now), now=now)


def approve(store: Store, n: int, *, now: float, text: str = ASKED) -> None:
    store.apply(CommandApproved(cid(n), text, "/bin/bash", "/work/tree", 600, WALL + now), now=now)


def finish(store: Store, n: int, *, now: float, tail: str = OUTPUT) -> None:
    end = End(
        outcome=Exited(code=0), at=WALL + now, duration_s=3.0, output_bytes=len(tail), tail=tail
    )
    store.apply(CommandEnded(cid(n), end), now=now)


def owed_commands(snap: Snapshot) -> list[CommandOwed]:
    return [o for o in snap.needs_you if isinstance(o, CommandOwed)]


def rows(
    state: CommandViewState | None = None,
    *,
    wired: bool = False,
    actions: Actions | None = None,
    shell: str | None = "/bin/bash",
    opened: str | None = None,
) -> inbox.CommandRows:
    """
    What a pane is handed to draw a command with. ``opened`` is the command
    whose row the operator opened, long enough ago for a first press to answer.
    """
    nothing = Actions(on_run=MagicMock(), on_refuse=MagicMock(), on_stop=MagicMock())
    state = state if state is not None else CommandViewState()
    if opened is not None:
        state.open(opened, at=-command_view.ARM_DELAY_S)
    return inbox.CommandRows(
        state=state,
        shell=shell,
        stripped_env=("VIRTUAL_ENV (/v) is unset",),
        actions=actions if actions is not None else nothing,
        now=WALL + 50.0,
        wired=wired,
    )


# -- the store: which commands owe a row ------------------------------------------------


def test_an_asked_and_a_running_command_owe_a_row_and_an_ended_one_does_not() -> None:
    store = team()
    request(store, 1, now=5.0)
    (row,) = owed_commands(store.snapshot())
    assert (row.kind, row.node, row.key) == (ObligationKind.COMMAND, CHILD, f"command:{cid(1)}")
    assert row.command.state is CommandState.ASKED and row.asker is AskerFate.LIVE

    approve(store, 1, now=6.0)
    (row,) = owed_commands(store.snapshot())
    assert row.command.state is CommandState.RUNNING and row.key == f"command:{cid(1)}"

    finish(store, 1, now=7.0)
    assert owed_commands(store.snapshot()) == []
    assert store.snapshot().command_since == {}


@pytest.mark.parametrize(
    "close",
    [
        CommandRefused(cid(1), "no", WALL + 9.0),
        CommandWithdrawn(CHILD, cid(1), None, WALL + 9.0),
    ],
    ids=["refused", "withdrawn"],
)
def test_a_command_refused_or_withdrawn_leaves_the_queue(close: Intent) -> None:
    store = team()
    request(store, 1, now=5.0)
    store.apply(close, now=9.0)

    assert store.snapshot().needs_you == ()


def test_a_running_command_keeps_its_row_after_its_asker_and_its_session_have_ended() -> None:
    """Cancel has to stay reachable: the row is where it is."""
    store = team()
    request(store, 1, now=5.0)
    approve(store, 1, now=6.0)
    for node in (CHILD, PEER, ROOT):
        store.apply(AgentFinished(node, AgentState.DONE, 7.0), now=7.0)

    snap = store.snapshot()
    (row,) = owed_commands(snap)
    assert row.asker is AskerFate.STOPPED and "its asker has stopped" in row.summary
    assert snap.detach_refusal(S) is DetachRefusal.OWES


def test_a_commands_wait_is_on_the_queues_clock_and_sorts_among_the_other_rows() -> None:
    """
    ``asked_at`` is an epoch reading. Used as the row's ``since`` it is larger
    than every other, so the command would sort last whenever it was asked and
    its age would be negative.
    """
    store = team()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, at=3.0).approval), now=3.0)
    request(store, 1, now=5.0)
    store.apply(ApprovalRequested(PEER, approval(PEER, "p2", at=7.0).approval), now=7.0)
    request(store, 2, PEER, now=9.0)

    queue = store.snapshot().needs_you

    assert [(o.kind, o.since) for o in queue] == [
        (ObligationKind.APPROVAL, 3.0),
        (ObligationKind.COMMAND, 5.0),
        (ObligationKind.APPROVAL, 7.0),
        (ObligationKind.COMMAND, 9.0),
    ]
    assert all(o.since < WALL for o in queue)


def test_a_command_keeps_the_wait_it_was_asked_at_while_it_runs() -> None:
    store = team()
    request(store, 1, now=5.0)
    approve(store, 1, now=60.0)
    store.apply(CommandStopAsked(cid(1), WALL + 70.0), now=70.0)

    (row,) = owed_commands(store.snapshot())
    assert row.since == 5.0 and "cancel asked" in row.summary


def test_the_row_is_composed_from_structure_and_holds_nothing_an_agent_wrote() -> None:
    store = team()
    request(store, 1, now=5.0)
    (row,) = owed_commands(store.snapshot())

    assert row.summary == (
        f"command asked from builder: 2 lines, {len(ASKED):,} characters; {cid(1)}"
    )

    approve(store, 1, now=6.0, text="true")
    (row,) = owed_commands(store.snapshot())
    # The size is of what is running, which the operator changed.
    assert row.summary == f"command running from builder: 1 line, 4 characters; {cid(1)}"


OPS = ("request", "approve", "refuse", "withdraw", "stop", "end", "read", "finish-agent")


@pytest.mark.parametrize("seed", range(40))
def test_over_generated_histories_the_queue_holds_exactly_the_open_commands(seed: int) -> None:
    """
    Whatever is done in whatever order: a command is in ``needs_you`` exactly
    while it is asked or running, under the instant it was asked on the queue's
    clock, with nothing of its texts in the row -- and its session cannot leave
    the fleet while it is.
    """
    rng = random.Random(seed)
    store = team()
    asked_at: dict[str, float] = {}
    now = 2.0
    for _ in range(60):
        now += rng.choice((0.0, 0.5, 3.0))
        n = rng.randrange(1, 7)
        node = rng.choice((ROOT, CHILD, PEER))
        match rng.choice(OPS):
            case "request":
                before = set(store.snapshot().commands)
                request(store, n, node, now=now, text=f"{ASKED} {n}")
                if cid(n) not in before and cid(n) in store.snapshot().commands:
                    asked_at[cid(n)] = now
            case "approve":
                approve(store, n, now=now, text=rng.choice((ASKED, f"{MARK} edited")))
            case "refuse":
                store.apply(CommandRefused(cid(n), f"{MARK} no", WALL + now), now=now)
            case "withdraw":
                store.apply(CommandWithdrawn(node, cid(n), None, WALL + now), now=now)
            case "stop":
                store.apply(CommandStopAsked(cid(n), WALL + now), now=now)
            case "end":
                finish(store, n, now=now)
            case "read":
                store.apply(CommandsRead(node, "rq", WALL + now, cid(n)), now=now)
            case "finish-agent":
                store.apply(AgentFinished(PEER, AgentState.DONE, now), now=now)

        snap = store.snapshot()
        open_ids = {c.id for c in snap.commands.values() if c.is_open}
        listed = owed_commands(snap)
        assert {o.command.id for o in listed} == open_ids
        assert len(listed) == len(open_ids)
        assert set(snap.command_since) == open_ids
        for row in listed:
            assert row.since == asked_at[row.command.id]
            assert row.command is snap.commands[row.command.id]
            assert row.node == row.command.asker and row.key == f"command:{row.command.id}"
            assert MARK not in row.summary
        assert [o.since for o in snap.needs_you] == sorted(o.since for o in snap.needs_you)
    assert asked_at, "the history asked for nothing"

    # With every agent ended, what is left open is the one thing keeping the
    # session on the fleet.
    for node in (CHILD, PEER, ROOT):
        store.apply(AgentFinished(node, AgentState.DONE, now), now=now)
    snap = store.snapshot()
    still_open = any(c.is_open for c in snap.commands.values())
    assert {o.kind for o in snap.needs_you} <= {ObligationKind.COMMAND}
    assert snap.detach_refusal(S) is (DetachRefusal.OWES if still_open else None)


def test_the_generated_histories_reach_every_state() -> None:
    """The property above is vacuous over histories in which nothing ever runs or ends."""
    assert MAX_OPEN_PER_ASKER >= 2
    rng = random.Random(0)
    store = team()
    seen: set[CommandState] = set()
    for n in range(1, 4):
        request(store, n, rng.choice((CHILD, PEER)), now=2.0 + n)
    approve(store, 1, now=10.0)
    seen |= {c.state for c in store.snapshot().commands.values()}
    finish(store, 1, now=11.0)
    seen |= {c.state for c in store.snapshot().commands.values()}
    assert seen == set(CommandState)


def test_a_restored_command_owes_no_row_and_has_no_wait() -> None:
    """No file puts a row in front of the operator."""
    store = team()
    lapsed = dataclasses.replace(asked(4), end=End(outcome=Lapsed(), at=WALL))
    store.apply(CommandsRestored(S, (lapsed,)), now=3.0)
    # A batch holding an open command is not what a restore yields, and is void.
    store.apply(CommandsRestored(S, (asked(5),)), now=4.0)

    snap = store.snapshot()
    assert set(snap.commands) == {cid(4)}
    assert owed_commands(snap) == [] and snap.command_since == {}


# -- theme --------------------------------------------------------------------------------


def test_a_command_has_a_glyph_no_other_row_and_no_state_has() -> None:
    glyphs = OBLIGATION_GLYPH
    others = {glyphs[kind] for kind in ObligationKind if kind is not ObligationKind.COMMAND}

    assert glyphs[ObligationKind.COMMAND] not in others
    assert glyphs[ObligationKind.COMMAND] not in STATE_GLYPH.values()


def test_a_command_is_drawn_in_the_alarm_role() -> None:
    assert theme._OBLIGATION_ROLE[ObligationKind.COMMAND] == "danger"
    for palette in theme.THEMES.values():
        assert palette.obligation(ObligationKind.COMMAND) == palette.danger


# -- the digest ---------------------------------------------------------------------------


def test_the_waiting_slot_counts_commands_by_state_and_names_their_askers() -> None:
    store = team()
    request(store, 1, CHILD, now=5.0)
    request(store, 2, PEER, now=6.0)
    request(store, 3, ROOT, now=7.0)
    approve(store, 2, now=8.0)

    waiting = digest.digest(store.snapshot(), S).waiting

    assert [(o.kind, o.who, o.since, o.command) for o in waiting] == [
        (ObligationKind.COMMAND, "builder", 5.0, CommandState.ASKED),
        (ObligationKind.COMMAND, "builder-2", 6.0, CommandState.RUNNING),
        (ObligationKind.COMMAND, "lead", 7.0, CommandState.ASKED),
    ]
    assert digest.owed_counts(waiting) == (
        f"2 command(s) {digest.COMMAND_ASKED}, 1 command(s) {digest.COMMAND_RUNNING}"
    )
    assert [digest.owed_standing(o) for o in waiting] == [
        digest.COMMAND_ASKED,
        digest.COMMAND_RUNNING,
        digest.COMMAND_ASKED,
    ]
    assert not any(MARK in o.summary for o in waiting)


def test_only_a_command_has_a_standing_in_the_waiting_slot() -> None:
    store = team()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, at=3.0).approval), now=3.0)

    (owed,) = digest.digest(store.snapshot(), S).waiting

    assert owed.command is None and digest.owed_standing(owed) == ""
    assert digest.owed_counts((owed,)) == "1 approval(s)"


def test_an_ended_command_is_not_waiting() -> None:
    store = team()
    request(store, 1, now=5.0)
    store.apply(CommandRefused(cid(1), "no", WALL + 6.0), now=6.0)

    assert digest.digest(store.snapshot(), S).waiting == ()


# -- the queue row ------------------------------------------------------------------------


def test_the_queue_row_draws_the_command_as_a_bash_row_does_behind_where_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The user, 2026-10-15: it "should mirror any other bash request". The
    summary is ``approval.summarize`` of it as a Bash call, escaped and
    flattened as a Bash row's is; the store's own summary, built from
    structure, is the digest's and the rail's and not this row's.
    """
    store = team()
    request(store, 1, now=5.0)
    snap = store.snapshot()
    (row,) = snap.needs_you

    drawn = _row_text(monkeypatch, snap, row)

    said = policy.summarize("Bash", {"command": ASKED})
    assert inbox.OUTSIDE + said in drawn
    assert said.startswith("2 lines · make ZQX-install")
    assert row.summary not in drawn
    assert OBLIGATION_GLYPH[ObligationKind.COMMAND] in drawn
    # The command, flattened, is the one thing on the row an agent wrote.
    assert [text for text in drawn if MARK in text] == [inbox.OUTSIDE + said]


def test_a_change_to_how_a_bash_row_is_summarised_is_a_change_to_a_command_row_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    One function, so the two rows cannot drift: the gate parks a Bash call
    with ``approval.summarize`` as its row, and a command's row is that same
    function of it. Here the Bash form inside it is changed, and a Bash call's
    row and a command's row both come out in the new form.
    """
    from pptmstr import driver, fake_driver

    assert driver.summarize is policy.summarize and fake_driver.summarize is policy.summarize
    real = policy.command_shown

    def shouting(tool_name: str, key: str, value: object) -> str | None:
        shown = real(tool_name, key, value)
        return None if shown is None else f"BASH<{shown.upper()}>"

    monkeypatch.setattr(policy, "command_shown", shouting)
    store = team()
    request(store, 1, now=5.0, text="make install")
    snap = store.snapshot()
    (row,) = snap.needs_you

    drawn = _row_text(monkeypatch, snap, row)

    assert driver.summarize("Bash", {"command": "make install"}) == "BASH<MAKE INSTALL>"
    assert inbox.OUTSIDE + "BASH<MAKE INSTALL>" in drawn


def test_a_running_command_and_one_left_running_say_so_ahead_of_the_command() -> None:
    store = team()
    request(store, 1, now=5.0, text="sleep 60")
    approve(store, 1, now=6.0, text="sleep 60")
    running = store.snapshot().commands[cid(1)]
    assert inbox.command_summary(running) == inbox.RUNNING_OUTSIDE + "sleep 60"
    store.apply(CommandStopAsked(cid(1), WALL + 7.0), now=7.0)
    stopping = store.snapshot().commands[cid(1)]
    assert inbox.command_summary(stopping) == (
        inbox.RUNNING_OUTSIDE + inbox.CANCEL_ASKED + "sleep 60"
    )
    left = _left_running().commands[cid(1)]
    assert inbox.command_summary(left).startswith(inbox.UNSUPERVISED)


def _expanded(
    monkeypatch: pytest.MonkeyPatch, snap: Snapshot, row: Any, given: inbox.CommandRows | None
) -> tuple[Any, list[dict[str, Any]]]:
    """The expanded row, with ``command_view.draw_row`` recording what it was handed."""
    fake = _stub_row_imgui(monkeypatch)
    handed: list[dict[str, Any]] = []

    def draw_row(state: object, command: object, **kwargs: Any) -> None:
        handed.append({"state": state, "command": command, "disabled": _depth(fake), **kwargs})

    monkeypatch.setattr(command_view, "draw_row", draw_row)
    inbox._row(
        snap,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        obligation=row,
        expanded=True,
        now=100.0,
        wrap=False,
        commands=given,
    )
    return fake, handed


def _depth(fake: Any) -> int:
    return int(fake.begin_disabled.call_count - fake.end_disabled.call_count)


def test_an_expanded_command_row_is_drawn_by_the_command_view_with_what_it_was_handed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wiring: a body nothing calls leaves every one of its own tests green."""
    store = team()
    request(store, 1, now=5.0)
    snap = store.snapshot()
    (row,) = snap.needs_you
    given = rows(wired=True)

    fake, (body,) = _expanded(monkeypatch, snap, row, given)

    assert body["state"] is given.state and body["actions"] is given.actions
    assert body["command"] is snap.commands[cid(1)]
    assert body["role"] == "builder"
    # The wall clock the pane was handed, and not the queue's 100.0.
    assert body["now"] == WALL + 50.0
    assert body["wrap"] is False
    assert body["where"] == command_view.Where(
        shell="/bin/bash",
        cwd="/work/tree",
        stripped_env=("VIRTUAL_ENV (/v) is unset",),
        home=os.path.expanduser("~"),
    )
    assert body["disabled"] == 0 and fake.begin_disabled.call_count == 0
    drawn = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]
    assert inbox.NOT_WIRED not in drawn
    # The summary is the one line of the row's own that holds the command.
    assert [text for text in drawn if MARK in text] == [inbox.command_summary(row.command)]


def test_until_it_is_wired_the_row_says_so_and_its_body_is_drawn_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    request(store, 1, now=5.0)
    snap = store.snapshot()
    (row,) = snap.needs_you

    fake, (body,) = _expanded(monkeypatch, snap, row, rows())

    assert body["disabled"] == 1
    assert _depth(fake) == 0
    drawn = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]
    assert inbox.NOT_WIRED in drawn
    assert "not wired in this build" in inbox.NOT_WIRED


def test_a_row_handed_nothing_to_draw_a_command_with_says_so_and_draws_no_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    request(store, 1, now=5.0)
    snap = store.snapshot()
    (row,) = snap.needs_you

    fake, handed = _expanded(monkeypatch, snap, row, None)

    assert handed == []
    fake.text_disabled.assert_called_once_with(inbox.NO_COMMAND_ROWS)


def test_a_session_whose_record_names_no_directory_is_not_given_one() -> None:
    store = Store()
    store.apply(AgentSpawned(node_id=ROOT, parent=None, task="t", model="m", started_at=0.0), now=1)
    request(store, 1, ROOT, now=5.0)
    snap = store.snapshot()

    found = inbox.where(snap, rows(shell=None), snap.commands[cid(1)])
    assert found == command_view.Where(
        shell=None,
        cwd=inbox.NO_DIRECTORY,
        stripped_env=("VIRTUAL_ENV (/v) is unset",),
        home=os.path.expanduser("~"),
    )
    # And no directory is no path a command could be said to write out.
    assert found.roots == ()


def test_the_directory_is_the_sessions_whatever_the_askers_own_record_says() -> None:
    """The runner starts the shell in the session's directory, so that is what the row names."""
    from types import MappingProxyType

    store = team()
    request(store, 1, now=5.0)
    snap = store.snapshot()
    moved = {**snap.nodes, CHILD: snap.nodes[CHILD].with_(cwd="/somewhere/else")}
    snap = dataclasses.replace(snap, nodes=MappingProxyType(moved))

    assert inbox.where(snap, rows(), snap.commands[cid(1)]).cwd == "/work/tree"


def test_the_header_says_no_key_acts_on_a_command_and_the_buttons_take_the_mouse() -> None:
    store = team()
    request(store, 1, now=5.0)
    (row,) = store.snapshot().needs_you

    hint = inbox.keys_hint(row)

    assert hint == inbox.COMMAND_KEYS
    assert "no key acts on a command" in hint and "take the mouse" in hint
    assert "a approve" not in hint and "Ctrl+Enter" not in hint


def test_the_inbox_attends_the_command_state_once_a_frame_with_the_row_under_the_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``inbox.draw`` is what calls ``attend``. Unhooked, an armed run outlives the
    cursor leaving its row and an edit outlives its command.
    """
    store = team()
    request(store, 1, now=5.0)
    request(store, 2, PEER, now=6.0)
    state = CommandViewState(
        edits={cid(1): "edited"},
        pending=FrozenRun(cid(1), "edited", 600),
        armed_at=0.0,
        opened=cid(1),
    )
    given = rows(state)
    for name in ("_header", "_row", "_zero_state", "_batch_controls"):
        monkeypatch.setattr(inbox, name, MagicMock())
    fake = MagicMock()
    fake.get_frame_count.return_value = 7
    monkeypatch.setattr(inbox, "imgui", fake)
    seen: list[str | None] = []
    frames: list[int] = []
    attend = state.attend

    def attended(self: CommandViewState, commands: Any, focused: Any, frame: int) -> None:
        seen.append(focused)
        frames.append(frame)
        attend(commands, focused, frame)

    monkeypatch.setattr(CommandViewState, "attend", attended)

    def frame(focus: FocusState) -> None:
        inbox.draw(
            store.snapshot(),
            focus,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            100.0,
            decisions=MagicMock(),
            commands=given,
        )

    first, second = owed_commands(store.snapshot())
    frame(FocusState(target=OnObligation(first.key)))
    assert seen == [cid(1)] and isinstance(state.pending, FrozenRun)
    # Dear ImGui's own count of frames, which is what tells a skipped one.
    assert frames == [7] and state.attended == 7 and state.is_open(cid(1))

    frame(FocusState(target=OnObligation(second.key)))
    assert seen == [cid(1), cid(2)]
    assert state.pending is None and state.edits == {cid(1): "edited"}
    # The cursor on another row closes the one that was open, and opens none.
    assert state.opened is None

    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 9.0), now=9.0)
    frame(FocusState(target=OnObligation(second.key)))
    assert state.edits == {} and len(seen) == 3


@pytest.mark.parametrize("shift", [False, True])
def test_the_approval_keys_do_nothing_on_a_command_row(
    monkeypatch: pytest.MonkeyPatch, shift: bool
) -> None:
    """
    ``a``, ``A``, ``r``, ``e``, Enter and Space on a command must not run,
    refuse or edit it -- and with an approval waiting beside it, must not
    approve *that* either.
    """
    store = team()
    request(store, 1, now=5.0)
    store.apply(ApprovalRequested(ROOT, approval(ROOT, at=6.0).approval), now=6.0)
    snap = store.snapshot()
    row = next(o for o in snap.needs_you if o.kind is ObligationKind.COMMAND)
    focus = FocusState(target=OnObligation(row.key))

    fake = MagicMock()
    fake.Key = real_imgui.Key
    fake.get_io.return_value.want_capture_keyboard = False
    fake.get_io.return_value.want_text_input = False
    fake.is_mouse_clicked.return_value = False
    fake.get_io.return_value.key_shift = shift
    # A mock's attributes are truthy: unset, every key would read as a chord.
    fake.get_io.return_value.key_ctrl = False
    fake.get_io.return_value.key_alt = False
    fake.get_io.return_value.key_super = False
    pressed = {
        real_imgui.Key.a,
        real_imgui.Key.r,
        real_imgui.Key.e,
        real_imgui.Key.enter,
        real_imgui.Key.keypad_enter,
        real_imgui.Key.space,
    }
    fake.is_key_pressed.side_effect = lambda key: key in pressed
    monkeypatch.setattr(review, "imgui", fake)
    bridge = MagicMock()
    state = review.ReviewState()

    review.handle_keys(snap, focus, state, bridge)

    assert bridge.mock_calls == []
    assert state.editing is None and not state.focus_reason and state.edits == {}
    assert not state.hold.held
    assert focus.target == OnObligation(row.key)
    assert store.snapshot() is snap

    # The same keys on the approval beside it do act, so the fake is pressing them.
    parked = next(o for o in snap.needs_you if o.kind is ObligationKind.APPROVAL)
    review.handle_keys(snap, FocusState(target=OnObligation(parked.key)), state, bridge)
    assert bridge.resolve.call_count >= 1 or state.editing is not None


# -- Dear ImGui itself: the row as the inbox draws it --------------------------------------
#
# A context with a display size and no backend: nothing is rendered. Keyboard
# navigation is on, since that is the configuration in which a key can press a
# button at all.


class Frames:
    """``inbox.draw_command`` in real frames, with where each button landed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, wired: bool, enabled: bool) -> None:
        store = team()
        request(store, 1, now=5.0, text="make install")
        self.snap = store.snapshot()
        self.command = self.snap.commands[cid(1)]
        self.called: list[tuple[object, ...]] = []
        self.state = CommandViewState()
        self.rows = rows(
            self.state,
            wired=wired,
            opened=cid(1),
            actions=Actions(
                on_run=lambda *a: self.called.append(("run", *a)),
                on_refuse=lambda *a: self.called.append(("refuse", *a)),
                on_stop=lambda *a: self.called.append(("stop", *a)),
            ),
        )
        self.enabled = enabled
        self.at: dict[str, tuple[float, float]] = {}
        self._monkeypatch = monkeypatch

    def __enter__(self) -> Frames:
        imgui = real_imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        at = self.at

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def button(self, label: str) -> bool:
                pressed = imgui.button(label)
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
                return bool(pressed)

        self._monkeypatch.setattr(command_view, "imgui", Noting())
        return self

    def __exit__(self, *exc: object) -> None:
        real_imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            real_imgui.new_frame()
            self.state.attend(self.snap.commands, self.command.id, real_imgui.get_frame_count())
            real_imgui.set_next_window_pos(real_imgui.ImVec2(0.0, 0.0))
            real_imgui.set_next_window_size(real_imgui.ImVec2(1200.0, 880.0))
            real_imgui.begin("needs you")
            inbox.draw_command(self.snap, self.rows, self.command, enabled=self.enabled)
            real_imgui.end()
            real_imgui.end_frame()

    def click(self, label: str) -> None:
        x, y = self.at[label]
        self.io.add_mouse_pos_event(x, y)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)

    def every_key(self) -> None:
        for _ in range(8):
            for key in (
                real_imgui.Key.tab,
                real_imgui.Key.space,
                real_imgui.Key.enter,
                real_imgui.Key.keypad_enter,
                real_imgui.Key.a,
                real_imgui.Key.r,
                real_imgui.Key.e,
            ):
                self.io.add_key_event(key, True)
                self.frame()
                self.io.add_key_event(key, False)
                self.frame(2)
            self.io.add_input_characters_utf8("a")
            self.frame(2)


@pytest.fixture
def frames(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[Frames]:
    wired, enabled = getattr(request, "param", (False, True))
    with Frames(monkeypatch, wired=wired, enabled=enabled) as real:
        real.frame(3)
        yield real


PRESSES = (command_view.RUN, command_view.EDIT, command_view.REFUSE)


@pytest.mark.parametrize("frames", [(True, False)], ids=["detail"], indirect=True)
def test_in_dear_imgui_details_copy_draws_no_press_and_no_key_acts_on_it(
    frames: Frames,
) -> None:
    """
    DETAIL's copy of a wired row, as DETAIL draws a Bash approval: the body,
    and no button of the command's own to click.
    """
    assert not set(PRESSES) & set(frames.at)
    untouched = dataclasses.replace(frames.state)
    frames.every_key()
    assert frames.state == untouched and frames.state.pending is None
    assert frames.called == []


@pytest.mark.parametrize("frames", [(False, True)], ids=["not-wired"], indirect=True)
def test_in_dear_imgui_nothing_on_a_dead_row_answers_the_mouse_or_any_key(
    frames: Frames,
) -> None:
    """
    Not wired: every button is drawn, and a click on each and every key that
    presses changes nothing and calls nothing.
    """
    assert set(PRESSES) <= set(frames.at)
    untouched = dataclasses.replace(frames.state)

    for label in PRESSES:
        frames.click(label)
    frames.every_key()

    assert frames.state == untouched and frames.state.pending is None
    assert frames.called == []


@pytest.mark.parametrize("frames", [(True, True)], ids=["wired"], indirect=True)
def test_in_dear_imgui_the_same_clicks_act_on_a_wired_row_and_the_keys_still_do_not(
    frames: Frames,
) -> None:
    """The control for the test above: its clicks land, so what stopped them was the row."""
    untouched = dataclasses.replace(frames.state)
    frames.every_key()
    assert frames.state == untouched and frames.called == []

    frames.click(command_view.RUN)

    assert frames.state.pending == FrozenRun(cid(1), "make install", 600)
    assert frames.called == []


# -- DETAIL -------------------------------------------------------------------------------


def _detail(
    monkeypatch: pytest.MonkeyPatch,
    snap: Snapshot,
    focus: FocusState,
    given: inbox.CommandRows | None,
) -> list[tuple[Any, ...]]:
    """Which command bodies ``detail.draw`` reached, in order, with the pixels stubbed."""
    reached: list[tuple[Any, ...]] = []
    fake = MagicMock()
    fake.tree_node_ex.return_value = True
    fake.small_button.return_value = False
    monkeypatch.setattr(detail, "imgui", fake)
    monkeypatch.setattr(widgets, "small_button", fake.small_button)
    monkeypatch.setattr(inbox, "imgui", fake)
    monkeypatch.setattr(detail, "_header", MagicMock())
    monkeypatch.setattr(detail.widgets, "section", MagicMock())
    monkeypatch.setattr(detail, "_narration", MagicMock())
    monkeypatch.setattr(detail.decision_view, "draw_closed", MagicMock())
    monkeypatch.setattr(
        command_view,
        "draw_row",
        lambda state, command, **kw: reached.append(
            (
                "row",
                state,
                command.id,
                kw["role"],
                kw["now"],
                kw["actions"],
                _depth(fake),
                kw["presses"],
            )
        ),
    )
    monkeypatch.setattr(
        command_view,
        "draw_ended",
        lambda command, **kw: reached.append(
            ("ended", command.id, kw["role"], kw["now"], kw["log_path"])
        ),
    )
    detail.draw(snap, focus, review.ReviewState(), detail.DetailState(), 10.0, None, given)
    reached.append(("text", [str(c.args[-1]) for c in fake.text_colored.call_args_list]))
    reached.append(("labels", [str(c.args[0]) for c in fake.tree_node_ex.call_args_list]))
    return reached


def _history() -> Store:
    """One open command and three ended ones, the last of them read by its asker."""
    store = team()
    request(store, 1, now=5.0)
    request(store, 2, now=6.0)
    request(store, 3, PEER, now=7.0)
    request(store, 4, PEER, now=8.0)
    store.apply(CommandRefused(cid(2), "not on this machine", WALL + 9.0), now=9.0)
    approve(store, 3, now=10.0)
    finish(store, 3, now=20.0)
    store.apply(CommandWithdrawn(PEER, cid(4), None, WALL + 30.0), now=30.0)
    return store


def test_a_focused_command_shows_the_rows_body_with_no_press_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The same state object the row draws from, so the two panes cannot show
    different edits -- disabled even when the row is wired, and drawn with no
    press at all, as DETAIL draws a Bash approval: a second press has one
    place it can land.
    """
    store = _history()
    snap = store.snapshot()
    (row,) = owed_commands(snap)
    given = rows(wired=True)

    reached = _detail(monkeypatch, snap, FocusState(target=OnObligation(row.key)), given)

    assert reached[0] == (
        "row",
        given.state,
        cid(1),
        "builder",
        WALL + 50.0,
        given.actions,
        1,
        False,
    )
    assert reached[0][1] is given.state


def test_detail_lists_the_sessions_ended_commands_newest_first_through_the_command_view(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr("pptmstr.settings.config_dir", lambda: tmp_path)
    store = _history()
    snap = store.snapshot()
    (row,) = owed_commands(snap)
    log = str(commandfile.command_dir(S, cid(3)) / commandfile.LOG_NAME)  # type: ignore[operator]

    reached = _detail(monkeypatch, snap, FocusState(target=OnObligation(row.key)), rows())

    assert [entry for entry in reached if entry[0] == "ended"] == [
        ("ended", cid(4), "builder-2", WALL + 50.0, None),
        ("ended", cid(3), "builder-2", WALL + 50.0, log),
        ("ended", cid(2), "builder", WALL + 50.0, None),
    ]
    text, labels = dict(reached[-2:])["text"], dict(reached[-2:])["labels"]
    # Outside the triangle: what its asker has not read. A withdrawal is its own.
    assert f"ended, not yet read by builder: {cid(2)}" in text
    assert f"ended, not yet read by builder-2: {cid(3)}" in text
    assert not any(cid(4) in line for line in text)
    assert labels[0] == "commands ended in this session (3)###ended-commands"
    assert labels[2].startswith(f"{cid(3)} from builder-2: exit code 0")
    assert not any(MARK in line for line in text + labels)


def test_the_ended_list_is_drawn_for_the_session_under_a_cursor_that_owes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    request(store, 1, now=5.0)
    store.apply(CommandRefused(cid(1), "no", WALL + 9.0), now=9.0)
    snap = store.snapshot()
    focus = FocusState()
    focus.to_node(snap, ROOT, scope=Scope.AGENT)

    reached = _detail(monkeypatch, snap, focus, rows())

    assert reached[0][:2] == ("ended", cid(1))


def test_a_result_the_asker_has_read_is_no_longer_said_to_be_unread() -> None:
    store = _history()
    assert len(detail.unread_command_lines(store.snapshot(), S)) == 2

    store.apply(CommandsRead(CHILD, "rq", WALL + 40.0, cid(2)), now=40.0)

    assert detail.unread_command_lines(store.snapshot(), S) == (
        f"ended, not yet read by builder-2: {cid(3)}",
    )
    assert detail.unread_command_lines(store.snapshot(), "another") == ()


def test_only_a_command_that_started_a_process_has_a_log_to_point_at(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr("pptmstr.settings.config_dir", lambda: tmp_path)
    snap = _history().snapshot()

    assert detail.log_path(snap.commands[cid(1)]) is None
    assert detail.log_path(snap.commands[cid(2)]) is None
    assert detail.log_path(snap.commands[cid(3)]) == commandbus.log_path(snap.commands[cid(3)])
    assert detail.log_path(snap.commands[cid(3)]) is not None


# -- the clipboard ------------------------------------------------------------------------


def test_the_clipboards_three_marks_are_the_buss() -> None:
    assert detail.ASKER_MARK == bus._QUOTE
    assert detail.OPERATOR_MARK == bus._ANSWER
    assert detail.OUTPUT_MARK == commandbus._OUTPUT
    assert len({detail.ASKER_MARK, detail.OPERATOR_MARK, detail.OUTPUT_MARK}) == 3


def _authors(lines: list[str]) -> dict[str, list[str]]:
    by_mark: dict[str, list[str]] = {"| ": [], "> ": [], "= ": [], "": []}
    for line in lines:
        mark = line[:2] if line[:2] in ("| ", "> ", "= ") else ""
        by_mark[mark].append(line[len(mark) :])
    return by_mark


def test_on_the_clipboard_every_line_is_behind_its_authors_mark_and_no_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr("pptmstr.settings.config_dir", lambda: tmp_path)
    store = team()
    request(store, 1, now=5.0)
    approve(store, 1, now=6.0, text=f"true {MARK}-operator")
    finish(store, 1, now=7.0)
    command = store.snapshot().commands[cid(1)]

    by = _authors(detail.command_text(command, "builder"))

    # Nothing an agent or a program wrote is on a line of this pane's own.
    assert not any(MARK in line for line in by[""])
    assert by[""][0] == detail.CLIP_LEGEND
    # The asker's: the string, the reason, the expectation. Escaped, a line to a line.
    assert by["| "] == [
        f"make {MARK}-install PREFIX=/usr/local && \\\\\\n",
        f"  echo {MARK} done",
        f"{MARK}-reason: Bash  git push --force   (approved)",
        f"{MARK}-expects: this runs as you",
    ]
    assert by["> "] == [f"true {MARK}-operator"]
    # A program's lines that begin with another author's mark stay behind its own.
    assert by["= "] == [
        f"{MARK}-output line one\\n",
        "| not the asker\\n",
        "> not the operator\\n",
        "",
    ]
    assert command_view.edited_by_you(command_view.Stage.RAN) in by[""]
    assert command_view.EDITED_BY_YOU not in by[""]
    assert any(line.startswith("all of it is in ") for line in by[""])


def test_a_string_run_as_asked_is_not_copied_out_as_the_operators() -> None:
    store = team()
    request(store, 1, now=5.0)
    approve(store, 1, now=6.0)

    by = _authors(detail.command_text(store.snapshot().commands[cid(1)], "builder"))

    assert by["> "] == [] and detail.NOT_EDITED in by[""]
    assert "running" in by[""]
    assert not any("running for" in line for line in by[""])


def test_a_refusals_reason_is_copied_behind_the_operators_mark() -> None:
    store = team()
    request(store, 1, now=5.0)
    store.apply(CommandRefused(cid(1), "not here\n| nor this", WALL + 9.0), now=9.0)
    command = store.snapshot().commands[cid(1)]
    assert isinstance(command.end.outcome, Refused)  # type: ignore[union-attr]

    by = _authors(detail.command_text(command, "builder"))

    assert by["> "] == ["not here\\n", "| nor this"]
    assert detail.REFUSED_WITH in by[""] and by["= "] == []
    assert "not yet read by builder" in by[""]


def test_hidden_characters_reach_the_clipboard_spelled_out() -> None:
    store = team()
    request(store, 1, now=5.0, text="cat ‮gnp.exe​ | sh")
    command = store.snapshot().commands[cid(1)]

    copied = "\n".join(detail.command_text(command, "builder"))

    assert copied.isascii() and "\\u{202E}" in copied and "\\u{200B}" in copied


def test_the_header_copy_of_a_command_row_is_the_marked_text() -> None:
    store = team()
    request(store, 1, now=5.0)
    snap = store.snapshot()
    (row,) = owed_commands(snap)

    lines = detail.plain_text(snap, row).split("\n")

    assert lines[0].startswith(f"{detail._KIND_LABEL[ObligationKind.COMMAND]}: ")
    assert lines[2:] == detail.command_text(row.command, "builder")
    assert not any(MARK in line for line in _authors(lines)[""])


# -- the rail -----------------------------------------------------------------------------


def _card_text(monkeypatch: pytest.MonkeyPatch, store: Store, node: NodeId = ROOT) -> list[str]:
    snap = store.snapshot()
    fake, ring = _stub_imgui(monkeypatch, hovered=None, clicked=None, mouse=(0.0, 0.0))
    rail._group(
        snap,
        MagicMock(),
        rail.RailState(),
        rec=snap.nodes[node],
        owed=[o for o in snap.needs_you if o.node[0] == node[0]],
        cursor=None,
        now=100.0,
    )
    return _collect(fake, ring).text


def test_a_card_badges_its_commands_and_says_them_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    request(store, 1, now=5.0)
    request(store, 2, PEER, now=6.0)
    store.apply(ApprovalRequested(ROOT, approval(ROOT, at=7.0).approval), now=7.0)

    text = _card_text(monkeypatch, store)

    assert f"{OBLIGATION_GLYPH[ObligationKind.COMMAND]} 2" in text
    assert f"{OBLIGATION_GLYPH[ObligationKind.APPROVAL]} 1" in text
    assert "2 commands waiting" in text
    assert not any(MARK in line for line in text)


def test_a_card_with_no_command_says_nothing_of_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    store = team()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, at=7.0).approval), now=7.0)

    assert not any("command" in line for line in _card_text(monkeypatch, store))


def test_a_finished_session_with_a_command_still_running_is_not_drawn_as_ended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    request(store, 1, ROOT, now=5.0)
    approve(store, 1, now=6.0)
    for node in (CHILD, PEER, ROOT):
        store.apply(AgentFinished(node, AgentState.DONE, 7.0), now=7.0)
    snap = store.snapshot()
    owed = [o for o in snap.needs_you if o.node[0] == S]

    assert rail._density(snap.nodes[ROOT], owed) == "active"
    assert "1 command waiting" in _card_text(monkeypatch, store)


# -- the fold of ended sub-agents ----------------------------------------------------------


def _folded(store: Store) -> tuple[NodeId, ...]:
    return fleet_view.fold(store.snapshot(), ROOT, None, True).folded


def test_an_ended_sub_agent_with_a_command_asked_or_running_is_not_folded() -> None:
    store = team()
    request(store, 1, now=5.0)
    store.apply(AgentFinished(CHILD, AgentState.DONE, 6.0), now=6.0)
    store.apply(AgentFinished(PEER, AgentState.DONE, 6.0), now=6.0)
    assert _folded(store) == (PEER,)

    approve(store, 1, now=7.0)
    assert _folded(store) == (PEER,)


def test_the_fold_asks_the_records_and_not_only_the_queue_for_an_open_command() -> None:
    """A snapshot whose queue lost the row must still not fold away what can run."""
    store = team()
    request(store, 1, now=5.0)
    store.apply(AgentFinished(CHILD, AgentState.DONE, 6.0), now=6.0)
    blind = dataclasses.replace(store.snapshot(), needs_you=())

    assert CHILD not in fleet_view.fold(blind, ROOT, None, True).folded


def test_a_result_its_asker_has_not_read_keeps_it_drawn_until_it_reads_it() -> None:
    store = team()
    request(store, 1, now=5.0)
    store.apply(AgentFinished(CHILD, AgentState.DONE, 6.0), now=6.0)
    store.apply(CommandRefused(cid(1), "no", WALL + 7.0), now=7.0)
    assert CHILD not in _folded(store)

    # The root's read is not the asker being handed it.
    store.apply(CommandsRead(ROOT, "rq", WALL + 8.0, cid(1)), now=8.0)
    assert CHILD not in _folded(store)

    store.apply(CommandsRead(CHILD, "rq", WALL + 9.0, cid(1)), now=9.0)
    assert CHILD in _folded(store)


def test_a_command_it_withdrew_itself_does_not_keep_it_drawn() -> None:
    store = team()
    request(store, 1, now=5.0)
    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 6.0), now=6.0)
    store.apply(AgentFinished(CHILD, AgentState.DONE, 7.0), now=7.0)

    assert CHILD in _folded(store)


# -- the application: a state field and a pass-through --------------------------------------


def _app_state() -> Any:
    from pptmstr import app

    state = app.AppState(store=Store(), bridge=Bridge(), settings=Settings())
    state.pool = MagicMock()
    state.frame_snap = state.store.snapshot()
    state.frame_now, state.frame_wall = 12.0, WALL + 12.0
    return state


def test_the_shell_is_looked_for_once_at_startup_and_never_in_a_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pptmstr import app
    from pptmstr.settings import Loaded
    from tests.test_app import _args

    looked: list[str] = []
    monkeypatch.setattr(
        commandrun,
        "shell_for",
        lambda: looked.append("shell") or commandrun.Shell("/bin/zsh", True, "SHELL"),
    )
    monkeypatch.setattr(
        commandrun, "environment_changes", lambda: looked.append("env") or ("PATH loses /v/bin",)
    )
    handed: list[Any] = []
    monkeypatch.setattr(inbox, "draw", lambda *a, **kw: handed.append(kw["commands"]))
    monkeypatch.setattr(detail, "draw", lambda *a, **kw: handed.append(a[6]))

    state = app._start(_args(fake=True), Loaded(Settings()))  # type: ignore[arg-type]
    assert looked == ["shell", "env"]
    assert state.command_host == app.CommandHost("/bin/zsh", ("PATH loses /v/bin",))

    state.pool = MagicMock()
    state.frame_snap = state.store.snapshot()
    state.frame_wall = WALL + 12.0
    # _reviewing reads ImGui's frame count, and there is no context here.
    monkeypatch.setattr(app.imgui, "get_frame_count", lambda: 1)
    panels = app._panels(state)
    for _ in range(3):
        panels["NEEDS YOU"]()
        panels["DETAIL"]()

    assert looked == ["shell", "env"]
    assert len(handed) == 6
    for given in handed:
        assert given.state is state.commands
        assert (given.shell, given.stripped_env) == ("/bin/zsh", ("PATH loses /v/bin",))
        assert given.now == WALL + 12.0 and given.wired is True
        assert given.unrecorded is state.command_unrecorded
        assert given.restored is state.command_restored


def test_a_shell_nothing_names_is_handed_on_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from pptmstr import app

    monkeypatch.setattr(commandrun, "shell_for", lambda: commandrun.NoShell("$SHELL is not set"))
    monkeypatch.setattr(commandrun, "environment_changes", lambda: ())

    assert app._command_host() == app.CommandHost(None, (), no_shell="$SHELL is not set")


def test_a_state_nothing_looked_for_a_shell_for_hands_the_panes_nothing() -> None:
    """ "No usable shell" is a finding, and a state built without a start has not made it."""
    from pptmstr import app

    state = _app_state()

    assert state.command_host is None and app._command_rows(state) is None


def test_only_the_application_builds_the_intents_that_act_on_a_command() -> None:
    """
    Read off the source: no module that draws builds the three intents that
    start, refuse or stop a command, so a pane can only call what it was handed.
    The application builds each of them, in one place each.
    """
    from pptmstr import app
    from pptmstr.ui import digest_pane

    for intent in ("CommandApproved", "CommandRefused", "CommandStopAsked"):
        for module in (inbox, detail, rail, fleet_view, command_view, digest_pane):
            assert f"{intent}(" not in inspect.getsource(module), (module.__name__, intent)
        assert inspect.getsource(app).count(f"{intent}(") == 1, intent


# -- what the application says of a command's files ------------------------------------------


def test_a_command_whose_record_is_not_on_disk_says_so_on_its_row_and_in_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    request(store, 1, now=5.0)
    snap = store.snapshot()
    (row,) = snap.needs_you
    given = dataclasses.replace(
        rows(wired=True), unrecorded={cid(1): "what was asked is not recorded: EROFS\nsecond"}
    )

    fake, _ = _expanded(monkeypatch, snap, row, given)

    said = "not recorded on disk - what was asked is not recorded: EROFS second"
    assert inbox.unrecorded_line(given, row.command) == said
    assert said in [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]
    assert inbox.unrecorded_line(rows(wired=True), row.command) is None
    assert inbox.unrecorded_line(None, row.command) is None

    reached = _detail(monkeypatch, snap, FocusState(target=OnObligation(row.key)), given)
    assert said in dict(reached[-2:])["text"]


def test_an_ended_command_whose_end_is_not_on_disk_says_so_in_the_ended_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    request(store, 1, now=5.0)
    store.apply(CommandRefused(cid(1), "no", WALL + 9.0), now=9.0)
    snap = store.snapshot()
    focus = FocusState()
    focus.to_node(snap, ROOT, scope=Scope.AGENT)
    given = dataclasses.replace(rows(), unrecorded={cid(1): "how it ended is not recorded: EIO"})

    reached = _detail(monkeypatch, snap, focus, given)

    assert "not recorded on disk - how it ended is not recorded: EIO" in dict(reached[-2:])["text"]


def test_a_command_read_back_from_a_file_is_not_said_to_be_unread_by_its_asker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    No file holds whether the asker read a result, so every restored command
    would be "not yet read" for good. The warning above the list leaves it out,
    and its body says what the line under it does and does not mean.
    """
    store = _history()
    snap = store.snapshot()
    given = dataclasses.replace(rows(), restored=frozenset({cid(2)}))

    assert detail.unread_command_lines(snap, S, given.restored) == (
        f"ended, not yet read by builder-2: {cid(3)}",
    )
    assert len(detail.unread_command_lines(snap, S)) == 2

    # The body is told which commands came from a file, and the clipboard is too.
    (row,) = owed_commands(snap)
    told: dict[str, bool] = {}
    monkeypatch.setattr(
        command_view,
        "draw_ended",
        lambda command, **kw: told.__setitem__(command.id, kw["restored"]),
    )
    fake = MagicMock()
    fake.tree_node_ex.return_value = True
    fake.small_button.return_value = False
    monkeypatch.setattr(detail, "imgui", fake)
    monkeypatch.setattr(widgets, "small_button", fake.small_button)
    detail.draw_ended_commands(snap, given, S)
    assert told[cid(2)] is True and told[cid(3)] is False

    copied = detail.command_text(snap.commands[cid(2)], "builder", restored=True)
    assert any("from before pptmstr restarted" in line for line in copied)
    assert "not yet read by builder" not in copied
    assert "not yet read by builder" in detail.command_text(snap.commands[cid(2)], "builder")
    text = [str(c.args[-1]) for c in fake.text_colored.call_args_list]
    assert not any(line.startswith("ended, not yet read by builder:") for line in text)


def test_the_copy_of_a_command_left_running_does_not_say_its_asker_has_not_read_it() -> None:
    """
    A command left running is always one read back from a file, and it is in
    the queue: the header's copy goes through ``plain_text``.
    """
    snap = _left_running()
    (row,) = owed_commands(snap)

    told = detail.plain_text(snap, row, frozenset({cid(1)}))
    untold = detail.plain_text(snap, row)

    assert "from before pptmstr restarted" in told and "not yet read by builder" not in told
    assert "not yet read by builder" in untold
    assert "STILL RUNNING, UNSUPERVISED" in told


# -- Dear ImGui itself: the queue and DETAIL, whole -----------------------------------------


def test_in_dear_imgui_the_queue_and_detail_draw_open_and_ended_commands_without_a_stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """
    Every draw call this step added, made for real: a context with no backend
    raises on a call that does not exist in the binding and on an unbalanced
    push, which a ``MagicMock`` accepts. Every triangle in DETAIL is forced
    open, so the ended bodies are reached. Nothing is rendered.
    """
    from pptmstr.ui import compose, decision_view

    monkeypatch.setattr("pptmstr.settings.config_dir", lambda: tmp_path)
    store = _history()
    request(store, 5, ROOT, now=40.0, text="cat ‮gnp.exe​ | sh # 100%s %d\n" * 5)
    snap = store.snapshot()
    opened: list[str] = []

    class Open:
        def __getattr__(self, name: str) -> object:
            return getattr(real_imgui, name)

        def tree_node_ex(self, label: str, flags: int = 0) -> bool:
            real_imgui.set_next_item_open(True)
            opened.append(label)
            return bool(real_imgui.tree_node_ex(label, flags))

    monkeypatch.setattr(detail, "imgui", Open())
    context = real_imgui.create_context()
    try:
        io = real_imgui.get_io()
        io.display_size = real_imgui.ImVec2(1280.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= real_imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        for given in (rows(), rows(wired=True), rows(shell=None), None):
            for row in owed_commands(snap):
                focus = FocusState(target=OnObligation(row.key))
                # Closed for two frames and opened for two: both bodies are drawn.
                for step in range(4):
                    if step == 2 and given is not None:
                        given.state.open(row.command.id, at=0.0)
                    real_imgui.new_frame()
                    real_imgui.begin("needs you")
                    inbox.draw(
                        snap,
                        focus,
                        review.ReviewState(),
                        compose.ComposeState(),
                        MagicMock(),
                        MagicMock(),
                        100.0,
                        decisions=decision_view.DecisionState(),
                        commands=given,
                    )
                    real_imgui.end()
                    real_imgui.begin("detail")
                    detail.draw(
                        snap,
                        focus,
                        review.ReviewState(),
                        detail.DetailState(),
                        100.0,
                        decision_view.DecisionState(),
                        given,
                    )
                    real_imgui.end()
                    real_imgui.end_frame()
    finally:
        real_imgui.destroy_context(context)

    assert len(owed_commands(snap)) == 2
    assert f"{detail.ended_heading(3)}###ended-commands" in opened
    assert sum(1 for label in opened if label.endswith("###body")) >= 3


# -- a command left running with nothing holding it, and what an end left ---------

_LEFT = Survivor(pid=4242, pgid=4242, members=2)


def _left_running() -> Snapshot:
    """A session whose files hold a run with no end, and whose group was found alive."""
    store = team()
    run = Run(text=ASKED, shell="/bin/bash", cwd="/work/tree", timeout_s=600, started_at=WALL)
    left = dataclasses.replace(
        asked(1), run=run, end=End(outcome=EndUnknown(survivor=_LEFT), at=WALL)
    )
    store.apply(CommandsRestored(S, (left,)), now=7.0)
    return store.snapshot()


def test_the_row_of_a_command_left_running_says_how_to_find_it_and_end_it() -> None:
    lines = inbox.survivor_lines(_LEFT)
    assert lines[0] == "STILL RUNNING, UNSUPERVISED."
    assert "The session its shell leads (pid 4242) still has 2 processes" in lines[1]
    assert "process group" not in lines[1]
    assert "no timeout and no cancel of this window's applies" in lines[1]
    # True of a command orphaned by a pptmstr that died AND of one a second
    # window is running right now: nothing on disk tells this window which.
    assert lines[2] == inbox.HELD_ELSEWHERE
    assert "another pptmstr window" in lines[2] and "check there before you kill" in lines[2]
    assert "This window does not hold it" in lines[1]
    assert not any("pptmstr no longer holds it" in line for line in lines)
    assert not any("stopped without recording" in line for line in lines)
    # Each command line is a line by itself, exactly as it would be typed.
    assert "ps -o pid,pgid,lstart,args -s 4242" in lines and "kill -- -4242" in lines
    assert commandrun.kill_line(_LEFT) == "kill -- -4242"
    # What is looked at is the session and what is killed is every group
    # found in it, and each line is introduced as that.
    assert "every process in that session" in lines[lines.index(commandrun.look_line(_LEFT)) - 1]
    assert "every process group found in that session" in (
        lines[lines.index(commandrun.kill_line(_LEFT)) - 1]
    )
    several = Survivor(pid=4242, pgid=4242, members=3, groups=(4242, 4250))
    assert "kill -- -4242 -4250" in inbox.survivor_lines(several)
    assert lines[-1] == inbox.WAYS_OUT
    one = inbox.survivor_lines(Survivor(pid=7, pgid=9, members=1))
    assert "still has 1 process." in one[1] and "kill -- -9" in one
    # The session is named by the shell that leads it, and looked at by that number.
    assert "(pid 7)" in one[1] and "ps -o pid,pgid,lstart,args -s 7" in one
    assert not any(MARK in line for line in lines)


def test_a_command_left_running_is_in_the_queue_in_its_own_words() -> None:
    snap = _left_running()
    (owed,) = owed_commands(snap)
    assert owed.command.unsupervised == _LEFT and owed.since == 7.0
    assert owed.summary.startswith("command STILL RUNNING, unsupervised, from builder: ")
    assert MARK not in owed.summary
    assert snap.detach_refusal(S) is not None


class _Survivors:
    """``inbox.draw_command`` for a command left running, in real frames."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
        self.snap = _left_running()
        self.command = self.snap.commands[cid(1)]
        self.checked: list[str] = []
        self.released: list[str] = []
        self.copied: list[str] = []
        self.texts: list[str] = []
        self.enabled = enabled
        self.rows = dataclasses.replace(
            rows(wired=True, opened=cid(1)),
            on_check=self.checked.append,
            checked={cid(1): "looked: there"},
            on_release=self.released.append,
        )
        self.at: dict[str, tuple[float, float]] = {}
        imgui = real_imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        at, copied, texts = self.at, self.copied, self.texts

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def button(self, label: str) -> bool:
                pressed = imgui.button(label)
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
                return bool(pressed)

            def set_clipboard_text(self, text: str) -> None:
                copied.append(text)

            def text_colored(self, colour: object, text: str) -> None:
                texts.append(text)
                imgui.text_colored(colour, text)  # type: ignore[arg-type]

            def text_unformatted(self, text: str) -> None:
                texts.append(text)
                imgui.text_unformatted(text)

        monkeypatch.setattr(inbox, "imgui", Noting())
        monkeypatch.setattr(command_view, "imgui", Noting())
        real_small_button = widgets.small_button

        def small_button(label: str) -> bool:
            pressed = real_small_button(label)
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
            return pressed

        monkeypatch.setattr(widgets, "small_button", small_button)

    def close(self) -> None:
        real_imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            real_imgui.new_frame()
            self.rows.state.attend(
                self.snap.commands, self.command.id, real_imgui.get_frame_count()
            )
            real_imgui.set_next_window_pos(real_imgui.ImVec2(0.0, 0.0))
            real_imgui.set_next_window_size(real_imgui.ImVec2(1200.0, 880.0))
            real_imgui.begin("needs you")
            inbox.draw_command(self.snap, self.rows, self.command, enabled=self.enabled)
            real_imgui.end()
            real_imgui.end_frame()

    def click(self, label: str) -> None:
        x, y = self.at[label]
        self.io.add_mouse_pos_event(x, y)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)


@pytest.fixture
def survivors(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[_Survivors]:
    drawn = _Survivors(monkeypatch, enabled=getattr(request, "param", True))
    try:
        drawn.frame(3)
        yield drawn
    finally:
        drawn.close()


def test_in_dear_imgui_the_row_of_a_command_left_running_is_drawn_and_only_a_click_looks_again(
    survivors: _Survivors,
) -> None:
    for line in inbox.survivor_lines(_LEFT):
        assert line in survivors.texts
    assert "looked: there" in survivors.texts
    # Not an asked command: what a profile reads is not what this row is about.
    assert commandrun.PROFILE_WARNING not in survivors.texts

    for _ in range(4):
        for key in (real_imgui.Key.tab, real_imgui.Key.space, real_imgui.Key.enter):
            survivors.io.add_key_event(key, True)
            survivors.frame()
            survivors.io.add_key_event(key, False)
            survivors.frame(2)
    assert survivors.checked == [] and survivors.copied == [], "a key acted on a command's row"
    assert survivors.released == [] and survivors.rows.state.pending is None

    survivors.click(inbox.CHECK_AGAIN)
    assert survivors.checked == [cid(1)]
    survivors.click(inbox.COPY_KILL)
    survivors.click(inbox.COPY_LOOK)
    assert survivors.copied == ["kill -- -4242", "ps -o pid,pgid,lstart,args -s 4242"]


@pytest.mark.parametrize("survivors", [False], indirect=True)
def test_in_details_dead_copy_the_lines_can_be_copied_and_nothing_looks_again(
    survivors: _Survivors,
) -> None:
    survivors.click(inbox.CHECK_AGAIN)
    survivors.click(inbox.COPY_KILL)
    assert survivors.checked == [] and survivors.copied == ["kill -- -4242"]
    survivors.frame(40)
    survivors.click(inbox.RELEASE)
    assert survivors.rows.state.pending is None and inbox.CONFIRM_RELEASE not in survivors.at
    assert survivors.released == []


def test_in_dear_imgui_releasing_a_session_takes_two_presses_and_the_second_waits(
    survivors: _Survivors,
) -> None:
    """
    As the run of a command: the first press arms and a confirmation takes
    its place, which answers nothing for ``ARM_DELAY_S``, so the two clicks of
    a double-click are not the two presses.
    """
    assert inbox.WAYS_OUT in survivors.texts
    assert inbox.RELEASE in survivors.at and inbox.CONFIRM_RELEASE not in survivors.at
    survivors.frame(40)  # past the delay after the row was opened
    survivors.click(inbox.RELEASE)
    assert survivors.released == [], "one press released the session"
    assert survivors.rows.state.pending == command_view.Releasing(cid(1))
    assert inbox.RELEASE_ARMED in survivors.texts and inbox.CONFIRM_RELEASE in survivors.at

    survivors.click(inbox.CONFIRM_RELEASE)
    assert survivors.released == [], "the second press answered inside the arm delay"
    survivors.frame(40)
    survivors.click(inbox.CONFIRM_RELEASE)
    assert survivors.released == [cid(1)] and survivors.rows.state.pending is None

    # Back disarms, and nothing is released.
    survivors.click(inbox.RELEASE)
    survivors.frame(40)
    survivors.click(command_view.BACK)
    assert survivors.rows.state.pending is None and survivors.released == [cid(1)]


def test_what_holds_the_session_and_the_ways_out_are_one_short_line() -> None:
    """The operator's rule: at most one short line; the explanation is behind its mark."""
    for left in (
        _LEFT,
        Survivor(pid=50, pgid=50, members=0, groups=(70,), escaped=(70,), shell=False),
    ):
        lines = inbox.survivor_lines(left)
        assert [line for line in lines if inbox.CHECK_AGAIN in line] == [inbox.WAYS_OUT]
        assert lines[-1] == inbox.WAYS_OUT
    assert "\n" not in inbox.WAYS_OUT and len(inbox.WAYS_OUT) <= 110
    assert "release" in inbox.WAYS_OUT
    assert not any(inbox.WAYS_OUT in p for p in inbox.RELEASE_HELP)
    assert any("sends no signal" in p for p in inbox.RELEASE_HELP)


def test_what_the_operators_profile_may_read_is_said_once_behind_the_question_mark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    One home: ``command_view.explanations``, behind the row's question mark;
    the row's own line says only that the profile runs first. This pane says
    it nowhere, so it is not said twice.
    """
    store = team()
    request(store, 1, now=5.0)
    explained: list[str] = []

    def drawn(snap: Snapshot) -> list[str]:
        said: list[str] = []
        explained.clear()
        fake = MagicMock()
        fake.text_colored.side_effect = lambda colour, text: said.append(str(text))
        for name in ("ImVec2", "InputTextFlags_", "ChildFlags_", "MouseButton_", "Key"):
            setattr(fake, name, getattr(real_imgui, name))
        fake.calc_text_size.side_effect = lambda text: real_imgui.ImVec2(7.0 * len(text), 13.0)
        fake.get_content_region_avail.return_value = real_imgui.ImVec2(700.0, 0.0)
        fake.get_time.return_value = 100.0
        fake.button.return_value = False
        fake.radio_button.return_value = False
        monkeypatch.setattr(inbox, "imgui", fake)
        monkeypatch.setattr(command_view, "imgui", fake)
        monkeypatch.setattr(widgets, "small_button", fake.small_button)
        fake.input_text_with_hint.side_effect = lambda label, hint, value: (False, value)
        monkeypatch.setattr(
            command_view, "help_mark", lambda key, title, paragraphs: explained.extend(paragraphs)
        )
        monkeypatch.setattr(command_view, "multiline_input", MagicMock())
        inbox.draw_command(snap, rows(wired=True, opened=cid(1)), snap.commands[cid(1)])
        return said

    said = drawn(store.snapshot())
    assert commandrun.PROFILE_WARNING not in said
    assert explained.count(commandrun.PROFILE_WARNING) == 1
    assert any("your profile first" in text for text in said)
    assert "direnv, mise, a dotenv plugin" in commandrun.PROFILE_WARNING
    assert "PROFILE_WARNING" not in inspect.getsource(inbox)


def test_what_an_end_left_is_said_in_detail_and_on_the_clipboard() -> None:
    store = team()
    request(store, 1, now=5.0)
    approve(store, 1, now=6.0)
    end = End(
        outcome=Exited(code=0),
        at=WALL + 9.0,
        duration_s=3.0,
        leftover=2,
        unconfirmed=f"its process group 99 was sent SIGKILL\nand {MARK} is not an agent's here",
    )
    store.apply(CommandEnded(cid(1), end), now=9.0)
    command = store.snapshot().commands[cid(1)]

    said = command_view.row_state(command, 0.0).text
    assert "2 leftover processes of its session" in said
    assert "NOT CONFIRMED DEAD: its process group 99 was sent SIGKILL and" in said
    assert "\n" not in said
    # One line of pptmstr's own, behind no author's mark, on the clipboard and
    # as the label the command is listed under in DETAIL.
    text = detail.command_text(command, "builder")
    assert said in text and not said.startswith(("| ", "> ", "= "))
    assert sum("NOT CONFIRMED DEAD" in line for line in text) == 1
    assert detail.ended_label(store.snapshot(), command, 0.0).endswith(said)

    plain = dataclasses.replace(command, end=dataclasses.replace(end, leftover=0, unconfirmed=None))
    assert "leftover" not in command_view.row_state(plain, 0.0).text


# -- the row that leaves from under the cursor, and telling two commands apart ----------


def _queue_drawn(
    monkeypatch: pytest.MonkeyPatch,
    snap: Snapshot,
    focus: FocusState,
    given: inbox.CommandRows | None,
) -> list[tuple[str, str]]:
    """
    What ``inbox.draw`` put down, in order: ``("row", key)`` for a collapsed
    row, ``("open", key)`` for an expanded one, ``("text", line)`` for a line
    of the pane's own, and ``("zero", "")`` / ``("splash", "")`` for the two
    empty states.
    """
    from pptmstr.ui import decision_view

    drawn: list[tuple[str, str]] = []
    fake = MagicMock()
    fake.ImVec2 = real_imgui.ImVec2
    fake.text_colored.side_effect = lambda colour, text: drawn.append(("text", str(text)))
    fake.get_item_rect_min.return_value = real_imgui.ImVec2(0.0, 0.0)
    fake.get_item_rect_max.return_value = real_imgui.ImVec2(0.0, 0.0)
    monkeypatch.setattr(inbox, "imgui", fake)
    monkeypatch.setattr(inbox, "_header", MagicMock())
    monkeypatch.setattr(inbox, "_batch_controls", MagicMock())
    monkeypatch.setattr(inbox, "_zero_state", lambda snap, now: drawn.append(("zero", "")))
    monkeypatch.setattr(inbox, "_splash", lambda now: drawn.append(("splash", "")))
    monkeypatch.setattr(
        inbox,
        "_row",
        lambda *a, obligation, expanded, **k: drawn.append(
            ("open" if expanded else "row", obligation.key)
        ),
    )
    inbox.draw(
        snap,
        focus,
        review.ReviewState(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        100.0,
        decisions=decision_view.DecisionState(),
        commands=given,
    )
    return drawn


def test_where_the_withdrawn_command_was_the_queue_says_what_left_and_opens_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The swap, at the pane. Before: A is open. After its asker withdraws it: no
    row is open, B is listed collapsed, and above B -- where A was -- is a line
    naming A by the tail of its id and saying it was withdrawn.
    """
    store = team()
    request(store, 1, now=5.0, text="pip install requests")
    request(store, 2, now=6.0, text="pip install reqeusts")
    focus = FocusState()
    focus.settle(store.snapshot())
    before = _queue_drawn(monkeypatch, store.snapshot(), focus, rows(wired=True))
    assert before == [("open", f"command:{cid(1)}"), ("row", f"command:{cid(2)}")]

    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 7.0), now=7.0)
    snap = store.snapshot()
    focus.settle(snap)
    after = _queue_drawn(monkeypatch, snap, focus, rows(wired=True))

    said = (
        f"the command you were reading - {cid(1)[-12:]} from builder - was withdrawn by its"
        " asker: nothing ran"
    )
    assert after == [
        ("text", said),
        ("text", inbox.NOTHING_UNDER_THE_CURSOR),
        ("row", f"command:{cid(2)}"),
    ]
    assert not any(kind == "open" for kind, _ in after)
    assert MARK not in said


def test_the_mark_is_drawn_where_the_row_was_and_in_the_empty_queue_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = team()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, "p1", at=1.0).approval), now=1.0)
    request(store, 1, now=5.0)
    request(store, 2, ROOT, now=6.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    focus.move(store.snapshot(), 1)

    store.apply(CommandRefused(cid(1), "no", WALL + 7.0), now=7.0)
    snap = store.snapshot()
    focus.settle(snap)
    middle = _queue_drawn(monkeypatch, snap, focus, rows(wired=True))
    assert [kind for kind, _ in middle] == ["row", "text", "text", "row"]
    assert middle[1][1].endswith("was refused by you: nothing ran")

    # The last row of the queue: the mark stands after what is left.
    focus.move(snap, 1)
    focus.settle(snap)
    store.apply(CommandWithdrawn(ROOT, cid(2), None, WALL + 8.0), now=8.0)
    snap = store.snapshot()
    focus.settle(snap)
    last = _queue_drawn(monkeypatch, snap, focus, rows(wired=True))
    assert [kind for kind, _ in last] == ["row", "text", "text"]

    # And with nothing left in it.
    alone = team()
    request(alone, 1, now=5.0)
    only = FocusState()
    only.settle(alone.snapshot())
    alone.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 7.0), now=7.0)
    only.settle(alone.snapshot())
    empty = _queue_drawn(monkeypatch, alone.snapshot(), only, rows(wired=True))
    assert [kind for kind, _ in empty] == ["text", "text", "zero"]
    assert "was withdrawn by its asker" in empty[0][1]


def test_what_left_is_said_for_every_kind_of_row_and_for_every_way_a_command_goes() -> None:
    store = team()
    request(store, 1, now=5.0)
    request(store, 2, now=5.5)
    request(store, 3, now=5.6)
    store.apply(ApprovalRequested(PEER, approval(PEER, "p1", at=6.0).approval), now=6.0)
    snap = store.snapshot()
    one, two, three = owed_commands(snap)
    (parked,) = [o for o in snap.needs_you if not isinstance(o, CommandOwed)]

    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 7.0), now=7.0)
    store.apply(CommandRefused(cid(2), f"{MARK} no", WALL + 7.0), now=7.0)
    approve(store, 3, now=8.0)
    finish(store, 3, now=9.0)
    after = store.snapshot()

    assert inbox.departure_line(after, one).endswith("was withdrawn by its asker: nothing ran")
    refused = inbox.departure_line(after, two)
    assert refused.endswith("was refused by you: nothing ran") and MARK not in refused
    ended = inbox.departure_line(after, three)
    assert f"{cid(3)[-12:]} from builder - has ended: exit code 0 after 3s" in ended
    gone = inbox.departure_line(dataclasses.replace(after, commands={}), one)
    assert gone.endswith("is no longer on record")
    assert inbox.departure_line(after, parked) == (
        "the approval you were on - Write from builder-2 - is no longer waiting: it was"
        " answered, or its agent ended"
    )
    # Every kind of obligation has words, by ``assert_never``: the source is
    # closed over the union.
    assert "assert_never(left)" in inspect.getsource(inbox.departure_line)


def test_each_command_is_counted_among_its_askers_and_the_count_reaches_the_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``4th command asked in this session, 3rd from builder``, and how many more
    of that agent's are waiting: counted from the snapshot, ended ones included
    in both counts and left out of the waiting. The count over the session is
    the one no two of its commands share: each asker has a first.
    """
    store = team()
    request(store, 1, now=5.0)
    request(store, 2, PEER, now=5.5)
    request(store, 3, now=6.0)
    request(store, 4, now=7.0)
    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 8.0), now=8.0)
    snap = store.snapshot()
    given = rows(wired=True)

    def among(n: int) -> command_view.Among:
        return inbox.among(snap, given, snap.commands[cid(n)])

    assert (among(3).ordinal, among(3).others_waiting) == (2, 1)
    assert (among(4).ordinal, among(4).others_waiting) == (3, 1)
    assert (among(2).ordinal, among(2).others_waiting) == (1, 0)
    assert not among(3).new
    assert [among(n).in_session for n in (1, 2, 3, 4)] == [1, 2, 3, 4]
    # Two askers' firsts: the same count from each, and not the same command.
    assert among(1).ordinal == among(2).ordinal == 1
    assert among(1).in_session != among(2).in_session
    # Another session's commands are not counted in this one's.
    elsewhere = dataclasses.replace(asked(9, ("s-2", None)), asked_at=WALL - 100.0)
    wider = dataclasses.replace(snap, commands={**snap.commands, elsewhere.id: elsewhere})
    assert inbox.among(wider, given, wider.commands[cid(1)]).in_session == 1

    handed: dict[str, Any] = {}
    monkeypatch.setattr(command_view, "draw_row", lambda state, command, **kw: handed.update(kw))
    monkeypatch.setattr(inbox, "imgui", MagicMock())
    inbox.draw_command(snap, given, snap.commands[cid(4)])
    assert handed["among"] == among(4) and handed["restored"] is False


def test_a_command_is_new_because_it_was_just_asked_and_not_because_the_queue_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Answering the row above a command, and withdrawing the command ahead of
    it, each put it somewhere else in the queue. Neither is the command
    arriving, and after either nothing would mark one that then did.
    """
    store = team()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, "p1", at=3.0).approval), now=3.0)
    request(store, 1, now=5.0)
    request(store, 2, now=6.0)
    given = rows(wired=True)
    focus = FocusState()

    def new(n: int, at: inbox.CommandRows) -> bool:
        snap = store.snapshot()
        focus.settle(snap)
        _queue_drawn(monkeypatch, snap, focus, at)
        return inbox.among(snap, at, snap.commands[cid(n)]).new

    # ``rows`` is 50 seconds on: both were asked 45 and 44 seconds ago.
    assert not new(1, given) and not new(2, given)
    just = dataclasses.replace(given, now=WALL + 6.0 + command_view.NEW_FOR_S - 0.5)
    assert new(2, just) and not new(1, just)

    # The row above is answered: both commands move up one.
    store.apply(ApprovalResolved(ROOT, "p1", True), now=60.0)
    assert not new(1, given) and not new(2, given)
    # The first is withdrawn: the second stands where it stood.
    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 70.0), now=70.0)
    later = dataclasses.replace(given, now=WALL + 71.0)
    assert not new(2, later)

    # One that does arrive is new, for as long as it is and no longer.
    store.apply(
        CommandRequested(
            CHILD, dataclasses.replace(asked(3), asked_at=WALL + 72.0), None, WALL + 72.0
        ),
        now=72.0,
    )
    assert new(3, dataclasses.replace(given, now=WALL + 73.0)) and not new(2, later)
    assert not new(3, dataclasses.replace(given, now=WALL + 72.0 + command_view.NEW_FOR_S))


def test_the_mark_of_a_departed_row_stays_where_the_row_was_while_the_queue_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Command 1 goes from between an approval and command 2. Then the approval
    is answered: the mark is still ahead of command 2, and not one row down
    where an index would put it.
    """
    store = team()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, "p1", at=1.0).approval), now=1.0)
    request(store, 1, now=5.0)
    request(store, 2, now=6.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    focus.move(store.snapshot(), 1)
    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 7.0), now=7.0)
    focus.settle(store.snapshot())
    given = rows(wired=True)

    def kinds() -> list[str]:
        snap = store.snapshot()
        focus.settle(snap)
        return [kind for kind, _ in _queue_drawn(monkeypatch, snap, focus, given)]

    assert kinds() == ["row", "text", "text", "row"]
    store.apply(ApprovalResolved(ROOT, "p1", True), now=8.0)
    assert kinds() == ["text", "text", "row"]
    request(store, 3, PEER, now=9.0)
    assert kinds() == ["text", "text", "row", "row"]
    store.apply(CommandWithdrawn(CHILD, cid(2), None, WALL + 10.0), now=10.0)
    assert kinds() == ["row", "text", "text"]


def test_a_click_on_a_row_above_the_mark_takes_the_mark_with_it_in_the_same_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The cursor is asked again at each row: the mark is the cursor's, and it has moved.
    """
    store = team()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, "p1", at=1.0).approval), now=1.0)
    request(store, 1, now=5.0)
    request(store, 2, now=6.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    focus.move(store.snapshot(), 1)
    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 7.0), now=7.0)
    snap = store.snapshot()
    focus.settle(snap)
    assert focus.departed is not None

    drawn: list[str] = []
    fake = MagicMock()
    fake.text_colored.side_effect = lambda colour, text: drawn.append(str(text))
    monkeypatch.setattr(inbox, "imgui", fake)
    for name in ("_header", "_batch_controls"):
        monkeypatch.setattr(inbox, name, MagicMock())

    def row(*a: Any, obligation: Any, **k: Any) -> None:
        if obligation.key == "approval:p1":
            focus.to_obligation(obligation)

    monkeypatch.setattr(inbox, "_row", row)
    inbox.draw(
        snap,
        focus,
        review.ReviewState(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        100.0,
        decisions=MagicMock(),
        commands=rows(wired=True),
    )

    assert inbox.NOTHING_UNDER_THE_CURSOR not in drawn


def test_in_dear_imgui_the_mark_of_a_departed_row_and_the_new_lines_draw_without_a_stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """
    The calls this adds, made for real in a context with no backend: the mark
    in a queue, in an empty queue and on the cold-start splash; the identity,
    NEW and others-waiting lines; a reason holding a lone surrogate. Nothing
    is rendered and nobody has looked at any of it.
    """
    from pptmstr.ui import compose, decision_view

    monkeypatch.setattr("pptmstr.settings.config_dir", lambda: tmp_path)
    store = team()
    request(store, 1, now=5.0, text="git push && ./deploy.sh")
    request(store, 2, now=6.0)
    request(store, 3, now=7.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 8.0), now=8.0)
    queued = store.snapshot()
    focus.settle(queued)
    assert focus.departed is not None
    for n in (2, 3):
        store.apply(CommandWithdrawn(CHILD, cid(n), None, WALL + 9.0), now=9.0)
    emptied = store.snapshot()
    # A record as only a file could hold it: the reducer refuses this reason.
    odd = dataclasses.replace(asked(4), reason="why \ud800 not", expects="a\x00b \udfff")
    surrogate = dataclasses.replace(queued, commands={**queued.commands, odd.id: odd})

    context = real_imgui.create_context()
    try:
        io = real_imgui.get_io()
        io.display_size = real_imgui.ImVec2(1280.0, 900.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= real_imgui.BackendFlags_.renderer_has_textures
        io.set_ini_filename("")
        given = rows(wired=True)
        for snap, cursor in (
            (queued, focus),
            (emptied, focus),
            (Store().snapshot(), focus),
            (queued, FocusState(target=OnObligation(f"command:{cid(2)}"))),
        ):
            for _ in range(2):
                real_imgui.new_frame()
                real_imgui.begin("needs you")
                inbox.draw(
                    snap,
                    cursor,
                    review.ReviewState(),
                    compose.ComposeState(),
                    MagicMock(),
                    MagicMock(),
                    100.0,
                    decisions=decision_view.DecisionState(),
                    commands=given,
                )
                real_imgui.end()
                real_imgui.end_frame()
        fresh = dataclasses.replace(given, now=WALL + 7.0)
        assert inbox.among(queued, fresh, queued.commands[cid(2)]).new
        for opened in (None, cid(2)):
            if opened is not None:
                fresh.state.open(opened, at=0.0)
            real_imgui.new_frame()
            real_imgui.begin("needs you")
            inbox.draw_command(queued, fresh, queued.commands[cid(2)])
            real_imgui.end()
            real_imgui.end_frame()
        given.state.open(odd.id, at=0.0)
        real_imgui.new_frame()
        real_imgui.begin("needs you")
        inbox.draw_command(surrogate, given, odd)
        real_imgui.end()
        real_imgui.end_frame()
    finally:
        real_imgui.destroy_context(context)


# -- a command's row is closed until the operator opens that row ---------------------------
#
# The property: a command's text is on screen only because the operator acted on
# THAT row -- a click on it, or the open key with the cursor on it -- and never
# because the cursor arrived there, a row left, or a row appeared. The queue and
# DETAIL are both drawn, in real frames with no backend, and every call that
# draws a command's block is counted. Nothing is rendered.


class Queue:
    """
    ``inbox.draw`` and ``detail.draw`` in real frames, in the order the
    application runs a frame: settle the cursor, read the frame's keys, draw.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, store: Store, tmp_path: Any) -> None:
        from pptmstr.ui import compose, decision_view

        monkeypatch.setattr("pptmstr.settings.config_dir", lambda: tmp_path)
        self.store = store
        self.focus = FocusState()
        self.review = review.ReviewState()
        self.compose = compose.ComposeState()
        self.decisions = decision_view.DecisionState()
        self.detail = detail.DetailState()
        self.ran: list[tuple[object, ...]] = []
        self.rows = rows(
            wired=True,
            actions=Actions(
                on_run=lambda *a: self.ran.append(a), on_refuse=MagicMock(), on_stop=MagicMock()
            ),
        )
        self.state = self.rows.state
        # What was drawn in the last frame: the blocks, the queue's rows by key
        # in order, and where each button of a command's row stood.
        self.blocks: list[str] = []
        self.headers: list[tuple[float, float]] = []
        self.at: dict[str, tuple[float, float]] = {}
        self.draws = True
        imgui = real_imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 2000.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        blocks, headers, at = self.blocks, self.headers, self.at
        block = command_view.command_block

        def centre() -> tuple[float, float]:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            return (low.x + high.x) / 2.0, (low.y + high.y) / 2.0

        class Rows:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def invisible_button(self, label: str, size: object) -> bool:
                pressed = imgui.invisible_button(label, size)  # type: ignore[arg-type]
                headers.append(centre())
                return bool(pressed)

        class Buttons:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def button(self, label: str) -> bool:
                pressed = imgui.button(label)
                # The queue is drawn first and DETAIL's dead copy after it:
                # the first of a label in a frame is the one that can act.
                at.setdefault(label, centre())
                return bool(pressed)

        def noted(text: str, **kwargs: Any) -> None:
            blocks.append(text)
            block(text, **kwargs)

        monkeypatch.setattr(inbox, "imgui", Rows())
        monkeypatch.setattr(command_view, "imgui", Buttons())
        monkeypatch.setattr(command_view, "command_block", noted)

    def close(self) -> None:
        real_imgui.destroy_context(self._context)

    @property
    def snap(self) -> Snapshot:
        return self.store.snapshot()

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            snap = self.snap
            self.focus.settle(snap)
            review.handle_keys(snap, self.focus, self.review, MagicMock())
            self.blocks.clear()
            self.headers.clear()
            self.at.clear()
            real_imgui.new_frame()
            if self.draws:
                real_imgui.set_next_window_pos(real_imgui.ImVec2(0.0, 0.0))
                real_imgui.set_next_window_size(real_imgui.ImVec2(1000.0, 1900.0))
                real_imgui.begin("needs you")
                inbox.draw(
                    snap,
                    self.focus,
                    self.review,
                    self.compose,
                    MagicMock(),
                    MagicMock(),
                    100.0,
                    decisions=self.decisions,
                    commands=self.rows,
                )
                real_imgui.end()
            real_imgui.set_next_window_pos(real_imgui.ImVec2(1000.0, 0.0))
            real_imgui.set_next_window_size(real_imgui.ImVec2(280.0, 1900.0))
            real_imgui.begin("detail")
            detail.draw(
                snap, self.focus, self.review, self.detail, 100.0, self.decisions, self.rows
            )
            real_imgui.end()
            real_imgui.end_frame()

    def on(self) -> str | None:
        current = self.focus.obligation(self.snap)
        return None if current is None else current.key

    def click_at(self, where: tuple[float, float]) -> None:
        self.io.add_mouse_pos_event(*where)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)

    def click_row(self, key: str) -> None:
        """A click on the header of the queue's row for ``key``, where it was last drawn."""
        keys = [o.key for o in self.snap.needs_you]
        self.click_at(self.headers[keys.index(key)])

    def click(self, label: str) -> None:
        self.click_at(self.at[label])

    def key(self, key: real_imgui.Key) -> None:
        self.io.add_key_event(key, True)
        self.frame(2)
        self.io.add_key_event(key, False)
        self.frame(2)

    def wait(self) -> None:
        """Long enough for a press held back by ``ARM_DELAY_S`` to answer."""
        self.frame(int(command_view.ARM_DELAY_S * 60) + 6)


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Iterator[Queue]:
    drawn = Queue(monkeypatch, team(), tmp_path)
    try:
        yield drawn
    finally:
        drawn.close()


def test_a_command_the_cursor_arrives_on_after_another_row_emptied_the_queue_is_not_shown(
    queue: Queue,
) -> None:
    """
    An approval, answered; then a command asked into the empty queue. The
    cursor yields to the queue and is on the command, and none of it is drawn.
    """
    queue.store.apply(ApprovalRequested(ROOT, approval(ROOT, "p1", at=1.0).approval), now=1.0)
    queue.frame(2)
    assert queue.on() == "approval:p1"
    queue.store.apply(ApprovalResolved(ROOT, "p1", True), now=2.0)
    queue.frame(2)

    request(queue.store, 1, now=5.0, text="make install")
    queue.frame(4)

    assert queue.on() == f"command:{cid(1)}"
    assert queue.blocks == [] and queue.at == {} and queue.state.opened is None


def test_no_way_the_cursor_comes_to_a_command_shows_it_and_each_way_of_opening_it_does(
    queue: Queue,
) -> None:
    """
    Every route but the operator's own act on the row: the first row of a
    fresh queue, a step with ``j`` and with ``k``, the row above being
    answered, a row arriving, and the command that was open being withdrawn.
    """
    store = queue.store
    request(store, 1, now=5.0, text="pip install requests")
    request(store, 2, now=6.0, text="pip install reqeusts")
    store.apply(ApprovalRequested(ROOT, approval(ROOT, "p1", at=7.0).approval), now=7.0)
    one, two = f"command:{cid(1)}", f"command:{cid(2)}"

    queue.frame(3)
    assert queue.on() == one and queue.blocks == []

    queue.key(real_imgui.Key.j)
    assert queue.on() == two and queue.blocks == []
    queue.key(real_imgui.Key.j)
    assert queue.on() == "approval:p1" and queue.blocks == []
    queue.key(real_imgui.Key.k)
    queue.key(real_imgui.Key.k)
    assert queue.on() == one and queue.blocks == []

    # A row arrives while the cursor rests on a command.
    request(store, 3, PEER, now=8.0, text="make install")
    queue.frame(3)
    assert queue.on() == one and queue.blocks == []

    # The open key, with the cursor on the row: the queue and DETAIL both draw it.
    queue.key(inbox.OPEN_KEY)
    assert queue.state.is_open(cid(1))
    assert queue.blocks == ["pip install requests", "pip install requests"]

    # It is withdrawn. Nothing opens where it was, and the cursor is on no row.
    store.apply(CommandWithdrawn(CHILD, cid(1), None, WALL + 9.0), now=9.0)
    queue.frame(3)
    assert queue.on() is None and queue.focus.departed is not None
    assert queue.blocks == [] and queue.state.opened is None
    # The open key opens nothing from there: there is no row under the cursor.
    queue.key(inbox.OPEN_KEY)
    assert queue.blocks == [] and queue.state.opened is None

    # A step down is the command that took its place: under the cursor, closed.
    queue.key(real_imgui.Key.j)
    assert queue.on() == two and queue.blocks == []

    # The row under the cursor is answered and a command is what follows it.
    queue.key(real_imgui.Key.j)
    assert queue.on() == "approval:p1"
    store.apply(ApprovalResolved(ROOT, "p1", True), now=10.0)
    queue.frame(3)
    assert queue.on() == f"command:{cid(3)}" and queue.blocks == []

    # A click on a row opens that row, and no other.
    queue.click_row(two)
    assert queue.on() == two and queue.state.is_open(cid(2))
    assert set(queue.blocks) == {"pip install reqeusts"}
    queue.click_row(f"command:{cid(3)}")
    assert queue.state.is_open(cid(3)) and not queue.state.is_open(cid(2))
    assert set(queue.blocks) == {"make install"}


def test_the_row_opened_with_a_click_is_run_with_two_more_and_not_before_each_is_due(
    queue: Queue,
) -> None:
    """
    What the operator does to run one command, end to end: a click on the row,
    a click on run, a click on run it. A click made at once after either of
    the first two does nothing: the row has just redrawn under the mouse.
    """
    request(queue.store, 1, now=5.0, text="make install")
    queue.frame(3)
    assert command_view.RUN not in queue.at

    queue.click_row(f"command:{cid(1)}")
    assert command_view.RUN in queue.at
    queue.click(command_view.RUN)
    assert queue.state.pending is None, "run answered the click that opened the row"

    queue.wait()
    queue.click(command_view.RUN)
    assert queue.state.pending == FrozenRun(cid(1), "make install", 600)
    queue.click(command_view.CONFIRM_RUN)
    assert queue.ran == [], "run it answered the click that armed it"

    queue.wait()
    queue.click(command_view.CONFIRM_RUN)
    assert queue.ran == [(cid(1), "make install", 600)]


def test_an_armed_run_is_gone_when_the_queue_comes_back_from_a_frame_it_was_not_drawn_in(
    queue: Queue,
) -> None:
    """
    Armed, and then the queue is not drawn -- another layout, a tab in front
    of it -- while DETAIL still is. The press does not wait there for the
    queue to come back.
    """
    request(queue.store, 1, now=5.0, text="make install")
    queue.frame(3)
    queue.click_row(f"command:{cid(1)}")
    queue.wait()
    queue.click(command_view.RUN)
    queue.wait()
    assert isinstance(queue.state.pending, FrozenRun) and command_view.CONFIRM_RUN in queue.at

    queue.draws = False
    queue.frame()
    # One frame undrawn, and DETAIL's dead copy has already let go of it by the next.
    queue.frame()
    assert queue.state.pending is None
    queue.draws = True
    queue.frame(2)

    assert queue.state.pending is None and command_view.CONFIRM_RUN not in queue.at
    assert queue.ran == []


def test_run_it_sends_nothing_on_a_frame_the_row_could_not_draw_the_string_it_runs(
    queue: Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The second press is in the row, under the frozen string, and the string is
    drawn first: a row that raises drawing it never reaches the press. Clicked
    where ``run it`` stood while that is so, nothing runs; and once the row
    draws again the same click does run, so the press was not dead for another
    reason. ``review.withheld`` is not on this route (the row is its own hold).
    """
    request(queue.store, 1, now=5.0, text="make install")
    queue.frame(3)
    queue.click_row(f"command:{cid(1)}")
    queue.wait()
    queue.click(command_view.RUN)
    queue.wait()
    assert isinstance(queue.state.pending, FrozenRun) and command_view.CONFIRM_RUN in queue.at
    where = queue.at[command_view.CONFIRM_RUN]

    io = queue.io
    io.config_error_recovery = True
    io.config_error_recovery_enable_assert = False
    io.config_error_recovery_enable_tooltip = False
    shown = command_view.command_block
    raised: list[str] = []

    def unshowable(text: str, **kwargs: Any) -> None:
        if text == "make install":
            raised.append(text)
            raise RuntimeError("the frozen string could not be drawn")
        shown(text, **kwargs)

    def frame() -> None:
        snap = queue.snap
        queue.focus.settle(snap)
        review.handle_keys(snap, queue.focus, queue.review, MagicMock())
        real_imgui.new_frame()
        real_imgui.set_next_window_pos(real_imgui.ImVec2(0.0, 0.0))
        real_imgui.set_next_window_size(real_imgui.ImVec2(1000.0, 1900.0))
        real_imgui.begin("needs you")
        try:
            inbox.draw(
                snap,
                queue.focus,
                queue.review,
                queue.compose,
                MagicMock(),
                MagicMock(),
                100.0,
                decisions=queue.decisions,
                commands=queue.rows,
            )
        except RuntimeError:
            pass
        real_imgui.end()
        real_imgui.end_frame()

    with monkeypatch.context() as patched:
        patched.setattr(command_view, "command_block", unshowable)
        io.add_mouse_pos_event(*where)
        frame()
        frame()
        io.add_mouse_button_event(0, True)
        frame()
        io.add_mouse_button_event(0, False)
        frame()
        frame()
    assert raised, "the row did try to draw the string"
    assert queue.ran == []

    queue.frame(2)
    queue.wait()
    queue.click(command_view.CONFIRM_RUN)
    assert queue.ran == [(cid(1), "make install", 600)]


def test_the_open_key_is_a_shortcut_only_while_the_keys_are_the_operators(
    queue: Queue,
) -> None:
    """
    ``o`` typed into a sentence whose box has gone is not the operator
    opening a command: ``review.Hold`` holds it as it holds ``a``.
    """
    request(queue.store, 1, now=5.0, text="make install")
    queue.frame(3)

    queue.review.hold.begin()
    queue.key(inbox.OPEN_KEY)
    assert queue.state.opened is None and queue.blocks == []

    queue.key(real_imgui.Key.escape)
    queue.key(inbox.OPEN_KEY)
    assert queue.state.is_open(cid(1))


def _open_key_with(**io: bool) -> tuple[bool, MagicMock]:
    fake = MagicMock()
    fake.Key = real_imgui.Key
    fake.PopupFlags_ = real_imgui.PopupFlags_
    for name in ("want_capture_keyboard", "want_text_input", "key_ctrl", "key_alt", "key_super"):
        setattr(fake.get_io.return_value, name, io.get(name, False))
    fake.is_popup_open.return_value = io.get("popup", False)
    fake.is_key_pressed.side_effect = lambda key, repeat: (
        key == inbox.OPEN_KEY and io.get("pressed", True) and not repeat
    )
    state = review.ReviewState()
    state.hold.free = io.get("free", True)
    return fake, state


@pytest.mark.parametrize(
    ("io", "opens"),
    [
        ({}, True),
        ({"pressed": False}, False),
        ({"free": False}, False),
        ({"want_capture_keyboard": True}, False),
        ({"want_text_input": True}, False),
        ({"key_ctrl": True}, False),
        ({"key_alt": True}, False),
        ({"key_super": True}, False),
        ({"popup": True}, False),
    ],
    ids=lambda value: ",".join(value) if isinstance(value, dict) else None,
)
def test_the_open_key_is_read_only_when_nothing_else_has_the_keyboard(
    monkeypatch: pytest.MonkeyPatch, io: dict[str, bool], opens: bool
) -> None:
    fake, state = _open_key_with(**io)
    monkeypatch.setattr(inbox, "imgui", fake)

    assert inbox._open_key(state) is opens
    if io.get("popup"):
        # Any popup at any level: a card's menu reads the keyboard as it draws.
        (_, flags), _ = fake.is_popup_open.call_args
        assert flags & int(real_imgui.PopupFlags_.any_popup_id)
        assert flags & int(real_imgui.PopupFlags_.any_popup_level)


def test_the_open_key_is_a_letter_no_other_shortcut_uses_and_the_header_names_it() -> None:
    assert inbox.OPEN_KEY == real_imgui.Key.o
    assert "o" not in {key for key, _ in review.SHORTCUTS}
    assert "o or a click on the row opens it" in inbox.COMMAND_KEYS
    assert "press o" in command_view.CLOSED


def test_the_open_key_opens_the_row_under_the_cursor_and_no_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring: with the key down, ``inbox.draw`` opens what the cursor is on, or nothing.
    """
    store = team()
    store.apply(ApprovalRequested(ROOT, approval(ROOT, "p1", at=1.0).approval), now=1.0)
    request(store, 1, now=5.0)
    request(store, 2, now=6.0)
    snap = store.snapshot()
    fake, held = _open_key_with()
    fake.get_frame_count.return_value = 3
    fake.get_time.return_value = 41.5
    monkeypatch.setattr(inbox, "imgui", fake)
    for name in ("_header", "_row", "_batch_controls"):
        monkeypatch.setattr(inbox, name, MagicMock())

    def drawn(key: str) -> CommandViewState:
        given = rows(wired=True)
        inbox.draw(
            snap,
            FocusState(target=OnObligation(key)),
            held,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            100.0,
            decisions=MagicMock(),
            commands=given,
        )
        return given.state

    assert drawn("approval:p1").opened is None
    second = drawn(f"command:{cid(2)}")
    assert second.opened == cid(2) and second.opened_at == 41.5


def test_the_survivor_block_is_part_of_the_open_row_and_the_closed_one_still_says_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = _left_running()
    command = snap.commands[cid(1)]
    said: list[str] = []
    fake = MagicMock()
    fake.text_colored.side_effect = lambda colour, text: said.append(str(text))
    fake.text_unformatted.side_effect = said.append
    fake.small_button.return_value = False
    monkeypatch.setattr(inbox, "imgui", fake)
    monkeypatch.setattr(command_view, "imgui", fake)
    monkeypatch.setattr(widgets, "small_button", fake.small_button)
    monkeypatch.setattr(command_view, "_ended", MagicMock())

    inbox.draw_command(snap, rows(wired=True), command)
    assert any(text.startswith("STILL RUNNING, UNSUPERVISED - ") for text in said)
    assert commandrun.kill_line(_LEFT) not in said and command_view.CLOSED in said

    said.clear()
    inbox.draw_command(snap, rows(wired=True, opened=cid(1)), command)
    assert commandrun.kill_line(_LEFT) in said and commandrun.look_line(_LEFT) in said


# -- what the session's own directory changes on the row -----------------------------------


def test_a_search_path_entry_inside_the_sessions_directory_is_listed_on_its_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """
    The runner drops it for this session and for no other, so the list made
    once at startup, with no directory, does not have it.
    """
    tree = tmp_path / "tree"
    inside = tree / "node_modules" / ".bin"
    inside.mkdir(parents=True)
    outside = tmp_path / "bin"
    outside.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([str(outside), str(inside), "/usr/bin"]))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    inbox._taken_out_for.cache_clear()
    store = Store()
    store.apply(
        AgentSpawned(node_id=ROOT, parent=None, task="t", model="m", started_at=0.0, cwd=str(tree)),
        now=1.0,
    )
    request(store, 1, ROOT, now=5.0)
    snap = store.snapshot()
    given = rows(wired=True)

    found = inbox.where(snap, given, snap.commands[cid(1)])

    assert found.stripped_env[0] == "VIRTUAL_ENV (/v) is unset"
    (added,) = found.stripped_env[1:]
    assert str(inside) in added and "inside the directory the command runs in" in added
    assert str(outside) not in added
    assert added in commandrun.environment_changes(cwd=str(tree))
    assert added not in commandrun.environment_changes()
    # What the runner is given for that directory is without it.
    assert str(inside) not in commandrun.environment(cwd=str(tree))["PATH"]
    # And what is behind the row's question mark carries it.
    explained = command_view.explanations(snap.commands[cid(1)].asked, found)
    assert any(str(inside) in paragraph for paragraph in explained)

    # Elsewhere, nothing is added to what the caller found.
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert inbox._taken_out_for(str(other), 0) == ()


def test_the_look_at_a_sessions_directory_is_not_made_every_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    It follows links on the disk. One look stands for ``_DIRECTORY_LOOK_S``.
    """
    looks: list[str | None] = []
    monkeypatch.setattr(
        inbox, "environment_changes", lambda cwd=None: (looks.append(cwd), ("x",))[1]
    )
    inbox._taken_out_for.cache_clear()
    store = team()
    request(store, 1, now=5.0)
    snap = store.snapshot()
    given = rows(wired=True)

    for step in range(120):
        at = dataclasses.replace(given, now=WALL + 50.0 + step / 60.0)
        inbox.where(snap, at, snap.commands[cid(1)])

    # Two seconds of frames: one look, or two where they straddle a boundary.
    assert 1 <= looks.count("/work/tree") <= 2
    later = dataclasses.replace(given, now=WALL + 50.0 + 10 * inbox._DIRECTORY_LOOK_S)
    inbox.where(snap, later, snap.commands[cid(1)])
    assert looks.count("/work/tree") >= 2
    inbox._taken_out_for.cache_clear()


def test_the_row_notes_the_sessions_directory_written_out_in_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring: the directory on the session's record reaches the scan through the row.
    """
    store = team()
    request(store, 1, now=5.0, text="/work/tree/bin/run --now")
    snap = store.snapshot()
    command = snap.commands[cid(1)]
    found = inbox.where(snap, rows(), command)
    assert found.roots[0] == "/work/tree"
    # From the home too, where the directory is under it.
    under = dataclasses.replace(found, cwd="/home/op/tree", home="/home/op")
    assert under.roots == commandtext.spellings("/home/op/tree", "/home/op")
    assert "~/tree" in under.roots
    sentence = commandtext.SENTENCES[commandtext.FlagKind.RUNS_SESSION_PATH]
    line = "May run files this session can write: session path."
    assert line in [said.text for said in command_view.text_warnings(command.asked, found.roots)]
    assert line not in [said.text for said in command_view.text_warnings(command.asked)]
    assert sentence in command_view.explanations(command.asked, found)

    said: list[str] = []
    fake = MagicMock()
    fake.text_colored.side_effect = lambda colour, text: said.append(str(text))
    for name in ("ImVec2", "InputTextFlags_", "ChildFlags_", "MouseButton_", "Key"):
        setattr(fake, name, getattr(real_imgui, name))
    fake.calc_text_size.side_effect = lambda text: real_imgui.ImVec2(7.0 * len(text), 13.0)
    fake.get_content_region_avail.return_value = real_imgui.ImVec2(700.0, 0.0)
    fake.get_time.return_value = 100.0
    fake.button.return_value = False
    fake.radio_button.return_value = False
    monkeypatch.setattr(inbox, "imgui", fake)
    monkeypatch.setattr(command_view, "imgui", fake)
    monkeypatch.setattr(widgets, "small_button", fake.small_button)
    fake.input_text_with_hint.side_effect = lambda label, hint, value: (False, value)
    explained: list[str] = []
    monkeypatch.setattr(
        command_view, "help_mark", lambda key, title, paragraphs: explained.extend(paragraphs)
    )
    inbox.draw_command(snap, rows(wired=True, opened=cid(1)), command)

    assert line in said and sentence in explained and sentence not in said


# -- a command left running that is known by its tag -------------------------------


def test_the_row_of_a_command_whose_shell_is_gone_names_only_what_carries_its_tag() -> None:
    """
    The shell's pid may be another program's by now. A row that said "the
    session its shell leads" and killed that session's groups would be about
    whoever has the number.
    """
    left = Survivor(
        pid=4242,
        pgid=4242,
        members=2,
        groups=(58, 70),
        pids=(51, 60),
        escaped=(60, 70, 71),
        marked=(51, 58),
        shell=False,
        unread=1,
    )
    lines = inbox.survivor_lines(left)
    assert lines[0] == "STILL RUNNING, UNSUPERVISED." and lines[2] == inbox.HELD_ELSEWHERE
    assert "Its shell (pid 4242) is gone, and 5 processes started with its PPTMSTR_COMMAND" in (
        lines[1]
    )
    assert "after that shell and in this boot, are alive" in lines[1]
    assert "2 in the session it led and 3 outside it (pid 60, 70, 71)" in lines[1]
    assert "The session its shell leads" not in " ".join(lines)
    # What could not be read is said, as not known either way, and is in neither line.
    assert lines[3].startswith("1 more process in that session number could not be read")
    assert "is NOT named below" in lines[3] and "cannot tell whether it is" in lines[3]
    assert "ps -o pid,pgid,sid,lstart,args -p 51,58,60,70,71" in lines
    assert "kill -- -58 -70 51 60" in lines
    assert not any("-4242" in line or "-s 4242" in line for line in lines)
    assert "a whole process group only where every process in it does" in (
        lines[lines.index(commandrun.kill_line(left)) - 1]
    )
    assert "env -i" in lines[-2] and lines[-1] == inbox.WAYS_OUT

    one = inbox.survivor_lines(
        Survivor(pid=7, pgid=7, members=1, pids=(9,), marked=(9,), shell=False)
    )
    assert "and 1 process started with its PPTMSTR_COMMAND" in one[1]
    assert "is alive: 1 in the session it led." in one[1] and "outside" not in one[1]
    assert "This window does not hold it" in one[1]
    assert not any("could not be read" in line for line in one)
    assert "kill -- 9" in one and "ps -o pid,pgid,sid,lstart,args -p 9" in one
    two = inbox.survivor_lines(
        Survivor(pid=7, pgid=7, members=1, pids=(9,), marked=(9,), shell=False, unread=2)
    )
    assert two[3].startswith("2 more processes in that session number could not be read")
    assert "are NOT named below" in two[3] and "whether they are this command's" in two[3]


def test_the_row_of_a_command_with_its_shell_says_what_left_its_session() -> None:
    plain = inbox.survivor_lines(_LEFT)
    left = dataclasses.replace(_LEFT, groups=(4242, 70), pids=(60,), escaped=(60, 70, 71))
    lines = inbox.survivor_lines(left)
    assert lines[:3] == plain[:3] and len(lines) == len(plain) + 1
    assert lines[3].startswith(
        "3 more processes of it are alive OUTSIDE that session (pid 60, 70, 71): started"
        " with its PPTMSTR_COMMAND in their environment"
    )
    assert "No pptmstr ends a process that has left the session" in lines[3]
    assert "ps -o pid,pgid,lstart,args -s 4242 -p 60,70,71" in lines
    assert "kill -- -70 -4242 60" in lines or "kill -- -4242 -70 60" in lines
    many = dataclasses.replace(left, escaped=tuple(range(100, 120)))
    said = inbox.survivor_lines(many)[3]
    assert "(pid 100, 101, 102, 103, 104, 105, 106, 107 and 12 more)" in said
    one = inbox.survivor_lines(dataclasses.replace(left, escaped=(60,)))[3]
    assert one.startswith("1 more process of it is alive OUTSIDE that session (pid 60)")
    assert "in its environment" in one
