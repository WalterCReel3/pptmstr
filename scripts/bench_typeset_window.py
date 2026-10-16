#!/usr/bin/env python3
"""
The first frame of a long log in TYPESET, by ``transcript_pane._TYPESET_WINDOW``.

The window bounds how many lines one ``TypesetFeed.advance`` lays out, and that is
the frame it bounds: the first on a node. This prints that frame for each window
asked for, on ``bench_panes.turns_transcript``'s 1,250-turn log (10,000 lines),
in ``bench_panes.Harness``'s headless frame of 900 by 700. The numbers in
``_TYPESET_WINDOW``'s docstring are this script's.

**The warm-up is reported on its own.** The first typeset frame a process draws
pays the library's one-time costs as well as the window's, so the run before any
measured one is a warm-up at the smallest window, and its first frame is printed
apart and not counted. Every measured run then has fresh pane state and a fresh
window, and ``--passes`` runs every window that many times, in order; the median
first frame of the passes is the figure.

**Read ratios, not absolutes**, as for ``bench_panes``: the load average is
printed before and after.

    .venv/bin/python scripts/bench_typeset_window.py
    .venv/bin/python scripts/bench_typeset_window.py --windows 400 1000 --passes 5
"""

from __future__ import annotations

import argparse
import itertools
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_panes as bench  # noqa: E402

from pptmstr import theme  # noqa: E402
from pptmstr.ui import transcript_pane, typeset  # noqa: E402
from pptmstr.ui.transcript_pane import RenderMode, TranscriptState  # noqa: E402
from pptmstr.ui.typeset import diffs  # noqa: E402

WINDOWS = (250, 400, 600, 800, 1000, 1500, 2000, 3000, 10_000)
TURNS = 1250


def first_frame(harness: bench.Harness, window: int, frames: int) -> float:
    """
    The first frame, in ms, of the log drawn with ``window`` and nothing held
    before it.
    """
    snap = bench.spawned(bench.turns_transcript(TURNS)).snapshot()
    state = TranscriptState(mode=RenderMode.TYPESET)
    tick = itertools.count(1)

    def draw() -> None:
        state.prune(next(tick))
        transcript_pane.draw(snap, state, bench.ROOT_NODE)

    old = transcript_pane._TYPESET_WINDOW
    transcript_pane._TYPESET_WINDOW = window
    try:
        row = bench.measure(harness, "log", f"typeset w={window}", "", draw, frames)
    finally:
        transcript_pane._TYPESET_WINDOW = old
    assert row.first_ms is not None
    return row.first_ms


def load() -> str:
    try:
        return Path("/proc/loadavg").read_text().strip()
    except OSError:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--windows", type=int, nargs="+", default=list(WINDOWS))
    parser.add_argument("--passes", type=int, default=3)
    parser.add_argument("--frames", type=int, default=10, help="measured after the first")
    args = parser.parse_args()

    harness = bench.Harness(900.0, 700.0)
    fonts = Path(theme.__file__).parent / "assets" / "fonts" / "Inconsolata-Bold.ttf"
    if not typeset.load_fonts(fonts):
        print("the typeset library did not load; nothing to measure", file=sys.stderr)
        return 1
    typeset.configure(typeset=True)
    diffs.columns()
    # bench.measure prints a row per run; only this script's table is wanted.
    bench._progress = lambda row: None
    print("load", load())
    try:
        warm = first_frame(harness, min(args.windows), args.frames)
        print(f"warm-up (window {min(args.windows)}, not counted): first {warm:.1f} ms")
        firsts: dict[int, list[float]] = {window: [] for window in args.windows}
        for _ in range(args.passes):
            for window in args.windows:
                firsts[window].append(first_frame(harness, window, args.frames))
        print(f"{'window':>7}  {'median':>8}  passes (ms)")
        for window, samples in firsts.items():
            spread = " ".join(f"{ms:.0f}" for ms in samples)
            print(f"{window:>7}  {statistics.median(samples):8.1f}  {spread}")
    finally:
        harness.close()
    print("load", load())
    return 0


if __name__ == "__main__":
    sys.exit(main())
