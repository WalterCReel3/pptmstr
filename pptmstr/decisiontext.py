"""
What the bus says about a decision: one read whole, a list, the headline a
raise is answered with, and the refusals the three decision tools can meet.

Cut from ``bus`` unchanged (``planning/2026-11-07-the-bus-is-a-table-of-tools.md``,
B7). ``_REVERSIBLE_SAID`` and ``_BLOCKS_ARE`` are here and not beside the
argument readers in ``bus`` that also read them: here, ``bus`` imports them;
there, this would import ``bus``. Pure: no SDK, no clock, no IO.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import assert_never

from .board.text import _refusal_text
from .channels.records import PeerIdentity
from .channels.text import _linked_now, peer_said
from .effects import between_quotes
from .model import (
    MAX_ID_CHARS,
    MAX_OPEN_DECISIONS_PER_ASKER,
    MAX_OPEN_SUBAGENT_DECISIONS,
    Answered,
    Decision,
    DecisionRefusal,
    Dismissed,
    NodeId,
    Standing,
    TaskId,
    TaskRefusal,
    Withdrawn,
)
from .replies import (
    _ANSWER,
    _LIST_NOTICE_ROOM,
    _MAX_BOARD_CHARS,
    _MAX_ROLE_CHARS,
    _QUOTE_LEGEND,
    WAKE_SAID,
    WAKE_SAID_SUBAGENT,
    _age,
    _argument,
    _cost,
    _named,
    _one_line,
    _quoted,
)

_ANSWER_LEGEND = (
    "A line whose first characters after any indent are '> ' is the operator's "
    "answer, as the operator typed it, and is neither this tool speaking nor text "
    "an agent wrote."
)


# How much of a question a list of decisions shows on its one line. The read of
# one decision by id returns the question whole.
_MAX_QUESTION_LINE_CHARS = 300


# What a read of one decision may spend on everything that is not one of the
# asker's texts, a blocked task's id, or the operator's answer: the two legends,
# the headline, the headings and the sentences of this module's own.
#
# It is the last term of the sum that makes that reply fit ``_MAX_BOARD_CHARS``
# by construction, with the answer in it whole. Laid out, a text of n characters
# costs at most 3n + 2 -- every character can be a line break, and each line
# carries a two-character mark and a newline -- and an id at most twice its
# length and two quote marks. So, at their largest:
#
#   4 texts    4 x (3 x 1,000 + 2)   = 12,008
#   answer          3 x 4,000 + 2    = 12,002
#   8 ids      8 x (2 x 200 + 4)     =  3,232
#   this                             =  2,000
#                                      29,242  <=  30,000
#
# tests/test_bus.py computes the sum from the constants and builds that reply.
_DECISION_FRAME_CHARS = 2_000


_REVERSIBLE_SAID: dict[bool | None, str] = {True: "yes", False: "no", None: "unstated"}


def _standing_said(standing: Standing, whose: str) -> str:
    """Whose decision it is, with ``whose`` the asker as its reader names it."""
    match standing:
        case Standing.OPERATORS:
            return "the operator's to take"
        case Standing.TAKEN:
            return f"taken by {whose}, for the operator to ratify"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(standing)


_BLOCKS_ARE = (
    "blocks is a list of the ids of tasks on your board that wait for this answer, "
    "as read_board shows them, and it may be empty. Anything else that waits "
    "belongs in the question or in if_unanswered."
)


def _blocks_said(blocks: Sequence[TaskId]) -> str:
    """What a decision blocks, on one line: never silence for an empty list."""
    if not blocks:
        return "blocks nothing on the board"
    return "blocks " + ", ".join(_named(task_id[:MAX_ID_CHARS]) for task_id in blocks)


def _decision_refusal_text(
    refusal: DecisionRefusal,
    decision_id: str,
    root: bool,
    strangers: Sequence[TaskId] = (),
    now_on: PeerIdentity | None = None,
) -> str:
    """
    Why a raise or a withdrawal did not take effect, and what to do instead.
    Exhaustive for ``board.text._refusal_text``'s reason.

    **Every remedy named is one the refused agent can take.** ``root`` is whether
    it is its session's lead, which has no lead to pass a question to, holds none
    of the sub-agents' allowance, and can read every decision of its session.

    **To a sub-agent, somebody else's decision and one that does not exist get
    one sentence.** Its ``read_decisions`` cannot tell them apart -- it is handed
    its own and nothing else -- and a withdrawal that could would be a way to
    ask whether an id is another agent's. The root is shown every decision of
    its session, so it is told which.

    ``strangers`` is the entries of ``blocks`` the board does not have, when the
    caller found out which. ``now_on`` is the lead on the live link of that
    name now, which the store names on ``LINKED_AFTER``
    (``DecisionSettled.peer``).
    """
    match refusal:
        case DecisionRefusal.TOO_MANY_OPEN:
            return (
                f"You have {MAX_OPEN_DECISIONS_PER_ASKER} decisions open in front of "
                f"the operator, which is the most one agent may have. The limit "
                f"counts only your own. read_decisions lists them: withdraw one that "
                f"no longer matters with withdraw_decision, or call again once the "
                f"operator has answered one."
            )
        case DecisionRefusal.SESSION_FULL:
            return (
                f"The sub-agents of this session have {MAX_OPEN_SUBAGENT_DECISIONS} "
                f"decisions open in front of the operator between them, which is the "
                f"most they may have. read_decisions lists yours, and withdraw_decision "
                f"frees a place if one of them no longer matters. If you have none to "
                f"withdraw, put this to your lead as a concern: the lead has an "
                f"allowance of its own that the sub-agents do not spend, and can raise "
                f"it for you."
            )
        case DecisionRefusal.UNKNOWN_TASK:
            named = ", ".join(_named(task_id[:MAX_ID_CHARS]) for task_id in strangers)
            which = f"blocks names {named}, and" if named else "An entry of blocks is"
            return (
                f"{which} no task with that id is on your board. {_BLOCKS_ARE} Leave "
                f"blocks out and the decision is recorded as blocking nothing on the "
                f"board."
            )
        case DecisionRefusal.NOT_A_LEAD:
            return (
                "Only a session's lead raises a decision on a channel: a link joins "
                "leads. Leave channel out to raise it inside your team, or put it to "
                "your lead as a concern if the other team should read it."
            )
        case DecisionRefusal.NOT_ON_CHANNEL:
            return (
                "No open link of that name has your session as a member. Only the "
                "operator links sessions; leave channel out to raise it inside your "
                "team."
            )
        case DecisionRefusal.LINKED_AFTER:
            return (
                "The live link of that name went live after you made this call, so "
                "it is not a link you could have raised this for, and its lead may "
                f"not be the one you meant: {_linked_now(now_on)} read_peer_messages "
                "lists your session's live links; raise it again if it is for the "
                "lead on that one."
            )
        case DecisionRefusal.ALREADY_RAISED:
            return "The id generated for it was already in use. Call again; nothing changed."
        case DecisionRefusal.NOT_THE_ASKER if root:
            return (
                "Another agent raised it -- one of your session, or the lead on the "
                "other end of a channel -- and only the agent that raised a decision "
                "withdraws it. read_decisions shows whose it is; send that agent a "
                "concern, or the other lead a message, if it should be taken back. The "
                "operator can dismiss it from their own pane."
            )
        case DecisionRefusal.NO_SUCH_DECISION | DecisionRefusal.NOT_THE_ASKER:
            return _unknown_decision_text(decision_id, root)
        case DecisionRefusal.ALREADY_ENDED:
            return (
                "It has already ended -- answered, dismissed or withdrawn -- and an "
                "ended decision stays as it ended. read_decisions shows which."
            )
        case DecisionRefusal.NOT_APPLIED:
            return _refusal_text(TaskRefusal.NOT_APPLIED)
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(refusal)


def _unknown_decision_text(decision_id: str, root: bool) -> str:
    """
    The answer to an id the reader is shown no decision under.

    In words and never an empty list. A decision is kept in its asker's session
    log and is back after a restart once that session is resumed, so an id a
    transcript names may be one this run has not read back yet.

    **The same words for every reason a lead is shown nothing**, because which
    one it is would tell a lead of an unrelated session that a decision exists
    under an id: so the lead on the far end of a link is told, among the rest,
    the two things such a decision needs to be shown -- its link live, and the
    session that raised it resumed in this run -- without which one is missing,
    and is never told to raise it, since it was never its own.
    """
    named = _named(decision_id[:MAX_ID_CHARS])
    if root:
        return (
            f"No decision {named} is shown to you. A decision raised in your session "
            f"is kept in its log and is back after a restart of pptmstr once your "
            f"session is resumed; read_decisions with no id lists every one shown to "
            f"you. One the lead on the other end of a channel raised is shown only "
            f"while the link it was raised on is live and the session that raised it "
            f"is resumed in this run."
        )
    return (
        f"No decision {named} is recorded for you. One you raised is kept in your "
        f"session's log and is back after a restart of pptmstr once your session is "
        f"resumed. A decision another agent raised is shown to that agent and to the "
        f"session's lead, and never here."
    )


def _sentence_opening(who: str | None) -> str:
    """``who`` as the first word of a sentence: "the lead on #x" capitalised."""
    said = who or ""
    return "The" + said[3:] if said.startswith("the ") else said


def _answer_lines(text: str) -> list[str]:
    """
    The operator's answer, whole, as the lines it is shown on: every one behind
    ``_ANSWER``, none cut, none dropped.

    A line break of any kind ``str.splitlines`` knows starts a new marked line,
    so no character of an answer can put what follows it on a line that reads as
    the tool's. Nothing else is changed: no stripping, and an answer ending in a
    line break ends in an empty marked line, so one made only of ``\n`` breaks is
    exactly the marked lines joined by ``\n``.
    """
    pieces = text.splitlines(keepends=True)
    lines = ["".join(piece.splitlines()) for piece in pieces]
    if pieces and pieces[-1] != lines[-1]:
        lines.append("")
    return [f"{_ANSWER}{line}" for line in lines]


def _unread(decision: Decision) -> bool:
    """Whether the operator ended ``decision`` and its asker has not read that by id."""
    return (
        isinstance(decision.outcome, Answered | Dismissed) and decision.first_delivered_at is None
    )


def _own_block(heading: str, text: str) -> list[str]:
    """One of the asker's own texts, whole, under a heading of its own."""
    if not text.strip():
        return [f"{heading}: (nothing was stated)"]
    return [f"{heading}:", *_quoted(text, len(text), "")[0]]


