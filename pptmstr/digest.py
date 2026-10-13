"""
One session's round, as a digest: what changed, what waits on the operator, what
is unverified, and what went wrong.

**A view, with nothing behind it but the snapshot.** Tasks, concerns and
obligations are already records with provenance -- who said it, how they say they
know, when -- and the board, the inbox and this are projections of them. Nothing
here is stored and nothing is cached: ``digest`` is a pure function of a snapshot,
a session and a point in time, so it cannot disagree with the board it summarises
(``STYLE.md`` §1, derive; do not store).

It exists because a lead reported to its operator at nearly every worker's return,
about thirty times in one session, and the operator's attention is the resource
that ran out. One report at the end of a round replaces those, and the two things
a per-return report buries -- work nobody ran and rules somebody broke -- get
slots of their own.

**The four slots do not treat the "since" point alike, and the difference is the
design.**

- *Changed* is what happened from the point on. It is the news.
- *Waiting* is whatever ``Snapshot.needs_you`` holds for the session now. An
  approval parked before the point is still parked.
- *Unverified* is the whole board, every time. Work that was not run last round
  has not been run since, and a slot that showed only this round's would let the
  list shrink by being read.
- *Incidents* are split at the point and never dropped: the ones from before it
  are carried as ``earlier_incidents``, so a reader sees what is new without the
  digest ever holding fewer than the board does.

The last two are the property this module may not break: **a digest never shows
fewer unverified items or incidents than the board holds for the session.**
``unverified`` is built from ``board.unrun_ledger`` and the board's own rows, and
the incident slots from ``board.incidents``, so that is a property of the
construction and is pinned as an invariant over generated boards in
``tests/test_digest.py``.

**What a round is, is the caller's to say.** The store has no notion of one, so
``since`` is a parameter. The bus tool passes the moment its caller last read a
digest, which the reducer keeps on the caller's record
(``AgentRecord.digest_read_at``); the pane passes None and draws that mark as a
divider.

**Times are compared with each other and with nothing else.** Every stamp here
came from ``time.monotonic()``, which orders events inside one process and says
nothing about the wall clock, so no slot renders one as a time of day.

**A decision awaiting the operator is in *waiting* like everything else owed.**
It is an obligation in ``needs_you``, so it arrives as an ``Owed`` with its kind
and its asker; its ``summary`` is the store's own row and holds none of the
asker's sentences.

**So is a break-glass command that is asked or running**, and it is the one
kind whose ``Owed`` says which of two states it is in (``Owed.command``): an
asked command has not run and a running one has, and a slot that counted the
two together would tell a lead nothing about whether what it asked for has
happened. Its ``summary`` is the store's row too, and holds none of the command.

**Where the next records go.** Requests between sessions do not exist as records
yet. ``Changed`` is the union one would join: a request another session sent or
answered is a member of it. A task adding it extends the union, the function that
fills the slot, every renderer -- each matches exhaustively, so mypy names them --
and the invariant tests.

Imports no imgui and no SDK, so it is testable with neither.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Sequence
from dataclasses import dataclass
from typing import assert_never

from .board import (
    BoardConcern,
    BoardTask,
    basis_label,
    board_tasks,
    incidents,
    not_run_label,
    role_name,
    unrun_ledger,
)
from .command import CommandState
from .model import (
    CLOSED_BY_OPERATOR,
    EARLIER_RUN,
    Basis,
    CommandOwed,
    ConcernState,
    ObligationKind,
    Snapshot,
    TaskId,
    TaskState,
)


class ChangeKind(enum.Enum):
    """
    The five things that happen to a task and leave a time behind.

    A claim is not one of them. It stamps nothing, and a round's news is what was
    asked for, what was asked for instead, what was finished, what was given
    back and what the operator closed.

    ``CLOSED`` is apart from ``COMPLETED`` because it is the opposite report:
    the operator said to stop waiting for a task, and nobody finished it. Counted
    under "completed", a round's tally would credit work that was not done.
    """

    DECLARED = "declared"
    COMPLETED = "completed"
    RELEASED = "released"
    AMENDED = "specification replaced"
    CLOSED = CLOSED_BY_OPERATOR


@dataclass(frozen=True, slots=True)
class TaskChange:
    """
    One thing that happened to one task.

    A task declared and completed in the same round is two of these, and one
    amended twice is two more. They are events, and collapsing them would report
    a round's work as its last state.
    """

    at: float
    kind: ChangeKind
    task_id: TaskId
    title: str
    # Who finished it, or who replaced its specification (``board.OPERATOR`` when
    # no agent did), under the name the board gives them. None for a declaration
    # and for a release: the record keeps the set of agents that ever gave a task
    # back and not which of them did so last, so naming one would be a guess.
    holder: str | None = None
    # On a replaced specification, the version the replacement made current.
    version: int | None = None
    # The first line of the result, on a completion that gave one. The whole of
    # it is on the board; this is what says which of several finished rows a
    # reader wants to open.
    line: str = ""
    # It happened in an earlier process and is here because the board was
    # restored from its log (``Digest.restored_at``). A reader with no mark is
    # given the whole session, and without this the whole of a restored board
    # reads as what its team just did.
    before_restart: bool = False


# What goes in the *changed* slot. One member today. A request between sessions
# joins here when it exists as a record.
Changed = TaskChange


@dataclass(frozen=True, slots=True)
class Owed:
    """
    One thing the operator owes the session, as ``Snapshot.needs_you`` holds it.

    ``summary`` is the operator's own queue row, and for a parked approval that is
    the text of a tool call nobody has approved yet -- a concern still in review,
    a command not yet run. The pane shows it. A surface an agent reads must not:
    see ``bus._digest_text``.
    """

    kind: ObligationKind
    # The node that is waiting, under its role name. For a decision and for a
    # command it is the asker, which was told to carry on and may be working.
    who: str
    since: float
    summary: str
    # Which of its two open states a command is in, and None for every other
    # kind. Read off the record when the slot is filled, so the count and each
    # line are worded from the same fact (``owed_standing``).
    command: CommandState | None = None


# What goes in the *waiting* slot. One member: everything in ``needs_you`` has the
# same four facts, an open decision included, and a command has its state beside
# them.
Waiting = Owed


class Doubt(enum.Enum):
    """
    Which statement about a task nobody has said was run.

    Three, because a task carries three separate statements with separate
    authors: what its declarer asserted in the specification, what its claimer
    asserted in the result, and whether the claimer executed what it wrote.
    """

    SPECIFICATION = "specification"
    RESULT = "result"
    NOT_RUN = "not run"


@dataclass(frozen=True, slots=True)
class Unverified:
    """
    One statement on the board that its own author did not mark as run.

    An item per statement and not per task, so the count is the number of things
    a reader would have to check and a task with three of them is not one line.
    """

    task_id: TaskId
    title: str
    owner: str | None
    doubt: Doubt
    # Where the statement stands, in the board's words (``board.basis_label``,
    # ``board.not_run_label``).
    standing: str
    # The claimer's list of what it did not run, when it gave one. Empty for the
    # other two doubts and for a claimer that did not say.
    text: str = ""


@dataclass(frozen=True, slots=True)
class Reported:
    """An incident and when it was posted, so a reader can place it against a mark."""

    at: float
    concern: BoardConcern
    # Whether a row of the session's board carries this concern, which is the one
    # condition under which a board read shows its body to every agent
    # (``board.BoardTask.concerns``): it named a task that is on the board and
    # was not withdrawn. Derived from the same rows, so a surface an agent reads
    # can show an incident's text exactly when the board does and cannot drift
    # from it. No default: a surface that built one without asking would be
    # choosing between publishing mail and hiding a finding.
    on_board: bool


@dataclass(frozen=True, slots=True)
class Digest:
    """
    The four slots, for one session, from one point.

    Every slot is always present. An empty tuple is a statement -- nothing
    changed, nothing is waiting -- and each renderer says it by name, because a
    slot left out reads as a slot that does not exist.
    """

    # The point ``changed`` and ``incidents`` are counted from, or None for the
    # whole session.
    since: float | None
    changed: tuple[Changed, ...]
    waiting: tuple[Waiting, ...]
    unverified: tuple[Unverified, ...]
    incidents: tuple[Reported, ...]
    # Incidents posted before ``since``. Empty when ``since`` is None. Carried so
    # that ``incidents`` and this together are always every incident on the board.
    earlier_incidents: tuple[Reported, ...] = ()
    # The instant this session's board was read back from its log, or None when
    # it was not (``model.Restore.at``). Everything stamped before it happened in
    # an earlier process, on every slot: ``changed`` marks its own entries, and
    # an incident is placed by ``Reported.at``.
    restored_at: float | None = None


def digest(snap: Snapshot, session_id: str, since: float | None = None) -> Digest:
    """
    The digest of ``session_id``'s board from ``since`` on.

    An event stamped exactly at ``since`` is counted as after it. The two ways to
    be wrong at that boundary are showing it in two consecutive digests and
    showing it in neither, and only the second loses something.
    """
    rows = board_tasks(snap, session_id)
    carried = {cid for row in rows for cid in row.concerns}
    reported = tuple(
        Reported(snap.concerns[c.id].posted_at, c, c.id in carried)
        for c in incidents(snap, session_id)
    )
    restore = snap.restores.get(session_id)
    restored_at = None if restore is None else restore.at
    return Digest(
        since=since,
        restored_at=restored_at,
        changed=_changed(snap, rows, since, restored_at),
        waiting=_waiting(snap, session_id),
        unverified=_unverified(rows, unrun_ledger(snap, session_id)),
        incidents=tuple(r for r in reported if _from(r.at, since)),
        earlier_incidents=tuple(r for r in reported if not _from(r.at, since)),
    )


def digest_mark(snap: Snapshot, session_id: str) -> float | None:
    """
    When the session's lead last read a digest, or None if it never has.

    The lead's and nobody else's: the digest is the lead's report to the operator,
    so the mark an operator wants drawn is where that report last reached. A
    worker that reads one has a mark of its own, which only its own next read
    uses.
    """
    record = snap.get((session_id, None))
    return None if record is None else record.digest_read_at


def first_line(text: str) -> str:
    """The first line of ``text`` with anything on it, trimmed, or empty."""
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _from(at: float, since: float | None) -> bool:
    return since is None or at >= since


def _changed(
    snap: Snapshot,
    rows: tuple[BoardTask, ...],
    since: float | None,
    restored_at: float | None = None,
) -> tuple[Changed, ...]:
    """
    Every declaration, replaced specification, completion, close and release
    from ``since`` on, oldest first.

    The times come from the records and the names from the board rows, so a
    change is scoped and addressed exactly as the board scopes and addresses the
    task it happened to.

    One stamped before ``restored_at`` is still listed -- it is on the board and
    the reader has not been told of it -- and is marked as from before the
    restart. The store stamps everything it restores strictly before that
    instant and nothing else can be, so the comparison is exact.
    """
    out: list[TaskChange] = []
    for row in rows:
        task = snap.tasks[row.id]
        if _from(task.declared_at, since):
            out.append(TaskChange(task.declared_at, ChangeKind.DECLARED, row.id, row.title))
        if task.released_at is not None and _from(task.released_at, since):
            out.append(TaskChange(task.released_at, ChangeKind.RELEASED, row.id, row.title))
        # One per replacement, from the history and not from the last stamp, so
        # two amendments in a round are two lines. The prior at index n was
        # replaced by version n + 2.
        for index, prior in enumerate(row.superseded):
            if _from(prior.replaced_at, since):
                out.append(
                    TaskChange(
                        prior.replaced_at,
                        ChangeKind.AMENDED,
                        row.id,
                        row.title,
                        holder=prior.replaced_by,
                        version=index + 2,
                    )
                )
        if (
            row.state is TaskState.COMPLETED
            and task.completed_at is not None
            and _from(task.completed_at, since)
        ):
            out.append(
                TaskChange(
                    task.completed_at,
                    # Decided by the row's own fact. A result that opens with the
                    # closing words is a claimer's text and a completion.
                    ChangeKind.CLOSED if row.closed_by_operator else ChangeKind.COMPLETED,
                    row.id,
                    row.title,
                    holder=row.owner,
                    line=first_line(row.result),
                )
            )
    if restored_at is not None:
        out = [
            dataclasses.replace(change, before_restart=change.at < restored_at) for change in out
        ]
    # Stable, so two changes with one stamp keep the board's order.
    return tuple(sorted(out, key=lambda change: change.at))


def _waiting(snap: Snapshot, session_id: str) -> tuple[Waiting, ...]:
    """
    What ``needs_you`` holds for this session, in its order.

    Filtered and renamed, never re-derived: that list is the one account of what
    the operator owes, and a second walk over pending approvals is how it came to
    have two answers before.
    """
    return tuple(
        Owed(
            o.kind,
            role_name(snap, o.node, session_id),
            o.since,
            o.summary,
            o.command.state if isinstance(o, CommandOwed) else None,
        )
        for o in snap.needs_you
        if o.node[0] == session_id
    )


def _unverified(
    rows: tuple[BoardTask, ...], ledger: tuple[BoardTask, ...]
) -> tuple[Unverified, ...]:
    """
    Every statement on the board not marked as run, in board order.

    A specification or a result is one unless its author said ``run``; "read",
    "relayed" and no marker at all are the three ways of not having said it. A
    task with no specification text, or no result, makes no statement of that
    kind to doubt.

    What was not run comes from ``ledger`` -- ``board.unrun_ledger`` -- and is
    not recomputed here, so the two cannot disagree about which finished rows
    count.

    The result of a task the operator closed is not one of these statements, for
    the reason it is not on the ledger: it is the operator's reason for no
    longer waiting, and asserts no work a reader could go and check.
    """
    unrun = {row.id for row in ledger}
    out: list[Unverified] = []
    for row in rows:

        def item(doubt: Doubt, standing: str, text: str = "", row: BoardTask = row) -> Unverified:
            return Unverified(row.id, row.title, row.owner, doubt, standing, text)

        if row.detail and row.basis is not Basis.RUN:
            out.append(item(Doubt.SPECIFICATION, basis_label(row.basis)))
        if row.result and row.result_basis is not Basis.RUN and not row.closed_by_operator:
            out.append(item(Doubt.RESULT, basis_label(row.result_basis)))
        if row.id in unrun:
            out.append(item(Doubt.NOT_RUN, not_run_label(row.not_run), row.not_run or ""))
    return tuple(out)


# -- the words both readers are shown ----------------------------------------------
#
# Here, beside the projection, for the reason ``board.basis_label`` is beside its
# own: the bus tool and the pane both render a digest, and a slot headed one way
# for the lead and another for the operator would be two reports of one round.
# Layout, bounds and what each reader may see of the waiting slot stay with the
# surface that draws them.


def counted(names: Sequence[str]) -> str:
    """How many of each name, in first-seen order: ``2 completed, 1 declared``."""
    seen: dict[str, int] = {}
    for name in names:
        seen[name] = seen.get(name, 0) + 1
    return ", ".join(f"{count} {name}" for name, count in seen.items())


def changed_heading(changed: Sequence[Changed]) -> str:
    """
    The slot's heading: how many of each kind, and, when some of them are a
    restored board's past, that they are.

    Said here because both readers of a digest are given this line. A lead
    resumed onto a restored board has no mark, so its first digest lists the
    whole board, and a lead told to report a round's changes would report an
    earlier process's work as its own round.
    """
    if not changed:
        return "CHANGED (0): nothing was declared, amended, completed or released."
    heading = f"CHANGED ({len(changed)}): {counted([c.kind.value for c in changed])}"
    old = sum(1 for change in changed if change.before_restart)
    if not old:
        return heading
    return (
        f"{heading}. This board was restored from its log: the {old} marked "
        f"'{EARLIER_RUN}' are history, not this round's changes."
    )


def change_label(change: Changed) -> str:
    """
    One change on one line: what happened, to which task, and by whom if known.

    The restart mark leads the line. After the title it would sit where an
    agent's own words end, and a title can be written to end in anything.
    """
    label = _change_label(change)
    return f"{EARLIER_RUN}, {label}" if change.before_restart else label


def _change_label(change: Changed) -> str:
    match change.kind:
        case ChangeKind.COMPLETED:
            who = f" by {change.holder}" if change.holder else ""
            return f"completed{who}: {change.task_id} {change.title}"
        case ChangeKind.CLOSED:
            return f"{CLOSED_BY_OPERATOR}: {change.task_id} {change.title}"
        case ChangeKind.DECLARED:
            return f"declared: {change.task_id} {change.title}"
        case ChangeKind.RELEASED:
            return f"released: {change.task_id} {change.title}"
        case ChangeKind.AMENDED:
            who = f" by {change.holder}" if change.holder else ""
            which = f", now v{change.version}" if change.version is not None else ""
            return f"specification replaced{who}{which}: {change.task_id} {change.title}"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(change.kind)


def owed_counts(waiting: Sequence[Waiting]) -> str:
    """How many of each kind of thing the operator owes, in the slot's own order."""
    return counted([_owed_name(owed) for owed in waiting])


