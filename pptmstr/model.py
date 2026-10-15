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

import builtins
import dataclasses
import enum
import posixpath
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar

from .approval import DiffText, Policy
from .command import (
    TIMEOUT_CHOICES_S,
    Command,
    CommandId,
    CommandState,
    RequestRefusal,
    command_refusal,
)
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
      -> SUPERVISING     (turn over, sub-agents still running)
      -> AWAITING_INPUT  (turn over, session live, operator may send another)
      -> DONE | FAILED | CANCELLED | RATE_LIMITED

    DONE means the operator closed the session, not that a turn ended.
    """

    SPAWNING = "spawning"
    THINKING = "thinking"
    CALLING_TOOL = "calling_tool"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING_TOOL = "running_tool"
    # A lead whose own turn is over while its sub-agents are still running.
    #
    # It exists because THINKING was what this rendered as, and THINKING is also what
    # a lead mid-turn renders as -- so the surfaces could not tell the operator
    # something that is measured true: a prompt sent now is dispatched now, as its
    # own turn, and answered without waiting for the fan-out
    # (scripts/verify_lead_turn_via_agent_session.py, ANSWERED-IN-WAIT-LOOP). Every
    # surface said the opposite. The state is what lets them stop.
    #
    # It is not an obligation. The lead is waiting on its workers, not on the
    # operator, and nothing is owed -- see ``_needs_you``, which deliberately does
    # not list it.
    SUPERVISING = "supervising"
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

        SUPERVISING is not active either, for the same reason one step removed: the
        lead is parked on its workers and doing nothing itself. The workers are
        separate nodes carrying their own states, so a fan-out with anything running
        in it already holds the loop at full speed through them -- counting the lead
        as well would be counting the same work twice, and a fan-out where every
        worker is itself parked on the operator is a tree that should idle.
        """
        return self in _ACTIVE_STATES

    @property
    def is_terminal(self) -> bool:
        """Whether no further transition is expected without operator action."""
        return self in _TERMINAL_STATES

    @property
    def is_final(self) -> bool:
        """
        Whether this is an end an ordinary status update cannot undo
        (``FINAL_STATES``). Narrower than ``is_terminal``: FAILED is terminal and
        not final.
        """
        return self in FINAL_STATES


_ACTIVE_STATES = frozenset({AgentState.THINKING, AgentState.CALLING_TOOL, AgentState.RUNNING_TOOL})
_TERMINAL_STATES = frozenset({AgentState.DONE, AgentState.FAILED, AgentState.CANCELLED})

# The ends that cannot be undone by an ordinary status update.
#
# A narrower question than ``AgentState.is_terminal``, and deliberately not the
# same set. ``is_terminal`` asks whether another transition is expected without
# the operator; this asks who is allowed to contradict the end, and only
# ``AgentFinished`` and ``AgentResumed`` are.
#
# FAILED is absent on purpose. A session that errored and then answered is an
# ordinary session again (see the store's StateChanged arm), and that recovery
# arrives as a plain StateChanged because nothing announces it -- the driver
# re-asserts a standing failure afterwards rather than suppressing the recovery.
# The driver reports an error result as FAILED with its client still connected,
# so a FAILED root is not evidence that its process has gone.
#
# DONE and CANCELLED make the opposite statement: somebody ended this agent. A
# sub-agent's SubagentStop fires once, and the wake that can follow it comes in as
# a second SubagentStart, which the driver translates to ``AgentResumed`` -- so
# nothing legitimate reaches a finished sub-agent through StateChanged. What does
# reach it is its own last AssistantMessage: the message stream is buffered while
# the control frame carrying the stop is dispatched straight away, so the answer
# translates into a THINKING that lands after the end it preceded.
#
# Here and not in the store because two questions are asked of it outside the
# reducer's arms: whether a session may leave the fleet
# (``Snapshot.detach_refusal``) and what has become of a decision's asker.
FINAL_STATES = frozenset({AgentState.DONE, AgentState.CANCELLED})

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

# What a lead is doing between its own turn ending and its workers finishing.
#
# Here rather than spelled in the driver for the same reason as the two above: the
# topic column is the row's one line of prose, and it has to agree with what
# ``compose`` tells the operator they can do about it. Two spellings of the same
# situation is how a row and a pane end up contradicting each other.
SUPERVISING_TOPIC = "supervising its sub-agents - you can send now"

# What a sub-agent was last doing when its session failed under it. It is ended
# CANCELLED and owes the operator nothing of its own -- the root's failure is the
# session's one row (``driver.AgentSession._settle_subagents_at_run_end``) -- so
# this is what tells it from a sub-agent of a session somebody closed.
ENDED_WITH_SESSION_TOPIC = "ended with its session"

# The topic of a record recalled from a session file: a session this process has
# not run. One word, because the card says how and how long ago from
# ``Recalled`` and a second account of that here could disagree with it.
RECALLED_TOPIC = "closed"


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
    # The call exactly as the agent made it. Not for drawing: see ``args``.
    raw_args: Mapping[str, Any]
    summary: str
    # ``time.monotonic()``, never ``time.time()``. This is the ``since`` an
    # ``ApprovalNeeded`` carries, so it is subtracted from the frame clock and sorted
    # against ``state_since`` and ``ended_at``, both of which are monotonic. An epoch
    # reading here is not merely a different origin: it is larger than every genuine
    # one, so it sorts last in a list ordered oldest-first, and the negative age it
    # produces clamps to "0s" -- the longest wait in the queue rendering as the
    # newest. Stamped by whoever builds the record; pinned by a test over every
    # construction site in ``pptmstr/``, because there is no seam that can enforce it.
    requested_at: float
    # Unified diff for file-mutating tools; None when the tool has no diff to show
    # (Bash, network calls) and the summary carries the whole story.
    diff: str | None = None
    # The before/after texts ``diff`` was rendered from; None exactly when the tool
    # has nothing diff-shaped. Both are carried although one follows from the other:
    # ``diff`` is ``approval.render_texts`` of this, and deriving it on read would put
    # ``difflib`` over a whole file on the frame path for as long as the call is
    # parked. Built from one ``approval.diff_texts`` call at the construction site,
    # so the two cannot describe different reads of the file.
    #
    # Holds the file's whole text, twice for a whole-file Edit, in every snapshot
    # until the approval settles -- about what ``diff`` already costs for a Write
    # that replaces a file, and more than it for a one-line Edit to a large one. In
    # memory only: nothing persists a ``PendingApproval``.
    texts: tuple[DiffText, ...] | None = None
    # ``raw_args`` as a pane may draw them (``drawable``), where that is another
    # mapping; None where the arguments are drawable as they are, which is nearly
    # always. Read through ``args``. Set by the reducer when the call enters the
    # store, and held rather than derived on read because the panes key their views
    # on the identity of what they are handed: a derivation per frame would be a
    # new mapping per frame for exactly the calls that have one.
    shown_args: Mapping[str, Any] | None = None

    @property
    def args(self) -> Mapping[str, Any]:
        """
        The arguments for anything that draws, measures or copies them as text.

        ``raw_args`` is the call exactly as the agent made it, and can hold what no
        text call takes: a lone surrogate raises in every one of ImGui's, and a NUL
        ends the string there. It is read where the call is *acted on* -- the
        editor's draft, which approve sends back to the tool -- and nowhere a
        string is drawn. Every other record field that holds session text is
        drawable as stored.
        """
        return self.raw_args if self.shown_args is None else self.shown_args


class ObligationKind(enum.Enum):
    """
    The five things the operator can owe an agent.

    Named as a closed set on purpose. The whole defect this exists to fix is that
    "waiting on you" had two unrelated implementations and one of them had no
    surface at all, so the count in the status bar was true about approvals and
    silently wrong about everything else.

    The first three are read off a node's state, and a node in one of them has
    stopped. ``DECISION`` is not like that: it is a record an agent filed, the
    agent that filed it may be working, and one node can have several open beside
    any of the other three. ``COMMAND`` is a record too -- a break-glass command
    an agent asked the operator to run -- and its asker carries on in the same
    way.
    """

    APPROVAL = "approval"
    QUESTION = "question"
    FAILURE = "failure"
    DECISION = "decision"
    COMMAND = "command"


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


# -- decisions awaiting the operator ----------------------------------------------

DecisionId = str

# How many decisions one agent may have open at once. Raising one parks nothing in
# front of the operator on any rung -- the queue row is the review -- so this is
# the bound on what one looping agent can put in ``needs_you``, which the status
# bar counts. Small enough to read in one screen of the queue.
#
# Per asker, so that no agent's allowance is spent by another: the root of a
# session is refused a raise only for what it has open itself.
MAX_OPEN_DECISIONS_PER_ASKER = 8

# How many the sub-agents of one session may have open between them. A second
# bound, because a lead can start any number of workers and each has the allowance
# above. It never turns the root away, and a decision whose asker has ended for
# good -- stopped, failed, or gone -- is not counted in it: that decision waits on
# the operator's dismissal, and nothing a working agent can do would free its
# place.
MAX_OPEN_SUBAGENT_DECISIONS = 24

# Each text an asker writes on a decision: the question, what happens if it goes
# unanswered, the evidence and the recommendation. A decision is a question and
# not a document; the long form belongs in a file the text names.
#
# These three and ``MAX_ANSWER_CHARS`` are sized together. ``read_decisions``
# returns one decision with all of them whole, and that reply has to fit the
# bound the bus holds every board read to. tests/test_bus.py holds the sum to it.
MAX_DECISION_TEXT_CHARS = 1_000

# How many tasks a decision may name as blocked.
MAX_BLOCKED_TASKS = 8

# The operator's answer. Refused past this by the ``DecisionAnswered`` arm, and
# never cut: an answer is stored byte for byte or not at all. A surface that
# takes the answer reads this to refuse a longer draft before it is sent, with
# the count, because the arm's refusal has nobody to reply to.
MAX_ANSWER_CHARS = 4_000


class Standing(enum.Enum):
    """
    Whose decision it is, as its asker states it.

    ``OPERATORS`` is one nobody has taken: the work it blocks is waiting for the
    answer. ``TAKEN`` is one the agent already took and went on from, and is
    asking to have ratified -- an answer that disagrees means work to undo, which
    is why the two are never drawn alike.
    """

    OPERATORS = "operators"
    TAKEN = "taken"


@dataclass(frozen=True, slots=True)
class Answered:
    """
    The operator answered. ``text`` is what they typed, byte for byte: never
    stripped, cut, or put on one line. The record exists because a paraphrase
    passed between agents drifts, and an answer tidied here has already begun to.
    """

    text: str
    at: float


@dataclass(frozen=True, slots=True)
class Dismissed:
    """The operator closed it without answering. No answer exists."""

    at: float


@dataclass(frozen=True, slots=True)
class Withdrawn:
    """Its asker took it back. No answer exists."""

    at: float


# How a decision ended. A union and not an ``answer`` field beside a state: an
# answer exists exactly when the operator gave one, and with one value for both
# there is no dismissed decision carrying an answer to be explained.
DecisionOutcome = Answered | Dismissed | Withdrawn


class DecisionRefusal(enum.Enum):
    """
    Why an agent's write to a decision did not take effect.

    ``TOO_MANY_OPEN``, ``SESSION_FULL``, ``UNKNOWN_TASK`` and ``ALREADY_RAISED``
    turn a raise away. The first two are apart because what the asker can do next
    differs: ``TOO_MANY_OPEN`` is its own allowance, which it frees by withdrawing
    one of its own, and ``SESSION_FULL`` is the sub-agents' shared one, which it
    may hold none of. ``UNKNOWN_TASK`` is a ``blocks`` naming something that is
    not a task on the asker's board.

    The other three turn a withdrawal away, and are apart for the reason
    ``TaskRefusal`` gives -- a mistyped id, somebody else's decision, and one
    that has already ended are three different things to do next.

    ``NOT_APPLIED`` is the member ``_apply`` never returns: the fallback for a
    request the store never saw, as on ``TaskRefusal``.
    """

    TOO_MANY_OPEN = "too_many_open"
    SESSION_FULL = "session_full"
    UNKNOWN_TASK = "unknown_task"
    ALREADY_RAISED = "already_raised"
    NO_SUCH_DECISION = "no_such_decision"
    NOT_THE_ASKER = "not_the_asker"
    ALREADY_ENDED = "already_ended"
    NOT_APPLIED = "not_applied"