def _state_word(decision: Decision) -> str:
    match decision.outcome:
        case None:
            return "open"
        case Answered():
            return "answered"
        case Dismissed():
            return "dismissed"
        case Withdrawn():
            return "withdrawn"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(decision.outcome)


def _headline(decision: Decision, asker: str | None) -> str:
    """
    The line a decision opens with in either reply. ``asker`` is the role that
    raised it when that is not the reader, and None when the reader did.
    """
    whose = "you" if asker is None else asker
    line = (
        f"Decision {_named(decision.id)} [{_state_word(decision)}] "
        f"{_standing_said(decision.standing, whose)}; "
        f"reversible: {_REVERSIBLE_SAID[decision.reversible]}"
    )
    if asker is None:
        return line
    return f"{line}; raised by {asker}, not by you: you can read it and cannot withdraw it"


def _decision_whole(decision: Decision, now: float, asker: str | None) -> list[str]:
    """
    One decision with everything on it, as the read by id returns it.

    **The operator's answer is whole on every read**, behind its own mark
    (``_answer_lines``), whether this is the first read or the fortieth. It has
    no other copy an agent can reach: a concern cut in a board read is whole in
    ``read_task``, and an answer cut here would be gone. So nothing here cuts,
    and the reply fits because each thing in it was bounded where it was
    accepted (``_DECISION_FRAME_CHARS``).

    **The evidence and the recommendation are two blocks and are never joined**:
    they are held apart on the record so that what is known can be read before
    what the asker wants done with it, and a reply that ran them together would
    hand the next reader the framing as though it were the finding.

    The asker's texts are quoted like any agent's (``_quoted``): this reply may
    be passed on, and they are still not the tool's words. ``asker`` is as on
    ``_headline``; a reader that is not the asker is told whether the asker has
    read the answer, because nothing hands it to the asker but its own read.
    """
    mine = asker is None
    by = "you" if mine else asker
    named = _named(decision.id)
    lines = [_headline(decision, asker)]
    lines += _own_block(f"Question, as {by} asked it", decision.question)

    read_at = decision.first_delivered_at
    if mine:
        delivery = (
            "handed to you now for the first time"
            if read_at is None
            else f"first delivered {_age(now - read_at)} ago"
        )
    else:
        delivery = (
            f"which {by} has not read"
            if read_at is None
            else f"which {by} first read {_age(now - read_at)} ago"
        )
    match decision.outcome:
        case None:
            if not mine and decision.channel is not None:
                # A channel decision is raised only by a lead, so a reader that
                # is not its asker is the lead on the link's other end.
                lines.append(
                    f"The operator has not answered. When they do, pptmstr sends your "
                    f"session a message naming it, as it does the asker's, or names it "
                    f"when your session is next resumed if it is not running then: "
                    f"read_decisions(decision_id={_argument(named)}) returns the answer "
                    f"while the link is open."
                )
            elif not mine:
                lines.append(
                    f"The operator has not answered. When they do, pptmstr sends this "
                    f"session a message naming it, and {by} is not woken: "
                    f"read_decisions(decision_id={_argument(named)}) then returns it."
                )
            elif decision.asker[1] is None:
                lines.append(
                    f"The operator has not answered. {WAKE_SAID} Ask again when you "
                    f"reach what this blocks."
                )
            else:
                lines.append(
                    f"The operator has not answered. {WAKE_SAID_SUBAGENT} Ask again "
                    f"when you reach what this blocks."
                )
        case Answered(text=text):
            lines.append(f"The operator's answer, {delivery}:")
            lines += _answer_lines(text) or ["(the operator answered with no text)"]
        case Dismissed():
            lines.append(
                f"The operator closed it without answering ({delivery}). No answer "
                f"exists: go on as you said you would if it went unanswered, or raise "
                f"it again if it still matters."
                if mine
                else f"The operator closed it without answering, {delivery}. No answer " f"exists."
            )
        case Withdrawn():
            lines.append(
                "You withdrew it. No answer exists."
                if mine
                else f"{_sentence_opening(by)} withdrew it. No answer exists."
            )

    lines.append(f"It {_blocks_said(decision.blocks)}, as {by} stated it.")
    lines += _own_block(f"If unanswered, as {by} stated it", decision.if_unanswered)
    lines += ["", *_own_block(f"Evidence, as {by} gave it", decision.evidence)]
    lines += ["", *_own_block(f"Recommendation, as {by} gave it", decision.recommendation)]
    return lines


