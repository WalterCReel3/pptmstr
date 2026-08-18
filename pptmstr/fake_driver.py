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
    ConcernPosted,
    ContextPolled,
    InboxRead,
    StateChanged,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TopicChanged,
)
from .model import (
    AWAITING_TOPIC,
    AgentState,
    Concern,
    ContextSnapshot,
    NodeId,
    PendingApproval,
    Task,
)
from .transcript import SegmentKind, Transcript

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
# Distinct directories so --fake exercises the project axis. A fake fleet that all
# shares one cwd renders as a single lane, which is the one arrangement the rail's
# grouping cannot be judged from.
_CWDS = (
    "~/Source/pptmstr",
    "~/Source/orbital",
    "~/scratch",
)
_TOOLS = (
    ("Read", {"file_path": "/home/wreel/Source/pptmstr/pptmstr/store.py"}),
    ("Write", {"file_path": "/tmp/pptmstr-demo.txt", "content": "hello\nworld\n"}),
    ("Bash", {"command": "pytest -q"}),
    # Long on purpose, and the only fixture here that is. Every other entry fits a
    # column, which is why the queue looked correct for as long as it did: a real
    # Bash call runs off the right edge of the row *and* past summarize's
    # 90-character clip, so the row is a lossy rendering of a command that could
    # delete something. DETAIL is the pane that has to hold this one.
    (
        "Bash",
        {
            "command": (
                "for f in $(git ls-files '*.py'); do "
                'python -m compileall -q "$f" || echo "FAILED $f" >> /tmp/compile.log; '
                "done && rg -n 'TODO|FIXME' pptmstr tests "
                "| awk -F: '{print $1}' | sort | uniq -c | sort -rn | head -20"
            )
        },
    ),
    (
        "Edit",
        {
            "file_path": "pptmstr/theme.py",
            "old_string": "DARK = Palette(",
            "new_string": "DARK = Palette(  # tweaked",
        },
    ),
)