# What an open command is waiting for, by state. ``ASKED`` says what has not
# happened and not what the operator has or has not read: nothing records a
# reading, and "not yet read" would be a guess about a person.
COMMAND_ASKED = "asked, and neither run nor refused by the operator yet"
COMMAND_RUNNING = "running"


def owed_standing(owed: Waiting) -> str:
    """
    Where a waiting command stands, in words, and "" for every other kind.

    Here and not at either renderer because both are given it: a lead reads
    this of a command it asked for, and "is waiting" would be false of an
    asker that was told to carry on and of a command that is running.
    """
    if owed.kind is not ObligationKind.COMMAND:
        return ""
    return COMMAND_RUNNING if owed.command is CommandState.RUNNING else COMMAND_ASKED


def _owed_name(owed: Waiting) -> str:
    match owed.kind:
        case ObligationKind.APPROVAL:
            return "approval(s)"
        case ObligationKind.QUESTION:
            return "question(s)"
        case ObligationKind.FAILURE:
            return "failed session(s)"
        case ObligationKind.DECISION:
            return "decision(s)"
        case ObligationKind.COMMAND:
            return f"command(s) {owed_standing(owed)}"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(owed.kind)


def unverified_heading(unverified: Sequence[Unverified]) -> str:
    """
    The slot's heading, which carries its count and never stands in for its items.

    The empty case says what an empty slot is made of, and whose word that is:
    no author on the board said anything short of ``run`` and no claimer left
    what it did not run unsaid, and nothing here checked either. Worded as what
    is absent, because the slot is also empty on a board with no specification
    text and nothing finished, where "every result is marked run" would report
    work that does not exist.
    """
    if not unverified:
        return (
            "UNVERIFIED (0): nothing. No specification or result on the board is "
            "marked anything short of run by its own author, and no finished task "
            "is without its claimer's statement that nothing was left unrun -- which "
            "is as true of a board with no such text and nothing finished. Those are "
            "their statements; nothing checked them."
        )
    tasks = len({item.task_id for item in unverified})
    return (
        f"UNVERIFIED ({len(unverified)} statement(s) on {tasks} task(s)), none of them "
        f"said to be run:"
    )