def _decision_listed(decision: Decision, now: float, asker: str | None) -> list[str]:
    """
    One decision in the list: what it is, one line of its question, and what
    became of it. **Never the operator's answer.** A list has no bound on how
    many answers it would hold, so it says an answer exists and names the read
    that returns it, and the store records an answer as read only for that read.
    """
    mine = asker is None
    by = "you" if mine else asker
    named = _named(decision.id)
    by_id = f"read_decisions(decision_id={_argument(named)})"
    flat = _one_line(decision.question)
    lines = [
        _headline(decision, asker),
        f"Question: {between_quotes(flat[:_MAX_QUESTION_LINE_CHARS])}",
    ]
    cut = len(flat) - _MAX_QUESTION_LINE_CHARS
    if cut > 0:
        lines.append(f"... {cut} more character(s) -- {by_id} returns it whole")

    read_at = decision.first_delivered_at
    match decision.outcome:
        case None:
            lines.append(
                "The operator has not answered. Nothing will interrupt you when they "
                "do: ask again when you reach what this blocks."
                if mine
                else "The operator has not answered."
            )
        case Answered():
            if mine and read_at is None:
                lines.append(
                    f"THE OPERATOR HAS ANSWERED and you have not read the answer: "
                    f"{by_id} returns it whole."
                )
            elif mine:
                assert read_at is not None
                lines.append(
                    f"The operator answered, and you first read the answer "
                    f"{_age(now - read_at)} ago: {by_id} returns it again, whole."
                )
            else:
                seen = (
                    "has not read the answer"
                    if read_at is None
                    else f"first read the answer {_age(now - read_at)} ago"
                )
                lines.append(f"The operator answered, and {by} {seen}: {by_id} returns it whole.")
        case Dismissed():
            lines.append(
                "The operator closed it without answering. No answer exists: go on as "
                "you said you would if it went unanswered, or raise it again if it "
                "still matters."
                if mine
                else "The operator closed it without answering. No answer exists."
            )
        case Withdrawn():
            lines.append(
                "You withdrew it. No answer exists."
                if mine
                else f"{_sentence_opening(by)} withdrew it. No answer exists."
            )
    return lines


