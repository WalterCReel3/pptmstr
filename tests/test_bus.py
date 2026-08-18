"""
The message bus and the task board: the two cross-agent projections (§2.7).

Tested through the store rather than around it, because the properties that matter
are properties of the reducer -- that a claim always answers, that a cycle never
gets on the board, that a second inbox read hands back nothing. None of them needs
a thread, and testing them with one would be testing the wrong thing.
"""

from __future__ import annotations

import contextlib
import dataclasses
import time
from collections.abc import Iterator

import mcp.types as mcp_types

from pptmstr.bridge import Bridge
from pptmstr.bus import FROM_KEY, build_server
from pptmstr.effects import ClaimSettled, InboxDelivered, TaskWriteSettled
from pptmstr.intents import (
    ConcernEdited,
    ConcernPosted,
    ConcernWithdrawn,
    InboxRead,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
)
from pptmstr.model import Concern, ConcernState, NodeId, Task, TaskRefusal, TaskState
from pptmstr.store import Store

LEAD: NodeId = ("sess-1", None)
QA: NodeId = ("sess-1", "agent-qa")
DEV: NodeId = ("sess-1", "agent-dev")


def concern(
    cid: str,
    *,
    sender: NodeId = QA,
    recipient: NodeId = DEV,
    body: str = "the retry loop never terminates",
    at: float = 1.0,
) -> Concern:
    return Concern(
        id=cid,
        sender=sender,
        recipient=recipient,
        subject="regression",
        body=body,
        posted_at=at,
    )


def task(tid: str, *, deps: tuple[str, ...] = (), at: float = 0.0) -> Task:
    return Task(id=tid, title=f"do {tid}", depends_on=deps, declared_at=at)


# -- the real server, driven the way the application drives it ---------------------


class _Session:
    """As much of ``AgentSession`` as ``build_server`` closes over."""

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge


class _Bus:
    """
    A live MCP server plus the frame loop that answers it.

    Here because five of the six tools now park on a future, and a test that only
    reads the intent they emitted cannot see what the agent was *told* -- which is
    the entire defect this class exists to cover. ``call`` therefore does what
    ``app.begin_frame`` does, in the same order: drain, apply, settle. Anything the
    real loop would fail to answer hangs here too, and is reported as a timeout
    rather than as a passing test.
    """

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge
        self.handler = build_server(_Session(bridge))["instance"].request_handlers[  # type: ignore[arg-type]
            mcp_types.CallToolRequest
        ]

    def call(
        self,
        store: Store,
        name: str,
        args: dict[str, object],
        *,
        sender: NodeId = DEV,
        timeout: float = 5.0,
    ) -> str:
        """The text the calling agent reads. Fails if the tool reports an error."""
        pending = self.bridge.submit(
            self.handler(
                mcp_types.CallToolRequest(
                    method="tools/call",
                    # The gate's stamp, as the CLI delivers it: a list, because the
                    # arguments survive a JSON round trip and JSON has no tuples.
                    params=mcp_types.CallToolRequestParams(
                        name=name, arguments={**args, FROM_KEY: list(sender)}
                    ),
                )
            )
        )
        deadline = time.monotonic() + timeout
        while not pending.done():
            self.pump(store)
            if time.monotonic() > deadline:
                raise AssertionError(f"{name} was never answered")
            time.sleep(0.002)
        answer = pending.result(timeout=timeout)
        assert not answer.root.isError, answer.root.content
        return str(answer.root.content[0].text)

    def pump(self, store: Store) -> None:
        """One frame's worth of the loop in ``app.begin_frame``."""
        for effect in store.apply_all(self.bridge.drain(), now=time.monotonic()):
            self.bridge.settle(effect)


@contextlib.contextmanager
def _live_bus() -> Iterator[_Bus]:
    bridge = Bridge()
    bridge.start()
    try:
        yield _Bus(bridge)
    finally:
        bridge.stop()


# -- concerns ---------------------------------------------------------------------


def test_a_posted_concern_waits_in_the_recipients_inbox() -> None:
    store = Store()
    store.apply(ConcernPosted(QA, concern("c1")))

    snap = store.snapshot()
    assert [c.id for c in snap.inbox_of(DEV)] == ["c1"]
    # Addressed to DEV, so it is not QA's problem even though QA sent it.
    assert snap.inbox_of(QA) == ()


def test_an_inbox_is_ordered_oldest_first() -> None:
    store = Store()
    store.apply(ConcernPosted(QA, concern("later", at=9.0)))
    store.apply(ConcernPosted(QA, concern("earlier", at=2.0)))

    assert [c.id for c in store.snapshot().inbox_of(DEV)] == ["earlier", "later"]


def test_reading_an_inbox_delivers_it_and_answers_the_reader() -> None:
    store = Store()
    store.apply(ConcernPosted(QA, concern("c1")))

    effects = store.apply(InboxRead(DEV, request_id="r1", at=5.0))

    assert effects == (
        InboxDelivered(request_id="r1", concerns=(store.snapshot().concerns["c1"],)),
    )
    delivered = store.snapshot().concerns["c1"]
    assert delivered.state is ConcernState.DELIVERED
    assert delivered.delivered_at == 5.0
    # Delivered means gone from the inbox, or the next read hands it over twice.
    assert store.snapshot().inbox_of(DEV) == ()


