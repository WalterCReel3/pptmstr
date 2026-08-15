#!/usr/bin/env python3
"""
Measure what idling actually costs and saves (design §8 step 5, §4.2).

Four phases against one live window, so the comparison is within a single process
and a single GPU context rather than across runs:

  full-speed      idling forced off, no sessions     -- the baseline
  idle            idling on, no sessions             -- the cold-start screen
  idle+fleet      idling on, one AWAITING_APPROVAL   -- the ordinary resting state
  active          idling on, one agent THINKING      -- the predicate under load

The claim being tested is not "idling works" but "``any_active`` drives it". The
last two phases are the ones that would catch a predicate wired backwards, which
the first two cannot: an app that idles all the time looks great on a CPU graph and
is useless, and a predicate keyed on "are there agents" rather than "is an agent
working" would betray itself at ``idle+fleet`` and nowhere else.

``idle`` and ``idle+fleet`` are separated because they are no longer the same
program. With no sessions the NEEDS YOU pane fills with an animated splash, so the
first measures the cold-start screen and the second measures an application that is
actually resting. The splash also only exists in TRIAGE, which is why ``--layout``
is explicit rather than inherited -- see the flag's own comment.

**Treat the layout and the fleet as part of every figure this prints.** An idle
number quoted without them describes one of three different states.

Also measures **cross-thread wake latency** -- how long an intent emitted from the
asyncio thread waits before a frame reflects it. §4.2 predicts <=111ms at 9fps and
calls that acceptable; this reports the real number. That is the "an agent finished
while you were reading something else" case.

CPU comes from resource.getrusage, which is stdlib and counts every thread in this
process. It does not count the compositor, so treat the absolute numbers as
this-process-only; the ratio between phases is the point.

Usage:  .venv/bin/python scripts/bench_idle.py [--seconds 4] [--layout TRIAGE] [--no-wake]
"""

from __future__ import annotations

import argparse
import resource
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


@dataclass
class Phase:
    name: str
    force_idling: bool | None  # None = let the app's own predicate decide
    make_active: bool
    # Seed a session that is parked rather than working. Distinguishes "the app
    # is resting" from "the app is resting *and* showing the cold-start splash",
    # which are different programs since the splash draws only on an empty fleet.
    seed_parked: bool = False
    frames: int = 0
    cpu: float = 0.0
    wall: float = 0.0

    @property
    def fps(self) -> float:
        return self.frames / self.wall if self.wall else 0.0

    @property
    def cpu_percent(self) -> float:
        return 100.0 * self.cpu / self.wall if self.wall else 0.0


