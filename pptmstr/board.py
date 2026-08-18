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
here, and ``store._pick_claim`` applies the same predicate (``Task.belongs_to``) so
a worker cannot claim a task this module would not have shown it.

The facts stay in ``model.py``: ``Task.blocked_on`` is the dependency graph's answer
and is derived on every read.

Imports no imgui, so the derivation is testable without a GL context.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import ConcernId, ConcernState, NodeId, Snapshot, TaskId, TaskState
from .templates import SOLO, by_name

# What a node with no record in the snapshot is called.
#
# Two different situations reach this and the snapshot cannot tell them apart: a
# node `AgentRemoved` pruned (which leaves the tasks it claimed behind), and one
# that has not spawned yet (the store files a concern to an unknown recipient
# deliberately -- store.py's ConcernPosted arm). Neither leaves a record, so a
# label reading "gone" or "not started" would assert a cause on a coin flip. It
# says only what is known: nothing here answers to this id.
UNKNOWN = "(unknown)"

# What the root session of a team is called. Not read from the record: the driver
# emits the root's `AgentSpawned` with no `agent_type` at all, so the usual
# `record.agent_type or record.task` idiom would put the operator's entire task
# prompt in a name column. `role_of` answers "lead" for this node, and an agent_id
# of None is the same fact without reaching into the driver.
LEAD = "lead"

# What qualifies a node belonging to a different session than the board showing it.
# Reachable because the task map is fleet-wide and ``store._pick_claim`` applies no
# session filter, so a worker in one session can claim work another declared.
#
# It says "another session" rather than anything suggesting the node is unknown or
# broken, because it is neither: it is a real agent doing real work, under a role
# name that is correct in its own session and misleading in this one.
FOREIGN = "another session"

# The names that mean the root of a session and so are never a sub-agent's address.
# Spelled here rather than imported from the driver, which pulls in the agent SDK and
# would cost this module its "testable without a runtime" property. The duplication is
# pinned: test_board asserts this derivation and the driver's allocation agree over the
# same spawn sequence.
ROOT_NAMES = (LEAD, "main", "root")


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


@dataclass(frozen=True, slots=True)
class BoardConcern:
    """One row of the concern log, delivered ones included: it is the audit trail."""

    id: ConcernId
    sender: str
    recipient: str
    subject: str
    state: ConcernState
    edited: bool
    # The task this concern is about, when the sender named one, and whether that
    # id is on this board. A concern naming a task that was never declared is a
    # typo the store deliberately does not refuse -- the message was still sent --
    # so the projection reports it, in the same shape `missing` reports a
    # dependency that does not exist.
    task_id: TaskId | None
    task_missing: bool


def role_name(snap: Snapshot, node: NodeId, session_id: str) -> str:
    """
    What to call a node on ``session_id``'s board.

    The root of a session is the lead by construction. Anything else renders the
    address its own bus answers to -- ``agent_type`` lowercased for the first agent
    in a role, ``builder-2`` for the second -- so an "Explore" record and the
    "explore" an agent must type to reach it read as one name, and two builders read
    as two.

    ``session_id`` is required rather than inferred because a role name is only
    meaningful relative to a board. Every node here is a role of *its own*
    session, and a claim can cross sessions (``store._pick_claim`` scans the
    fleet-wide map with no session filter), so a node from elsewhere would
    otherwise render as this team's own member. "lead" is the worst case: it is
    the one name an operator would never think to doubt.
    """
    base = _role_within_session(snap, node)
    if node[0] != session_id:
        # Named, not hidden. The row is real and someone is working it; what it
        # must not do is claim to be one of this board's own agents.
        return f"{base}, {FOREIGN}"
    return base


def _role_within_session(snap: Snapshot, node: NodeId) -> str:
    """The name a node answers to inside its own session, ignoring which board asked."""
    if node[1] is None:
        return LEAD
    record = snap.get(node)
    if record is None or not record.agent_type:
        # Deliberately not `record.task`: a sub-agent's task is a prompt, and the
        # rail's `agent_type or task` fallback works only because a sub-agent
        # always has a type. A record without one is not worth a paragraph here.
        return UNKNOWN if record is None else node[1]
    return _addresses(snap, node[0]).get(node, record.agent_type.lower())


