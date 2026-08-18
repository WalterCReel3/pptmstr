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
from pptmstr.effects import BoardDelivered, ClaimSettled, InboxDelivered, TaskWriteSettled
from pptmstr.intents import (
    BoardRead,
    ConcernEdited,
    ConcernPosted,
    ConcernWithdrawn,
    InboxRead,
    TaskAmended,
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


def task(
    tid: str,
    *,
    deps: tuple[str, ...] = (),
    at: float = 0.0,
    touches: tuple[str, ...] = (),
) -> Task:
    return Task(id=tid, title=f"do {tid}", depends_on=deps, declared_at=at, touches=touches)


def declared(
    tid: str,
    *,
    deps: tuple[str, ...] = (),
    at: float = 0.0,
    by: NodeId | None = LEAD,
    request_id: str | None = None,
    touches: tuple[str, ...] = (),
) -> TaskDeclared:
    """
    A declaration with a declarer, which is the only kind the application makes.

    ``by`` defaults to a node rather than to None because a task belongs to the
    session that declared it (``Task.belongs_to``) and one declared by nobody is on
    no board and claimable by no worker. The bus stamps every declaration with the
    sender the gate authenticated -- an unstamped call raises -- so an unattributed
    task is a state only a test can build, and a suite built on one would be
    exercising a path production cannot reach.
    """
    return TaskDeclared(
        task(tid, deps=deps, at=at, touches=touches), node_id=by, request_id=request_id
    )


# -- the real server, driven the way the application drives it ---------------------


class _Session:
    """
    As much of ``AgentSession`` as ``build_server`` closes over.

    ``resolve_role`` answers for the roles ``team()`` spawns and refuses everything
    else, which is the branch ``post_concern`` reports out of ``role_status``.
    """

    ROLES: dict[str, NodeId] = {"lead": LEAD, "builder": DEV, "reviewer": QA}

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge

    def resolve_role(self, name: str) -> NodeId | None:
        return self.ROLES.get(name)

    def role_status(self, name: str) -> str:
        return f"No agent answers to {name!r}."

    def role_of(self, node: NodeId) -> str | None:
        return next((r for r, n in self.ROLES.items() if n == node), None)


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
    store.apply(declared("t1"))

    assert [t.id for t in store.snapshot().claimable_tasks()] == ["t1"]


def test_a_dependency_blocks_until_it_completes() -> None:
    store = Store()
    store.apply(declared("t1", at=0.0))
    store.apply(declared("t2", deps=("t1",), at=1.0))

    assert [t.id for t in store.snapshot().claimable_tasks()] == ["t1"]

    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV, "t1", at=4.0))

    # No unblocking step ran. t2 is claimable because the graph says so.
    assert [t.id for t in store.snapshot().claimable_tasks()] == ["t2"]


def test_a_dependency_on_a_nonexistent_task_blocks_rather_than_vanishes() -> None:
    store = Store()
    store.apply(declared("t2", deps=("typo",)))

    assert store.snapshot().claimable_tasks() == ()


def test_a_cycle_never_reaches_the_board() -> None:
    store = Store()
    store.apply(declared("t1", deps=("t2",)))
    store.apply(declared("t2", deps=("t1",)))

    # t1 is admitted (t2 does not exist yet, so no cycle closes); t2 would close
    # one and is refused. Every member of a cycle is unclaimable forever, and the
    # symptom is workers idling while the board shows outstanding work.
    assert set(store.snapshot().tasks) == {"t1"}


def test_a_longer_cycle_is_caught_too() -> None:
    store = Store()
    store.apply(declared("a", deps=("b",)))
    store.apply(declared("b", deps=("c",)))
    store.apply(declared("c", deps=("a",)))

    assert set(store.snapshot().tasks) == {"a", "b"}


def test_a_task_cannot_depend_on_itself() -> None:
    store = Store()
    store.apply(declared("t1", deps=("t1",)))

    assert store.snapshot().tasks == {}


def test_redeclaring_an_id_does_not_disturb_the_claim_on_it() -> None:
    store = Store()
    store.apply(declared("t1"))
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
    store.apply(declared("t1", by=LEAD))

    assert store.snapshot().tasks["t1"].declared_by == LEAD


