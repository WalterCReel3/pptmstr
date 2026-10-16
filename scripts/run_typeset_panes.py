#!/usr/bin/env python3
"""
Run the application with the fake fleet, walk its queue, and say which renderer
drew what: the seam's calls by pane, and the panes' own text beside them.

A pane decides per frame whether its text goes through the typeset seam
(``typeset.enabled(pane)``), and inside the seam markdown is the library's and
everything else ImGui's. Neither decision can be read off a picture by a script,
so this counts them where they are made:

  seam      calls into ``pptmstr.ui.typeset`` that draw, by the module that made
            them and by name (``markdown``, ``document``, ``plain``, ``diff``,
            and ``stream`` for a ``Stream.draw``)
  library   draws of the library's own views (``TypesetView.draw`` and its
            stream and diff views), which is what "typeset" finally means
  own       the panes' own renderers for the same surfaces: DETAIL's diff and
            arguments, the inbox row's diff rows, arguments and preview, the
            block renderer DETAIL draws prose with, and the context log's
            line modes

The cursor is moved down the queue with ``j`` every ``--step`` frames (the key
event enters at ImGui, as ``review.handle_keys`` reads it), so approvals,
questions and failures are each under it for a while. Then it is put on each
session that owes nothing, as a click on its rail card does
(``FocusState.to_node``, on the cursor ``detail.draw`` was last handed): a
session mid-turn is DETAIL's stream and a finished one its deliverable. A PNG is
written at each stop under ``--out``. **Nothing here says how any of it looks, and nobody has
looked at the pictures unless they say so.**

``--context`` brings the context log's tab to the front beside DETAIL. The
log is drawn as the pane draws it: its markdown runs through the library, and
with the library absent the pane's own wrapped lines.

**Literal text** is counted by who drew each part, each frame: ``library``
for a literal part the library set as a code block of its own, ``imgui`` for
one ImGui drew (by its ``Kind``), and why the seam left each one it was asked
for with ImGui (``views.literal_unfit``'s answers). ``--traceback`` draws
DETAIL's failure with a traceback of several lines in place of the fake
fleet's one-line error, stood in at ``detail._failure`` and said so in the
report, so that DETAIL shows literal text that is not an approval's.

``--diffs`` sets who draws a diff for the run (``typeset.diffs.ADOPT``:
``measured``, which is the product's, ``always`` or ``never``). An approval's
diff is counted on both sides: ``typeset.diff`` and the library's
``TypesetDiffView`` where the library drew it, ``detail._diff_lines`` and
``inbox._diff_rows`` where the pane did. The report says what was measured of
the library's columns, in which frame the measurement was taken (0: at the
font load, before any frame), and what ``diff_adopted`` answered on each frame.

The library is found as the application's entry point finds it
(``pptmstr.pairing``): an installed imtypeset, or this checkout's built pair
where none is installed or ``PPTMSTR_TYPESET_DEPS=1`` asks.
``IMSCRIBE_NO_TYPESET=1`` is the run without it. The report says which library
it was, and the sentence the Text menu would show.

Usage:
    scripts/xvfb_shot.sh -- .venv/bin/python scripts/run_typeset_panes.py --out shots/seam2-with
    scripts/xvfb_shot.sh -- .venv/bin/python scripts/run_typeset_panes.py \\
        --context --out shots/seam3-with
    scripts/xvfb_shot.sh -- env IMSCRIBE_NO_TYPESET=1 \\
        .venv/bin/python scripts/run_typeset_panes.py --out shots/seam2-without
    scripts/xvfb_shot.sh -- .venv/bin/python scripts/run_typeset_panes.py \\
        --diffs never --out shots/diffs-never
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from screenshot import _grab  # noqa: E402


def _scratch_config() -> Path:
    """
    Point this process at a configuration nobody owns, and say where it is.

    ``app.main`` reads ``settings.json`` and hands hello_imgui an ini under the
    user's configuration directory before it looks at ``--fake``, and an ``--argv``
    without ``--fake`` also moves every board log there and recalls the fleet
    (``app._start``). So a run counts the renderers at the default settings and the
    coded layout, whoever runs it, and what it should differ in goes through
    ``--argv`` and this script's own flags.

    Three things are moved, and all three before ``pptmstr.app`` is asked for
    anything: ``XDG_CONFIG_HOME``, which ``settings.config_dir`` and hello_imgui's
    ini both resolve from; ``CLAUDE_CONFIG_DIR``, which is where the SDK lists
    transcripts; and ``brief.legacy_root``, the CLI's projects directory, which is
    derived from the home directory and reads neither variable. A real session started through
    ``--argv`` therefore finds no credentials: this script is for the fake fleet.

    The directory is left behind, since the ini and whatever a run recorded are
    the evidence of what it did.
    """
    from pptmstr import brief

    scratch = Path(tempfile.mkdtemp(prefix="pptmstr-panes-"))
    config, claude = scratch / "config", scratch / "claude"
    config.mkdir()
    (claude / "projects").mkdir(parents=True)
    os.environ["XDG_CONFIG_HOME"] = str(config)
    os.environ["CLAUDE_CONFIG_DIR"] = str(claude)
    brief.legacy_root = lambda: claude / "projects"
    print(
        f"configuration for this run is under {scratch}, left in place:"
        " XDG_CONFIG_HOME, CLAUDE_CONFIG_DIR and the CLI projects root all point there",
        flush=True,
    )
    return scratch


def counted(owner: Any, name: str, tally: Counter[str], label: str, by_caller: bool) -> None:
    """
    Wrap ``owner.name`` so each call is counted under ``label``, with the calling
    module's last name in front where ``by_caller``.
    """
    real = getattr(owner, name)

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        key = label
        if by_caller:
            caller = sys._getframe(1).f_globals.get("__name__", "?").rsplit(".", 1)[-1]
            key = f"{caller}.{label}"
        tally[key] += 1
        return real(*args, **kwargs)

    setattr(owner, name, wrapper)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "shots" / "seam2")
    ap.add_argument("--stops", type=int, default=8, help="rows of the queue to stop on")
    ap.add_argument("--nodes", type=int, default=8, help="sessions owing nothing to stop on")
    ap.add_argument("--step", type=int, default=45, help="frames at each stop")
    ap.add_argument(
        "--context",
        action="store_true",
        help="bring the context log to the front beside DETAIL",
    )
    ap.add_argument(
        "--diffs",
        choices=("measured", "always", "never"),
        default="measured",
        help="who draws a diff: typeset.diffs.ADOPT for this run",
    )
    ap.add_argument(
        "--traceback",
        action="store_true",
        help="DETAIL's failure with a traceback of several lines in place of the fleet's error",
    )
    ap.add_argument("--argv", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args()
    _scratch_config()

    from imgui_bundle import hello_imgui, imgui, immapp

    from pptmstr import app as pptmstr_app
    from pptmstr import pairing, version

    pairing.adopt()
    from pptmstr.ui import detail, inbox, rich_pane, transcript_pane, typeset
    from pptmstr.ui.focus import Scope
    from pptmstr.ui.typeset import diffs, gate, views

    diffs.ADOPT = args.diffs
    measurements: list[str] = []
    real_measure = gate._measured_columns

    def measuring(theme: Any) -> Any:
        context = imgui.get_current_context()
        frame = imgui.get_frame_count() if context is not None else -1
        try:
            found = real_measure(theme)
        except Exception as failure:
            measurements.append(f"frame {frame}: raised {type(failure).__name__}: {failure}")
            raise
        measurements.append(
            f"frame {frame}: holds {found.holds}, cell {found.cell:.6f}, baked {found.baked:g} "
            f"at {found.size:g} px, {found.off} of {found.glyphs} glyphs off their column, "
            f"{found.mishit} mishit"
        )
        return found

    gate._measured_columns = measuring  # type: ignore[assignment]
    seam: Counter[str] = Counter()
    library: Counter[str] = Counter()
    own: Counter[str] = Counter()
    for name in ("markdown", "document", "plain", "diff"):
        counted(typeset, name, seam, name, True)
    counted(views.Stream, "draw", seam, "stream", True)
    for owner, name, label in (
        (detail, "_diff_lines", "detail._diff_lines"),
        (detail, "_arguments", "detail._arguments"),
        (inbox, "_diff_rows", "inbox._diff_rows"),
        (inbox, "_native_arguments", "inbox._native_arguments"),
        (inbox, "_native_preview", "inbox._native_preview"),
        (rich_pane, "draw", "rich_pane.draw"),
        (transcript_pane, "_draw_lines", "transcript_pane._draw_lines"),
    ):
        counted(owner, name, own, label, False)
    shown: Counter[str] = Counter()
    real_log = transcript_pane.draw

    def log_draw(snap: Any, state: Any, selected: Any, *rest: Any, **named: Any) -> None:
        real_log(snap, state, selected, *rest, **named)
        if selected is not None:
            feed = state.feeds.get(selected)
            held = 0 if feed is None else sum(len(feed.views.views(k)) for k in feed.views.keys())
            shown[f"{state.shown_mode().value}, {'a feed' if feed else 'no feed'}"] += 1
            handed["log"] = f"{state.shown_mode().value}, {held} of the library's views held"

    transcript_pane.draw = log_draw  # type: ignore[assignment]
    literal: Counter[str] = Counter()
    real_native, real_show_view, real_unfit = views._native, views._show_view, views.literal_unfit

    def native(im: Any, part: Any, wide: float) -> None:
        literal[f"imgui {part.kind.value}"] += 1
        real_native(im, part, wide)

    def show_view(im: Any, segment: Any, *rest: Any) -> float:
        if segment.literal:
            literal["library literal"] += 1
        return float(real_show_view(im, segment, *rest))

    def unfit(part: Any) -> str | None:
        said = real_unfit(part)
        if said is not None:
            literal[f"left with ImGui: {said}"] += 1
        return said

    views._native = native  # type: ignore[assignment]
    views._show_view = show_view  # type: ignore[assignment]
    views.literal_unfit = unfit  # type: ignore[assignment]
    if args.traceback:
        import dataclasses

        real_failure = detail._failure
        traceback_text = (
            "Traceback (most recent call last):\n"
            '  File "tle/parse.py", line 88, in parse_line_1\n'
            "    checksum = _checksum(line[:68])\n"
            '  File "tle/parse.py", line 41, in _checksum\n'
            "    total += int(char) if char.isdigit() else (1 if char == '-' else 0)\n"
            "    |        |    |           |                  |\n"
            "    0        9    18          30                 49\n"
            "ValueError: invalid literal for int() with base 10: '*'\n"
            "\tat column 62 of\t'1 25544U 98067A   08264.51782528 -.00002182  00000-0'\n"
            "exit status 1"
        )

        def failure(pane: Any, obligation: Any) -> None:
            real_failure(pane, dataclasses.replace(obligation, error=traceback_text))

        detail._failure = failure  # type: ignore[assignment]
    try:
        import imscribe

        for cls in (imscribe.TypesetView, imscribe.TypesetStreamView, imscribe.TypesetDiffView):
            if "draw" in vars(cls):
                counted(cls, "draw", library, cls.__name__, False)
    except Exception as failure:  # noqa: BLE001 -- said in the report, not fatal
        library[f"not counted ({type(failure).__name__})"] = 0

    frames = (args.stops + args.nodes) * args.step
    counter = {"n": 0}
    stops: list[str] = []
    handed: dict[str, Any] = {}
    visited: list[Any] = []
    real_detail = detail.draw

    def detail_draw(snap: Any, focus: Any, *rest: Any) -> None:
        handed["snap"], handed["focus"] = snap, focus
        real_detail(snap, focus, *rest)

    detail.draw = detail_draw  # type: ignore[assignment]

    def next_node() -> str:
        """
        Put the cursor on a session that owes nothing and has not been stopped
        on, one mid-turn before one that has finished. What it was, for the report.
        """
        snap, focus = handed.get("snap"), handed.get("focus")
        if snap is None or focus is None:
            return "DETAIL has not drawn"
        owing = {obligation.node for obligation in snap.needs_you}
        free = [node for node in snap.nodes if node not in owing and node not in visited]
        free.sort(key=lambda node: not snap.nodes[node].state.is_active)
        if not free:
            return "no session left that owes nothing"
        node = free[0]
        visited.append(node)
        focus.to_node(snap, node, scope=Scope.AGENT)
        record = snap.nodes[node]
        what = "mid-turn" if record.state.is_active else "settled"
        return f"{what}, {'a' if record.deliverable else 'no'} deliverable"

    enabled: Counter[str] = Counter()
    adopted: Counter[str] = Counter()
    real_run = immapp.run

    def patched_run(runner: Any, *rest: Any) -> Any:
        user_before = runner.callbacks.before_swap
        user_after = runner.callbacks.after_swap

        def before_swap() -> None:
            if user_before:
                user_before()
            for pane in typeset.Pane:
                enabled[pane.name] += bool(typeset.enabled(pane))
            adopted[str(typeset.diff_adopted())] += 1
            handed["diffs"] = typeset.diff_renderer() or "(none: not typeset)"
            if counter["n"] % args.step == args.step - 1:
                stop = counter["n"] // args.step
                out = args.out / f"stop-{stop}.png"
                _grab(out)
                held = sorted({str(key[1]) for key in inbox._VIEWS.keys()})
                where = handed.pop("where", "a row of the queue")
                log = handed.get("log", "not drawn")
                snap, focus = handed.get("snap"), handed.get("focus")
                under = None if snap is None or focus is None else focus.obligation(snap)
                on = "nothing owed" if under is None else under.kind.value
                drew = literal["library literal"] - handed.get("literal_at", 0)
                handed["literal_at"] = literal["library literal"]
                stops.append(
                    f"stop {stop} ({where}): {out}; DETAIL on {on}; inbox holds "
                    f"{held or 'nothing'}; log {log}; literal parts the library drew "
                    f"since the stop before {drew}"
                )
                handed["sentence"] = typeset.unavailable_reason() or "(none: text is typeset)"

        def after_swap() -> None:
            if user_after:
                user_after()
            counter["n"] += 1
            io = imgui.get_io()
            at = counter["n"] % args.step
            if counter["n"] >= args.stops * args.step:
                if at == 0:
                    handed["where"] = f"a session owing nothing: {next_node()}"
            elif at == 0:
                io.add_key_event(imgui.Key.j, True)
            elif at == 1:
                io.add_key_event(imgui.Key.j, False)
            if counter["n"] == 2:
                docking = hello_imgui.get_runner_params().docking_params
                docking.focus_dockable_window("DETAIL")
                if args.context:
                    docking.focus_dockable_window("CONTEXT")
            if counter["n"] >= frames:
                hello_imgui.get_runner_params().app_shall_exit = True

        runner.callbacks.before_swap = before_swap
        runner.callbacks.after_swap = after_swap
        runner.fps_idling.enable_idling = False
        return real_run(runner, *rest)

    immapp.run = patched_run  # type: ignore[assignment]
    try:
        status = int(pptmstr_app.main(args.argv or ["--fake"]))
    finally:
        immapp.run = real_run  # type: ignore[assignment]

    print(f"run_typeset_panes: {counter['n']} frames, the application's exit status {status}")
    print(f"  {version.library_line()}")
    print(f"  load average: {' '.join(f'{v:.2f}' for v in os.getloadavg())}")
    print(f"  the Text menu's sentence at the last stop: {handed.get('sentence', 'not read')}")
    print(
        "  frames each pane's typeset branch was on, of "
        f"{counter['n']}: {dict(sorted(enabled.items()))}"
    )
    print(
        f"  diffs.ADOPT {diffs.ADOPT!r}; diff_adopted() by frame: {dict(sorted(adopted.items()))}"
    )
    print(f"  the sentence about diffs at the last frame: {handed.get('diffs', 'not read')}")
    for taken in measurements or ["none taken"]:
        print(f"  the library's columns, measured: {taken}")
    print(f"  seam calls, by caller: {dict(sorted(seam.items())) or 'none'}")
    print(f"  the library's views drawn: {dict(sorted(library.items())) or 'none'}")
    print(f"  the panes' own renderers: {dict(sorted(own.items())) or 'none'}")
    print(
        "  literal parts drawn, part-frames by who drew them: "
        f"{dict(sorted(literal.items())) or 'none'}"
    )
    if args.traceback:
        print("  DETAIL's failure was drawn with a stood-in traceback (--traceback)")
    print(
        f"  frames the context log drew a session, by mode: {dict(sorted(shown.items())) or 'none'}"
    )
    for stop in stops:
        print(f"  {stop}")
    print("  nobody has looked at the pictures")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
