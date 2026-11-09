"""
Break-glass commands: what a step does to a record, and the refusals of a
request or a withdrawal.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from ..command import (
    MAX_OPEN_PER_ASKER,
    MAX_OPEN_SUBAGENTS,
    Command,
    CommandId,
    CommandState,
    End,
    RequestRefusal,
    Run,
    request_refusal,
)
from ..effects import CommandSettled, Effect
from ..ids import is_command_id
from ..intents import CommandApproved, CommandRequested, CommandsRestored, CommandWithdrawn
from ..model import CommandRefusal, Snapshot
from .owing import _allowance

# ``command.py`` refuses, with ``ValueError``, a record that would say something
# its parts do not. Nothing here lets one out of ``_apply``: each arm tests the
# state before it builds, and what is built from an intent's own values goes
# through ``_closed`` or ``_approved``, which answer None instead.


def _with_command(
    commands: Mapping[CommandId, Command], command: Command
) -> Mapping[CommandId, Command]:
    """``commands`` with ``command`` under its id, as a new map."""
    return MappingProxyType({**commands, command.id: command})


def _command_settled(
    request_id: str | None, command_id: CommandId, refusal: CommandRefusal | RequestRefusal | None
) -> tuple[Effect, ...]:
    """The reply to a request or a withdrawal, or nothing when nobody asked."""
    if request_id is None:
        return ()
    return (CommandSettled(request_id=request_id, command_id=command_id, refusal=refusal),)


def _request_refusal(
    snap: Snapshot, intent: CommandRequested
) -> CommandRefusal | RequestRefusal | None:
    """
    Why a command may not be put in front of the operator, or None if it may.

    The id first: it names a directory, and one the store holds is a retry to
    be told so and not that an allowance is spent. Then the string and the two
    texts, by ``command.request_refusal`` -- the handler asks it too, and this
    is the asking that counts.

    **Two bounds, and the root meets only the first**, as for a decision
    (``_raise_refusal``) and for its reasons: ``MAX_OPEN_PER_ASKER`` open for
    one agent, and ``MAX_OPEN_SUBAGENTS`` between the sub-agents of a session,
    counting only those whose asker has not ended. Open is asked or running.
    """
    asker = intent.node_id
    handed = intent.command
    if not is_command_id(handed.id):
        return CommandRefusal.BAD_ID
    if handed.id in snap.commands:
        return CommandRefusal.ALREADY_ASKED
    unaskable = request_refusal(handed.asked, handed.reason, handed.expects)
    if unaskable is not None:
        return unaskable
    # The caps are on what an agent has asked and not had an end to: a command
    # left running unsupervised owes a row, and is no request of anybody's.
    still_open = [c for c in snap.commands_awaiting(asker[0]) if c.is_open]
    match _allowance(snap, still_open, asker, MAX_OPEN_PER_ASKER, MAX_OPEN_SUBAGENTS):
        case "asker":
            return CommandRefusal.TOO_MANY_OPEN
        case "workers":
            return CommandRefusal.SESSION_FULL
        case None:
            return None


def _approved(snap: Snapshot, intent: CommandApproved) -> Command | None:
    """
    The command ``intent`` names with its run recorded, or None when the
    approval is not applied.

    **This is the whole of who may start a command, and of what starts**: an
    intent that names a node is an agent's and is not applied, and every field
    of the ``Run`` is the intent's. The string the agent asked for is not read.
    """
    if intent.node_id is not None:
        return None
    if snap.command_approval_refusal(intent.command_id, intent.text, intent.timeout_s) is not None:
        return None
    try:
        return dataclasses.replace(
            snap.commands[intent.command_id],
            run=Run(
                text=intent.text,
                shell=intent.shell,
                cwd=intent.cwd,
                timeout_s=intent.timeout_s,
                started_at=intent.at,
            ),
        )
    except ValueError:
        # A run the record cannot hold: no shell named.
        return None


def _closed(command: Command, end: End) -> Command | None:
    """
    ``command`` ended as ``end``, or None when the record cannot say that: an
    outcome that only ends an unapproved command, handed over for one that has
    a run, or the reverse.
    """
    try:
        return dataclasses.replace(command, end=end)
    except ValueError:
        return None


def _command_withdrawal_refusal(
    commands: Mapping[CommandId, Command], intent: CommandWithdrawn
) -> CommandRefusal | None:
    held = commands.get(intent.command_id)
    if held is None:
        return CommandRefusal.NO_SUCH_COMMAND
    if held.asker != intent.node_id:
        return CommandRefusal.NOT_THE_ASKER
    if held.state is not CommandState.ASKED:
        return CommandRefusal.NOT_ASKED
    return None


def _with_restored(
    commands: Mapping[CommandId, Command], intent: CommandsRestored
) -> Mapping[CommandId, Command] | None:
    """
    ``commands`` with the ones read from a session's files added, or None when
    nothing is applied.

    The batch is taken whole or not at all. One that holds an open command, a
    command of another session, or an id that is not a command id did not come
    from ``commandfile.restore``, and the rest of it is not worth more than the
    part that shows so. **So no file puts a command in front of the operator to
    be run, or leaves one running.**

    A command already held is skipped and never replaced: what this process saw
    of it is worth more than the file's account, and a second restore of one
    session changes nothing.
    """
    if intent.node_id is not None:
        return None
    if any(
        c.is_open or c.session_id != intent.session_id or not is_command_id(c.id)
        for c in intent.commands
    ):
        return None
    added: dict[CommandId, Command] = {}
    for restored in intent.commands:
        if restored.id not in commands:
            added.setdefault(restored.id, restored)
    if not added:
        return None
    return MappingProxyType({**commands, **added})


def _command_since(
    snap: Snapshot, commands: Mapping[CommandId, Command], now: float
) -> Mapping[CommandId, float]:
    """
    ``Snapshot.command_since`` for the step that leaves ``commands``: an entry
    for every command that owes a row (``Command.owes_row``) and for no other.

    A command with no entry yet became open in this step, and ``now`` is when.
    Asked once per step, of the records, and not in the arm that files a
    request or the three that end one: an arm cannot forget to stamp or to
    clear what it never writes. Made from the records on every step and not
    only when they change, so every open command has an entry whatever
    snapshot the step began from, and ``_owing_commands`` reads it unguarded.
    """
    held = snap.command_since
    since = {c.id: held.get(c.id, now) for c in commands.values() if c.owes_row}
    return held if since == held else MappingProxyType(since)
