"""
What the bus says about the board: a row, a task read whole or in parts, the
replies to a board write, and the refusals a board call can meet.

The handlers in ``bus`` decide what happened and these say it, cut from ``bus``
unchanged (``planning/2026-11-07-the-bus-is-a-table-of-tools.md``, B7). The other
text modules of the bus import from here, and this imports none of them.

The underscore on a name says "not for a caller outside the bus", as it does in
``replies``. Pure: no SDK, no clock, no IO.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator, Mapping, Sequence
from typing import assert_never

from ..effects import between_quotes
from ..model import (
    ALL_WRITERS,
    MAX_ID_CHARS,
    MAX_WITHDRAWAL_REASON_CHARS,
    ClaimRefusal,
    ConcernId,
    NodeId,
    Provenance,
    TaskId,
    TaskRefusal,
    TaskState,
    closing_result,
    is_closing_notice,
    is_withdrawal_notice,
    withdrawal_reason,
)
from ..replies import (
    _ANSWER,
    _MAX_BOARD_CHARS,
    _QUOTE,
    _QUOTE_LEGEND,
    _argument,
    _cost,
    _named,
    _one_line,
    _quoted,
)
from .rows import (
    UNDELIVERABLE,
    BoardConcern,
    BoardTask,
    BoardVersion,
    all_writers_label,
    ended_label,
    foreign_wait_label,
    stale_label,
    version_label,
)
from .words import basis_label, incident_marker, not_run_label, property_label

# One task's specification in a board read. Generous: this is the field the tool
# exists to expose, and a lead that has to ask for the rest of its own spec is the
# defect being fixed. Bounded at all because a board read is a reply an agent pays
# for by the token, and one 40,000-character detail would crowd out every other row.
_MAX_DETAIL_CHARS = 2000


# One result in a board read: what a claimer reported when it finished. The same
# size as a specification because it is the same kind of text -- a paragraph one
# agent wrote for the next -- and the reader of a finished row needs it for the
# reason the reader of an open one needs the specification.
_MAX_RESULT_CHARS = 2000


# One agent note in a board read. Tighter than a specification: a concern is one
# finding, and a reader scanning a board wants the finding rather than the essay.
# Announced when it bites, like every other bound here.
_MAX_CONCERN_CHARS = 1200


# What a claimer listed as not run, in a board read. A note's size, because it is
# the same kind of text: a short list of findings about the work, not an account
# of it. Whether such a list exists at all is never cut -- see ``_board_line``.
_MAX_NOT_RUN_CHARS = 1200


# The whole reply is bounded by ``replies._MAX_BOARD_CHARS``: the bounds above are
# per row, and a bound on each row is not a bound on their sum.


def _board_line(
    row: BoardTask,
    reasons: Mapping[ConcernId, BoardConcern] | None = None,
    budget: int | None = None,
    root: bool = False,
) -> str:
    """
    One board row, as the asking agent reads it. ``root`` says that agent is the
    session's lead, which changes one phrase: what a row whose holder is gone
    says can be done about it (``_stranded``).

    Says who holds a task rather than only its state, because "claimed" without an
    owner is the answer that sends a worker to ask the lead what it already could
    have known. The owner is the address the bus routes to, so a reader can act on
    it -- ``post_concern(to="builder-2")`` -- rather than only recognise it.

    A dependency this board does not have is called out separately from one that
    is merely unfinished. They look identical on the record and they are different
    problems: the first will never clear on its own, and nothing else in the
    session would ever say so. It is itself two things with two remedies, and
    the row knows which (``BoardTask.held_elsewhere``): an id nothing in the
    window has is a typo in somebody's ``depends_on`` or a task not declared
    yet (``_UNDECLARED``), and an id another session's task has can never be
    declared here at all (``board.rows.foreign_wait_label``).

    **Carries ``detail``, which is the point of the tool rather than a detail of
    it.** ``Task.detail`` is the closest thing to an authoritative specification
    this system has, and ``claim_task`` hands it over once, to the agent that wins
    the task. A lead reads back what it wrote here, and so does the worker holding
    the task, which cannot claim it a second time: ``_pick_claim`` requires
    ``is_claimable``, which requires PENDING, so the current owner is told
    "Nothing claimable right now".

    An amended specification depends on that. What a worker is asked to do about
    one is re-read the board instead of trusting its transcript copy, and a row
    without the text would make that a discipline it cannot practise
    (`what-the-board-does-not-carry.md`).

    ``touches`` rides along because a worker that cannot see which files a task
    claims cannot honour the boundary the auto-dependency exists to enforce, and an
    open concern is named because a row stalled for a recorded reason is a different
    row from one stalled silently.

    **``budget`` is this row's whole allowance for prose**, shared by the
    specification, the result, the claimer's list of what it did not run and the
    note bodies, or None for the per-row bounds alone. It
    exists because the sum of bounded rows is not itself bounded (see
    ``_MAX_BOARD_CHARS``). Nothing above the prose is charged against it: the
    identifier, state, owner, blockers, files, the property the work may not break,
    and every note's sender and subject are what a reader needs in order to claim
    correctly and to know who to ask, and a board that has lost those has lost the
    thing it is for.

    **The property is the one sentence on a row that is never cut.** It is bounded
    where it is written (``model.MAX_PROPERTY_CHARS``), so it can be shown whole
    here, directly under the title.

    **A finished row always says where it stands on what was not run**, in the
    result's heading or in a heading of its own, and that survives a cut that
    takes every word under it. An unstated row reading as a verified one is the
    default this refuses.

    **A row the operator closed says that on its headline and has no claimer's
    standing at all** (``board.rows.closed_label``): it is finished and nothing was
    built, so a basis and a "not run" beside it would describe work nobody
    reported. The operator's reason is laid out where a result would be, behind
    the operator's mark and not an agent's (``_ANSWER``, ``_closing_reason``).

    The specification is served before the notes when the two compete. A worker
    reads this row to decide whether to claim it and then to build from it, and a
    note is a finding *about* work whose description it presumes.

    **A result is served before either, and laid out after the specification.**
    Only a completed row has one, and a completed row is read for what happened
    rather than for what was asked: the work the specification describes is over,
    and the reader is the worker building on it. Serving the specification first
    would spend a crowded board's whole share on text whose job is done: a
    finished row's specification alone can be longer than its share. The layout
    stays in the order the two were written, so a row reads as asked-then-answered
    whichever of them was cut.

    **A result and a list of what was not run are the two paragraphs here no
    operator saw first, under any policy.** Both ride ``complete_task``, which the
    gate never parks (``approval._BUS_AUTO``). A specification and a note are parked
    at ``declare_task`` and ``post_concern`` wherever those are reviewed, which is
    not every rung. Each is the account of the agent its heading names, and a
    reader should weigh it as that.

    **No line an agent wrote has the form of a line this function writes, and no
    field an agent wrote can pass for the words beside it.** Every line of a body
    is laid out behind ``_QUOTE``, which nothing here starts a line of its own
    with: a body indented under its heading and otherwise emitted as written lets
    its second line be a row headline, a result heading with another agent's name
    in it, or a note attributed to the lead. Every one-line field has its line
    breaks taken out and is delimited (``_delimited_task``), because a title laid
    out bare can end in ``· held by builder-2 · specification says: run`` and an
    id can carry a state of its own ahead of the real one.

    What a cut leaves behind names ``read_task``, which returns the row whole.
    """
    row = _delimited_task(row)
    parts = [f"- {row.id} [{row.state.value}] {row.title}"]
    if row.owner is not None:
        parts.append(f"held by {row.owner}")
    if row.owner_gone:
        parts.append(f"but {_stranded(row, root)}")
    if _waiting_on(row):
        parts.append(f"waiting on {', '.join(_waiting_on(row))}")
    if row.after_all_writers:
        parts.append(all_writers_label(row))
    if _undeclared(row):
        parts.append(f"{_UNDECLARED}: {', '.join(_undeclared(row))}")
    if row.held_elsewhere:
        parts.append(foreign_wait_label(row))
    if row.touches:
        parts.append(f"writes {', '.join(row.touches)}")
    if row.provenance is not None:
        parts.append(_provenance_said(row.provenance))
    if row.unworked:
        # On the headline, beside the state it qualifies: "completed" alone
        # reads as work done, and this outlives a cut that takes the reason.
        parts.append(ended_label(row))
    # Which specification this is and whether its holder has seen it, on the
    # headline for the reason the basis is: both outlive a cut that takes the
    # text, and a replaced specification shown without its version reads as the
    # one that was always there.
    parts += [fact for fact in (version_label(row), stale_label(row)) if fact]
    if row.detail:
        # On the headline rather than on the paragraph it qualifies, so it is
        # structure: a crowded board that cuts the specification to nothing still
        # says how its author stood behind it, and a standing that vanished with
        # the text would leave what remains reading as the settled kind.
        parts.append(f"specification {basis_label(row.basis)}")
    if row.state is TaskState.COMPLETED and not row.result and row.not_run is None:
        # A finished row with no account at all still says where it stands. The
        # result heading carries this for a row that has one.
        parts.append(not_run_label(None))
    line = " · ".join(parts)
    if row.property:
        # Directly under the title and charged to no budget: it is one bounded
        # sentence (``model.MAX_PROPERTY_CHARS``), and it is what a reader ranks
        # everything below it against.
        line += f"{_DETAIL_INDENT}{property_label(row.property)}"
    spare = None if budget is None else max(0, budget)

    # Measured ahead of the result and the specification and emitted after both:
    # what a claimer wrote and did not run is the part of a finished row a reader
    # most needs and is least likely to go looking for.
    unrun, unrun_dropped = _quoted(row.not_run or "", _MAX_NOT_RUN_CHARS, _DETAIL_LEAD, spare)
    if spare is not None:
        spare -= _cost(unrun)

    # Likewise ahead of the specification: the budget goes to the result before
    # it, and the row still reads in the order it was written.
    account = _account(row)
    result, result_dropped = _quoted(
        account, _MAX_RESULT_CHARS, _DETAIL_LEAD, spare, _account_mark(row)
    )
    if spare is not None:
        spare -= _cost(result)

    if row.detail:
        # Under its row rather than joined with the separator: a specification is
        # a paragraph, and run into a one-line summary it makes the summary
        # unreadable.
        body, dropped = _quoted(row.detail, _MAX_DETAIL_CHARS, _DETAIL_LEAD, spare)
        line += _laid(body)
        if dropped:
            line += f"\n    {_rest(dropped, row.id)}"
        if spare is not None:
            spare -= _cost(body)
    if account:
        # The heading is structure, like a note's sender: it is what says a result
        # exists when the board could not afford a word of it.
        line += f"\n    -- {_result_heading(row)}"
        line += _laid(result)
        if result_dropped:
            line += f"\n    {_rest(result_dropped, row.id)}"
    if row.not_run:
        # A heading of its own, so a board that could not afford a word of the
        # list still says there is one.
        line += f"\n    -- {_not_run_heading(row)}"
        line += _laid(unrun)
        if unrun_dropped:
            line += f"\n    {_rest(unrun_dropped, row.id)}"
    line += _reasons_text(row, reasons or {}, spare)
    return line


def _provenance_said(provenance: Provenance) -> str:
    """
    Where a task came from, when another lead asked for it, as a board row says
    it: the channel, the message it answers and the charter mark, all of it the
    declarer's statement, and said to be.

    One line (``_one_line``) whatever a restored log held: the channel and the
    message id are plain names where they are written (``model.stated_provenance``),
    and this is a headline a line break would let a name pass for.
    """
    return f"{_one_line(provenance.said())} (as its declarer stated it)"


def _stranded(row: BoardTask, root: bool) -> str:
    """
    What a row whose holder is gone says about it, after the holder's name.

    **Only the session's lead is told a release will work, and only where it
    will.** The store takes a release that is not the holder's from the root of
    the task's session when the holder is an agent of an earlier run of it
    (``store.tasks._may_reclaim``), and from nobody else. Told to a worker, or about a
    holder that merely finished its turn, the sentence sends an agent to make a
    call that is refused. Which holder it is comes from the row
    (``BoardTask.holder_left_behind``) and never from the holder's name, which
    an agent's address can be made to resemble. ``row`` is as
    ``_delimited_task`` leaves it.

    **Each of the other two readers is told who can move it**, since "stranded"
    alone leaves an agent with a row it can do nothing about and no name to take
    it to: a worker is sent to the lead for the release only the lead can make,
    and every reader of a row no agent's call can move is told the operator can
    close it (``_OPERATOR_CLOSES``) and what that leaves (``_CLOSED_IS_NOT_DONE``).
    """
    if root and row.holder_left_behind:
        return (
            f"its holder is not running and will not come back to it -- "
            f"release_task(task_id={_argument(row.id)}) puts it back for another agent "
            f"to claim, which only you, this session's lead, can do"
        )
    if row.holder_left_behind:
        return (
            "its holder is not running and will not come back to it -- only this "
            "session's lead can release it, so say so in a concern to lead that "
            "names this task"
        )
    return (
        f"its owner has stopped -- this task is stranded, and {_OPERATOR_CLOSES}, "
        f"{_CLOSED_IS_NOT_DONE}"
    )


# What an agent is told wherever a task cannot be moved by any call an agent can
# make: a claim whose holder has stopped and is nobody's to release, a task only
# its amender would work, a dependency on an id this board can never have. One
# spelling, so the remedy reads the same wherever an agent meets it.
#
# True of every unfinished task, whoever holds it: the pane offers the close on
# each row that is not finished and asks the operator for a reason
# (``ui.board_pane.closable``).
_OPERATOR_CLOSES = "the operator can close it from the board pane"


# What follows ``_OPERATOR_CLOSES`` wherever the task named is one whose work may
# still be wanted. A close is not a release: the row reads completed, reports no
# work, keeps its id, and lets go of every task that waited on it
# (``intents.TaskClosed``). An agent told only that the operator can close a
# stranded task would wait for it to come back.
_CLOSED_IS_NOT_DONE = (
    "which ends it unfinished -- it is not put back, and work still wanted is "
    "declared again under a new id"
)


# What an agent is told when it tries to finish or give back a task the operator
# closed (``TaskRefusal.CLOSED_BY_OPERATOR``), and under the operator's notice in
# the inbox of the agent that held it (``model.closing_notice``). One text, so
# the two places an agent learns of a close cannot describe it differently.
#
# It does not say the agent was stopped, because nothing stops it: the close
# takes the row away and lets go of what waited on it, the files it named
# included, while its holder may still be writing them.
_CLOSED_UNDER_YOU = (
    "The operator closed this task. It is finished as closed, not as done, and it "
    "is nobody's: complete_task and release_task on it are refused, and nothing "
    "sent with either is recorded -- a result included. What waited on it has been "
    "released, the files it named with it, so another agent may be writing them "
    "now: stop work on this task. If what you did, or left half-done in those "
    "files, matters to whoever is next, post it to the lead as a concern. read_task "
    "with this id shows the operator's reason."
)


# What an agent is told when it tries to finish or give back a task that was
# withdrawn (``TaskRefusal.WITHDRAWN``), and in the notice its withdrawal sends
# the holder. The reason follows it, introduced with whose words it is, where the
# row can be read back (``withdrawn_because``). Not ``_CLOSED_UNDER_YOU``: the
# operator did not stop this, and an agent told so would take it up with the
# wrong party. It does not say who withdrew it, which is the declarer or the
# session's lead standing in for it, and is said where the reason is.
_WITHDRAWN_UNDER_YOU = (
    "This task was withdrawn: the work is no longer wanted. It is finished "
    "as withdrawn, not as done, and it is nobody's: complete_task and release_task "
    "on it are refused, and nothing sent with either is recorded -- a result "
    "included. What waited on it has been released, the files it named with it, so "
    "another agent may be writing them now: stop work on this task. If what you "
    "did, or left half-done in those files, matters to whoever is next, post it to "
    "the lead as a concern."
)


# A dependency on an id nothing in this window has, on a row. Capitals because
# it is a blocker that never clears by waiting. An id another session's task has
# is not said this way: a reader told "never declared" goes on to declare it
# and is refused (``board.rows.foreign_wait_label`` is what such a row says).
_UNDECLARED = "NEVER DECLARED ON THIS BOARD"


# What a dependency on such an id needs, said where one task is read whole.
# Definite, because the row is: no session's task has any id it is said of.
_UNDECLARED_ACCOUNT = (
    "No task of any session in this window has an id above. This task waits on "
    "each of them until a task with that id is declared on this board and "
    "finished."
)


# What an agent is told about an id its board does not list, by every call that
# looks one up. One account for the two things it can be, because none of those
# calls can tell them apart: nothing has the id, or a task of another session in
# this window does -- which is on no board this agent can read and holds the id
# against a declaration here.
_NOT_ON_BOARD = (
    "read_board lists every id that is. A task another session in this window "
    "declared is not on your board and cannot be read, claimed or changed from "
    "here, and its id cannot be declared here either."
)


def _undeclared(row: BoardTask) -> tuple[TaskId, ...]:
    """
    The dependencies of ``row`` that nothing in the window has an id for:
    ``missing`` without the ones another session's task holds.
    """
    return tuple(d for d in row.missing if d not in row.held_elsewhere)


def _closing_reason(result: str) -> str:
    """
    The operator's reason for closing a task, as typed, out of the result the
    store composed around it -- or of that result's first line, which is what a
    digest carries. Empty when the operator gave none.

    The inverse of ``model.closing_result`` and written against it rather than
    against its words. **Only for a result known to be a close's**
    (``BoardTask.closed_by_operator``, ``ChangeKind.CLOSED``): a claimer can
    open a result of its own with the same words, and this would then hand part
    of an agent's text to the operator's mark.
    """
    # What is left after the label is nothing, or ": " and the reason, or -- on
    # a first line that was trimmed, of a reason that began with a line break --
    # the colon alone.
    return result[len(closing_result("")) :].removeprefix(":").removeprefix(" ")


def _withdrawal_reason(row: BoardTask) -> str:
    """
    The reason a task was withdrawn with, out of the result the store composed
    around it (``model.withdrawal_reason``). Only for a row known to be
    withdrawn, for ``_closing_reason``'s reason.
    """
    return withdrawal_reason(row.result, row.withdrawn_by_lead, row.withdrawal_edited)


# What follows whose reason a withdrawal's is, where the operator rewrote it
# (``model.REASON_EDITED`` on a label, which has no "reason" before it).
_AS_EDITED = "as edited by the operator"


def _withdrawal_heading(by_lead: bool, edited: bool) -> str:
    """
    Whose words a withdrawal's reason is, as the line that introduces it.

    The reason stands behind the mark that quotes an agent, so this line is
    what says which agent, and that the words are no longer that agent's where
    the operator rewrote them: the session's lead, standing in for a declarer
    that had stopped (``model.Task.withdrawn_by_lead``), is not the declarer.
    """
    if by_lead:
        said = "its session lead's reason for withdrawing it, its declarer having stopped"
    else:
        said = "its declarer's reason for withdrawing it"
    return f"{said}, {_AS_EDITED}:" if edited else f"{said}:"


def _account(row: BoardTask) -> str:
    """
    The text a finished row lays out where a result goes: the claimer's result,
    the operator's reason on a row the operator closed, or the declarer's on a
    row its declarer withdrew.
    """
    if row.closed_by_operator:
        return _closing_reason(row.result)
    if row.withdrawn:
        return _withdrawal_reason(row)
    return row.result


def _account_mark(row: BoardTask) -> str:
    """Whose mark ``_account``'s lines stand behind: the operator's, or an agent's."""
    return _ANSWER if row.closed_by_operator else _QUOTE


