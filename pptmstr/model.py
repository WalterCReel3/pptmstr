"""
Immutable records that make up the store's world.

Everything here is frozen. The store never edits a record in place; it builds a
replacement and swaps the reference (I3). That is what makes ``Store.snapshot()``
an attribute read rather than a deep copy, which is the difference between a UI
that can rebuild itself every frame in Python and one that cannot.

No record carries a colour, a widget ID, or a scroll position. Presentation state
lives in the UI layer (design §6); mixing the two is how a store stops being a
single source of truth.
"""

from __future__ import annotations

import dataclasses
import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar

from .transcript import Transcript

# (session_id, agent_id). Root sessions have agent_id None; sub-agents carry the
# agent_id the SDK reports on their tool-lifecycle hooks. The SDK's own note is
# that parallel sub-agents interleave over one control channel and agent_id is
# the only reliable way to attribute a hook to the right one -- so this pair is
# identity, and it is also the basis for every ImGui widget key (I6). Never a
# list index, which reorders.
NodeId = tuple[str, str | None]


class AgentState(enum.Enum):
    """
    Where an agent is in its lifecycle.

    SPAWNING -> THINKING <-> CALLING_TOOL -> AWAITING_APPROVAL -> RUNNING_TOOL
                                          \\-> (auto-approved) -/
      -> AWAITING_INPUT  (turn over, session live, operator may send another)
      -> DONE | FAILED | CANCELLED | RATE_LIMITED

    DONE means the operator closed the session, not that a turn ended.
    """

    SPAWNING = "spawning"
    THINKING = "thinking"
    CALLING_TOOL = "calling_tool"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING_TOOL = "running_tool"
    # Turn finished, session still connected, ready for another prompt. Idle, like
    # AWAITING_APPROVAL: a conversation paused on the operator must cost nothing.
    # This is what DONE used to be mistaken for -- an agent that has asked a
    # question and one that has finished the job are not the same thing, and
    # rendering both as DONE is what made a waiting agent look complete.
    AWAITING_INPUT = "awaiting_input"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RATE_LIMITED = "rate_limited"

    @property
    def is_active(self) -> bool:
        """
        Whether this state should hold the render loop at full speed (§4.2).

        AWAITING_APPROVAL is deliberately *not* active: an agent parked on human
        review is the normal resting state of this tool, and it must cost nothing
        (I8). An orchestrator that spins at 60fps while waiting on its operator has
        its idle behaviour exactly backwards.
        """
        return self in _ACTIVE_STATES

    @property
    def is_terminal(self) -> bool:
        """Whether no further transition is expected without operator action."""
        return self in _TERMINAL_STATES


_ACTIVE_STATES = frozenset({AgentState.THINKING, AgentState.CALLING_TOOL, AgentState.RUNNING_TOOL})
_TERMINAL_STATES = frozenset({AgentState.DONE, AgentState.FAILED, AgentState.CANCELLED})

# The two topics a turn ending lands on. An interrupted turn is not a terminal
# state -- interrupt is the recoverable lever, so the session stays messageable --
# which leaves the topic as the only carrier for "your interrupt landed".
#
# They live here, next to the states they accompany, because both the driver that
# writes them and the ``needs_you`` projection that reads them need the same
# spelling, and a row and an inbox entry disagreeing about whether a turn was
# interrupted is worse than either being wrong alone.
AWAITING_TOPIC = "waiting for you"
INTERRUPTED_TOPIC = "interrupted - waiting for you"