def _decisions_text(
    shown: Sequence[Decision],
    wanted: str | None,
    now: float,
    reader: NodeId,
    role_of: Callable[[NodeId], str],
    peers: Mapping[str, PeerIdentity] | None = None,
) -> str:
    """
    The reply to ``read_decisions``: one decision whole when an id was given,
    and otherwise a list of every decision the reader is shown.

    ``shown`` is what the store handed over -- the reader's own, and for the
    root of a session every decision raised in it. ``role_of`` names the asker
    of one that is not the reader's, and ``peers`` the lead of another session
    that raised one over a link (``DecisionsDelivered.peers``).

    **Both forms fit ``_MAX_BOARD_CHARS``.** The read by id fits by what a
    decision may hold (``_DECISION_FRAME_CHARS``). The list is filled against
    that bound and says what it left out, keeping first what the reader has to
    act on: the open ones, then the ones whose ending it has not read.
    """
    root = reader[1] is None

    def asker(decision: Decision) -> str | None:
        if decision.asker == reader:
            return None
        if decision.session_id != reader[0] and decision.channel is not None:
            # The store hands another session's decision only to a member of
            # the live link it was raised over, and its name was checked to be
            # one a channel can have.
            peer = (peers or {}).get(decision.session_id, PeerIdentity(session=decision.session_id))
            return f"the lead on #{_named(decision.channel)} ({peer_said(peer)})"
        return _one_line(role_of(decision.asker))[:_MAX_ROLE_CHARS]

    if wanted is not None:
        chosen = next((d for d in shown if d.id == wanted), None)
        if chosen is None:
            return _unknown_decision_text(wanted, root)
        legend = f"{_QUOTE_LEGEND} {_ANSWER_LEGEND}"
        return "\n".join([legend, "", *_decision_whole(chosen, now, asker(chosen))])

    if not shown:
        whose = "in your session" if root else "for you"
        return (
            f"No decision is recorded {whose}. A decision is kept in its asker's "
            f"session log, and one raised before a restart of pptmstr is back once "
            f"that session is resumed."
        )

    mine = [d for d in shown if d.asker == reader]
    still = sum(1 for d in shown if d.is_open)
    across = sum(1 for d in shown if d.session_id != reader[0])
    counted = (
        f"Your session has {len(shown)} decision(s), {still} still open; "
        f"{len(mine)} of them you raised yourself. As the session's lead you are "
        f"shown every one, each marked with who raised it."
        if root and not across
        else (
            (
                f"You are shown {len(shown)} decision(s), {still} still open; "
                f"{len(mine)} of them you raised yourself, and {across} the lead on the "
                f"other end of a channel raised, shown while that link is open. As the "
                f"session's lead you are shown every one raised in your session too, "
                f"each marked with who raised it."
            )
            if root
            else f"You have raised {len(shown)} decision(s), {still} still open."
        )
    )
    head = (
        f"{counted} No answer is in this list: read_decisions(decision_id=...) returns "
        f"one decision with everything on it and the operator's answer whole. "
        f"{_QUOTE_LEGEND}"
    )

    def rank(decision: Decision) -> tuple[int, float]:
        # The reader's own session first: its open decisions, then the answers
        # the reader has not read. Another lead's open decisions follow them, so
        # a list cut to fit drops those before anything the reader acts on.
        if decision.is_open and decision.session_id == reader[0]:
            return 0, decision.raised_at
        if decision.asker == reader and _unread(decision):
            return 1, decision.raised_at
        if decision.is_open:
            return 2, decision.raised_at
        return 3, -decision.raised_at

    spare = _MAX_BOARD_CHARS - len(head) - _LIST_NOTICE_ROOM
    kept: dict[str, list[str]] = {}
    for decision in sorted(shown, key=rank):
        entry = ["", *_decision_listed(decision, now, asker(decision))]
        if _cost(entry) > spare:
            break
        spare -= _cost(entry)
        kept[decision.id] = entry

    lines = [head]
    for decision in shown:
        lines += kept.get(decision.id, [])
    left = [d for d in shown if d.id not in kept]
    if left:
        still_open = sum(1 for d in left if d.is_open)
        unread = sum(1 for d in left if d.asker == reader and _unread(d))
        lines += [
            "",
            f"... {len(left)} more decision(s) did not fit and are not listed "
            f"({still_open} open, {unread} with an ending you have not read). Each is "
            f"still returned by read_decisions(decision_id=...) under the id "
            f"raise_decision gave for it.",
        ]
    return "\n".join(lines)
