#!/usr/bin/env python3
"""
What each text pane costs per frame, CPU-side, in a headless ImGui frame.

Five panes set text, and a change of renderer under any of them is judged against
the numbers this prints. It answers one recorded question outright
(planning/2026-09-08-the-toolkit-was-never-the-constraint.md, section 6): whether
``transcript_pane.draw``'s ``cache.lines[:stable]`` slice -- O(total lines), every
frame -- is what RICH mode costs at 100k lines, or whether something else is.

**What is timed.** ``perf_counter_ns`` around the pane's own draw function and
nothing else: not ``new_frame``, not ``render``, not a swap. There is no window and
no rasteriser, so a figure here is Python plus ImGui's layout and draw-list
submission, which is the part a renderer change moves. It is not a frame time and
says nothing about a GPU.

**What a row is.** One scenario in its own ImGui window with fresh pane state:

  first    the first frame drawn, which builds every cache the pane keeps
  median   over ``--frames`` frames after a warm-up
  p95      the same frames; the garbage collector is left on, as the app leaves it
  vtx      vertices ImGui emitted for the whole frame, on the last measured one
  seam     calls into ``pptmstr.ui.typeset`` during the last measured frame

``vtx`` is there to show what a row actually drew: a pane that submitted nothing
because its window was clipped away would post a fine median and a count near zero,
and two renderers that differ in how much they cull differ here first.

``seam`` is the column that says which renderer a row measured. A run with
``--typeset on`` over a pane that has not been moved to the seam prints 0 there and
has measured the old path under a new label; the count is taken from the calls, not
from the flag.

**Headless.** A context, an atlas and ``new_frame``/``render`` with no backend
(``tests/test_review_keys.py`` runs the same way). Inconsolata is loaded as the
first font and the bold face beside it, as ``theme.load_fonts`` does under the
runner; FontAwesome is not merged, so a state glyph is the fallback glyph here. Each
pane is drawn in a ``--width`` x ``--height`` window.

**Read ratios, not absolutes.** The header prints the load average the run started
and ended under. Compare rows of one run with each other, or two runs taken
back to back on the same machine.

Usage:
    .venv/bin/python scripts/bench_panes.py                    # today's renderers
    .venv/bin/python scripts/bench_panes.py --typeset on       # through the seam
    .venv/bin/python scripts/bench_panes.py --only detail --json shots/detail.json
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import itertools
import json
import os
import statistics
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import imgui_bundle  # noqa: E402
from imgui_bundle import imgui  # noqa: E402

from pptmstr import board, fake_driver, theme  # noqa: E402
from pptmstr.approval import diff_texts, render_texts, summarize  # noqa: E402
from pptmstr.bridge import Bridge  # noqa: E402
from pptmstr.intents import (  # noqa: E402
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    SubagentDelivered,
)
from pptmstr.model import AgentState, NodeId, PendingApproval, Snapshot  # noqa: E402
from pptmstr.store import Store  # noqa: E402
from pptmstr.transcript import SegmentKind, Transcript  # noqa: E402
from pptmstr.ui import (  # noqa: E402
    board_pane,
    brief_pane,
    detail,
    inbox,
    review,
    rich_pane,
    transcript_pane,
    typeset,
)
from pptmstr.ui.compose import ComposeState  # noqa: E402
from pptmstr.ui.focus import FocusState, Scope  # noqa: E402
from pptmstr.ui.transcript_pane import RenderMode, TranscriptState  # noqa: E402

WARMUP_FRAMES = 20
FRAME_S = 1.0 / 60.0
REBUILDS = 15
ROOT_NODE: NodeId = ("bench", None)
SUB_NODE: NodeId = ("bench", "agent-1")

# The public calls a pane makes into the seam. Counted, so a row can say whether it
# went through the seam at all.
_SEAM_CALLS = ("markdown", "plain", "document", "diff", "stream", "label")


@dataclass(frozen=True, slots=True)
class Row:
    group: str
    scenario: str
    # Size of what was drawn, in the unit the scenario names.
    size: str
    first_ms: float | None
    median_ms: float
    p95_ms: float
    max_ms: float
    vertices: int | None
    seam_calls: int | None


# ---------------------------------------------------------------------------
# The frame
# ---------------------------------------------------------------------------


class Harness:
    """
    One headless ImGui context, and a frame that times a draw function inside it.
    """

    def __init__(self, width: float, height: float) -> None:
        self.size = imgui.ImVec2(width, height)
        self.context = imgui.create_context()
        io = imgui.get_io()
        io.set_ini_filename("")
        io.display_size = imgui.ImVec2(width + 40.0, height + 40.0)
        io.delta_time = 1.0 / 60.0
        io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        bundled = Path(imgui_bundle.__file__).parent / "assets" / "fonts"
        vendored = Path(theme.__file__).parent / "assets" / "fonts"
        # The first font added is the default for every widget, as in the app.
        body = io.fonts.add_font_from_file_ttf(str(bundled / "Inconsolata-Medium.ttf"), 16.0)
        bold = io.fonts.add_font_from_file_ttf(str(vendored / "Inconsolata-Bold.ttf"), 16.0)
        # ``theme.load_fonts`` fills this table and needs a hello_imgui runner to do
        # it. Without the entries every bold run would be drawn in the body face.
        theme._FACES[theme.Face.BODY] = body
        theme._FACES[theme.Face.BOLD] = bold
        theme.set_theme("dark")
        theme.apply_style()
        self.seam_calls = 0
        self.vertices = 0
        self.frames = 0
        self._windows = itertools.count()
        self._window = ""
        self._count_seam_calls()

    def _count_seam_calls(self) -> None:
        """
        Wrap the seam's draw calls with a counter.

        Reaches a pane that calls ``typeset.markdown(...)`` through the module. One
        that imported the name directly would be missed and would read as 0.
        """
        for name in _SEAM_CALLS:
            original = getattr(typeset, name, None)
            if original is None:
                continue

            def counted(*args: Any, _original: Any = original, **kwargs: Any) -> Any:
                self.seam_calls += 1
                return _original(*args, **kwargs)

            setattr(typeset, name, counted)

    def fresh_window(self) -> None:
        """
        Give the next scenario a window of its own: no scroll position, no child
        windows and no tree state carried over from the one before.
        """
        self._window = f"##bench-{next(self._windows)}"

    def frame(self, body: Callable[[], None]) -> int:
        """
        One frame with ``body`` drawn in the scenario's window. Nanoseconds inside
        ``body``.
        """
        typeset.pre_frame()
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(10.0, 10.0))
        imgui.set_next_window_size(self.size)
        imgui.begin(self._window, None, imgui.WindowFlags_.no_saved_settings)
        self.seam_calls = 0
        started = time.perf_counter_ns()
        try:
            body()
        finally:
            elapsed = time.perf_counter_ns() - started
            imgui.end()
            imgui.render()
        self.vertices = imgui.get_draw_data().total_vtx_count
        self.frames += 1
        return elapsed

    def close(self) -> None:
        typeset.reset()
        imgui.destroy_context(self.context)


def _ms(ns: float) -> float:
    return ns / 1e6


def _p95(samples: list[int]) -> float:
    ordered = sorted(samples)
    return float(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))])


def measure(
    harness: Harness,
    group: str,
    scenario: str,
    size: str,
    body: Callable[[], None],
    frames: int,
    *,
    before_frame: Callable[[], None] | None = None,
) -> Row:
    """
    Draw ``body`` for a first frame, a warm-up and ``frames`` measured frames.

    ``before_frame`` runs outside the timed region: it is what the rest of the
    application does between frames, such as a driver appending a token.
    """
    harness.fresh_window()
    first = harness.frame(body)
    for _ in range(WARMUP_FRAMES):
        if before_frame is not None:
            before_frame()
        harness.frame(body)
    samples: list[int] = []
    for _ in range(frames):
        if before_frame is not None:
            before_frame()
        samples.append(harness.frame(body))
    row = Row(
        group=group,
        scenario=scenario,
        size=size,
        first_ms=_ms(first),
        median_ms=_ms(statistics.median(samples)),
        p95_ms=_ms(_p95(samples)),
        max_ms=_ms(max(samples)),
        vertices=harness.vertices,
        seam_calls=harness.seam_calls,
    )
    _progress(row)
    return row


def timed(fn: Callable[[], object], reps: int) -> list[int]:
    """Nanoseconds for each of ``reps`` calls of ``fn``, outside any frame."""
    samples: list[int] = []
    for _ in range(reps):
        started = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - started)
    return samples


def part(group: str, scenario: str, size: str, samples: list[int]) -> Row:
    """A row for something timed on its own rather than as a pane."""
    row = Row(
        group=group,
        scenario=scenario,
        size=size,
        first_ms=None,
        median_ms=_ms(statistics.median(samples)),
        p95_ms=_ms(_p95(samples)),
        max_ms=_ms(max(samples)),
        vertices=None,
        seam_calls=None,
    )
    _progress(row)
    return row


def _progress(row: Row) -> None:
    print(f"  .. {row.group} | {row.scenario} | {row.size}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def numbered(text: str, tag: str) -> str:
    """
    ``text`` with ``tag`` at the end of every line that can take one.

    A session does not say the same thing twice, and a fixture that does measures
    a renderer's caches: the seam builds equal parts once and the library keeps
    layout by a block's value, so the report repeated verbatim typesets at about
    half of what the same amount of distinct text costs. Fences, table rows and
    rules are left alone so the text is the same markdown with the tag in it.
    """
    out: list[str] = []
    for line in text.split("\n"):
        bare = line.strip()
        plain = bare and not bare.startswith(("```", "~~~", "|")) and set(bare) - set("-=*_ ")
        out.append(f"{line} {tag}" if plain else line)
    return "\n".join(out)


def report_transcript(lines: int) -> Transcript:
    """
    The fake fleet's report script, written as many times as it takes to reach
    ``lines`` lines: reasoning, tool calls, tool results, an error and the markdown
    report, in the proportions a session has them. Each time through is numbered,
    so no two runs are the same text (``numbered``).
    """
    transcript = Transcript()
    written = 0
    for cycle in itertools.count(1):
        if written >= lines:
            break
        for kind, text in fake_driver._REPORT_SCRIPT:
            transcript.append(kind, numbered(text, f"#{cycle}"))
            written += text.count("\n")
    return transcript


def report_markdown() -> str:
    """The OUTPUT bytes of the report script: the markdown a model wrote."""
    return "".join(text for kind, text in fake_driver._REPORT_SCRIPT if kind is SegmentKind.OUTPUT)


def tokens() -> Iterator[str]:
    """
    The report's markdown in four-character pieces, forever, each time through
    numbered apart from the last and from every run of ``report_transcript``.
    """
    for cycle in itertools.count(1):
        text = numbered(report_markdown(), f"~{cycle}")
        for start in range(0, len(text), 4):
            yield text[start : start + 4]


def spawned(transcript: Transcript | None = None) -> Store:
    """A store holding one root session."""
    store = Store()
    store.apply(
        AgentSpawned(
            node_id=ROOT_NODE,
            parent=None,
            task="the session the bench draws",
            model="claude-sonnet-5",
            started_at=time.monotonic(),
            cwd="/x/bench",
            transcript=transcript if transcript is not None else Transcript(),
        )
    )
    return store


def write_call(lines: int) -> tuple[str, Mapping[str, Any]]:
    """
    A Write of ``lines`` lines of Python to a file that does not exist, so its diff
    is the whole content as additions.
    """
    unit = fake_driver._CHECKSUM_MODULE.splitlines()
    body = list(itertools.islice(itertools.cycle(unit), lines))
    return "Write", {
        "file_path": "bench_panes/no_such_file.py",
        "content": "\n".join(body) + "\n",
    }


def parked(call: tuple[str, Mapping[str, Any]]) -> tuple[Snapshot, FocusState, PendingApproval]:
    """One session with ``call`` parked for approval and the cursor on it."""
    name, args = call
    texts = diff_texts(name, args)
    pending = PendingApproval(
        id="bench-pending",
        node=ROOT_NODE,
        tool_name=name,
        tool_use_id="tu-bench",
        raw_args=args,
        summary=summarize(name, args),
        requested_at=time.monotonic(),
        diff=render_texts(name, texts),
        texts=texts,
    )
    store = spawned()
    store.apply(ApprovalRequested(ROOT_NODE, pending))
    snap = store.snapshot()
    focus = FocusState()
    focus.to_obligation(snap.needs_you[0])
    assert focus.obligation(snap) is not None
    return snap, focus, pending


def delivered(text: str) -> tuple[Snapshot, FocusState]:
    """A finished sub-agent that handed ``text`` over, with the cursor on it."""
    store = spawned()
    store.apply(
        AgentSpawned(
            node_id=SUB_NODE,
            parent=ROOT_NODE,
            task="a sub-agent with an answer",
            model="claude-sonnet-5",
            started_at=time.monotonic(),
            agent_type="Explore",
            transcript=Transcript(),
        )
    )
    store.apply(SubagentDelivered(SUB_NODE, text))
    store.apply(AgentFinished(SUB_NODE, AgentState.DONE, ended_at=time.monotonic()))
    snap = store.snapshot()
    focus = FocusState()
    focus.to_node(snap, SUB_NODE, scope=Scope.AGENT)
    assert focus.obligation(snap) is None and focus.node(snap) == SUB_NODE
    assert snap.nodes[SUB_NODE].deliverable == text
    return snap, focus


def fake_fleet() -> tuple[fake_driver.FakeDriver, Snapshot]:
    """The ``--fake`` fleet as it stands on its first frame."""
    driver = fake_driver.FakeDriver(Bridge())
    asyncio.run(driver._seed_tree())
    store = Store()
    store.apply_all(driver.bridge.drain())
    return driver, store.snapshot()


# ---------------------------------------------------------------------------
# 1. The transcript pane
# ---------------------------------------------------------------------------


def bench_transcript(harness: Harness, sizes: list[int], frames: int) -> list[Row]:
    rows: list[Row] = []
    for lines in sizes:
        transcript = report_transcript(lines)
        snap = spawned(transcript).snapshot()
        stream = tokens()

        def append_token(
            transcript: Transcript = transcript, stream: Iterator[str] = stream
        ) -> None:
            transcript.append(SegmentKind.OUTPUT, next(stream))

        # Idle for every mode first: the streaming scenarios grow the transcript.
        for streaming in (False, True):
            for mode in RenderMode:
                state = TranscriptState(mode=mode)
                tick = itertools.count(1)

                def draw(
                    state: TranscriptState = state,
                    snap: Snapshot = snap,
                    tick: Iterator[int] = tick,
                ) -> None:
                    # As ``app`` does before the panes: prune, then draw.
                    state.prune(next(tick))
                    transcript_pane.draw(snap, state, ROOT_NODE)

                rows.append(
                    measure(
                        harness,
                        "transcript_pane.draw",
                        f"{mode.name} {'one token a frame' if streaming else 'idle'}",
                        f"{len(_lines_of(snap)):,} lines",
                        draw,
                        frames,
                        before_frame=append_token if streaming else None,
                    )
                )
        rows.extend(bench_transcript_parts(transcript, snap))
    return rows


def _lines_of(snap: Snapshot) -> list[transcript_pane.Line]:
    """The line cache as a throwaway sync builds it, for a row's size column."""
    cache = transcript_pane.NodeTranscript()
    record = snap.nodes[ROOT_NODE]
    cache.sync(record.transcript, record.transcript.published_length)
    return cache.lines


