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
from typing import Any

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
      -> DONE | FAILED | CANCELLED | RATE_LIMITED
    """

    SPAWNING = "spawning"
    THINKING = "thinking"
    CALLING_TOOL = "calling_tool"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING_TOOL = "running_tool"
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
    usage: UsageRollup = field(default_factory=UsageRollup)
    context: ContextSnapshot | None = None
    pending: PendingApproval | None = None

    transcript: Transcript = field(default_factory=Transcript)
    started_at: float = 0.0
    ended_at: float | None = None
    error: str | None = None

    def with_(self, **changes: Any) -> AgentRecord:
        """Copy with fields replaced."""
        return dataclasses.replace(self, **changes)


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
    # Every pending approval across every node, oldest first. The review queue (§5.4)
    # is a projection over all agents, not a per-node list -- approving one write at
    # a time per agent is the bottleneck the batching exists to remove.
    review_queue: tuple[PendingApproval, ...]
    any_active: bool

    @staticmethod
    def empty() -> Snapshot:
        return Snapshot(
            seq=0,
            nodes=MappingProxyType({}),
            order=(),
            review_queue=(),
            any_active=False,
        )

    def get(self, node_id: NodeId) -> AgentRecord | None:
        return self.nodes.get(node_id)

    def children_of(self, node_id: NodeId | None) -> tuple[NodeId, ...]:
        return tuple(n for n in self.order if self.nodes[n].parent == node_id)