@dataclass(frozen=True, slots=True)
class Decision:
    """
    Something an agent wants the operator to decide, as a record rather than a
    sentence in a transcript.

    Every text field is the asker's and none is the store's: ``question``, what
    happens ``if_unanswered``, the ``evidence``, and the ``recommendation``.
    **The last two are separate fields and are never joined** -- the point of
    holding them apart is that the operator can read what is known before reading
    what the asker wants done with it.

    ``blocks`` is not a text. It is the ids of the tasks on the asker's board
    that wait for the answer, each one checked against that board when the
    decision is filed, and it may be empty. Empty is the statement "this blocks
    nothing on the board" and every surface says so in those words: prose here
    would have to be searched for task ids, and a sentence saying a task is NOT
    held up names that task as surely as one saying it is.

    ``reversible`` is three-valued and has no default. None is "the asker did not
    say", which is a different statement from either answer, and a default would
    put one of them in the asker's mouth.

    ``asker`` is the node the gate authenticated and ``raised_at`` the instant the
    reducer filed it; the ``DecisionRaised`` arm writes both, whatever the record
    it was handed says. ``outcome`` and ``first_delivered_at`` are written only by
    the arms for the operator's answer and dismissal, the asker's withdrawal, and
    the asker's read.

    ``first_delivered_at`` is when the asker was first handed the operator's
    answer or dismissal: its own read of this decision by id, which is the one
    reply that carries the answer. A read by the session's root of a decision
    another agent raised does not set it, and neither does a list of decisions,
    which shows no answer. A fact for the pane. Nothing reads it to decide
    whether to hand the answer over again: the asker is given it on every read.

    Not in the board log. A decision does not survive a restart of pptmstr.
    """

    id: DecisionId
    asker: NodeId
    question: str
    blocks: tuple[TaskId, ...]
    if_unanswered: str
    evidence: str
    recommendation: str
    standing: Standing
    reversible: bool | None
    raised_at: float
    outcome: DecisionOutcome | None = None
    first_delivered_at: float | None = None

    @property
    def session_id(self) -> str:
        """The session it was raised in, which is its asker's."""
        return self.asker[0]

    @property
    def is_open(self) -> bool:
        return self.outcome is None

    @property
    def answer(self) -> str | None:
        """The operator's words, or None when there are none. ``""`` is an answer."""
        return self.outcome.text if isinstance(self.outcome, Answered) else None

    @property
    def answered_at(self) -> float | None:
        return self.outcome.at if isinstance(self.outcome, Answered) else None


class AskerFate(enum.Enum):
    """
    What has become of the agent a decision is for, read off its record.

    The question each member answers is whether an answer given now will be
    read, and by what.

    ``LIVE`` is an agent in a turn: it reads the answer when it next asks.
    ``IDLE`` is one whose turn has ended with its session still connected
    (``AgentState.AWAITING_INPUT``). Nothing wakes it when a decision is answered,
    so the answer sits unread until the operator sends that session a prompt --
    and this is the ordinary state of an agent that asked and stopped to wait.

    ``STOPPED`` is an agent whose run ended; a sub-agent in it can still be woken,
    so an answer may yet be read. ``FAILED`` is one whose session died.

    ``UNANNOUNCED`` is a sub-agent the store has no record of while its session's
    root has one: it called the bus before its start was reported, and it may be
    working. ``GONE`` is an asker nothing in a run that is going now can wake: an
    agent of an earlier run of its session (``Snapshot.of_earlier_run``) --
    another process's, which has no record here, or this process's from before
    the session was started again, which has one and whose session is still in
    the store -- and an asker whose session is not in the store at all, a root
    with no record or a sub-agent whose root has none. Nothing will read an
    answer to it.

    ``has_ended`` is what the count of a session's open decisions reads.
    """

    LIVE = "live"
    IDLE = "idle"
    STOPPED = "stopped"
    FAILED = "failed"
    UNANNOUNCED = "unannounced"
    GONE = "gone"

    @property
    def has_ended(self) -> bool:
        """
        Whether the asker has finished for good as far as its own session can
        tell: nothing it does will withdraw the decision or free its place.
        """
        return self in (AskerFate.STOPPED, AskerFate.FAILED, AskerFate.GONE)


@dataclass(frozen=True, slots=True)
class DecisionOwed:
    """
    A decision nobody has answered, dismissed or withdrawn.

    ``summary`` is composed by the store from structure -- the id, the asker's
    role, the standing, the reversibility, the tasks it blocks, what became of
    the asker -- and holds none of the asker's sentences. The queue draws a
    summary in its own voice beside ``approval.summarize``'s, so a question shown
    there would be an agent's text reading as the queue's. The texts are on
    ``decision``, for a surface that marks them as authored.

    ``node`` is the asker. Unlike the other three kinds it does not say the node
    has stopped.
    """

    node: NodeId
    since: float
    summary: str
    decision: Decision
    asker: AskerFate

    kind: ClassVar[ObligationKind] = ObligationKind.DECISION

    @property
    def key(self) -> str:
        """Per decision: one node can have several open at once."""
        return f"{self.kind.value}:{self.decision.id}"


@dataclass(frozen=True, slots=True)
class CommandOwed:
    """
    A break-glass command that is asked or running.

    Both states owe a row. An asked one waits to be read and run or refused; a
    running one is listed because stopping it is the operator's and has to stay
    reachable whatever has become of its asker.

    ``summary`` is composed by the store from structure -- the state, the
    asker's role, how many lines and characters the string has, the id -- and
    holds nothing of the string, the reason or the expectation, for the reason
    ``DecisionOwed.summary`` holds none of a question. The texts are on
    ``command``, for a surface that marks each as its author's.

    ``since`` is on the store's clock like every other obligation's, and is
    not ``command.asked_at``, which is a wall-clock time
    (``Snapshot.command_since``).

    ``node`` is the asker, and it has not stopped: it was told to carry on.
    """

    node: NodeId
    since: float
    summary: str
    command: Command
    asker: AskerFate

    kind: ClassVar[ObligationKind] = ObligationKind.COMMAND

    @property
    def key(self) -> str:
        """Per command: one node can have several open at once."""
        return f"{self.kind.value}:{self.command.id}"


# A tagged union, not a base class with five subclasses: the inbox renders one row
# shape for all of them (glyph, identity, wait, summary) and a different body for
# each when a row expands, and `match` over a union is what makes the store's own
# exhaustiveness trick available to that second half.
Obligation = ApprovalNeeded | QuestionPending | SessionFailed | DecisionOwed | CommandOwed


# -- break-glass commands ----------------------------------------------------------
#
# The records are ``command.py``'s and are not declared again here. What is here
# is what the store says about one: why it turned a write away.

# The operator's reason for refusing a command. Past this the ``CommandRefused``
# arm applies nothing, and never cuts: the asker reads the reason back whole, in
# a reply the bus bounds. A surface that takes the reason reads this to refuse a
# longer draft first, as with ``MAX_ANSWER_CHARS``.
MAX_COMMAND_REFUSAL_CHARS = 1_000


class CommandRefusal(enum.Enum):
    """
    Why a write to a break-glass command did not take effect, where the reason
    is the store's and not the command string's (``command.RequestRefusal`` is
    that half, and a settled write carries one or the other).

    ``BAD_ID``, ``ALREADY_ASKED``, ``TOO_MANY_OPEN`` and ``SESSION_FULL`` turn a
    request away. The last two are apart for the reason they are on
    ``DecisionRefusal``: one is the asker's own allowance, the other the
    sub-agents' shared one.

    ``NO_SUCH_COMMAND``, ``NOT_THE_ASKER`` and ``NOT_ASKED`` turn a withdrawal
    away. ``NOT_ASKED`` is a command that is no longer only asked: the operator
    has approved it, or it has ended.

    ``NO_SUCH_COMMAND``, ``NOT_ASKED``, ``BAD_TIMEOUT`` and
    ``ALREADY_RUNNING_IN_SESSION`` turn the operator's approval away
    (``Snapshot.command_approval_refusal``). Nobody is parked on an approval,
    so the surface that offers it asks first and says which.

    ``NOT_APPLIED`` is the member ``_apply`` never returns: the fallback for a
    request the store never saw, as on ``TaskRefusal``.

    The value is the stable name; ``sentence`` is what is said.
    """

    BAD_ID = "bad_id"
    ALREADY_ASKED = "already_asked"
    TOO_MANY_OPEN = "too_many_open"
    SESSION_FULL = "session_full"
    NO_SUCH_COMMAND = "no_such_command"
    NOT_THE_ASKER = "not_the_asker"
    NOT_ASKED = "not_asked"
    BAD_TIMEOUT = "bad_timeout"
    ALREADY_RUNNING_IN_SESSION = "already_running_in_session"
    NOT_APPLIED = "not_applied"

    @property
    def sentence(self) -> str:
        return _COMMAND_REFUSAL_SENTENCES[self]