def bench_transcript_parts(transcript: Transcript, snap: Snapshot) -> list[Row]:
    """
    The statements of ``transcript_pane.draw`` that run before anything is drawn,
    each timed alone against a cache that is already caught up.

    These are the per-frame costs that depend on the length of the transcript and
    not on what is visible, which is what the planning record asks about.
    """
    group = "transcript_pane parts"
    state = TranscriptState(mode=RenderMode.RICH)
    cache = state.cache_for(ROOT_NODE)
    cache.sync(transcript, transcript.published_length)
    stable = len(cache.lines) - (1 if cache.open_line else 0)
    cache.block_cursor.feed(cache.lines[:stable])
    size = f"{len(cache.lines):,} lines"
    reps = 200 if len(cache.lines) <= 20_000 else 60
    shown = rich_pane.windowed(cache.block_cursor.blocks)
    visible = transcript_pane._visible(state, cache)
    stream = tokens()

    def sync_one_token() -> None:
        transcript.append(SegmentKind.OUTPUT, next(stream))
        cache.sync(transcript, transcript.published_length)

    rows = [
        part(group, "RICH: cache.lines[:stable]", size, timed(lambda: cache.lines[:stable], reps)),
        part(
            group,
            "RICH: block_cursor.feed(slice), nothing new",
            size,
            timed(lambda: cache.block_cursor.feed(cache.lines[:stable]), reps),
        ),
        part(
            group,
            "RICH: block_cursor.live_block",
            size,
            timed(lambda: cache.block_cursor.live_block(cache.lines), reps),
        ),
        part(
            group,
            "RICH: rich_pane.windowed",
            size,
            timed(lambda: rich_pane.windowed(cache.block_cursor.blocks), reps),
        ),
        part(
            group,
            "RICH: block_copy_text(windowed)",
            size,
            timed(lambda: rich_pane.block_copy_text(shown), reps),
        ),
        part(
            group,
            "RAW/WRAP: _visible()",
            size,
            timed(lambda: transcript_pane._visible(state, cache), reps),
        ),
        part(
            group,
            "RAW/WRAP: copy_text(visible)",
            size,
            timed(lambda: transcript_pane.copy_text(visible), reps),
        ),
        part(
            group,
            "all modes: sync(), nothing new",
            size,
            timed(lambda: cache.sync(transcript, transcript.published_length), reps),
        ),
        part(group, "all modes: append + sync() of one token", size, timed(sync_one_token, reps)),
    ]
    return rows


