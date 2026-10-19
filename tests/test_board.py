"""
The session board projection: scoping, role naming, and derived blocked-ness.

Driven through a real ``Store`` rather than by hand-building snapshots, because
two of the properties under test are properties of the whole path -- that a task
declared over the bus carries its declarer at all, and that blocked-ness answers
for the task's own board out of a map that holds every session's.
"""

from __future__ import annotations

import dataclasses

import pytest

from pptmstr import boardlog, bus
from pptmstr.board import (
    FOREIGN,
    LEAD,
    UNDELIVERABLE,
    UNKNOWN,
    board_concerns,
    board_tasks,
    has_board,
    role_name,
)
from pptmstr.effects import ClaimSettled
from pptmstr.intents import (
    AgentFinished,
    AgentRemoved,
    AgentSpawned,
    BoardRestored,
    ConcernPosted,
    ConcernWithdrawn,
    InboxRead,
    StateChanged,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
)
from pptmstr.model import (
    AgentState,
    ClaimRefusal,
    Concern,
    ConcernState,
    NodeId,
    Snapshot,
    Task,
    TaskState,
)
from pptmstr.store import Store

S1 = "sess-1"
S2 = "sess-2"
LEAD_1: NodeId = (S1, None)
DEV_1: NodeId = (S1, "agent-dev")
QA_1: NodeId = (S1, "agent-qa")
LEAD_2: NodeId = (S2, None)
DEV_2: NodeId = (S2, "agent-dev")


def spawn(store: Store, node: NodeId, *, agent_type: str | None, task: str = "") -> None:
    store.apply(
        AgentSpawned(
            node_id=node,
            parent=None if node[1] is None else (node[0], None),
            task=task,
            model="opus",
            started_at=0.0,
            agent_type=agent_type,
        )
    )


def declare(
    store: Store,
    tid: str,
    *,
    by: NodeId,
    deps: tuple[str, ...] = (),
    at: float = 0.0,
    detail: str = "",
    touches: tuple[str, ...] = (),
):
    store.apply(
        TaskDeclared(
            Task(
                id=tid,
                title=f"do {tid}",
                detail=detail,
                depends_on=deps,
                declared_at=at,
                touches=touches,
            ),
            node_id=by,
        )
    )


def team(store: Store, session: str) -> None:
    """A lead and two workers, the shape every template produces."""
    spawn(store, (session, None), agent_type=None, task="the operator's whole prompt")
    spawn(store, (session, "agent-dev"), agent_type="builder")
    spawn(store, (session, "agent-qa"), agent_type="reviewer")


# -- role naming ------------------------------------------------------------------


def test_the_root_of_a_session_is_the_lead_not_its_task_prompt() -> None:
    """
    The driver emits the root's AgentSpawned with no agent_type, so the usual
    `agent_type or task` idiom would render the operator's entire prompt as a
    name. The lead is the recipient of nearly every concern, so this is the
    common case, not an edge.
    """
    store = Store()
    team(store, S1)

    assert role_name(store.snapshot(), LEAD_1, S1) == LEAD
    assert store.snapshot().get(LEAD_1).agent_type is None


def test_a_role_reads_as_the_name_an_agent_would_address_it_by() -> None:
    store = Store()
    spawn(store, DEV_1, agent_type="Explore")

    # The driver allocates the first agent of a type `agent_type.lower()`, so the
    # address is "explore". A board naming it "Explore" would show a role nobody can
    # reach under the string it displays.
    assert role_name(store.snapshot(), DEV_1, S1) == "explore"


def test_two_agents_in_one_role_do_not_read_as_one_agent() -> None:
    """
    The bus gives the second builder its own address, so a board rendering both as
    "builder" would put one name on two agents -- and the concern log, which is the
    audit trail of who said what, is the surface that loses the most by it.
    """
    store = Store()
    spawn(store, LEAD_1, agent_type=None)
    spawn(store, DEV_1, agent_type="builder")
    spawn(store, (S1, "agent-dev2"), agent_type="builder")
    spawn(store, QA_1, agent_type="reviewer")

    snap = store.snapshot()
    assert role_name(snap, DEV_1, S1) == "builder"
    assert role_name(snap, (S1, "agent-dev2"), S1) == "builder-2"
    assert role_name(snap, QA_1, S1) == "reviewer"


def test_the_board_calls_an_agent_what_the_bus_routes_to() -> None:
    """
    Two implementations of one rule: the driver allocates the address on
    SubagentStart, and the board recovers it from the spawn order in the snapshot.
    Nothing but this test stops them drifting, and a board that disagrees is the
    operator reading a name no one can write.

    Driven through the real ``AgentSession`` and a real ``Store`` over one spawn
    sequence -- including a wake, an off-template type and a capitalised one, which
    are the three places the two derivations could part company.
    """
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    bridge = Bridge()
    session = AgentSession(bridge, task="t", template=FEATURE)
    session.announce()

    snap = _run_spawns(
        session,
        bridge,
        ("a-1", "builder"),
        ("a-2", "reviewer"),
        ("a-3", "builder"),
        # A wake, not a spawn: the same id starting again after it stopped.
        ("a-1", "builder"),
        ("a-4", "Explore"),
        # The CLI resolves an agent_type the template never declared, and "lead" is
        # the one that would divert every upward report if it were allocatable.
        ("a-5", "lead"),
    )

    expected = {
        "a-1": "builder",
        "a-2": "reviewer",
        "a-3": "builder-2",
        "a-4": "explore",
        "a-5": "lead-2",
    }
    _assert_agrees(snap, session, expected)
    assert role_name(snap, session.node_id, session.session_id) == session.role_of(session.node_id)


def test_the_board_steps_over_a_declared_name_the_way_the_bus_does(monkeypatch) -> None:
    """
    The half of the rule FEATURE cannot exercise: a template with a role literally
    called "builder-2" pushes the second builder to "builder-3", and the board has to
    step over it too or it renders the role's name onto an agent that is not it.

    The template is registered because the board recovers it by name from the
    registry -- the root record carries a name, not a shape.
    """
    from pptmstr import templates
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import Role, WorkTemplate

    template = WorkTemplate(
        name="collide",
        description="d",
        lead_prompt="p",
        roles=(
            Role(name="builder", description="d", prompt="p"),
            Role(name="builder-2", description="d", prompt="p"),
        ),
    )
    monkeypatch.setattr(templates, "BUILT_IN", (*templates.BUILT_IN, template))

    bridge = Bridge()
    session = AgentSession(bridge, task="t", template=template)
    session.announce()
    snap = _run_spawns(
        session,
        bridge,
        ("a-1", "builder"),
        ("a-2", "builder"),
        ("a-3", "builder-2"),
    )

    _assert_agrees(snap, session, {"a-1": "builder", "a-2": "builder-3", "a-3": "builder-2"})


def _run_spawns(session, bridge, *spawns: tuple[str, str]) -> Snapshot:
    """Fire SubagentStart for each (agent_id, agent_type) and store what it emitted."""
    import asyncio

    async def start_them() -> None:
        for agent_id, agent_type in spawns:
            await session._subagent_start(_start_hook(agent_id, agent_type), None, None)

    asyncio.run(start_them())
    store = Store()
    for intent in bridge.drain():
        store.apply(intent)
    return store.snapshot()


def _assert_agrees(snap: Snapshot, session, expected: dict[str, str]) -> None:
    for agent_id, address in expected.items():
        node = (session.session_id, agent_id)
        assert session.role_of(node) == address
        assert role_name(snap, node, session.session_id) == address


def _start_hook(agent_id: str, agent_type: str) -> dict[str, object]:
    return {
        "hook_event_name": "SubagentStart",
        "agent_id": agent_id,
        "agent_type": agent_type,
        "session_id": "sess-driver",
        "cwd": "/tmp",
        "transcript_path": "/tmp/t.jsonl",
    }


def test_a_node_with_no_record_is_named_rather_than_crashing() -> None:
    assert role_name(Snapshot.empty(), ("sess-9", "agent-ghost"), "sess-9") == UNKNOWN


def test_another_sessions_root_does_not_read_as_this_boards_lead() -> None:
    """
    The worst case of the fleet-wide task map: `_pick_claim` applies no session
    filter, so another session's root can hold a task on this board. Rendering it
    as "lead" would put a foreign agent under the one name an operator would
    never think to doubt.
    """
    store = Store()
    team(store, S1)
    team(store, S2)
    snap = store.snapshot()

    assert role_name(snap, LEAD_2, S1) == f"{LEAD}, {FOREIGN}"
    # Still the lead of its own board. The name was never wrong, only the board.
    assert role_name(snap, LEAD_2, S2) == LEAD


