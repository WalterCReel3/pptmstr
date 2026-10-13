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

from .approval import Policy
from .boardlog import Board, Clock
from .command import Command, CommandId, End
from .model import (
    AgentState,
    Basis,
    Concern,
    ConcernId,
    ContextSnapshot,
    Decision,
    DecisionId,
    NodeId,
    PendingApproval,
    Recalled,
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
    # The base ``cwd``'s writes are measured from. None means "inherit from the
    # parent", for the same reason and by the same resolution as ``cwd`` above: the
    # two travel together, and a sub-agent measured against a different base than the
    # session it runs inside would report divergences that are only a change of ruler.
    session_base: str | None = None
    # Which allowlist the gate measures this node's calls against. None means
    # "inherit from the parent", resolved in the store by the same rule as ``cwd``
    # and ``session_base`` above -- and here the inheritance is not a convenience but a
    # requirement, because ``driver._policy_for`` already classifies a sub-agent's
    # calls under its session's policy. A sub-agent's record showing STRICT under an
    # under-gated session would be a reassurance the gate does not honour.
    policy: Policy | None = None
    # Supplied by the spawner rather than created by the store. The driver writes
    # into it directly from the asyncio thread (I7), so both sides must hold the
    # same object -- letting the store build its own would give the UI an empty
    # buffer while the driver filled an orphan.
    transcript: Transcript | None = None
    # On a root announce: ``task`` is the CLI's label for a conversation whose
    # original task nothing recorded, and not a prompt
    # (``AgentRecord.task_is_label``).
    task_is_label: bool = False


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

    **Nothing may emit it for a node a restored board names.** "Gone" is read as
    "named by its session's restore and without a record in this process"
    (``Snapshot.left_behind``), which means "never had one here" only while no
    record is taken away. Removing a restored party that has since been woken
    and has finished its turn makes it gone again, and ``store._may_reclaim``
    then hands its claim to the session's root -- reclaim from an agent that
    merely finished, which that function rules out. An emitter has to settle
    that first.
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
    A task's specification was replaced after it was declared, by the operator or
    by the agent that declared it.

    **A different operation from a declaration, which is why it is a different
    intent.** ``TaskDeclared`` ignores a re-declared id outright, and that guard is
    correct: the only way a repeat declaration happens is a retry, and overwriting
    would silently unclaim work somebody is doing. Weakening it to make room for
    amendment would trade a live defect for a worse one. An amendment targets a task
    that is *expected* to exist and *expected* to be claimed, which is the opposite
    precondition, so it gets its own arm and leaves that guard alone.

    **Who may.** ``node_id`` None marks it as the operator's, the way
    ``ConcernEdited`` does, and the operator may always. An agent may only when it
    declared the task and has never held it -- ``store._amender_refusal`` is the
    whole rule. The point of a specification is that one participant sets it and
    another is bound by it, and an agent that could rewrite the one it is working
    to could rewrite the thing it is being held to. The declarer that keeps its
    hands off the task is the participant that set it, so its amendment is the
    setter correcting itself; the holder's would be the bound party moving the
    bar. "Has never held it" and not "does not hold it now", because the second is
    a check of one instant that a release immediately before the amendment and a
    claim immediately after it walk straight through. The same rule is kept from
    the other side by the claim: an agent that amended a task may not then take
    it (``store._claim_refusal``). So for the life of a task an agent sets its
    specification or works to it, and the only agent that does both is one that
    declared a task and claimed it as declared.

    The operator has not ruled on the agent half of that: it is question 2 of
    `planning/2026-10-12-session-control-and-the-inter-team-record-in-one-sequence.md`,
    built as the lead recommended there. Answering it "no" is one line of
    ``store._amender_refusal``.

    **An amendment is a version.** The text it replaces moves to
    ``Task.superseded`` with who replaced it and when; nothing is overwritten. An
    amendment that changes neither the text nor the property is not one: it adds
    no version, and replies as a success because the board says what was asked.
    Either may be left out, and the one left out stands.

    Amending a task that does not exist changes nothing, like every other arm.
    What it must never do is *create* one -- a typo in a task id would otherwise
    put an unclaimable, undeclared task on the board with no title.

    **Necessary and not sufficient.** ``claim_task`` copies ``detail`` into the
    worker's context once, at claim time, so an amendment applied afterwards
    changes the board and by itself reaches nobody: this arm tells no one. The
    worker mid-task is the one reader who must see it and the one reader who has
    already read. What the arm leaves alone is what makes that visible: the claim
    recorded the version it handed over (``Task.claimed_version``) and an
    amendment does not move it, so the holder is behind by a number anything can
    read. ``bus.py`` is what closes the gap, without turning work away. A
    holder's ``read_task`` carries the current text, and when the reply carries
    it whole it emits ``TaskVersionHanded``, which is how a later handing-over
    is recorded. A holder's ``complete_task`` lands at once whatever version the
    board is on: the result is recorded against the version its author was last
    handed, the board shows that beside it, and the reply shows the holder the
    current text. That reply emits nothing, since a finished task has no holder
    to hand a version to.
    """

    task_id: TaskId
    # The whole specification as it should now read, or None to leave the text
    # standing. None and not "send back what is there": the sender's copy is
    # only as recent as its last read, and a text that landed since would be
    # put back to that copy by an amendment that meant to change the property.
    detail: str | None
    node_id: NodeId | None = None
    # The property the amended task may not break, or None to leave the declared
    # one standing. Optional because the two are amended separately: an amender
    # correcting a paragraph of ``detail`` has not thereby withdrawn the sentence
    # the work is held to, and an empty string is how they do withdraw it.
    property: str | None = None
    # None when nobody is waiting to be told, as on ``TaskDeclared``: the
    # operator's editor emits and moves on.
    request_id: str | None = None
    # The emitter's clock, for the reason ``TaskReleased.at`` gives. None takes
    # the frame's.
    at: float | None = None
    # How the amender says it knows what the new ``detail`` asserts. It stands
    # only beside text the amendment actually replaced.
    basis: Basis | None = None


@dataclass(frozen=True, slots=True)
class TaskVersionHanded:
    """
    The agent holding a task was shown a version of its specification.

    The claim is the first time that happens and stamps it itself. This is every
    later time: a specification replaced under a holder reaches it only when
    something puts the new text in front of it, and whatever does so says which
    version that was. It moves ``Task.claimed_version``, and through it the
    version a result is recorded as written against.

    ``version`` is what was shown, stated by the emitter, and not "whatever is
    current when this is applied": a second amendment can land between the showing
    and this intent, and crediting the holder with that one is the false statement
    the stamp exists to prevent.

    A record of something that happened, with nobody waiting on it, so there is no
    reply. It changes nothing unless ``node_id`` holds the task open and
    ``version`` is one the task has had -- at least 1, at most its current one --
    and not earlier than the one already recorded: a holder is not un-shown a
    text.

    ``bus.py`` emits it from one reply: ``read_task`` by the holder, when the
    reply put the current specification in front of it whole. A text too long
    for one reply to carry whole is shown and not recorded as shown.
    ``complete_task`` does not emit it: a completion lands against the version
    the holder was already handed.
    """

    node_id: NodeId  # the holder
    task_id: TaskId
    version: int


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

    ``result`` is the claimer's account of the work, and it lands on the task with
    the state change or not at all: a refused completion leaves no result behind,
    because the node that sent it does not hold the task it is describing. Empty
    means the claimer gave none.

    ``basis`` is how the claimer says it knows what ``result`` asserts, and it
    travels with the result or not at all: the store keeps it only beside a
    result, since a standing with no text to qualify is a claim about nothing.

    ``not_run`` is what the claimer says the completion did not execute, in the
    three values ``model.Task.not_run`` documents: None for unsaid, text, or the
    empty string for a stated "nothing was left unrun". The store keeps the empty
    statement only beside a result, for the reason it keeps ``basis`` only there.
    """

    node_id: NodeId
    task_id: TaskId
    at: float
    request_id: str | None = None
    result: str = ""
    basis: Basis | None = None
    not_run: str | None = None


@dataclass(frozen=True, slots=True)
class TaskReleased:
    """
    A claimer gave the work back, and it returns to the pool.

    Distinct from completion so that a worker which cannot proceed does not have to
    lie about the outcome to unblock a teammate.

    Guarded on the claimer as ``TaskCompleted`` is, and answered the same way: a
    release the store refused must not read as one it accepted.

    One release is taken from a node that is not the claimer: a session's root
    may give back a task whose holder is an agent of an earlier run of the
    session (``store._may_reclaim``). That is a release only --
    the same root's ``TaskCompleted`` for that task is refused like anybody's.
    """

    node_id: NodeId
    task_id: TaskId
    request_id: str | None = None
    # When the claimer gave it back, on the emitter's clock like a completion's
    # ``at``. None for a caller with no clock to offer -- the fake driver, a test --
    # and the store then stamps the release with the frame it is applied in.
    at: float | None = None


@dataclass(frozen=True, slots=True)
class TaskClosed:
    """
    The operator closed a task: stop waiting for this.

    **Not a claim that the work was done.** The task becomes COMPLETED so that
    what it was holding up is let go -- every task that depends on it becomes
    claimable, and the files it declared stop queueing later tasks behind it
    (``store._auto_depends`` sequences only against unfinished tasks). That is
    the whole purpose: it is the way out of a task nothing else can move -- one
    whose only willing agent amended it, one waiting on an id another session's
    board holds, one whose holder will not finish. What it reports is written
    by the store and by nobody else: ``model.closing_result``, the words
    "closed by the operator" and then ``reason`` exactly as typed. No basis and
    no statement of what was run, because there is no account of work here, and
    ``Task.closed_by_operator`` so that no surface has to read the label out of
    text an agent could have typed.

    **Attributed to the operator and never to a holder.** A task closed while an
    agent held it loses that holder: the row is nobody's completion, and the
    agent's own ``complete_task`` or ``release_task`` afterwards is refused as
    closed by the operator (``TaskRefusal.CLOSED_BY_OPERATOR``), its result not
    kept. ``Task.held_by`` still says who had claimed it.

    **The holder is told and is not stopped.** The arm files a concern from the
    operator to it (``model.closing_notice``), carrying the reason, which it
    reads at its next ``read_inbox`` like any other mail and which is on the
    board and in the log. Nothing here interrupts an agent, so between the close
    and that read the holder may still be writing files this has let go to the
    next task: closing a held task releases the one thing that kept two agents
    out of a file, and the notice is what asks the first to stop.

    ``reason`` is bounded by ``model.MAX_CLOSE_REASON_CHARS``. A close carrying a
    longer one changes nothing, and nothing cuts it.

    **The operator's alone, and by construction.** ``node_id`` is None for every
    instance and is not a field: there is no way to build one that names an
    agent, so no bus tool can be made to emit one on an agent's behalf.

    Taken in any unfinished state. On a finished task, or an id nothing has, it
    changes nothing -- which includes a second close, so the first reason stands.
    """

    task_id: TaskId
    # Stored as typed, behind the label: not stripped, not put on one line.
    reason: str = ""
    # The emitter's clock, for the reason ``TaskReleased.at`` gives. None takes
    # the frame's.
    at: float | None = None

    @property
    def node_id(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class DigestRead:
    """
    An agent asked for the digest of its session's round. The reply is a
    ``DigestDelivered`` effect, and the asker's mark moves to ``at``.

    A read, a mark and a question at once, which is ``InboxRead``'s shape and is
    one intent for ``InboxRead``'s reason: split into a query and a later mark,
    there is a window in which the asker has been handed a round the store still
    counts as unread, and whatever landed in that window is reported twice or --
    with the mark taken first -- not at all.

    The round runs from the asker's previous mark, which the reducer reads off
    its record. No ``since`` argument: one supplied by the caller would be one the
    model chose, and a model that picked a recent point would be handed a digest
    with last round's incidents filed as old.

    ``at`` is the emitter's clock, as every stamp it will be compared with is.
    """

    node_id: NodeId
    request_id: str
    at: float


@dataclass(frozen=True, slots=True)
class BoardRestored:
    """
    A session's board, as its log describes it, is handed to the store.

    The shell reads the log off the frame (``boardsync.load``) and emits this
    before the session it is for can emit anything, so the queue's order is what
    puts a resumed session's board in the store ahead of its first bus call.

    ``board`` is ``boardlog.restore_log``'s value, untranslated: parties are
    still a node or a bare address, texts' bases are still text, and every time
    is on the wall clock. Translating it is the reducer's, because what a party
    with no id becomes and whether a task's id is free are questions about the
    snapshot. ``clock`` is the one reading of both clocks that translation needs,
    taken when the log was read; the reducer reads no clock of its own.

    It is not an agent's to send: nothing on the bus builds one, and what it
    carries is applied only where ``store``'s arm finds it could have come from
    this session's reducer. What became of it is ``Snapshot.restores`` -- there
    is no reply, since nobody is parked on it.

    Yields no board record (``boardfacts.outcomes``). A restored board came out
    of the log, and deriving records from it would append the log to itself.
    """

    session_id: str
    board: Board
    clock: Clock
    # No node: like an operator's edit, this is nothing an agent did.
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class DecisionRaised:
    """
    An agent put a decision in front of the operator.

    The handler builds ``decision`` and the store does not take its word for it.
    The arm stamps the asker from ``node_id`` -- the sender the gate
    authenticated -- and the instant, and blanks the outcome and the delivery
    stamp, so a decision cannot arrive already answered. An id the store already
    holds is refused and the record it names is left as it is: with ids minted by
    the handler only a retry reaches that, and replacing would let a second raise
    rewrite a question the operator has answered.

    Refused as well when ``blocks`` names something that is not a task on the
    asker's board, when the asker already has
    ``model.MAX_OPEN_DECISIONS_PER_ASKER`` open, and -- for a sub-agent only --
    when the sub-agents of its session have ``model.MAX_OPEN_SUBAGENT_DECISIONS``
    open between them. The reply is a ``DecisionSettled`` effect.
    """

    node_id: NodeId  # the asker
    decision: Decision
    # None when nobody is waiting to be told, as on ``TaskDeclared``.
    request_id: str | None = None
    # The emitter's clock, for the reason ``TaskReleased.at`` gives. None takes
    # the frame's.
    at: float | None = None


@dataclass(frozen=True, slots=True)
class DecisionAnswered:
    """
    The operator answered a decision.

    **The operator's alone.** ``node_id`` None marks it as theirs, the way
    ``ConcernEdited`` does, and the arm applies nothing that names a node: an
    answer is the one field of a decision whose whole value is who wrote it.

    ``answer`` is stored as it is given -- not stripped, not cut, not put on one
    line. Applied to a decision that has already ended it changes nothing, so the
    first answer stands.
    """

    decision_id: DecisionId
    answer: str
    node_id: NodeId | None = None
    at: float | None = None


@dataclass(frozen=True, slots=True)
class DecisionDismissed:
    """
    The operator closed a decision without answering it.

    The way out for a decision whose asker will never read an answer -- stopped,
    failed, or closed with its session -- and for one the operator declines to
    take. The operator's alone, as ``DecisionAnswered`` is, and it records no
    answer.
    """

    decision_id: DecisionId
    node_id: NodeId | None = None
    at: float | None = None


@dataclass(frozen=True, slots=True)
class DecisionWithdrawn:
    """
    The agent that raised a decision took it back.

    Applied only for the asker and only while the decision is open: an agent
    cannot close another agent's decision, and cannot retract one the operator
    has answered. It records no answer. The reply is a ``DecisionSettled``
    effect.
    """

    node_id: NodeId  # the asker
    decision_id: DecisionId
    request_id: str | None = None
    at: float | None = None


@dataclass(frozen=True, slots=True)
class DecisionsRead:
    """
    An agent asked what has become of the decisions it may read. The reply is a
    ``DecisionsDelivered`` effect holding every one of them, ended or not: its
    own, and for the root of a session every decision raised in that session
    (``Snapshot.decisions_readable_by``).

    A read, a mark and a question at once, for ``InboxRead``'s reason. The mark
    is ``Decision.first_delivered_at``, and it is set on ``decision_id`` alone:
    the one decision whose answer the reply will carry, and only when the reader
    is the agent that raised it and the operator has answered or dismissed it.
    A read that names none is a list, which shows no answer and marks nothing;
    the root reading another agent's decision marks nothing either. It is set
    once and gates nothing: the same answer is in the reply to every later read.

    ``node_id`` is the gate's, so no argument an agent writes widens what it is
    shown. ``decision_id`` is the agent's, and names only which of those to mark.
    """

    node_id: NodeId  # the reader
    request_id: str
    at: float
    # The decision the reply carries whole, or None for a list of them.
    decision_id: DecisionId | None = None


@dataclass(frozen=True, slots=True)
class CommandRequested:
    """
    An agent asked the operator to run a command.

    The handler builds ``command`` and the store does not take its word for it.
    The arm keeps the id and the three texts and builds the record again: the
    asker is ``node_id``, the sender the gate authenticated, the instant is
    ``at``, and there is no run, no stop, no end and no delivery stamp, so a
    command cannot arrive approved or already run.

    Refused: an id that is not a command id (it names a directory); an id the
    store already holds, which is left as it stands; a string or a text
    ``command.request_refusal`` refuses -- the handler asks that too, and the
    store is the authority; ``command.MAX_OPEN_PER_ASKER`` open for the asker;
    and, for a sub-agent only, ``command.MAX_OPEN_SUBAGENTS`` open between the
    sub-agents of its session. The reply is a ``CommandSettled`` effect.

    **``at`` is the wall clock** (``time.time``) on every command intent, and
    has no default: these records are written to files beside the runner's own
    stamps, and the frame's clock is monotonic.
    """

    node_id: NodeId  # the asker
    command: Command
    # None when nobody is waiting to be told, as on ``TaskDeclared``.
    request_id: str | None
    at: float


@dataclass(frozen=True, slots=True)
class CommandApproved:
    """
    The operator said: run this.

    **The operator's alone.** ``node_id`` None marks it as theirs and the arm
    applies nothing that names a node.

    **``text`` is what runs**, and nothing else is: the arm never reads the
    string the agent asked for. It is the operator's edit or the asked string
    as the row sent it, and it is held to ``command.command_refusal`` either
    way. ``shell``, ``cwd`` and ``timeout_s`` are the shell's reading of the
    machine and the operator's pick; the timeout must be one of
    ``command.TIMEOUT_CHOICES_S``.

    Applied only to a command that is still only asked, so at most once, and
    not while another command of the same session is running
    (``Snapshot.command_approval_refusal`` is the whole test, and the surface
    asks it before sending). Applied, it records the ``Run`` and orders it with
    an ``effects.CommandToRun``; otherwise it changes nothing and orders
    nothing.
    """

    command_id: CommandId
    text: str
    shell: str
    cwd: str
    timeout_s: float
    at: float
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class CommandRefused:
    """
    The operator refused a command, with a reason the asker reads back.

    The operator's alone, and only for a command that is still only asked: a
    running one is stopped, not refused. ``reason`` is stored as it is given and
    may be empty; one over ``model.MAX_COMMAND_REFUSAL_CHARS`` is not applied
    and not cut.
    """

    command_id: CommandId
    reason: str
    at: float
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class CommandWithdrawn:
    """
    The agent that asked for a command took it back.

    Applied only for the asker and only while the command is still only asked:
    an agent cannot close another agent's request, and cannot retract one the
    operator has approved -- that one is running. The reply is a
    ``CommandSettled`` effect.
    """

    node_id: NodeId  # the asker
    command_id: CommandId
    request_id: str | None
    at: float


@dataclass(frozen=True, slots=True)
class CommandStopAsked:
    """
    The operator asked for a running command to be killed.

    The operator's alone, and only for a running command. The first one stamps
    ``Command.stop_asked_at``; every one orders the kill with an
    ``effects.CommandToStop``, which is safe to repeat. It ends nothing: the end
    is the runner's to report (``CommandEnded``).
    """

    command_id: CommandId
    at: float
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class CommandEnded:
    """
    A command the operator approved has ended, and ``end`` is how.

    **Not an agent's to send.** The shell emits it from the runner's report,
    with no node, and the arm applies nothing that names one.

    Applied only to a running command -- one with a run and no end -- and so
    once: a second changes nothing. ``NotStarted`` is among the ends it may
    carry, for an approved command no process came of. An ``end`` that cannot
    close an approved command (``command.precludes_run``) is not applied.

    The one exception to "once" is a command left running unsupervised
    (``Command.unsupervised``), which has an end already, an ``EndUnknown``
    that names what was found alive. For that command an ``EndUnknown`` is
    applied in its place: the shell looked again, and the new one names what is
    there now, or nothing. No other end replaces one.
    """

    command_id: CommandId
    end: End
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class CommandsRead:
    """
    An agent asked what has become of the commands it may read. The reply is an
    ``effects.CommandsDelivered`` holding every one of them, ended or not: its
    own, and for the root of a session every command asked in that session
    (``Snapshot.commands_readable_by``).

    A read, a mark and a question at once, as ``DecisionsRead`` is. The mark is
    ``Command.first_delivered_at``, set once, on ``command_id`` alone and only
    when the reader asked for that command and it has ended: the one reply the
    result is in. A read that names none is a list and marks nothing, and the
    root reading another agent's command marks nothing either.

    ``node_id`` is the gate's, so no argument an agent writes widens what it is
    shown.
    """

    node_id: NodeId  # the reader
    request_id: str
    at: float
    # The command the reply carries whole, or None for a list of them.
    command_id: CommandId | None = None


@dataclass(frozen=True, slots=True)
class CommandsRestored:
    """
    The commands recorded in a session's files, read at startup
    (``commandfile.restore``), are put where their asker and the operator can
    read them.

    **Nothing open comes out of a file.** A batch holding a command that has
    not ended, one asked in a session other than ``session_id``, or one whose
    id is not a command id, is not ``restore``'s and none of it is applied. A
    command the store already holds is left as the store has it.

    The shell's, with no node; the arm applies nothing that names one. Carries
    no time: every stamp on these records is the file's.
    """

    session_id: str
    commands: tuple[Command, ...]
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class SessionRecalled:
    """
    A session's file was read at startup, and the session is put on the fleet
    as a closed card: a root record with no process behind it.

    **It never replaces anything.** The store builds the record only when it
    holds no record at all under ``session_id`` and no open decision asked under
    it; otherwise this is a no-op. A recall is read off the frame and can land
    after the same session was launched in this window, and a record of
    something that ran here is worth more than a file's account of it.

    Everything it carries is what the file recorded. ``recalled`` holds what has
    no field on a record or would lie in one -- the wall-clock times, how it
    ended, the gate as granted, the cost estimate (``model.Recalled``); the rest
    are the record's own launch facts, None or empty where the file has none.
    The record is DONE whatever ``recalled.ended_how`` says: a session that had
    failed comes back owing nothing.

    No time is carried for the record's own stamps. They are monotonic and this
    process observed no instant of the session's.

    Yields no board record (``boardfacts.outcomes``), and reads no board log:
    the board comes back when the session is resumed.
    """

    session_id: str
    task: str
    model: str
    recalled: Recalled
    cwd: str | None = None
    session_base: str | None = None
    template: str | None = None
    brief: str | None = None
    # No node: nothing an agent did, and the record it makes carries no stamp of
    # this process's clock.
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class SessionDetached:
    """
    The operator took a session off the fleet.

    Honoured only where ``Snapshot.detach_refusal`` gives none: the session's
    root is in a final state (``AgentState.is_final``), every sub-agent of it
    is terminal, and nothing of it is owed. Answered either way with an
    ``effects.SessionDetachSettled`` saying which.

    Terminal is enough for a sub-agent and not for the root. A sub-agent acts
    only through its session's run, so one left FAILED cannot come back while
    the root's run is over; a FAILED root can, on its next message, with its
    client still connected.

    **That is the store's half, and it is not enough on its own.** A snapshot
    does not say whether the pool still has a run under the id. The pool keeps a
    session whose run ended by itself until it is closed or replaced, and a
    launch over a live id reports the old run's end after the new one is
    announced. Whatever emits this asks the pool first and emits nothing while a
    run exists.

    Nothing is removed: the records, the board and its task ids stay in the
    store. The session comes back on the fleet when its root is announced again,
    and on any step after which the rule above stops holding: an agent of it is
    running again, or something of it is owed.
    """

    session_id: str
    # Echoed on the answer. Empty when nobody is parked on it, which is every
    # caller in the window: the app reads the answer by its type.
    request_id: str = ""
    # No node: an operator's act.
    node_id: NodeId | None = None


@dataclass(frozen=True, slots=True)
class FailedSessionClosed:
    """
    The operator closed a session that had failed and has no run left to close.

    FAILED is terminal and not final: the driver reports an error result with
    its client still connected, and the session's next message makes it an
    ordinary session again, so the store keeps a failed root revivable. A root
    whose run then died, or was never connected, stays in that state for good --
    ``SessionPool.close`` on a session with no run emits nothing -- and a root
    that is not final can never leave the fleet. This is what says that one is
    over: the root becomes DONE and its failure is acknowledged, in one step.

    **Whether a run is left is the shell's to say, and it has to say it.**
    ``run_held`` is what the pool answered when it was asked
    (``SessionPool.has_run``), and it has no default: with a run held this is a
    no-op, and closing that session is the pool's to do, which ends the run and
    reports the end itself. A no-op as well unless the node is a root in FAILED.

    Names the root, as ``FailureAcknowledged`` does: an operator's act, and it
    changes that node's state, which is stamped from the intent's node.
    """

    node_id: NodeId
    run_held: bool


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
    | TaskVersionHanded
    | TaskDeclared
    | TaskClaimRequested
    | TaskCompleted
    | TaskReleased
    | TaskClosed
    | BoardRead
    | DigestRead
    | BoardRestored
    | DecisionRaised
    | DecisionAnswered
    | DecisionDismissed
    | DecisionWithdrawn
    | DecisionsRead
    | CommandRequested
    | CommandApproved
    | CommandRefused
    | CommandWithdrawn
    | CommandStopAsked
    | CommandEnded
    | CommandsRead
    | CommandsRestored
    | SessionRecalled
    | SessionDetached
    | FailedSessionClosed
)