# ---------------------------------------------------------------------------
# 2. Coming back to a pruned transcript
# ---------------------------------------------------------------------------


def bench_prune(harness: Harness, lines: int) -> list[Row]:
    """
    The frame that rebuilds a line cache ``TranscriptState.prune`` dropped.

    The cache is dropped by the pane's own rule -- its clock is moved past
    ``transcript_pane._CACHE_KEEP_S`` with nothing drawn, as time passes while
    another node is selected -- and the next draw is the one timed.
    """
    rows: list[Row] = []
    transcript = report_transcript(lines)
    snap = spawned(transcript).snapshot()
    for mode in RenderMode:
        state = TranscriptState(mode=mode)
        frame_no = 0
        clock = 0.0

        def draw(state: TranscriptState = state) -> None:
            transcript_pane.draw(snap, state, ROOT_NODE)

        harness.fresh_window()
        for _ in range(WARMUP_FRAMES):
            frame_no += 1
            clock += FRAME_S
            state.prune(frame_no, clock)
            harness.frame(draw)
        size = f"{len(state.caches[ROOT_NODE].lines):,} lines"
        rebuilds: list[int] = []
        for _ in range(REBUILDS):
            frame_no += 1
            clock += transcript_pane._CACHE_KEEP_S + FRAME_S
            state.prune(frame_no, clock)
            if ROOT_NODE in state.caches:
                raise RuntimeError("prune kept the cache; the rebuild would not be measured")
            rebuilds.append(harness.frame(draw))
            for _ in range(3):
                frame_no += 1
                clock += FRAME_S
                state.prune(frame_no, clock)
                harness.frame(draw)
        rows.append(part("return to a pruned node", f"{mode.name} rebuild frame", size, rebuilds))
    return rows


