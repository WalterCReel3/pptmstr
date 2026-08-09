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

from .model import AgentState, ContextSnapshot, NodeId, PendingApproval, UsageRollup
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
    topic: str = "starting"
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
class AgentRemoved:
    """Dropped from the tree entirely. Descendants go with it."""

    node_id: NodeId


# Kept as an explicit union rather than a base class: it gives the store's match
# statement exhaustiveness checking under mypy, so adding an intent without
# handling it is a type error rather than a silently ignored state change.
Intent = (
    AgentSpawned
    | StateChanged
    | TopicChanged
    | UsageAccrued
    | ContextPolled
    | CompactionObserved
    | ApprovalRequested
    | ApprovalResolved
    | AgentFinished
    | AgentRemoved
)