def test_a_declarer_is_not_lost_when_the_task_is_admitted() -> None:
    """
    The reducer rebuilds the record on admission. The provenance has to survive
    that rebuild, not just be present on the intent.
    """
    store = Store()
    store.apply(declared("t1", at=0.0, by=LEAD))
    store.apply(declared("t2", deps=("t1",), at=1.0, by=QA))

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


# -- file overlap becomes a dependency ---------------------------------------------
#
# `depends_on` is the entire mechanism keeping two agents out of one file, and it was
# opt-in and invoked by hand by the one participant nothing checks. These pin the
# store deriving it instead, and the honest limits of that derivation.


def test_two_tasks_over_one_file_are_sequenced_without_being_asked() -> None:
    store = Store()
    store.apply(declared("t1", at=0.0, touches=("pptmstr/store.py",)))
    store.apply(declared("t2", at=1.0, touches=("pptmstr/store.py", "pptmstr/model.py")))

    assert store.snapshot().tasks["t2"].depends_on == ("t1",)
    # The edge points at the older task, so the board keeps declaration order and
    # the second declarer waits rather than the first.
    assert store.snapshot().tasks["t1"].depends_on == ()


def test_the_overlap_check_is_not_fooled_by_how_a_path_is_spelled() -> None:
    """
    The failure this guards is silent: two spellings of one file look like two files
    to a string comparison, and the concurrent write goes ahead with nothing said.
    """
    store = Store()
    store.apply(declared("t1", at=0.0, touches=("./pptmstr/store.py",)))
    store.apply(declared("t2", at=1.0, touches=("pptmstr/ui/../store.py",)))

    assert store.snapshot().tasks["t1"].touches == ("pptmstr/store.py",)
    assert store.snapshot().tasks["t2"].depends_on == ("t1",)


def test_tasks_over_different_files_are_left_to_run_at_once() -> None:
    store = Store()
    store.apply(declared("t1", at=0.0, touches=("pptmstr/store.py",)))
    store.apply(declared("t2", at=1.0, touches=("pptmstr/bus.py",)))

    # The point of the field is parallelism where it is safe, so a false edge here
    # costs exactly what the feature was meant to buy.
    assert store.snapshot().tasks["t2"].depends_on == ()


def test_a_finished_task_is_not_something_to_wait_for() -> None:
    store = Store()
    store.apply(declared("t1", at=0.0, touches=("pptmstr/store.py",)))
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV, task_id="t1", at=2.0))

    store.apply(declared("t2", at=3.0, touches=("pptmstr/store.py",)))

    # A completed task is not writing anything. Depending on it would be a wait that
    # is already over, which reads on the board as a blocker that is not one.
    assert store.snapshot().tasks["t2"].depends_on == ()


def test_another_sessions_task_is_not_made_a_blocker() -> None:
    """
    A dependency on a task from another board would never appear on the board that
    is waiting for it: unresolvable and invisible at once. The recorded limit is
    that two sessions in one working directory get no protection from this.
    """
    store = Store()
    other: NodeId = ("sess-2", None)
    store.apply(declared("t1", at=0.0, by=other, touches=("pptmstr/store.py",)))
    store.apply(declared("t2", at=1.0, by=LEAD, touches=("pptmstr/store.py",)))

    assert store.snapshot().tasks["t2"].depends_on == ()


def test_a_dependency_the_declarer_already_named_is_not_added_twice() -> None:
    store = Store()
    store.apply(declared("t1", at=0.0, touches=("pptmstr/store.py",)))
    store.apply(declared("t2", at=1.0, deps=("t1",), touches=("pptmstr/store.py",)))

    assert store.snapshot().tasks["t2"].depends_on == ("t1",)


def test_an_added_dependency_cannot_close_a_cycle() -> None:
    """
    The added edges run after ``_would_cycle`` has passed, so this is the claim that
    makes that ordering safe: nothing existing can name the new task, because its id
    is not on the board yet, so every added edge points from a new node at an old one.
    """
    store = Store()
    store.apply(declared("t1", at=0.0, touches=("a.py",)))
    store.apply(declared("t2", at=1.0, deps=("t1",), touches=("a.py",)))
    store.apply(declared("t3", at=2.0, deps=("t2",), touches=("a.py",)))

    tasks = store.snapshot().tasks
    assert (tasks["t1"].depends_on, tasks["t2"].depends_on) == ((), ("t1",))
    assert tasks["t3"].depends_on == ("t2", "t1")
    # No member of the chain is permanently blocked: the oldest is claimable now, and
    # a cycle would mean nothing on the board ever is.
    assert store.apply(TaskClaimRequested(DEV, request_id="k1")) == (
        ClaimSettled(request_id="k1", task=store.snapshot().tasks["t1"]),
    )