# ---------------------------------------------------------------------------
# 3-6. The other panes
# ---------------------------------------------------------------------------


def _require_diff_drawn(
    harness: Harness, review_state: review.ReviewState, pending: PendingApproval
) -> None:
    """
    Refuse a row that did not reach the diff of the call it names.

    Either renderer leaves a trace: the old one splits the diff into
    ``ReviewState.diff_lines`` where it draws it, and the new one is a call into the
    seam. Neither means the pane took a branch that shows no diff -- a cursor that
    was not on the approval, a row that was not expanded.
    """
    if pending.id not in review_state.diff_lines and harness.seam_calls == 0:
        raise RuntimeError("the parked call's diff was not drawn; the row measured something else")


def bench_detail(harness: Harness, frames: int) -> list[Row]:
    rows: list[Row] = []
    for lines in (60, 5000):
        snap, focus, pending = parked(write_call(lines))
        review_state = review.ReviewState()
        pane = detail.DetailState()

        def draw(
            snap: Snapshot = snap,
            focus: FocusState = focus,
            review_state: review.ReviewState = review_state,
            pane: detail.DetailState = pane,
        ) -> None:
            detail.draw(snap, focus, review_state, pane, time.monotonic())

        diff_lines = len((pending.diff or "").splitlines())
        rows.append(
            measure(
                harness,
                "detail.draw",
                f"parked Write, {lines:,} lines",
                f"{diff_lines:,} diff lines",
                draw,
                frames,
            )
        )
        _require_diff_drawn(harness, review_state, pending)

    markdown = report_markdown()
    for label, text in (
        ("deliverable, the fake fleet's", fake_driver._DELIVERABLE),
        ("deliverable, the report x10", markdown * 10),
    ):
        snap, focus = delivered(text)
        review_state = review.ReviewState()
        pane = detail.DetailState()

        def draw_deliverable(
            snap: Snapshot = snap,
            focus: FocusState = focus,
            review_state: review.ReviewState = review_state,
            pane: detail.DetailState = pane,
        ) -> None:
            detail.draw(snap, focus, review_state, pane, time.monotonic())

        rows.append(
            measure(
                harness,
                "detail.draw",
                label,
                f"{text.count(chr(10)):,} lines",
                draw_deliverable,
                frames,
            )
        )
    return rows