@dataclass(frozen=True, slots=True)
class UsageRollup:
    """
    Token and cost totals accumulated over a session. This is the *money* axis.

    Cumulative and monotonic, which is what separates it from ContextSnapshot:
    this is what has been spent, not what is currently resident. The two are
    deliberately not shown together -- see ContextSnapshot.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # The SDK's own client-side estimate. Not authoritative billing; label it as an
    # estimate wherever it is shown.
    total_cost_usd: float = 0.0

    def plus(self, other: UsageRollup) -> UsageRollup:
        return UsageRollup(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            total_cost_usd=self.total_cost_usd + other.total_cost_usd,
        )


class ContextPressure(enum.Enum):
    """
    Coarse read on session health, derived once where the poll lands.

    Panels branch on this rather than doing arithmetic on token counts, which keeps
    the retire-or-continue judgement in one place instead of spread across the UI --
    and keeps division off the per-frame render path.
    """

    NOMINAL = "nominal"
    NEARING_COMPACTION = "nearing_compaction"
    # Sticky: once a session has compacted it has already lost reasoning, and no
    # later drop in occupancy undoes that.
    COMPACTED = "compacted"


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """
    Context-window occupancy: a *session-health* signal, not a spend meter.

    This exists to answer one question -- "is this session about to be compacted,
    and should I retire it and start a fresh one?" Compaction discards the reasoning
    that got the agent here, and what comes after is worse at anything that depended
    on it. The number is here so the operator can pre-empt that, not to ration
    anything. Money is UsageRollup's job and belongs in a different widget.

    Polled, not pushed (``get_context_usage()`` is a control request), so it is
    always slightly stale -- carry ``polled_at`` and let the UI say how stale rather
    than implying it is live.

    ``max_tokens`` is the effective ceiling, already reduced by the autocompact
    buffer; ``raw_max_tokens`` is the model's nominal window and is only worth
    showing as context for the gap between them.
    """

    used_tokens: int
    max_tokens: int
    raw_max_tokens: int
    percentage: float
    auto_compact_enabled: bool
    auto_compact_threshold: int | None
    model: str
    polled_at: float
    # Counted from the PreCompact hook. "This session has compacted twice" is a much
    # stronger retire signal than any occupancy percentage: it reports damage already
    # done rather than damage predicted.
    compactions: int = 0
    last_compaction_at: float | None = None

    def with_compaction_history(self, compactions: int, last_at: float | None) -> ContextSnapshot:
        """
        Carry compaction history onto a freshly polled occupancy reading.

        A poll reports what is resident now and knows nothing about what was
        discarded earlier, so without this the count would reset on every poll and
        COMPACTED would flicker back to NOMINAL as soon as the bar dropped -- which
        is precisely the moment it is most wrong.
        """
        return dataclasses.replace(self, compactions=compactions, last_compaction_at=last_at)

    @property
    def tokens_until_compaction(self) -> int | None:
        """
        Headroom before autocompact fires, which is the number worth showing.

        None when there is no threshold to measure against -- either autocompact is
        off, or the CLI did not report one. Callers must not fall back to max_tokens
        here: that would silently answer a different question.
        """
        if not self.auto_compact_enabled or self.auto_compact_threshold is None:
            return None
        return max(0, self.auto_compact_threshold - self.used_tokens)

    def pressure(self, warn_fraction: float = 0.15) -> ContextPressure:
        """
        Coarse health, warning when within ``warn_fraction`` of the threshold.

        Measured against the compaction threshold rather than the window size,
        because the threshold is where the damage happens. Falls back to raw
        occupancy only when no threshold is available.
        """
        if self.compactions > 0:
            return ContextPressure.COMPACTED
        headroom = self.tokens_until_compaction
        if headroom is None:
            # No threshold to aim at, so occupancy is all there is. Deliberately
            # conservative: near-full without autocompact means a hard stop ahead.
            frac = self.used_tokens / self.max_tokens if self.max_tokens else 0.0
            return (
                ContextPressure.NEARING_COMPACTION
                if frac >= 1.0 - warn_fraction
                else ContextPressure.NOMINAL
            )
        threshold = self.auto_compact_threshold or 0
        if threshold and headroom <= threshold * warn_fraction:
            return ContextPressure.NEARING_COMPACTION
        return ContextPressure.NOMINAL


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """
    A tool call parked in front of the operator.

    Pure data on purpose. The ``asyncio.Future`` that actually unblocks the agent
    lives in the Bridge, keyed by ``id``. If it lived here, every consumer of the
    store would be holding a handle into another thread's event loop, and the store
    would stop being testable without asyncio.
    """

    id: str
    node: NodeId
    tool_name: str
    tool_use_id: str
    raw_args: Mapping[str, Any]
    summary: str
    requested_at: float
    # Unified diff for file-mutating tools; None when the tool has no diff to show
    # (Bash, network calls) and the summary carries the whole story.
    diff: str | None = None


class ObligationKind(enum.Enum):
    """
    The three ways an agent can be waiting on its operator.

    Named as a closed set on purpose. The whole defect this exists to fix is that
    "waiting on you" had two unrelated implementations and one of them had no
    surface at all, so the count in the status bar was true about approvals and
    silently wrong about everything else.
    """

    APPROVAL = "approval"
    QUESTION = "question"
    FAILURE = "failure"


def _node_key(kind: ObligationKind, node: NodeId) -> str:
    return f"{kind.value}:{node[0]}:{node[1] or ''}"


@dataclass(frozen=True, slots=True)
class ApprovalNeeded:
    """A tool call parked in front of the operator."""

    node: NodeId
    since: float
    summary: str
    approval: PendingApproval

    kind: ClassVar[ObligationKind] = ObligationKind.APPROVAL

    @property
    def key(self) -> str:
        """
        Stable identity for the cursor and for ImGui widget keys.

        Never a position in ``needs_you``. That list is sorted by age and reorders
        whenever anything arrives or is answered, so an index-derived identity
        silently retargets the cursor -- and this is the cursor that decides which
        agent an ``approve`` applies to (I6).

        Keyed on the approval rather than the node, because one node can have
        several calls parked at once.
        """
        return f"{self.kind.value}:{self.approval.id}"


@dataclass(frozen=True, slots=True)
class QuestionPending:
    """
    A turn ended with the session still live, so the agent is waiting to be told
    something.

    Carries no guess at *what* was asked. A turn that ends is either a finished job
    or a question, and the two are indistinguishable from the outside -- inferring
    which from a trailing question mark was considered and rejected when sessions
    became conversational, and re-introducing that guess here to make a prettier row
    summary would be the same mistake at a different layer. The transcript holds
    what was actually said; the inbox row's job is to say that someone is waiting.
    """

    node: NodeId
    since: float
    summary: str

    kind: ClassVar[ObligationKind] = ObligationKind.QUESTION

    @property
    def key(self) -> str:
        """A session has at most one unanswered turn, so the node identifies it."""
        return _node_key(self.kind, self.node)


@dataclass(frozen=True, slots=True)
class SessionFailed:
    """
    A session died. Listed as an obligation because a crashed session is work that
    has stopped and only the operator can decide what happens next -- and because
    the surface that showed it before this existed was nothing at all.
    """

    node: NodeId
    since: float
    summary: str
    error: str | None = None

    kind: ClassVar[ObligationKind] = ObligationKind.FAILURE

    @property
    def key(self) -> str:
        return _node_key(self.kind, self.node)


# A tagged union, not a base class with three subclasses: the inbox renders one row
# shape for all three (glyph, identity, wait, summary) and three different bodies
# when a row expands, and `match` over a union is what makes the store's own
# exhaustiveness trick available to that second half.
Obligation = ApprovalNeeded | QuestionPending | SessionFailed


@dataclass(frozen=True, slots=True)
class AgentRecord:
    """One node in the agent tree."""

    node_id: NodeId
    parent: NodeId | None
    depth: int

    state: AgentState
    # Derived mechanically from current activity ("reading store.py", "running
    # pytest"), so it is always present and always free. Never produced by a
    # summarisation call -- this field is visible every frame.
    topic: str
    task: str
    model: str

    agent_type: str | None = None
    # The directory this agent runs in, inherited from the parent for sub-agents.
    # Chosen at launch and then, until this field existed, gone -- which left the
    # one axis the tool is sold on (work across several projects) unrenderable:
    # twelve sessions over three repos were twelve indistinguishable rows.
    #
    # The *fact*, not a project name. Grouping cwds into projects is a presentation
    # decision and is made in the UI (see ui/projects.py); putting the derived name
    # here would freeze one grouping rule into the store.
    cwd: str | None = None
    # The work template this session was launched under, on a root record only.
    # None on a sub-agent, which does not have one.
    #
    # Stored for the same reason as ``cwd`` directly above: it is chosen at launch,
    # nothing else in the snapshot implies it, and a launch-time choice that is not
    # stored is simply gone. It lives on ``driver.AgentSession`` otherwise, which
    # the UI must not reach into -- a frame reads one Snapshot and nothing else.
    #
    # The *fact* -- the template's name -- not a derived "is a team" boolean. What
    # counts as a team is a presentation judgement (see ui/board.has_board), and
    # freezing one answer here would be the same mistake as storing a project name
    # instead of a cwd.
    template: str | None = None
    usage: UsageRollup = field(default_factory=UsageRollup)
    context: ContextSnapshot | None = None
    # A tuple, not one slot. An assistant turn can contain several tool calls, the
    # CLI dispatches PreToolUse for all of them concurrently, and the gate parks a
    # future for each -- three at once, measured. A single slot silently discarded
    # every approval but the last while their agents stayed blocked forever.
    pending: tuple[PendingApproval, ...] = ()

    def pending_by_id(self, pending_id: str) -> PendingApproval | None:
        return next((p for p in self.pending if p.id == pending_id), None)

    transcript: Transcript = field(default_factory=Transcript)
    # Both are time.monotonic(), never time.time(). These exist to be subtracted from
    # each other, and monotonic is the only clock that survives an NTP step or a
    # daylight-saving jump without producing a negative or wildly large duration.
    # Mixing the two clocks yields an elapsed time in the hundreds of thousands of
    # hours, which is at least loud; mixing them the other way is silently wrong.
    started_at: float = 0.0
    ended_at: float | None = None
    # When this node entered the state it is in now. Stamped by the store from the
    # frame clock, so it lags the emitting thread by at most one frame -- irrelevant
    # against waits measured in seconds, and the alternative was a required
    # timestamp on four intents that twelve call sites would have to remember.
    #
    # Exists because "how long has this been waiting on me?" had no answer for two
    # of the three obligation kinds. An approval carries requested_at and a failure
    # carries ended_at; a session that ended its turn with a question carried
    # nothing, so the one obligation most likely to be forgotten was also the one
    # that could not be sorted by age.
    state_since: float = 0.0
    error: str | None = None
    # Set once the operator has cleared this session's failure from the inbox. Only
    # meaningful while state is FAILED.
    acknowledged: bool = False
    # The whole of this node's final answer, on a sub-agent that has stopped.
    #
    # Not derivable from the transcript, which is the reason it is stored. The
    # transcript is an append-only stream of OUTPUT, TOOL_CALL and TOOL_RESULT
    # segments with no marker saying where an answer begins -- "the OUTPUT since the
    # last tool call" reconstructs it only for a sub-agent that never spoke
    # mid-run, and gets it wrong for one that did. The driver, by contrast, is
    # handed the answer whole on the SubagentStop hook and knows exactly what it is.
    #
    # Only a sub-agent has one today. A root's turn boundary is a ResultMessage,
    # which carries no parent_tool_use_id and so can never be attributed to a
    # sub-agent's node -- the reason this comes from the hook rather than from the
    # message stream.
    deliverable: str | None = None

    def with_(self, **changes: Any) -> AgentRecord:
        """Copy with fields replaced."""
        return dataclasses.replace(self, **changes)


ConcernId = str
TaskId = str


def _empty_map() -> Mapping[Any, Any]:
    return MappingProxyType({})


class ConcernState(enum.Enum):
    """
    Where a concern is between one agent posting it and another reading it.

    WITHDRAWN is terminal and reachable only from POSTED: once a concern has been
    delivered, the recipient has seen it, and pretending otherwise would make the
    store disagree with a transcript that already contains it.
    """

    POSTED = "posted"
    DELIVERED = "delivered"
    WITHDRAWN = "withdrawn"


@dataclass(frozen=True, slots=True)
class Concern:
    """
    One agent's message to another, as a store object rather than text in transit.

    ``sender`` is stamped by the approval gate from the ``agent_id`` on the hook
    input, never taken from the tool's arguments. An in-process MCP handler is told
    only the tool name and its arguments -- no session, no agent, no tool-use id --
    so a sender the model wrote is a sender the model chose, and a worker posting
    as the lead would be an ordinary consequence of asking a model to fill a field
    rather than anything exotic. The gate is the only participant that knows who
    called, which makes it the authentication layer and not merely a policy one.

    ``body`` is the text as it will be delivered, which is not necessarily the text
    that was posted -- ``edited`` records that the operator changed it in flight.
    That capability is the reason this is a record at all: a concern that could
    only be allowed or denied would not need to exist outside the transcript.
    """

    id: ConcernId
    sender: NodeId
    recipient: NodeId
    subject: str
    body: str
    posted_at: float
    state: ConcernState = ConcernState.POSTED
    edited: bool = False
    delivered_at: float | None = None


class TaskState(enum.Enum):
    """
    Deliberately without a BLOCKED member.

    Blocked-ness is a function of the dependency graph and is derived on read (see
    ``Task.is_claimable``). Storing it would be a second fact about the same thing,
    kept true by whoever remembers to update it on every completion -- which is the
    shape of the defect ``needs_you`` exists to fix, one layer down.

    Three states, all reachable: a released task returns to PENDING rather than
    reaching a terminal "abandoned". A task still CLAIMED by a node that has since
    died is a real condition and a derived one -- the claimer's state is already in
    the same snapshot -- so it belongs in a projection when there is a pane to show
    it in, not in a fourth member here that nothing sets.
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    COMPLETED = "completed"


