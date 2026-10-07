"""
The gate dial: what the operator picks, whether it arrives, and what is shown back.

Three separable failures, and only the first is about the widget. The dial can set
a field nothing reads; the field can reach the session and be displayed from the
draft instead of the session, which goes stale the moment the session's policy
moves; and the operator can be left with no way to move it. There is a test here
for each.

Which preset is the relaxed one is deliberately not spelled: these are tests of the
dial, not of what a rung admits, so renaming the preset must not reach this file.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from pptmstr.app import AppState, _launch, _policy_of, _relaxed_count, _revoke_policy
from pptmstr.approval import Policy, inherits_to_subagents, requires_containment
from pptmstr.bridge import Bridge
from pptmstr.driver import AgentSession
from pptmstr.model import AgentRecord, AgentState, LaunchSpec, NodeId
from pptmstr.pool import SessionPool
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.theme import P
from pptmstr.ui.health import gate_line
from pptmstr.ui.launcher import POLICIES, LauncherState
from pptmstr.ui.widgets import _GATE_PROBES, gate_adds, gate_parks

TIMEOUT = 5.0

# Any rung that is not the default. `Policy` is required to have one for the dial to
# mean anything, and which one it is is not this file's business.
RELAXED = next(p for p in Policy if p is not Policy.STRICT)


@pytest.fixture()
def bridge() -> Iterator[Bridge]:
    b = Bridge()
    b.start()
    try:
        yield b
    finally:
        b.stop()


def _app(bridge: Bridge, *, cap: int = 0) -> AppState:
    """
    Enough application to run a launch through.

    ``cap=0`` is what keeps this test free of a CLI subprocess: the pool registers
    and announces a session over cap but does not start it, so everything the launch
    path does to a session is observable and nothing connects.
    """
    state = AppState(store=Store(), bridge=bridge, settings=Settings())
    state.pool = SessionPool(bridge, cap=cap)
    return state


def _launched(state: AppState, spec: LaunchSpec) -> AgentSession:
    """Run a launch and wait for the session it queues to appear."""
    _launch(state, spec)
    pool = state.pool
    assert pool is not None
    deadline = time.monotonic() + TIMEOUT
    while not pool.sessions and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pool.sessions, "the launch never reached the pool"
    return next(iter(pool.sessions.values()))


# -- the draft ---------------------------------------------------------------------


def test_a_launch_nobody_touched_the_dial_on_is_strict() -> None:
    """
    Off by default. The dial widens the gate, so it is asked for rather than
    arrived at, and a draft that was never opened produces the spec it produced
    before the dial existed.
    """
    assert LauncherState(task="x").spec().policy is Policy.STRICT


def test_the_default_does_not_depend_on_the_order_of_the_enum() -> None:
    """
    The draft holds the policy value, not an index into ``POLICIES``. Were it an
    index, declaring a rung above ``STRICT`` in approval.py would silently move
    every launch onto it -- a widening of the gate with no edit to this file and
    nothing on screen to say so.
    """
    assert LauncherState().policy is Policy.STRICT
    assert POLICIES.index(LauncherState().policy) == list(Policy).index(Policy.STRICT)


def test_the_rung_the_operator_picked_reaches_the_spec() -> None:
    assert LauncherState(task="x", policy=RELAXED).spec().policy is RELAXED


def test_the_dial_offers_every_rung_the_gate_defines() -> None:
    """
    Read off the enum rather than listed. A second list is a list that disagrees,
    and the disagreement would be a preset the gate honours and the launcher cannot
    reach -- or worse, a name the launcher shows that no longer classifies anything.
    """
    assert POLICIES == tuple(Policy)


# -- what a fork carries -----------------------------------------------------------


def test_a_fork_does_not_inherit_a_widened_gate() -> None:
    """
    ``ui/health.py`` forks and ``ui/inbox.py`` relaunches straight from a record, on
    one click and with no modal in between. A policy that travelled that path would
    let one deliberate choice widen the gate for every later session descended from
    it, from a button pressed for an unrelated reason.
    """
    record = AgentRecord(
        node_id=("sess-relaxed", None),
        parent=None,
        depth=0,
        state=AgentState.THINKING,
        topic="orienting",
        task="audit the parser",
        model="claude-opus-5",
        cwd="/srv/repo",
    )
    assert LaunchSpec.from_record(record).policy is Policy.STRICT


# -- the wiring --------------------------------------------------------------------


def test_the_spec_reaches_the_session_and_not_only_the_launcher(bridge: Bridge) -> None:
    """
    The defect this whole task is exposed to: a dial that sets a field no session
    ever reads. ``AgentSession`` takes ``policy=`` and defaults it to ``STRICT``, so
    a launch path that forgot to pass it would leave every test of the widget and
    every test of the gate passing while the feature did nothing.
    """
    state = _app(bridge)
    session = _launched(state, LaunchSpec(task="orient", model="claude-opus-5", policy=RELAXED))
    assert session.policy is RELAXED


def test_a_launch_that_asked_for_nothing_starts_strict(bridge: Bridge) -> None:
    state = _app(bridge)
    session = _launched(state, LaunchSpec(task="orient", model="claude-opus-5"))
    assert session.policy is Policy.STRICT


# -- the display -------------------------------------------------------------------


def test_the_display_reads_the_session_rather_than_the_draft(bridge: Bridge) -> None:
    """
    The correctness case for sourcing the display from the session.

    The policy narrows at runtime, and the spec is frozen at launch. A pane that
    rendered the spec would keep naming the rung the operator asked for at launch
    for a session that has left it -- worse than showing nothing, because it is
    acted on.
    """
    state = _app(bridge)
    spec = LaunchSpec(task="orient", model="claude-opus-5", policy=RELAXED)
    session = _launched(state, spec)
    node = session.node_id

    assert _policy_of(state, node) is RELAXED
    session.revoke_policy()
    assert _policy_of(state, node) is Policy.STRICT
    # The draft is unmoved, which is exactly why it is the wrong source.
    assert spec.policy is RELAXED


def test_a_sub_agents_node_reports_its_sessions_gate(bridge: Bridge) -> None:
    """
    Health for a sub-agent is its session's. The lookup folds to the root, so a
    cursor resting on a worker shows the gate that is actually classifying its
    calls rather than nothing at all.
    """
    state = _app(bridge)
    session = _launched(state, LaunchSpec(task="orient", model="claude-opus-5", policy=RELAXED))
    sub: NodeId = (session.session_id, "worker-1")
    assert _policy_of(state, sub) is RELAXED


def test_a_node_no_session_holds_reports_nothing(bridge: Bridge) -> None:
    """
    None rather than ``STRICT``. A closed session is not a session running under a
    strict gate, and saying so would put a live-sounding reading next to a record
    that is finished.
    """
    state = _app(bridge)
    assert _policy_of(state, ("no-such-session", None)) is None


def test_the_gate_line_is_silent_when_there_is_no_session() -> None:
    assert gate_line(None) is None


def test_a_widened_gate_is_not_drawn_in_the_colour_of_an_ordinary_fact() -> None:
    """
    The whole of D3's requirement in one assertion. Both rungs are stated -- a
    variable nobody displays is one the UI can be silently wrong about -- but they
    must not read alike, or the operator scanning the pane learns nothing from the
    line being there.
    """
    strict = gate_line(Policy.STRICT)
    relaxed = gate_line(RELAXED)
    assert strict is not None and relaxed is not None
    assert strict[1] is P.text_dim
    assert relaxed[1] is P.warn
    assert strict[0] != relaxed[0]


def test_the_gate_line_names_the_rung_it_was_given() -> None:
    """
    From the enum member, so the rename that is coming for the preset reaches the
    screen without an edit here.
    """
    line = gate_line(RELAXED)
    assert line is not None
    assert RELAXED.value in line[0]


# -- what the display claims about a rung ------------------------------------------


def test_the_strict_rung_adds_nothing() -> None:
    assert gate_adds(Policy.STRICT) == ()


def test_every_probe_parks_under_the_default_rung() -> None:
    """
    What makes ``gate_adds`` mean "widening" without subtracting anything.

    Every probe must park under ``STRICT``, so an auto-approval under any other
    rung is a widening by definition. A probe for something ``STRICT`` already
    admits -- ``Read``, say -- would be reported as added by every rung, and the
    display would credit ``PERMISSIVE`` with a permission the default already has.
    """
    assert gate_parks(Policy.STRICT) == tuple(label for label, _tool, _arguments in _GATE_PROBES)


def test_every_other_rung_adds_something_the_operator_can_be_told() -> None:
    """
    A rung that admits nothing ``STRICT`` does not is a rung with no effect, and
    the dial would be offering the operator a choice that does nothing. It would
    also draw a blank line where the widening is supposed to be described.
    """
    for policy in Policy:
        if policy is not Policy.STRICT:
            assert gate_adds(policy), f"{policy.name} widens nothing"


def test_a_rung_that_releases_writes_pays_for_it_with_containment() -> None:
    """
    The ladder's cap, asserted where the operator reads it.

    This used to read "no rung admits writes or spawns", which was the right rule
    for a ladder whose top rung was ``PERMISSIVE``. ``AUTONOMOUS`` breaks it on
    purpose: it releases the whole of ``_REVIEW`` and the allowlist stops being
    what bounds the session. Deleting the assertion at that point would have
    removed the guard rather than the mistake, so what is checked now is the trade
    rather than the prohibition -- a rung may stop parking writes, but only by
    requiring the containment that bounds them, which
    ``AgentSession._uncontained_autonomy`` then refuses to start without.

    Both halves are read off ``approval`` rather than listed here, so a fourth rung
    that released writes while answering False to ``requires_containment`` fails
    here, before it could put a reassuring line on the launcher.
    """
    for policy in Policy:
        parks_writes = "writes" in gate_parks(policy)
        parks_spawns = "spawns" in gate_parks(policy)
        if requires_containment(policy):
            continue
        # An orchestrator that gates writes but not the spawning of things that
        # write has a hole in it, so the two travel together at every uncontained
        # rung rather than being checked one at a time.
        assert parks_writes, f"{policy.name} releases writes with no containment to bound them"
        assert parks_spawns, f"{policy.name} releases spawns with no containment to bound them"


def test_the_only_rung_excused_from_that_is_the_one_that_cannot_start_uncontained() -> None:
    """
    The other half of the trade, so the excuse above cannot be handed out freely.

    ``requires_containment`` is what the driver's start-time refusal is keyed on, so
    a rung claiming the exemption is a rung that cannot launch without a sandbox.
    Asserting the set is exactly ``{AUTONOMOUS}`` keeps that a deliberate, reviewed
    list rather than something a future rung can join by returning True.
    """
    excused = {p for p in Policy if requires_containment(p)}
    assert excused == {Policy.AUTONOMOUS}
    # And the exemption is only worth having where it is used: the rung really does
    # stop parking the two, which is what made the narrowing necessary.
    assert "writes" not in gate_parks(Policy.AUTONOMOUS)
    assert "spawns" not in gate_parks(Policy.AUTONOMOUS)


def test_a_rung_that_inherits_bounds_its_fleet_by_approval_or_by_containment() -> None:
    """
    The trade behind 2026-09-24's reversal of 2026-08-11 §4, as a rule rather than
    as two hand-checked rungs.

    §4's objection to inheritance was that one approval relaxes the gate for an
    unbounded number of downstream calls. Two different things answer it, and a
    rung needs one of them: ``PERMISSIVE`` parks spawns, so N agents cost N
    approvals and the operator bounds the fleet; ``AUTONOMOUS`` releases spawns but
    cannot start without containment, so each agent's reach is bounded instead of
    its count. A rung that did neither would be §4's objection with nothing left
    answering it.

    Read off ``approval`` rather than listed here, so a fourth rung has to pick one.
    """
    for policy in Policy:
        if not inherits_to_subagents(policy):
            continue
        bounded = "spawns" in gate_parks(policy) or requires_containment(policy)
        assert (
            bounded
        ), f"{policy.name} inherits with neither an approval nor a sandbox bounding fan-out"


def test_what_a_rung_adds_and_what_it_parks_do_not_overlap() -> None:
    """
    Both lines are shown together, so a label appearing in both would have the
    display contradicting itself in two adjacent rows.
    """
    for policy in Policy:
        assert not set(gate_adds(policy)) & set(gate_parks(policy))


# -- the ambient count -------------------------------------------------------------


def test_the_status_bar_counts_only_the_sessions_running_widened(bridge: Bridge) -> None:
    state = _app(bridge)
    assert _relaxed_count(state) == 0
    _launched(state, LaunchSpec(task="a", model="claude-opus-5"))
    assert _relaxed_count(state) == 0
    _launch(state, LaunchSpec(task="b", model="claude-opus-5", policy=RELAXED))
    pool = state.pool
    assert pool is not None
    deadline = time.monotonic() + TIMEOUT
    while len(pool.sessions) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _relaxed_count(state) == 1


def test_the_count_drops_when_a_session_is_narrowed(bridge: Bridge) -> None:
    state = _app(bridge)
    session = _launched(state, LaunchSpec(task="a", model="claude-opus-5", policy=RELAXED))
    assert _relaxed_count(state) == 1
    session.revoke_policy()
    assert _relaxed_count(state) == 0


# -- the operator's own narrowing ---------------------------------------------------


def test_the_operator_can_narrow_a_session_that_is_not_converging(bridge: Bridge) -> None:
    """
    The operator's own narrowing, which is the case no automatic rule can serve:
    a session that is wandering rather than converging never reaches the call that
    would narrow it by itself. This is the pane's button.
    """
    state = _app(bridge)
    session = _launched(state, LaunchSpec(task="orient", model="claude-opus-5", policy=RELAXED))
    _revoke_policy(state, session.node_id)
    assert session.policy is Policy.STRICT


def test_narrowing_from_a_sub_agents_node_narrows_the_session(bridge: Bridge) -> None:
    """
    The cursor can rest on a worker, and the policy belongs to the session. Acting
    from there must narrow the session rather than silently doing nothing -- the
    operator pressed the only button on offer.
    """
    state = _app(bridge)
    session = _launched(state, LaunchSpec(task="orient", model="claude-opus-5", policy=RELAXED))
    _revoke_policy(state, (session.session_id, "worker-1"))
    assert session.policy is Policy.STRICT


def test_revoking_an_unknown_node_is_a_no_op(bridge: Bridge) -> None:
    state = _app(bridge)
    _revoke_policy(state, ("no-such-session", None))
    _revoke_policy(state, None)