def bench_inbox(harness: Harness, frames: int) -> list[Row]:
    rows: list[Row] = []
    actions = inbox.InboxActions(
        send=lambda node, text: None,
        interrupt=lambda node: None,
        close=lambda node: None,
        dismiss=lambda node: None,
        relaunch=lambda spec: None,
    )
    for lines in (60, 5000):
        snap, focus, pending = parked(write_call(lines))
        review_state = review.ReviewState()
        compose = ComposeState()
        bridge = Bridge()

        def draw(
            snap: Snapshot = snap,
            focus: FocusState = focus,
            review_state: review.ReviewState = review_state,
            compose: ComposeState = compose,
            bridge: Bridge = bridge,
        ) -> None:
            inbox.draw(snap, focus, review_state, compose, bridge, actions, time.monotonic())

        diff_lines = len((pending.diff or "").splitlines())
        rows.append(
            measure(
                harness,
                "inbox.draw",
                f"one approval expanded, Write of {lines:,} lines",
                f"{diff_lines:,} diff lines",
                draw,
                frames,
            )
        )
        _require_diff_drawn(harness, review_state, pending)
    return rows


def bench_board(harness: Harness, frames: int, fleet: tuple[Any, Snapshot]) -> list[Row]:
    driver, snap = fleet
    lead = driver.cast["lead"]
    focus = FocusState()
    focus.to_node(snap, lead, scope=Scope.SESSION)
    node = focus.node(snap)
    assert node is not None and node[0] == lead[0]
    tasks = board.board_tasks(snap, lead[0])
    concerns = board.board_concerns(snap, lead[0])
    size = f"{len(tasks)} tasks, {len(concerns)} concerns"
    bridge = Bridge()
    rows: list[Row] = []

    tree_node_ex = imgui.tree_node_ex
    opens = 0

    def open_every_row(*args: Any, **kwargs: Any) -> bool:
        # The pane keeps no record of which rows are open -- ImGui does, per
        # window -- so the bench opens them where the pane asks.
        nonlocal opens
        imgui.set_next_item_open(True)
        result = bool(tree_node_ex(*args, **kwargs))
        opens += int(result)
        return result

    for label, opened in (("every row collapsed", False), ("every row expanded", True)):
        pane = board_pane.BoardState()

        def draw(pane: board_pane.BoardState = pane) -> None:
            board_pane.draw(snap, focus, pane, bridge)

        if opened:
            imgui.tree_node_ex = open_every_row  # type: ignore[assignment]
        try:
            row = measure(harness, "board_pane.draw", label, size, draw, frames)
        finally:
            imgui.tree_node_ex = tree_node_ex  # type: ignore[assignment]
        if opened and opens == 0:
            raise RuntimeError("no board row opened; the expanded row measured a collapsed board")
        rows.append(row)
    return rows