def _legend(closed: bool) -> str:
    """
    The legend of a reply that quotes agents, with the operator's mark explained
    beside it when the reply shows a reason the operator typed.
    """
    return f"{_QUOTE_LEGEND} {_CLOSING_LEGEND}" if closed else _QUOTE_LEGEND


def _agent_notes(concerns: Sequence[BoardConcern]) -> dict[ConcernId, BoardConcern]:
    """
    The concerns a board read lays out as notes on its rows, by id: every one
    but the operator's notice to the holder of a task it closed
    (``model.closing_notice``).

    That one is not a note an agent made, and a row lays each note out as an
    agent's, behind the mark that says an agent is being quoted. Nothing is
    lost with it: its body is the reason, which the closed row it names shows
    behind the operator's mark, and its recipient reads it in its inbox.

    Nor the notice to the holder of a withdrawn task
    (``model.withdrawal_notice``), for the same reason: its subject is
    pptmstr's sentence under the withdrawer's name, and its body is the reason
    the withdrawn row already shows with whose words it is.
    """
    return {
        c.id: c for c in concerns if not is_closing_notice(c.id) and not is_withdrawal_notice(c.id)
    }


def _result_heading(row: BoardTask) -> str:
    """
    What a result is introduced as: whose account it is.

    On a row the operator closed there is no claimer and no account of work, so
    the heading introduces the one thing under it, the operator's reason. That
    the row was closed, and what that means, is said above it wherever this is
    laid out (``board.rows.closed_label``).

    Attributed because the reader has to weigh it, and "held by" earlier in the row
    says who holds the task rather than who is speaking in this paragraph. The
    claimer's basis rides in the heading for the reason the specification's rides
    on the headline: it outlives a cut that takes the whole body.

    Where the claimer stands on what it did not run rides here too, when that is
    one of the two short answers -- it did not say, or it says nothing was left.
    A list of what was not run has ``_not_run_heading`` instead.
    """
    if row.closed_by_operator:
        return "the operator's reason for closing it:"
    if row.withdrawn:
        # An agent's words, behind the mark that quotes an agent: the
        # withdrawal is an agent's act, unlike a close.
        return _withdrawal_heading(row.withdrawn_by_lead, row.withdrawal_edited)
    said = basis_label(row.result_basis)
    if not row.not_run:
        said += f"; {not_run_label(row.not_run)}"
    return f"result, as {row.owner} reported it ({said}):" if row.owner else f"result ({said}):"