def test_a_second_read_hands_back_nothing_and_still_answers() -> None:
    store = Store()
    store.apply(ConcernPosted(QA, concern("c1")))
    store.apply(InboxRead(DEV, request_id="r1", at=5.0))

    effects = store.apply(InboxRead(DEV, request_id="r2", at=6.0))

    # An empty answer, not an absent one. The distinction is the whole point of
    # the effect channel: an unanswered read leaves the agent parked forever.
    assert effects == (InboxDelivered(request_id="r2", concerns=()),)


def test_an_edit_in_flight_is_what_gets_delivered() -> None:
    store = Store()
    store.apply(ConcernPosted(QA, concern("c1", body="rewrite the whole module")))
    store.apply(ConcernEdited("c1", body="narrow this to the retry loop"))

    (delivered,) = store.apply(InboxRead(DEV, request_id="r1", at=5.0))

    assert isinstance(delivered, InboxDelivered)
    (received,) = delivered.concerns
    # The effect carries the edited text, not the posted text. If these ever
    # disagree, the store and the recipient's transcript disagree about what was
    # said, which is worse than not having the feature.
    assert received.body == "narrow this to the retry loop"
    assert received.edited is True


def test_a_withdrawn_concern_is_never_delivered() -> None:
    store = Store()
    store.apply(ConcernPosted(QA, concern("c1")))
    store.apply(ConcernWithdrawn("c1", reason="already fixed"))

    (delivered,) = store.apply(InboxRead(DEV, request_id="r1", at=5.0))

    assert isinstance(delivered, InboxDelivered)
    assert delivered.concerns == ()
    assert store.snapshot().concerns["c1"].state is ConcernState.WITHDRAWN


def test_a_delivered_concern_can_no_longer_be_edited_or_withdrawn() -> None:
    store = Store()
    store.apply(ConcernPosted(QA, concern("c1", body="original")))
    store.apply(InboxRead(DEV, request_id="r1", at=5.0))

    store.apply(ConcernEdited("c1", body="too late"))
    store.apply(ConcernWithdrawn("c1"))

    after = store.snapshot().concerns["c1"]
    assert after.body == "original"
    assert after.state is ConcernState.DELIVERED


def test_a_concern_to_an_unspawned_node_is_kept_not_dropped() -> None:
    store = Store()
    ghost: NodeId = ("sess-1", "agent-not-yet")
    store.apply(ConcernPosted(QA, concern("c1", recipient=ghost)))

    # The sender believes it sent this. Losing it would leave the operator with no
    # view of a message that exists in a transcript.
    assert [c.id for c in store.snapshot().inbox_of(ghost)] == ["c1"]


# -- the task board ---------------------------------------------------------------


def test_a_task_with_no_dependencies_is_claimable() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))

    assert [t.id for t in store.snapshot().claimable_tasks()] == ["t1"]


def test_a_dependency_blocks_until_it_completes() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1", at=0.0)))
    store.apply(TaskDeclared(task("t2", deps=("t1",), at=1.0)))

    assert [t.id for t in store.snapshot().claimable_tasks()] == ["t1"]

    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV, "t1", at=4.0))

    # No unblocking step ran. t2 is claimable because the graph says so.
    assert [t.id for t in store.snapshot().claimable_tasks()] == ["t2"]


def test_a_dependency_on_a_nonexistent_task_blocks_rather_than_vanishes() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t2", deps=("typo",))))

    assert store.snapshot().claimable_tasks() == ()


def test_a_cycle_never_reaches_the_board() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1", deps=("t2",))))
    store.apply(TaskDeclared(task("t2", deps=("t1",))))

    # t1 is admitted (t2 does not exist yet, so no cycle closes); t2 would close
    # one and is refused. Every member of a cycle is unclaimable forever, and the
    # symptom is workers idling while the board shows outstanding work.
    assert set(store.snapshot().tasks) == {"t1"}


def test_a_longer_cycle_is_caught_too() -> None:
    store = Store()
    store.apply(TaskDeclared(task("a", deps=("b",))))
    store.apply(TaskDeclared(task("b", deps=("c",))))
    store.apply(TaskDeclared(task("c", deps=("a",))))

    assert set(store.snapshot().tasks) == {"a", "b"}


def test_a_task_cannot_depend_on_itself() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1", deps=("t1",))))

    assert store.snapshot().tasks == {}


def test_redeclaring_an_id_does_not_disturb_the_claim_on_it() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))

    store.apply(TaskDeclared(Task(id="t1", title="something else")))

    held = store.snapshot().tasks["t1"]
    assert held.state is TaskState.CLAIMED
    assert held.claimed_by == DEV
    assert held.title == "do t1"


# -- task provenance --------------------------------------------------------------
#
# There is one Store for the whole fleet, so `tasks` is a global map. An unclaimed
# task has no node on it at all, so without the declarer nothing can say which
# session's board it belongs to.


def test_a_declared_task_remembers_who_declared_it() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1"), node_id=LEAD))

    assert store.snapshot().tasks["t1"].declared_by == LEAD