def bench_brief(harness: Harness, frames: int, fleet: tuple[Any, Snapshot]) -> list[Row]:
    driver, snap = fleet
    lead = driver.cast["lead"]
    focus = FocusState()
    focus.to_node(snap, lead, scope=Scope.SESSION)
    pane = brief_pane.BriefState()
    record = snap.nodes[(lead[0], None)]
    assert record.brief
    entries = pane.entries(Path(record.brief))
    chars = sum(len(item.entry.body) for item in entries)

    def draw() -> None:
        brief_pane.draw(snap, focus, pane)

    return [
        measure(
            harness,
            "brief_pane.draw",
            "the fake fleet's brief",
            f"{len(entries)} entries, {chars:,} chars",
            draw,
            frames,
        )
    ]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _cell(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}" if value < 10 else f"{value:.1f}"


def render(rows: list[Row]) -> str:
    header = (
        "group",
        "scenario",
        "size",
        "first ms",
        "median ms",
        "p95 ms",
        "max ms",
        "vtx",
        "seam",
    )
    table = [header] + [
        (
            r.group,
            r.scenario,
            r.size,
            _cell(r.first_ms),
            _cell(r.median_ms),
            _cell(r.p95_ms),
            _cell(r.max_ms),
            "-" if r.vertices is None else f"{r.vertices:,}",
            "-" if r.seam_calls is None else str(r.seam_calls),
        )
        for r in rows
    ]
    widths = [max(len(line[i]) for line in table) for i in range(len(header))]
    out = []
    previous = ""
    for index, line in enumerate(table):
        if index > 1 and line[0] != previous:
            out.append("")
        previous = line[0]
        cells = [
            cell.ljust(widths[i]) if i < 3 else cell.rjust(widths[i]) for i, cell in enumerate(line)
        ]
        out.append("  ".join(cells).rstrip())
        if index == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def _load() -> str:
    one, five, fifteen = os.getloadavg()
    return f"{one:.2f} {five:.2f} {fifteen:.2f}"