class TaskRefusal(enum.Enum):
    """
    Why a write to the board did not take effect.

    An enum rather than a string because the reducer is pure and prose is the
    shell's job: ``bus.py`` owns the words an agent reads, and a sentence built in
    ``_apply`` would be presentation smuggled into the core.

    The members are separate for the reason STYLE.md §3 gives -- an error message
    that does not distinguish the two mistakes it covers sends a worker back to
    make the same one. Three pairs are here for that reason alone:
    ``NOT_CLAIMED`` is "nobody holds this, claim it first" against ``NOT_YOURS``,
    "somebody else is working it, ask them"; ``ALREADY_COMPLETE`` is neither, and
    the recovery is to stop rather than to retry; and ``DUPLICATE_ID`` against
    ``WOULD_CYCLE`` is a name collision against a dependency graph the declarer has
    to redraw.

    ``NOT_APPLIED`` is the one member ``_apply`` never returns. It is the answer the
    Bridge hands back when the store never got to see the intent at all -- shutdown,
    mainly -- and it exists because the alternative fallback is reporting a success
    that certainly did not happen, which is the defect this whole channel is for.
    """

    DUPLICATE_ID = "duplicate_id"
    WOULD_CYCLE = "would_cycle"
    NO_SUCH_TASK = "no_such_task"
    NOT_CLAIMED = "not_claimed"
    NOT_YOURS = "not_yours"
    ALREADY_COMPLETE = "already_complete"
    NOT_APPLIED = "not_applied"