_COMMAND_REFUSAL_SENTENCES = {
    CommandRefusal.BAD_ID: "The command's id does not have the form of a command id.",
    CommandRefusal.ALREADY_ASKED: (
        "A command with that id has already been asked. It stands as it was asked."
    ),
    CommandRefusal.TOO_MANY_OPEN: (
        "You already have as many commands waiting on the operator as one agent "
        "may. Withdraw one that is still only asked, or wait for one to end."
    ),
    CommandRefusal.SESSION_FULL: (
        "The sub-agents of this session already have as many commands waiting on "
        "the operator as they may between them. Wait for one to end."
    ),
    CommandRefusal.NO_SUCH_COMMAND: "There is no command with that id.",
    CommandRefusal.NOT_THE_ASKER: (
        "That command was asked by another agent. Only its asker may withdraw it."
    ),
    CommandRefusal.NOT_ASKED: (
        "That command is no longer only asked: the operator has acted on it, or " "it has ended."
    ),
    CommandRefusal.BAD_TIMEOUT: "The timeout is not one of the bounds a command may run under.",
    CommandRefusal.ALREADY_RUNNING_IN_SESSION: (
        "Another command of this session is running. One runs at a time."
    ),
    CommandRefusal.NOT_APPLIED: (
        "The request was not applied: pptmstr stopped before it was read."
    ),
}


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    """
    Everything an operator settles before a session exists.

    A value rather than a positional argument list, which is the correction row 9
    paid for: ``template`` was droppable because the callback took four loose
    strings and a three-argument lambda typechecked cleanly, so a relaunch silently
    started a team session solo. One more optional field on that signature would be
    one more chance at the same defect, and a launch path is exactly where a
    silently-dropped field is least visible -- the session starts, it just starts
    wrong.

    Not store state. It is what the launcher hands the driver, and nothing keeps a
    copy after the session announces itself.
    """

    task: str
    model: str
    # Empty means this process's directory; the launcher normalises before building one.
    cwd: str = "."
    # The base ``cwd``'s writes are measured from, resolved by ``tree.session_base``
    # at the launch sites rather than here: this is a frozen value type and the
    # resolution stats the filesystem.
    #
    # None is honest rather than a default worth guessing at -- a spec built
    # without one measures against ``cwd``, which is what every record did before
    # the field existed.
    session_base: str | None = None
    # By name. None is solo, spelled here rather than at each call site because
    # `relaunch` and `fork` pass `AgentRecord.template`, which is None on any record
    # that is not a session root.
    template: str | None = None
    # The directory holding this session's brief, or None for a session launched
    # with nothing but its task.
    #
    # A path and never the content: a stored copy of premises is the duplicate
    # STYLE.md §1 names as this codebase's historic defect shape, and the brief is
    # on disk precisely so there is one home for it.
    #
    # Stored rather than derived, which is the part worth the argument. The
    # canonical location is `~/.claude/projects/<cwd-slug>/briefs/<session-id>/`,
    # and that *is* a function of two facts a record already holds -- so §1 says
    # compute it. It is stored anyway because a fork or a relaunch is a new session
    # id, so deriving would hand the new session an empty directory and lose the
    # premises at exactly the moment the record exists to keep them. A derived path
    # is right for a session that owns its brief and wrong for every session that
    # inherits one.
    brief: str | None = None
    # The id of an existing session to continue, or None to start a fresh one.
    #
    # It restores the conversation and nothing else. A resumed session has no
    # `AgentRecord` on the other side of the CLI, so `task`, `model`, `template` and
    # `brief` above are still whatever the operator supplied for *this* launch --
    # they are not read back out of the session being continued, and no surface may
    # present them as though they were.
    #
    # Deliberately not derived from `from_record`: a relaunch and a fork both want a
    # new conversation from the same premises, which is the opposite of this.
    resume: str | None = None
    # The containment configuration as the JSON string `ClaudeAgentOptions.settings`
    # takes -- what `sandbox.containment_settings` returns -- or None for a launch that
    # is not contained. Text rather than anything structured for the reason
    # `driver.AgentSession.containment` gives: several of the keys have no slot in the
    # SDK's `SandboxSettings`.
    #
    # Not carried by `from_record`, and the contrast with `session_base` above is the
    # useful part. `session_base` is carried because a relaunch that measures against a
    # different base is a silent defect, so dropping it would lose a guarantee. This is
    # dropped because `ui/health.py` forks with `actions.fork(LaunchSpec.from_record(
    # root))` -- no modal, no combo, no confirmation -- and a fork is the one launch
    # path with no surface on which an operator chooses either of these. A value that
    # arrives without the surface that sets it is a value nobody chose.
    containment: str | None = None
    # Which allowlist the gate measures this session's calls against. `Policy.STRICT` is
    # the gate as it has always classified; nothing widens unless a launch says so.
    #
    # Not carried by `from_record` either, and it travels with `containment` rather than
    # separately: `planning/2026-09-03` records that the containment layer and the
    # under-gated allowlist are not separable, because the sandbox covers `Bash` and its
    # children while the writing tools run inside the CLI process. Carrying one without
    # the other would relaunch half an arrangement, and carrying both would make an
    # under-gated session inheritable in one click from a button pressed for an
    # unrelated reason.
    #
    # Per launch and nowhere else: this is deliberately absent from `Settings`, because
    # a persisted toggle is set for the session the operator is watching and forgotten
    # for the four they are not.
    #
    # The launch-time choice only. A session's live policy moves after it starts, via
    # `driver.AgentSession.set_policy`, so no surface may render this as what a running
    # session is currently gated by.
    policy: Policy = Policy.STRICT
    # How many sub-agents this one session may run at once, or None to use
    # ``Settings.subagent_cap``. ``app._launch`` is the only reader.
    #
    # None rather than a number, and the distinction is load-bearing twice. It keeps a
    # spec built without one behaving exactly as every spec did before the field
    # existed; and it leaves ``0`` free to mean what it says -- a session that may not
    # spawn at all, which is a thing an operator might want under the autonomous mode
    # and which ``spec.subagent_cap or settings.subagent_cap`` would silently eat.
    #
    # **Not carried by ``from_record``, for a different reason than ``containment``
    # and ``policy`` above.** Those are dropped because they are permissions and
    # ``ui/health.py`` forks with no modal and no confirmation, so a permission
    # arriving through a surface nobody saw is a permission nobody chose. A cap is
    # capacity, not permission, and that argument does not reach it. It is dropped
    # because ``AgentRecord`` does not hold one: there is nothing on a record to
    # carry, and the setting it would fall back to is the operator's current answer
    # rather than the one that happened to be in force when the session first ran.
    # Re-reading it is the behaviour a relaunch wants.
    subagent_cap: int | None = None
    # What the session is sent first, when that is not ``task``: the operator's
    # message on a resume. None sends ``task``, which is every fresh launch.
    #
    # Separate from ``task`` because ``task`` is also what the session's root is
    # announced with -- the card's title, and what ``from_record`` relaunches
    # from. A resume that carried "ok, carry on" there would retitle the session
    # and make Fork and Retry start a new one with it.
    #
    # Not carried by ``from_record``: a fork or a relaunch starts a conversation
    # from the task, and this is one message of the conversation it is not
    # continuing.
    message: str | None = None
    # Whether ``task`` is only a title: the CLI's label for a conversation
    # whose original task nothing recorded (``AgentRecord.titled_by_label``).
    #
    # A resume carries it so the announce can say it of the record it builds,
    # and the card keeps both its title and the fact about it. ``from_record``
    # carries it too, and there it is a refusal in waiting: a fork or a
    # relaunch has only ``task`` to open the new conversation with, and the
    # shell starts nothing from a spec that says its task is no prompt.
    task_is_label: bool = False

    @classmethod
    def from_record(cls, record: AgentRecord) -> LaunchSpec:
        """
        The spec that starts this session again -- a relaunch, or a fork of it.

        One constructor rather than the same four lines in ``inbox`` and ``health``.
        Row 9's defect was those two call sites building a launch by hand and one of
        them omitting the template; two hand-built copies of a growing argument list
        will disagree eventually, and the failure is silent because a launch that
        drops a field still launches.
        """
        return cls(
            task=record.task,
            model=record.model,
            cwd=record.cwd or ".",
            # Carried, unlike a field row 9 dropped: a relaunch or fork of a
            # session measured from one base must not silently start measuring
            # from another, which is what recomputing it here would risk.
            session_base=record.session_base,
            template=record.template,
            brief=record.brief,
            task_is_label=record.titled_by_label,
        )


class TranscriptPresence(enum.Enum):
    """
    Whether the CLI still has the conversation a session file describes.

    Three answers and not a flag: the listing that would say can itself fail,
    and "could not look" read as either of the others is a card offering a
    resume that cannot happen or refusing one that can.
    """

    PRESENT = "present"
    GONE = "gone"
    UNKNOWN = "unknown"


class EndedHow(enum.Enum):
    """
    How a session's last run under pptmstr ended, as its session file has it.
    The values are the file's spellings.
    """

    CLOSED = "closed"
    FAILED = "failed"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class Recalled:
    """
    What a session file knew about a session this process has not run.

    Its presence on a root's record (``AgentRecord.recalled``) is the statement
    "nothing here was observed by this process": there is no CLI process behind
    the record, no transcript was read, and nothing was spent.

    **Every time here is wall-clock seconds, and none of them may be put in the
    record's own time fields.** Those are ``time.monotonic()`` and exist to be
    subtracted from each other. A wall time converted to that clock is negative
    for anything before this boot, and falls behind by every suspend after the
    conversion, so "closed three hours ago" has to be worked out from
    ``last_active`` against a wall clock at the moment it is drawn.

    **None means the file did not say**, for every field. A reader shows the
    value or shows that it is not known; it does not show the default of the
    record field beside it. ``AgentRecord.policy`` on a recalled record is the
    case that matters: it holds ``STRICT`` because the field has no other way
    to say nothing, and the rung the session was granted is ``policy`` here
    (``AgentRecord.granted_policy`` answers for both kinds of record).
    """

    transcript: TranscriptPresence = TranscriptPresence.UNKNOWN
    # First launch under pptmstr, and the last turn end, end or launch.
    created_at: float | None = None
    last_active: float | None = None
    # None is a session that was killed, or one whose file does not say: the two
    # are not told apart by the file.
    ended_how: EndedHow | None = None
    # The rung as last granted, live changes included, or None when the file
    # names none this build knows.
    policy: Policy | None = None
    contained: bool | None = None
    # An estimate, for display. Never added to a total: it was not spent by this
    # process, and ``AgentRecord.usage`` on the same record is zero for that
    # reason.
    cost_usd: float | None = None
    # What the session's failure said, when it ended in one. Here and not in
    # ``AgentRecord.error``, which is the failure of a run this process saw.
    error: str | None = None
    # The last thing the operator sent the session, as the file has it: what
    # the resume form's message box opens with. None when the file holds none,
    # which includes a message too long for a file to keep.
    last_message: str | None = None
    # The sub-agent cap the session's last run was given.
    subagent_cap: int | None = None
    # Whether ``AgentRecord.task`` is the task the session was first launched
    # with. False for a session whose file records none -- one first run under
    # a build that wrote no file -- where the record's ``task`` is the CLI's own
    # label for the conversation: a title, and not a prompt to start a new
    # session from. ``created_at`` is then when this build first saw the
    # session and not when it began. None, like every field here, is that
    # nothing said; a reader acts on False.
    task_recorded: bool | None = None


class DetachRefusal(enum.Enum):
    """
    Why a session may not be taken off the fleet (``Snapshot.detach_refusal``).

    The first two are different mistakes and are told apart for that reason: one
    is a session still doing something, the other is one the operator sees as
    over and that is not.
    """

    NO_SUCH_SESSION = "no session under that id"
    # An agent of it is in a state that is not an end at all.
    NOT_ENDED = "an agent of it has not ended"
    # FAILED is an end a status update undoes: the driver reports an error
    # result with its client still connected, and the session's next message
    # makes it an ordinary session again. Closing it is what makes it final.
    FAILED_NOT_CLOSED = "it failed and has not been closed, so it can still answer"
    OWES = "something of it is waiting on you"


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
    # The directory ``cwd``'s writes are measured from, resolved once at launch by
    # ``tree.session_base``. Inherited by sub-agents alongside ``cwd``, because a write
    # is placed against the pair and splitting them would measure a sub-agent in one
    # base and its parent in another.
    #
    # Stored rather than derived, and the reason is stronger than for ``cwd``: this
    # follows from ``cwd`` *through the filesystem*, and the reducer does no IO.
    # Freezing the answer at launch is also what keeps a run's measurements in one
    # unit for its whole life -- a symlink on the operator's path re-pointed mid-run
    # would otherwise change the units underneath comparisons already made.
    #
    # None on a record written before this field existed, which ``relative_write``
    # reads as "measure against ``cwd``" -- exactly what it did then.
    session_base: str | None = None
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
    # Where this session's premises live, on a root record only, or None for a
    # session launched with nothing but its task. See ``LaunchSpec.brief`` for why
    # it is a path, why the store never holds the content, and why it is stored
    # rather than derived from ``cwd`` and the session id.
    #
    # Nothing reads it yet. That is the honest state of this row and not an
    # oversight: it makes premises *addressable*, which is the whole of step 1, and
    # the writer and the reader are steps 2 and 3. A path with no reader is a
    # smaller thing than a premise with no address.
    brief: str | None = None
    # Which allowlist this node's tool calls are measured against. Inherited by
    # sub-agents, because ``driver._policy_for`` classifies them under their
    # session's policy -- so a sub-agent that did not inherit here would carry a
    # record disagreeing with the gate that actually judges it.
    #
    # Stored for the same reason as ``cwd`` and ``template``: it is chosen at launch,
    # nothing else in the snapshot implies it, and it otherwise lives only on
    # ``driver.AgentSession``, which the UI must not reach into.
    #
    # ``STRICT`` rather than ``None`` on a record written without one, because STRICT
    # is what the gate classifies an unspecified launch against -- there is no
    # "unknown" state to be honest about, only the default the gate already applies.
    policy: Policy = Policy.STRICT
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
    # When this node last read a digest (``digest.py``), or None if it never has.
    #
    # The mark a round is counted from, and it is here for the reason a delivered
    # concern carries ``delivered_at``: "since I last asked" kept in the bus
    # handler would be state the snapshot cannot see, lost on the handler's next
    # construction and invisible to the pane that draws the mark as a divider.
    #
    # Stored, not derived: nothing else in the snapshot records that a read
    # happened. Monotonic, like every stamp it is compared with.
    digest_read_at: float | None = None
    # Set on the root of a session recalled from its session file and not run by
    # this process; None on every record a run announced. See ``Recalled``.
    #
    # Such a record is DONE, the one state that owes nothing, idles and cannot be
    # moved by a status update. Its ``started_at`` is 0.0 and its ``ended_at``
    # None, which is how a record says it holds no time: the wall times are in
    # the value here. ``usage`` is zero, ``context`` None and the transcript
    # empty because this process observed none of them.
    #
    # ``AgentSpawned`` for the id builds a fresh record, so a resume clears this
    # by construction.
    recalled: Recalled | None = None
    # The operator took this session off the fleet. On a root only, and it is the
    # stored half of the answer: **ask** ``Snapshot.off_fleet``, which also
    # requires that nothing of the session can act or is owed. The store clears
    # it on any step after which that stops holding, so it never outlives a
    # session's being over.
    detached: bool = False
    # The announce said ``task`` is the CLI's label for the conversation and
    # not the task the session was launched with: a session first run under a
    # build that recorded none, resumed in this window. Ask
    # ``titled_by_label``, which answers for a recalled record as well.
    task_is_label: bool = False

    @property
    def titled_by_label(self) -> bool:
        """
        Whether ``task`` is only a title -- a summary, a first prompt or the bare
        id -- and not a prompt a new session can be started from. A closed card
        says so through its file's account (``Recalled.task_recorded``), and the
        record a resume of it announces through ``task_is_label``, so the answer
        does not change when the card is resumed.
        """
        if self.recalled is not None:
            return self.recalled.task_recorded is False
        return self.task_is_label

    @property
    def granted_policy(self) -> Policy | None:
        """
        The rung this node was last known to be gated by, or None when nothing
        says: the record's own for a node a run announced, the session file's for
        a recalled one.
        """
        return self.policy if self.recalled is None else self.recalled.policy

    def with_(self, **changes: Any) -> AgentRecord:
        """Copy with fields replaced."""
        return dataclasses.replace(self, **changes)