GROUPS = ("transcript", "prune", "detail", "inbox", "board", "brief")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    ap.add_argument(
        "--typeset",
        choices=("off", "on"),
        default="off",
        help="draw through pptmstr.ui.typeset (Settings.typeset) or the renderers before it",
    )
    ap.add_argument(
        "--mono",
        action="store_true",
        help="every role that may be monospace is: typeset.configure(mono=True)",
    )
    ap.add_argument("--frames", type=int, default=300, help="measured frames per scenario")
    ap.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[1_000, 10_000, 100_000],
        help="transcript lengths, in lines",
    )
    ap.add_argument("--prune-lines", type=int, default=5_000)
    ap.add_argument("--width", type=float, default=900.0)
    ap.add_argument("--height", type=float, default=700.0)
    ap.add_argument(
        "--only",
        nargs="+",
        choices=GROUPS,
        default=list(GROUPS),
        help="which groups to run",
    )
    ap.add_argument("--json", type=Path, default=None, help="also write the rows here")
    args = ap.parse_args()
    if args.frames < 20:
        ap.error("--frames must be at least 20 for a p95 to mean anything")

    harness = Harness(args.width, args.height)
    want = args.typeset == "on"
    if want and not typeset.load_fonts(
        Path(theme.__file__).parent / "assets" / "fonts" / "Inconsolata-Bold.ttf"
    ):
        print("--typeset on: the seam's faces did not load, so nothing would be typeset")
        return 2
    typeset.configure(typeset=want, mono=args.mono)
    if typeset.enabled() != want:
        print(f"--typeset {args.typeset}: typeset.enabled() is {typeset.enabled()}")
        return 2

    load_before = _load()
    started = time.monotonic()
    rows: list[Row] = []
    try:
        if "transcript" in args.only:
            rows += bench_transcript(harness, args.sizes, args.frames)
        if "prune" in args.only:
            rows += bench_prune(harness, args.prune_lines)
        if "detail" in args.only:
            rows += bench_detail(harness, args.frames)
        if "inbox" in args.only:
            rows += bench_inbox(harness, args.frames)
        if {"board", "brief"} & set(args.only):
            fleet = fake_fleet()
            if "board" in args.only:
                rows += bench_board(harness, args.frames, fleet)
            if "brief" in args.only:
                rows += bench_brief(harness, args.frames, fleet)
    finally:
        frames_drawn = harness.frames
        harness.close()

    header = (
        f"bench_panes: typeset {'on' if want else 'off'}"
        f"{', monospace prose' if args.mono else ''}"
        f" | pane {args.width:.0f}x{args.height:.0f} | {args.frames} frames a scenario"
        f" | {frames_drawn:,} frames drawn in {time.monotonic() - started:.0f}s\n"
        f"python {sys.version.split()[0]}, imgui {imgui.get_version()},"
        f" gc {'on' if gc.isenabled() else 'off'}, {os.cpu_count()} cpus\n"
        f"load average (1/5/15 min) before: {load_before} | after: {_load()}\n"
        "CPU ms inside the pane's draw function; headless, nothing rasterised.\n"
    )
    print(header)
    print(render(rows))
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {"typeset": want, "mono": args.mono, "rows": [asdict(r) for r in rows]},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