def _addresses(snap: Snapshot, session_id: str) -> dict[NodeId, str]:
    """
    Every sub-agent of one session, under the address the bus routes to it.

    Derived, not stored: the driver allocates an address per sub-agent in spawn
    order, and spawn order plus each record's ``agent_type`` is exactly what the
    snapshot already holds (``Snapshot.subagents_of``). A copy on
    ``AgentRecord`` would be a second version of a fact the records already imply.

    It replays ``AgentSession._address_for``: first of a type takes the bare role
    name, later ones take ``-2``, ``-3``, and a candidate a *declared* role already
    answers to is skipped so two agents never share an address. That last rule needs
    the template, which the root record carries by name.

    **Where it cannot agree.** ``AgentRemoved`` prunes a record; the driver's table
    keeps the address. Pruning a sub-agent would shift every later ordinal here and
    nowhere else. Nothing emits that intent today (see its docstring, which asks that
    it stay that way), and if something starts to, the address has to move onto the
    record instead of being recomputed from what survived.
    """
    root = (session_id, None)
    root_record = snap.get(root)
    template = by_name(root_record.template or "") if root_record is not None else None
    declared = frozenset(template.role_names()) if template is not None else frozenset()

    out: dict[NodeId, str] = {}
    taken: set[str] = set()
    for record in snap.subagents_of(root):
        base = (record.agent_type or "").lower()
        if not base:
            continue
        ordinal = 1
        while True:
            candidate = base if ordinal == 1 else f"{base}-{ordinal}"
            spoken_for = (
                candidate in taken
                or candidate in ROOT_NAMES
                or (candidate != base and candidate in declared)
            )
            if not spoken_for:
                break
            ordinal += 1
        taken.add(candidate)
        out[record.node_id] = candidate
    return out


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
    """
    record = snap.get((session_id, None))
    return record is not None and record.template is not None and record.template != SOLO.name


def board_tasks(snap: Snapshot, session_id: str) -> tuple[BoardTask, ...]:
    """
    The tasks declared by one session, oldest first.

    Scoped by ``Task.belongs_to``, which is the same predicate ``store._pick_claim``
    applies -- so a worker is handed only tasks this function would have shown it.
    Ordered by declaration rather than by state so a row does not jump when somebody
    claims it: the board is read while work moves.

    **That symmetry is new and was the open question here.** The filter used to live
    only in this function while the reducer scanned the fleet-wide map, so a worker
    in session B could claim a task session A declared -- the row stayed on A's board
    and appeared on B's never, leaving B's operator watching an agent work on
    something nothing accounted for. Deciding it in the model rather than in this
    pane is what makes the pane and the reducer incapable of disagreeing.

    ``role_name``'s foreign-claimer handling stays, and is now the record of a state
    the store can no longer enter: a task claimed across sessions before the
    predicate existed still renders honestly rather than as one of this board's own
    agents.

    Blocked-ness is derived against the **whole** ``snap.tasks`` map, never
    against the filtered rows. A missing dependency counts as unsatisfied, so
    filtering first would invent a blocker out of a cross-session dependency and
    show a claimable task as stuck.
    """
    explained = _open_concerns_by_task(snap, session_id)
    rows = []
    for task in sorted(snap.tasks.values(), key=lambda t: (t.declared_at, t.id)):
        if not task.belongs_to(session_id):
            continue
        blocked = task.blocked_on(snap.tasks)
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
                missing=tuple(d for d in blocked if d not in snap.tasks),
                touches=task.touches,
                concerns=explained.get(task.id, ()),
            )
        )
    return tuple(rows)


def _open_concerns_by_task(snap: Snapshot, session_id: str) -> dict[TaskId, tuple[ConcernId, ...]]:
    """
    Concerns naming a task, indexed by that task, oldest first.

    Built once per call rather than scanned per row: the alternative is a pass over
    every concern for every task, and this projection is rebuilt every frame.

    Scoped by sender to match ``board_concerns``, so a row's explanation and the
    concern log below it cannot disagree about which messages are this board's.
    """
    index: dict[TaskId, list[ConcernId]] = {}
    for c in sorted(snap.concerns.values(), key=lambda c: (c.posted_at, c.id)):
        if c.task_id is None or c.sender[0] != session_id:
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
    another session cannot arise today (roles resolve within a session) and would
    still belong on its sender's board if it did.
    """
    rows = [
        BoardConcern(
            id=c.id,
            sender=role_name(snap, c.sender, session_id),
            recipient=role_name(snap, c.recipient, session_id),
            subject=c.subject,
            state=c.state,
            edited=c.edited,
            task_id=c.task_id,
            task_missing=c.task_id is not None and c.task_id not in snap.tasks,
        )
        for c in sorted(snap.concerns.values(), key=lambda c: (c.posted_at, c.id))
        if c.sender[0] == session_id
    ]
    return tuple(rows)


def _is_gone(snap: Snapshot, node: NodeId) -> bool:
    """A claimer that will not be making further progress without the operator."""
    record = snap.get(node)
    return record is None or record.state.is_terminal