def _not_run_heading(row: BoardTask) -> str:
    """
    What a claimer's list of what it did not run is introduced as.

    Attributed like the result it sits beside, and for the same reason: it is the
    claimer's account, unreviewed, and may be shorter than the truth.
    """
    return f"not run, as {row.owner} states it:" if row.owner else "not run:"


def _rest(dropped: int, task_id: TaskId, part: str = "") -> str:
    """
    What stands in for text a read cut, and where the whole of it is.

    Names the call rather than a participant. "Ask the lead for the rest" made the
    lead a relay for text the board already held, and the lead's copy was its own
    transcript -- the thing a board exists to not depend on.

    ``part`` is for the one read that cuts with no wider read behind it:
    ``read_task`` on a task too large for a reply names the argument that
    returns the one text (``_part_text``). A board read and a digest name the
    task alone, and ``read_task`` is then where a text still too long to ride
    with the others says which part it is.
    """
    which = f', part="{part}"' if part else ""
    return (
        f"... {dropped} more character(s) -- "
        f"read_task(task_id={_argument(task_id)}{which}) returns it whole"
    )


def _reasons_text(
    row: BoardTask, by_id: Mapping[ConcernId, BoardConcern], budget: int | None = None
) -> str:
    """
    What other agents have recorded about this task, in their own words.

    **The count is not enough, which is the whole of this.** A row saying "1 open
    concern" tells a reader that a conclusion exists and not what it was, so the
    reader has to go and derive it again -- which is the relitigating the board is
    meant to end. The subject alone has the same defect one level up: "checksum
    ignored in sixty places" names a finding without giving the reader anything to
    evaluate.

    **Only concerns that named a task are broadcast.** A concern with no
    ``task_id`` is a message between two agents -- a question to the lead, an
    answer, a heads-up -- and putting it on everyone's board would turn a
    point-to-point channel into a public one. ``Concern.task_id`` is what makes
    that line drawable: a finding *about shared work* is shared state, and the
    rest is mail.

    What is rendered is the text as *delivered* -- the operator's edit included,
    which is the version the recipient acted on -- and a note the operator rewrote
    says so beside its sender's basis, since that basis was given for the words the
    sender wrote. Whether an operator saw a body first depends on the policy:
    ``post_concern`` is parked wherever it is reviewed and is not under the rung
    that releases it (``approval._AUTONOMOUS_AUTO``). So a body is quoted line by
    line like every other text an agent wrote (``_quoted``).

    **``budget`` bounds the bodies and never the attributions.** Under a whole-board
    ceiling the bodies are the first thing a long board can afford to lose, and the
    sender and subject are the last: they are what tells a reader that a finding
    exists and who to ask for it, which is the count-versus-subject distinction
    above taken one step further rather than abandoned. A note whose body did not
    fit still appears, and still says how much of it is missing.
    """
    shown = [_delimited_note(by_id[cid]) for cid in row.concerns if cid in by_id]
    if not shown:
        return ""
    spare = budget
    out = [f"\n    -- {len(shown)} agent note(s) on this task:"]
    for concern in shown:
        body, dropped = _quoted(concern.body, _MAX_CONCERN_CHARS, _NOTE_LEAD, spare)
        said = f"{basis_label(concern.basis)}{_edited_note(concern.edited)}{_unread_note(concern)}"
        out.append(
            f"\n       [{concern.sender}, {said}] "
            f"{incident_marker(concern.incident)}{concern.subject or '(no subject)'}"
        )
        out.append(_laid(body))
        if dropped:
            out.append(f"\n       {_rest(dropped, row.id)}")
        if spare is not None:
            spare -= _cost(body)
    return "".join(out)


