"""
What ``read_digest`` says: ``board.digest``'s ``Digest`` laid out, and held to
the bound every reply is.

Cut from ``bus`` unchanged (``planning/2026-11-07-the-bus-is-a-table-of-tools.md``,
B7). Pure: no SDK, no clock, no IO.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence

from ..effects import between_quotes
from ..model import BEFORE_RESTART, ConcernState, ObligationKind, withdrawal_reason
from ..replies import _ANSWER, _MAX_BOARD_CHARS, _QUOTE, _named, _one_line, _quoted
from .digest import (
    EARLIER_INCIDENTS,
    ChangeKind,
    Digest,
    Owed,
    Reported,
    TaskChange,
    Unverified,
    change_label,
    changed_heading,
    incident_attribution,
    incident_label,
    incidents_heading,
    owed_counts,
    owed_standing,
    unverified_heading,
    unverified_label,
)
from .rows import UNDELIVERABLE, BoardConcern
from .text import (
    _MAX_CONCERN_CHARS,
    _MAX_NOT_RUN_CHARS,
    _closing_reason,
    _delimited_note,
    _legend,
    _rest,
)

# The first line of a result, in a digest. A digest names what finished and the
# board holds the account; a line is enough to tell which finished row to open.
_MAX_DIGEST_LINE_CHARS = 200


# What follows the heading of the two slots a lead may not summarise, when they
# hold anything. Said in the reply because that is where the lead is reading when
# it decides what to pass on; the headings themselves are shared with the pane,
# whose reader is the operator and is not being told what to do.
_IN_FULL = " Report every one of these to the operator."


# How a digest renderer is handed its allowance: text, the piece's own cap and
# the mark its lines stand behind in, the quoted lines that may be shown and how
# much was dropped out.
_Prose = Callable[[str, int, str], tuple[list[str], int]]


# What a quoted line is laid out on under a digest item.
_ITEM_LEAD = "    "


def _digest_text(digest: Digest, budget: int | None = None) -> str:
    """
    A digest as the asking agent reads it: four headed slots, every one present.

    **A slot is never left out and an item is never dropped.** An empty slot says
    what its emptiness means, in words, because "INCIDENTS" missing from a report
    reads as a report with no such section. The unverified slot and both incident
    lists are one line per item whatever the budget, since the digest may not show
    fewer of either than the board holds; what a budget takes is the prose under a
    line, and each cut says where the rest is.

    ``budget`` is the allowance for each piece of prose -- a result's first line, a
    list of what was not run, an incident's body -- laid out, or None for the
    per-piece bounds alone. ``_bounded_digest_text`` sets it.

    **Every label is one line, every field an agent wrote on it is delimited
    (``between_quotes``, ``_named``) and every piece of prose is quoted**, for the reason
    ``text._board_line`` gives: a slot heading is a line like any other, and a list of
    what was not run that could write one would put a forged INCIDENTS slot under
    the sentence telling the lead to report every item to the operator.

    **An incident's subject and text are shown only when a board read shows
    them.** See ``_incident_lines``.

    **The waiting slot names who is waiting and for what kind of thing, and not
    what about.** ``Owed.summary`` is the operator's queue row, and for a parked
    approval it is the text of a call nobody has approved: another agent's concern
    still in review, a command not yet run. This tool is auto-approved on the
    grounds that everything it returns was reviewed or is the caller's own board
    (``gate.tools.BUS_AUTO``), and one summary would make it a way round the gate.
    The operator's pane shows them.

    Says nothing about clock time. The stamps behind "since" order events inside
    this process and mean nothing outside it.
    """

    def prose(text: str, cap: int, mark: str) -> tuple[list[str], int]:
        return _quoted(text, cap, _ITEM_LEAD, budget, mark)

    if digest.since is None and digest.restored_at is not None:
        # A resumed lead has no mark, so this is the whole restored board, and
        # "from its start" above the sentence telling it what to report would
        # have it report an earlier run's work as this round's.
        opening = (
            f"Digest of this session's board as it was restored from its log: you "
            f"have not read one in this run, so CHANGED lists the whole board. What "
            f"is marked '{BEFORE_RESTART}' there is history and is not this round's "
            f"news."
        )
    elif digest.since is None:
        opening = "Digest of this session from its start: you have not read one before."
    else:
        opening = "Digest of this session since you last read one."
    closed = any(
        change.kind is ChangeKind.CLOSED and _closing_reason(change.line)
        for change in digest.changed
    )
    lines = [f"{opening} {_legend(closed)}"]

    lines += ["", changed_heading(digest.changed)]
    for change in digest.changed:
        lines += _change_lines(change, prose)

    lines += ["", _waiting_heading(digest.waiting)]
    lines += [_waiting_line(owed) for owed in digest.waiting]

    lines += ["", unverified_heading(digest.unverified) + (_IN_FULL if digest.unverified else "")]
    for item in digest.unverified:
        lines += _unverified_lines(item, prose)

    lines += ["", incidents_heading(digest) + (_IN_FULL if digest.incidents else "")]
    for reported in digest.incidents:
        lines += _incident_lines(reported, prose)
    if digest.earlier_incidents:
        # Listed, not counted. They were in an earlier digest in full, and a
        # reader of this one still has to be able to see that they exist.
        lines.append(f"  {EARLIER_INCIDENTS}:")
        lines += [
            f"  - {_incident_headline(reported)}{'' if reported.on_board else _NO_SUBJECT}"
            for reported in digest.earlier_incidents
        ]
    return "\n".join(lines)


def _change_lines(change: TaskChange, prose: _Prose) -> list[str]:
    named = dataclasses.replace(
        change,
        task_id=_named(change.task_id),
        title=between_quotes(change.title),
        holder=None if change.holder is None else _one_line(change.holder),
    )
    out = [f"- {change_label(named)}"]
    # A close's line is the store's label and then the operator's reason. The
    # label is on the line above already; what is left is the operator's, and
    # behind an agent's mark it would be attributed to nobody who wrote it.
    # A withdrawal's line is the store's label and then the declarer's reason:
    # the reason alone goes behind the agent mark, the declarer being an agent,
    # so the store's words are not quoted as the declarer's. No legend of its
    # own is needed, for the same reason: the agent mark already has one.
    closed = change.kind is ChangeKind.CLOSED
    if closed:
        line = _closing_reason(change.line)
    elif change.kind is ChangeKind.WITHDRAWN:
        line = withdrawal_reason(change.line, change.withdrawn_by_lead, change.withdrawal_edited)
    else:
        line = change.line
    if line:
        shown, dropped = prose(line, _MAX_DIGEST_LINE_CHARS, _ANSWER if closed else _QUOTE)
        out += shown
        if dropped:
            out.append(f"    {_rest(dropped, named.task_id)}")
    return out


def _waiting_line(owed: Owed) -> str:
    """
    One thing the operator owes, as an agent is told of it: who and what kind.

    A decision, a command, a held wake, an unanswered peer question and held
    peer mail are each worded apart from the rest: none of them says the node
    it names is waiting.
    An approval, a question and a failure each stop the node they name, so "is
    waiting" is true of it; an agent that raised a decision or asked for a command was told to
    carry on, and may be working. A command says which of its two open states it
    is in (``board.digest.owed_standing``), because one the operator has started is
    owed a cancel and not a reading, and "has not run" would be false of it.
    """
    who = _one_line(owed.who)
    if owed.kind is ObligationKind.DECISION:
        return f"- {owed.kind.value}: {who} asked the operator and has no answer yet"
    if owed.kind is ObligationKind.COMMAND:
        return (
            f"- {owed.kind.value}: {who} asked the operator to run it; it is {owed_standing(owed)}"
        )
    if owed.kind is ObligationKind.WAKE_CHAIN:
        return (
            f"- {owed.kind.value}: a wake for {who} on a channel is held until the operator "
            "releases it; read_peer_messages reads what waits there now"
        )
    if owed.kind is ObligationKind.PEER_QUESTION:
        return (
            f"- {owed.kind.value}: the operator is shown that {who} has not answered a "
            "question the lead linked to it asked: it is older than the age the operator "
            f"set, or {who}'s session is not running"
        )
    if owed.kind is ObligationKind.PEER_HELD:
        return (
            f"- {owed.kind.value}: mail for {who} from a linked lead is held: the link it was "
            "sent over does not hand it over in this run"
        )
    return f"- {owed.kind.value}: {who} is waiting"


def _waiting_heading(waiting: Sequence[Owed]) -> str:
    if not waiting:
        return "WAITING ON THE OPERATOR (0): nothing in this session is parked on the operator."
    return (
        f"WAITING ON THE OPERATOR ({len(waiting)}), oldest first: {owed_counts(waiting)}. "
        f"What each is about is the operator's to read and is not shown here."
    )


def _unverified_lines(item: Unverified, prose: _Prose) -> list[str]:
    named = dataclasses.replace(
        item,
        task_id=_named(item.task_id),
        title=between_quotes(item.title),
        owner=None if item.owner is None else _one_line(item.owner),
    )
    out = [f"- {unverified_label(named)}"]
    if item.text:
        shown, dropped = prose(item.text, _MAX_NOT_RUN_CHARS, _QUOTE)
        out += shown
        if dropped:
            out.append(f"    {_rest(dropped, named.task_id)}")
    return out


# What stands where a subject would, on a line that has nothing under it to say why.
_NO_SUBJECT = " (subject not shown)"


def _incident_headline(reported: Reported) -> str:
    """
    The one line every incident has, whatever else is shown of it: who reported
    it to whom and what became of it, and its subject only when a board read
    shows that subject (``_incident_lines``).

    A subject is as much the sender's text as a body is -- up to
    ``bus.MAX_LINE_CHARS`` of it -- so a headline that always carried one would hand
    every agent a withheld message on the line meant to say only that it exists.
    The same goes for the id of a task that is on no row of the board: it is
    whatever the sender typed, and no board read shows it.
    """
    c = _delimited_note(reported.concern)
    # After the subject, which stands between quotes, so it reads as the tool's.
    unread = f" -- {UNDELIVERABLE}" if c.undeliverable else ""
    if reported.on_board:
        return incident_label(dataclasses.replace(reported, concern=c)) + unread
    if c.task_missing:
        c = dataclasses.replace(c, task_id=None)
    return incident_attribution(dataclasses.replace(reported, concern=c)) + unread


def _incident_lines(reported: Reported, prose: _Prose) -> list[str]:
    """
    One incident: its attribution always, and its subject and text only when a
    board read hands the same reader the same subject and text.

    ``read_digest`` is open to every agent in the session and is never parked, so
    whatever it prints is broadcast. A board read broadcasts a concern on one
    condition -- a row carries it (``Reported.on_board``) -- and withholds a
    concern the operator withdrew and one that named no task on the board, which
    is mail between two agents. Printing those here would make the incident flag
    a way to publish a message its sender addressed to one agent, and would
    deliver a message the operator stopped under a label saying it was not
    delivered.

    The line itself stays, so the slot never holds fewer incidents than the board
    does, and it says why nothing the sender wrote is on or under it.
    """
    c = _delimited_note(reported.concern)
    out = [f"- {_incident_headline(reported)}"]
    if not reported.on_board:
        out.append(f"    ({_withheld(c)})")
        return out
    shown, dropped = prose(c.body, _MAX_CONCERN_CHARS, _QUOTE)
    out += shown
    if dropped and c.task_id is not None:
        out.append(f"    {_rest(dropped, c.task_id)}")
    return out


def _withheld(c: BoardConcern) -> str:
    """
    Why an incident's subject and text are not in a digest. The two mistakes a reader could
    make about the gap are named apart: one is the operator's act and the other
    is the sender's choice of address.
    """
    if c.state is ConcernState.WITHDRAWN:
        return "its subject and text are not shown here: the operator withdrew it before delivery"
    if c.task_id is None or c.task_missing:
        return (
            f"its subject and text are not shown here: it named no task on this "
            f"board, so it is mail between {c.sender} and {c.recipient}, and "
            f"{c.recipient} reads it with read_inbox"
        )
    return "its subject and text are not shown here: no row of this board carries it"


def _bounded_digest_text(digest: Digest) -> str:
    """
    The whole digest reply, under the ceiling a board read keeps.

    ``text._board_text``'s rule and for its reason: structure is never dropped and
    prose degrades. Rendered once whole; if that fits it is the reply. Otherwise
    it is rendered with no prose to measure the floor, and what the ceiling leaves
    over the floor is shared equally between the pieces of prose.

    The floor wins when even it does not fit. Every unverified item and every
    incident is a line of the floor, and dropping one to get under a number is the
    thing a digest may not do.
    """
    full = _digest_text(digest)
    if len(full) <= _MAX_BOARD_CHARS:
        return full
    floor = _digest_text(digest, budget=0)
    pieces = (
        sum(1 for change in digest.changed if change.line)
        + sum(1 for item in digest.unverified if item.text)
        + sum(1 for reported in digest.incidents if reported.on_board and reported.concern.body)
    )
    notice = (
        "\n\nThis digest did not fit, so each result line, list of what was not run "
        "and incident body below is cut to {share} characters. Nothing is hidden: "
        "every change, every unverified statement and every incident is listed. "
        "read_task(task_id) returns any one task, its specification whole."
    )
    # A share is what a piece may cost laid out, which ``_quoted`` charges whole.
    spare = _MAX_BOARD_CHARS - len(floor) - len(notice)
    share = max(0, spare // max(1, pieces))
    head, _, rest = _digest_text(digest, budget=share).partition("\n")
    return head + notice.format(share=share) + "\n" + rest