def test_a_declarer_is_not_lost_when_the_task_is_admitted() -> None:
    """
    The reducer rebuilds the record on admission. The provenance has to survive
    that rebuild, not just be present on the intent.
    """
    store = Store()
    store.apply(TaskDeclared(task("t1", at=0.0), node_id=LEAD))
    store.apply(TaskDeclared(task("t2", deps=("t1",), at=1.0), node_id=QA))

    tasks = store.snapshot().tasks
    assert (tasks["t1"].declared_by, tasks["t2"].declared_by) == (LEAD, QA)
    # The rebuild must not have disturbed anything else about the record.
    assert tasks["t2"].depends_on == ("t1",)
    assert tasks["t2"].state is TaskState.PENDING


def test_provenance_comes_from_the_intent_not_from_the_record() -> None:
    """
    The declarer the gate authenticated is the only one that counts, so a
    ``declared_by`` set on the incoming record is overwritten either way. One
    writer for the field: a fallback to the record would be a second, and a
    caller could then choose its own provenance.
    """
    store = Store()
    store.apply(TaskDeclared(dataclasses.replace(task("t1"), declared_by=QA), node_id=LEAD))
    store.apply(TaskDeclared(dataclasses.replace(task("t2"), declared_by=QA), node_id=None))

    tasks = store.snapshot().tasks
    assert (tasks["t1"].declared_by, tasks["t2"].declared_by) == (LEAD, None)


def test_the_declare_handler_stamps_the_task_with_its_caller() -> None:
    """
    The wiring, not the reducer (STYLE.md §2). The handler discarded ``_sender``
    entirely, so every reducer-level test above passes with the bus still emitting
    an unattributed ``TaskDeclared``. This drives the real MCP server.
    """
    store = Store()
    with _live_bus() as bus:
        text = bus.call(store, "declare_task", {"task_id": "t1", "title": "do t1"}, sender=QA)

    assert store.snapshot().tasks["t1"].declared_by == QA
    assert "t1 is on the board" in text


# -- claiming ---------------------------------------------------------------------


def test_a_claim_is_answered_with_the_task_it_won() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))

    effects = store.apply(TaskClaimRequested(DEV, request_id="k1"))

    assert effects == (ClaimSettled(request_id="k1", task=store.snapshot().tasks["t1"]),)
    assert store.snapshot().tasks["t1"].claimed_by == DEV


def test_an_empty_board_still_answers_the_claim() -> None:
    store = Store()

    effects = store.apply(TaskClaimRequested(DEV, request_id="k1"))

    # The failure this guards is an agent parked forever on a future because the
    # board happened to be empty when it asked.
    assert effects == (ClaimSettled(request_id="k1", task=None),)


def test_a_blocked_board_answers_the_claim_too() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t2", deps=("missing",))))

    assert store.apply(TaskClaimRequested(DEV, request_id="k1")) == (
        ClaimSettled(request_id="k1", task=None),
    )


def test_two_workers_racing_for_one_task_settle_differently() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))

    effects = store.apply_all(
        [
            TaskClaimRequested(DEV, request_id="k1"),
            TaskClaimRequested(QA, request_id="k2"),
        ]
    )

    # Serialization is the whole mechanism: the second claim reads the first's
    # result because they are two intents in one queue, not two threads in a lock.
    first, second = effects
    assert isinstance(first, ClaimSettled) and first.task is not None
    assert first.task.id == "t1"
    assert second == ClaimSettled(request_id="k2", task=None)
    assert store.snapshot().tasks["t1"].claimed_by == DEV


def test_self_claiming_takes_the_oldest_first() -> None:
    store = Store()
    store.apply(TaskDeclared(task("newer", at=9.0)))
    store.apply(TaskDeclared(task("older", at=1.0)))

    (settled,) = store.apply(TaskClaimRequested(DEV, request_id="k1"))

    assert isinstance(settled, ClaimSettled) and settled.task is not None
    assert settled.task.id == "older"


def test_claiming_a_named_task_that_is_blocked_wins_nothing() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1", at=0.0)))
    store.apply(TaskDeclared(task("t2", deps=("t1",), at=1.0)))

    # Not "falls back to t1". A worker that asked for t2 is told no.
    assert store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t2")) == (
        ClaimSettled(request_id="k1", task=None),
    )


def test_a_released_task_returns_to_the_pool() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))
    store.apply(TaskClaimRequested(DEV, request_id="k1"))

    store.apply(TaskReleased(DEV, "t1"))

    released = store.snapshot().tasks["t1"]
    assert released.state is TaskState.PENDING
    assert released.claimed_by is None

    (settled,) = store.apply(TaskClaimRequested(QA, request_id="k2"))
    assert isinstance(settled, ClaimSettled) and settled.task is not None
    assert settled.task.id == "t1"


def test_only_the_claimer_can_complete_or_release() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))
    store.apply(TaskClaimRequested(DEV, request_id="k1"))

    store.apply(TaskCompleted(QA, "t1", at=3.0))
    store.apply(TaskReleased(QA, "t1"))

    still = store.snapshot().tasks["t1"]
    assert still.state is TaskState.CLAIMED
    assert still.claimed_by == DEV


def test_completing_an_unclaimed_task_does_nothing() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))

    store.apply(TaskCompleted(DEV, "t1", at=3.0))

    assert store.snapshot().tasks["t1"].state is TaskState.PENDING


