"""
The last hop of a launch: what the operator chose at the dial reaches the session.

``ui/launcher`` builds a ``LaunchSpec`` carrying ``containment`` and ``policy``, and
``model``/``driver`` both have somewhere to put them. Between those two facts sits
``app._launch``, which is the only code that turns one into the other -- and a field
it forgets is a field nothing complains about. The session starts; it just starts
uncontained, under the strict gate, while the modal that asked for the mode is gone.

The refusal here is the other half. ``planning/2026-09-03-a-dangerously-autonomous-mode.md``
§8c measured that the CLI accepts an unrecognised settings key *silently*, so a CLI
too old for the sandbox keys does not fail the launch -- it runs it with the keys
ignored. Reading the version and refusing below the floor is the only thing between a
version shortfall and a session the operator believes is contained.
"""

from __future__ import annotations

import threading

import pytest

from pptmstr import cli_version
from pptmstr.approval import Policy
from pptmstr.bridge import Bridge
from pptmstr.cli_version import BelowFloor, FloorCheck, MeetsFloor, Unreadable
from pptmstr.driver import AgentSession
from pptmstr.model import LaunchSpec
from pptmstr.settings import Settings
from pptmstr.store import Store

# Stands in for what `sandbox.containment_settings()` returns. Its content does not
# matter to `_launch` -- it is opaque text that must arrive unchanged -- and a literal
# keeps these tests from depending on the sandbox module's current spelling.
CONTAINMENT = '{"sandbox": {"network": {"strictAllowlist": true}}}'

_MEETS = MeetsFloor("2.1.251", (2, 1, 251), cli_version.SANDBOX_FLOOR)
_BELOW = BelowFloor("2.1.9", (2, 1, 9), cli_version.SANDBOX_FLOOR)
_UNREADABLE = Unreadable("claude --version exited 127", cli_version.SANDBOX_FLOOR)


class _Outcome:
    """
    What one run of ``_launch`` did: what reached the pool, and what was measured.
    """

    def __init__(self) -> None:
        self.sessions: list[AgentSession] = []
        # One entry per call to the version check: the path it was asked about and
        # the thread it ran on. Empty means it was never consulted.
        self.checked_paths: list[str | None] = []
        self.checked_on: list[threading.Thread] = []
        self.pool_thread: threading.Thread | None = None


def _run_launch(
    spec: LaunchSpec,
    monkeypatch: pytest.MonkeyPatch,
    *,
    check: FloorCheck | None = None,
    settings: Settings | None = None,
) -> _Outcome:
    """
    Drive ``_launch`` against a pool that only records, with the version read faked.

    ``check`` is what the stand-in returns. ``None`` means the run is not expected to
    consult it at all, and the stand-in raises if it is called -- which is what makes
    "an uncontained launch does not pay for the subprocess" a testable claim rather
    than an assertion about code nobody ran.

    ``bridge.stop`` is the synchronisation: it drains outstanding loop tasks before
    it tears the loop down, so ``go`` has run to completion by the time this returns
    and an empty ``sessions`` means refused rather than not-yet.
    """
    from pptmstr.app import AppState, _launch

    out = _Outcome()

    def fake_check(path: str | None = None, **kwargs: object) -> FloorCheck:
        out.checked_paths.append(path)
        out.checked_on.append(threading.current_thread())
        if check is None:
            raise AssertionError("the CLI version was read for a launch that asked for no mode")
        return check

    monkeypatch.setattr(cli_version, "check_installed_cli", fake_check)

    class _RecordingPool:
        def submit(self, session: AgentSession) -> None:
            out.pool_thread = threading.current_thread()
            out.sessions.append(session)

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=settings or Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(state, spec)
    finally:
        bridge.stop()
    return out


def _spec(**kw: object) -> LaunchSpec:
    return LaunchSpec(task="do a thing", model="claude-sonnet-5", cwd="/tmp", **kw)  # type: ignore[arg-type]


# -- the mode reaches the session --------------------------------------------------


def test_the_mode_chosen_at_the_dial_reaches_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Both halves, in one assertion each, because they are only useful together.

    ``LauncherState.spec`` sets containment and policy as a pair -- the policy
    releases ``Bash`` from the gate and the containment is the only thing bounding
    what a released ``Bash`` reaches. A ``_launch`` that carried one and dropped the
    other would start either an unattended agent with the whole machine or a sandbox
    around a gate that is still asking, and neither reads as wrong from any surface.
    """
    out = _run_launch(
        _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS), monkeypatch, check=_MEETS
    )

    assert [s.containment for s in out.sessions] == [CONTAINMENT]
    assert [s.policy for s in out.sessions] == [Policy.AUTONOMOUS]


def test_a_spec_that_asked_for_nothing_starts_the_session_it_always_did(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The defaults are the whole of the old behaviour, so they are worth pinning
    separately: a pass-through that inverted a condition would show up here and
    nowhere else.
    """
    out = _run_launch(_spec(), monkeypatch)

    assert [s.containment for s in out.sessions] == [None]
    assert [s.policy for s in out.sessions] == [Policy.STRICT]


# -- the per-launch sub-agent cap --------------------------------------------------


