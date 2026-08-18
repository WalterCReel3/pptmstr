"""
The session board projection: scoping, role naming, and derived blocked-ness.

Driven through a real ``Store`` rather than by hand-building snapshots, because
two of the properties under test are properties of the whole path -- that a task
declared over the bus carries its declarer at all, and that blocked-ness answers
from the fleet-wide map rather than the filtered rows.
"""

from __future__ import annotations

from pptmstr.board import (
    FOREIGN,
    LEAD,
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
    ConcernPosted,
    ConcernWithdrawn,
    InboxRead,
    StateChanged,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
)
from pptmstr.model import AgentState, Concern, ConcernState, NodeId, Snapshot, Task, TaskState
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


def test_a_cross_session_dependency_does_not_invent_a_blocker() -> None:
    """
    The reason blocked-ness is derived against the whole map and not the filtered
    rows: `blocked_on` treats a dependency it cannot find as unsatisfied, so
    filtering first would show a task whose dependency is merely in another
    session as permanently stuck.
    """
    store = Store()
    team(store, S1)
    team(store, S2)
    declare(store, "shared", by=LEAD_2, at=0.0)
    declare(store, "mine", by=LEAD_1, deps=("shared",), at=1.0)

    store.apply(TaskClaimRequested(DEV_2, request_id="k1", task_id="shared"))
    store.apply(TaskCompleted(DEV_2, "shared", at=2.0))

    row = board_tasks(store.snapshot(), S1)[0]
    assert (row.id, row.blocked_on, row.missing) == ("mine", (), ())


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