@dataclass(frozen=True, slots=True)
class Task:
    """
    A unit of work an agent can claim.

    Carries no trace of the request that claimed it. An earlier draft put the
    winning ``claim_id`` here so the UI thread could find its own answer by
    scanning, which made a correlation token part of the domain and obliged
    ``TaskReleased`` to clear it -- miss that and a later claim reusing the id gets
    answered by a stale record. The reducer emits an ``Effect`` instead
    (``effects.py``), so the answer travels as a value and the record stays about
    the work.
    """

    id: TaskId
    title: str
    detail: str = ""
    depends_on: tuple[TaskId, ...] = ()
    claimed_by: NodeId | None = None
    state: TaskState = TaskState.PENDING
    declared_at: float = 0.0
    completed_at: float | None = None
    # Who put it on the board. Stored rather than derived because nothing else in
    # the snapshot implies it: there is one Store for the whole fleet, so `tasks` is
    # a global map, and an unclaimed task has no other node attached to it. Without
    # this a board cannot be scoped to the session whose agents are working it.
    declared_by: NodeId | None = None

    def is_claimable(self, tasks: Mapping[TaskId, Task]) -> bool:
        """
        Pending, and every dependency completed.

        A dependency that does not exist counts as unsatisfied rather than as
        absent. Declaring a task against a typo'd id should leave it unclaimable
        and visibly so, not silently ready.
        """
        if self.state is not TaskState.PENDING:
            return False
        return not self.blocked_on(tasks)

    def blocked_on(self, tasks: Mapping[TaskId, Task]) -> tuple[TaskId, ...]:
        """
        The dependencies not yet completed, in declared order.

        ``is_claimable`` is defined in terms of this rather than repeating the
        walk, so the pane that shows *why* a task is blocked and the board that
        decides *whether* it is cannot disagree.

        A dependency naming a task that does not exist is unsatisfied, not absent
        -- same rule as ``is_claimable``, and the reason a typo'd id shows as
        blocked. Whether such an id was ever declared is a further question, and
        one only the caller's ``tasks`` map can answer: `d not in tasks`.

        Answered for a task in any state. A CLAIMED or COMPLETED task normally has
        none outstanding, but nothing in the store enforces that -- a task can be
        claimed and its dependency released afterwards -- and returning the honest
        graph answer is cheaper than a state guard that hides it.
        """
        return tuple(
            d
            for d in self.depends_on
            if (dep := tasks.get(d)) is None or dep.state is not TaskState.COMPLETED
        )