_FRAGMENTS = (
    "checking the call sites ",
    "that looks right.\n",
    "hmm, the offsets do not line up ",
    "-> 3 matches\n",
    "re-reading the store to be sure ",
    "done.\n",
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
        self.transcripts: dict[NodeId, Transcript] = {}
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
        third = self._spawn(parent=second, agent_type="general-purpose")
        # A grandchild, so the pane's depth handling is exercised rather than assumed.
        self._spawn(parent=third, agent_type="Explore")

        # One of each interesting terminal/edge state, so the pane's less common
        # branches are visible on launch rather than only after a long wait.
        self.bridge.emit(ContextPolled(root, _context(30_000)))
        self.bridge.emit(ContextPolled(self.nodes[1], _context(112_000)))
        self.bridge.emit(ContextPolled(self.nodes[2], _context(60_000, threshold=None)))
        self.bridge.emit(ContextPolled(second, _context(90_000)))
        self.bridge.emit(CompactionObserved(second, at=time.time(), trigger="auto"))
        self.bridge.emit(ContextPolled(second, _context(8_000)))

        self.bridge.emit(ApprovalRequested(self.nodes[1], self._pending(self.nodes[1], "Write")))
        # Two more, on different nodes, so the queue is genuinely cross-agent and
        # the scoped batch button has something to act on.
        self.bridge.emit(ApprovalRequested(self.nodes[2], self._pending(self.nodes[2], "Edit")))
        self.bridge.emit(ApprovalRequested(self.nodes[3], self._pending(self.nodes[3], "Bash")))
        # A turn that ended with a question, its prose sitting behind a bulky tool
        # result. This is the only fixture for two things. AWAITING_INPUT is the
        # obligation kind nothing else here produces, so the inbox composer and
        # DETAIL's "what it said" had only ever been seen against hand-built
        # snapshots -- and this is the exact shape that made both of them render
        # machinery instead of words.
        asked = self._spawn(parent=None, agent_type=None)
        asked_transcript = self.transcripts[asked]
        asked_transcript.append(SegmentKind.TOOL_CALL, "Grep(pattern=checksum)\n")
        asked_transcript.append(
            SegmentKind.TOOL_RESULT,
            "Grep -> " + "".join(f"tle/parse.py:{i}: checksum ignored\n" for i in range(60)),
        )
        asked_transcript.append(
            SegmentKind.OUTPUT,
            "The checksum is computed but never compared, in sixty places.\n"
            "Do you want me to fix the parser, or only report it?\n",
        )
        self.bridge.emit(StateChanged(asked, AgentState.AWAITING_INPUT, topic=AWAITING_TOPIC))

        self.bridge.emit(
            AgentFinished(
                self.nodes[4],
                AgentState.FAILED,
                # monotonic, to match AgentRecord.started_at -- see model.AgentRecord.
                ended_at=time.monotonic(),
                error="tool call rejected",
            )
        )

        # One team, so DETAIL's board section is reachable at all. Every other root
        # here is solo and correctly renders no board, which is exactly why the
        # section could not be judged from this fixture before.
        self._seed_board(self._spawn(parent=None, agent_type=None, template="feature"))

    def _seed_board(self, root: NodeId) -> None:
        """
        One team session with a board on it, so the BOARD pane is reachable.

        Without this the whole fixture is solo sessions, and the board renders as
        absent everywhere -- which is correct behaviour and proves nothing about
        the drawing. Every state the section has is represented here: claimed and
        being worked, blocked, blocked on an id that was never declared, completed
        by a worker that has since stopped, still claimed by a worker that stopped
        holding it, and a concern in each of its three states including one the
        operator rewrote.

        The last two are the pair worth keeping distinct. Only one of them is a
        warning, and a fixture carrying just the stranded row cannot show that the
        ordinary success path is being flagged as one.

        Rows also carry a specification, a file list and -- on one row -- a concern
        naming the task, because those three are what the row gained when the board
        stopped being a section in DETAIL. A fixture without them exercises the pane
        at its narrowest and says nothing about the disclosure it exists for.
        """
        builder = self._spawn(parent=root, agent_type="builder")
        reviewer = self._spawn(parent=root, agent_type="reviewer")
        scout = self._spawn(parent=root, agent_type="explore")

        for task in (
            Task(
                id="t1",
                title="parse the element set",
                detail=(
                    "Read the TLE two-line format straight from the bytes. Keep the "
                    "column offsets in one table -- the format is fixed-width and "
                    "every field is positional, so a split on whitespace is wrong on "
                    "the lines where a value runs into its neighbour."
                ),
                touches=("tle/parse.py",),
                declared_at=1.0,
            ),
            Task(
                id="t2",
                title="validate the checksum",
                detail="Modulo-10 over the line, minus signs counting as one.",
                depends_on=("t1",),
                touches=("tle/parse.py", "tle/checksum.py"),
                declared_at=2.0,
            ),
            Task(
                id="t3",
                title="cover the parser in tests",
                touches=("tests/test_parse.py",),
                depends_on=("t2",),
                declared_at=3.0,
            ),
            # A dependency nobody declared. Unclaimable forever, and this pane is
            # the only place that is visible.
            Task(id="t4", title="update the changelog", depends_on=("t9",), declared_at=4.0),
            # Unblocked on purpose. A claim is refused while its dependencies are
            # unfinished, so a task behind t2 could not reach the "owner stopped"
            # state this row exists to show.
            Task(id="t5", title="profile the hot loop", declared_at=5.0),
        ):
            self.bridge.emit(TaskDeclared(task, node_id=root))

        # Done properly and by an agent that has since stopped, which is what a
        # worker that did its job looks like. Not a warning, and the fixture needs
        # it precisely so that a regression putting the warning here is visible.
        self.bridge.emit(TaskClaimRequested(scout, request_id="fake-k1", task_id="t1"))
        self.bridge.emit(TaskCompleted(scout, "t1", at=time.monotonic()))
        self.bridge.emit(
            AgentFinished(scout, AgentState.DONE, ended_at=time.monotonic(), error=None)
        )
        # Claimed and still being worked.
        self.bridge.emit(TaskClaimRequested(builder, request_id="fake-k2", task_id="t2"))
        # Claimed by an agent that then stopped, which is the stranded row: the task
        # stays CLAIMED, nothing releases it, and nobody is working it.
        self.bridge.emit(TaskClaimRequested(reviewer, request_id="fake-k3", task_id="t5"))
        self.bridge.emit(
            AgentFinished(reviewer, AgentState.DONE, ended_at=time.monotonic(), error=None)
        )

        # fake-c2 names t2, which is the row that is claimed and being worked: a
        # live, correctly reasoning owner that is deliberately waiting. `owner_gone`
        # is false there, so without the link that row is pixel-identical to
        # ordinary progress -- the exact case the task_id field exists for.
        for cid, sender, subject, body, about in (
            ("fake-c1", reviewer, "the retry loop never terminates", "third call onward", None),
            ("fake-c2", builder, "checksum ignored in sixty places", "tle/parse.py", "t2"),
            (
                "fake-c3",
                reviewer,
                "two roles answer to one name",
                "only the first is reachable",
                None,
            ),
        ):
            self.bridge.emit(
                ConcernPosted(
                    sender,
                    Concern(
                        id=cid,
                        sender=sender,
                        recipient=root,
                        subject=subject,
                        body=body,
                        posted_at=time.monotonic(),
                        # The operator's rewrite, which is now a stamped fact
                        # rather than an unsettable field.
                        edited=cid == "fake-c2",
                        task_id=about,
                    ),
                )
            )
        # One read, so the log shows delivered and waiting side by side rather than
        # three rows in the same state.
        self.bridge.emit(InboxRead(node_id=root, request_id="fake-r1", at=time.monotonic()))

    def _spawn(
        self, parent: NodeId | None, agent_type: str | None, template: str = "solo"
    ) -> NodeId:
        n = next(_ids)
        node: NodeId = (f"sess-{n}", None) if parent is None else (parent[0], f"agent-{n}")
        # The transcript travels with the spawn, exactly as the real driver does it:
        # both sides must hold the same object or the pane reads an empty buffer.
        transcript = Transcript()
        self.transcripts[node] = transcript
        task = self.rng.choice(_TASKS)
        if parent is None:
            # A root's opening task goes through AgentSession.send, which writes a
            # SYSTEM segment -- and that segment is the only turn boundary the
            # transcript has (Transcript.turn_prose). A fixture that skipped it
            # would leave every fake session looking like one endless turn, so the
            # boundary would be exercised by nothing. Sub-agents are spawned by a
            # tool call rather than by send and correctly get no marker.
            transcript.append(SegmentKind.SYSTEM, f"\n> {task}\n")
        transcript.append(SegmentKind.REASONING, "Working out where to start.\n")
        transcript.append(SegmentKind.OUTPUT, "Right, here is the plan.\n")
        transcript.append(SegmentKind.TOOL_CALL, "Read(file_path=pptmstr/store.py)\n")
        transcript.append(SegmentKind.TOOL_RESULT, "268 lines\n")
        self.bridge.emit(
            AgentSpawned(
                node_id=node,
                parent=parent,
                task=task,
                model=self.rng.choice(_MODELS),
                started_at=time.monotonic(),
                agent_type=agent_type,
                topic="starting",
                # Roots only. A sub-agent inherits its session's directory in the
                # store, which is the same rule the real driver relies on.
                cwd=self.rng.choice(_CWDS) if parent is None else None,
                # Roots only, exactly as the real driver announces it: a sub-agent
                # has no template of its own and must not read as a team.
                template=template if parent is None else None,
                transcript=transcript,
            )
        )
        self.nodes.append(node)
        return node

    def _pending(self, node: NodeId, prefer: str | None = None) -> PendingApproval:
        name, args = next((t for t in _TOOLS if t[0] == prefer), self.rng.choice(_TOOLS))
        from .approval import render_diff, summarize

        return PendingApproval(
            id=f"pending-{next(_ids)}",
            node=node,
            tool_name=name,
            tool_use_id=f"tu-{next(_ids)}",
            raw_args=args,
            summary=summarize(name, args),
            requested_at=time.time(),
            diff=render_diff(name, args),
        )

    def _tick(self) -> None:
        """Move one random agent along. Deliberately shallow: this is a fixture."""
        if not self.nodes:
            return
        node = self.rng.choice(self.nodes)
        # Stream a few characters at a time, the way real output arrives -- that is
        # what exercises the pane's incremental line cache rather than a bulk write.
        transcript = self.transcripts.get(node)
        if transcript is not None:
            kind = self.rng.choice(
                (SegmentKind.REASONING, SegmentKind.OUTPUT, SegmentKind.TOOL_RESULT)
            )
            transcript.append(kind, self.rng.choice(_FRAGMENTS))
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