def _edited_note(edited: bool) -> str:
    """
    What follows a sender's basis when the operator rewrote the body under it.

    Beside the basis on every read that shows one, because the basis is the
    sender's statement about the text it wrote and the two are only readable
    together (``model.Concern.basis``).
    """
    return ", edited by the operator before delivery" if edited else ""


def _unread_note(note: BoardConcern) -> str:
    """
    What follows a note's attribution when it is waiting for an agent that is not
    running (``BoardConcern.undeliverable``): nothing in this process reads that
    inbox, so a reader that takes the note for delivered, or for about to be, is
    wrong about who knows what it says.
    """
    return f", {UNDELIVERABLE}" if note.undeliverable else ""


# What a quoted line is laid out on under a row and under a note. Named because
# they are charged against a row's budget rather than only written (``_quoted``).
_DETAIL_LEAD = "    "


_NOTE_LEAD = "       "


# The indent a row's own one-line property is laid out on.
_DETAIL_INDENT = "\n    "


def _delimited_task(row: BoardTask) -> BoardTask:
    """
    A row whose one-line fields can each be told from the line they are laid out
    on: ids and paths as ``_named`` leaves them, the title and the property as
    ``between_quotes`` does. A property nobody stated stays empty, which is how every
    reader of the field tells there is none.

    The owner is only made one line. A role name is the address the bus routes
    to and the board's own words for a holder it cannot name (``addresses.UNKNOWN``,
    ``addresses.FOREIGN``); between quotes those would read as something an agent
    wrote.
    """

    def each(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_named(value) for value in values)

    return dataclasses.replace(
        row,
        id=_named(row.id),
        title=between_quotes(row.title),
        owner=None if row.owner is None else _one_line(row.owner),
        blocked_on=each(row.blocked_on),
        missing=each(row.missing),
        held_elsewhere=each(row.held_elsewhere),
        touches=each(row.touches),
        depends_on=each(row.depends_on),
        writers=each(row.writers),
        property=between_quotes(row.property) if row.property else "",
    )


def _waiting_on(row: BoardTask) -> tuple[str, ...]:
    """
    What a row lists as "waiting on": ``blocked_on`` without the writers
    ``all_writers_label`` names, so a gate's row lists each writer once. Those
    lines are never cut, and a board of N writers would otherwise carry 2N ids
    on every gate.
    """
    if not row.after_all_writers:
        return row.blocked_on
    return tuple(t for t in row.blocked_on if t not in row.writers)


def _delimited_note(note: BoardConcern) -> BoardConcern:
    """
    A note whose one-line fields can each be told from the line they are laid out
    on (``_delimited_task``). An absent subject stays empty for the same reason
    an absent property does.
    """
    return dataclasses.replace(
        note,
        sender=_one_line(note.sender),
        recipient=_one_line(note.recipient),
        subject=between_quotes(note.subject) if note.subject else "",
        task_id=None if note.task_id is None else _named(note.task_id),
    )


def _laid(lines: Sequence[str]) -> str:
    """``lines`` as they continue a row: each on a line of its own."""
    return "".join(f"\n{line}" for line in lines)