def test_a_declarer_is_told_the_board_edited_its_plan() -> None:
    """
    The alternative is the lead finding out from a worker that cannot claim the
    task -- the same fact, arriving later and looking like a defect.
    """
    store = Store()
    store.apply(declared("t1", at=0.0, touches=("pptmstr/store.py",)))

    effects = store.apply(declared("t2", at=1.0, request_id="d1", touches=("pptmstr/store.py",)))

    assert effects == (TaskWriteSettled(request_id="d1", refusal=None, auto_depends=("t1",)),)


def test_a_declaration_that_added_nothing_says_so_by_saying_nothing() -> None:
    store = Store()
    effects = store.apply(declared("t1", request_id="d1", touches=("pptmstr/store.py",)))

    assert effects == (TaskWriteSettled(request_id="d1", refusal=None, auto_depends=()),)


def test_a_refused_declaration_carries_no_added_dependencies() -> None:
    store = Store()
    store.apply(declared("t1", at=0.0, touches=("pptmstr/store.py",)))

    # Duplicate id: nothing landed, so nothing was added to it.
    effects = store.apply(declared("t1", at=1.0, request_id="d1", touches=("pptmstr/store.py",)))

    assert effects == (
        TaskWriteSettled(request_id="d1", refusal=TaskRefusal.DUPLICATE_ID, auto_depends=()),
    )


def test_the_declare_handler_reports_a_dependency_the_board_added() -> None:
    """
    The wiring, not the reducer (STYLE.md §2). The store can compute the edge and
    the handler can still drop it on the floor, which is the whole failure this row
    is about -- shared state written by one participant and read by nobody.
    """
    store = Store()
    with _live_bus() as bus:
        bus.call(
            store,
            "declare_task",
            {"task_id": "t1", "title": "do t1", "touches": ["pptmstr/store.py"]},
            sender=LEAD,
        )
        text = bus.call(
            store,
            "declare_task",
            {"task_id": "t2", "title": "do t2", "touches": ["./pptmstr/store.py"]},
            sender=LEAD,
        )

    assert store.snapshot().tasks["t2"].depends_on == ("t1",)
    assert "is on the board" in text
    assert "t1" in text
    # Naming the reason as well as the id: an edge with no reason invites the lead to
    # strip it back off, which is the concurrent write the field exists to prevent.
    assert "same file" in text or "also write" in text


def test_a_caller_cannot_evade_the_overlap_check_with_its_own_spelling() -> None:
    """
    Normalisation belongs to the store for the same reason ``declared_by`` does: the
    reducer compares these, so a caller choosing the spelling could choose to miss.
    """
    store = Store()
    with _live_bus() as bus:
        bus.call(
            store,
            "declare_task",
            {"task_id": "t1", "title": "do t1", "touches": ["  pptmstr/store.py  "]},
            sender=LEAD,
        )

    assert store.snapshot().tasks["t1"].touches == ("pptmstr/store.py",)


# -- a concern that explains a row (row 3) -----------------------------------------


def test_a_concern_can_name_the_task_it_is_about() -> None:
    """
    The wiring, not the reducer (STYLE.md §2). The store can carry the link and the
    handler can still drop it, which leaves the board exactly as blind as before.
    """
    store = Store()
    with _live_bus() as bus:
        bus.call(
            store,
            "post_concern",
            {
                "to": "lead",
                "subject": "waiting on the schema decision",
                "body": "holding this until the store lands",
                "task_id": "t1",
            },
            sender=DEV,
        )
        # post_concern is the one tool that does not park on a future, so its reply
        # resolves before the frame loop has drained the intent it emitted.
        bus.pump(store)

    (posted,) = store.snapshot().concerns.values()
    assert posted.task_id == "t1"


