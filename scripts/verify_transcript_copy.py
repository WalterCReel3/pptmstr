#!/usr/bin/env python3
"""
Does right-click-to-copy actually hit the run the operator pointed at?

The line cache and the run arithmetic are unit-tested; the part that cannot be is
the bit that only exists inside a live frame -- hit-testing a row by its vertical
span, and latching that row's run while the context menu steals the hover. A
mistake there is silent: the menu opens, names a plausible kind, and copies the
wrong text.

So this drives a real window with synthetic mouse input and checks two things:

  1. **Sweep.** Walking the pointer down the transcript must latch runs in order,
     0 then 1 then 2, with the boundaries where the line counts say they are. A
     hit-test that used the item's *horizontal* extent would drop out on short
     lines and show up here as gaps.
  2. **Click.** Right-click a row in the middle run, pick the first menu entry,
     and the clipboard must hold that run whole -- including the line a filter is
     hiding, since a run copy is deliberately unfiltered.

The pointer is driven by warping it (``io.want_set_mouse_pos``) rather than by
queueing position events. Queued positions lose: the GLFW backend re-reads the real
cursor whenever it moves over the window, so on a live desktop the injected position
survives only until the operator twitches. Warping makes the real cursor follow, so
there is nothing to fight. **It therefore moves the physical pointer** for the couple
of seconds it runs.

Usage:  .venv/bin/python scripts/verify_transcript_copy.py
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if TYPE_CHECKING:
    from pptmstr.ui.transcript_pane import NodeTranscript

# Three runs, deliberately uneven, with a short line in the middle one: a short
# line is what an x-extent hit-test gets wrong.
CONTENT = [
    ("reasoning", "Looking at the failing test first.\nIt is the fence handling.\n"),
    ("output", "Here is the plan:\n\n- fix it\n- ok\n- add a test\n"),
    ("tool_call", "Bash(command=pytest -q)\n"),
]
TARGET_RUN = 1
SWEEP_STEP = 3.0


@dataclass
class Probe:
    frame: int = 0
    child_top: float = 0.0
    child_bottom: float = 0.0
    sweep: list[tuple[float, int | None]] = field(default_factory=list)
    sweep_y: float | None = None
    click_y: float | None = None
    click_x: float = 0.0
    stage: str = "settle"
    popup_run: int | None = None
    clipboard: str | None = None
    failures: list[str] = field(default_factory=list)
    done: bool = False


def main() -> int:
    ap = argparse.ArgumentParser(description="verify right-click-to-copy in the pane")
    # The two draw paths hit-test through the same helper but reach it differently:
    # one row per clipper step, or one wrapped paragraph at a time. Worth running both.
    ap.add_argument("--wrap", action="store_true", help="exercise the wrapped path")
    args = ap.parse_args()

    from imgui_bundle import ImVec2, hello_imgui, imgui, immapp

    from pptmstr.transcript import SegmentKind, Transcript
    from pptmstr.ui import transcript_pane as tp

    transcript = Transcript()
    for kind, text in CONTENT:
        transcript.append(SegmentKind(kind), text)
    cache = tp.NodeTranscript()
    cache.sync(transcript, transcript.published_length)

    mode = tp.RenderMode.WRAP if args.wrap else tp.RenderMode.RAW
    state = tp.TranscriptState(follow_tail=False, mode=mode)
    probe = Probe()
    print(f"mode: {'wrap' if args.wrap else 'clipper'}")

    def point_at(x: float, y: float) -> None:
        io = imgui.get_io()
        io.mouse_pos = ImVec2(x, y)
        io.want_set_mouse_pos = True

    def gui() -> None:
        io = imgui.get_io()
        probe.frame += 1

        # Explicit geometry: an auto-sized window gives the child no height, and the
        # sweep then has nothing to walk down.
        imgui.set_next_window_pos(ImVec2(0.0, 0.0))
        imgui.set_next_window_size(ImVec2(880.0, 560.0))
        imgui.begin("verify")
        probe.child_top = imgui.get_cursor_screen_pos().y
        probe.child_bottom = probe.child_top + imgui.get_content_region_avail().y
        probe.click_x = imgui.get_cursor_screen_pos().x + 40.0
        tp._draw_lines(state, cache, tp._visible(state, cache))
        imgui.end()

        # Read the latch for the position injected last frame, then queue the next.
        if probe.stage == "settle" and probe.frame > 3:
            if probe.child_bottom - probe.child_top < 100.0:
                probe.failures.append(
                    f"harness: child is {probe.child_bottom - probe.child_top:.0f}px tall"
                )
                probe.done = True
            probe.stage = "sweep"
            probe.sweep_y = probe.child_top + 1.0
        elif probe.stage == "sweep":
            assert probe.sweep_y is not None
            probe.sweep.append((probe.sweep_y, state.context_run))
            probe.sweep_y += SWEEP_STEP
            if probe.sweep_y >= probe.child_bottom:
                probe.stage = "aim"
        elif probe.stage == "aim":
            hits = [y for y, run in probe.sweep if run == TARGET_RUN]
            if not hits:
                probe.failures.append(
                    f"pointer never latched run {TARGET_RUN}; "
                    f"observed {sorted({r for _, r in probe.sweep}, key=str)}"
                )
                probe.done = True
            else:
                # Middle of the run's band, so the click is not on a boundary row.
                probe.click_y = hits[len(hits) // 2]
                probe.stage = "press"
        elif probe.stage == "press":
            io.add_mouse_button_event(1, True)
            probe.stage = "release"
        elif probe.stage == "release":
            io.add_mouse_button_event(1, False)
            probe.stage = "menu"
        elif probe.stage == "menu":
            # The popup spawns at the pointer; the run entry is its first row.
            probe.popup_run = state.context_run
            assert probe.click_y is not None
            point_at(probe.click_x + 20.0, probe.click_y + 14.0)
            probe.stage = "pick"
        elif probe.stage == "pick":
            io.add_mouse_button_event(0, True)
            probe.stage = "picked"
        elif probe.stage == "picked":
            io.add_mouse_button_event(0, False)
            probe.stage = "read"
        elif probe.stage == "read":
            probe.clipboard = imgui.get_clipboard_text()
            probe.done = True

        if probe.stage == "sweep" and probe.sweep_y is not None:
            point_at(probe.click_x, probe.sweep_y)
        elif probe.stage in ("press", "release") and probe.click_y is not None:
            point_at(probe.click_x, probe.click_y)

        if probe.done:
            hello_imgui.get_runner_params().app_shall_exit = True

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_geometry.size = (900, 600)
    params.app_window_params.window_title = "verify_transcript_copy"
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.callbacks.show_gui = gui
    params.fps_idling.enable_idling = False
    immapp.run(params)

    return report(probe, cache)


def report(probe: Probe, cache: NodeTranscript) -> int:
    bands: list[tuple[int | None, int]] = []
    for _, run in probe.sweep:
        if bands and bands[-1][0] == run:
            bands[-1] = (run, bands[-1][1] + 1)
        else:
            bands.append((run, 1))
    # Empty space below the last line latches nothing, and that is correct -- so the
    # order check looks at the runs that were seen, not at the gaps between them.
    observed = [run for run, _ in bands if run is not None]

    print(f"frames: {probe.frame}, samples: {len(probe.sweep)}")
    print(f"bands:  {bands}")

    if observed != list(range(len(cache.run_starts))):
        probe.failures.append(f"runs were not latched in order: {observed}")

    # Band widths must be proportional to line counts. Compared as ratios so the
    # check does not encode a font size.
    counts = [cache.run_bounds(i)[1] - cache.run_bounds(i)[0] for i in range(len(cache.run_starts))]
    sampled: dict[int, int] = {}
    for run, n in bands:
        if run is not None:
            sampled[run] = sampled.get(run, 0) + n
    for run, lines in enumerate(counts):
        share = sampled.get(run, 0) / max(1, sum(sampled.values()))
        expected = lines / sum(counts)
        if abs(share - expected) > 0.12:
            probe.failures.append(
                f"run {run} occupied {share:.0%} of the sweep, expected ~{expected:.0%}"
            )

    if probe.popup_run != TARGET_RUN:
        probe.failures.append(f"menu latched run {probe.popup_run}, pointed at {TARGET_RUN}")

    want = cache.run_text(TARGET_RUN)
    if probe.clipboard != want:
        probe.failures.append(f"clipboard held {probe.clipboard!r}, expected {want!r}")
    else:
        print(f"clipboard: {probe.clipboard!r}")

    for line in probe.failures:
        print(f"FAIL: {line}")
    if not probe.failures:
        print("OK: hover, latch and copy all agree")
    return 1 if probe.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