# -- a write the board refuses ----------------------------------------------------
#
# The three tools that assert facts are guarded, and every guard used to drop the
# write in silence while its handler returned the success text unconditionally. The
# reducer's behaviour is unchanged by this; what is new is that the refusal is a
# value somebody has to look at.


def _refusal(effects: tuple[object, ...]) -> TaskRefusal | None:
    (settled,) = effects
    assert isinstance(settled, TaskWriteSettled)
    return settled.refusal


def test_a_write_the_board_accepted_answers_with_no_refusal() -> None:
    store = Store()

    declared = store.apply(TaskDeclared(task("t1"), node_id=DEV, request_id="d1"))
    store.apply(TaskClaimRequested(DEV, request_id="k1"))
    released = store.apply(TaskReleased(DEV, "t1", request_id="x1"))
    store.apply(TaskClaimRequested(DEV, request_id="k2"))
    completed = store.apply(TaskCompleted(DEV, "t1", at=3.0, request_id="f1"))

    assert (_refusal(declared), _refusal(released), _refusal(completed)) == (None, None, None)


def test_a_declaration_the_board_drops_says_which_way_it_dropped() -> None:
    """
    Both silent drops, named apart. A duplicate id is a naming collision the
    declarer fixes by renaming; a cycle is a dependency graph it has to redraw. A
    single "declaration refused" would send it back to guess which.
    """
    store = Store()
    store.apply(TaskDeclared(task("t1")))
    store.apply(TaskDeclared(task("t2", deps=("t1",))))

    duplicate = store.apply(TaskDeclared(task("t1"), request_id="d1"))
    cycle = store.apply(TaskDeclared(task("t3", deps=("t3",)), request_id="d2"))

    assert _refusal(duplicate) is TaskRefusal.DUPLICATE_ID
    assert _refusal(cycle) is TaskRefusal.WOULD_CYCLE
    # And the refusal is not a rollback of something half-done.
    assert "t3" not in store.snapshot().tasks
    assert store.snapshot().tasks["t1"].title == "do t1"


def test_a_write_to_a_task_you_do_not_hold_says_which_mistake_it_was() -> None:
    """
    Four conditions, four answers, because the recoveries are four different
    things: declare it, claim it, stop, or go and talk to whoever holds it.
    """
    store = Store()
    store.apply(TaskDeclared(task("t1")))

    missing = store.apply(TaskCompleted(DEV, "nope", at=1.0, request_id="f1"))
    unclaimed = store.apply(TaskCompleted(DEV, "t1", at=1.0, request_id="f2"))

    store.apply(TaskClaimRequested(DEV, request_id="k1"))
    someone_elses = store.apply(TaskCompleted(QA, "t1", at=2.0, request_id="f3"))

    store.apply(TaskCompleted(DEV, "t1", at=3.0))
    twice = store.apply(TaskCompleted(DEV, "t1", at=4.0, request_id="f4"))

    assert _refusal(missing) is TaskRefusal.NO_SUCH_TASK
    assert _refusal(unclaimed) is TaskRefusal.NOT_CLAIMED
    assert _refusal(someone_elses) is TaskRefusal.NOT_YOURS
    assert _refusal(twice) is TaskRefusal.ALREADY_COMPLETE
    # The completion that was refused as ALREADY_COMPLETE did not move the clock.
    assert store.snapshot().tasks["t1"].completed_at == 3.0


def test_a_release_is_refused_on_the_same_terms_as_a_completion() -> None:
    """
    The two arms share one guard, so they cannot drift into disagreeing about who
    owns a task -- which would be a worker told it may finish work it may not give
    back, or the reverse.
    """
    store = Store()
    store.apply(TaskDeclared(task("t1")))

    missing = store.apply(TaskReleased(DEV, "nope", request_id="x1"))
    unclaimed = store.apply(TaskReleased(DEV, "t1", request_id="x2"))

    store.apply(TaskClaimRequested(DEV, request_id="k1"))
    someone_elses = store.apply(TaskReleased(QA, "t1", request_id="x3"))

    store.apply(TaskCompleted(DEV, "t1", at=3.0))
    finished = store.apply(TaskReleased(DEV, "t1", request_id="x4"))

    assert _refusal(missing) is TaskRefusal.NO_SUCH_TASK
    assert _refusal(unclaimed) is TaskRefusal.NOT_CLAIMED
    assert _refusal(someone_elses) is TaskRefusal.NOT_YOURS
    assert _refusal(finished) is TaskRefusal.ALREADY_COMPLETE
    assert store.snapshot().tasks["t1"].state is TaskState.COMPLETED


def test_a_write_nobody_is_waiting_on_answers_nobody() -> None:
    """
    ``request_id`` None is a real case, not a test convenience: the fake driver's
    scripted board and any operator-side write have no agent parked on a future.
    An effect for one would be settled against nothing every frame.
    """
    store = Store()

    assert store.apply(TaskDeclared(task("t1"))) == ()
    assert store.apply(TaskDeclared(task("t1"))) == ()  # refused, and still silent
    assert store.apply(TaskCompleted(QA, "t1", at=1.0)) == ()
    assert store.apply(TaskReleased(QA, "t1")) == ()


