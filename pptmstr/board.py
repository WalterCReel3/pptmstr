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

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass

from .model import (
    CLOSED_BY_OPERATOR,
    EARLIER_RUN,
    Basis,
    ConcernId,
    ConcernState,
    NodeId,
    Snapshot,
    Task,
    TaskId,
    TaskState,
    is_address_only,
    is_operator,
)
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
# Here and not in the driver, which imports this: the driver pulls in the agent SDK,
# and this module is testable without a runtime.
ROOT_NAMES = (LEAD, "main", "root")


def basis_label(basis: Basis | None) -> str:
    """
    A text's standing as its author gave it, in the words every surface uses.

    **"says", because it is said.** The marker is the author's own account of how
    it knows -- nothing in the system checks whether anything was run -- so it is
    rendered as speech and never as a property of the text. "verified: run" would
    be the board vouching for a claim it only carried.

    **None is named rather than left blank.** An unmarked text beside marked ones
    reads as the settled kind, which is the misreading the marker exists to stop: a
    claim written into a specification looks established whether or not anybody
    established it. So the absence is shown, and it is shown as an absence -- not
    as the weakest member, which would be a standing nobody stated either.

    Here rather than in ``bus.py`` or the pane because both render it, and two
    spellings of one statement would be two boards.
    """
    return "basis unstated" if basis is None else f"says: {basis.value}"


def property_label(text: str) -> str:
    """
    A task's stated property as the line every surface puts under its title, or
    nothing when none was stated.

    Worded as the constraint it is, not as a field name: the reader is a builder
    deciding what it may trade away and a reviewer ranking what it found, and
    "must not break" is the question both are asking of the sentence.
    """
    return f"must not break: {text}" if text else ""


def not_run_label(not_run: str | None) -> str:
    """
    Where a completion stands on what it did not execute, in a few words that
    outlive a cut.

    **None is "unstated", never "nothing".** A result with no list of what was not
    run is the ordinary result, and it reads as verified exactly because nothing
    beside it says otherwise -- which is how artefacts that were written and never
    executed sat behind green test counts. So the absence is named on every
    completed row, and the one spelling that says nothing was left unrun is
    reserved for a claimer that stated it.

    **That statement is rendered as said**, for the reason ``basis_label`` gives:
    nothing here can check it.

    Text is not rendered here. It is prose with no natural bound, so each surface
    lays it out under its own budget and this says only that it is there.
    """
    if not_run is None:
        return "not run: unstated"
    if not not_run:
        return "says nothing was left unrun"
    return "says what was not run"


def incident_marker(incident: bool) -> str:
    """
    What goes ahead of a concern's subject when its sender marked it an incident,
    and nothing when it did not.

    Nothing rather than a "no incident" label, and the asymmetry is deliberate.
    The marker is something a sender sets; its absence is a sender that did not
    set it, which is most of them, including ones describing a slip in prose. A
    label on every unmarked concern would have the board saying of each that
    nothing went wrong, on no evidence.
    """
    return "INCIDENT " if incident else ""


# Who replaced a specification when no agent did, and who sent a concern the
# store filed on the operator's action (``model.operator_party``).
OPERATOR = "the operator"


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