@dataclass(frozen=True, slots=True)
class Snapshot:
    """
    The whole world at one instant. The UI builds a frame from exactly one of these
    (I2), taken once at frame start (§4.1).

    ``order`` and ``any_active`` are computed at construction, not derived on read.
    ``any_active`` in particular is read every frame to drive idling, so it must not
    be a scan over the node table.
    """

    seq: int
    nodes: Mapping[NodeId, AgentRecord]
    # Tree pre-order: each parent immediately followed by its descendants. The UI
    # renders this list directly and indents by record.depth, so it never walks the
    # tree itself.
    order: tuple[NodeId, ...]
    # Everything waiting on the operator, across every agent, oldest first.
    #
    # One projection over all three obligation kinds, not three lists that agree by
    # convention. This replaced a queue of approvals alone, which is why the status
    # bar could read "nothing awaiting review" while a session sat on an unanswered
    # question and another had crashed -- two of the three things the operator owed
    # attention to were modelled by a different mechanism and counted by nothing.
    #
    # Everything that counts or badges an obligation reads this. Nothing re-derives
    # from `pending`: that habit is what produced the original defect, and it
    # reproduced itself once already inside the mock built to fix it.
    needs_you: tuple[Obligation, ...]
    any_active: bool

    # The two cross-agent projections (§2.7). Top-level rather than hung off
    # AgentRecord: a concern has two nodes and a task has none until it is claimed,
    # so either would have to be stored twice and kept in agreement.
    #
    # default_factory, not a bare MappingProxyType({}): dataclasses reject an
    # unhashable default outright, and the shared instance a naive default would
    # give every Snapshot is the thing that rule exists to prevent.
    concerns: Mapping[ConcernId, Concern] = field(default_factory=_empty_map)
    tasks: Mapping[TaskId, Task] = field(default_factory=_empty_map)

    @staticmethod
    def empty() -> Snapshot:
        return Snapshot(
            seq=0,
            nodes=MappingProxyType({}),
            order=(),
            needs_you=(),
            any_active=False,
            concerns=MappingProxyType({}),
            tasks=MappingProxyType({}),
        )

    def inbox_of(self, node: NodeId) -> tuple[Concern, ...]:
        """
        Everything posted to ``node`` and not yet delivered or withdrawn, oldest
        first -- the order a recipient should work them in.
        """
        return tuple(
            sorted(
                (
                    c
                    for c in self.concerns.values()
                    if c.recipient == node and c.state is ConcernState.POSTED
                ),
                key=lambda c: c.posted_at,
            )
        )

    def claimable_tasks(self) -> tuple[Task, ...]:
        """
        Every task a worker could take right now, oldest first.

        Derived on each call rather than cached on the snapshot: it is read when an
        agent claims, which is rare, and a cached copy would be one more thing that
        can disagree with the graph it summarises.
        """
        return tuple(
            sorted(
                (t for t in self.tasks.values() if t.is_claimable(self.tasks)),
                key=lambda t: t.declared_at,
            )
        )

    @property
    def approvals(self) -> tuple[PendingApproval, ...]:
        """
        The approval subset, for the one caller that legitimately needs it: the
        watchdog comparing parked futures to what the operator can reach.

        Filtered out of ``needs_you`` rather than re-walked from ``pending`` so
        there is still exactly one list. A watchdog that built its own view of the
        world would be checking an invariant against itself.
        """
        return tuple(o.approval for o in self.needs_you if isinstance(o, ApprovalNeeded))

    def get(self, node_id: NodeId) -> AgentRecord | None:
        return self.nodes.get(node_id)

    def children_of(self, node_id: NodeId | None) -> tuple[NodeId, ...]:
        return tuple(n for n in self.order if self.nodes[n].parent == node_id)