@dataclass
class Bench:
    seconds: float
    phases: list[Phase] = field(default_factory=list)
    index: int = 0
    started: float = 0.0
    start_cpu: float = 0.0
    wake_sent_at: float | None = None
    wake_latency_ms: list[float] = field(default_factory=list)
    done: bool = False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=4.0, help="per phase")
    ap.add_argument("--fps-idle", type=float, default=9.0)
    # Named rather than inherited, and that is now load-bearing. The first two
    # phases run with zero sessions, which is the state the NEEDS YOU splash fills,
    # and NEEDS YOU exists only in TRIAGE -- so what this measures depends on the
    # layout. Left to `remember_selected_alternative_layout`, consecutive runs would
    # measure two different programs and the difference would look like noise.
    ap.add_argument(
        "--layout",
        default="TRIAGE",
        choices=("TRIAGE", "FOCUS"),
        help="TRIAGE draws the empty-fleet splash; FOCUS has no NEEDS YOU pane",
    )
    # The wake probe perturbs the phase it measures: it emits an intent halfway
    # through the idle phase, and hello_imgui answers input with a full-speed
    # window, so the average fps for the phase lands somewhere between 9 and 60
    # depending on how long that window happens to last. That was already known and
    # written down (design 4.3, "one run out of four"), but it was a confound rather
    # than a control -- there was no way to measure the resting state without it.
    # This is that way. Latency and resting cost are separate claims and each is
    # cleanest measured without the other running.
    ap.add_argument(
        "--no-wake",
        action="store_true",
        help="skip the cross-thread wake probe, so the idle phase is undisturbed",
    )
    args = ap.parse_args()

    from imgui_bundle import hello_imgui, immapp

    from pptmstr import app as pptmstr_app
    from pptmstr.intents import AgentSpawned, StateChanged
    from pptmstr.model import AgentState

    bench = Bench(seconds=args.seconds)
    bench.phases = [
        Phase("full-speed", force_idling=False, make_active=False),
        Phase("idle", force_idling=None, make_active=False),
        Phase("idle+fleet", force_idling=None, make_active=False, seed_parked=True),
        Phase("active", force_idling=None, make_active=True),
    ]

    state_ref: dict[str, object] = {}
    real_appstate = pptmstr_app.AppState

    def capture_state(*a: object, **kw: object) -> object:
        st = real_appstate(*a, **kw)  # type: ignore[arg-type]
        state_ref["state"] = st
        return st

    node = ("bench-session", None)

    def advance(runner: object) -> None:
        """Called once per frame, after the app's own pre_new_frame."""
        st = state_ref.get("state")
        if st is None or bench.done:
            return
        phase = bench.phases[bench.index]

        if bench.started == 0.0:
            bench.started = time.perf_counter()
            bench.start_cpu = cpu_seconds()
            if phase.make_active or phase.seed_parked:
                # Drive the store the way the driver would, so the predicate is
                # exercised through its real input rather than poked directly.
                #
                # AWAITING_APPROVAL for the parked phase: not active, so idling
                # stays on, and not terminal either -- it is the state the app is
                # designed to rest in, an agent holding a decision for the operator.
                seeded = AgentState.THINKING if phase.make_active else AgentState.AWAITING_APPROVAL
                st.bridge.emit(  # type: ignore[attr-defined]
                    AgentSpawned(node, None, "bench", "m", time.monotonic())
                )
                st.bridge.emit(StateChanged(node, seeded))  # type: ignore[attr-defined]
            elif phase.name == "idle" and not args.no_wake:
                # Named rather than positional: the probe belongs to the empty-fleet
                # resting phase specifically, and adding a phase above must not
                # silently move it somewhere it would inject a second node.
                _schedule_wake(st, bench, node)

        phase.frames += 1
        elapsed = time.perf_counter() - bench.started

        # Measure the wake: the frame that first sees the injected topic.
        if bench.wake_sent_at is not None:
            snap = st.frame_snap  # type: ignore[attr-defined]
            if snap is not None and snap.get(node) is not None:
                bench.wake_latency_ms.append((time.perf_counter() - bench.wake_sent_at) * 1000.0)
                bench.wake_sent_at = None
                st.store.apply_all([])  # type: ignore[attr-defined]

        if elapsed >= bench.seconds:
            phase.wall = elapsed
            phase.cpu = cpu_seconds() - bench.start_cpu
            bench.index += 1
            bench.started = 0.0
            if bench.index >= len(bench.phases):
                bench.done = True
                hello_imgui.get_runner_params().app_shall_exit = True
                return
            # Clean the injected node so the next phase starts from a known state.
            from pptmstr.intents import AgentRemoved

            st.bridge.emit(AgentRemoved(node))  # type: ignore[attr-defined]

        # Phase override goes last: the app sets enable_idling from any_active in
        # its own pre_new_frame, so overriding earlier would be silently undone.
        if phase.force_idling is not None:
            runner.fps_idling.enable_idling = phase.force_idling  # type: ignore[attr-defined]

    real_run = immapp.run

    def patched_run(runner: object, *rest: object) -> object:
        user_pre = runner.callbacks.pre_new_frame  # type: ignore[attr-defined]

        def pre_new_frame() -> None:
            if user_pre:
                user_pre()
            advance(runner)

        runner.callbacks.pre_new_frame = pre_new_frame  # type: ignore[attr-defined]
        return real_run(runner, *rest)

    pptmstr_app.AppState = capture_state  # type: ignore[assignment,misc]
    immapp.run = patched_run  # type: ignore[assignment]
    try:
        pptmstr_app.main(["--fps-idle", str(args.fps_idle), "--layout", args.layout])
    finally:
        immapp.run = real_run  # type: ignore[assignment]
        pptmstr_app.AppState = real_appstate  # type: ignore[misc]

    probe = "no wake probe" if args.no_wake else "wake probe on"
    print(
        f"\nfps_idle = {args.fps_idle:g}, {args.seconds:g}s per phase, "
        f"layout {args.layout}, {probe}\n"
    )
    print(f"{'phase':<12} {'fps':>8} {'cpu %':>8} {'frames':>8}")
    print("-" * 40)
    for phase in bench.phases:
        print(f"{phase.name:<12} {phase.fps:>8.1f} {phase.cpu_percent:>8.1f} {phase.frames:>8}")

    full, idle, parked, active = bench.phases
    print()
    if idle.cpu_percent and full.cpu_percent:
        print(f"idle costs {idle.cpu_percent / full.cpu_percent:.1%} of full-speed CPU")
    if parked.cpu_percent and idle.cpu_percent:
        print(
            f"empty-fleet idle is {idle.cpu_percent / parked.cpu_percent:.2f}x "
            f"a parked fleet ({idle.cpu_percent:.1f}% vs {parked.cpu_percent:.1f}%) "
            "-- the difference is the splash"
        )
    print(f"active phase ran at {active.fps:.1f} fps vs {idle.fps:.1f} idle")
    if active.fps < idle.fps * 2:
        print("WARNING: 'active' did not speed up -- any_active may not be driving idling")
    if bench.wake_latency_ms:
        worst = max(bench.wake_latency_ms)
        budget = 1000.0 / args.fps_idle
        print(f"cross-thread wake latency: {worst:.0f}ms (§4.2 predicts <= {budget:.0f}ms)")
    return 0


def _schedule_wake(state: object, bench: Bench, node: tuple[str, str | None]) -> None:
    """Emit an intent from the asyncio thread mid-phase and time the UI's reaction."""
    from pptmstr.intents import AgentSpawned

    def later() -> None:
        time.sleep(bench.seconds * 0.5)
        bench.wake_sent_at = time.perf_counter()
        state.bridge.emit(  # type: ignore[attr-defined]
            AgentSpawned(node, None, "wake probe", "m", time.monotonic())
        )

    threading.Thread(target=later, daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
