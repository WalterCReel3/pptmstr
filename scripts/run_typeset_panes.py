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
            block renderer DETAIL shares with the context log, and the log's
            line modes

The cursor is moved down the queue with ``j`` every ``--step`` frames (the key
event enters at ImGui, as ``review.handle_keys`` reads it), so approvals,
questions and failures are each under it for a while. Then it is put on each
session that owes nothing, as a click on its rail card does
(``FocusState.to_node``, on the cursor ``detail.draw`` was last handed): a
session mid-turn is DETAIL's stream and a finished one its deliverable. A PNG is
written at each stop under ``--out``. **Nothing here says how any of it looks, and nobody has
looked at the pictures unless they say so.**

``--context MODE`` starts the context log in that mode, as
``Settings.context_mode`` would, and brings its tab to the front beside DETAIL:
``typeset`` is the log's markdown runs through the library, and with the
library absent the pane's own wrapped lines.

The library is used when ``IMTYPESET_LIBRARY`` and ``IMTYPESET_PYTHON`` name the
built pair (``make typeset-env`` prints them) and not otherwise; the report says
which, and the sentence the Text menu would show.

Usage:
    scripts/xvfb_shot.sh -- .venv/bin/python scripts/run_typeset_panes.py --out shots/seam2-with
    scripts/xvfb_shot.sh -- .venv/bin/python scripts/run_typeset_panes.py \\
        --context typeset --out shots/seam3-with
    scripts/xvfb_shot.sh -- env -u IMTYPESET_LIBRARY -u IMTYPESET_PYTHON \\
        .venv/bin/python scripts/run_typeset_panes.py --out shots/seam2-without
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from screenshot import _grab  # noqa: E402


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
        choices=("raw", "wrap", "rich", "typeset"),
        default=None,
        help="the mode the context log starts in, and bring the log to the front",
    )
    ap.add_argument("--argv", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args()

    from imgui_bundle import hello_imgui, imgui, immapp

    from pptmstr import app as pptmstr_app
    from pptmstr.ui import detail, inbox, rich_pane, transcript_pane, typeset
    from pptmstr.ui.focus import Scope
    from pptmstr.ui.typeset import views

    seam: Counter[str] = Counter()
    library: Counter[str] = Counter()
    own: Counter[str] = Counter()
    for name in ("markdown", "document", "plain", "diff"):
        counted(typeset, name, seam, name, True)
    counted(views.Stream, "draw", seam, "stream", True)
    for owner, name, label in (
        (detail, "_diff", "detail._diff"),
        (detail, "_arguments", "detail._arguments"),
        (inbox, "_diff_rows", "inbox._diff_rows"),
        (inbox, "_native_arguments", "inbox._native_arguments"),
        (inbox, "_native_preview", "inbox._native_preview"),
        (rich_pane, "draw", "rich_pane.draw"),
        (transcript_pane, "_draw_lines", "transcript_pane._draw_lines"),
        (transcript_pane, "_draw_rich", "transcript_pane._draw_rich"),
    ):
        counted(owner, name, own, label, False)
    shown: Counter[str] = Counter()
    real_log = transcript_pane.draw

    def log_draw(snap: Any, state: Any, selected: Any, *, start_in: Any = None) -> None:
        if args.context is not None:
            start_in = transcript_pane.RenderMode(args.context)
        real_log(snap, state, selected, start_in=start_in)
        if selected is not None:
            feed = state.feeds.get(selected)
            held = 0 if feed is None else sum(len(feed.views.views(k)) for k in feed.views.keys())
            shown[f"{state.shown_mode().value}, {'a feed' if feed else 'no feed'}"] += 1
            handed["log"] = f"{state.shown_mode().value}, {held} of the library's views held"

    transcript_pane.draw = log_draw  # type: ignore[assignment]
    try:
        import advtext

        for cls in (advtext.TypesetView, advtext.TypesetStreamView, advtext.TypesetDiffView):
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
    real_run = immapp.run

    def patched_run(runner: Any, *rest: Any) -> Any:
        user_before = runner.callbacks.before_swap
        user_after = runner.callbacks.after_swap

        def before_swap() -> None:
            if user_before:
                user_before()
            for pane in typeset.Pane:
                enabled[pane.name] += bool(typeset.enabled(pane))
            if counter["n"] % args.step == args.step - 1:
                stop = counter["n"] // args.step
                out = args.out / f"stop-{stop}.png"
                _grab(out)
                held = sorted({str(key[1]) for key in inbox._VIEWS.keys()})
                where = handed.pop("where", "a row of the queue")
                log = handed.get("log", "not drawn")
                stops.append(
                    f"stop {stop} ({where}): {out}; inbox holds {held or 'nothing'}; log {log}"
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
                if args.context is not None:
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
    print(f"  IMTYPESET_LIBRARY={os.environ.get('IMTYPESET_LIBRARY') or '(unset)'}")
    print(f"  load average: {' '.join(f'{v:.2f}' for v in os.getloadavg())}")
    print(f"  the Text menu's sentence at the last stop: {handed.get('sentence', 'not read')}")
    print(
        "  frames each pane's typeset branch was on, of "
        f"{counter['n']}: {dict(sorted(enabled.items()))}"
    )
    print(f"  seam calls, by caller: {dict(sorted(seam.items())) or 'none'}")
    print(f"  the library's views drawn: {dict(sorted(library.items())) or 'none'}")
    print(f"  the panes' own renderers: {dict(sorted(own.items())) or 'none'}")
    print(
        f"  frames the context log drew a session, by mode: {dict(sorted(shown.items())) or 'none'}"
    )
    for stop in stops:
        print(f"  {stop}")
    print("  nobody has looked at the pictures")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