def test_another_sessions_worker_is_named_and_qualified() -> None:
    store = Store()
    team(store, S1)
    team(store, S2)
    snap = store.snapshot()

    # Named, not hidden: it is a real agent doing real work, and "builder" is
    # accurate -- it just is not this team's builder.
    assert role_name(snap, DEV_2, S1) == f"builder, {FOREIGN}"
    assert role_name(snap, DEV_2, S2) == "builder"


def test_the_foreign_qualifier_does_not_imply_a_broken_node() -> None:
    """
    It reads as a location, not a fault. An unknown node and a foreign one are
    different conditions and must not collapse into one word.
    """
    assert FOREIGN == "another session"
    assert UNKNOWN not in FOREIGN


def test_a_worker_cannot_claim_a_task_from_another_session() -> None:
    """
    The asymmetry the board read forced a decision on. ``board_tasks`` has always
    filtered by declarer while ``_pick_claim`` filtered by nothing, so a worker
    could be handed a task that appeared on no board -- its own operator watching
    an agent work on something with nothing on screen to account for it.

    Both now ask ``Task.belongs_to``. Asserted on the effect rather than only on
    the board, because the claim being *refused* is the property; a task that
    stayed PENDING because the claim silently did nothing would look the same.
    """
    store = Store()
    team(store, S1)
    team(store, S2)
    declare(store, "t1", by=LEAD_1)

    (settled,) = store.apply(TaskClaimRequested(DEV_2, request_id="k1", task_id="t1"))

    assert isinstance(settled, ClaimSettled)
    assert settled.task is None, "a worker was handed a task from another session's board"
    assert store.snapshot().tasks["t1"].state is TaskState.PENDING
    # Still S1's, and still nobody's on S2.
    assert [r.id for r in board_tasks(store.snapshot(), S1)] == ["t1"]
    assert board_tasks(store.snapshot(), S2) == ()


def test_an_anything_claim_does_not_reach_across_sessions_either() -> None:
    """
    The named-task path and the self-claiming path are separate branches of
    ``_pick_claim`` and only one of them was exercised above. A filter on the first
    and not the second would leave `claim_task()` -- the call the briefing actually
    tells workers to make -- reaching the whole fleet.
    """
    store = Store()
    team(store, S1)
    team(store, S2)
    declare(store, "theirs", by=LEAD_1)

    (settled,) = store.apply(TaskClaimRequested(DEV_2, request_id="k1"))

    assert isinstance(settled, ClaimSettled) and settled.task is None


def test_a_foreign_owner_is_still_named_rather_than_rendered_as_a_local_one() -> None:
    """
    ``role_name``'s foreign handling now guards a state the reducer cannot enter,
    and it stays. It is one function call from a board row, the renderer is what
    a stale store or a future cross-session feature would meet first, and "lead" is
    the one name an operator would never think to doubt.

    Driven through ``role_name`` directly because the path that used to build this
    state -- a cross-session claim -- is exactly what the test above pins shut.
    """
    store = Store()
    team(store, S1)
    team(store, S2)
    snap = store.snapshot()

    assert role_name(snap, DEV_2, S2) == "builder"
    assert role_name(snap, DEV_2, S1) == f"builder, {FOREIGN}"
    assert role_name(snap, LEAD_2, S1) == f"lead, {FOREIGN}"


# -- scoping ----------------------------------------------------------------------


def test_two_sessions_boards_do_not_bleed_into_each_other() -> None:
    """
    There is one Store and one fleet-wide task map, so scoping is the whole
    reason `declared_by` exists.
    """
    store = Store()
    team(store, S1)
    team(store, S2)
    declare(store, "t1", by=LEAD_1)
    declare(store, "t2", by=DEV_2)

    assert [t.id for t in board_tasks(store.snapshot(), S1)] == ["t1"]
    assert [t.id for t in board_tasks(store.snapshot(), S2)] == ["t2"]


def test_a_task_with_no_declarer_is_on_nobodys_board() -> None:
    store = Store()
    team(store, S1)
    store.apply(TaskDeclared(Task(id="orphan", title="do orphan")))

    assert board_tasks(store.snapshot(), S1) == ()


def test_a_dependency_on_an_id_only_another_session_has_reads_as_missing() -> None:
    """
    ``tasks`` is one map for every session, so the id is found -- on a board this
    one's readers are never shown. Finished there or not, it is not what this
    task is waiting for: the row says the dependency is missing, and the
    stranger's completion does not make the task claimable.
    """
    store = Store()
    team(store, S1)
    team(store, S2)
    declare(store, "shared", by=LEAD_2, at=0.0)
    declare(store, "mine", by=LEAD_1, deps=("shared",), at=1.0)
    before = board_tasks(store.snapshot(), S1)[0]

    store.apply(TaskClaimRequested(DEV_2, request_id="k1", task_id="shared"))
    store.apply(TaskCompleted(DEV_2, "shared", at=2.0))
    effects = store.apply(TaskClaimRequested(DEV_1, request_id="k2", task_id="mine"))

    row = board_tasks(store.snapshot(), S1)[0]
    assert (before.blocked_on, before.missing) == (("shared",), ("shared",))
    assert (row.id, row.blocked_on, row.missing) == ("mine", ("shared",), ("shared",))
    assert effects == (ClaimSettled("k2", None, ClaimRefusal.WAITING),)


def test_a_concern_about_an_id_only_another_session_has_names_a_missing_task() -> None:
    store = Store()
    team(store, S1)
    team(store, S2)
    declare(store, "theirs", by=LEAD_2)
    declare(store, "ours", by=LEAD_1)
    for cid, about in (("c1", "theirs"), ("c2", "ours")):
        mail = dataclasses.replace(concern(cid, sender=QA_1, recipient=LEAD_1), task_id=about)
        store.apply(ConcernPosted(QA_1, mail))

    rows = {row.id: row.task_missing for row in board_concerns(store.snapshot(), S1)}

    assert rows == {"c1": True, "c2": False}


# -- blocked-ness -----------------------------------------------------------------


def test_an_unfinished_dependency_is_named_not_just_counted() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1, at=0.0)
    declare(store, "t2", by=LEAD_1, deps=("t1",), at=1.0)

    rows = {t.id: t for t in board_tasks(store.snapshot(), S1)}
    assert rows["t2"].blocked_on == ("t1",)
    assert rows["t2"].missing == ()

    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV_1, "t1", at=2.0))

    # No unblocking step ran, and the pane cached nothing.
    assert board_tasks(store.snapshot(), S1)[1].blocked_on == ()


def test_a_dependency_on_an_id_that_was_never_declared_reads_as_a_typo() -> None:
    """
    `declare_task` accepts a depends_on naming nothing and answers "on the
    board", so this row is the operator's only signal that a lead typo'd an id.
    Distinguishing it from an ordinary wait is the difference between waiting and
    intervening.
    """
    store = Store()
    team(store, S1)
    declare(store, "t2", by=LEAD_1, deps=("t9",))

    row = board_tasks(store.snapshot(), S1)[0]
    assert row.blocked_on == ("t9",)
    assert row.missing == ("t9",)


def test_a_mixed_dependency_list_separates_the_typo_from_the_wait() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1, at=0.0)
    declare(store, "t2", by=LEAD_1, deps=("t1", "t9"), at=1.0)

    row = board_tasks(store.snapshot(), S1)[1]
    assert row.blocked_on == ("t1", "t9")
    assert row.missing == ("t9",)


# -- an owner that stopped --------------------------------------------------------


def test_a_task_claimed_by_a_vanished_node_says_so() -> None:
    """
    AgentRemoved pops the node and leaves the tasks it claimed alone, so a board
    can outlive its worker: the row reads CLAIMED forever and nobody is working it.

    Nothing emits AgentRemoved today (intents.py says so, and says why it should
    stay that way), so this pins the reducer's behaviour rather than a live path.
    The reachable version of the same row is the terminal-claimer test below.
    """
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(AgentRemoved(DEV_1))

    row = board_tasks(store.snapshot(), S1)[0]
    assert (row.state, row.owner, row.owner_gone) == (TaskState.CLAIMED, UNKNOWN, True)


def test_a_task_claimed_by_a_finished_node_says_so_too() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(StateChanged(DEV_1, AgentState.DONE))

    row = board_tasks(store.snapshot(), S1)[0]
    # The record is still there and still names a role; what it cannot do is more work.
    assert (row.owner, row.owner_gone) == ("builder", True)