ConcernId = str
TaskId = str


def _empty_map() -> Mapping[Any, Any]:
    return MappingProxyType({})


class Basis(enum.Enum):
    """
    How the author of a text says they know what it asserts.

    Three members and no more. They are the three ways a claim arrives at a board:
    its author executed something and is reporting the output; its author
    established it by reading code or records and executed nothing; or its author
    is passing on what another agent or a record said and checked neither. This
    repository already treats the first line as the one between a result and a
    guess (STYLE.md §2), and the second is the relay a lead performs every time an
    observation becomes a specification.

    **It is the author's statement and nothing else.** A sender cannot be wrong
    about who it is -- the gate establishes that -- and it can be wrong about this.
    Nothing checks it and nothing could: whether a suite was run is not a fact the
    snapshot holds. So every surface that shows a member shows it as said, never as
    found.

    **Unstated is ``None``, and there is deliberately no member for it and no
    default.** A text that arrived without one was not run, read or relayed as far
    as the board knows; it is unmarked, and filling in the likeliest member would
    put a claim in an author's mouth (STYLE.md §3, a default where the honest answer
    is a refusal). The fields below are ``Basis | None`` for that reason, and a
    reader of ``None`` is told the standing was not stated.

    No ordering is defined. A text of mixed standing is asked to carry the weakest
    that applies to anything in it, which is a rule for authors and is said to them
    in prose; nothing in the store compares two members.
    """

    RUN = "run"
    READ = "read"
    RELAYED = "relayed"

    @classmethod
    def stated(cls, raw: object) -> Basis | None:
        """
        The member an author's own spelling names, None when it named none, and
        ``ValueError`` when it named something that is not one.

        Absent and empty are both "did not say": a model handed an optional string
        fills it with ``""`` often enough that treating that as a mistake would
        refuse calls whose author meant to omit it. Case and surrounding space are
        forgiven, because ``READ`` is what agents write by hand. Nothing else is.

        Here rather than in ``bus.py`` because two readers apply it to the same
        arguments -- the handler that stores the basis and the approval row an
        operator signs it off from -- and a second spelling of the rule would let
        the row show a standing the handler then refuses.
        """
        if raw is None or not str(raw).strip():
            return None
        return cls(str(raw).strip().lower())


# The longest property a task may state.
#
# The property is one sentence and every surface shows it whole -- it is the one
# piece of a row's prose the board's budget never cuts -- so its length is paid in
# full by every board read, once per row, and has to be bounded where it is written
# instead. 300 is a judgement with two measurements behind it: the sentences this
# was built from ran 119 and 137 characters, so the limit is twice the longest with
# room to spare; and forty rows at the limit cost 12,000 characters, which leaves
# more than half of ``bus._MAX_BOARD_CHARS`` for everything that is not a property.
MAX_PROPERTY_CHARS = 300


def normalised_property(raw: object) -> str:
    """
    A stated property as one line: runs of whitespace collapsed, ends trimmed.

    A property is a sentence shown under a title, and a newline inside one would
    break the row it sits on in every surface that lays it out. Absent and blank
    both come back empty, which is "none was stated".

    Does not enforce ``MAX_PROPERTY_CHARS``. A refusal needs words and somebody to
    read them, so each writer measures with ``property_overrun`` and says so in its
    own voice; this is the spelling they all measure.
    """
    return " ".join(str(raw or "").split())


def property_overrun(text: str) -> int:
    """How many characters a normalised property is past the limit, or 0."""
    return max(0, len(text) - MAX_PROPERTY_CHARS)


# Spellings of "no" an author gives a flag it meant to leave unset.
_UNSET_FLAG = frozenset({"", "false", "no", "0", "none", "null"})


def stated_flag(raw: object) -> bool | None:
    """
    A boolean argument as its author gave it: True, False, or None when the value
    is not recognisably either.

    A JSON boolean is taken as it is. A string is read past its case, because a
    model fills a boolean with ``"true"`` and a tool that only knew ``True`` would
    drop the statement in silence. None is returned for anything else rather than a
    guess, and the two callers resolve it in opposite directions on purpose -- see
    ``stated_incident`` and ``bus.complete_task``.
    """
    if raw is None or raw is False:
        return False
    if raw is True:
        return True
    word = str(raw).strip().lower()
    if word in _UNSET_FLAG:
        return False
    if word in ("true", "yes", "1"):
        return True
    return None


def stated_incident(raw: object) -> bool:
    """
    Whether a concern's sender marked it as an incident.

    **Anything that is not a plain "no" is a yes.** ``incident="touched a file
    outside touches"`` is a sender saying something went wrong in the wrong
    argument, and reading it as unmarked would file a reported slip as ordinary
    mail. A concern wrongly carrying the marker costs a reader one look; one
    wrongly missing it is the uncounted incident the marker exists to end.
    """
    return stated_flag(raw) is not False


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
    # The task this concern is about, when the sender named one.
    #
    # Optional because most concerns are not about a board row -- a question to the
    # lead, an answer, a heads-up -- and requiring one would make agents invent a
    # task id to send a message.
    #
    # This is what lets "claimed, and there is an open concern about it" be derived
    # rather than stored. The alternative that keeps being reached for is a fourth
    # ``TaskState`` or a "held deliberately" flag, and ``TaskState``'s docstring
    # already refuses that shape: intent stored as a flag is kept true by whoever
    # remembers to update it. A builder that stalls a task for a good reason
    # *already* records the reason by posting a concern -- the link was the only
    # thing missing, so the board can now show a reason for every stalled row that
    # anyone bothered to explain, with no new state and no flag to forget.
    #
    # Not validated against the board. A concern naming a task that does not exist
    # is still a message that was sent and delivered, and refusing it would put the
    # reducer in the business of rejecting mail over a typo in an optional field.
    # The projection reports the dangling reference instead, the way ``missing``
    # already does for a dependency that was never declared.
    task_id: TaskId | None = None
    # How the sender says it knows what ``body`` asserts, or None when it did not
    # say. The sender's claim about the text as it wrote it: an operator's edit in
    # flight changes ``body`` and sets ``edited``, and leaves this alone, so the two
    # are read together.
    basis: Basis | None = None
    # The sender marked this as a report of something that went wrong: a rule
    # broken, a file written outside ``touches``, a harness that restored over a
    # live tree. The sender's own statement, like ``basis``, and nothing acts on
    # it -- delivery, the gate and the inbox treat a marked concern exactly as an
    # unmarked one. It exists so that slips are countable instead of being a
    # sentence inside somebody's result.
    #
    # False is "not marked", which is not "nothing went wrong": most slips are
    # reported in prose by an agent that did not think to set this.
    incident: bool = False


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

    ``NOT_THE_DECLARER`` and ``AMENDER_HELD_IT`` are the two ways an agent's
    amendment is turned away, apart for the same reason: the first is asking the
    wrong agent's question -- the declarer or the operator can make the change --
    and the second is the right agent asking for the one thing it may not have,
    whose recovery is the operator and not a retry. ``CLAIMER_AMENDED_IT`` is the
    second seen from the other side, on a claim: the task is there and is ready,
    and this agent is the one that may not take it.

    ``CLOSED_BY_OPERATOR`` is ``ALREADY_COMPLETE`` for a task the operator closed
    (``intents.TaskClosed``), apart because the agent that held it is in a
    different place from one finishing a task twice: it did not finish this, what
    it sent was not kept, and the files it was writing may be another agent's now.
    "Nothing is left to do" is the wrong thing to tell it.

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
    CLOSED_BY_OPERATOR = "closed_by_operator"
    NOT_THE_DECLARER = "not_the_declarer"
    AMENDER_HELD_IT = "amender_held_it"
    CLAIMER_AMENDED_IT = "claimer_amended_it"
    NOT_APPLIED = "not_applied"


class ClaimRefusal(enum.Enum):
    """
    Why a claim won no task.

    Its own enum and not more members of ``TaskRefusal``, because a claim is
    turned away for reasons no board write is: the task is there and is waiting
    on another, or the asker already has it. The members are apart for
    ``TaskRefusal``'s reason -- each has a different recovery, and "was not
    claimed" covering all of them sends an agent to read the board to find out
    which mistake it made.

    ``NOT_ON_YOUR_BOARD`` is one answer for an id nothing has and an id another
    session's board has. The asker can do the same thing about both, and telling
    them apart would report what is on a board it was never shown.

    ``NOTHING_CLAIMABLE`` is the only answer to a claim that named no task. It is
    not a mistake: the board is empty, or what is left is blocked, held, or
    something this agent gave back or wrote the specification of.
    """

    NOTHING_CLAIMABLE = "nothing_claimable"
    NOT_ON_YOUR_BOARD = "not_on_your_board"
    HELD = "held"
    ALREADY_YOURS = "already_yours"
    COMPLETE = "complete"
    WAITING = "waiting"
    AMENDED_BY_YOU = "amended_by_you"


def normalised_touches(paths: Iterable[str]) -> tuple[str, ...]:
    """
    File paths in the one spelling the overlap check compares.

    Two leads writing ``./pptmstr/store.py`` and ``pptmstr/store.py`` mean the same
    file, and an overlap check on raw strings would miss it -- silently, producing
    exactly the concurrent write the field exists to prevent. Order is preserved and
    duplicates are dropped, so the stored tuple reads back as the declarer wrote it.

    **What it does not do**, stated because the gap is the interesting part: it
    does not resolve a path against a session's ``cwd``, follow a symlink, or
    reconcile an absolute path with a relative one. Those need the filesystem, and
    the reducer does no IO. A declarer that mixes absolute and relative paths gets
    no protection, which is a reason for the briefing to ask for paths relative to the
    session's directory rather than a reason to put a ``Path.resolve`` in a pure
    function.

    **The strip is an anti-evasion control, not a convenience.** The declaring caller
    is a model choosing its own spelling, and the reducer compares what this stores,
    so a caller free to pad a path is free to miss the overlap check:
    ``"  pptmstr/store.py  "`` names the same file and would collide with nothing.
    ``store._auto_depends`` is the whole of what keeps two agents out of one file, so
    that miss costs a concurrent write rather than a tidier string.
    ``tests/test_bus.py:test_a_caller_cannot_evade_the_overlap_check_with_its_own_spelling``
    is what pins it.
    """
    out: list[str] = []
    for raw in paths:
        cleaned = raw.strip()
        if not cleaned:
            continue
        # posixpath rather than os.path: a task's files are repository paths, and
        # the separator should not depend on which machine the orchestrator runs on.
        cleaned = posixpath.normpath(cleaned)
        if cleaned in (".", "..") or cleaned in out:
            continue
        out.append(cleaned)
    return tuple(out)


# The tools whose approved call writes a named file. Spelled here rather than
# imported from ``approval``: the reducer's module graph stops at ``model``, and
# ``approval`` reads the disk. Deliberate duplication, and pinned by a test against
# ``approval.summarize``'s own branch, because unpinned duplication is the thing
# that drifts (`STYLE.md` §3).
#
# ``NotebookEdit`` is in the set and carries ``notebook_path`` rather than
# ``file_path``; reading only ``file_path`` records a notebook write as no write.
WRITING_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})