# -- the effect channel itself ----------------------------------------------------


def test_effects_come_back_in_application_order() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))
    store.apply(ConcernPosted(QA, concern("c1")))

    effects = store.apply_all(
        [
            TaskClaimRequested(DEV, request_id="k1"),
            InboxRead(DEV, request_id="r1", at=5.0),
            TaskClaimRequested(QA, request_id="k2"),
        ]
    )

    assert [type(e).__name__ for e in effects] == [
        "ClaimSettled",
        "InboxDelivered",
        "ClaimSettled",
    ]


def test_intents_that_answer_nobody_produce_no_effects() -> None:
    store = Store()

    assert store.apply(TaskDeclared(task("t1"))) == ()
    assert store.apply(ConcernPosted(QA, concern("c1"))) == ()
    assert store.apply(ConcernWithdrawn("c1")) == ()


def test_bus_state_does_not_disturb_the_agent_projections() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))
    store.apply(ConcernPosted(QA, concern("c1")))
    store.apply(TaskClaimRequested(DEV, request_id="k1"))

    snap = store.snapshot()
    # A busy board is not an obligation and not activity. Both would be wrong in
    # the same direction: the app would never idle (§4.2) and the inbox would
    # count work nobody is waiting on.
    assert snap.needs_you == ()
    assert snap.any_active is False


def test_an_old_snapshot_still_describes_the_old_board() -> None:
    store = Store()
    store.apply(TaskDeclared(task("t1")))
    before = store.snapshot()

    store.apply(TaskClaimRequested(DEV, request_id="k1"))

    # I3: records are immutable and the store swaps references. A frame that took
    # this snapshot renders a consistent world even as the board moves under it.
    assert before.tasks["t1"].state is TaskState.PENDING
    assert store.snapshot().tasks["t1"].state is TaskState.CLAIMED


# -- the wake path (§2.3) ---------------------------------------------------------
#
# Measured in scripts/verify_wake_path.py: a sibling's SendMessage restarts a
# finished sub-agent, and the CLI reports it through a *second* SubagentStart
# carrying the original agent_id. These pin what the record must survive.


def resumable_store() -> tuple[Store, NodeId]:
    from pptmstr.intents import AgentFinished, AgentSpawned, UsageAccrued
    from pptmstr.model import AgentState, UsageRollup
    from pptmstr.transcript import SegmentKind, Transcript

    store = Store()
    store.apply(
        AgentSpawned(node_id=LEAD, parent=None, task="lead", model="m", started_at=0.0),
        now=0.0,
    )
    buf = Transcript()
    store.apply(
        AgentSpawned(
            node_id=QA,
            parent=LEAD,
            task="alpha",
            model="m",
            started_at=1.0,
            agent_type="alpha",
            transcript=buf,
        ),
        now=1.0,
    )
    buf.append(SegmentKind.OUTPUT, "alpha ready.")
    store.apply(
        UsageAccrued(QA, UsageRollup(input_tokens=900, output_tokens=120, total_cost_usd=0.03))
    )
    store.apply(AgentFinished(QA, AgentState.DONE, ended_at=9.6), now=9.6)
    return store, QA


def test_a_resumed_agent_keeps_the_transcript_the_ui_is_reading() -> None:
    from pptmstr.intents import AgentResumed

    store, node = resumable_store()
    before = store.snapshot().nodes[node].transcript

    store.apply(AgentResumed(node, at=16.59), now=16.59)

    after = store.snapshot().nodes[node].transcript
    # Object identity, not equal contents. Readers hold (buffer, length_at_snapshot)
    # under I7, so a replacement buffer leaves every pane following this node
    # reading something nothing writes to.
    assert after is before
    assert "alpha ready." in after.text()


def test_a_resumed_agent_keeps_what_it_already_spent() -> None:
    from pptmstr.intents import AgentResumed

    store, node = resumable_store()
    store.apply(AgentResumed(node, at=16.59), now=16.59)

    rec = store.snapshot().nodes[node]
    assert rec.usage.output_tokens == 120
    assert rec.usage.total_cost_usd == 0.03
    # Cost is cumulative and monotonic (§2.4). A resume that zeroed it would make
    # the money axis lie in the one direction that matters.
    assert rec.started_at == 1.0


def test_a_resumed_agent_is_live_again() -> None:
    from pptmstr.intents import AgentResumed
    from pptmstr.model import AgentState

    store, node = resumable_store()
    store.apply(AgentResumed(node, at=16.59), now=16.59)

    rec = store.snapshot().nodes[node]
    assert rec.state is AgentState.THINKING
    assert rec.ended_at is None
    # The idle predicate has to notice, or the app stays at idle FPS while a woken
    # agent works (§4.2).
    assert store.snapshot().any_active is True


def test_a_resumed_agent_carries_no_stale_failure() -> None:
    from pptmstr.intents import AgentFinished, AgentResumed
    from pptmstr.model import AgentState

    store, node = resumable_store()
    store.apply(AgentFinished(node, AgentState.FAILED, ended_at=9.6, error="boom"), now=9.6)
    assert len(store.snapshot().needs_you) == 1

    store.apply(AgentResumed(node, at=16.59), now=16.59)

    rec = store.snapshot().nodes[node]
    assert rec.error is None
    # A running node listed as an unacknowledged failure would sit in the inbox
    # forever, since nothing will finish it a second time under that error.
    assert store.snapshot().needs_you == ()