def test_a_completed_task_is_not_stranded_by_its_worker_finishing() -> None:
    """
    The combination every successful run produces, and the one the other owner
    tests all miss: `TaskCompleted` keeps `claimed_by`, and a sub-agent that did
    its job stops with AgentFinished(DONE). Without the state check every
    completed row renders as stranded, which puts the warning colour on the
    majority of a healthy board and buries the row it exists for.
    """
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV_1, "t1", at=2.0))
    store.apply(AgentFinished(DEV_1, AgentState.DONE, ended_at=3.0))

    row = board_tasks(store.snapshot(), S1)[0]
    assert row.state is TaskState.COMPLETED
    # Still names who did it -- that is worth reading. It is not a warning.
    assert (row.owner, row.owner_gone) == ("builder", False)


def test_a_completed_task_whose_worker_vanished_is_not_stranded_either() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV_1, "t1", at=2.0))
    store.apply(AgentRemoved(DEV_1))

    row = board_tasks(store.snapshot(), S1)[0]
    assert (row.owner, row.owner_gone) == (UNKNOWN, False)


def test_a_live_claimer_is_not_reported_as_gone() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(StateChanged(DEV_1, AgentState.THINKING))

    row = board_tasks(store.snapshot(), S1)[0]
    assert (row.owner, row.owner_gone) == ("builder", False)


def test_an_unclaimed_task_has_no_owner_and_is_not_gone() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)

    row = board_tasks(store.snapshot(), S1)[0]
    assert (row.owner, row.owner_gone) == (None, False)


# -- concerns ---------------------------------------------------------------------


def concern(cid: str, *, sender: NodeId, recipient: NodeId, at: float = 1.0) -> Concern:
    return Concern(
        id=cid,
        sender=sender,
        recipient=recipient,
        subject=f"about {cid}",
        body="...",
        posted_at=at,
    )


def test_a_concern_reads_as_role_to_role() -> None:
    store = Store()
    team(store, S1)
    store.apply(ConcernPosted(QA_1, concern("c1", sender=QA_1, recipient=LEAD_1)))

    row = board_concerns(store.snapshot(), S1)[0]
    assert (row.sender, row.recipient) == ("reviewer", LEAD)


def test_a_delivered_concern_stays_on_the_board() -> None:
    """
    The audit trail is the reason concerns are store objects. A log of only what
    is still waiting throws away the record of what a worker was actually told.
    """
    store = Store()
    team(store, S1)
    store.apply(ConcernPosted(QA_1, concern("c1", sender=QA_1, recipient=DEV_1)))
    store.apply(InboxRead(node_id=DEV_1, request_id="r1", at=2.0))

    row = board_concerns(store.snapshot(), S1)[0]
    assert row.state is ConcernState.DELIVERED
    assert store.snapshot().inbox_of(DEV_1) == ()


def test_a_concern_to_a_node_with_no_record_still_renders() -> None:
    """
    The store files a concern to an unknown recipient on purpose, so the log can
    hold a row whose recipient the snapshot cannot name. It must render, and it
    must not claim to know why the record is absent.
    """
    store = Store()
    team(store, S1)
    ghost: NodeId = (S1, "agent-not-yet")
    store.apply(ConcernPosted(QA_1, concern("c1", sender=QA_1, recipient=ghost)))

    row = board_concerns(store.snapshot(), S1)[0]
    assert (row.sender, row.recipient) == ("reviewer", UNKNOWN)


def test_concerns_do_not_bleed_between_sessions() -> None:
    store = Store()
    team(store, S1)
    team(store, S2)
    store.apply(ConcernPosted(QA_1, concern("c1", sender=QA_1, recipient=LEAD_1)))
    store.apply(ConcernPosted(DEV_2, concern("c2", sender=DEV_2, recipient=LEAD_2)))

    assert [c.id for c in board_concerns(store.snapshot(), S1)] == ["c1"]
    assert [c.id for c in board_concerns(store.snapshot(), S2)] == ["c2"]


def test_rows_are_ordered_oldest_first_and_do_not_reorder_on_claim() -> None:
    store = Store()
    team(store, S1)
    declare(store, "first", by=LEAD_1, at=0.0)
    declare(store, "second", by=LEAD_1, at=1.0)
    declare(store, "third", by=LEAD_1, at=2.0)

    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="third"))

    # Claiming the last one must not float it. The board is read while work moves.
    assert [t.id for t in board_tasks(store.snapshot(), S1)] == ["first", "second", "third"]


# -- whether a session has a board at all -----------------------------------------


def team_session(store: Store, session: str, template: str | None) -> None:
    store.apply(
        AgentSpawned(
            node_id=(session, None),
            parent=None,
            task="the operator's whole prompt",
            model="opus",
            started_at=0.0,
            template=template,
        )
    )


def test_a_team_session_has_a_board_from_its_first_frame() -> None:
    """
    Read from the launched template, not from what is on the board. Emptiness
    would make the heading appear mid-run on whichever event happened first.
    """
    store = Store()
    team_session(store, S1, "feature")

    assert has_board(store.snapshot(), S1)
    assert board_tasks(store.snapshot(), S1) == ()


def test_a_solo_session_has_no_board() -> None:
    store = Store()
    team_session(store, S1, "solo")

    assert not has_board(store.snapshot(), S1)


def test_a_solo_session_that_spawned_helpers_still_has_no_board() -> None:
    """
    Why 'has children' is not the test: a solo session spawns Explore sub-agents
    constantly, and labelling those a team would put a board on nearly everything.
    """
    store = Store()
    team_session(store, S1, "solo")
    spawn(store, DEV_1, agent_type="Explore")

    assert not has_board(store.snapshot(), S1)


def test_a_session_with_no_template_recorded_has_no_board() -> None:
    store = Store()
    team_session(store, S1, None)

    assert not has_board(store.snapshot(), S1)


def test_a_session_with_no_root_record_has_no_board() -> None:
    assert not has_board(Snapshot.empty(), "sess-9")


# -- what a row carries for the operator (row 3) -----------------------------------
#
# `Task.detail` was written once, read once, by one agent, and displayed nowhere.
# `Concern` had no task_id, so a task stalled for a good reason was pixel-identical
# to ordinary work in progress -- `owner_gone` distinguishes the wrong two cases.


def test_a_row_carries_the_spec_its_declarer_wrote() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1, detail="rewrite the retry loop; keep the backoff")

    assert board_tasks(store.snapshot(), S1)[0].detail == (
        "rewrite the retry loop; keep the backoff"
    )


def test_a_row_carries_the_files_the_task_claimed() -> None:
    """
    On the row because the dependency graph is now partly derived from it: an edge
    the board added is not in the lead's plan, and `blocked_on` without `touches`
    is a wait with no visible cause.
    """
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1, at=0.0, touches=("pptmstr/store.py",))
    declare(store, "t2", by=LEAD_1, at=1.0, touches=("./pptmstr/store.py",))

    rows = {r.id: r for r in board_tasks(store.snapshot(), S1)}
    # Normalised, so the row shows the spelling the overlap check actually compared.
    assert rows["t2"].touches == ("pptmstr/store.py",)
    assert rows["t2"].blocked_on == ("t1",)


def test_a_stalled_task_shows_the_reason_somebody_bothered_to_record() -> None:
    """
    The defect this row exists for: a live, working, correctly-reasoning owner that
    is deliberately waiting. `owner_gone` is false, so without this the row reads
    as ordinary progress.
    """
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(
        ConcernPosted(
            DEV_1,
            Concern(
                id="c1",
                sender=DEV_1,
                recipient=LEAD_1,
                subject="waiting on the schema decision",
                body="holding this until the store lands",
                posted_at=1.0,
                task_id="t1",
            ),
        )
    )

    row = board_tasks(store.snapshot(), S1)[0]
    assert (row.state, row.owner_gone) == (TaskState.CLAIMED, False)
    assert row.concerns == ("c1",)


def test_a_concern_about_nothing_in_particular_lands_on_no_row() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(
        ConcernPosted(
            DEV_1,
            Concern(
                id="c1",
                sender=DEV_1,
                recipient=LEAD_1,
                subject="a question",
                body="which model should I use",
                posted_at=1.0,
            ),
        )
    )

    assert board_tasks(store.snapshot(), S1)[0].concerns == ()


def test_a_withdrawn_concern_is_not_a_reason_any_more() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(ConcernPosted(DEV_1, _about("c1", "t1")))
    store.apply(ConcernWithdrawn("c1"))

    assert board_tasks(store.snapshot(), S1)[0].concerns == ()


def test_a_delivered_concern_still_explains_the_row() -> None:
    """
    The recipient having read it does not settle the matter it raised. Dropping it
    on delivery would make the explanation vanish exactly when someone started
    acting on it.
    """
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(ConcernPosted(DEV_1, _about("c1", "t1")))
    store.apply(InboxRead(LEAD_1, request_id="r1", at=2.0))

    assert board_tasks(store.snapshot(), S1)[0].concerns == ("c1",)