def test_a_launch_that_named_no_cap_gets_the_operators_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The field's default is the whole of the old behaviour: every spec built before it
    existed, and every one ``from_record`` builds today, carries ``None``.
    """
    out = _run_launch(_spec(), monkeypatch, settings=Settings(subagent_cap=6))

    assert [s.subagent_cap for s in out.sessions] == [6]


def test_a_launch_that_named_a_cap_overrides_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The operator asked for an override at launch, and this is the only hop that can
    grant one -- ``AgentSession.subagent_cap`` is set once, at construction.
    """
    out = _run_launch(_spec(subagent_cap=2), monkeypatch, settings=Settings(subagent_cap=6))

    assert [s.subagent_cap for s in out.sessions] == [2]


def test_a_launch_capped_at_zero_may_not_spawn_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The value the obvious implementation eats.

    ``spec.subagent_cap or settings.subagent_cap`` reads ``0`` as "unset" and hands
    back the setting, which turns a session the operator said may not spawn into one
    that may spawn six times -- under a policy where every spawn is auto-approved and
    nobody is asked. It is the one substitution in this file that widens a bound
    rather than narrowing it, and it typechecks.
    """
    out = _run_launch(_spec(subagent_cap=0), monkeypatch, settings=Settings(subagent_cap=6))

    assert [s.subagent_cap for s in out.sessions] == [0]


def test_the_modal_is_drawn_against_the_setting_it_offers_to_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The number on screen is the one in ``Settings``, and nothing else can supply it.

    ``launcher._cap_line`` refuses to substitute ``DEFAULT_SUBAGENT_CAP`` for an
    unknown setting -- deliberately, because a stand-in is wrong for exactly the
    operator who changed it -- so the section renders without a figure and the
    override box does not render at all unless this argument arrives. The unit tests
    on ``draw`` pass the value in themselves and would keep passing with the call site
    handing it nothing, which is STYLE.md §2's watchdog-nothing-calls shape.
    """
    from pptmstr import app
    from pptmstr.ui import launcher

    seen: list[object] = []
    monkeypatch.setattr(
        launcher, "draw", lambda state, **kw: seen.append(kw.get("subagent_cap", "ABSENT"))
    )
    monkeypatch.setattr(launcher, "handle_shortcut", lambda state: None)

    class _IdlePool:
        running_count = 0
        queued_count = 0
        cap = 4

    bridge = Bridge()
    bridge.start()
    try:
        state = app.AppState(store=Store(), bridge=bridge, settings=Settings(subagent_cap=6))
        state.pool = _IdlePool()  # type: ignore[assignment]
        app._draw_overlays(state)
    finally:
        bridge.stop()

    assert seen == [6]


# -- the floor refusal -------------------------------------------------------------


def test_an_uncontained_launch_never_reads_the_cli_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The ordinary launch is the common one and must neither pay for a subprocess nor
    be refusable by one. ``check`` is left None here, so the stand-in raises if the
    guard is ever widened to run unconditionally -- the exception is swallowed by the
    loop task, but the session never reaches the pool and the run reads as refused.
    """
    out = _run_launch(_spec(), monkeypatch)

    assert out.checked_paths == []
    assert len(out.sessions) == 1


def test_a_cli_below_the_floor_refuses_a_contained_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    §8c: the key is accepted silently, so the old CLI runs the session with the
    containment ignored. Starting it anyway is the one outcome this cannot have.
    """
    out = _run_launch(
        _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS), monkeypatch, check=_BELOW
    )

    assert out.checked_paths == [None]
    assert out.sessions == []


def test_a_cli_whose_version_cannot_be_read_refuses_a_contained_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``Unreadable`` is a refusal and not a pass. ``cli_version`` keeps "could not
    tell" distinct from "checked and fine" precisely so this call can refuse on it;
    collapsing them here would throw away the module's whole design and reproduce the
    SDK's ``except Exception: pass``, which §8 cites as the behaviour not to copy.
    """
    out = _run_launch(
        _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS), monkeypatch, check=_UNREADABLE
    )

    assert out.checked_paths == [None]
    assert out.sessions == []


def test_the_refusal_says_which_of_the_two_failures_it_was() -> None:
    """
    A refusal that read the same for both would send an operator to upgrade a CLI
    that is fine, or to debug a PATH that is not the problem. The pure half is tested
    directly because that is the half that can be.
    """
    from pptmstr.app import _containment_refusal

    assert _containment_refusal(_MEETS) is None

    below = _containment_refusal(_BELOW)
    assert below is not None and "2.1.9" in below and cli_version.SANDBOX_FLOOR in below

    unreadable = _containment_refusal(_UNREADABLE)
    assert unreadable is not None and "exited 127" in unreadable
    assert "2.1.9" not in unreadable


# -- where the blocking read runs --------------------------------------------------


def test_the_version_read_runs_off_both_the_frame_loop_and_the_asyncio_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``check_installed_cli`` spawns ``claude --version`` and waits up to ten seconds.

    On the UI thread that is a visible stall -- I7, and the reason ``ui/launcher``
    scans sessions on a worker. On the asyncio loop it is worse in a quieter way: the
    loop hosts every live session, the gate's parked futures and the watchdogs that
    report on them, so ten seconds there is ten seconds in which no approval can be
    answered and nothing says why.

    The two threads named here are the ones it must not be. ``pool.submit`` runs
    inside the launch coroutine, so ``pool_thread`` *is* the loop thread -- which is
    what lets this compare against it without reaching into ``Bridge``.
    """
    out = _run_launch(
        _spec(containment=CONTAINMENT, policy=Policy.AUTONOMOUS), monkeypatch, check=_MEETS
    )

    assert len(out.checked_on) == 1
    assert out.pool_thread is not None
    assert out.checked_on[0] is not out.pool_thread
    assert out.checked_on[0] is not threading.current_thread()
