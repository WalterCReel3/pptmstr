#!/usr/bin/env python3
"""
Measure what idling actually costs and saves (design §8 step 5, §4.2).

Three phases against one live window, so the comparison is within a single process
and a single GPU context rather than across runs:

  full-speed      idling forced off                  -- the baseline
  idle            idling on, nothing active          -- the resting state
  active          idling on, one agent THINKING      -- the predicate under load

The claim being tested is not "idling works" but "``any_active`` drives it". The
third phase is the one that would catch a predicate wired backwards, which the
first two cannot: an app that idles all the time looks great on a CPU graph and is
useless.

Also measures **cross-thread wake latency** -- how long an intent emitted from the
asyncio thread waits before a frame reflects it. §4.2 predicts <=111ms at 9fps and
calls that acceptable; this reports the real number. That is the "an agent finished
while you were reading something else" case.

CPU comes from resource.getrusage, which is stdlib and counts every thread in this
process. It does not count the compositor, so treat the absolute numbers as
this-process-only; the ratio between phases is the point.

Usage:  .venv/bin/python scripts/bench_idle.py [--seconds 4]
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
    args = ap.parse_args()

    from imgui_bundle import hello_imgui, immapp

    from pptmstr import app as pptmstr_app
    from pptmstr.intents import AgentSpawned, StateChanged
    from pptmstr.model import AgentState

    bench = Bench(seconds=args.seconds)
    bench.phases = [
        Phase("full-speed", force_idling=False, make_active=False),
        Phase("idle", force_idling=None, make_active=False),
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
            if phase.make_active:
                # Drive the store the way the driver would, so the predicate is
                # exercised through its real input rather than poked directly.
                st.bridge.emit(  # type: ignore[attr-defined]
                    AgentSpawned(node, None, "bench", "m", time.monotonic())
                )
                st.bridge.emit(StateChanged(node, AgentState.THINKING))  # type: ignore[attr-defined]
            elif bench.index > 0:
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
        pptmstr_app.main(["--fps-idle", str(args.fps_idle)])
    finally:
        immapp.run = real_run  # type: ignore[assignment]
        pptmstr_app.AppState = real_appstate  # type: ignore[misc]

    print(f"\nfps_idle = {args.fps_idle:g}, {args.seconds:g}s per phase\n")
    print(f"{'phase':<12} {'fps':>8} {'cpu %':>8} {'frames':>8}")
    print("-" * 40)
    for phase in bench.phases:
        print(f"{phase.name:<12} {phase.fps:>8.1f} {phase.cpu_percent:>8.1f} {phase.frames:>8}")

    full, idle, active = bench.phases
    print()
    if idle.cpu_percent and full.cpu_percent:
        print(f"idle costs {idle.cpu_percent / full.cpu_percent:.1%} of full-speed CPU")
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