def test_the_reasons_on_a_row_are_ordered_oldest_first() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(ConcernPosted(DEV_1, _about("later", "t1", at=9.0)))
    store.apply(ConcernPosted(DEV_1, _about("earlier", "t1", at=2.0)))

    assert board_tasks(store.snapshot(), S1)[0].concerns == ("earlier", "later")


def test_another_sessions_concern_does_not_explain_this_boards_row() -> None:
    """
    Scoped by sender to match `board_concerns`, so a row's explanation and the
    concern log below it cannot disagree about whose messages these are.
    """
    store = Store()
    team(store, S1)
    team(store, S2)
    declare(store, "t1", by=LEAD_1)
    store.apply(ConcernPosted(DEV_2, _about("c1", "t1", sender=DEV_2, recipient=LEAD_2)))

    assert board_tasks(store.snapshot(), S1)[0].concerns == ()


def test_a_concern_naming_a_task_that_was_never_declared_says_so() -> None:
    """
    Not refused at the tool: the message was still sent, and rejecting mail over a
    typo in an optional field costs the part that was certainly correct. Reported
    the way `missing` reports a dependency that does not exist.
    """
    store = Store()
    team(store, S1)
    store.apply(ConcernPosted(DEV_1, _about("c1", "t-nope")))

    row = board_concerns(store.snapshot(), S1)[0]
    assert (row.task_id, row.task_missing) == ("t-nope", True)


def test_a_concern_naming_a_real_task_is_not_marked_missing() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(ConcernPosted(DEV_1, _about("c1", "t1")))

    row = board_concerns(store.snapshot(), S1)[0]
    assert (row.task_id, row.task_missing) == ("t1", False)


def _about(
    cid: str,
    tid: str | None,
    *,
    at: float = 1.0,
    sender: NodeId = DEV_1,
    recipient: NodeId = LEAD_1,
) -> Concern:
    return Concern(
        id=cid,
        sender=sender,
        recipient=recipient,
        subject="waiting on the schema decision",
        body="holding this until the store lands",
        posted_at=at,
        task_id=tid,
    )


# -- what a finished row carries ----------------------------------------------------


def test_a_row_carries_the_result_its_claimer_reported() -> None:
    """
    The projection is where a stored result becomes one somebody reads: the pane
    and both bus reads are built from these rows and from nothing else.
    """
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    declare(store, "t2", by=LEAD_1, at=1.0)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV_1, "t1", at=2.0, result="ran the suite; did not run mypy"))

    done, open_ = board_tasks(store.snapshot(), S1)
    assert (done.id, done.result) == ("t1", "ran the suite; did not run mypy")
    assert (open_.id, open_.result) == ("t2", "")


def test_a_row_names_every_dependency_and_not_only_the_unfinished_ones() -> None:
    """
    ``blocked_on`` empties as work completes, which is its job. A reader of one
    whole task is owed the declared graph: "waits on nothing" and "was built on
    t1, which is done" are different histories.
    """
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    declare(store, "t2", by=LEAD_1, deps=("t1", "tpyo"), at=1.0)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV_1, "t1", at=2.0))

    row = board_tasks(store.snapshot(), S1)[1]
    assert row.depends_on == ("t1", "tpyo")
    assert row.blocked_on == ("tpyo",)


def test_a_dependency_the_board_added_is_among_the_ones_a_row_names() -> None:
    """The auto-dependency lands on the record, so the whole-task read shows it."""
    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1, touches=("a.py",))
    declare(store, "t2", by=LEAD_1, touches=("a.py",), at=1.0)

    assert board_tasks(store.snapshot(), S1)[1].depends_on == ("t1",)


# -- how an author says it knows ----------------------------------------------------


def test_an_unstated_basis_is_named_as_unstated_and_a_stated_one_as_said() -> None:
    """
    The one spelling both the pane and the bus render. "says", because nothing in
    the system checked it; and an absence that is named, because an unmarked text
    beside marked ones reads as the settled kind.
    """
    from pptmstr.board import basis_label
    from pptmstr.model import Basis

    assert basis_label(None) == "basis unstated"
    assert [basis_label(b) for b in Basis] == ["says: run", "says: read", "says: relayed"]


def test_the_rows_carry_each_authors_basis_and_derive_none() -> None:
    from pptmstr.model import Basis

    store = Store()
    team(store, S1)
    store.apply(
        TaskDeclared(
            Task(id="t1", title="x", detail="d", basis=Basis.RELAYED, declared_at=0.0),
            node_id=LEAD_1,
        )
    )
    declare(store, "t2", by=LEAD_1, at=1.0, detail="d")
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV_1, "t1", at=2.0, result="ran it", basis=Basis.RUN))
    for cid, basis in (("c1", Basis.READ), ("c2", None)):
        store.apply(
            ConcernPosted(
                QA_1,
                Concern(
                    id=cid,
                    sender=QA_1,
                    recipient=LEAD_1,
                    subject="s",
                    body="b",
                    posted_at=3.0,
                    task_id="t1",
                    basis=basis,
                ),
            )
        )

    snap = store.snapshot()
    marked, unmarked = board_tasks(snap, S1)
    assert (marked.basis, marked.result_basis) == (Basis.RELAYED, Basis.RUN)
    assert (unmarked.basis, unmarked.result_basis) == (None, None)
    assert {c.id: c.basis for c in board_concerns(snap, S1)} == {"c1": Basis.READ, "c2": None}


# -- a property, what was not run, and an incident ----------------------------------


def test_what_was_not_run_is_unstated_until_said_and_nothing_only_when_stated() -> None:
    """
    The three spellings every surface renders, and the two that must never
    collapse: a claimer that did not say, and one that said nothing was left.
    Neither label for a stated answer vouches for it.
    """
    from pptmstr.board import not_run_label

    assert not_run_label(None) == "not run: unstated"
    assert not_run_label("") == "says nothing was left unrun"
    assert not_run_label("the probe") == "says what was not run"
    assert "nothing" not in not_run_label(None)


def test_an_incident_is_marked_and_an_unmarked_concern_says_nothing_either_way() -> None:
    from pptmstr.board import incident_marker, property_label

    assert incident_marker(True) == "INCIDENT "
    assert incident_marker(False) == ""
    assert property_label("never drop a row.") == "must not break: never drop a row."
    assert property_label("") == ""


def _round(store: Store) -> None:
    """
    One session's finished round: five tasks covering every standing a completed
    row can have, one still open, and three concerns of which one is marked.
    """
    team(store, S1)
    store.apply(
        TaskDeclared(
            Task(id="t0", title="x", property="never drop a row.", declared_at=0.0),
            node_id=LEAD_1,
        )
    )
    for i in range(1, 6):
        declare(store, f"t{i}", by=LEAD_1, at=float(i))
    finishes: dict[str, dict[str, object]] = {
        "t0": {"result": "ran it", "not_run": "the probe, live"},
        "t1": {"result": "ran it"},
        "t2": {"result": "ran it", "not_run": ""},
        "t3": {},
        "t4": {"not_run": "everything"},
    }
    for n, (tid, given) in enumerate(finishes.items()):
        store.apply(TaskClaimRequested(DEV_1, request_id=f"k{n}", task_id=tid))
        store.apply(TaskCompleted(DEV_1, tid, at=10.0 + n, **given))  # type: ignore[arg-type]
    store.apply(TaskClaimRequested(DEV_1, request_id="k9", task_id="t5"))
    for n, (cid, incident, state_of) in enumerate(
        (("c1", True, "posted"), ("c2", False, "posted"), ("c3", True, "withdrawn"))
    ):
        store.apply(
            ConcernPosted(
                DEV_1,
                Concern(
                    id=cid,
                    sender=DEV_1,
                    recipient=LEAD_1,
                    subject=cid,
                    body="b",
                    posted_at=20.0 + n,
                    incident=incident,
                ),
            )
        )
        if state_of == "withdrawn":
            store.apply(ConcernWithdrawn(cid))


def test_the_rows_carry_the_property_the_list_and_the_mark_as_stored() -> None:
    store = Store()
    _round(store)
    snap = store.snapshot()
    rows = {r.id: r for r in board_tasks(snap, S1)}

    assert rows["t0"].property == "never drop a row." and rows["t1"].property == ""
    assert {tid: r.not_run for tid, r in rows.items()} == {
        "t0": "the probe, live",
        "t1": None,
        "t2": "",
        "t3": None,
        "t4": "everything",
        "t5": None,
    }
    assert {c.id: c.incident for c in board_concerns(snap, S1)} == {
        "c1": True,
        "c2": False,
        "c3": True,
    }