def test_a_resume_for_an_unknown_node_is_dropped() -> None:
    from pptmstr.intents import AgentResumed

    store = Store()
    store.apply(AgentResumed(("gone", "agent-x"), at=1.0), now=1.0)

    # Dropped rather than fabricated: a record built from a resume would have no
    # transcript, no history and an invented parent.
    assert store.snapshot().nodes == {}


def test_the_tree_does_not_reshuffle_when_a_node_wakes() -> None:
    from pptmstr.intents import AgentResumed

    store, node = resumable_store()
    before = store.snapshot().order

    store.apply(AgentResumed(node, at=16.59), now=16.59)

    # Order is the basis for widget keys (I6). A wake is not a shape change.
    assert store.snapshot().order == before


# -- the tool surface -------------------------------------------------------------


def test_the_gate_and_the_bus_agree_on_tool_names() -> None:
    """
    approval.py spells the server name rather than importing it, so that the policy
    stays free of SDK imports. That is a deliberate duplication, and this is what
    keeps the two copies honest.
    """
    from pptmstr import approval
    from pptmstr.bus import BUS_TOOLS, qualified

    assert approval._BUS_POST == qualified("post_concern")
    assert approval._BUS_AUTO | {approval._BUS_POST} == set(BUS_TOOLS)


def test_a_message_between_agents_is_reviewed() -> None:
    from pptmstr.approval import Disposition, classify
    from pptmstr.bus import qualified

    assert classify(qualified("post_concern"), {}) is Disposition.REQUIRE_APPROVAL


def test_coordination_is_not_reviewed() -> None:
    from pptmstr.approval import Disposition, classify
    from pptmstr.bus import qualified

    # Bookkeeping, not decisions. Parking these would make the operator a
    # bottleneck on a worker taking the next item off a board they already approved.
    for name in ("read_inbox", "claim_task", "declare_task", "complete_task", "release_task"):
        assert classify(qualified(name), {}) is Disposition.AUTO_APPROVE, name


def test_a_parked_concern_reads_as_a_message_not_an_argument_dict() -> None:
    from pptmstr.approval import summarize
    from pptmstr.bus import qualified

    row = summarize(
        qualified("post_concern"),
        {"to": "dev", "subject": "retry loop never terminates", "body": "..."},
    )
    assert row == "message dev: retry loop never terminates"


def test_an_unstamped_bus_call_is_refused_rather_than_guessed() -> None:
    import pytest

    from pptmstr.bus import UnstampedCall, _sender

    # A call that reaches a handler unstamped bypassed the gate. Defaulting would
    # attribute someone's message to a guess.
    with pytest.raises(UnstampedCall):
        _sender({"to": "dev", "subject": "s", "body": "b"})


def test_the_stamp_survives_a_json_round_trip() -> None:
    import json

    from pptmstr.bus import FROM_KEY, _sender

    # The stamp goes out to the CLI as JSON and comes back, so the NodeId tuple
    # arrives as a list. Reconstituting it is not optional.
    wire = json.loads(json.dumps({FROM_KEY: [("s1"), "agent-qa"]}))
    assert _sender(wire) == ("s1", "agent-qa")

    root = json.loads(json.dumps({FROM_KEY: ["s1", None]}))
    assert _sender(root) == ("s1", None)


# -- the schema the model is actually shown ---------------------------------------
#
# Nothing else in the suite looks at a bus tool's schema. The handlers are driven
# with complete argument dicts, which bypasses validation entirely -- so a tool
# could announce a contract its own description contradicts and every test here
# would still pass. That is how ``claim_task()`` came to answer
# "'task_id' is a required property" to the call its description asks for.
#
# jsonschema below is the validator a real call meets, not a stand-in for one:
# ``mcp.server.lowlevel.Server.call_tool`` validates arguments against the tool's
# announced ``inputSchema`` before dispatching to the handler. It is not a direct
# dependency and does not need to be -- mcp requires it, claude-agent-sdk requires
# mcp, and pptmstr requires the SDK.


def _announced_schemas() -> dict[str, dict[str, object]]:
    """
    The ``inputSchema`` for each tool as the CLI is handed it, from the real server.
    """
    import asyncio

    import mcp.types as mcp_types

    from pptmstr.bridge import Bridge
    from pptmstr.bus import build_server

    class _Session:
        def __init__(self, bridge: Bridge) -> None:
            self.bridge = bridge

    server = build_server(_Session(Bridge()))["instance"]  # type: ignore[arg-type]
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    listed = asyncio.run(handler(mcp_types.ListToolsRequest(method="tools/list")))
    return {t.name: t.inputSchema for t in listed.root.tools}


