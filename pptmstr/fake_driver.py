"""
A fake agent driver, for building the UI without the SDK.

Emits intents on a timer so every state the tree pane can render is reachable
without spawning a subprocess or spending a token. It is not a simulator and should
not grow into one: its job is to make states reachable, and it gets deleted when
step 3 lands rather than maintained alongside the real driver.

Runs on the Bridge's asyncio thread and talks to the UI exactly the way the real
driver will -- ``bridge.emit(...)`` and nothing else. That is the point of building
against it: if the pane works here, the only thing step 3 changes is where the
intents come from.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time

from .bridge import Bridge
from .intents import (
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    CompactionObserved,
    ContextPolled,
    StateChanged,
    TopicChanged,
)
from .model import AgentState, ContextSnapshot, NodeId, PendingApproval

_MODELS = ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001")
_TOPICS = (
    "reading pptmstr/store.py",
    "running pytest",
    "grepping for call sites",
    "writing tests/test_bridge.py",
    "waiting on approval",
    "summarising findings",
)
_TASKS = (
    "wire the approval gate",
    "audit the threading boundary",
    "port the tree pane",
    "chase a flaky test",
)
_TOOLS = (
    ("Read", {"file_path": "/home/wreel/Source/pptmstr/pptmstr/store.py"}),
    ("Write", {"file_path": "/tmp/out.txt", "content": "hello"}),
    ("Bash", {"command": "pytest -q"}),
    ("Edit", {"file_path": "pptmstr/app.py", "old_string": "a", "new_string": "b"}),
)

_ids = itertools.count(1)


def _context(
    used: int, *, threshold: int | None = 120_000, compactions: int = 0
) -> ContextSnapshot:
    return ContextSnapshot(
        used_tokens=used,
        max_tokens=180_000,
        raw_max_tokens=200_000,
        percentage=100.0 * used / 180_000,
        auto_compact_enabled=threshold is not None,
        auto_compact_threshold=threshold,
        model="claude-opus-5",
        polled_at=time.time(),
        compactions=compactions,
    )


class FakeDriver:
    """Drives a small tree of pretend agents through plausible transitions."""

    def __init__(self, bridge: Bridge, *, seed: int = 0) -> None:
        self.bridge = bridge
        self.rng = random.Random(seed)
        self.nodes: list[NodeId] = []
        self._stopping = False

    async def run(self) -> None:
        """Spawn a starting tree, then keep it moving until cancelled."""
        try:
            await self._seed_tree()
            while not self._stopping:
                await asyncio.sleep(0.6)
                self._tick()
        except asyncio.CancelledError:
            # Expected on shutdown; nothing to unwind.
            raise

    async def _seed_tree(self) -> None:
        root = self._spawn(parent=None, agent_type=None)
        await asyncio.sleep(0.1)
        for _ in range(2):
            self._spawn(parent=root, agent_type=self.rng.choice(("Explore", "code-reviewer")))
        second = self._spawn(parent=None, agent_type=None)
        self._spawn(parent=second, agent_type="general-purpose")

        # One of each interesting terminal/edge state, so the pane's less common
        # branches are visible on launch rather than only after a long wait.
        self.bridge.emit(ContextPolled(root, _context(30_000)))
        self.bridge.emit(ContextPolled(self.nodes[1], _context(112_000)))
        self.bridge.emit(ContextPolled(self.nodes[2], _context(60_000, threshold=None)))
        self.bridge.emit(ContextPolled(second, _context(90_000)))
        self.bridge.emit(CompactionObserved(second, at=time.time(), trigger="auto"))
        self.bridge.emit(ContextPolled(second, _context(8_000)))

        self.bridge.emit(ApprovalRequested(self.nodes[1], self._pending(self.nodes[1], "Write")))
        self.bridge.emit(
            AgentFinished(
                self.nodes[4],
                AgentState.FAILED,
                # monotonic, to match AgentRecord.started_at -- see model.AgentRecord.
                ended_at=time.monotonic(),
                error="tool call rejected",
            )
        )

    def _spawn(self, parent: NodeId | None, agent_type: str | None) -> NodeId:
        n = next(_ids)
        node: NodeId = (f"sess-{n}", None) if parent is None else (parent[0], f"agent-{n}")
        self.bridge.emit(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task=self.rng.choice(_TASKS),
                model=self.rng.choice(_MODELS),
                started_at=time.monotonic(),
                agent_type=agent_type,
                topic="starting",
            )
        )
        self.nodes.append(node)
        return node

    def _pending(self, node: NodeId, prefer: str | None = None) -> PendingApproval:
        name, args = next((t for t in _TOOLS if t[0] == prefer), self.rng.choice(_TOOLS))
        return PendingApproval(
            id=f"pending-{next(_ids)}",
            node=node,
            tool_name=name,
            tool_use_id=f"tu-{next(_ids)}",
            raw_args=args,
            summary=f"{name} {list(args.values())[0]}",
            requested_at=time.time(),
        )

    def _tick(self) -> None:
        """Move one random agent along. Deliberately shallow: this is a fixture."""
        if not self.nodes:
            return
        node = self.rng.choice(self.nodes)
        roll = self.rng.random()
        if roll < 0.45:
            self.bridge.emit(
                StateChanged(node, AgentState.THINKING, topic=self.rng.choice(_TOPICS))
            )
        elif roll < 0.7:
            self.bridge.emit(
                StateChanged(node, AgentState.RUNNING_TOOL, topic=self.rng.choice(_TOPICS))
            )
        elif roll < 0.8:
            self.bridge.emit(TopicChanged(node, self.rng.choice(_TOPICS)))
        elif roll < 0.9:
            self.bridge.emit(ApprovalRequested(node, self._pending(node)))
        else:
            self.bridge.emit(StateChanged(node, AgentState.RATE_LIMITED, topic="backing off"))

    def stop(self) -> None:
        self._stopping = True