def test_the_ledger_lists_every_finished_task_nobody_said_was_run_in_full() -> None:
    """
    On it: a claimer that named something, a claimer that gave a result and no
    list, a completion with no account at all, and a list with no result. Off it:
    the one row whose claimer stated nothing was left, and the row that is not
    finished -- unfinished work is not unverified work.

    The unstated rows are the point. A ledger of only the rows that volunteered
    something would be shortest for the team that said least.
    """
    from pptmstr.board import unrun_ledger

    store = Store()
    _round(store)
    ledger = unrun_ledger(store.snapshot(), S1)

    assert [(r.id, r.not_run) for r in ledger] == [
        ("t0", "the probe, live"),
        ("t1", None),
        ("t3", None),
        ("t4", "everything"),
    ]
    assert all(r.owner == "builder" for r in ledger)


def test_the_incident_list_is_the_marked_concerns_and_keeps_a_withdrawn_one() -> None:
    from pptmstr.board import incidents

    store = Store()
    _round(store)
    listed = incidents(store.snapshot(), S1)

    assert [(c.id, c.state) for c in listed] == [
        ("c1", ConcernState.POSTED),
        ("c3", ConcernState.WITHDRAWN),
    ]


def test_the_ledger_and_the_incident_list_are_scoped_to_the_session_asked_about() -> None:
    from pptmstr.board import incidents, unrun_ledger

    store = Store()
    _round(store)
    team(store, S2)
    snap = store.snapshot()

    assert unrun_ledger(snap, S2) == () and incidents(snap, S2) == ()
    assert len(unrun_ledger(snap, S1)) == 4 and len(incidents(snap, S1)) == 2


def test_a_row_carries_its_version_what_its_holder_was_handed_and_every_earlier_text() -> None:
    """
    The amender is a name a reader can use: a role for an agent, and the operator
    by name where the record holds None.
    """
    from pptmstr.board import OPERATOR, BoardVersion
    from pptmstr.intents import TaskAmended
    from pptmstr.model import Basis

    store = Store()
    team(store, S1)
    lead, dev = (S1, None), (S1, "agent-dev")
    store.apply(
        TaskDeclared(
            Task(id="t1", title="x", detail="first", property="p.", basis=Basis.RUN), node_id=lead
        )
    )
    store.apply(TaskClaimRequested(dev, request_id="k1", task_id="t1"))
    store.apply(TaskAmended("t1", detail="second", node_id=lead, at=5.0, basis=Basis.READ))
    store.apply(TaskAmended("t1", detail="third", at=6.0))
    (row,) = board_tasks(store.snapshot(), S1)

    assert (row.detail, row.version, row.claimed_version) == ("third", 3, 1)
    assert row.superseded == (
        BoardVersion("first", "p.", Basis.RUN, replaced_at=5.0, replaced_by="lead"),
        BoardVersion("second", "p.", Basis.READ, replaced_at=6.0, replaced_by=OPERATOR),
    )


def test_a_task_never_amended_is_version_one_with_no_history() -> None:
    store = Store()
    team(store, S1)
    declare(store, "t1", by=(S1, None))
    (row,) = board_tasks(store.snapshot(), S1)

    assert (row.version, row.claimed_version, row.superseded) == (1, None, ())


# -- which specification a row shows, and whether its holder has seen it --------------


def _versioned(*then: object) -> Store:
    """t1 declared by the lead, claimed by the builder, then whatever follows."""
    store = Store()
    team(store, S1)
    declare(store, "t1", by=(S1, None), detail="first")
    store.apply(TaskClaimRequested((S1, "agent-dev"), request_id="k1", task_id="t1"))
    for intent in then:
        store.apply(intent)  # type: ignore[arg-type]
    return store


def test_a_row_at_its_declared_specification_says_nothing_about_versions() -> None:
    from pptmstr.board import stale_label, version_label

    (row,) = board_tasks(_versioned().snapshot(), S1)

    assert (version_label(row), stale_label(row)) == ("", "")
    assert row.holder == (S1, "agent-dev")


def test_a_row_says_which_version_it_shows_and_who_replaced_the_last_one() -> None:
    from pptmstr.board import version_label
    from pptmstr.intents import TaskAmended

    store = _versioned(
        TaskAmended("t1", detail="second", node_id=(S1, None)),
        TaskAmended("t1", detail="third"),
    )
    (row,) = board_tasks(store.snapshot(), S1)

    assert version_label(row) == "specification v3, replaced by the operator"
    earlier = board_tasks(_versioned(TaskAmended("t1", "2", node_id=(S1, None))).snapshot(), S1)
    assert version_label(earlier[0]) == "specification v2, replaced by lead"


def test_a_row_says_when_its_holder_or_its_result_is_behind_the_specification() -> None:
    """
    One fact at two moments. While the task is held it is that the holder has not
    been shown the text; once finished it is which text the result is an account
    of. A holder shown the current one, and a task nobody holds, say nothing.
    """
    from pptmstr.board import stale_label
    from pptmstr.intents import TaskAmended, TaskReleased, TaskVersionHanded

    dev = (S1, "agent-dev")
    amended = TaskAmended("t1", detail="second")

    def label(*then: object) -> str:
        (row,) = board_tasks(_versioned(*then).snapshot(), S1)
        return stale_label(row)

    assert label(amended) == "its holder was handed v1 and has not been shown v2"
    assert label(amended, TaskVersionHanded(dev, "t1", version=2)) == ""
    assert label(amended, TaskReleased(dev, "t1")) == ""
    assert label(amended, TaskCompleted(dev, "t1", at=5.0)) == (
        "result written against v1; the specification is now v2"
    )
    assert label(TaskCompleted(dev, "t1", at=5.0), amended) == (
        "result written against v1; the specification is now v2"
    )
    assert (
        label(amended, TaskVersionHanded(dev, "t1", version=2), TaskCompleted(dev, "t1", 5.0)) == ""
    )


def test_a_claim_that_recorded_no_version_is_said_to_be_unrecorded_once_the_text_moved() -> None:
    """
    A state only a restore or a test builds. It must not read as "handed the
    current one", and while the task has had one text there is nothing to say.
    """
    import dataclasses

    from pptmstr.board import BoardVersion, stale_label

    (row,) = board_tasks(_versioned().snapshot(), S1)
    unrecorded = dataclasses.replace(row, claimed_version=None)
    moved = dataclasses.replace(
        unrecorded,
        version=2,
        superseded=(BoardVersion("first", "", None, replaced_at=1.0, replaced_by="lead"),),
    )

    assert stale_label(unrecorded) == ""
    assert stale_label(moved) == (
        "its holder was handed a version nobody recorded and has not been shown v2"
    )


def test_a_replacers_name_cannot_break_the_line_a_version_is_said_on() -> None:
    """A role name is an agent's type, which is a string a model chose."""
    import dataclasses

    from pptmstr.board import BoardVersion, version_label

    (row,) = board_tasks(_versioned().snapshot(), S1)
    forged = dataclasses.replace(
        row,
        version=2,
        superseded=(
            BoardVersion("first", "", None, replaced_at=1.0, replaced_by="builder\n- t9 [done]"),
        ),
    )

    assert version_label(forged) == "specification v2, replaced by builder - t9 [done]"


# -- the one allocator ---------------------------------------------------------------


def test_an_address_is_stepped_over_whoever_or_nobody_holds_it() -> None:
    from pptmstr.board import address_for

    held = {"builder": "a-1", "builder-2": None, "reviewer": "a-9"}

    assert address_for("a-new", "Builder ", held, ()) == "builder-3"
    assert address_for("a-new", "builder", held, ("builder-3",)) == "builder-4"
    assert address_for("a-new", "lead", {}, ()) == "lead-2"
    assert address_for("a-new", " ", {}, ()) == "agent"


def test_an_id_the_table_names_gets_its_address_back_wherever_it_starts() -> None:
    from pptmstr.board import address_for

    held = {"builder": "a-1", "builder-2": "a-2", "builder-3": "a-new"}

    assert address_for("a-2", "builder", held, ()) == "builder-2"
    # By id and never by role: the type it reports now is not consulted.
    assert address_for("a-2", "reviewer", held, ()) == "builder-2"


def test_a_table_is_the_same_whatever_order_its_pairs_arrive_in() -> None:
    """
    The driver's copy is made from a log's ``Board.addresses`` and the board's
    from the ``Restore.parties`` the store made of that log, and the two do not
    share an order. An address written under two ids is reserved for neither.
    """
    import itertools

    from pptmstr.board import reserved

    pairs = [
        ("builder", "a-1"),
        (" Builder ", None),
        ("builder-2", "a-2"),
        ("builder-2", "a-3"),
        ("reviewer", None),
        ("", "a-4"),
        ("lead", None),
        ("main", "a-5"),
    ]
    expected = {"builder": "a-1", "builder-2": None, "reviewer": None}

    for order in itertools.permutations(pairs):
        assert reserved(order) == expected