def test_a_concern_about_no_task_in_particular_still_sends() -> None:
    """
    Most concerns are not about a board row. Requiring one would make agents invent
    a task id to send a message.
    """
    store = Store()
    with _live_bus() as bus:
        text = bus.call(
            store,
            "post_concern",
            {"to": "lead", "subject": "a question", "body": "which model"},
            sender=DEV,
        )
        bus.pump(store)

    (posted,) = store.snapshot().concerns.values()
    assert posted.task_id is None
    assert "delivered" in text.lower()


def test_a_concern_naming_a_task_that_does_not_exist_is_still_delivered() -> None:
    """
    The tool's other refusal -- an unresolvable role -- stops the message reaching
    anyone. A bad task id costs the link and nothing else, so refusing the whole
    message would lose the part that was certainly correct.
    """
    store = Store()
    with _live_bus() as bus:
        text = bus.call(
            store,
            "post_concern",
            {"to": "lead", "subject": "s", "body": "b", "task_id": "t-nope"},
            sender=DEV,
        )
        bus.pump(store)

    (posted,) = store.snapshot().concerns.values()
    assert posted.task_id == "t-nope"
    assert "delivered" in text.lower()


# -- claiming ---------------------------------------------------------------------


def test_a_claim_is_answered_with_the_task_it_won() -> None:
    store = Store()
    store.apply(declared("t1"))

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
    store.apply(declared("t2", deps=("missing",)))

    assert store.apply(TaskClaimRequested(DEV, request_id="k1")) == (
        ClaimSettled(request_id="k1", task=None),
    )


def test_two_workers_racing_for_one_task_settle_differently() -> None:
    store = Store()
    store.apply(declared("t1"))

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
    store.apply(declared("newer", at=9.0))
    store.apply(declared("older", at=1.0))

    (settled,) = store.apply(TaskClaimRequested(DEV, request_id="k1"))

    assert isinstance(settled, ClaimSettled) and settled.task is not None
    assert settled.task.id == "older"


def test_claiming_a_named_task_that_is_blocked_wins_nothing() -> None:
    store = Store()
    store.apply(declared("t1", at=0.0))
    store.apply(declared("t2", deps=("t1",), at=1.0))

    # Not "falls back to t1". A worker that asked for t2 is told no.
    assert store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t2")) == (
        ClaimSettled(request_id="k1", task=None),
    )


def test_a_released_task_returns_to_the_pool() -> None:
    store = Store()
    store.apply(declared("t1"))
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
    store.apply(declared("t1"))
    store.apply(TaskClaimRequested(DEV, request_id="k1"))

    store.apply(TaskCompleted(QA, "t1", at=3.0))
    store.apply(TaskReleased(QA, "t1"))

    still = store.snapshot().tasks["t1"]
    assert still.state is TaskState.CLAIMED
    assert still.claimed_by == DEV


def test_completing_an_unclaimed_task_does_nothing() -> None:
    store = Store()
    store.apply(declared("t1"))

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

    put_on_board = store.apply(declared("t1", by=DEV, request_id="d1"))
    store.apply(TaskClaimRequested(DEV, request_id="k1"))
    released = store.apply(TaskReleased(DEV, "t1", request_id="x1"))
    store.apply(TaskClaimRequested(DEV, request_id="k2"))
    completed = store.apply(TaskCompleted(DEV, "t1", at=3.0, request_id="f1"))

    assert (_refusal(put_on_board), _refusal(released), _refusal(completed)) == (None, None, None)


def test_a_declaration_the_board_drops_says_which_way_it_dropped() -> None:
    """
    Both silent drops, named apart. A duplicate id is a naming collision the
    declarer fixes by renaming; a cycle is a dependency graph it has to redraw. A
    single "declaration refused" would send it back to guess which.
    """
    store = Store()
    store.apply(declared("t1"))
    store.apply(declared("t2", deps=("t1",)))

    duplicate = store.apply(declared("t1", request_id="d1"))
    cycle = store.apply(declared("t3", deps=("t3",), request_id="d2"))

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
    store.apply(declared("t1"))

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
    store.apply(declared("t1"))

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

    assert store.apply(declared("t1")) == ()
    assert store.apply(declared("t1")) == ()  # refused, and still silent
    assert store.apply(TaskCompleted(QA, "t1", at=1.0)) == ()
    assert store.apply(TaskReleased(QA, "t1")) == ()


# -- the effect channel itself ----------------------------------------------------