def unverified_label(item: Unverified) -> str:
    """One unverified statement on one line: which task, whose, and where it stands."""
    who = f" [{item.owner}]" if item.owner else ""
    match item.doubt:
        case Doubt.SPECIFICATION | Doubt.RESULT:
            return f"{item.task_id} {item.title}{who}: {item.doubt.value} {item.standing}"
        case Doubt.NOT_RUN:
            return f"{item.task_id} {item.title}{who}: {item.standing}"
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(item.doubt)


def incidents_heading(digest: Digest) -> str:
    """
    The incident slot's heading. Empty is "none marked", never "none".

    The marker is something a sender sets, so an empty slot is the absence of a
    mark and the heading says what that leaves uncounted.
    """
    new, earlier = len(digest.incidents), len(digest.earlier_incidents)
    tally = f"{new} this round, {earlier} earlier" if earlier else f"{new}"
    if not new:
        return (
            f"INCIDENTS ({tally}): none marked{' this round' if earlier else ''}. A slip "
            f"described only in a result or in an unmarked concern is not counted here."
        )
    return f"INCIDENTS ({tally}):"


# What introduces the incidents from before the point a digest counts from.
EARLIER_INCIDENTS = "reported before this round, and still on the board"


def incident_attribution(reported: Reported) -> str:
    """
    Who reported an incident to whom, how they say they know, which task they
    named and what became of it: its line, short of the subject.

    Apart from ``incident_label`` because a surface an agent reads shows a
    concern's subject only when a board read does (``Reported.on_board``), and
    still has to list the incident. The task id is here as the sender gave it,
    and one that is on no board is the sender's typing like the subject is:
    ``bus._incident_headline`` drops it before asking.
    """
    c = reported.concern
    notes = [basis_label(c.basis)]
    if c.task_id is not None:
        notes.append(f"about {c.task_id}")
    if c.state is ConcernState.WITHDRAWN:
        notes.append("withdrawn by the operator before delivery")
    if c.edited:
        notes.append("edited by the operator before delivery")
    return f"[{c.sender} to {c.recipient}, {', '.join(notes)}]"


def incident_label(reported: Reported) -> str:
    """One incident's attribution and subject: the part no budget cuts."""
    return f"{incident_attribution(reported)} {reported.concern.subject or '(no subject)'}"