def version_label(row: BoardTask) -> str:
    """
    Which specification a row is showing and who made it so, or nothing for a
    task still on the one it was declared with.

    Silent at version one because that is nearly every row, and a board that
    numbered them all would bury the few whose specification moved. Here, beside
    ``basis_label``, for its reason: the bus tool and the pane both say this.
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
    session. ``store._pick_claim`` and the restore both keep a task among its
    own session's agents, so a node from elsewhere on this board is a snapshot
    the store did not build -- and it would otherwise render as this team's own
    member. "lead" is the worst case: it is the one name an operator would never
    think to doubt.

    An agent of an earlier run of the session is ``builder-2, from an earlier
    run`` and never ``builder-2`` (``Snapshot.of_earlier_run``), whether a
    restored board names it or this process still has its record. The
    qualifier says the claim or the mail is not waiting on anything that is
    running, and it comes off the moment that agent is started again.
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
    if is_operator(node):
        # The sender of the store's notice to a holder whose task the operator
        # closed (``model.operator_party``). It has no record and no address by
        # design, and a restored board names it like any other party, so every
        # test below would call it an agent nobody can name, of an earlier run.
        return OPERATOR
    address = address_in_session(snap, node) or UNKNOWN
    return f"{address}, {EARLIER_RUN}" if snap.of_earlier_run(node) else address


def address_in_session(snap: Snapshot, node: NodeId) -> str:
    """
    The address ``node`` has inside its own session and nothing after it, or the
    empty string when the snapshot has no name for it.

    What ``role_name`` qualifies and what a log record is written with
    (``boardfacts``): a qualifier belongs to a reading of the board at one
    moment, and an address is the name.

    A node with no record has the address its session's restored board had for
    it, when one names it.
    """
    if node[1] is None:
        return LEAD
    record = snap.get(node)
    if record is None:
        return snap.left_behind(node) or ""
    if not record.agent_type:
        # Deliberately not `record.task`: a sub-agent's task is a prompt, and the
        # rail's `agent_type or task` fallback works only because a sub-agent
        # always has a type. A record without one is not worth a paragraph here.
        return node[1]
    return _addresses(snap, node[0]).get(node, record.agent_type.lower())


def address_for(
    agent_id: str,
    agent_type: str,
    held: Mapping[str, str | None],
    declared: Collection[str],
) -> str:
    """
    The address a sub-agent answers to. **The one place that is decided.**

    ``held`` is every address of the session that is spoken for, with the agent
    id it belongs to: the ones allocated so far in this process, and the ones an
    earlier process's log names (``reserved``). The driver asks this when a
    sub-agent starts and routes mail by the answer; ``_addresses`` asks it once
    per record in spawn order and that is what every board read, the pane and
    the log's records show. Two callers of one function over the same table and
    the same order cannot name an agent differently, and neither has a rule of
    its own to drift.

    An id ``held`` already names gets that address back, wherever in the order it
    starts: a sub-agent woken under its id keeps its name, in this process or in
    one that restored the log.

    Otherwise the first agent of a type takes the bare role name and later ones
    ``builder-2``, ``builder-3`` -- the convention ``templates.lead_briefing``
    hands the lead. A candidate that is in ``held``, that is one of the root's
    names, or that another role in the template is called (``declared``) is
    stepped over: an address in ``held`` under another id, or under none, is
    never handed out again.
    """
    for address, holder in held.items():
        if holder == agent_id:
            return address
    base = agent_type.strip().lower() or "agent"
    ordinal = 1
    while True:
        candidate = base if ordinal == 1 else f"{base}-{ordinal}"
        spoken_for = (
            candidate in held
            or candidate in ROOT_NAMES
            or (candidate != base and candidate in declared)
        )
        if not spoken_for:
            return candidate
        ordinal += 1


def reserved(named: Iterable[tuple[str, str | None]]) -> dict[str, str | None]:
    """
    The table ``address_for`` is given, from ``(address, agent id)`` pairs in any
    order: each address once, as an agent types it, with the one agent id it
    belongs to.

    The id is None where no pair gives one, and **None where two pairs give
    different ones**. An address two agents were written under belongs to
    neither as far as anything here can tell, so neither is handed it back and
    nobody new takes it. Deciding by which pair came first would make the answer
    depend on the order of the source, and the two sources of one session's
    table -- a log's ``Board.addresses`` and the ``Restore.parties`` the store
    made of the same log -- do not share an order.

    An empty address and the root's names are left out: nothing is ever
    allocated either, so neither is a thing to reserve.
    """
    ids: dict[str, set[str]] = {}
    for address, agent_id in named:
        key = address.strip().lower()
        if not key or key in ROOT_NAMES:
            continue
        known = ids.setdefault(key, set())
        if agent_id is not None:
            known.add(agent_id)
    return {key: next(iter(known)) if len(known) == 1 else None for key, known in ids.items()}


def restored_table(snap: Snapshot, session_id: str) -> dict[str, str | None]:
    """
    The addresses ``session_id``'s restored board names, as ``reserved`` makes a
    table of them: what ``_addresses`` starts from. A party the log knew by
    address alone has a stand-in for a node and no agent id.

    Equal to what ``app._addresses_of`` makes of the log the restore came from,
    which is what the session's driver starts from. That is two derivations of
    one table and it is pinned by test, not by construction: the store builds
    ``Restore.parties`` from the log and this reads it back.
    """
    restore = snap.restores.get(session_id)
    return reserved(
        (address, None if is_address_only(node) else node[1])
        for node, address in (() if restore is None else restore.parties.items())
    )


def _addresses(snap: Snapshot, session_id: str) -> dict[NodeId, str]:
    """
    Every sub-agent of one session that has a record, under the address the bus
    routes to it.

    Derived, not stored: the driver allocates an address per sub-agent in spawn
    order with ``address_for``, and this asks ``address_for`` again in the order
    the snapshot holds the records (``Snapshot.subagents_of``), starting from the
    table the driver started from -- the addresses the session's restored board
    names (``Restore.parties``; ``app._addresses_of`` builds the driver's from
    the same log).

    **Where it cannot agree**, because the two tables or the two orders differ:

    - ``AgentRemoved`` prunes a record; the driver's table keeps the address, and
      every later ordinal here would shift. Nothing emits that intent (its
      docstring asks that it stay that way).
    - A restore the store refused whole leaves ``Restore.parties`` without the
      log's addresses, while the driver was constructed with them before the
      store had answered.
    - A session relaunched over a run that is still ending copies that run's
      table when it is constructed. A sub-agent the ending run starts after
      that has a record here and no entry there.

    The fix for all three is the address moving onto the record, carried by
    ``AgentSpawned``, instead of being recomputed from what the snapshot holds.
    """
    # ``address_for``'s last rule -- a declared role's name is stepped over --
    # needs the template, which the root record carries by name.
    root = (session_id, None)
    root_record = snap.get(root)
    template = by_name(root_record.template or "") if root_record is not None else None
    declared = frozenset(template.role_names()) if template is not None else frozenset()

    held = restored_table(snap, session_id)
    out: dict[NodeId, str] = {}
    for record in snap.subagents_of(root):
        agent_id = record.node_id[1]
        if agent_id is None or not record.agent_type:
            continue
        address = address_for(agent_id, record.agent_type, held, declared)
        held[address] = agent_id
        out[record.node_id] = address
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

    Scoped by ``Task.belongs_to``, which is the same predicate ``store._pick_claim``
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

    **The row's copy of ``store._may_reclaim``**, asked for the root of
    ``session_id``: true means that root's ``release_task`` on this row is one
    the store takes, and the bus offers it on nothing else. Kept in step by a
    test that makes the release for each kind of holder, because the store
    cannot import this module and a second spelling of a rule drifts.

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
        if c.sender[0] == session_id
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

    A task the operator closed is not on it. The ledger is of work somebody
    reported and nobody said was run, and a closed row reports none: it says so
    itself (``closed_label``), which is a stronger statement than "unstated".
    """
    return tuple(
        row
        for row in board_tasks(snap, session_id)
        if row.state is TaskState.COMPLETED and row.not_run != "" and not row.closed_by_operator
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