# The smallest call each tool's own description says is enough, with the sentence
# that licenses each omission quoted beside it. Written as calls rather than as
# expected `required` lists on purpose: the description is the contract a model
# reads, and this table is what it reads it as.
DOCUMENTED_CALLS: dict[str, dict[str, object]] = {
    # "Use the agent's role name" -- all three are named, none optional.
    "post_concern": {"to": "lead", "subject": "regression", "body": "the retry loop"},
    "read_inbox": {},
    # "Omit task_id to be given the oldest task whose dependencies are all met."
    "claim_task": {},
    # "Omit it and the board generates one" / "Omit it for a title-only task" /
    # "Omit it for none" -- which leaves the title as the whole of a declaration.
    "declare_task": {"title": "do t1"},
    "complete_task": {"task_id": "t1"},
    "release_task": {"task_id": "t1"},
}


def test_every_tool_accepts_the_call_its_own_description_documents() -> None:
    import jsonschema

    schemas = _announced_schemas()
    assert set(schemas) == set(DOCUMENTED_CALLS), "a tool changed; document its call here"

    for name, args in DOCUMENTED_CALLS.items():
        errors = list(jsonschema.Draft202012Validator(schemas[name]).iter_errors(args))
        assert not errors, f"{name}({args}) is refused: {[e.message for e in errors]}"


def test_an_argument_a_tool_cannot_work_without_is_still_required() -> None:
    """
    The counterweight to the test above, which emptying every ``required`` list
    would also satisfy -- leaving ``complete_task`` callable with nothing to
    complete and the model free to omit the one argument that carries the work.
    """
    import jsonschema

    schemas = _announced_schemas()
    for name, needed in (
        ("post_concern", "to"),
        ("declare_task", "title"),
        ("complete_task", "task_id"),
        ("release_task", "task_id"),
    ):
        args = dict(DOCUMENTED_CALLS[name])
        del args[needed]
        errors = list(jsonschema.Draft202012Validator(schemas[name]).iter_errors(args))
        assert errors, f"{name} accepts a call with no {needed}"


def test_a_claim_with_no_task_id_asks_for_anything_claimable() -> None:
    """
    The documented call all the way through, not only past validation: omitting
    ``task_id`` has to reach the handler's default and become a request for
    *anything*, which is the self-claiming model the board is for. Passing the
    schema and then asking for the task named "" would be the same defect one
    layer in.
    """
    import mcp.types as mcp_types

    from pptmstr.bridge import Bridge
    from pptmstr.bus import FROM_KEY, build_server

    class _Session:
        def __init__(self, bridge: Bridge) -> None:
            self.bridge = bridge

    bridge = Bridge()
    bridge.start()  # claim_task parks on a future, so this one needs the loop
    try:
        server = build_server(_Session(bridge))["instance"]  # type: ignore[arg-type]
        handler = server.request_handlers[mcp_types.CallToolRequest]
        call = handler(
            mcp_types.CallToolRequest(
                method="tools/call",
                params=mcp_types.CallToolRequestParams(
                    name="claim_task",
                    # The stamp and nothing else: exactly what the CLI would deliver
                    # for a `claim_task()` the gate has stamped.
                    arguments={FROM_KEY: list(DEV)},
                ),
            )
        )
        result = bridge.submit(call)

        intents: list[object] = []
        for _ in range(200):
            intents.extend(bridge.drain())
            if intents:
                break
            time.sleep(0.005)

        assert len(intents) == 1
        asked = intents[0]
        assert isinstance(asked, TaskClaimRequested)
        assert asked.task_id is None, "an omitted task_id must mean 'anything', not ''"

        won = task("t1")
        bridge.settle(ClaimSettled(request_id=asked.request_id, task=won))
        answered = result.result(timeout=5)
    finally:
        bridge.stop()

    assert not answered.root.isError, answered.root.content
    assert "t1" in answered.root.content[0].text


def test_a_declaration_the_board_refused_is_not_reported_as_success() -> None:
    """
    The keystone defect, at the layer it lived on. Every reducer test above passed
    while ``declare_task`` returned "Task t1 is on the board." for a declaration the
    store had dropped -- the handler composed the outcome instead of waiting for it,
    so no test of the store could see the lie. The lead then waited on a task that
    was not there.

    Asserted on the text the agent reads, because that is the thing that was wrong.
    """
    store = Store()
    with _live_bus() as bus:
        first = bus.call(store, "declare_task", {"task_id": "t1", "title": "do t1"}, sender=LEAD)
        again = bus.call(store, "declare_task", {"task_id": "t1", "title": "same id"}, sender=LEAD)
        cycle = bus.call(
            store,
            "declare_task",
            {"task_id": "t2", "title": "eats itself", "depends_on": ["t2"]},
            sender=LEAD,
        )

    assert "t1 is on the board" in first
    assert "NOT on the board" in again and "already on the board" in again
    assert "NOT on the board" in cycle and "cycle" in cycle
    # The board agrees with what each caller was told.
    assert set(store.snapshot().tasks) == {"t1"}
    assert store.snapshot().tasks["t1"].title == "do t1"