def test_effects_come_back_in_application_order() -> None:
    store = Store()
    store.apply(declared("t1"))
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

    assert store.apply(declared("t1")) == ()
    assert store.apply(ConcernPosted(QA, concern("c1"))) == ()
    assert store.apply(ConcernWithdrawn("c1")) == ()


def test_bus_state_does_not_disturb_the_agent_projections() -> None:
    store = Store()
    store.apply(declared("t1"))
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
    store.apply(declared("t1"))
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
    assert approval._BUS_DECLARE == qualified("declare_task")
    assert approval._BUS_AUTO | {approval._BUS_POST, approval._BUS_DECLARE} == set(BUS_TOOLS)


def test_a_message_between_agents_is_reviewed() -> None:
    from pptmstr.approval import Disposition, classify
    from pptmstr.bus import qualified

    assert classify(qualified("post_concern"), {}) is Disposition.REQUIRE_APPROVAL


def test_a_declaration_is_reviewed() -> None:
    """
    Row 7, per-declaration: a declaration is where work comes into existence, so it
    is where the decision belongs. It was previously auto-approved on the premise
    that the board had already been approved -- and nothing had ever approved it.
    """
    from pptmstr.approval import Disposition, classify
    from pptmstr.bus import qualified

    assert classify(qualified("declare_task"), {}) is Disposition.REQUIRE_APPROVAL


def test_coordination_is_not_reviewed() -> None:
    from pptmstr.approval import Disposition, classify
    from pptmstr.bus import qualified

    # Bookkeeping about work whose existence is now a decision the operator made at
    # declaration. Parking these would make the operator a bottleneck on a worker
    # taking the next item off a board they have already approved -- which, unlike
    # before, is now true.
    for name in ("read_inbox", "read_board", "claim_task", "complete_task", "release_task"):
        assert classify(qualified(name), {}) is Disposition.AUTO_APPROVE, name


def test_a_parked_concern_reads_as_a_message_not_an_argument_dict() -> None:
    from pptmstr.approval import summarize
    from pptmstr.bus import qualified

    row = summarize(
        qualified("post_concern"),
        {"to": "dev", "subject": "retry loop never terminates", "body": "..."},
    )
    assert row == "message dev: retry loop never terminates"


def test_a_parked_declaration_reads_as_the_work_not_an_argument_dict() -> None:
    """
    Without a case of its own the row falls through to the generic branch and reads
    `mcp__pptmstr__declare_task task_id='t-a1b2'`, which is the serialised argument
    dict this function exists to avoid. The queue is scanned, not read.
    """
    from pptmstr.approval import summarize
    from pptmstr.bus import qualified

    row = summarize(qualified("declare_task"), {"task_id": "t1", "title": "validate the checksum"})
    assert row == "declare validate the checksum"


def test_a_parked_declaration_carries_its_size() -> None:
    """
    Sign-off is a scoping moment, so the two facts that say how big a task is ride
    on the row rather than waiting behind it: what it will write, and what it is
    sequenced after.
    """
    from pptmstr.approval import summarize
    from pptmstr.bus import qualified

    row = summarize(
        qualified("declare_task"),
        {
            "title": "validate the checksum",
            "touches": ["tle/parse.py", "tle/checksum.py"],
            "depends_on": ["t1"],
        },
    )
    assert row == (
        "declare validate the checksum · writes tle/parse.py, tle/checksum.py · after t1"
    )


def test_a_declaration_with_no_title_is_named_rather_than_blank() -> None:
    from pptmstr.approval import summarize
    from pptmstr.bus import qualified

    assert summarize(qualified("declare_task"), {"task_id": "t1"}) == "declare (untitled)"


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
    # "See every task on your team's board" -- takes nothing. The session is the
    # gate's stamp, not an argument, so there is nothing here for a model to fill.
    "read_board": {},
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


