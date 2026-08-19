"""
Intents: the only way the world changes (I4).

Every mutation is a value. Nothing calls a setter on the store; something appends
one of these and the store applies it between frames. That buys three things worth
the indirection: mutation stays off the frame-build path, every state change is a
loggable object, and the store has exactly one writer.

**Single-writer resolution.** The design sketch is ambiguous about who mutates the
store -- §3's diagram has the asyncio thread writing to it, while §4.1 has the UI
thread draining the queue and applying. Doing both would be a data race on the node
table dressed up as an architecture. This implementation settles it:

    the main/UI thread is the sole writer of the store; the asyncio thread only
    ever enqueues intents.

So ``Store`` needs no lock at all -- it is confined to one thread, and the queue is
the only crossing. The approval gate therefore does *not* call ``store.set_pending()``
as the sketch shows; it enqueues ``ApprovalRequested`` and awaits its future.

The one deliberate exception is ``Transcript``, which the asyncio thread writes
directly. It is append-only and internally synchronised precisely so it can sit
outside this scheme (I7) -- routing a token stream through the intent queue would
put one queue item per token on the UI thread's critical path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .model import (
    AgentState,
    Concern,
    ConcernId,
    ContextSnapshot,
    NodeId,
    PendingApproval,
    Task,
    TaskId,
    UsageRollup,
)
from .transcript import Transcript


@dataclass(frozen=True, slots=True)
class AgentSpawned:
    """A session or sub-agent came into existence."""

    node_id: NodeId
    parent: NodeId | None
    task: str
    model: str
    started_at: float
    agent_type: str | None = None
    # The session's work template, by name. Set on a root announce and left None
    # for a sub-agent, which is spawned by the CLI and has no template of its own.
    template: str | None = None
    # The session's brief directory, on a root announce only. Same shape and same
    # reason as ``template``: chosen at launch, implied by nothing in the snapshot,
    # and gone if the announce does not carry it.
    brief: str | None = None
    topic: str = "starting"
    # Where this agent runs. None means "inherit from the parent", which is what a
    # sub-agent does -- it runs in the session's directory and its spawn hook is not
    # told one. The store resolves the inheritance so no emitter has to remember to.
    cwd: str | None = None
    # Supplied by the spawner rather than created by the store. The driver writes
    # into it directly from the asyncio thread (I7), so both sides must hold the
    # same object -- letting the store build its own would give the UI an empty
    # buffer while the driver filled an orphan.
    transcript: Transcript | None = None


@dataclass(frozen=True, slots=True)
class StateChanged:
    node_id: NodeId
    state: AgentState
    # State and topic almost always move together ("now running a tool" / "running
    # pytest"), so they travel as one intent rather than two -- two would let a
    # frame land between them and render a state with the previous topic.
    topic: str | None = None


@dataclass(frozen=True, slots=True)
class TopicChanged:
    """Activity changed without a state transition, or set_topic was called."""

    node_id: NodeId
    topic: str


@dataclass(frozen=True, slots=True)
class UsageAccrued:
    """Token/cost delta from one message. Added to the running total."""

    node_id: NodeId
    delta: UsageRollup


@dataclass(frozen=True, slots=True)
class ContextPolled:
    """Result of a get_context_usage() poll."""

    node_id: NodeId
    context: ContextSnapshot


@dataclass(frozen=True, slots=True)
class CompactionObserved:
    """
    The PreCompact hook fired.

    Carried separately from ContextPolled because it is an *event*, not a
    measurement: the count it increments survives every later poll, and it is the
    strongest signal the UI has that a session should be retired (§2.4).
    """

    node_id: NodeId
    at: float
    trigger: str  # "auto" | "manual"


@dataclass(frozen=True, slots=True)
class ApprovalRequested:
    """
    A tool call is parked. The agent is now blocked until this is resolved.

    The matching Future lives in the Bridge under ``pending.id``; the store only
    learns that something is waiting.
    """

    node_id: NodeId
    pending: PendingApproval


@dataclass(frozen=True, slots=True)
class ApprovalResolved:
    """
    The operator decided. Clears the pending slot; the agent resumes on the
    asyncio side once the Bridge completes the future.
    """

    node_id: NodeId
    pending_id: str
    approved: bool
    reason: str | None = None
    edited_args: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class AgentFinished:
    node_id: NodeId
    state: AgentState
    ended_at: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SubagentProgress:
    """
    A background sub-agent reported what it is doing.

    Carried separately from TopicChanged because it arrives keyed by the spawning
    tool_use_id rather than by agent_id, and the driver resolves that to a node.
    """

    node_id: NodeId
    description: str


@dataclass(frozen=True, slots=True)
class SubagentDelivered:
    """
    A sub-agent stopped, and handed over its whole final answer.

    Separate from ``SubagentProgress`` because the two are different registers, not
    different lengths of the same thing: progress is what a row shows while work
    happens, and this is the thing the work was for. The hook gives both at once --
    ``SubagentProgress`` still carries the one-line topic -- so folding them into one
    intent would make every progress report claim to be an answer.

    The text is not clipped anywhere on the way to the store. ``last_assistant_message``
    on the ``SubagentStop`` hook is the only thing a sub-agent produces that is
    *marked* as its answer -- its ``AssistantMessage``s reach the stream and its own
    transcript, but nothing there says which of them was the deliverable, and no
    ``ResultMessage`` can be attributed to its node at all. Truncating here would
    destroy the only copy the store can hold.
    """

    node_id: NodeId
    text: str


@dataclass(frozen=True, slots=True)
class AgentResumed:
    """
    A node that had finished is running again (§2.3).

    Distinct from ``AgentSpawned`` because the SDK reports both through the same
    hook. ``SubagentStart`` fires a second time for the *same* ``agent_id`` when a
    sibling's ``SendMessage`` wakes a completed sub-agent -- measured, not assumed:
    ``scripts/verify_wake_path.py`` observed the resume 7 seconds after the agent's
    own completion notification, carrying its original id.

    Treating that as a spawn is not a cosmetic error. ``AgentSpawned`` builds a
    fresh ``AgentRecord``, which zeroes the usage rollup, resets ``started_at``, and
    -- worst -- swaps in a new ``Transcript``, orphaning the ``(buffer, length)``
    handle any pane is already reading against (I7). The record must survive its own
    resurrection; only the liveness fields move.
    """

    node_id: NodeId
    at: float
    topic: str = "resumed"


@dataclass(frozen=True, slots=True)
class AgentRemoved:
    """
    Dropped from the tree entirely. Descendants go with it.

    Nothing emits this today, and §2.3 is the reason to keep it that way: a
    finished node can be woken by a sibling, so pruning one whose id another agent
    still holds would leave the resumed work with nowhere to land. The store's
    ``AgentResumed`` arm drops an intent for an unknown node rather than
    resurrecting a record it has no history for.
    """

    node_id: NodeId


# Kept as an explicit union rather than a base class: it gives the store's match
# statement exhaustiveness checking under mypy, so adding an intent without
# handling it is a type error rather than a silently ignored state change.
@dataclass(frozen=True, slots=True)
class FailureAcknowledged:
    """
    The operator has seen a crashed session and cleared it from the inbox.

    A fact about the operator, not about the agent -- but it belongs in the store
    all the same, because ``needs_you`` is built there and every count reads that
    one list. Filtering dismissed failures in the UI instead would leave the status
    bar reporting obligations the inbox no longer shows, which is precisely the
    disagreement this whole projection exists to remove.

    Needed because a failure is the one obligation with no natural resolution. An
    approval is answered and a question is replied to or closed; a crashed session
    has already ended, so without this it would sit in the queue forever.
    """

    node_id: NodeId


# -- the message bus and the task board (§2.7) ------------------------------------
#
# Every intent below carries ``node_id`` because the store stamps ``state_since``
# by looking it up (store.py:318), and an intent that named no node would have to
# be special-cased there. Operator actions have no node to name, so the field is
# optional -- and the store's stamp is guarded on it rather than each new arm being
# trusted to remember.


@dataclass(frozen=True, slots=True)
class ConcernPosted:
    """
    One agent sent another a concern.

    ``concern.sender`` is authoritative and comes from the gate's ``agent_id``, not
    from the tool arguments -- see ``model.Concern``. The driver builds the record;
    the store only files it.
    """

    node_id: NodeId  # the sender
    concern: Concern


@dataclass(frozen=True, slots=True)
class InboxRead:
    """
    A recipient asked for its inbox. Everything waiting for it is delivered, and
    the reply comes back as an ``InboxDelivered`` effect.

    A read, a mutation and a question at once, which is why it is one intent rather
    than a query followed by a ``ConcernDelivered``. Splitting them would open a
    window in which the recipient has been handed concerns the store still shows as
    waiting -- and the asking agent cannot close that window itself, because it
    reads the store from the asyncio thread, which is to say not at all.
    """

    node_id: NodeId  # the recipient
    request_id: str
    at: float


@dataclass(frozen=True, slots=True)
class ConcernEdited:
    """
    The operator changed a concern's text before it was delivered.

    The capability §2.7 exists for. Applying to an already-delivered concern is a
    no-op rather than an error: the recipient has read it, and rewriting history it
    has already acted on would put the store and the transcript in disagreement.
    """

    concern_id: ConcernId
    body: str
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class ConcernWithdrawn:
    """The operator stopped a concern from being delivered."""

    concern_id: ConcernId
    reason: str | None = None
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class TaskAmended:
    """
    The operator changed a task's specification after it was declared.

    **A different operation from a declaration, which is why it is a different
    intent.** ``TaskDeclared`` ignores a re-declared id outright, and that guard is
    correct: the only way a repeat declaration happens is a retry, and overwriting
    would silently unclaim work somebody is doing. Weakening it to make room for
    amendment would trade a live defect for a worse one. An amendment targets a task
    that is *expected* to exist and *expected* to be claimed, which is the opposite
    precondition, so it gets its own arm and leaves that guard alone.

    ``node_id`` is None to mark it as the operator's, the way ``ConcernEdited``
    already does. Nothing else in the system may emit this: an agent that could
    rewrite its own specification could rewrite the thing it is being held to, and
    the point of a spec is that one participant sets it and another is bound by it.

    Amending a task that does not exist is a no-op rather than an error, like every
    other arm. What it must never do is *create* one -- a typo in a task id would
    otherwise put an unclaimable, undeclared task on the board with no title.

    **Necessary and not sufficient, which its own record says plainly.**
    ``claim_task`` copies ``detail`` into the worker's context once, at claim time,
    so a task amended afterwards changes the board and reaches nobody. The worker
    mid-task is the one reader who must see it and the one reader who has already
    read. What closes that is the worker re-reading the board rather than trusting
    its transcript copy -- which `what-the-board-does-not-carry.md` calls the
    structurally stronger option, and which stopped being "a discipline the workers
    do not have" when the board read began carrying ``detail``.
    """

    task_id: TaskId
    detail: str
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class TaskDeclared:
    """
    A unit of work was put on the board.

    Rejected by the store if its dependencies would close a cycle -- see
    ``store._would_cycle``. A cycle is not a wedged task, it is a wedged *team*:
    every member of the cycle is permanently unclaimable, and the symptom is
    workers that idle while the board says there is work.

    ``request_id`` None means nobody is waiting to be told: the fake driver's
    scripted board, a test, an operator action. It is optional rather than required
    for that reason and not as a convenience -- a declaration with no agent parked
    on it is a real case, and the store emits no reply for one.
    """

    task: Task
    node_id: NodeId | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskClaimRequested:
    """
    A worker asked for work. The store decides, because it is the only serialized
    writer and therefore the only place the decision cannot race.

    ``task_id`` None means "whatever is claimable", which is the self-claiming
    model worth copying from agent teams. The answer comes back as a
    ``ClaimSettled`` effect rather than on this intent or on the record it wins:
    ``_apply`` is pure and cannot complete the future the asking agent is parked on.
    """

    node_id: NodeId
    request_id: str
    task_id: TaskId | None = None


@dataclass(frozen=True, slots=True)
class BoardRead:
    """
    An agent asked what is on its board. Nothing changes; the reply is everything.

    A question with no mutation, which makes it the odd one of the five: ``InboxRead``
    is a read *and* a mark, and a claim is a read and a write. This one exists as an
    intent anyway rather than as a direct store call, because the handler runs on the
    asyncio thread and the store is confined to the UI thread -- reading it from a bus
    handler is the thread violation the Bridge exists to make unstateable, and a
    "harmless" read of a snapshot mid-swap is exactly how that starts.

    The board is scoped to ``node_id``'s own session (``Task.belongs_to``), so this
    carries no session argument: one supplied by the caller would be one the model
    chose, and the gate already authenticated which session is asking.
    """

    node_id: NodeId
    request_id: str


@dataclass(frozen=True, slots=True)
class TaskCompleted:
    """
    A claimer finished. Anything depending on this becomes claimable by that fact
    alone.

    Applied only for the node that holds the claim, so a stale completion cannot
    finish a task out from under its new owner. The claimer therefore has to be told
    which way it went -- a ``TaskWriteSettled`` on ``request_id``, when one is given.
    """

    node_id: NodeId
    task_id: TaskId
    at: float
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class TaskReleased:
    """
    A claimer gave the work back, and it returns to the pool.

    Distinct from completion so that a worker which cannot proceed does not have to
    lie about the outcome to unblock a teammate.

    Guarded on the claimer exactly as ``TaskCompleted`` is, and answered the same
    way: a release the store refused must not read as one it accepted.
    """

    node_id: NodeId
    task_id: TaskId
    request_id: str | None = None


Intent = (
    AgentSpawned
    | FailureAcknowledged
    | StateChanged
    | TopicChanged
    | UsageAccrued
    | ContextPolled
    | CompactionObserved
    | ApprovalRequested
    | ApprovalResolved
    | AgentFinished
    | SubagentProgress
    | SubagentDelivered
    | AgentResumed
    | AgentRemoved
    | ConcernPosted
    | InboxRead
    | ConcernEdited
    | ConcernWithdrawn
    | TaskAmended
    | TaskDeclared
    | TaskClaimRequested
    | TaskCompleted
    | TaskReleased
    | BoardRead
)