def test_a_completion_the_board_refused_is_not_reported_as_success() -> None:
    """
    ``complete_task`` told a non-owner *"Anything waiting on it is now claimable."*
    -- a lead could be told a dependency had cleared when nothing had unblocked.
    """
    store = Store()
    with _live_bus() as bus:
        bus.call(store, "declare_task", {"task_id": "t1", "title": "do t1"}, sender=LEAD)
        bus.call(store, "claim_task", {"task_id": "t1"}, sender=DEV)

        intruder = bus.call(store, "complete_task", {"task_id": "t1"}, sender=QA)
        owner = bus.call(store, "complete_task", {"task_id": "t1"}, sender=DEV)
        after = bus.call(store, "complete_task", {"task_id": "t1"}, sender=DEV)

    assert "NOT complete" in intruder and "Another agent holds the claim" in intruder
    assert "now claimable" in owner
    assert "NOT complete" in after and "already complete" in after
    assert store.snapshot().tasks["t1"].state is TaskState.COMPLETED


def test_a_release_the_board_refused_is_not_reported_as_success() -> None:
    store = Store()
    with _live_bus() as bus:
        bus.call(store, "declare_task", {"task_id": "t1", "title": "do t1"}, sender=LEAD)
        unclaimed = bus.call(store, "release_task", {"task_id": "t1"}, sender=DEV)
        bus.call(store, "claim_task", {"task_id": "t1"}, sender=DEV)
        intruder = bus.call(store, "release_task", {"task_id": "t1"}, sender=QA)
        owner = bus.call(store, "release_task", {"task_id": "t1"}, sender=DEV)

    assert "NOT released" in unclaimed and "Claim it first" in unclaimed
    assert "NOT released" in intruder
    assert "back on the board" in owner
    assert store.snapshot().tasks["t1"].state is TaskState.PENDING


def test_a_board_write_the_store_never_saw_is_not_reported_as_success() -> None:
    """
    The one refusal the reducer never produces. A write abandoned at shutdown has
    no ordinary negative to fall back on the way a claim does -- ``claim_task``
    abandons as "nothing was claimable", which is merely unlucky, while a write
    abandoned as "it landed" is the same lie the effect channel is here to remove.
    """
    bridge = Bridge()
    bridge.start()
    try:
        bus = _Bus(bridge)
        pending = bridge.submit(
            bus.handler(
                mcp_types.CallToolRequest(
                    method="tools/call",
                    params=mcp_types.CallToolRequestParams(
                        name="declare_task",
                        arguments={"task_id": "t1", "title": "do t1", FROM_KEY: list(LEAD)},
                    ),
                )
            )
        )
        # Parked, and deliberately never applied: this is the frame loop dying
        # between the emit and the apply.
        for _ in range(400):
            if bridge.asking_count:
                break
            time.sleep(0.005)
        assert bridge.asking_count == 1, "the write did not park on a future at all"

        bridge.abandon_all_requests()
        answered = pending.result(timeout=5).root
    finally:
        bridge.stop()

    assert not answered.isError, answered.content
    text = answered.content[0].text
    assert "NOT on the board" in text and "shutting down" in text


def test_a_stranded_bus_request_is_noticed() -> None:
    """
    A bus request has no surface: an agent awaiting claim_task is not an obligation
    and appears nowhere in needs_you, by design. So a dropped effect looks like
    nothing at all.

    It is not harmless. The store commits the domain change at *apply* time -- the
    board says CLAIMED, the concern says DELIVERED -- while the answer travels
    separately, so a lost effect leaves the board and the agent disagreeing with
    neither side complaining. Found by the research template reviewing this repo.
    """
    from pptmstr.app import _STRANDED_REQUEST_GRACE_S, AppState, _check_for_stranded_requests
    from pptmstr.bridge import Bridge
    from pptmstr.effects import ClaimSettled
    from pptmstr.settings import Settings

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings())

        async def park() -> None:
            await bridge.ask("r1", ClaimSettled(request_id="r1", task=None))

        bridge.submit(park())
        for _ in range(200):
            time.sleep(0.005)
            if bridge.asking_count:
                break

        # First sighting only starts the clock; a request outstanding for part of
        # one frame is the ordinary case, not a fault.
        state.frame_now = 100.0
        _check_for_stranded_requests(state)
        assert state.stranded_reported is False

        state.frame_now = 100.0 + _STRANDED_REQUEST_GRACE_S + 1
        _check_for_stranded_requests(state)
        assert state.stranded_reported is True
    finally:
        bridge.stop()


def test_the_watchdog_resets_when_the_reply_lands() -> None:
    from pptmstr.app import AppState, _check_for_stranded_requests
    from pptmstr.bridge import Bridge
    from pptmstr.settings import Settings

    state = AppState(store=Store(), bridge=Bridge(), settings=Settings())
    state.stranded_since = 1.0
    state.stranded_reported = True
    state.frame_now = 99.0

    # Nothing outstanding: the ordinary state, and it must clear rather than latch.
    _check_for_stranded_requests(state)

    assert state.stranded_since is None
    assert state.stranded_reported is False


def test_the_watchdogs_are_actually_wired_into_the_frame_loop() -> None:
    """
    Source-level, because the alternative is an untested integration point.

    Both watchdogs are pure functions with thorough unit tests, and unwiring either
    from begin_frame leaves every one of those tests passing -- verified by doing
    it. A watchdog nothing calls is worse than none: it reads as covered.
    """
    import inspect

    from pptmstr.app import begin_frame

    body = inspect.getsource(begin_frame)
    assert "_check_for_lost_approvals(state)" in body
    assert "_check_for_stranded_requests(state)" in body
