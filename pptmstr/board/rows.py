"""
One session's task board and concern log, as rows.

**Read by two participants that used to have one reader between them.** The DETAIL
pane draws these rows, and so does the ``read_board`` bus tool -- a worker asking
what is on its board is answered from this module, through the effect channel, with
the projection the operator is looking at. That is the point: a board with two
derivations would be two boards, and the failure it exists to prevent is a team and
its operator disagreeing about what the work is.

It lives beside the store rather than under ``ui/`` for that reason. It was in
``ui/`` while a pane was the only reader, which was true and stopped being true;
nothing about scoping a board to a session or naming its participants was ever
presentation.

The store holds a single fleet-wide ``tasks`` map and a single ``concerns`` map --
there is one ``Store`` for every session (app.py) -- so the session scoping happens
here, and ``store.tasks._pick_claim`` applies the same predicate (``Task.belongs_to``) so
a worker cannot claim a task this module would not have shown it.

The facts stay in ``model.py``: ``Task.blocked_on`` is the dependency graph's answer
and is derived on every read.

Imports no imgui, so the derivation is testable without a GL context.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass

from ..addresses import OPERATOR, role_name
from ..model import (
    CLOSED_BY_OPERATOR,
    Basis,
    ConcernId,
    ConcernState,
    NodeId,
    Provenance,
    Snapshot,
    Task,
    TaskId,
    TaskState,
    withdrawal_label,
)
from ..templates import SOLO


def amender_name(snap: Snapshot, node: NodeId | None, session_id: str) -> str:
    """
    Who replaced a specification, as a board names them: ``role_name`` for an
    agent, and ``OPERATOR`` for the None an operator's amendment carries.
    """
    return OPERATOR if node is None else role_name(snap, node, session_id)


@dataclass(frozen=True, slots=True)
class BoardVersion:
    """
    A specification a task used to have: ``model.TaskVersion`` with its replacer
    under a name a reader can use.
    """

    detail: str
    property: str
    basis: Basis | None
    replaced_at: float
    # A role name or ``OPERATOR``, never a NodeId.
    replaced_by: str


@dataclass(frozen=True, slots=True)
class BoardTask:
    """
    One row of the task table.

    ``blocked_on`` and ``owner_gone`` are recomputed for every frame that builds
    one of these. Nothing here is cached on the snapshot: blocked-ness is a
    function of the graph (``TaskState`` has no BLOCKED member by design) and the
    owner's liveness is a function of the node table in the same snapshot.
    """

    id: TaskId
    title: str
    # The specification as the declarer wrote it.
    #
    # Carried because it was written once, read once, by one agent, and displayed
    # nowhere: the only read of `Task.detail` in the application was the
    # `claim_task` reply. The participant who can correct a wrong spec is the one
    # who could not see it, which is how a task sat CLAIMED with a live, correctly
    # reasoning owner and a reason nobody could read.
    detail: str
    state: TaskState
    # None when nothing has claimed it. Otherwise a role name, never a NodeId:
    # ("sess-4", "agent-7") means nothing to a reader.
    owner: str | None
    # Still CLAIMED, but the claimer has finished, failed, been cancelled, or left
    # the snapshot entirely. The task reads CLAIMED forever and no one is working
    # it. Promised by the TaskState docstring as the derived condition a board owes
    # its reader.
    #
    # The state check is the whole signal. `TaskCompleted` keeps `claimed_by`, and
    # a sub-agent that did its job stops with `AgentFinished(DONE)` -- so without
    # it every completed row on a successful run reads as stranded, and the one row
    # that is actually stranded stops standing out.
    owner_gone: bool
    blocked_on: tuple[TaskId, ...]
    # The subset of blocked_on naming a task that was never declared, which is a
    # typo in a lead's depends_on and not a wait. `declare_task` accepts these
    # silently, so this row is the operator's only signal.
    missing: tuple[TaskId, ...]
    # The files this task declared it would write.
    #
    # On the row because the dependency graph above is now partly derived from it:
    # an edge the board added is not in the lead's plan, and an operator reading
    # `blocked_on` with no sight of `touches` sees a wait with no cause.
    touches: tuple[str, ...]
    # Concerns naming this task and not yet withdrawn, newest last.
    #
    # The derived answer to "claimed, and there is an open concern about it". A row
    # stalled for a recorded reason is not the same row as one stalled silently, and
    # `owner_gone` distinguishes the wrong two cases on its own: it is false for the
    # task whose owner is alive, working, and deliberately waiting, which is exactly
    # the row that looked identical to ordinary progress.
    #
    # WITHDRAWN is excluded because a retracted reason is not a reason. DELIVERED is
    # kept: the recipient having read it does not settle the matter it raised, and
    # dropping it here would make the row's explanation vanish at the moment someone
    # started acting on it.
    concerns: tuple[ConcernId, ...]
    # Everything the task was declared to wait for, finished or not, in declared
    # order. ``blocked_on`` is the unfinished subset and is what a row shows while
    # work moves; this is what a reader of one whole task is owed, because "waits on
    # nothing" and "waited on t-4, which is done" are different histories and only
    # the second says whose work this was built on.
    depends_on: tuple[TaskId, ...] = ()
    # What the claimer reported when it completed the task, as it wrote it. Empty
    # until then, and empty afterwards if it reported nothing.
    #
    # On the row because a result held only on the record is a result nobody reads:
    # the next worker and the operator both meet a finished task here, and neither
    # was in the transcript it was written in.
    result: str = ""
    # How each text's own author says it knows what it asserts, or None where it
    # did not say: the declarer about ``detail``, the claimer about ``result``.
    # Passed through from the record untouched -- this projection derives nothing
    # about a standing, because nothing in the snapshot could support one.
    basis: Basis | None = None
    result_basis: Basis | None = None
    # The property the work may not break, one line, or empty when none was
    # stated. Structure rather than prose on every surface: it is shown whole.
    property: str = ""
    # What the claimer says it did not execute, in ``model.Task.not_run``'s three
    # values: None for unsaid, text, or empty for a stated "nothing".
    not_run: str | None = None
    # Which specification ``detail`` and ``property`` are, counting the
    # declaration as one.
    version: int = 1
    # The version the holder was handed when it claimed, or None when nobody
    # holds the row or the claim recorded none. On a COMPLETED row this is the
    # version the result was written against; less than ``version`` means the
    # specification was replaced after the claimer read it, on any row that has
    # an owner.
    claimed_version: int | None = None
    # Every earlier specification, oldest first: version one is at index zero.
    superseded: tuple[BoardVersion, ...] = ()
    # The node behind ``owner``, for a caller that has to ask "is that me" -- the
    # bus deciding whether the agent completing a task is the one that was handed
    # its specification. Never shown: ``owner`` is the name a reader can use.
    holder: NodeId | None = None
    # Still CLAIMED, and the holder is an agent of an earlier run of the session
    # (``Snapshot.of_earlier_run``): another process's, or this one's from before
    # the session was started again. The subset of ``owner_gone`` whose claim the
    # session's lead can give back, where a holder of the current run that only
    # finished its turn can be woken and keeps it. ``_holder_left_behind`` is the
    # rule.
    holder_left_behind: bool = False
    # The subset of ``missing`` naming an id another session's task has. Not a
    # typo: the id is taken, fleet-wide, so it can never be declared on this
    # board and the wait can never end by itself (``foreign_wait_label``).
    held_elsewhere: tuple[TaskId, ...] = ()
    # Finished because the operator closed it, and not by any agent's work
    # (``model.Task.closed_by_operator``). ``owner`` is None on such a row and
    # ``result`` is the closing words and the operator's reason.
    closed_by_operator: bool = False
    # Where the work came from when another lead asked for it
    # (``model.Task.provenance``): the declarer's statement, all of it.
    provenance: Provenance | None = None
    # Finished because its declarer withdrew it, and not by any agent's work
    # (``model.Task.withdrawn``). ``owner`` is None on such a row and ``result``
    # is the withdrawal's words and the declarer's reason.
    withdrawn: bool = False
    # Of a withdrawn row: its session's root withdrew it in place of a declarer
    # that had stopped, so the reason is the root's (``model.Task.withdrawn_by_lead``).
    withdrawn_by_lead: bool = False
    # Of a withdrawn row: the operator rewrote the reason in flight
    # (``model.Task.withdrawal_edited``).
    withdrawal_edited: bool = False
    # Its declarer named ``model.ALL_WRITERS``: it waits on every writer on this
    # board, including writers declared after it (``model.Task.after_all_writers``).
    after_all_writers: bool = False
    # Who those writers are at the time of this read (``model.Task.writers``).
    # They are also in ``blocked_on``. They are listed here as well because a
    # reader of the row cannot tell them from declared dependencies, and the
    # set changes with every declaration and completion.
    writers: tuple[TaskId, ...] = ()

    # ``builtins.property`` by its full name: inside this class the bare name is
    # the ``property`` field above.
    @builtins.property
    def unworked(self) -> bool:
        """
        Finished with no agent's account of work: closed by the operator, or
        withdrawn by its declarer. Neither has a claimer to state a basis or
        what was run, and a surface that asked for one would report its absence
        as a gap.
        """
        return self.closed_by_operator or self.withdrawn


def all_writers_label(row: BoardTask) -> str:
    """
    What a row says about waiting on every writer, or nothing for a row that
    does not.

    It names who the writers are at this read, and says when there are none, so
    the set is not taken for one stored on the task. A finished row only says
    what was declared, because it waits on nothing any more (``model.Task.writers``).
    """
    if not row.after_all_writers:
        return ""
    if row.state is TaskState.COMPLETED:
        return "declared to wait on every writer on this board"
    now = ", ".join(row.writers) if row.writers else "none unfinished"
    return f"waits on every writer on this board, declared before or after it (now: {now})"


def version_label(row: BoardTask) -> str:
    """
    Which specification a row is showing and who made it so, or nothing for a
    task still on the one it was declared with.

    Silent at version one because that is nearly every row, and a board that
    numbered them all would bury the few whose specification moved. Here rather
    than in a renderer, for ``words.basis_label``'s reason: the bus tool and the
    pane both say this.
    """
    if not row.superseded:
        return ""
    who = " ".join(row.superseded[-1].replaced_by.splitlines())
    return f"specification v{row.version}, replaced by {who}"


def stale_label(row: BoardTask) -> str:
    """
    What a row says when the specification it shows is not the one its holder was
    handed, or nothing when it is.

    On a finished row that is which version the result was written against. On a
    claimed one it is that the holder has not been shown the current text. Both
    are the same fact at two moments -- ``claimed_version`` behind ``version`` --
    and it is the fact amendment exists to make readable: a result filed under a
    specification its author never saw looks, without this, like a result for it.

    A row whose claim recorded no version says so once the specification has
    moved, and says nothing while there has only ever been one text to be handed.
    """
    if row.owner is None or row.version == 1 or row.claimed_version == row.version:
        return ""
    handed = (
        "a version nobody recorded" if row.claimed_version is None else f"v{row.claimed_version}"
    )
    if row.state is TaskState.COMPLETED:
        return f"result written against {handed}; the specification is now v{row.version}"
    if row.state is TaskState.CLAIMED:
        return f"its holder was handed {handed} and has not been shown v{row.version}"
    return ""


def closed_label(row: BoardTask) -> str:
    """
    What a row the operator closed says in place of a claimer's standing, or
    nothing for any other row.

    Says both halves, because the state beside it reads "completed" and a
    reader that stops there takes the work for done: who ended it, and that
    nothing is reported as built or run. Read off the row's own fact and never
    off the result's opening words, which a claimer can type.
    """
    if not row.closed_by_operator:
        return ""
    return f"{CLOSED_BY_OPERATOR} -- not finished by an agent, and no work is reported"


def withdrawn_label(row: BoardTask) -> str:
    """
    What a row its declarer withdrew says in place of a claimer's standing, or
    nothing for any other row.

    ``closed_label`` for a withdrawal, and for its reasons: who ended the task,
    whose words its reason is, and that nothing is reported as built or run,
    read off the row's own facts (``model.withdrawal_label``).
    """
    if not row.withdrawn:
        return ""
    who = withdrawal_label(row.withdrawn_by_lead, row.withdrawal_edited)
    return f"{who} -- not finished by an agent, and no work is reported"


def ended_label(row: BoardTask) -> str:
    """
    ``closed_label`` or ``withdrawn_label``, whichever the row is, or nothing
    for a row an agent finished or that is not finished.
    """
    return closed_label(row) or withdrawn_label(row)


def foreign_wait_label(row: BoardTask) -> str:
    """
    What a row waiting on an id another session's task has says about it, or
    nothing for a row with no such dependency.

    One sentence carrying the three things an agent otherwise learns from three
    refusals that contradict each other: the id is not on this board, it cannot
    be put there, and what ends the wait. "Declare it again" is under a new id
    by necessity -- this task's own id stays on the board once it is closed.
    """
    if not row.held_elsewhere:
        return ""
    ids = ", ".join(row.held_elsewhere)
    which = "which" if len(row.held_elsewhere) == 1 else "each of which"
    return (
        f"waits on {ids}, {which} another session in this window holds -- it cannot "
        f"be declared here; ask the operator to close this task and declare it "
        f"again, under a new id, without that dependency"
    )


@dataclass(frozen=True, slots=True)
class BoardConcern:
    """One row of the concern log, delivered ones included: it is the audit trail."""

    id: ConcernId
    sender: str
    recipient: str
    subject: str
    # What the sender actually said, not just what they called it.
    #
    # Carried because the subject is a label and the defect is about reasoning. A
    # reader given "checksum ignored in sixty places" and nothing else knows a
    # conclusion exists and not what it was, so it has to re-derive it -- which is
    # the relitigating this projection is meant to end.
    #
    # As delivered, which is not always as posted: the operator can rewrite a
    # concern in flight, and ``edited`` says when they did.
    body: str
    state: ConcernState
    edited: bool
    # The task this concern is about, when the sender named one, and whether that
    # id is on this board. A concern naming a task that was never declared is a
    # typo the store deliberately does not refuse -- the message was still sent --
    # so the projection reports it, in the same shape `missing` reports a
    # dependency that does not exist.
    task_id: TaskId | None
    task_missing: bool
    # How the sender says it knows what ``body`` asserts, or None when it did not
    # say. The sender's statement about what it wrote; ``edited`` says when the
    # body is no longer that.
    basis: Basis | None = None
    # The sender marked this as a report of something that went wrong.
    incident: bool = False
    # Still waiting, for a recipient of an earlier run of the session
    # (``Snapshot.of_earlier_run``): nothing running reads that inbox, so it is
    # not waiting to be read. It is handed over if that agent is started again
    # under the id it had, and otherwise never.
    undeliverable: bool = False


# What a concern's row says in place of "waiting" when ``undeliverable``.
UNDELIVERABLE = "posted to an agent that is not running"


def has_board(snap: Snapshot, session_id: str) -> bool:
    """
    Whether this session is a team, and so has a board worth a heading.

    Read from the launched template rather than derived from what is on the board.
    Both derivable signals are wrong rather than merely awkward:

    * **Emptiness** flips mid-run, on a different event each run -- a research
      session whose lead posts a concern before declaring a task flips on the
      concern. It is wrong exactly during the first minutes, which is when the
      operator is deciding whether the team is working at all.
    * **Has children** labels every solo session a team, because a solo session
      spawns ``Explore`` sub-agents constantly.

    Deciding it here rather than in the store is the ``ui/projects.py`` split:
    the record carries the template's name, and what counts as a team is this
    module's judgement. SOLO is the only shape with no board today; a template
    with roles has one from the first frame, empty or not.

    **False for a session recalled from its file, and for one off the fleet.**
    A recalled record carries the template it was last launched under and no
    board: its log is read when it is resumed and not before, so "a team from
    the first frame, empty or not" would draw an empty board over one that was
    never looked at. A session off the fleet is drawn nowhere
    (``Snapshot.off_fleet``).
    """
    record = snap.get((session_id, None))
    if record is None or record.recalled is not None or snap.off_fleet(session_id):
        return False
    return record.template is not None and record.template != SOLO.name


def board_tasks(snap: Snapshot, session_id: str) -> tuple[BoardTask, ...]:
    """
    The tasks declared by one session, oldest first.

    Scoped by ``Task.belongs_to``, which is the same predicate ``store.tasks._pick_claim``
    applies -- so a worker is handed only tasks this function would have shown it.
    Ordered by declaration rather than by state so a row does not jump when somebody
    claims it: the board is read while work moves.

    Deciding the scope in the model rather than in this pane is what makes the
    pane and the reducer incapable of disagreeing about whose task a task is: a
    worker claiming across sessions would leave the row on one board while the
    other's operator watched an agent work on something nothing accounted for.

    ``role_name``'s foreign-claimer handling is the record of a state the store
    does not enter -- neither a claim nor a restore gives a task to another
    session's agent -- so a snapshot built some other way still renders
    honestly rather than as one of this board's own agents.

    Blocked-ness is scoped to the same board, by ``Task.blocked_on`` and not by
    filtering here: a dependency on an id this board does not have is
    unsatisfied and listed in ``missing``, whether nothing has that id or
    another session's task does (``Task.dependency``).
    """
    explained = _open_concerns_by_task(snap, session_id)
    rows = []
    for task in sorted(snap.tasks.values(), key=lambda t: (t.declared_at, t.id)):
        if not task.belongs_to(session_id):
            continue
        blocked = task.blocked_on(snap.tasks)
        missing = tuple(d for d in blocked if task.dependency(snap.tasks, d) is None)
        owner = task.claimed_by
        rows.append(
            BoardTask(
                id=task.id,
                title=task.title,
                detail=task.detail,
                state=task.state,
                owner=None if owner is None else role_name(snap, owner, session_id),
                owner_gone=(
                    task.state is TaskState.CLAIMED and owner is not None and _is_gone(snap, owner)
                ),
                blocked_on=blocked,
                missing=missing,
                held_elsewhere=tuple(d for d in missing if d in snap.tasks),
                touches=task.touches,
                concerns=explained.get(task.id, ()),
                depends_on=task.depends_on,
                result=task.result,
                basis=task.basis,
                result_basis=task.result_basis,
                property=task.property,
                not_run=task.not_run,
                version=task.version,
                claimed_version=task.claimed_version,
                holder=owner,
                holder_left_behind=_holder_left_behind(snap, task, session_id),
                closed_by_operator=task.closed_by_operator,
                provenance=task.provenance,
                withdrawn=task.withdrawn,
                withdrawn_by_lead=task.withdrawn_by_lead,
                withdrawal_edited=task.withdrawal_edited,
                after_all_writers=task.after_all_writers,
                writers=task.writers(snap.tasks),
                superseded=tuple(
                    BoardVersion(
                        detail=prior.detail,
                        property=prior.property,
                        basis=prior.basis,
                        replaced_at=prior.replaced_at,
                        replaced_by=amender_name(snap, prior.replaced_by, session_id),
                    )
                    for prior in task.superseded
                ),
            )
        )
    return tuple(rows)


def _holder_left_behind(snap: Snapshot, task: Task, session_id: str) -> bool:
    """
    Whether ``task``, a row of ``session_id``'s board, is held by an agent of an
    earlier run of that session (``Snapshot.of_earlier_run``).

    **The row's copy of ``store.tasks._may_reclaim``**, asked for the root of
    ``session_id``: true means that root's ``release_task`` on this row is one
    the store takes, and the bus offers it on nothing else. A copy and not a
    call, because ``store`` imports this module. Kept in step by a test that
    makes the release for each kind of holder (``tests/test_board.py``,
    ``test_a_row_says_its_holder_was_left_behind_exactly_where_the_lead_may_release_it``),
    because a second spelling of a rule drifts.

    Narrower than the store in one place, on purpose. A holder from another
    session that is of an earlier run of *that* session is False here, and the
    store would take the root's release of it. The store puts no such holder
    on a row (``board_tasks``), so the difference is one sentence not offered
    about a state nothing reaches, and the rule as ``_may_reclaim`` states it
    is about the task's own session.
    """
    holder = task.claimed_by
    return (
        task.state is TaskState.CLAIMED
        and holder is not None
        and holder[0] == session_id
        and snap.of_earlier_run(holder)
    )


def _on_board(snap: Snapshot, task_id: TaskId, session_id: str) -> bool:
    """
    Whether ``session_id``'s board has a task ``task_id``. An id only another
    session's board has is not on this one, for a concern as for a dependency.
    """
    task = snap.tasks.get(task_id)
    return task is not None and task.belongs_to(session_id)


def _open_concerns_by_task(snap: Snapshot, session_id: str) -> dict[TaskId, tuple[ConcernId, ...]]:
    """
    Concerns naming a task, indexed by that task, oldest first.

    Built once per call rather than scanned per row: the alternative is a pass over
    every concern for every task, and this projection is rebuilt every frame.

    Scoped by sender to match ``board_concerns``, so a row's explanation and the
    concern log below it cannot disagree about which messages are this board's.
    A peer message is neither, for ``board_concerns``' reason.
    """
    index: dict[TaskId, list[ConcernId]] = {}
    for c in sorted(snap.concerns.values(), key=lambda c: (c.posted_at, c.id)):
        if c.task_id is None or c.sender[0] != session_id or c.is_peer:
            continue
        if c.state is ConcernState.WITHDRAWN:
            continue
        index.setdefault(c.task_id, []).append(c.id)
    return {tid: tuple(ids) for tid, ids in index.items()}


def board_concerns(snap: Snapshot, session_id: str) -> tuple[BoardConcern, ...]:
    """
    Every concern between agents of one session, oldest first.

    Delivered and withdrawn ones stay. The store keeps a concern precisely because
    it is the only record of what a worker was actually told -- including an edit
    the operator made on the way through -- and a log of only what is still
    waiting throws that away.

    Scoped by sender, which is authenticated by the gate. A concern to a node in
    another session cannot arise through ``post_concern`` (roles resolve within a
    session) and would still belong on its sender's board if it did.

    **A message between leads is not here.** It is filed in its sender's log
    like a concern, if that session was recording its board when it was sent,
    but it is not mail inside the team: a worker reading the board would be
    reading another team's words to its lead, and a team count would count
    them. ``read_peer_messages`` is where a lead reads them.
    """
    rows = [
        BoardConcern(
            id=c.id,
            sender=role_name(snap, c.sender, session_id),
            recipient=role_name(snap, c.recipient, session_id),
            subject=c.subject,
            body=c.body,
            state=c.state,
            edited=c.edited,
            task_id=c.task_id,
            task_missing=c.task_id is not None and not _on_board(snap, c.task_id, session_id),
            basis=c.basis,
            incident=c.incident,
            undeliverable=(c.state is ConcernState.POSTED and snap.of_earlier_run(c.recipient)),
        )
        for c in sorted(snap.concerns.values(), key=lambda c: (c.posted_at, c.id))
        if c.sender[0] == session_id and not c.is_peer
    ]
    return tuple(rows)


def unrun_ledger(snap: Snapshot, session_id: str) -> tuple[BoardTask, ...]:
    """
    The finished tasks on one session's board that nobody has said were run in
    full, oldest first.

    A completed row is on it unless its claimer stated that nothing was left
    unrun. So it holds two kinds of row and a reader tells them apart by
    ``not_run``: text is a claimer naming what it wrote and did not execute, and
    None is a claimer that did not say -- which includes a completion with no
    result at all, the least accounted-for row a board has.

    **Unstated rows are on the ledger, and that is what makes it one.** Listing
    only the rows that volunteered something would report an agent that said
    "the probe was never run live" and stay silent about one that said nothing,
    which ranks candour as the defect.

    Rows, not a count, and nothing here calls a short ledger good: a row leaves
    it on its claimer's word alone.

    A task the operator closed, or its declarer withdrew, is not on it. The
    ledger is of work somebody reported and nobody said was run, and such a row
    reports none: it says so itself (``ended_label``), which is a stronger
    statement than "unstated".
    """
    return tuple(
        row
        for row in board_tasks(snap, session_id)
        if row.state is TaskState.COMPLETED and row.not_run != "" and not row.unworked
    )


def incidents(snap: Snapshot, session_id: str) -> tuple[BoardConcern, ...]:
    """
    The concerns on one session's board whose senders marked them as incidents,
    oldest first.

    Withdrawn ones stay. The operator stopping a message from being delivered
    does not undo the slip it reported, and ``state`` on the row says where it
    got to.

    **An empty answer means nobody set the marker.** It does not mean nothing
    went wrong: a slip described in a result or in an unmarked concern is not
    here, and a reader that needs to say so in words should say "none marked".
    """
    return tuple(row for row in board_concerns(snap, session_id) if row.incident)


def _is_gone(snap: Snapshot, node: NodeId) -> bool:
    """A claimer that will not be making further progress without the operator."""
    record = snap.get(node)
    return record is None or record.state.is_terminal