def test_a_worker_can_read_the_board_it_is_working_from() -> None:
    """
    Report 2, which the record calls *literally true and a gap in the original
    specification*: `Task.detail` was written by one participant, read in exactly
    one place in the application, and shown to nobody. A worker could not list the
    tasks it was being asked to coordinate around.

    Driven through the real server so the answer is the text an agent reads, not
    the effect the store produced -- the projection is the point, and a tuple of
    rows nobody formats is the same gap one layer up.
    """
    store = Store()
    with _live_bus() as bus:
        empty = bus.call(store, "read_board", {}, sender=LEAD)
        bus.call(store, "declare_task", {"task_id": "t1", "title": "wire the gate"}, sender=LEAD)
        bus.call(
            store,
            "declare_task",
            {"task_id": "t2", "title": "test the gate", "depends_on": ["t1"]},
            sender=LEAD,
        )
        bus.call(store, "claim_task", {"task_id": "t1"}, sender=DEV)
        listed = bus.call(store, "read_board", {}, sender=QA)

    assert "no tasks on it yet" in empty
    assert "2 task(s)" in listed
    # Every row, not only the caller's own and not only what is claimable: QA holds
    # nothing and is shown both.
    assert "t1" in listed and "wire the gate" in listed
    assert "t2" in listed and "test the gate" in listed
    # The three derived facts a bare Task cannot carry.
    assert "claimed" in listed
    assert "held by" in listed
    assert "waiting on t1" in listed


def test_the_board_read_names_a_dependency_that_was_never_declared() -> None:
    """
    A typo in a `depends_on` and an honest wait are the same row on the record and
    opposite problems: one clears when a teammate finishes, the other never clears
    at all. `declare_task` accepts a missing id silently, so this line is the only
    thing in the session that would ever say so to the agent that has to act on it.
    """
    store = Store()
    with _live_bus() as bus:
        bus.call(
            store,
            "declare_task",
            {"task_id": "t2", "title": "blocked forever", "depends_on": ["tpyo"]},
            sender=LEAD,
        )
        listed = bus.call(store, "read_board", {}, sender=DEV)

    assert "NEVER DECLARED: tpyo" in listed


def test_a_worker_is_shown_its_own_sessions_board_and_no_other() -> None:
    """
    The scoping decision, at the layer an agent meets it. Two sessions share one
    fleet-wide task map, and the read is keyed on the sender the gate authenticated
    rather than on anything the model can pass -- there is no session argument to
    get wrong.
    """
    other: NodeId = ("sess-2", None)
    store = Store()
    with _live_bus() as bus:
        bus.call(store, "declare_task", {"task_id": "ours", "title": "our work"}, sender=LEAD)
        bus.call(store, "declare_task", {"task_id": "theirs", "title": "their work"}, sender=other)

        mine = bus.call(store, "read_board", {}, sender=DEV)
        yours = bus.call(store, "read_board", {}, sender=other)

    assert "ours" in mine and "theirs" not in mine
    assert "theirs" in yours and "ours" not in yours
    # And the claim agrees with the read: the worker cannot take what it was not shown.
    assert store.snapshot().tasks["theirs"].state is TaskState.PENDING


def test_the_board_read_and_the_pane_are_one_projection() -> None:
    """
    The reason ``board.py`` moved out of ``ui/``. Two derivations of "what is on
    this board" would be two boards, and the operator and the team disagreeing
    about the work is the failure the whole record is organised around.

    Pinned by identity of the rows rather than by comparing strings: the tool
    formats what the pane draws, and a second implementation in ``bus.py`` is
    exactly what this forbids.
    """
    from pptmstr.board import board_tasks

    store = Store()
    with _live_bus() as bus:
        bus.call(store, "declare_task", {"task_id": "t1", "title": "a thing"}, sender=LEAD)
        bus.call(store, "claim_task", {"task_id": "t1"}, sender=DEV)

        (asked,) = store.apply(BoardRead(node_id=QA, request_id="b1"))

    assert isinstance(asked, BoardDelivered)
    assert asked.tasks == board_tasks(store.snapshot(), "sess-1")


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


def test_a_declaration_is_reviewed_by_policy_and_not_by_accident() -> None:
    """
    `classify` is fail-closed, so a tool in neither set requires approval anyway --
    which makes the behavioural assertion above pass whether the decision was made
    or merely not made. `_REVIEW`'s own comment says the list must read as a
    decision rather than as whatever was left over, and this is what holds it to
    that: membership, not outcome.
    """
    from pptmstr import approval

    assert approval._BUS_DECLARE in approval._REVIEW
    assert approval._BUS_DECLARE not in approval._BUS_AUTO


