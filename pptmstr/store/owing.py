"""
What the operator is owed: the ``needs_you`` projection, the obligation rows
for decisions and commands with their wording, and the allowance and wake a
raise or a request is answered with.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from typing import Literal

from .. import drawable
from ..addresses import role_name
from ..command import Command, CommandState
from ..effects import Effect, SessionWoken, WakeReason
from ..model import (
    INTERRUPTED_TOPIC,
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    AskerFate,
    CommandOwed,
    Decision,
    DecisionOwed,
    NodeId,
    Obligation,
    QuestionPending,
    SessionFailed,
    Snapshot,
    Standing,
    TaskId,
)
from .nodes import _FINAL_STATES


def _needs_you(
    nodes: dict[NodeId, AgentRecord], order: tuple[NodeId, ...]
) -> tuple[Obligation, ...]:
    """
    Everything a node's own state says is waiting on the operator, oldest first:
    the approvals, the questions and the failures. The other two kinds, a decision
    and a command, are records and not states: ``_owing_decisions`` and
    ``_owing_commands`` add those.

    One walk producing all three kinds. Ordered by how long each has been waiting
    rather than by tree position, because the operator works a backlog and the thing
    that has been blocked longest is the thing costing the most. Ties keep tree
    order, since the walk is over ``order`` and the sort is stable.

    **These three** are mutually exclusive per node by construction and not by luck,
    and it takes two constructions rather than one. While a node is live, the
    ``pending`` guard in StateChanged and SubagentProgress holds anything carrying an
    approval in AWAITING_APPROVAL, which is neither of the states the other two kinds
    read. Once an end is declared, ``_FINAL_STATES`` stops those same arms from moving
    the node at all, so a finished node still carrying an approval sits in DONE or
    CANCELLED -- again neither. FAILED is the one terminal state that can be entered
    with an approval outstanding, and ``AgentFinished`` clears ``pending`` as it sets
    it, so that pair cannot coexist either.

    **That is not a property of ``needs_you``.** A node with an open decision or
    an open command can be in any state, can have any one of these three beside
    it, and can have several of either open. So nothing may read "this node is in the list" as
    "this node has stopped", or take one obligation's kind for the kind of
    everything its node or its session owes.
    """
    out: list[Obligation] = []
    for nid in order:
        rec = nodes.get(nid)
        if rec is None:
            continue

        for p in rec.pending:
            out.append(
                ApprovalNeeded(node=nid, since=p.requested_at, summary=p.summary, approval=p)
            )

        if rec.state is AgentState.AWAITING_INPUT:
            # Deliberately not the agent's last sentence. Naming the two things the
            # operator can do about it is useful and honest; summarising what was
            # asked would mean guessing whether the turn ended in a question at all.
            #
            # Whether the turn was interrupted is a different kind of fact and does
            # belong here: the driver reads it off the result's terminal_reason, so
            # it is the CLI reporting what it did rather than a guess about what was
            # said. An interrupt that does not read as one in the list the operator
            # actually works from tells them their interrupt did nothing.
            interrupted = rec.topic == INTERRUPTED_TOPIC
            out.append(
                QuestionPending(
                    node=nid,
                    since=rec.state_since,
                    summary=(
                        "interrupted - reply or close"
                        if interrupted
                        else "ended its turn - reply or close"
                    ),
                )
            )

        elif rec.state is AgentState.FAILED and not rec.acknowledged:
            # The record holds how the run failed as it was said, for its session
            # file (``_VERBATIM``); the queue row draws it.
            error = None if rec.error is None else drawable.text(rec.error)
            first_line = (error or "").strip().splitlines()
            out.append(
                SessionFailed(
                    node=nid,
                    # A failure's wait starts when it died. ended_at is set by the
                    # same intent that set FAILED, so state_since only stands in for
                    # a record that reached FAILED some other way.
                    since=rec.ended_at if rec.ended_at is not None else rec.state_since,
                    summary=first_line[0][:120] if first_line else "session failed",
                    error=error,
                )
            )

    return tuple(sorted(out, key=lambda o: o.since))


def _allowance(
    snap: Snapshot,
    open_items: Sequence[Decision | Command],
    asker: NodeId,
    per_asker_cap: int,
    worker_cap: int,
) -> Literal["asker", "workers"] | None:
    """
    Which of the two caps ``asker`` has met, or None if neither: its own
    (``"asker"``), or the one its session's sub-agents share (``"workers"``).
    The root meets only its own. The reasons are ``_raise_refusal``'s.

    ``open_items`` is what the caller counts as open in the asker's session,
    already filtered: a decision and a command are open for different reasons,
    and this counts and does not judge.
    """
    if sum(1 for item in open_items if item.asker == asker) >= per_asker_cap:
        return "asker"
    if asker[1] is None:
        return None
    among_workers = sum(
        1
        for item in open_items
        if item.asker[1] is not None
        and (item.asker == asker or not _asker_fate(snap, item.asker).has_ended)
    )
    return "workers" if among_workers >= worker_cap else None


def _woken(asker: NodeId, reason: WakeReason, subject_id: str) -> tuple[Effect, ...]:
    """
    The order to wake ``asker``'s session about ``subject_id``. A sub-agent's
    session is woken through its lead, the only one with a turn to wake into.
    """
    return (SessionWoken(session_id=asker[0], reason=reason, subject_id=subject_id),)


def _owing_commands(world: Snapshot) -> Snapshot:
    """
    ``world`` with every command that is asked or running in ``needs_you``, at
    its place by age (``Snapshot.commands_awaiting``).

    A step over a built snapshot for the reasons ``_owing_decisions`` is one:
    the row names its asker's role, and "in the queue exactly while it is open"
    then has no arm that could forget to remove a row. The common case -- no
    command open -- returns ``world`` itself.

    ``since`` is ``Snapshot.command_since`` and never ``Command.asked_at``,
    which is a wall-clock time among monotonic ones.
    """
    awaiting = world.commands_awaiting()
    if not awaiting:
        return world
    owed = [
        CommandOwed(
            node=c.asker,
            since=world.command_since[c.id],
            summary=_command_summary(world, c),
            command=c,
            asker=_asker_fate(world, c.asker),
        )
        for c in awaiting
    ]
    # Stable, so among equal waits everything else a node owes stays ahead of
    # its commands and commands keep the order they were asked in.
    return dataclasses.replace(
        world, needs_you=tuple(sorted((*world.needs_you, *owed), key=lambda o: o.since))
    )


_COMMAND_STATE_TEXT = {CommandState.ASKED: "asked", CommandState.RUNNING: "running"}


_COMMAND_FATE_TEXT: dict[AskerFate, str | None] = {
    AskerFate.LIVE: None,
    AskerFate.IDLE: "its asker has ended its turn, and the result wakes its session",
    AskerFate.STOPPED: "its asker has stopped",
    AskerFate.FAILED: "its asker failed",
    AskerFate.UNANNOUNCED: "its asker has not announced itself yet",
    AskerFate.GONE: "its asker is gone",
}


def _command_summary(world: Snapshot, command: Command) -> str:
    """
    The queue row for a command that is asked or running, built from structure
    and from nothing its asker wrote.

    For the reason ``_decision_summary`` gives, and with more at stake: this
    row is drawn in the queue's own voice, and the row it stands for runs a
    string outside the sandbox. A first line of the command, or of the reason
    for it, would be an agent's sentence saying what the operator is about to
    approve. So the row says that a command is waiting, whose, in which state
    and how big; the string is on the record, for the one surface that draws it
    whole and escaped.

    The size is of the string that would run or is running: what was asked,
    or the run's own text once there is one.

    The id is last, for the reason a decision's is.
    """
    text = command.asked if command.run is None else command.run.text
    lines = text.count("\n") + 1
    parts = [f"{lines} line{'' if lines == 1 else 's'}, {len(text):,} characters"]
    if command.run is not None and command.stop_asked_at is not None:
        parts.append("cancel asked")
    fate = _COMMAND_FATE_TEXT[_asker_fate(world, command.asker)]
    if fate is not None:
        parts.append(fate)
    if _PLAIN_ID.fullmatch(command.id):
        parts.append(command.id)
    who = " ".join(role_name(world, command.asker, command.session_id).split())
    left = command.unsupervised
    if left is not None:
        # Before the size: it is the one row of this kind that is an alarm.
        parts.insert(
            0,
            (
                f"its shell's session {left.pid} is alive and nothing bounds it"
                if left.shell
                else f"its shell is gone, {left.alive} of its processes"
                f" {'is' if left.alive == 1 else 'are'} alive and nothing bounds"
                f" {'it' if left.alive == 1 else 'them'}"
            ),
        )
        return f"command STILL RUNNING, unsupervised, from {who}: " + "; ".join(parts)
    state = _COMMAND_STATE_TEXT.get(command.state, command.state.value)
    return f"command {state} from {who}: " + "; ".join(parts)


def _owing_decisions(world: Snapshot) -> Snapshot:
    """
    ``world`` with every open decision in ``needs_you``, at its place by age.

    A second step over a built snapshot and not a branch of ``_needs_you``,
    because a decision's row names its asker's role and the tasks it blocks, and
    both are projections of a whole snapshot (``addresses.role_name``,
    ``Task.belongs_to``). The common case -- no decision open -- returns
    ``world`` itself.

    Derived from the records on every step, so "in the queue exactly while it is
    open" has no arm that could forget to remove a row.
    """
    owed = [
        DecisionOwed(
            node=d.asker,
            since=d.raised_at,
            summary=_decision_summary(world, d),
            decision=d,
            asker=_asker_fate(world, d.asker),
        )
        for d in world.decisions.values()
        if d.is_open
    ]
    if not owed:
        return world
    # Stable, so among equal waits a node's own obligations stay ahead of its
    # decisions and decisions keep the order they were raised in.
    return dataclasses.replace(
        world, needs_you=tuple(sorted((*world.needs_you, *owed), key=lambda o: o.since))
    )


def _asker_fate(world: Snapshot, asker: NodeId) -> AskerFate:
    """
    What has become of ``asker``, as ``AskerFate`` words it.

    **No record is two different things.** A sub-agent can call the bus before
    its start is reported, so one with no record under a root that has one may
    be working, and telling the operator to dismiss its decision would be
    telling them to close a live agent's question. A root always has a record
    from the moment its session is launched, and removing a root removes its
    whole subtree, so an asker with no record AND no root's record is one whose
    session has left the store.

    **An agent of an earlier run of its session is gone, with or without a
    record** (``Snapshot.of_earlier_run``, asked first): another process's,
    which has none, and this process's from before the session was started
    again, which has a terminal one. The second would otherwise read as
    stopped -- "can still be woken" -- and nothing in the run that is going now
    wakes it. It is the reading every other surface gives the same agent.

    A sub-agent removed on its own would read as unannounced. Nothing emits
    ``AgentRemoved`` for one; whatever starts to has this to settle.
    """
    if world.of_earlier_run(asker):
        return AskerFate.GONE
    record = world.get(asker)
    if record is None:
        announced_later = asker[1] is not None and world.get((asker[0], None)) is not None
        return AskerFate.UNANNOUNCED if announced_later else AskerFate.GONE
    if record.state is AgentState.FAILED:
        return AskerFate.FAILED
    if record.state in _FINAL_STATES:
        return AskerFate.STOPPED
    if record.state is AgentState.AWAITING_INPUT:
        return AskerFate.IDLE
    return AskerFate.LIVE


# What an id has to look like to be drawn in the operator's queue: no spaces and
# nothing that could finish or start a phrase of the queue's own.
_PLAIN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


_SHOWN_TASKS = 3


_STANDING_TEXT = {
    Standing.OPERATORS: "yours to take",
    Standing.TAKEN: "already taken, wants ratifying",
}


_REVERSIBLE_TEXT = {True: "yes", False: "no", None: "not stated"}


_FATE_TEXT: dict[AskerFate, str | None] = {
    AskerFate.LIVE: None,
    AskerFate.IDLE: "its asker has ended its turn, and answering wakes its session",
    AskerFate.STOPPED: "its asker has stopped",
    AskerFate.FAILED: "its asker failed",
    AskerFate.UNANNOUNCED: "its asker has not announced itself yet",
    AskerFate.GONE: "its asker is gone",
}


def _decision_summary(world: Snapshot, decision: Decision) -> str:
    """
    The queue row for an open decision, built from structure and from nothing the
    asker wrote as prose.

    The queue draws a summary in its own colour and font beside
    ``gate.park.summarize``'s rows, the detail pane puts it on the clipboard and
    the digest draws it as one of its own items. A question shown there -- or its
    first line -- is an agent's sentence in the operator's own voice, and
    "Bash  git push --force  (approved)" is a sentence an agent can write. So the
    row says that a decision is waiting and what kind; the question is on the
    record, for a surface that marks it as the asker's.

    A role name is on one line here whatever its agent type holds, for the
    reason a board row's owner is.

    **The id is last.** It is a random token, and a row cut to a narrow pane
    loses its end: what is cut should be the token and not whose decision it is,
    whether it can be undone, or what it holds up. Only an id that is one plain
    word is shown at all.
    """
    parts = [
        _STANDING_TEXT[decision.standing],
        f"reversible: {_REVERSIBLE_TEXT[decision.reversible]}",
        _blocks_text(world, decision),
    ]
    fate = _FATE_TEXT[_asker_fate(world, decision.asker)]
    if fate is not None:
        parts.append(fate)
    if _PLAIN_ID.fullmatch(decision.id):
        parts.append(decision.id)
    who = " ".join(role_name(world, decision.asker, decision.session_id).split())
    return f"decision from {who}: " + "; ".join(parts)


def _blocks_text(world: Snapshot, decision: Decision) -> str:
    blocked = blocked_tasks(world, decision)
    if not blocked:
        return "blocks nothing on the board"
    plain = [task_id for task_id in blocked if _PLAIN_ID.fullmatch(task_id)]
    shown = plain[:_SHOWN_TASKS]
    rest = len(blocked) - len(shown)
    if not shown:
        return f"blocks {rest} task(s)"
    return "blocks " + ", ".join(shown) + (f" and {rest} more" if rest else "")


def blocked_tasks(
    world: Snapshot, decision: Decision, session_id: str | None = None
) -> tuple[TaskId, ...]:
    """
    The tasks ``decision.blocks`` names that are on its asker's board, each once,
    in the order it names them.

    ``session_id`` is the board to look on when ``decision`` has not been filed
    yet and its ``asker`` is still whatever the handler wrote; None reads it off
    the record.

    Every id a filed decision names was on that board when it was filed
    (``_raise_refusal``), so this differs from ``decision.blocks`` only by a
    repeat.
    """
    board = decision.session_id if session_id is None else session_id
    found: list[TaskId] = []
    for task_id in decision.blocks:
        task = world.tasks.get(task_id)
        if task is not None and task.belongs_to(board) and task_id not in found:
            found.append(task_id)
    return tuple(found)
