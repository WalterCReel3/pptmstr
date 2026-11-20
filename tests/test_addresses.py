"""
What a node is called: the address its own session routes to it, and the name a
board gives it.

Driven through a real ``Store`` (and, where the driver's allocator is the other
half of the claim, a real ``AgentSession``), because the property is that two
derivations of one name agree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from claude_agent_sdk import HookContext, SubagentStartHookInput

from pptmstr import boardlog
from pptmstr.addresses import FOREIGN, UNKNOWN, role_name
from pptmstr.model import Snapshot
from pptmstr.naming import LEAD
from pptmstr.store import Store
from tests.test_board import (
    _CLOCK,
    DEV_1,
    DEV_2,
    LEAD_1,
    LEAD_2,
    OLD_BUILDER_2,
    QA_1,
    S1,
    S2,
    _declared,
    _old_claim,
    _resumed,
    spawn,
    team,
)

if TYPE_CHECKING:
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

# ``OLD_BUILDER_2`` as a log's ``by_id`` holds it: the same tuple, typed as the
# record's field. ``NodeId`` allows a None agent id, and this node has one.
OLD_BUILDER_2_ID = cast(tuple[str, ...], OLD_BUILDER_2)

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
    lead = store.snapshot().get(LEAD_1)
    assert lead is not None
    assert lead.agent_type is None


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


def test_the_board_steps_over_a_declared_name_the_way_the_bus_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def _run_spawns(session: AgentSession, bridge: Bridge, *spawns: tuple[str, str]) -> Snapshot:
    """Fire SubagentStart for each (agent_id, agent_type) and store what it emitted."""
    import asyncio

    async def start_them() -> None:
        for agent_id, agent_type in spawns:
            await session._subagent_start(
                _start_hook(agent_id, agent_type), None, HookContext(signal=None)
            )

    asyncio.run(start_them())
    store = Store()
    for intent in bridge.drain():
        store.apply(intent)
    return store.snapshot()


def _assert_agrees(snap: Snapshot, session: AgentSession, expected: dict[str, str]) -> None:
    for agent_id, address in expected.items():
        node = (session.session_id, agent_id)
        assert session.role_of(node) == address
        assert role_name(snap, node, session.session_id) == address


def _start_hook(agent_id: str, agent_type: str) -> SubagentStartHookInput:
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


# -- a party from before the restart ------------------------------------------------


def test_a_node_nothing_restored_and_nothing_spawned_is_still_only_unknown() -> None:
    """The qualifier is for a party a log named. It asserts a cause, so it needs one."""
    store = _old_claim(OLD_BUILDER_2_ID)

    assert role_name(store.snapshot(), (S1, "never-heard-of"), S1) == UNKNOWN


def test_a_restored_party_the_log_had_no_name_for_is_unknown_from_before_the_restart() -> None:
    store = _resumed(
        _declared("t1", 50.0),
        boardlog.Claimed(at=_CLOCK.wall - 40.0, task_id="t1", by="", by_id=OLD_BUILDER_2_ID),
    )

    assert role_name(store.snapshot(), OLD_BUILDER_2, S1) == f"{UNKNOWN}, from an earlier run"


def test_a_restored_party_seen_from_another_sessions_board_carries_both_qualifiers() -> None:
    store = _old_claim(OLD_BUILDER_2_ID)

    assert role_name(store.snapshot(), OLD_BUILDER_2, S2) == (
        f"builder-2, from an earlier run, {FOREIGN}"
    )