@dataclass(frozen=True, slots=True)
class ApprovedWrites:
    """
    What a task's approved tool calls said they would write, accumulated.

    **These are approved writes, not landed ones.** The gate sees a call it
    permitted; the tool can still fail afterwards, and a permission the operator
    granted is not a filesystem fact. A reader who treats these as disk state will
    eventually read a divergence that is a failed tool call.

    **Retained rather than derived, and it is the exception it looks like.**
    ``approval.render_diff`` reads the current file from disk to build the diff at
    park time, so the "before" state lives only inside the parked record: once the
    write lands there is no snapshot the line counts follow from. `STYLE.md` §1's
    *derive; do not store* asks whether another fact in the same snapshot implies
    this one, and after the write none does.

    **The line counts are a lower bound, and three inputs put them there.** A
    ``NotebookEdit`` parks with no diff at all and contributes a path and no lines. A
    ``Bash`` heredoc contributes neither, and is invisible to this and always will
    be. A write whose task was completed in the same assistant turn is attributed to
    no open task and contributes nothing anywhere.

    ``paths`` is relative to the session's directory and distinct, in the order first
    written, so it can be compared against a declaration written in the same units.
    ``unplaced``
    holds the ones that could not be put in those units -- an absolute path outside
    the writing agent's ``cwd``, or a write by an agent whose ``cwd`` is unknown.
    They are held apart because a units mismatch reported as an out-of-declaration
    write is a false accusation, and it is the failure this split exists to prevent.
    """

    paths: tuple[str, ...] = ()
    unplaced: tuple[str, ...] = ()
    lines_added: int = 0
    lines_removed: int = 0

    def plus(self, other: ApprovedWrites) -> ApprovedWrites:
        """
        This measurement and another. Paths merge as a set; lines sum.

        A file written ten times is one path and ten writes' worth of lines. Summing
        the path count instead would report ten files, which is the number `Item 3`'s
        one line would then show the operator.
        """
        return ApprovedWrites(
            paths=_merged(self.paths, other.paths),
            unplaced=_merged(self.unplaced, other.unplaced),
            lines_added=self.lines_added + other.lines_added,
            lines_removed=self.lines_removed + other.lines_removed,
        )