# -- the board read carries the specification --------------------------------------
#
# `Task.detail` was written by declare_task and read in exactly one place: the string
# claim_task interpolates. So the lead could not read back what it wrote, and the
# worker holding a task could not ask for it again -- `_pick_claim` requires
# `is_claimable`, which requires PENDING, so the owner is told "Nothing claimable
# right now". That is what made the recorded fix for an amended spec impossible
# rather than merely undisciplined.


def test_a_worker_can_re_read_the_spec_of_the_task_it_holds() -> None:
    store = Store()
    store.apply(
        TaskDeclared(
            Task(id="t1", title="do t1", detail="keep the backoff; do not touch the parser"),
            node_id=LEAD,
        )
    )
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=DEV)

    assert "keep the backoff; do not touch the parser" in text


def test_a_lead_can_read_back_the_spec_it_wrote() -> None:
    """
    The other half of the same defect, and the one an amendment needs: a lead that
    cannot see its own declaration cannot tell that it needs amending.
    """
    store = Store()
    store.apply(TaskDeclared(Task(id="t1", title="do t1", detail="the whole spec"), node_id=LEAD))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=LEAD)

    assert "the whole spec" in text


def test_a_task_with_no_spec_adds_no_empty_line() -> None:
    store = Store()
    store.apply(declared("t1"))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=DEV)

    assert "t1" in text
    assert "\n    " not in text


def test_a_long_spec_is_bounded_and_says_what_it_dropped() -> None:
    """
    A silent cap on a specification is how a worker builds confidently against half
    of one -- worse than no spec, because half a spec reads as a whole one.
    """
    from pptmstr.bus import _MAX_DETAIL_CHARS

    store = Store()
    store.apply(
        TaskDeclared(
            Task(id="t1", title="do t1", detail="x" * (_MAX_DETAIL_CHARS + 500)), node_id=LEAD
        )
    )

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=LEAD)

    assert "500 more character(s)" in text


def test_the_board_read_names_the_files_a_task_claims() -> None:
    """
    A worker that cannot see which files a task claims cannot honour the boundary
    the auto-dependency exists to enforce.
    """
    store = Store()
    store.apply(declared("t1", touches=("pptmstr/store.py",)))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=DEV)

    assert "writes pptmstr/store.py" in text


def test_an_agent_reads_what_another_agent_concluded_about_a_task() -> None:
    """
    The defect this closes. A row stalled for a recorded reason is a different row
    from one stalled silently -- but a *count* only says a conclusion exists, and a
    subject only labels it. Neither lets the reader evaluate the finding, so it
    re-derives it, which is the relitigating the board exists to end.
    """
    store = Store()
    store.apply(declared("t1"))
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))
    store.apply(ConcernPosted(DEV, _about("c1", "t1")))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=QA)

    assert "waiting on the schema decision" in text
    assert "holding this until the store lands" in text


def test_a_message_that_is_not_about_a_task_is_not_broadcast() -> None:
    """
    A concern with no task_id is mail between two agents -- a question, an answer,
    a heads-up. Putting it on everyone's board turns a point-to-point channel into
    a public one, and `Concern.task_id` is what makes the line drawable.
    """
    store = Store()
    store.apply(declared("t1"))
    store.apply(ConcernPosted(DEV, _about("c1", None, body="do not broadcast me")))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=QA)

    assert "do not broadcast me" not in text


def test_a_withdrawn_note_is_not_read_back_as_reasoning() -> None:
    """A retracted conclusion is not a conclusion. `BoardTask.concerns` drops it."""
    store = Store()
    store.apply(declared("t1"))
    store.apply(ConcernPosted(DEV, _about("c1", "t1", body="I take this back")))
    store.apply(ConcernWithdrawn("c1"))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=QA)

    assert "I take this back" not in text


def test_a_long_note_is_bounded_and_says_what_it_dropped() -> None:
    from pptmstr.bus import _MAX_CONCERN_CHARS

    store = Store()
    store.apply(declared("t1"))
    store.apply(ConcernPosted(DEV, _about("c1", "t1", body="y" * (_MAX_CONCERN_CHARS + 300))))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=QA)

    assert "300 more character(s)" in text


def test_a_note_names_who_recorded_it() -> None:
    """
    The reader has to be able to act on it -- `post_concern(to=...)` routes by the
    same name -- and an unattributed finding is one the reader cannot follow up.
    """
    store = Store()
    store.apply(declared("t1"))
    store.apply(ConcernPosted(DEV, _about("c1", "t1")))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=QA)

    # DEV has no spawned record here, so the projection names it honestly rather
    # than inventing a role -- the point is that the slot is filled at all.
    assert "[" in text.split("agent note(s)")[1].split("\n")[1]