def _board_text(
    tasks: Sequence[BoardTask],
    by_id: Mapping[ConcernId, BoardConcern],
    root: bool = False,
    finished: int | None = None,
) -> str:
    """
    The whole board reply, bounded.

    **Rows are never dropped.** An agent reads this to find out what exists, what is
    held and what is blocked, and it has to know about the task it is *not* going to
    claim -- that is how it stays off another agent's files. A listing short of a row
    is wrong in a way a listing short of a paragraph is not, and it is wrong
    invisibly, which is the shape every other bound here avoids.

    So the degradation is in the prose. Each row is measured with no prose at all;
    what the ceiling leaves over that floor is divided equally between the rows, and
    each spends its share on its result if it has one, then its specification, then
    its note bodies.
    Equally rather than by need: a proportional split would spend the board's budget
    on whichever tasks were declared with the longest descriptions, and length is
    not importance.

    Under the ceiling nothing is shared out: a board that fits is rendered with the
    per-row bounds alone, which keeps every board small enough to fit out of the
    business of being bounded.

    **The structural floor wins if even it does not fit.** Then the reply is the
    floor plus the notice explaining it, over the ceiling, rather than rows dropped
    to get under it. A board of that many tasks has a problem this function cannot
    solve, and hiding rows from the agent that has to work around them is not a fix.

    So the bound this holds is ``max(_MAX_BOARD_CHARS, floor + notice)``, and it is
    the ceiling itself for every board whose structure fits inside it.

    ``finished`` is how many finished tasks an open-only read left out, or None
    for a read of every task; a listing short of rows says so in its first line.
    """
    closed = any(row.closed_by_operator and _account(row) for row in tasks)
    if finished is None:
        counted = f"{len(tasks)} task(s) on your board."
    else:
        counted = (
            f"{len(tasks)} open task(s) on your board; {finished} finished one(s) -- "
            f"completed, closed or withdrawn -- are not listed, and read_board() lists them."
        )
    preamble = f"{counted} {_legend(closed)}"
    full = "\n".join([preamble, *(_board_line(row, by_id, root=root) for row in tasks)])
    if len(full) <= _MAX_BOARD_CHARS:
        return full

    # Rendered a second time rather than estimated. The floor is what the rows cost
    # with every paragraph removed, and measuring it is the only way to divide what
    # is left without the arithmetic and the renderer disagreeing.
    floor = "\n".join([preamble, *(_board_line(row, by_id, budget=0, root=root) for row in tasks)])
    notice = (
        "\n\nThis board did not fit, so the specification, result and notes of each "
        "task below share {share} characters. Nothing is hidden: every task is listed "
        "and every note names its sender and subject. read_task(task_id) returns any "
        "one task: its specification whole, then its result and every note on it, and "
        "for a task too large for one reply, how to read each of those whole."
    )
    spare = _MAX_BOARD_CHARS - len(floor) - len(notice)
    share = max(0, spare // len(tasks))
    return "\n".join(
        [
            preamble + notice.format(share=share),
            *(_board_line(row, by_id, share, root) for row in tasks),
        ]
    )


# What ``read_task`` may add under a task: the sentence saying whether the holder
# is now recorded as shown the version above. Kept out of the task's own
# allowance so the two together are inside ``_MAX_BOARD_CHARS``, which is the
# only size a reply is recorded as a showing at.
_SHOWN_NOTE_ROOM = 500


# The names ``read_task``'s ``part`` takes for the two texts a task has one of.
# A note is ``note:<its id>`` and an earlier specification ``version:<number>``.
_PART_RESULT = "result"


_PART_NOT_RUN = "not_run"


def _task_text(
    row: BoardTask,
    by_id: Mapping[ConcernId, BoardConcern],
    root: bool = False,
    ceiling: int | None = None,
) -> str:
    """
    One task: its current specification whole, and everything else any agent
    wrote onto its row -- whole when the reply has room, and otherwise cut with
    the call that returns each text whole.

    The counterpart to ``_board_line`` and deliberately not a mode of it. A board
    read is bounded because it is every row at once; this is one row, asked for by
    name, and every bound ``_board_line`` applies ends by pointing here. Text on
    the board that no agent on the board can reach is the one property the pair
    exists to rule out, and it still is: what this cuts is behind ``part``
    (``_part_text``), one text to a reply.

    **``ceiling`` is what the reply may come to, or None for no bound.** It is
    there because this is also the read that records a holder as shown a
    replaced specification (``read_task``), and a reply is evidence of a
    showing only at a size an agent can read: a task on its third
    20,000-character specification, each earlier one whole, is over 60,000
    characters, past both replies ``_MAX_BOARD_CHARS`` was set under.

    **The current specification is never what gives way.** It is laid out first
    of the texts and whole at any ceiling, since it is what the holder is held
    to and what the record says it was shown. The result, the list of what was
    not run, the note bodies and the earlier specifications share equally what
    the ceiling leaves over everything else -- ``_board_text``'s rule and for
    its reason, that length is not importance -- and each one cut ends in
    ``_rest``'s line naming its part.

    **Structure is never dropped, so the ceiling can be exceeded.** The headline,
    the dependencies and files, each note's attribution and each earlier
    version's heading are laid out whatever they cost, and so is the
    specification. A caller that needs the bound kept compares the length it
    was given with the ceiling it asked for; ``read_task`` does, and records no
    showing when the reply is over.

    Under the ceiling nothing is shared out, which keeps every task small enough
    to fit out of the business of being bounded: the reply is every character.

    Built from the same ``BoardTask`` and ``BoardConcern`` rows as the board read,
    so it is scoped as that is scoped and names agents as that names them. The
    notes are the ones the row lists -- concerns that named this task and were not
    withdrawn -- and each says who it was to as well as who it was from, because a
    reader of one task is reconstructing a conversation rather than scanning for
    one.

    Says what is absent rather than leaving a gap. A reader that asked for a whole
    task and got no result section cannot tell "none was given" from "this tool
    does not show them".

    Each text is quoted line by line (``_quoted``): this is the read a worker
    builds from, so it is the one where a forged heading would cost most.
    """
    whole = _task_lines(row, by_id, root, None)
    if ceiling is None or _cost(whole) - 1 <= ceiling:
        return "\n".join(whole)
    # Rendered again rather than estimated, as ``_board_text`` does: the floor
    # is what the task costs with every text but the specification removed.
    floor = _cost(_task_lines(row, by_id, root, 0)) - 1
    pieces = sum(1 for _ in _cuttable(row, by_id))
    # The notice names the share, and was measured naming a share of one digit.
    spare = ceiling - floor - len(str(ceiling))
    share = max(0, spare // max(1, pieces))
    return "\n".join(_task_lines(row, by_id, root, share))


def _cuttable(row: BoardTask, by_id: Mapping[ConcernId, BoardConcern]) -> Iterator[str]:
    """
    Every text of a task that ``_task_text`` may cut, each once and none empty:
    all of them but the current specification.
    """
    yield from (text for text in (_account(row), row.not_run or "") if text)
    yield from (by_id[cid].body for cid in row.concerns if cid in by_id and by_id[cid].body)
    yield from (prior.detail for prior in row.superseded if prior.detail)


def _task_lines(
    row: BoardTask, by_id: Mapping[ConcernId, BoardConcern], root: bool, share: int | None
) -> list[str]:
    """
    ``_task_text``'s lines, with each cuttable text given ``share`` characters
    laid out, or None for all of it. The notice saying texts were cut is one of
    the lines whenever ``share`` is a number, so the floor measured at zero has
    already paid for it.
    """
    task_id = _named(row.id)
    row = _delimited_task(row)

    def piece(text: str, part: str, mark: str = _QUOTE) -> list[str]:
        lines, dropped = _quoted(text, len(text), "", share, mark)
        return [*lines, _rest(dropped, task_id, part)] if dropped else lines

    lines = [f"Task {row.id} [{row.state.value}] {row.title}"]
    stated = property_label(row.property)
    lines.append(stated[:1].upper() + stated[1:] if stated else "Must not break: (none was stated)")
    if row.unworked:
        # In place of a holder: the row has none, and "held by nobody" under
        # "completed" would leave a reader looking for who finished it.
        ended = ended_label(row)
        lines.append(ended[:1].upper() + ended[1:])
    elif row.owner is None:
        lines.append("held by nobody")
    elif row.owner_gone:
        lines.append(f"held by {row.owner}, but {_stranded(row, root)}")
    else:
        lines.append(f"held by {row.owner}")
    if row.depends_on:
        lines.append(f"depends on: {', '.join(row.depends_on)}")
    if _waiting_on(row):
        lines.append(f"still waiting on: {', '.join(_waiting_on(row))}")
    if row.after_all_writers:
        waits = all_writers_label(row)
        lines.append(f"{waits[:1].upper()}{waits[1:]}.")
    if _undeclared(row):
        lines += [f"{_UNDECLARED}: {', '.join(_undeclared(row))}", _UNDECLARED_ACCOUNT]
    if row.held_elsewhere:
        waits = foreign_wait_label(row)
        lines.append(f"{waits[:1].upper()}{waits[1:]}.")
    if row.touches:
        lines.append(f"writes: {', '.join(row.touches)}")
    if row.provenance is not None:
        said = _provenance_said(row.provenance)
        lines.append(said[:1].upper() + said[1:])
    for fact in (version_label(row), stale_label(row)):
        if fact:
            lines.append(fact[:1].upper() + fact[1:])
    account = _account(row)
    lines.append(_legend(row.closed_by_operator and bool(account)))
    if share is not None:
        lines.append(
            f"This task did not fit one reply. Its specification is whole below; its "
            f"result, list of what was not run, note bodies and earlier "
            f"specifications are each cut to {share} characters, and each one cut "
            f"ends in a line giving how much was left out and the read_task call "
            f"that returns it whole. Nothing is hidden: every note and every earlier "
            f"version is listed."
        )

    lines += ["", *_specification_lines(row)]

    if account:
        heading = _result_heading(row)
        lines += [
            "",
            heading[:1].upper() + heading[1:],
            *piece(account, _PART_RESULT, _account_mark(row)),
        ]
    elif row.closed_by_operator:
        lines += ["", "The operator's reason for closing it:", "(none was given)"]
    elif row.state is TaskState.COMPLETED and row.not_run is None:
        lines += ["", "Result:", f"(completed without one; {not_run_label(None)})"]
    elif row.state is TaskState.COMPLETED:
        lines += ["", "Result:", "(completed without one)"]
    else:
        lines += ["", "Result:", "(none yet -- the task is not complete)"]
    if row.not_run:
        heading = _not_run_heading(row)
        lines += ["", heading[:1].upper() + heading[1:], *piece(row.not_run, _PART_NOT_RUN)]

    notes = [(cid, _delimited_note(by_id[cid])) for cid in row.concerns if cid in by_id]
    lines += ["", f"{len(notes)} agent note(s) on this task" + (":" if notes else ".")]
    for cid, note in notes:
        lines += ["", _note_heading(note), *piece(note.body, f"note:{_one_line(cid)}")]

    if row.superseded:
        # Last, so a reader of the task as it stands is not made to read past what
        # it used to say: these are the texts a finished result may have been
        # written against.
        lines += ["", f"{len(row.superseded)} earlier specification(s), oldest first:"]
        for number, prior in enumerate(row.superseded, start=1):
            lines += [
                "",
                *_version_heading(number, prior),
                *(
                    piece(prior.detail, f"version:{number}")
                    if prior.detail
                    else ["(no specification text)"]
                ),
            ]
    return lines


def _note_heading(note: BoardConcern) -> str:
    """The line over a note's body where one task is read. ``note`` is delimited."""
    return (
        f"[from {note.sender} to {note.recipient}, "
        f"{basis_label(note.basis)}{_edited_note(note.edited)}{_unread_note(note)}] "
        f"{incident_marker(note.incident)}{note.subject or '(no subject)'}"
    )


def _version_heading(number: int, prior: BoardVersion) -> list[str]:
    """The two lines over an earlier specification: which it was, and its property."""
    held = property_label(between_quotes(prior.property)) if prior.property else ""
    return [
        f"Version {number} ({basis_label(prior.basis)}), replaced by "
        f"{_one_line(prior.replaced_by)}:",
        held[:1].upper() + held[1:] if held else "Must not break: (none was stated)",
    ]


def _part_text(row: BoardTask, by_id: Mapping[ConcernId, BoardConcern], part: str) -> str:
    """
    One text of a task, whole and alone: what ``read_task`` returns for ``part``.

    Where the rest of a text ``_task_text`` cut is. Every text an agent can put
    on a task is accepted under ``bus.MAX_TEXT_CHARS``, which is under the reply
    ceiling, so each fits a reply of its own; only the operator's hand can write
    a longer one, and it is returned whole all the same, since a bound here
    would be the unreachable text this exists to rule out.

    A part the task does not have is answered with the parts it does: a note's
    id and a version's number are things a caller copies from the cut line, and
    one mistyped should not cost a second guess.
    """
    task_id = _named(row.id)
    shown = _delimited_task(row)

    def alone(heading: str, text: str, mark: str = _QUOTE) -> str:
        body = _quoted(text, len(text), "", None, mark)[0] if text else ["(it is empty)"]
        legend = _CLOSING_LEGEND if mark == _ANSWER else _QUOTE_LEGEND
        return "\n".join([f"Task {task_id}: {heading}", legend, *body])

    notes = {cid: by_id[cid] for cid in row.concerns if cid in by_id}
    kind, _, which = part.partition(":")
    account = _account(row)
    if part == _PART_RESULT and account:
        return alone(_result_heading(shown), account, _account_mark(row))
    if part == _PART_NOT_RUN and row.not_run:
        return alone(_not_run_heading(shown), row.not_run)
    if kind == "note" and which in notes:
        return alone(_note_heading(_delimited_note(notes[which])), notes[which].body)
    if kind == "version" and which.isdecimal() and 1 <= int(which) <= len(row.superseded):
        prior = row.superseded[int(which) - 1]
        heading, held = _version_heading(int(which), prior)
        return alone(f"{heading[:-1]}. {held}:", prior.detail)
    has = [
        *([_PART_RESULT] if account else []),
        *([_PART_NOT_RUN] if row.not_run else []),
        *(f"note:{_one_line(cid)}" for cid in notes),
        *(f"version:{number}" for number in range(1, len(row.superseded) + 1)),
    ]
    listed = ", ".join(_named(name) for name in has) if has else "none"
    return (
        f"Task {task_id} has no part {between_quotes(part[:MAX_ID_CHARS])}. The parts it has "
        f"are: {listed}. Leave part out for the task itself, which always carries "
        f"its current specification whole."
    )


def _specification_lines(row: BoardTask) -> list[str]:
    """
    A task's current specification, whole and quoted, under a heading that says
    which version it is once there has been more than one.

    ``row`` is as ``_delimited_task`` leaves it.
    """
    which = f", version {row.version}" if row.version > 1 else ""
    if not row.detail:
        return [f"Specification{which}:", "(none was given)"]
    return [
        f"Specification{which} ({basis_label(row.basis)}):",
        *_quoted(row.detail, len(row.detail), "")[0],
    ]


def _unshown(row: BoardTask, me: NodeId) -> bool:
    """
    Whether ``me`` holds ``row`` open against a specification it was never handed.

    A claim that recorded no version counts as handed the first: while a task has
    had one text there is only one it can have been.
    """
    return (
        row.state is TaskState.CLAIMED
        and row.holder == me
        and row.version > (row.claimed_version or 1)
    )


def _behind(row: BoardTask, me: NodeId) -> bool:
    """
    Whether ``me`` finished ``row`` against a specification older than the one
    the board holds now. ``_unshown`` for a row that is over.
    """
    return (
        row.state is TaskState.COMPLETED
        and row.holder == me
        and row.version > (row.claimed_version or 1)
    )


def _landed_behind_text(row: BoardTask, root: bool, reachable: bool) -> str:
    """
    What a holder is told when its result stands against a specification older
    than the one the board holds: which version its result is recorded against,
    which the board holds, the current text, and what to do if the work does not
    meet it.

    **It does not say when the text was replaced.** A finished task can be
    amended too, and the row read back here does not carry when the completion
    landed, so "while you held it" would be said of an amendment made after the
    holder had finished. "Since you were handed it" is true of both.

    ``reachable`` is whether the name of whoever replaced the text is an address
    a concern can be sent to, which is the session's to answer
    (``AgentSession.resolve_role``). The operator is not one, and neither is an
    amender of an earlier run; read off the name instead, either could be
    imitated by an agent's address.

    **The completion is not turned away for this.** A refusal here would drop the
    result the holder sent and leave the claim with an agent that may never call
    again; and a refusal that showed the new text and then counted the holder as
    having read it would record a reading nobody can vouch for, since a reply of
    this size is one an agent has been measured unable to read
    (``_MAX_BOARD_CHARS``). So the result is stored against the version the holder
    was handed, which is what it is an account of, and the board says so on the
    row (``board.rows.stale_label``).

    The current text is bounded as a board row's is and names ``read_task`` for
    the rest: this reply is a notice, and the read is where the text is whole.
    """
    row = _delimited_task(row)
    had = (
        "a version the claim did not record"
        if row.claimed_version is None
        else f"version {row.claimed_version}"
    )
    by = _one_line(row.superseded[-1].replaced_by) if row.superseded else ""
    if by and reachable:
        whom = f"say so in a concern to {by} that names this task"
    elif root:
        whom = "say so to the operator -- raise_decision puts it in front of them"
    else:
        whom = "say so in a concern to lead that names this task"
    stated = property_label(row.property)
    body, dropped = _quoted(row.detail, _MAX_DETAIL_CHARS, "")
    return "\n".join(
        [
            f"Its specification has been replaced since you were handed it, and "
            f"nothing told you. "
            f"Your result is recorded as written against {had}, which is the one you "
            f"were handed, and the board holds version {row.version}"
            f"{f', replaced by {by}' if by else ''}. The board shows that beside your "
            f"result. Below is version {row.version}: if your work does not meet it, "
            f"{whom}. A finished task cannot be completed a second time.",
            _QUOTE_LEGEND,
            "",
            stated[:1].upper() + stated[1:] if stated else "Must not break: (none is stated)",
            (
                f"Specification, version {row.version} ({basis_label(row.basis)}):"
                if row.detail
                else f"Specification, version {row.version}: (none was given)"
            ),
            *body,
            *([_rest(dropped, row.id)] if dropped else []),
        ]
    )


def _amended_text(
    task_id: TaskId, row: BoardTask | None, at: float, basis_sent: bool = False, unkept: str = ""
) -> str:
    """
    What an amender is told once the board has taken its amendment: the version it
    made and which of the text and the property that version replaced, or that it
    made none, and who is still working to an earlier one.

    ``at`` is the stamp the amendment carried, which is how the version it made is
    found among the task's history: the reply to a write says only that it landed.
    Which half changed is read off that history too, and not off what was sent --
    a text sent back as it stands replaced nothing.

    ``basis_sent`` is whether the amender stated a basis, and ``unkept`` is the
    sentence for one the operator's edit discarded. Both are about a basis
    beside a *replaced* text, so neither is said of a text that stands except to
    say the basis went nowhere: the store keeps the author's
    (``store._apply``'s ``TaskAmended`` arm).
    """
    named = _named(task_id)
    if row is None:
        return (
            f"Task {named}'s specification was replaced, but the board could not be "
            f"read back to say which version that made. read_task shows it." + unkept
        )
    made = next((n for n, prior in enumerate(row.superseded) if prior.replaced_at == at), None)
    if made is None:
        return (
            f"Task {named} is unchanged: what you sent is the specification and the "
            f"property it already has, so no new version was made. It is at version "
            f"{row.version}."
        )
    before = row.superseded[made]
    after = row.superseded[made + 1] if made + 1 < len(row.superseded) else row
    reworded = after.detail != before.detail
    held_to = (
        ""
        if after.property == before.property
        else "withdrawn" if not after.property else "replaced"
    )
    if reworded and held_to:
        which = f"Its text was replaced and its property was {held_to}."
    elif reworded:
        which = "Its text was replaced and its property stands."
    else:
        which = (
            f"Its property was {held_to} and its text stands, under the basis its "
            f"author gave it."
        )
        if basis_sent:
            which += (
                " The basis you sent was not kept: a basis is a statement about a "
                "text, and this amendment replaced none."
            )
    said = [f"Task {named}'s specification is now version {made + 2}.", which]
    if reworded and unkept:
        said.append(unkept.strip())
    if row.version > made + 2:
        said.append(f"It has been replaced again since and is at version {row.version}.")
    stale = stale_label(row)
    if stale and row.state is TaskState.CLAIMED:
        said.append(
            f"It is held by {_one_line(row.owner or '')}: {stale}. Nothing tells a holder "
            f"that its specification changed. It sees the new text when it next calls "
            f"read_task on this task; if it completes first, its result lands recorded "
            f"against the version it was handed and it is told then. Post it a concern "
            f"if it should stop and read it now."
        )
    elif stale:
        said.append(f"It is already finished: {stale}.")
    return " ".join(said)


# What ``model.stored_as_sent`` refuses, in the words of a refusal.
_NOT_IN_AN_ID = (
    "a NUL, a lone surrogate, or a backslash followed by u and 0000, 005c, or d800 "
    "to dfff in any case of hex"
)


def _escaped_dependency_text(entries: Sequence[int]) -> str:
    """
    Why a declaration naming, in ``depends_on``, an id no task can be declared
    under is refused (``TaskRefusal.ESCAPED_ID``).

    ``entries`` are the 1-based positions in ``depends_on`` as the caller sent
    it. They are named by position and not echoed, for the reason the id is not:
    what such an entry holds is what cannot be shown as sent.
    """
    which = (
        f"Entry {entries[0]} of its depends_on holds"
        if len(entries) == 1
        else f"Entries {', '.join(str(n) for n in entries)} of its depends_on hold"
    )
    return (
        f"{which} a character a task id may not: {_NOT_IN_AN_ID}. Such an id cannot be "
        "named in a declaration, and a task restored from an older board log under "
        "such an id cannot be depended on by id. Leave the entry out, or name the "
        "task it meant by its id, and declare it again."
    )


def _refusal_text(refusal: TaskRefusal) -> str:
    """
    What the agent is told, and what to do about it.

    The words live here rather than in the store because ``_apply`` is pure and
    prose is presentation. Each one names the recovery: a refusal that only says no
    sends a worker back to make the same mistake, which STYLE.md §3 records as
    having already happened once at this exact boundary.

    The match is exhaustive on purpose -- a new ``TaskRefusal`` member with no
    sentence is a mypy error at the final arm rather than a ``KeyError`` reaching an
    agent mid-run.
    """
    match refusal:
        case TaskRefusal.DUPLICATE_ID:
            return _duplicate_id_text(None)
        case TaskRefusal.WOULD_CYCLE:
            return (
                "Declaring it would close a dependency cycle, which leaves every task "
                "in the loop permanently unclaimable."
            )
        case TaskRefusal.NO_SUCH_TASK:
            return f"No task with that id is on your board. {_NOT_ON_BOARD}"
        case TaskRefusal.ALREADY_COMPLETE:
            return "It is already complete. Nothing changed, and nothing is left to do on it."
        case TaskRefusal.CLOSED_BY_OPERATOR:
            return _CLOSED_UNDER_YOU
        case TaskRefusal.NOT_CLAIMED:
            return "Nobody holds it. Claim it first, with claim_task and that id."
        case TaskRefusal.NOT_YOURS:
            return (
                "Another agent holds the claim, and only the claimer can change a task's "
                "state. Send them a concern rather than acting on it yourself."
            )
        case TaskRefusal.NOT_THE_DECLARER:
            return (
                "Only the agent that declared a task, or the operator, can replace its "
                "specification, and you did not declare this one. Send its declarer a "
                "concern saying what should change."
            )
        case TaskRefusal.AMENDER_HELD_IT:
            return (
                "You declared it, and you also hold it or have held it, so the "
                "specification is one you were bound by and is not yours to replace. "
                "Nothing changed. The operator can amend it; a retry will not."
            )
        case TaskRefusal.CLAIMER_AMENDED_IT:
            return (
                "You replaced this task's specification, so it is not yours to work to: "
                "the agent that sets a specification and the agent bound by it are "
                "different agents. Leave it for another agent to claim; if none "
                f"will, {_OPERATOR_CLOSES}, {_CLOSED_IS_NOT_DONE}."
            )
        case TaskRefusal.NOT_THE_WITHDRAWER:
            return (
                "Only the agent that declared a task can withdraw it, or the lead of "
                "its session once that agent has stopped, and you are neither for this "
                "one. Nothing changed. Send its declarer a concern saying why it is no "
                "longer wanted, or the lead if the declarer is still running."
            )
        case TaskRefusal.WITHDRAW_HELD_LIVE:
            return _held_live_text(None)
        case TaskRefusal.WITHDRAW_FINISHED:
            return (
                "It is already finished -- completed, closed by the operator or "
                "withdrawn -- so nothing waits on it and there is nothing to withdraw. "
                "Nothing changed. read_task with this id shows how it ended."
            )
        case TaskRefusal.NO_WITHDRAWAL_REASON:
            return (
                f"A withdrawal needs a reason of 1 to {MAX_WITHDRAWAL_REASON_CHARS} "
                "characters: it is shown in place of a result, and is what its holder "
                "reads. Nothing changed."
            )
        case TaskRefusal.WITHDRAWN:
            return _WITHDRAWN_UNDER_YOU
        case TaskRefusal.RESERVED_ID:
            return (
                f"The id {ALL_WRITERS!r} is reserved: in depends_on it means every task "
                "on this board that writes a file, so no task can be named by it. "
                "Declare it again under another id."
            )
        case TaskRefusal.ESCAPED_ID:
            return (
                f"Its id holds a character a task id may not: {_NOT_IN_AN_ID}. The "
                "board would list the task under another id than the one you sent, "
                "and no later call naming it would find it. Declare it again under "
                "another id."
            )
        case TaskRefusal.NOT_APPLIED:
            return "The board never saw the request -- the application is shutting down."
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(refusal)


def _duplicate_id_text(on_my_board: bool | None, held_off_fleet: str | None = None) -> str:
    """
    What a declarer is told when the id it chose is taken.

    **Two mistakes under one refusal, named apart when the caller knows which.**
    Task ids are one namespace for every session in the window
    (``store.tasks._declaration_refusal``), so the id may be a row of the declarer's
    own board or a task of another session that no read of this board shows.
    "Claim the task that is there" is the recovery for the first and a dead end
    for the second -- that claim is answered "not on your board" -- and a task
    waiting on the second can never be satisfied. ``on_my_board`` is whether the
    declarer's own board lists the id, or None when nothing read the board to
    find out, and then the sentence covers both.

    **A third, when the store names it** (``TaskWriteSettled.held_off_fleet``):
    the id is a task of a session the operator removed from the fleet. That
    board is on no card and no agent of it is running, so "another session in
    this window" would send the lead to an operator who can see no such
    session; and nothing frees the id but a restart, which is the one fact here
    the other two sentences do not have. The store's answer is about the apply
    and outranks ``on_my_board``, which a later read made.
    """
    if held_off_fleet is not None:
        return (
            f"That id belongs to a task of session {_named(held_off_fleet)}, which the "
            "operator removed from the fleet in this window. Ids are shared by every "
            "session pptmstr has run since it started, a removed session keeps its "
            "board, and so the id stays held until pptmstr is restarted. Use a "
            "different id: that task cannot be read, claimed or declared from here, "
            "and a task on your board that waits on it never becomes claimable -- "
            f"{_OPERATOR_CLOSES}, and the work is then declared again under a new id "
            "that does not wait on it."
        )
    if on_my_board:
        return (
            "That id is already on your board, and re-declaring it would overwrite "
            "work another agent may already be doing. Use a different id, or claim "
            "the task that is there."
        )
    elsewhere = (
        "it cannot be read, claimed or declared from here. Use a different id. A "
        "task on your board that waits on this one says so on its row and never "
        f"becomes claimable: {_OPERATOR_CLOSES}, and the work is then declared "
        "again under a new id that does not wait on it."
    )
    if on_my_board is None:
        return (
            "That id is already taken, and re-declaring it would overwrite work "
            "another agent may already be doing. If read_board lists it, use a "
            "different id or claim the task that is there. If it does not, a task "
            f"of another session in this window has the id, and {elsewhere}"
        )
    return (
        "That id belongs to a task of another session in this window. Ids are "
        "shared by every session pptmstr is running, that task is not on your "
        f"board, and {elsewhere}"
    )


def _held_text(lost: str, row: BoardTask | None, root: bool) -> str:
    """
    What an agent is told when the task it asked for by id is held.

    ``row`` is the task as the asker's board has it, or None when it could not
    be read back. A holder that is working is somebody to send a concern to. A
    holder that has stopped is not, and the sentence for it is the row's own
    (``_stranded``): sent to "send them a concern", a lead writes to an agent
    of an earlier run that nothing delivers to, about a claim that only
    the lead's own ``release_task`` gives back.
    """
    if row is None or not row.owner_gone:
        return (
            f"{lost} Another agent holds it. read_board says who; send them a "
            f"concern if it matters, and claim something else."
        )
    row = _delimited_task(row)
    return f"{lost} {row.owner} holds it, but {_stranded(row, root)}. Nothing changed."


def _claim_refusal_text(
    refusal: ClaimRefusal, wanted: TaskId | None, row: BoardTask | None = None, root: bool = False
) -> str:
    """
    Why a claim won nothing, and what to do about each reason.

    Apart because each has a different next step, and "was not claimed" covering
    all of them sends an agent to read the board to find out which mistake it
    made. Exhaustive for ``_refusal_text``'s reason.

    ``row`` and ``root`` are read for a held task only (``_held_text``).
    """
    named = _named(wanted or "")
    lost = f"Task {named} was NOT claimed."
    match refusal:
        case ClaimRefusal.NOTHING_CLAIMABLE:
            # An ordinary condition, not a failure. Said plainly so the agent
            # stops asking and does not retry into a loop.
            return (
                "Nothing claimable right now -- the board is empty, or everything "
                "left on it is blocked by unfinished dependencies, held by another "
                "agent, or a task you gave back or wrote the specification of. "
                "read_board shows which; a task you gave back can still be claimed "
                "by its id."
            )
        case ClaimRefusal.NOT_ON_YOUR_BOARD:
            return f"{lost} No task with that id is on your board. {_NOT_ON_BOARD}"
        case ClaimRefusal.HELD:
            return _held_text(lost, row, root)
        case ClaimRefusal.ALREADY_YOURS:
            return (
                f"{lost} You already hold it, and nothing changed. read_task returns "
                f"its specification; complete_task or release_task ends your claim."
            )
        case ClaimRefusal.COMPLETE:
            return f"{lost} It is already complete. read_task shows its result."
        case ClaimRefusal.CLOSED:
            return (
                f"{lost} The operator closed it: it is finished as closed, not as done, "
                f"and nobody did the work. read_task shows the operator's reason."
            )
        case ClaimRefusal.WITHDRAWN:
            return (
                f"{lost} It was withdrawn: the work is no longer wanted, and nobody did "
                f"it. read_task shows who withdrew it and why."
            )
        case ClaimRefusal.WAITING:
            return (
                f"{lost} It still waits on an unfinished dependency, or on an id no "
                f"task on this board has. read_task shows which: it becomes claimable "
                f"when the first kind finish, and says what the second needs."
            )
        case ClaimRefusal.AMENDED_BY_YOU:
            return f"{lost} {_refusal_text(TaskRefusal.CLAIMER_AMENDED_IT)}"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(refusal)


# An answer to a decision and the reason a task was closed are the operator's,
# and are laid out behind ``replies._ANSWER``. Each of the two has its own legend,
# since no reply shows both: a decision is read by its own tool.
_CLOSING_LEGEND = (
    "A line whose first characters after any indent are '> ' is the reason the "
    "operator gave for closing a task, as the operator typed it, and is neither "
    "this tool speaking nor text an agent wrote."
)


def _auto_depends_text(added: tuple[TaskId, ...]) -> str:
    """
    What the declarer is told when the board added a dependency it did not ask for.

    Said at declaration rather than left for the declarer to discover, because the
    alternative is finding out from a worker that cannot claim the task -- the same
    fact, arriving later and looking like a bug. This is the one place the board
    changes what an agent asked for, and an agent planning a sequence needs to know
    its plan was edited.

    Names the tasks and the reason together. "Depends on t-4" without the reason
    invites the lead to strip it back off, which is the concurrent write the field
    exists to prevent; the file overlap is what makes the edge non-negotiable.

    Each id is one another declaration chose, so it is laid out as ``_named``
    leaves it, like every other id in a reply.
    """
    ids = ", ".join(_named(task_id) for task_id in added)
    subject = "an unfinished task" if len(added) == 1 else "unfinished tasks"
    return (
        f"It also now depends on {ids}, added by the board: it writes files {subject} "
        f"on this board will also write. Leave the dependency in place -- it is what "
        f"keeps the two off the same file. If the overlap is wrong, narrow the touches "
        f"of one of them rather than removing the edge."
    )


def _already_behind_text(behind: tuple[TaskId, ...]) -> str:
    """
    What the declarer is told when the board left an overlap without a dependency
    because the other task already waits on this one.

    Said for the reason ``_auto_depends_text`` is, about the opposite order. Every
    other overlap queues the new task behind the older one; here the older one is
    behind the new, and a declarer that planned on the older task writing the
    shared file first has the order backwards. "Do not add the edge" is said
    because that is the repair a lead would reach for, and it is the cycle the
    board declined to make. The ids are laid out as ``_auto_depends_text`` lays
    its own.
    """
    ids = ", ".join(_named(task_id) for task_id in behind)
    one = len(behind) == 1
    return (
        f"It writes files {ids} also {'writes' if one else 'write'}, and the board "
        f"added no dependency on {'it' if one else 'them'}: "
        f"{'that task already waits' if one else 'those tasks already wait'} on this "
        f"one, so the edge would have left both sides waiting on each other. This "
        f"task is therefore worked first, and {ids} cannot be claimed until it is "
        f"finished, which is what keeps them off the same file. Do not add the "
        f"dependency yourself."
    )


def _held_live_text(holder: str | None) -> str:
    """
    Why the lead may not withdraw a task an agent that can still be woken
    holds (``TaskRefusal.WITHDRAW_HELD_LIVE``), naming the holder when a read
    after the refusal still finds it holding the task.
    """
    who = (
        f"{_one_line(holder)} holds it"
        if holder is not None
        else "When you asked, it was held by an agent"
    )
    return (
        f"{who}, which can still be woken and may be holding it across a wait, so "
        "a withdrawal by you would take the work out of its hands. Nothing "
        "changed. Post the holder a concern saying why the task is no longer "
        "wanted, so it can release it, and withdraw it once nobody holds it; or "
        "wait until the holder has finished with it."
    )


def _declare_refusal_text(
    refusal: TaskRefusal,
    held_off_fleet: str | None,
    cycle: tuple[TaskId, ...],
    on_my_board: bool | None,
    named: Sequence[TaskId] = (),
    gated: bool = False,
) -> str:
    """
    Why a declaration is refused, for the real declaration and its dry run alike,
    so the two cannot word one refusal two ways.

    ``held_off_fleet`` and ``cycle`` are the store's (``TaskWriteSettled``);
    ``on_my_board`` is ``_duplicate_id_text``'s, asked only for a duplicate id
    that is not held off the fleet. ``named`` is the declaration's
    ``depends_on``.

    **A cycle's first step says which way it closed.** The loop runs from the
    task being declared back to it, and its first step is either an id the
    declaration named or, for a task declared with ``ALL_WRITERS``, a writer on
    its board -- one that already waits on it, which is the case where the
    declaration's own ``depends_on`` may be nothing but that word.

    ``gated`` is ``graphtext._loop_has_a_gate``: whether the rule about that word is
    given. A loop of named dependencies alone gets the loop and not the rule.
    """
    if refusal is TaskRefusal.DUPLICATE_ID:
        if held_off_fleet is not None:
            # The store said whose it is, as of the apply: a session off the
            # fleet is not the declarer's, so no read of the board is asked.
            return _duplicate_id_text(False, held_off_fleet)
        return _duplicate_id_text(on_my_board)
    why = _refusal_text(refusal)
    if len(cycle) > 1:
        first = _named(cycle[1])
        if cycle[1] in named:
            why += (
                f" Its depends_on names {first}, which already waits on it, directly "
                f"or through other tasks."
            )
        else:
            why += (
                f" It would wait on every writer on this board ({ALL_WRITERS!r}), and "
                f"{first} is a writer that already waits on it, directly or through "
                f"other tasks."
            )
        if gated:
            why += (
                f" A task declared with {ALL_WRITERS!r} waits on every unfinished task "
                f"with touches on its board, so a task with touches cannot wait on it, "
                f"directly or through others."
            )
        why += f" The loop: {' -> '.join(_named(t) for t in cycle)}."
    return why