def test_the_driver_and_the_board_share_the_root_names_and_the_allocator() -> None:
    """
    The wiring. Two spellings of the root's names, or a driver with a loop of its
    own, is two allocators again however well each is tested.
    """
    import inspect

    from pptmstr import board, driver

    assert driver.ROOT_ADDRESSES is board.ROOT_NAMES
    assert driver.address_for is board.address_for
    source = inspect.getsource(driver.AgentSession._address_for)
    assert "return address_for(" in source and "while" not in source
    assert "address_for(" in inspect.getsource(board._addresses)


# -- a party from before the restart ------------------------------------------------

OLD_BUILDER_2: NodeId = (S1, "agent-old-2")
NEW_BUILDER: NodeId = (S1, "agent-new-1")
NEW_BUILDER_2: NodeId = (S1, "agent-new-2")
_CLOCK = boardlog.Clock(wall=5_000.0, monotonic=100.0)


def _declared(task_id: str, seconds_ago: float) -> boardlog.Declared:
    return boardlog.Declared(
        at=_CLOCK.wall - seconds_ago,
        task_id=task_id,
        title=f"do {task_id}",
        declared_by="lead",
        declared_by_id=(S1,),
    )


def _resumed(*records: boardlog.Record) -> Store:
    """
    A session resumed in a new process: its logged board restored, then its lead
    and two builders started. What this process calls them depends on the names
    the log has: an address the restored board names is not given to either.
    """
    store = Store()
    store.apply(BoardRestored(S1, boardlog.restore(records), _CLOCK), now=_CLOCK.monotonic)
    spawn(store, LEAD_1, agent_type=None)
    spawn(store, NEW_BUILDER, agent_type="builder")
    spawn(store, NEW_BUILDER_2, agent_type="builder")
    return store


def _old_claim(by_id: tuple[str, ...]) -> Store:
    """``t1`` held by the earlier process's builder-2, ``t2`` free and then taken."""
    store = _resumed(
        _declared("t1", 50.0),
        boardlog.Claimed(at=_CLOCK.wall - 40.0, task_id="t1", by="builder-2", by_id=by_id),
        _declared("t2", 30.0),
    )
    store.apply(TaskClaimRequested(NEW_BUILDER_2, request_id="k", task_id="t2"))
    return store


@pytest.mark.parametrize(
    "by_id", [OLD_BUILDER_2, ()], ids=["logged with its id", "by address only"]
)
def test_no_live_agent_is_given_the_name_a_restored_board_has_for_another(
    by_id: tuple[str, ...],
) -> None:
    """
    The agent that held ``t1`` when the last process ended was ``builder-2``.
    The second builder this process starts is not a second ``builder-2``: it
    steps over the name, so on the board, and in the text ``read_board`` hands
    every agent, no bare name is one the old claim is also under -- whether the
    log has the old agent's id or only its address.
    """
    store = _old_claim(by_id)
    snap = store.snapshot()

    old, live = board_tasks(snap, S1)

    assert role_name(snap, NEW_BUILDER, S1) == "builder"
    assert role_name(snap, NEW_BUILDER_2, S1) == "builder-3"
    assert (old.id, old.owner) == ("t1", "builder-2, from an earlier run")
    assert (live.id, live.owner, live.holder) == ("t2", "builder-3", NEW_BUILDER_2)
    assert old.holder != live.holder and old.owner_gone and not live.owner_gone

    text = bus._board_text((old, live), {})
    held = [line.split("held by ")[1] for line in text.splitlines() if "held by " in line]
    assert [name.split(" · ")[0].split(" but ")[0] for name in held] == [
        "builder-2, from an earlier run",
        "builder-3",
    ]


def test_a_node_nothing_restored_and_nothing_spawned_is_still_only_unknown() -> None:
    """The qualifier is for a party a log named. It asserts a cause, so it needs one."""
    store = _old_claim(OLD_BUILDER_2)

    assert role_name(store.snapshot(), (S1, "never-heard-of"), S1) == UNKNOWN


def test_a_restored_party_the_log_had_no_name_for_is_unknown_from_before_the_restart() -> None:
    store = _resumed(
        _declared("t1", 50.0),
        boardlog.Claimed(at=_CLOCK.wall - 40.0, task_id="t1", by="", by_id=OLD_BUILDER_2),
    )

    assert role_name(store.snapshot(), OLD_BUILDER_2, S1) == f"{UNKNOWN}, from an earlier run"


def test_a_restored_party_seen_from_another_sessions_board_carries_both_qualifiers() -> None:
    store = _old_claim(OLD_BUILDER_2)

    assert role_name(store.snapshot(), OLD_BUILDER_2, S2) == (
        f"builder-2, from an earlier run, {FOREIGN}"
    )


def test_a_restored_agent_that_is_running_again_has_the_name_it_had() -> None:
    """
    Woken under the id the log has, it has a record, so it is an agent of this
    process and reads as one -- under the address the log has for that id,
    although two builders were started here before it. The driver gives it the
    same one back (``AgentSession._address_for``), so the name routes.
    """
    store = _old_claim(OLD_BUILDER_2)
    spawn(store, OLD_BUILDER_2, agent_type="builder")
    snap = store.snapshot()

    old, live = board_tasks(snap, S1)

    assert old.owner == "builder-2" and not old.owner_gone
    assert (role_name(snap, NEW_BUILDER, S1), live.owner) == ("builder", "builder-3")


def _announce(store: Store, node: NodeId, at: float, agent_type: str | None = None) -> None:
    store.apply(
        AgentSpawned(
            node_id=node,
            parent=None if node[1] is None else (node[0], None),
            task="",
            model="opus",
            started_at=at,
            agent_type=agent_type,
        )
    )


def _started_again() -> Store:
    """
    One window and no restored board. A builder took ``t1`` and ended; the
    session was started again; a builder of the new run took ``t2`` and finished
    its turn.
    """
    store = Store()
    _announce(store, LEAD_1, 1.0)
    _announce(store, DEV_1, 2.0, "builder")
    declare(store, "t1", by=LEAD_1, at=3.0)
    declare(store, "t2", by=LEAD_1, at=4.0)
    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="t1"))
    store.apply(AgentFinished(DEV_1, AgentState.FAILED, ended_at=5.0))
    _announce(store, LEAD_1, 9.0)
    _announce(store, QA_1, 10.0, "builder")
    store.apply(TaskClaimRequested(QA_1, request_id="k2", task_id="t2"))
    store.apply(AgentFinished(QA_1, AgentState.DONE, ended_at=11.0))
    return store


def test_an_earlier_runs_agent_is_named_as_one_while_this_process_still_has_its_record() -> None:
    """
    Resumed in one window: nothing was restored and the old builder is a record,
    so without the qualifier its row reads as a builder of this run that
    stopped. The new run's builder keeps its own address beside it, and the
    address alone -- what a log record is written with -- carries no qualifier.
    """
    from pptmstr.board import address_in_session

    snap = _started_again().snapshot()

    old, new = board_tasks(snap, S1)

    assert (old.id, old.owner, old.owner_gone) == ("t1", "builder, from an earlier run", True)
    assert (new.id, new.owner, new.owner_gone) == ("t2", "builder-2", True)
    assert old.holder_left_behind and not new.holder_left_behind
    assert address_in_session(snap, DEV_1) == "builder"
    assert address_in_session(snap, LEAD_1) == role_name(snap, LEAD_1, S1) == "lead"
    assert role_name(snap, DEV_1, S2) == f"builder, from an earlier run, {FOREIGN}"


def test_the_qualifier_comes_off_when_the_earlier_runs_agent_is_started_again() -> None:
    store = _started_again()

    _announce(store, DEV_1, 12.0, "builder")

    old = board_tasks(store.snapshot(), S1)[0]
    assert (old.owner, old.owner_gone, old.holder_left_behind) == ("builder", False, False)


def test_mail_waiting_for_an_earlier_runs_agent_in_this_window_is_undeliverable() -> None:
    """
    The same row a restored board gives (below), reached with no restart: the
    recipient's run is over, so nothing reads that inbox. Mail to the new run's
    builder, which only finished its turn, is ordinary waiting mail.
    """
    store = _started_again()
    for at, (cid, recipient) in enumerate((("c-old", DEV_1), ("c-new", QA_1))):
        concern = Concern(
            id=cid, sender=LEAD_1, recipient=recipient, subject="s", body="b", posted_at=20.0 + at
        )
        store.apply(ConcernPosted(LEAD_1, concern))

    old, new = board_concerns(store.snapshot(), S1)

    assert (old.recipient, old.undeliverable) == ("builder, from an earlier run", True)
    assert (new.recipient, new.undeliverable) == ("builder-2", False)

    _announce(store, DEV_1, 12.0, "builder")
    assert not board_concerns(store.snapshot(), S1)[0].undeliverable