def _about(
    cid: str,
    tid: str | None,
    *,
    body: str = "holding this until the store lands",
    sender: NodeId = DEV,
) -> Concern:
    return Concern(
        id=cid,
        sender=sender,
        recipient=LEAD,
        subject="waiting on the schema decision",
        body=body,
        posted_at=1.0,
        task_id=tid,
    )


# -- the operator amends a specification (row 6) -----------------------------------
#
# An operator redirected a builder mid-task; the spec it superseded had no second
# reader. The lead had no record of the instruction, found a near-miss phrase in a
# different builder's concern, and told the worker at length that it had promoted a
# suggestion into an order. Every step was locally correct.


def test_an_amendment_rewrites_the_spec_in_place() -> None:
    store = Store()
    store.apply(TaskDeclared(Task(id="t1", title="do t1", detail="put the art in DETAIL"), LEAD))

    store.apply(TaskAmended(task_id="t1", detail="put the art in NEEDS YOU; leave detail.py"))

    assert store.snapshot().tasks["t1"].detail == "put the art in NEEDS YOU; leave detail.py"


def test_an_amendment_does_not_disturb_the_claim_on_the_task() -> None:
    """
    The worker holding it is the reader the amendment is *for*. Unclaiming to force
    a re-read would take the work away from the one participant already doing it.
    """
    store = Store()
    store.apply(declared("t1"))
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))

    store.apply(TaskAmended(task_id="t1", detail="new spec"))

    held = store.snapshot().tasks["t1"]
    assert (held.state, held.claimed_by) == (TaskState.CLAIMED, DEV)


def test_an_amendment_keeps_everything_it_does_not_change() -> None:
    store = Store()
    store.apply(
        TaskDeclared(
            Task(
                id="t1",
                title="do t1",
                detail="old",
                depends_on=("t0",),
                touches=("a.py",),
                declared_at=3.0,
            ),
            LEAD,
        )
    )

    store.apply(TaskAmended(task_id="t1", detail="new"))

    held = store.snapshot().tasks["t1"]
    assert (held.title, held.depends_on, held.touches) == ("do t1", ("t0",), ("a.py",))
    assert held.declared_by == LEAD


def test_an_amendment_to_a_task_that_does_not_exist_creates_nothing() -> None:
    """
    A typo in a task id would otherwise put an untitled, undeclared, unclaimable row
    on the board, indistinguishable from a real task nobody had got to yet.
    """
    store = Store()
    store.apply(TaskAmended(task_id="t-nope", detail="x"))

    assert store.snapshot().tasks == {}


def test_declaring_over_an_existing_id_still_does_not_amend_it() -> None:
    """
    The guard row 6 must not weaken. A repeat declaration is a retry, and honouring
    it would silently unclaim work somebody is doing -- which is why an amendment is
    a different intent rather than a relaxation of this arm.
    """
    store = Store()
    store.apply(TaskDeclared(Task(id="t1", title="do t1", detail="original"), LEAD))
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))

    store.apply(TaskDeclared(Task(id="t1", title="something else", detail="rewritten"), LEAD))

    held = store.snapshot().tasks["t1"]
    assert (held.title, held.detail) == ("do t1", "original")
    assert held.claimed_by == DEV


def test_an_amended_spec_is_what_the_worker_reads_back() -> None:
    """
    The amendment is necessary and not sufficient: `claim_task` copies `detail` into
    the worker's context once, so the board changing reaches nobody by itself. What
    closes it is the worker asking the board rather than trusting that copy -- which
    stopped being a discipline the workers cannot practise when the board read began
    carrying the spec.
    """
    store = Store()
    store.apply(TaskDeclared(Task(id="t1", title="do t1", detail="put the art in DETAIL"), LEAD))
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))
    store.apply(TaskAmended(task_id="t1", detail="put the art in NEEDS YOU"))

    with _live_bus() as bus:
        text = bus.call(store, "read_board", {}, sender=DEV)

    assert "put the art in NEEDS YOU" in text
    assert "put the art in DETAIL" not in text