def _merged(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    return first + tuple(p for p in second if p not in first)


def count_diff_lines(diff: str | None) -> tuple[int, int]:
    """
    Lines added and lines removed in a unified diff. Pure: no disk, no clock.

    ``None`` and the empty string measure to zero rather than raising. Most of what
    an agent does parks with no diff -- ``Bash``, the network tools, ``NotebookEdit``
    -- so an exception here would fire on the ordinary case.

    **Stateful over hunks, and it has to be.** A ``@@`` header declares how many old
    and new lines its body holds, so the body is consumed against that budget and
    everything outside a body is skipped. Classifying by leading character instead
    would be fooled by this repository's own content: ``difflib`` prefixes a removed
    line with ``-``, so deleting a horizontal rule produces the physical line
    ``----``, and a ``---`` test applied to that reads a deletion as a file header.
    Inside a body the budget says the line is content, and ``----`` is unambiguously
    a removal.

    A diff whose before-text has no closing newline undercounts by one. ``_unified``
    splits with ``keepends=True`` and ``difflib`` emits no *\\ No newline at end of
    file* marker, so the last removal and the following addition arrive joined into
    one physical line. That is one of the reasons ``ApprovedWrites`` calls its line
    counts a lower bound.
    """
    if not diff:
        return 0, 0
    added = 0
    removed = 0
    old_left = 0
    new_left = 0
    for line in diff.splitlines():
        hunk = _hunk_budget(line)
        if hunk is not None:
            old_left, new_left = hunk
            continue
        if old_left <= 0 and new_left <= 0:
            continue
        if line.startswith("+"):
            added += 1
            new_left -= 1
        elif line.startswith("-"):
            removed += 1
            old_left -= 1
        elif line.startswith("\\"):
            # A "no newline" marker belongs to the line above and is not one itself.
            continue
        else:
            old_left -= 1
            new_left -= 1
    return added, removed


def _hunk_budget(line: str) -> tuple[int, int] | None:
    """
    How many old and new lines the hunk this line opens holds, or None if it opens
    no hunk. A range without a length is one line, which is the unified format's own
    default and not a guess.
    """
    if not line.startswith("@@"):
        return None
    body = line[2:].partition("@@")[0].split()
    if len(body) != 2 or not body[0].startswith("-") or not body[1].startswith("+"):
        return None
    lengths = []
    for part in body:
        _, sep, length = part[1:].partition(",")
        if sep and not length.isdigit():
            return None
        lengths.append(int(length) if sep else 1)
    return lengths[0], lengths[1]


def written_path(tool_name: str, args: Mapping[str, Any]) -> str | None:
    """
    The file a parked call names, or None when it names none.

    **None is not evidence that nothing is written.** ``Bash`` names no path and can
    write anything; a heredoc is invisible here and always will be. This answers
    "which file did the call declare", and a clean record over a session of ``Bash``
    calls has earned nothing.

    **The key is chosen by the tool, not by which one happens to be filled in.**
    ``NotebookEdit`` declares ``notebook_path`` and the other three declare
    ``file_path``, so a call carrying both has one target and one decoy. Reading the
    first truthy key let the decoy answer for it: a ``NotebookEdit`` naming a
    harmless ``file_path`` was measured on that while the tool acted on
    ``notebook_path``. Whether the CLI forwards an argument a tool's schema does not
    declare is unmeasured -- ``driver._stamp_bus_call`` records the same gap and
    writes its stamp unconditionally rather than resting on it -- and a containment
    check that is correct only if an unmeasured behaviour does not happen is the
    defect whether or not it happens.

    The path itself is returned exactly as it arrived, unstripped and unresolved.
    ``planning/2026-09-03`` §11 U5 is the constraint: this, ``normalised_touches``
    and ``relative_write`` must normalise identically or a declaration and a write
    are compared in different units, and a caller that needs a stricter reading of
    the spelling does it on top of this rather than by changing it here.
    """
    if tool_name not in WRITING_TOOLS:
        return None
    raw = args.get("notebook_path" if tool_name == "NotebookEdit" else "file_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return raw


def relative_write(path: str, cwd: str | None, session_base: str | None = None) -> str | None:
    """
    A written path in the units a declaration is written in, or None when the two
    cannot be put in the same units.

    ``session_base`` is the base those units are measured from: the directory the
    session was launched in, resolved once at launch by ``tree.session_base`` and
    carried on ``AgentRecord``. It defaults to None, which measures against ``cwd``
    instead -- the behaviour of every record written before the field existed, and the
    reason adding it does not reinterpret history.

    **Both are needed, not either.** ``session_base`` places an absolute write, and
    ``cwd`` is what a *relative* write was typed against. The two are the same
    directory for a session launched today, and they are separate arguments because
    nothing in this function may assume that: a record may carry a base and no ``cwd``
    at all, and a sub-agent's pair is inherited rather than supplied. A model standing
    in ``<base>/pptmstr`` writing ``store.py`` means ``pptmstr/store.py``, and reading
    that against the base alone would record ``store.py`` and report a compliant agent
    as having written outside its declaration.

    A declaration is relative to the session's directory by construction:
    ``normalised_touches`` reconciles no absolute path with a relative one and says so,
    and ``bus`` and ``templates`` tell every lead the same. A write path is whatever
    the model passed. Comparing the two directly reports every write as
    out-of-declaration, which is the reverse of useful -- an affordance that fires on
    every row.

    ``cwd`` is the writing agent's own (``AgentRecord.cwd``, inherited from the
    parent for a sub-agent), so this is a string operation on facts already in the
    snapshot rather than a filesystem question. It follows no symlink and resolves
    nothing: an absolute path that is not under ``cwd`` returns None, and the caller
    records it as unplaced rather than as a divergence.
    """
    cleaned = path.strip()
    if not cleaned:
        return None

    base = _absolute(session_base)

    if not posixpath.isabs(cleaned):
        # A relative path is whatever the model typed, and it typed it against its
        # own ``cwd``. Expressing it against the base needs the step between the two;
        # skipping that step records a write to ``<base>/pptmstr/store.py`` as
        # ``store.py`` and then reads it as out-of-declaration.
        #
        # ``inner`` is "" both when there is no base to rebase onto and when the agent
        # already stands at it -- in either case the path is already in the units a
        # declaration is written in, which is the case every session launched today is
        # in.
        inner = "" if base is None else _within(base, _absolute(cwd))
        if inner is None:
            # A cwd that is not under the base it is paired with. The two disagree
            # about where zero is, and guessing which to believe would rebase the
            # write onto a tree it has nothing to do with.
            return None
        candidate = posixpath.normpath(posixpath.join(inner, cleaned)) if inner else cleaned
        if candidate == ".." or candidate.startswith("../"):
            # Climbed out of the base. There is no spelling of it in the declaration's
            # units, so the caller records it as unplaced rather than as a divergence.
            return None
        normalised = normalised_touches((candidate,))
        return normalised[0] if normalised else None

    measured_from = base or _absolute(cwd)
    if measured_from is None:
        return None
    prefix = measured_from if measured_from.endswith("/") else measured_from + "/"
    target = posixpath.normpath(cleaned)
    if not target.startswith(prefix):
        return None
    return target[len(prefix) :] or None


def _absolute(path: str | None) -> str | None:
    """
    ``path`` normalised, or None unless it is absolute. Pure: no filesystem.
    """
    if not path:
        return None
    base = posixpath.normpath(path)
    return base if posixpath.isabs(base) else None


def _within(base: str, inner: str | None) -> str | None:  # noqa: D401
    """
    ``inner`` expressed relative to ``base``: "" when they are the same directory,
    None when ``inner`` is not under ``base``.

    None rather than "" for the not-under case, because those are different answers.
    Treating an unrelated directory as the base itself would rebase every relative
    write in it onto a root it has nothing to do with.
    """
    if inner is None:
        return None
    if inner == base:
        return ""
    prefix = base if base.endswith("/") else base + "/"
    return inner[len(prefix) :] if inner.startswith(prefix) else None


def approved_write(
    tool_name: str,
    args: Mapping[str, Any],
    cwd: str | None,
    diff: str | None,
    session_base: str | None = None,
) -> ApprovedWrites:
    """
    One resolved approval, measured. Pure: the arguments are the whole input.

    ``args`` is what the operator approved. When a parked call was edited before it
    was approved that is ``edited_args`` over ``raw_args``, because correcting a
    wrong path is the first reason ``driver._park`` gives for edit-then-approve --
    so taking the path from the original arguments would name a file that was never
    written and miss the one that was.

    The line counts come from the diff regardless, and that diff was rendered from
    the arguments as parked. So on an edited call the counts describe what the
    operator reviewed rather than what reaches disk. They cannot be recomputed here:
    the "before" text came off the disk at park time and this function does no IO.
    """
    added, removed = count_diff_lines(diff)
    raw = written_path(tool_name, args)
    if raw is None:
        return ApprovedWrites(lines_added=added, lines_removed=removed)
    placed = relative_write(raw, cwd, session_base)
    if placed is None:
        return ApprovedWrites(unplaced=(raw,), lines_added=added, lines_removed=removed)
    return ApprovedWrites(paths=(placed,), lines_added=added, lines_removed=removed)


@dataclass(frozen=True, slots=True)
class TaskVersion:
    """
    A specification a task used to have, and who replaced it when.

    Only ever a *prior* text: the one a task has now is on the task
    (``Task.detail``, ``Task.property``, ``Task.basis``), so a history that also
    held it would be a second copy to keep in step. ``Task.superseded`` is these in
    the order they stood.

    ``replaced_by`` is None for the operator, the way
    ``intents.TaskAmended.node_id`` is. It is also who wrote the text that came
    *next*, which is what makes every version's author readable without a second
    field: version one is the declarer's, and version n+1 is by whoever replaced
    version n.
    """

    detail: str
    property: str
    # The standing its own author gave this text, which left the task with it.
    basis: Basis | None
    replaced_at: float
    replaced_by: NodeId | None = None


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
    # The files this task will write, normalised (``normalised_touches``).
    #
    # Stored rather than derived, and checked against STYLE.md §1 before being
    # added: nothing else in the snapshot implies which files a task will edit --
    # the work has not happened yet -- so this is the same case as ``declared_by``
    # and not a second copy of a fact something else already holds.
    #
    # It exists because per-file ownership was prose inside ``detail``, honoured by
    # every agent that read it and reached by nothing else. A lead declared two
    # tasks over the same three files with no dependency between them, two agents
    # wrote those files at once, and the only reason it cost minutes rather than a
    # corrupted file is that a worker noticed and released. ``depends_on`` is the
    # mechanism that prevents it, it is unbypassable, and it was simply not
    # invoked -- so the store derives it from this instead of trusting that the one
    # participant nothing checks remembered to.
    touches: tuple[str, ...] = ()
    claimed_by: NodeId | None = None
    state: TaskState = TaskState.PENDING
    declared_at: float = 0.0
    completed_at: float | None = None
    # Who put it on the board. Stored rather than derived because nothing else in
    # the snapshot implies it: there is one Store for the whole fleet, so `tasks` is
    # a global map, and an unclaimed task has no other node attached to it. Without
    # this a board cannot be scoped to the session whose agents are working it.
    declared_by: NodeId | None = None
    # The agents that took this task and gave it back, in the order they did.
    #
    # Stored rather than derived, and it is the one kind of fact that has to be:
    # release history is not implied by anything else in the snapshot. ``claimed_by``
    # is cleared by the release that creates this entry, so after it there is nothing
    # left to distinguish a task an agent has already declined from one it has never
    # seen. STYLE.md §1's question is whether another fact in the same snapshot
    # implies this one, and none does.
    #
    # It is not the ``claim_id`` mistake this record's docstring describes. That was a
    # transport correlation token, meaningful only to the request that minted it and
    # obliging every later arm to clear it. This is a domain fact -- an agent declined
    # this work -- and it is as much the board's business as who declared it.
    #
    # ``store._pick_claim`` consults it for a bare claim only, so a declined task is
    # skipped by the agent that declined it, stays claimable by every other agent, and
    # is still takeable by id by anyone including its releaser. A task that no agent
    # could claim at all would be worse than the loop this prevents: a board nobody is
    # watching cannot report a task it has quietly lost.
    #
    # Distinct, so it grows by one per agent that declines rather than once per
    # release, which is what keeps it small without a cap. Its ceiling is the number
    # of agents that have ever run against this board, and a cap would have to answer
    # "which release do we forget?" -- whose only safe answer is the one that
    # reintroduces the loop.
    released_by: tuple[NodeId, ...] = ()
    # What the approvals resolved while this task was claimed measured, accumulated.
    #
    # An accumulator rather than a projection, and it is the one place in this record
    # that has to be: each addend exists only inside the ``PendingApproval`` that
    # carried it, and that record is dropped in the same reducer arm that adds it
    # here (``ApprovedWrites`` carries why it cannot be recovered afterwards).
    #
    # Zero and unmeasured read the same, deliberately. A task that wrote nothing and
    # a task whose every write was a ``Bash`` heredoc both report zero, and nothing
    # here distinguishes them because nothing in the snapshot can.
    writes: ApprovedWrites = ApprovedWrites()
    # What the claimer said about the work when it finished: what was done, what was
    # run, what was not. Empty when it said nothing, which is an ordinary completion.
    #
    # Stored, and checked against STYLE.md §1 first: the only other place this text
    # exists is the transcript of the agent that wrote it, which no other agent can
    # read and no snapshot holds. A sub-agent's answer to its lead
    # (``AgentRecord.deliverable``) is a different fact with a different reader -- it is
    # about a node, it goes to one parent, and a worker that completes three tasks
    # has one of those and three of these.
    #
    # Written by ``TaskCompleted``, from the node holding the claim, and by
    # ``TaskClosed``, which composes the operator's closing words
    # (``closing_result``; ``closed_by_operator`` says which it was). Nothing
    # clears it and nothing has to: COMPLETED is terminal (a finished task can be
    # neither released nor claimed), so there is no later owner for a result to be
    # stale against. ``TaskAmended`` leaves it alone for the reason it leaves the
    # claim alone -- it rewrites what was asked, not what was reported.
    result: str = ""
    # How the author of ``detail`` says it knows what it asserts, or None when it
    # did not say. Replaced with the text by ``TaskAmended``: an amended ``detail``
    # is its amender's, so it carries the standing the amender gave it -- none, for
    # the operator -- and never the one the previous author gave other words.
    basis: Basis | None = None
    # The same statement about ``result``, by the claimer that wrote it. Its own
    # field rather than one shared with ``basis`` because the two texts have
    # different authors and are written at different times -- a specification
    # relayed from a reviewer is routinely completed by a builder that ran it.
    # None whenever ``result`` is empty: there is no text for it to be about.
    result_basis: Basis | None = None
    # The one property the work may not break, as one line, or empty when the
    # declarer stated none. Part of the specification: the operator signs it off
    # with the declaration, a claimer is handed it with ``detail``, and
    # ``TaskAmended`` is the only thing that changes it afterwards. Its own field
    # rather than a sentence inside ``detail`` because it is the part a reviewer
    # ranks findings against, and prose inside a specification is cut with the
    # specification on a crowded board.
    property: str = ""
    # What the claimer says its completion did NOT execute. Three values, and the
    # type is what keeps them apart:
    #
    # - ``None``: it did not say. The ordinary case, and never shown as "nothing".
    # - text: what was written and not run, in its words.
    # - ``""``: it stated that nothing its result names was left unrun -- the list
    #   of unrun things, given and empty.
    #
    # Written by the ``TaskCompleted`` arm with ``result``, and by nothing else.
    # The empty statement is kept only beside a result, because "everything I
    # named was run" names nothing when there is no result; text is kept either
    # way, since it says something on its own.
    not_run: str | None = None
    # When this task was last given back, or None if it never was.
    #
    # Stored because nothing implies it: a release clears ``claimed_by`` and adds
    # to ``released_by``, which is the set of agents that ever declined the task
    # and carries neither an order in time nor a time. A digest needs to say a
    # task came back during a round, and without this a released task and one
    # never claimed are the same record.
    released_at: float | None = None
    # Every specification this task had before the one it has now, oldest first.
    # Empty for a task that was never amended.
    #
    # Stored because an amendment overwrites ``detail`` and ``property``, and after
    # it nothing else in the snapshot holds what a claimer was working to or what
    # a finished result was a result of. Written by one arm, ``TaskAmended``, and
    # appended to only when the specification actually changed.
    superseded: tuple[TaskVersion, ...] = ()
    # The version the claimer was handed when it took the task, or None when
    # nobody holds it. ``claim_task`` copies the specification into the claimer's
    # context once, so this is the one version the board knows that agent has read.
    #
    # Stored because nothing implies it: a claim leaves no time behind, so it
    # cannot be placed among the amendments afterwards. Set by the claim, moved
    # forward by ``TaskVersionHanded`` when the holder is shown a later text,
    # cleared by a release and by the operator's close, and left alone by an
    # amendment and by the completion
    # -- which is what makes ``completed_version`` the text the result was written
    # against and not whatever stood on the board when the result arrived. Only
    # a reply that carries the text whole to its holder counts as a showing
    # (``intents.TaskVersionHanded`` names them); a holder that met a later
    # version in a listing is credited with the earlier one, which errs toward a
    # reader checking, never away from it.
    claimed_version: int | None = None
    # Every agent that has claimed this task, once each, in the order they first
    # did. Whoever holds it now or finished it is in here, and so is everyone who
    # gave it back.
    #
    # Stored, and not the same fact as ``released_by`` plus ``claimed_by``: an
    # amendment empties ``released_by`` because a declined offer no longer
    # exists, and "this agent has worked to this task's specification" has to
    # outlive exactly that. It is what the rule that an agent may not replace a
    # specification it has been bound by is asked of (``store._amender_refusal``).
    # Written by the claim arm and by nothing else; nothing clears it.
    held_by: tuple[NodeId, ...] = ()
    # The operator closed this task (``intents.TaskClosed``): it is COMPLETED
    # because the operator said to stop waiting for it, and no agent finished it.
    #
    # Stored, and not read off ``result``, which opens with
    # ``CLOSED_BY_OPERATOR`` on such a task: a claimer can type those words into
    # its own result, and who ended a task is the one thing about it that must
    # not be something an agent can write. Nor off "COMPLETED with no holder",
    # which a task built by hand also is. Written by one arm and never cleared.
    closed_by_operator: bool = False

    # ``builtins.property`` by its full name below: inside this class the bare name
    # is the ``property`` field above.

    @builtins.property
    def version(self) -> int:
        """
        Which specification this is, counting the declaration as one.
        """
        return len(self.superseded) + 1

    @builtins.property
    def amended_at(self) -> float | None:
        """
        When the specification was last replaced, or None if it never was.
        """
        return self.superseded[-1].replaced_at if self.superseded else None

    @builtins.property
    def amended_by(self) -> NodeId | None:
        """
        Who last replaced the specification. None is the operator -- or nobody,
        which ``amended_at`` tells apart.
        """
        return self.superseded[-1].replaced_by if self.superseded else None

    @builtins.property
    def completed_version(self) -> int | None:
        """
        The version the result was written against, or None when there is no
        completion or the claim behind it recorded no version.

        Derived: a completion keeps ``claimed_version``, so a field of its own
        would be a copy of it on every finished task.
        """
        return self.claimed_version if self.state is TaskState.COMPLETED else None

    def was_amended_by(self, node: NodeId) -> bool:
        """
        Whether ``node`` wrote any version of this task after the first.

        Read off the history. An amendment that changed nothing added no version
        and does not count: nothing was rewritten.
        """
        return any(prior.replaced_by == node for prior in self.superseded)

    def wrote_outside_declaration(self) -> tuple[str, ...]:
        """
        The files this task wrote that ``touches`` does not name, in write order.

        Derived rather than stored: both sides are on this record, so a second field
        would be a copy of a fact this snapshot already implies -- kept true by
        whoever remembers to update it, which `STYLE.md` §1 names as the shape of
        this codebase's sync defects.

        **A task that declared nothing diverges from nothing.** Empty ``touches`` is
        the absence of a declaration rather than a declaration of no files, and
        reading it the other way makes every write by such a task out-of-scope --
        turning the one line the operator is meant to trust into a row that is always
        lit. What granularity a directory-shaped declaration should have is a
        separate question and is not answered here: the comparison is over whole
        normalised paths.

        Silence here is not compliance. ``ApprovedWrites.unplaced`` and every
        pathless tool are outside what this can see.
        """
        if not self.touches:
            return ()
        declared = frozenset(normalised_touches(self.touches))
        return tuple(p for p in self.writes.paths if p not in declared)

    def belongs_to(self, session_id: str) -> bool:
        """
        Whether this task is on ``session_id``'s board.

        Scoped by declarer, because a task carries no session otherwise and an
        unclaimed one carries no node at all. Scoping by *claimer* would empty a
        fresh board until somebody took something, which is the reverse defect.

        **An undeclared-by task belongs to no session, and that is deliberate.**
        Nothing in the application produces one -- ``bus.declare_task`` stamps the
        sender the gate authenticated, and an unstamped call raises rather than
        defaulting -- so the only sources are tests and a future operator-side
        write. Answering True here would make such a task claimable by every
        session at once, which is precisely the "claim work you were never shown"
        hole this predicate closes.

        One predicate rather than two agreeing filters: ``board.board_tasks`` shows
        what it returns and ``store._pick_claim`` hands out what it returns, and
        those two had drifted -- the pane filtered by session and the reducer
        filtered by nothing.
        """
        return self.declared_by is not None and self.declared_by[0] == session_id

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
        blocked. So is one naming a task of another session's board
        (``dependency``). Whether such an id was ever declared on this board is
        a further question, and ``dependency`` returning None is its answer.

        Answered for a task in any state. A CLAIMED or COMPLETED task normally has
        none outstanding, but nothing in the store enforces that -- a task can be
        claimed and its dependency released afterwards -- and returning the honest
        graph answer is cheaper than a state guard that hides it.
        """
        return tuple(
            d
            for d in self.depends_on
            if (dep := self.dependency(tasks, d)) is None or dep.state is not TaskState.COMPLETED
        )

    def dependency(self, tasks: Mapping[TaskId, Task], task_id: TaskId) -> Task | None:
        """
        The task ``task_id`` names on this task's own board, or None when that
        board has no such task.

        **An id only another session's board has is not on this one.** ``tasks``
        is one map for every session, so an id written into ``depends_on`` can be
        answered by a stranger's task -- by a lead's own choice of id, or because
        a restore left this board's task of that name out for the collision. A
        task waiting on that would be waiting on work its board does not show,
        and would be released by a completion nobody on it made. It reads as
        missing instead: unsatisfied, and said to be.

        A task with no recorded declarer is on no board (``belongs_to``), so
        there is none to scope its dependencies to and the id is looked up as it
        is. Nothing in the application produces one.
        """
        other = tasks.get(task_id)
        if other is None or self.declared_by is None:
            return other
        return other if other.belongs_to(self.declared_by[0]) else None


# What an agent of an earlier run of its session is called on every surface, after
# its address (``Snapshot.of_earlier_run``). ``board.FOREIGN`` is the same
# qualifier in space; this one is in time.
#
# It says "run" and not "restart" because two things end a run and only one of
# them is pptmstr restarting: a session resumed inside one window starts a new
# run too, and nothing was restarted.
EARLIER_RUN = "from an earlier run"
# The name the qualifier is imported under by the modules that write it into what
# an agent reads. One value, so a board row and a bus reply cannot call one agent
# two things.
BEFORE_RESTART = EARLIER_RUN

# What leads the result of a task the operator closed (``intents.TaskClosed``).
CLOSED_BY_OPERATOR = "closed by the operator"


def closing_result(reason: str) -> str:
    """
    The result of a task the operator closed: ``CLOSED_BY_OPERATOR``, and behind
    it the operator's reason exactly as typed. A reason of nothing but
    whitespace is no reason, and the label stands alone.

    Composed here, once, because two things write it and must agree to the
    character: the reducer's arm, and a board log reading the close back.
    """
    return f"{CLOSED_BY_OPERATOR}: {reason}" if reason.strip() else CLOSED_BY_OPERATOR


# The operator's reason for closing a task. A close whose reason is longer is not
# applied (the ``TaskClosed`` arm), and is never cut: the reason becomes the
# task's result and the body of what its holder is told, byte for byte, and a
# pasted file there is a board record no log will take. The box that takes the
# reason reads this to refuse a longer one with the count, since the arm's
# refusal has nobody to reply to. The size of an operator's answer to a decision,
# which is the same kind of text.
MAX_CLOSE_REASON_CHARS = MAX_ANSWER_CHARS

# The agent id of the node standing for the operator as the sender of a concern
# (``operator_party``). The ``/`` is there for the reason ``_ADDRESS_ONLY`` has
# one: no id the CLI hands an agent contains it.
_OPERATOR_PARTY = "operator/"


def operator_party(session_id: str) -> NodeId:
    """
    The node a concern from the operator is sent by, on ``session_id``'s board.

    A concern has a sender and a board is scoped by its sender's session, so
    the operator's mail needs a node in the session it is about. Not an agent
    and never one: nothing spawns under it, it has no record and no address,
    and no bus call can be stamped with it -- the gate stamps a caller with the
    id the CLI gave it. So a concern from this node was filed by the store, on
    the operator's action, and by nothing else.

    Deliberately not the session's root. A message the lead did not write,
    shown as the lead's, is the one misattribution a reader would never think
    to doubt.
    """
    return (session_id, _OPERATOR_PARTY)


def is_operator(node: NodeId) -> bool:
    """Whether ``node`` is ``operator_party``'s stand-in for the operator."""
    return node[1] == _OPERATOR_PARTY


# What the id of a ``closing_notice`` begins with, ahead of its task's id.
_CLOSING_NOTICE = f"{_OPERATOR_PARTY}closed/"


def is_closing_notice(concern_id: ConcernId) -> bool:
    """
    Whether a concern's id is one ``closing_notice`` made.

    For a reader that has a concern's projection and not its sender's node. A
    concern an agent posts is given its id by the bus, which mints none of this
    form, so the id says as much as the sender does.
    """
    return concern_id.startswith(_CLOSING_NOTICE)


def closing_notice(task: Task, holder: NodeId, reason: str, at: float) -> Concern:
    """
    What the holder of a task is sent when the operator closes it under them: a
    concern from the operator (``operator_party``) about that task, whose body
    is the reason exactly as typed and nothing else.

    The reason alone, because a body is one author's text: what a close does,
    and what the holder should do about it, are the words of whichever surface
    hands this over (``bus`` for an agent's inbox), said outside the quotation.

    The id is the task's. A task is closed once and ids are the store's keys,
    so there is one notice per task and a replay of the close cannot file a
    second.
    """
    return Concern(
        id=f"{_CLOSING_NOTICE}{task.id}",
        sender=operator_party(task.declared_by[0] if task.declared_by else holder[0]),
        recipient=holder,
        subject=f"the operator closed {task.id}, which you held",
        body=reason,
        posted_at=at,
        task_id=task.id,
    )


# Ahead of the address in the agent id of a party a log names by address alone.
# The ``/`` is the point: the CLI keeps a sub-agent's transcript in a file named
# for its agent id, so an id it hands out is one path component and this is not.
# That is read off the CLI's documentation and is not measured here.
_ADDRESS_ONLY = "restored/"


def restored_party(session_id: str, address: str) -> NodeId:
    """
    The node standing for a party a board log knows by address and not by id.

    Not an agent and never one: nothing spawns under it, so it has no record,
    holds whatever the log says it held, and reads no inbox. The store checks
    when it builds one that nothing in the snapshot already has the id;
    ``_ADDRESS_ONLY`` says why nothing later should.
    """
    return (session_id, f"{_ADDRESS_ONLY}{address}")


def is_address_only(node: NodeId) -> bool:
    """Whether ``node`` is a ``restored_party`` stand-in and not an agent's id."""
    return node[1] is not None and node[1].startswith(_ADDRESS_ONLY)


class Unplaced(enum.Enum):
    """
    Why something a board log holds is not in the store as the log has it.

    The first five are about one task or concern, and everything else the log
    held was placed around it. The last three are a restore that placed nothing:
    the store already had a board for the session and it was not the log's.
    """

    # Another session's board already has a task or concern under this id. Ids
    # are fleet-wide, and placing this one would have replaced theirs.
    OTHER_SESSION_HAS_ID = "another session already has this id"
    # The log says another session declared or sent it, so it is not this
    # session's to restore.
    NOT_THIS_SESSIONS = "the log attributes it to another session"
    # No step of the store leaves a task like this: finished with nobody having
    # held it, claimed by nobody or by another session's agent, amended by an
    # agent that may not amend it, or carrying a holder or a result in a state
    # that has neither.
    INCOHERENT = "no step of the store produces it"
    WOULD_CYCLE = "its dependencies would close a cycle"
    # The board handed over names the id more than once; the first was taken.
    REPEATED = "named more than once"
    # The store holds it for this session and holds it differently.
    STORE_DIFFERS = "the store already holds it, differently"
    # The store holds it for this session and the log has never heard of it.
    STORE_ONLY = "the store holds it and the log does not"
    # Nothing wrong with it; the restore it was part of placed nothing.
    WITHHELD = "not placed: the store's board is not the log's"


@dataclass(frozen=True, slots=True)
class NotPlaced:
    """One task or concern a restore left out, or found out of step, and why."""

    # "task" or "concern".
    what: str
    id: str
    why: Unplaced
    # For ``OTHER_SESSION_HAS_ID``, the session whose board has the id, when the
    # record under it names one; None for every other reason. Who holds an id
    # does not change while the record stands. Whether that session is one the
    # operator can see does, so it is asked when the report is read
    # (``Snapshot.off_fleet``) and is not kept here.
    holder: str | None = None


_REFUSED = frozenset({Unplaced.STORE_DIFFERS, Unplaced.STORE_ONLY, Unplaced.WITHHELD})


@dataclass(frozen=True, slots=True)
class Restore:
    """
    What became of one session's logged board when it was handed to the store.

    Kept on the snapshot (``Snapshot.restores``) and not returned as an effect:
    nobody is parked on it, three readers want it at different times -- the shell
    deciding whether to append behind the log, the pane, the digest -- and a
    party's name on the board is read from it for as long as the board is shown.

    **States what the store holds, never what one application did**, so the same
    board handed over twice leaves the same value: ``tasks`` and ``concerns``
    count what is in the store as the log has it, whether this application put
    it there or found it.

    **Ask** ``adoptable`` **before appending behind the log**, and ``short``
    before treating the board as the whole of what was logged.
    """

    session_id: str
    # The instant, on the store's clock, the log was read for the board the
    # store holds; every time that board carries is before it. None when nothing
    # from a log has entered the store for this session.
    at: float | None = None
    tasks: int = 0
    concerns: int = 0
    unplaced: tuple[NotPlaced, ...] = ()
    # ``boardlog.Board.short`` as the board handed over answered it: the log
    # holds more than its tasks and concerns show. Carried as the answer and not
    # worked out again from the fields below, which are some of the reasons and
    # need not be all of them.
    log_short: bool = False
    # ``boardlog.Board.damage`` in words, and how many records the log holds as
    # stray, of a kind this build does not define, and contested, and how many
    # times its writer stopped recording.
    log_damage: str | None = None
    log_stray: int = 0
    log_unrecognised: int = 0
    log_contested: int = 0
    log_stopped: int = 0
    # Every sub-agent of this session the board handed over names, under the
    # address the log last had for it -- empty where it had none. Named by
    # anything on that board, a task or concern this report lists as unplaced
    # included: the agent existed before the restart whether or not what it
    # touched could be placed. A restore that was refused whole adds none. A
    # node in here with no record in the snapshot is a party from before the
    # restart (``Snapshot.left_behind``).
    parties: Mapping[NodeId, str] = field(default_factory=_empty_map)

    @builtins.property
    def refused(self) -> bool:
        """
        Whether the last board handed over was applied not at all, because the
        store's board for the session was not the log's.
        """
        return any(left.why in _REFUSED for left in self.unplaced)

    @builtins.property
    def short(self) -> bool:
        """
        Whether the log says anything the store's board for this session does
        not: something unplaced here, or something the log could not give.
        """
        return bool(self.unplaced) or self.log_short

    @builtins.property
    def adoptable(self) -> bool:
        """
        Whether a record appended behind this log would come from a reducer that
        holds everything the log says: a board from it is in the store, nothing
        was left out, and the log gave all it had.

        The strict reading, on purpose. A log that is short stays short, so a
        session whose log has one stray record is never appended to again by a
        process that restored it. The other reading appends records the next
        restore will mark contested or never reach, behind a board the operator
        was shown as whole. Loosening it is dropping ``log_short`` from this
        line.
        """
        return self.at is not None and not self.short


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
    #
    # Roots that ran in this process come first, in the order they were launched;
    # recalled roots (``AgentRecord.recalled``) follow, in the order they were
    # recalled. A fleet recalled at startup would otherwise sit above everything
    # launched after it for the life of the process.
    order: tuple[NodeId, ...]
    # Everything waiting on the operator, across every agent, oldest first.
    #
    # One projection over every obligation kind, not one list per kind that agree by
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
    # Approved writes by a node holding more than one claimed task, which are
    # therefore attributed to none.
    #
    # A count and not a list, because the interesting quantity is how often the
    # attribution is impossible rather than which write it was. Nothing in the
    # snapshot says which of a node's open tasks a given write was for, and guessing
    # -- by declaration order, or by a claim timestamp added for the purpose --
    # would put a made-up number into the one series this instrument produces.
    #
    # Non-zero is a reading about the *board*, not about the writer: "one task at a
    # time" is prose in ``templates.py`` that ``_pick_claim`` does not enforce, and
    # this is what makes a breach of it visible instead of silent.
    unattributed_writes: int = 0
    # What became of each session's logged board, by session id: one entry per
    # session a ``BoardRestored`` was applied for, refused ones included.
    #
    # Stored because nothing implies it. A restored task is an ordinary task, so
    # afterwards the tasks and concerns say nothing of having come from a log,
    # of what the log held that is not among them, or of which of the nodes they
    # name were agents of an earlier process.
    restores: Mapping[str, Restore] = field(default_factory=_empty_map)
    # Every decision an agent has raised in this process, ended ones included: an
    # answer has to stay where its asker can read it again.
    decisions: Mapping[DecisionId, Decision] = field(default_factory=_empty_map)
    # Every break-glass command asked in this process or restored from a
    # session's files, ended ones included: what ran changed the operator's
    # machine, and its asker reads the result back later.
    commands: Mapping[CommandId, Command] = field(default_factory=_empty_map)
    # When each open command was asked, on the clock every obligation's
    # ``since`` is read from: the reducer's ``now`` at the step that filed it.
    #
    # Stored because nothing implies it. ``Command.asked_at`` is a wall-clock
    # time -- it is written to the command's files and read back by another
    # process -- and the queue sorts and ages its rows on a monotonic one. The
    # two have different origins, so a wall reading among the others would sort
    # last and show as the newest row in the queue.
    #
    # Only open commands have an entry: an ended one owes no row, and a
    # restored one is never open.
    command_since: Mapping[CommandId, float] = field(default_factory=_empty_map)

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

    def decisions_of(self, node: NodeId) -> tuple[Decision, ...]:
        """
        Every decision ``node`` raised, open or ended, oldest first.

        The asker's own and nobody else's: these are the ones ``node`` may
        withdraw, and the ones whose answer is the operator's words to it.
        """
        return tuple(
            sorted(
                (d for d in self.decisions.values() if d.asker == node),
                key=lambda d: d.raised_at,
            )
        )

    def decisions_readable_by(self, node: NodeId) -> tuple[Decision, ...]:
        """
        Every decision ``node`` may read, open or ended, oldest first: its own,
        and for the root of a session every decision raised in that session.

        The root reads them all because an answer is a record of what the
        operator decided for the session's work, and the agent that plans that
        work is its lead. A sub-agent's answer that reached the lead only when
        the sub-agent passed it on would arrive as an agent's paraphrase. Reading
        is all this widens: only the asker withdraws, and only the operator
        answers.
        """
        if node[1] is not None:
            return self.decisions_of(node)
        return tuple(
            sorted(
                (d for d in self.decisions.values() if d.session_id == node[0]),
                key=lambda d: d.raised_at,
            )
        )

    def commands_of(self, node: NodeId) -> tuple[Command, ...]:
        """
        Every command ``node`` asked for, open or ended, oldest first: the ones
        it may withdraw while they are only asked.
        """
        return tuple(
            sorted(
                (c for c in self.commands.values() if c.asker == node),
                key=lambda c: c.asked_at,
            )
        )

    def commands_readable_by(self, node: NodeId) -> tuple[Command, ...]:
        """
        Every command ``node`` may read, open or ended, oldest first: its own,
        and for the root of a session every command asked in that session.

        The root reads them all for the reason it reads every decision
        (``decisions_readable_by``): what the operator ran for the session is a
        fact its lead plans around. Reading is all this widens. Only the asker
        withdraws, and only the operator approves, refuses or stops.
        """
        if node[1] is not None:
            return self.commands_of(node)
        return tuple(
            sorted(
                (c for c in self.commands.values() if c.session_id == node[0]),
                key=lambda c: c.asked_at,
            )
        )

    def commands_awaiting(self, session_id: str | None = None) -> tuple[Command, ...]:
        """
        Every command that is asked, running, or left running unsupervised,
        oldest first: the ones that owe the operator a row
        (``Command.owes_row``). Of one session when ``session_id`` is given,
        and of every session when it is None.

        Running ones are among them because stopping one is the operator's and
        has to stay reachable, whatever has become of the asker. One left
        running by a pptmstr that is gone is among them because nothing but the
        operator can end it; it has ended as a record (``Command.is_open`` is
        False) and nothing here makes it runnable.
        """
        return tuple(
            sorted(
                (
                    c
                    for c in self.commands.values()
                    if c.owes_row and (session_id is None or c.session_id == session_id)
                ),
                key=lambda c: c.asked_at,
            )
        )

    def unsupervised_command(self, session_id: str) -> Command | None:
        """
        A command of ``session_id`` whose shell's session was found alive with no
        pptmstr holding it (``Command.unsupervised``), or None. The oldest when
        there are several.

        Not ``running_command``: nothing here can stop it, and a surface that
        offered a cancel for it would offer one that does nothing.
        """
        for held in self.commands_awaiting(session_id):
            if held.unsupervised is not None:
                return held
        return None

    def running_command(self, session_id: str) -> Command | None:
        """
        The command of ``session_id`` that is running, or None. There is at most
        one: an approval is not applied while there is one
        (``command_approval_refusal``).
        """
        for held in self.commands.values():
            if held.session_id == session_id and held.state is CommandState.RUNNING:
                return held
        return None

    def command_approval_refusal(
        self, command_id: CommandId, text: str, timeout_s: float
    ) -> CommandRefusal | RequestRefusal | None:
        """
        Why the operator's approval of ``command_id``, to run ``text`` under
        ``timeout_s``, would not be applied -- or None if it would.

        **This is the definition, and the reducer asks it here**
        (``intents.CommandApproved``). Nobody is parked on an approval to be
        told it was turned away, so the surface that offers Run asks first, with
        the draft it is about to send.

        ``text`` is the string that would run, the operator's edit or the
        asker's own, and it is held to ``command.command_refusal`` either way:
        an edit reaches the same shell. Only a command that is still only asked
        is approved, so one is approved at most once.
        """
        held = self.commands.get(command_id)
        if held is None:
            return CommandRefusal.NO_SUCH_COMMAND
        if held.state is not CommandState.ASKED:
            return CommandRefusal.NOT_ASKED
        unrunnable = command_refusal(text)
        if unrunnable is not None:
            return unrunnable
        if timeout_s not in TIMEOUT_CHOICES_S:
            return CommandRefusal.BAD_TIMEOUT
        if self.running_command(held.session_id) is not None:
            return CommandRefusal.ALREADY_RUNNING_IN_SESSION
        if self.unsupervised_command(held.session_id) is not None:
            # One command of a session at a time is the bound, and a process
            # nothing supervises is the case it matters most for.
            return CommandRefusal.ALREADY_RUNNING_IN_SESSION
        return None

    def get(self, node_id: NodeId) -> AgentRecord | None:
        return self.nodes.get(node_id)

    def left_behind(self, node: NodeId) -> str | None:
        """
        The address a restored board had for ``node`` when ``node`` is a party
        from before the restart, and None when it is not one.

        **This is what "gone" means wherever the store acts on it**: the node is
        named by its session's restored board and has no record in this process.
        Both halves are needed. A sub-agent that finished its turn has a record
        and can be woken, so it is not gone, however long it has been quiet. A
        node with no record that no log named is one that has not announced
        itself yet, and it may be working.

        The address is empty for a party the log had no name for, which is still
        not None.

        **"Has no record" is read as "never had one in this process", and that
        holds only while nothing takes a record away.** ``intents.AgentRemoved``
        does, and nothing emits it. An emitter that removed a restored party
        which had since been woken and had finished its turn would make that
        agent gone again here, and ``store._may_reclaim`` would hand its claim
        to the root -- the reading of "gone" that function's docstring rules
        out. Whatever starts emitting it has to settle that first.
        """
        if node in self.nodes:
            return None
        restore = self.restores.get(node[0])
        return None if restore is None else restore.parties.get(node)

    def of_earlier_run(self, node: NodeId) -> bool:
        """
        Whether ``node`` is an agent of an earlier run of its session: one whose
        claims and mail are not waiting on anything in the run that is going now.

        **This is the definition, and every reader asks it here** -- the store
        deciding whether a session's root may release a claim
        (``store._may_reclaim``), the board naming the agent and marking mail to
        it, the log recording a reclaim. A run of a session is one CLI process
        driving it. A run ends when pptmstr exits, and also when the session is
        resumed or relaunched inside one window; an agent is of an earlier run
        when either holds:

        - **Another process's.** ``left_behind``: the session's restored board
          names it and this process has no record of it.
        - **This process's, before the session was started again.** It is a
          sub-agent, its record is terminal, and it was spawned before the
          session's root was last announced (``started_at`` strictly less than
          the root's). ``AgentSpawned`` replaces a record whole and restamps
          ``started_at``, and a session announces its root again each time it is
          launched -- twice per launch, both before it connects and so before
          any sub-agent of that launch can start, which is why a repeat changes
          nothing here.

        **The second rule asks for both, and each half guards a different
        mistake.** The start stamp is what keeps a root from taking work from an
        agent of the run that is going now. The ended record is what keeps it from
        taking work from an agent that may still be writing: a stamp says when an
        agent began and nothing about whether it has stopped, and it is the
        ordering of announces, which is the pool's to keep, that would otherwise
        be the only thing standing between a relaunch and a live holder. So an
        earlier run's sub-agent has to be *made* terminal, and the driver does
        that: every path out of ``AgentSession.run`` ends each sub-agent that has
        not stopped, ahead of the root's own end
        (``driver.AgentSession._settle_subagents_at_run_end``). A run whose task
        is destroyed without unwinding says none of that, and what it held stays
        held until the operator closes it (``intents.TaskClosed``).

        **An agent of the run that is going now is not one, however long it has
        been quiet.** A sub-agent that finished its turn was spawned after the
        root's announce; it can be woken and may be holding a task across a
        wait. And an earlier run's agent that the CLI starts again stops being
        one: a new run's driver has not seen its id, so it is announced with
        ``AgentSpawned`` and restamped, and until it ends it is not terminal
        either.

        A root is never one by the second rule: the session's own lead is the
        run.
        """
        if self.left_behind(node) is not None:
            return True
        record = self.nodes.get(node)
        root = self.nodes.get((node[0], None))
        return (
            node[1] is not None
            and record is not None
            and root is not None
            and record.state.is_terminal
            and record.started_at < root.started_at
        )

    def detach_refusal(self, session_id: str) -> DetachRefusal | None:
        """
        Why ``session_id`` may not be taken off the fleet, or None if it may.

        **A session may leave only when nothing of it can act and nothing of it
        is owed**: its root is in a final state (``AgentState.is_final``), every
        sub-agent under the id is terminal, no obligation names one of its
        nodes, and no command asked in it is still asked or running. "Terminal"
        is not enough for the root. FAILED is terminal, the driver reports it with the
        client still connected, and the next message revives the record, so a
        failed session leaves only after it has been closed.

        **It is enough for a sub-agent.** A sub-agent has no process of its own:
        what could revive a failed one is its session's run, and a root in a
        final state is a run that has ended -- which the shell also asks the
        pool, below. A run that ends leaves the
        sub-agents it had given up on in FAILED for good, and nothing closes a
        sub-agent, so asking them to be final would keep every such session on
        the fleet with no way off. Should one be revived all the same, it is no
        longer terminal and this refuses again, which is what puts the session
        back (``off_fleet``).

        Asked of every record under the id and not of the root's subtree, so a
        sub-agent whose parent link is missing still counts.

        **This is the store's half.** Whether the pool still has a run under the
        id is not in a snapshot, and a caller that takes a session off the fleet
        asks the pool as well (``intents.SessionDetached``).
        """
        if (session_id, None) not in self.nodes:
            return DetachRefusal.NO_SUCH_SESSION
        states = [rec.state for nid, rec in self.nodes.items() if nid[0] == session_id]
        if any(not state.is_terminal for state in states):
            return DetachRefusal.NOT_ENDED
        if not self.nodes[(session_id, None)].state.is_final:
            return DetachRefusal.FAILED_NOT_CLOSED
        if any(owed.node[0] == session_id for owed in self.needs_you):
            return DetachRefusal.OWES
        if self.commands_awaiting(session_id):
            # Asked here of the records and not of ``needs_you``: a command
            # that is asked or running is owed whether or not a row is drawn
            # for it.
            return DetachRefusal.OWES
        return None

    def off_fleet(self, session_id: str) -> bool:
        """
        Whether ``session_id`` is off the fleet: the operator removed it, and it
        is still a session nothing of which can act or is owed.

        **This is the definition, and every reader asks it here** and not
        ``AgentRecord.detached``. The flag records that the operator asked; this
        adds that the asking still holds (``detach_refusal``), so a session that
        has come back to life or come to owe something is on the fleet whatever
        the flag says. The store clears the flag on the same condition, and this
        does not rely on it having done so.

        False for an id the store has no root for.
        """
        root = self.nodes.get((session_id, None))
        return root is not None and root.detached and self.detach_refusal(session_id) is None

    def children_of(self, node_id: NodeId | None) -> tuple[NodeId, ...]:
        return tuple(n for n in self.order if self.nodes[n].parent == node_id)

    def subagents_of(self, root: NodeId) -> tuple[AgentRecord, ...]:
        """
        Every descendant of a root session, in spawn order.

        No state filter, so the list is append-only: a terminal sub-agent stays on
        it. That is what makes it usable as the membership of a session rather than
        as a liveness signal, and the callers that want liveness ask the records.

        Structural rather than presentational, which is why it sits beside
        ``children_of`` -- ``order`` is a pre-order walk this class already owns, and
        the bus address projection in ``board.py`` needs it from outside ``ui/``.
        """
        return tuple(
            self.nodes[nid]
            for nid in self.order
            if nid != root and nid[0] == root[0] and self.nodes[nid].parent is not None
        )