# -- a task the operator closed -------------------------------------------------------


def test_a_row_the_operator_closed_says_so_and_is_nobodys_completion() -> None:
    """
    The row is "completed" and names no owner, and its label says both halves:
    who ended it, and that no work is reported. It is off the ledger of results
    nobody said were run, because it reports no result of anybody's.
    """
    from pptmstr.board import closed_label, unrun_ledger
    from pptmstr.intents import TaskClosed

    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k", task_id="t1"))
    held = board_tasks(store.snapshot(), S1)[0]

    store.apply(TaskClosed("t1", "superseded by t9"), now=5.0)

    (row,) = board_tasks(store.snapshot(), S1)
    assert (held.closed_by_operator, closed_label(held)) == (False, "")
    assert (row.state, row.owner, row.holder) == (TaskState.COMPLETED, None, None)
    assert row.closed_by_operator and not row.owner_gone
    assert row.result == "closed by the operator: superseded by t9"
    assert closed_label(row) == (
        "closed by the operator -- not finished by an agent, and no work is reported"
    )
    assert unrun_ledger(store.snapshot(), S1) == ()


def test_a_claimer_that_types_the_closing_words_has_not_closed_its_task() -> None:
    """
    The label is read off the store's fact and never off text an agent wrote.
    """
    from pptmstr.board import closed_label, unrun_ledger

    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k", task_id="t1"))
    store.apply(TaskCompleted(DEV_1, "t1", at=2.0, result="closed by the operator: trust me"))

    (row,) = board_tasks(store.snapshot(), S1)

    assert (row.closed_by_operator, closed_label(row), row.owner) == (False, "", "builder")
    assert [r.id for r in unrun_ledger(store.snapshot(), S1)] == ["t1"]


# -- a task its declarer withdrew ------------------------------------------------------


def test_a_row_its_declarer_withdrew_says_so_and_is_nobodys_completion() -> None:
    """
    ``closed_label``'s rule for a withdrawal, under its own words, and off the
    ledger for the same reason: it reports no result of anybody's work.
    """
    from pptmstr.board import closed_label, ended_label, unrun_ledger, withdrawn_label
    from pptmstr.intents import TaskWithdrawn

    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k", task_id="t1"))
    held = board_tasks(store.snapshot(), S1)[0]

    store.apply(TaskWithdrawn(LEAD_1, "t1", "replaced by t9"), now=5.0)

    (row,) = board_tasks(store.snapshot(), S1)
    assert (held.withdrawn, held.unworked, withdrawn_label(held), ended_label(held)) == (
        False,
        False,
        "",
        "",
    )
    assert (row.state, row.owner, row.holder) == (TaskState.COMPLETED, None, None)
    assert row.withdrawn and row.unworked and not row.closed_by_operator
    assert row.result == "withdrawn by its declarer: replaced by t9"
    assert closed_label(row) == ""
    assert (
        withdrawn_label(row)
        == ended_label(row)
        == ("withdrawn by its declarer -- not finished by an agent, and no work is reported")
    )
    assert unrun_ledger(store.snapshot(), S1) == ()


@pytest.mark.parametrize("edited", [False, True], ids=["as sent", "edited"])
@pytest.mark.parametrize("by_lead", [False, True], ids=["by its declarer", "by the lead"])
def test_a_withdrawn_row_says_who_withdrew_it_and_whose_words_its_reason_is(
    by_lead: bool, edited: bool
) -> None:
    """
    The row carries the task's two facts, and its label -- which the board
    pane draws through ``ended_label`` -- is ``model.withdrawal_label`` of them.
    """
    from pptmstr.board import ended_label, withdrawn_label
    from pptmstr.intents import AgentFinished, TaskWithdrawn
    from pptmstr.model import AgentState, withdrawal_label

    declarer = DEV_1 if by_lead else LEAD_1
    store = Store()
    team(store, S1)
    declare(store, "t1", by=declarer)
    store.apply(AgentFinished(DEV_1, AgentState.DONE, ended_at=1.0))

    store.apply(TaskWithdrawn(LEAD_1, "t1", "x", edited=edited), now=5.0)

    (row,) = board_tasks(store.snapshot(), S1)
    assert (row.withdrawn_by_lead, row.withdrawal_edited) == (by_lead, edited)
    assert withdrawn_label(row) == ended_label(row)
    assert withdrawn_label(row) == (
        f"{withdrawal_label(by_lead, edited)} -- not finished by an agent, and no work is reported"
    )
    assert ("withdrawn by its declarer" in withdrawn_label(row)) is not by_lead
    assert ("edited by the operator" in withdrawn_label(row)) is edited


def test_a_claimer_that_types_the_withdrawal_words_has_not_withdrawn_its_task() -> None:
    from pptmstr.board import ended_label, unrun_ledger

    store = Store()
    team(store, S1)
    declare(store, "t1", by=LEAD_1)
    store.apply(TaskClaimRequested(DEV_1, request_id="k", task_id="t1"))
    store.apply(TaskCompleted(DEV_1, "t1", at=2.0, result="withdrawn by its declarer: trust me"))

    (row,) = board_tasks(store.snapshot(), S1)

    assert (row.withdrawn, ended_label(row), row.owner) == (False, "", "builder")
    assert [r.id for r in unrun_ledger(store.snapshot(), S1)] == ["t1"]


# -- a wait on an id another session holds ---------------------------------------------


def test_a_row_waiting_on_another_sessions_id_says_it_cannot_end_and_what_ends_it() -> None:
    """
    Two missing dependencies that are different mistakes. ``typo`` was never
    declared and can be. ``shared`` is another session's: the id is taken
    fleet-wide, so declaring it here is refused and the wait has no end an agent
    can reach -- the row names it, says so, and says who can end it.
    """
    from pptmstr.board import foreign_wait_label

    store = Store()
    team(store, S1)
    team(store, S2)
    declare(store, "shared", by=LEAD_2, at=0.0)
    declare(store, "mine", by=LEAD_1, deps=("typo", "shared"), at=1.0)
    declare(store, "plain", by=LEAD_1, deps=("typo",), at=2.0)

    mine, plain = board_tasks(store.snapshot(), S1)

    assert (mine.missing, mine.held_elsewhere) == (("typo", "shared"), ("shared",))
    assert (plain.missing, plain.held_elsewhere) == (("typo",), ())
    assert foreign_wait_label(plain) == ""
    assert foreign_wait_label(mine) == (
        "waits on shared, which another session in this window holds -- it cannot be "
        "declared here; ask the operator to close this task and declare it again, under "
        "a new id, without that dependency"
    )
    assert board_tasks(store.snapshot(), S2)[0].held_elsewhere == ()


def _root_release_lands(snap: Snapshot, task_id: str) -> bool:
    """Whether the store takes ``S1``'s root releasing ``task_id`` as it stands."""
    from pptmstr.intents import TaskReleased
    from pptmstr.store import _apply

    after, _ = _apply(snap, TaskReleased(LEAD_1, task_id), _CLOCK.monotonic + 50.0)
    return after.tasks[task_id].state is TaskState.PENDING


def _holders_of_every_kind() -> dict[str, tuple[Snapshot, str]]:
    """One held or once-held row per kind of holder, each with its snapshot."""
    old = _old_claim(OLD_BUILDER_2).snapshot()

    by_address = _old_claim(()).snapshot()

    finished = _old_claim(OLD_BUILDER_2)
    finished.apply(AgentFinished(NEW_BUILDER_2, AgentState.DONE, ended_at=300.0))

    removed = _old_claim(OLD_BUILDER_2)
    removed.apply(AgentRemoved(NEW_BUILDER_2))

    woken = _old_claim(OLD_BUILDER_2)
    spawn(woken, OLD_BUILDER_2, agent_type="builder")
    woken.apply(AgentFinished(OLD_BUILDER_2, AgentState.DONE, ended_at=300.0))

    done = _resumed(
        _declared("t1", 50.0),
        boardlog.Claimed(at=_CLOCK.wall - 40.0, task_id="t1", by="builder-2", by_id=OLD_BUILDER_2),
        boardlog.Completed(
            at=_CLOCK.wall - 30.0, task_id="t1", by="builder-2", by_id=OLD_BUILDER_2, result="r"
        ),
    ).snapshot()

    again = _started_again().snapshot()

    return {
        "of an earlier run in this window": (again, "t1"),
        "started after the session was started again, and finished its turn": (again, "t2"),
        "from before the restart": (old, "t1"),
        "from before the restart, logged by address only": (by_address, "t1"),
        "running in this process": (old, "t2"),
        "finished its turn in this process": (finished.snapshot(), "t2"),
        "its record taken away, and no log named it": (removed.snapshot(), "t2"),
        "from before the restart, woken, finished again": (woken.snapshot(), "t1"),
        "from before the restart, and the task is finished": (done, "t1"),
    }


@pytest.mark.parametrize("kind", sorted(_holders_of_every_kind()))
def test_a_row_says_its_holder_was_left_behind_exactly_where_the_lead_may_release_it(
    kind: str,
) -> None:
    """
    The row's boolean is a second spelling of ``store._may_reclaim``, which the
    bus turns into an instruction. So the release is made: where the row says
    yes the store takes it, and where the row says no the store refuses.
    """
    snap, task_id = _holders_of_every_kind()[kind]
    row = next(t for t in board_tasks(snap, S1) if t.id == task_id)

    assert row.holder_left_behind is _root_release_lands(snap, task_id)


def test_the_holders_the_lead_may_release_are_the_ones_from_before_the_restart() -> None:
    """The test above holds for a boolean that is always False. This one names them."""
    left = {
        kind: next(t for t in board_tasks(snap, S1) if t.id == task_id).holder_left_behind
        for kind, (snap, task_id) in _holders_of_every_kind().items()
    }

    assert {kind for kind, said in left.items() if said} == {
        "of an earlier run in this window",
        "from before the restart",
        "from before the restart, logged by address only",
    }


def test_a_holder_another_sessions_restore_names_is_not_left_behind_on_this_board() -> None:
    """
    A task on ``S1``'s board held by a party of ``S2`` that ``S2``'s log names
    and nothing answers to. No claim and no restore builds it, so the snapshot
    is made by hand. The row declines to call that holder left behind -- the
    rule is about the task's own session's restore -- though the store, as it
    is written, takes the root's release of it.
    """
    old_foreign: NodeId = (S2, "agent-old")
    store = _resumed(_declared("t1", 50.0))
    store.apply(
        BoardRestored(
            S2,
            boardlog.restore(
                (
                    boardlog.Declared(
                        at=_CLOCK.wall - 50.0,
                        task_id="u1",
                        title="do u1",
                        declared_by="lead",
                        declared_by_id=(S2,),
                    ),
                    boardlog.Claimed(
                        at=_CLOCK.wall - 40.0, task_id="u1", by="builder", by_id=old_foreign
                    ),
                )
            ),
            _CLOCK,
        ),
        now=_CLOCK.monotonic,
    )
    snap = store.snapshot()
    assert snap.left_behind(old_foreign) == "builder"
    held = dataclasses.replace(snap.tasks["t1"], state=TaskState.CLAIMED, claimed_by=old_foreign)
    snap = dataclasses.replace(snap, tasks={**snap.tasks, "t1": held})

    (row,) = board_tasks(snap, S1)

    assert row.owner == f"builder, from an earlier run, {FOREIGN}"
    assert row.owner_gone and not row.holder_left_behind
    assert _root_release_lands(snap, "t1")


def _mail(store: Store, concern_id: str, to: NodeId) -> None:
    store.apply(
        ConcernPosted(
            LEAD_1,
            Concern(
                id=concern_id, sender=LEAD_1, recipient=to, subject="s", body="b", posted_at=1.0
            ),
        )
    )


def _restored_mail() -> Store:
    """A concern the old lead posted to the old builder-2, never read."""
    return _resumed(
        boardlog.Posted(
            at=_CLOCK.wall - 20.0,
            concern_id="c-old",
            sender="lead",
            recipient="builder-2",
            subject="s",
            body="b",
            sender_id=(S1,),
            recipient_id=OLD_BUILDER_2,
        )
    )


def test_a_concern_waiting_for_an_agent_that_is_not_running_says_so() -> None:
    """
    An inbox is read by the node it belongs to, and nothing in this process is
    that node. The row would otherwise read as ordinary waiting mail, to a name a
    live agent now answers to.
    """
    store = _restored_mail()

    (row,) = board_concerns(store.snapshot(), S1)

    assert row.state is ConcernState.POSTED and row.undeliverable
    assert row.recipient == "builder-2, from an earlier run"
    assert UNDELIVERABLE == "posted to an agent that is not running"
    assert store.snapshot().inbox_of(NEW_BUILDER_2) == ()


def test_it_is_deliverable_again_when_that_agent_is_woken_and_is_handed_over() -> None:
    store = _restored_mail()
    spawn(store, OLD_BUILDER_2, agent_type="builder")

    (waiting,) = board_concerns(store.snapshot(), S1)
    assert not waiting.undeliverable
    store.apply(InboxRead(OLD_BUILDER_2, "rq", 200.0))

    (read,) = board_concerns(store.snapshot(), S1)
    assert read.state is ConcernState.DELIVERED and not read.undeliverable


def test_only_waiting_mail_to_a_party_a_log_named_is_called_undeliverable() -> None:
    """
    A concern to a node that has not spawned yet is an ordinary ordering and is
    delivered when it does; one that was withdrawn is not waiting for anybody.
    """
    store = _restored_mail()
    _mail(store, "c-early", (S1, "not-started-yet"))
    _mail(store, "c-live", NEW_BUILDER)
    store.apply(ConcernWithdrawn("c-old"))

    rows = {row.id: row for row in board_concerns(store.snapshot(), S1)}

    assert [rows[cid].undeliverable for cid in ("c-old", "c-early", "c-live")] == [
        False,
        False,
        False,
    ]
    assert rows["c-old"].state is ConcernState.WITHDRAWN


# -- a closed card and a removed session have no board ------------------------------


def test_a_recalled_team_session_has_no_board_until_it_is_resumed() -> None:
    """
    The record carries the team template it last ran under, and "a team has a
    board from its first frame, empty or not" would draw an empty board over a
    log nobody read. A resume announces the root again, and then it has one.
    """
    from tests.test_ui_text import CLOSED, CLOSED_ROOT, recall

    store = Store()
    recall(store, template="feature")
    assert store.snapshot().nodes[CLOSED_ROOT].template == "feature"
    assert not has_board(store.snapshot(), CLOSED)

    team_session(store, CLOSED, "feature")
    assert store.snapshot().nodes[CLOSED_ROOT].recalled is None
    assert has_board(store.snapshot(), CLOSED)


def test_a_team_session_taken_off_the_fleet_has_no_board() -> None:
    from pptmstr.intents import AgentFinished, SessionDetached
    from pptmstr.model import AgentState

    store = Store()
    team_session(store, S1, "feature")
    store.apply(AgentFinished((S1, None), AgentState.DONE, ended_at=2.0), now=2.0)
    # Ended and still on the fleet: a team, with its board.
    assert has_board(store.snapshot(), S1)

    store.apply(SessionDetached(S1), now=3.0)

    assert store.snapshot().off_fleet(S1)
    assert not has_board(store.snapshot(), S1)


# -- a gate on every writer --------------------------------------------------------------


def test_a_gate_row_lists_its_writers_now_and_a_finished_one_only_what_was_declared() -> None:
    """
    The writers are on the row twice: in ``blocked_on``, which decides, and in
    ``writers``, which says which of those came from the word rather than from
    ``depends_on``. Neither is missing, and none is another session's.
    """
    from pptmstr.board import all_writers_label
    from pptmstr.model import ALL_WRITERS

    store = Store()
    team(store, S1)
    declare(store, "gate", by=LEAD_1, deps=(ALL_WRITERS,))
    declare(store, "w1", by=LEAD_1, at=1.0, touches=("a.py",))
    declare(store, "away", by=LEAD_2, at=2.0, touches=("a.py",))
    waiting, _ = board_tasks(store.snapshot(), S1)

    store.apply(TaskClaimRequested(DEV_1, request_id="k1", task_id="w1"))
    store.apply(TaskCompleted(DEV_1, "w1", at=3.0))
    store.apply(TaskClaimRequested(DEV_1, request_id="k2", task_id="gate"))
    store.apply(TaskCompleted(DEV_1, "gate", at=4.0))
    declare(store, "w2", by=LEAD_1, at=5.0, touches=("b.py",))
    done, _, _ = board_tasks(store.snapshot(), S1)

    assert (waiting.after_all_writers, waiting.writers, waiting.blocked_on) == (
        True,
        ("w1",),
        ("w1",),
    )
    assert (waiting.depends_on, waiting.missing) == ((), ())
    assert all_writers_label(waiting) == (
        "waits on every writer on this board, declared before or after it (now: w1)"
    )
    assert (done.writers, done.blocked_on) == ((), ())
    assert all_writers_label(done) == "declared to wait on every writer on this board"
    assert all_writers_label(board_tasks(store.snapshot(), S1)[1]) == ""
