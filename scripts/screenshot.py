#!/usr/bin/env python3
"""
Run the app for N frames, grab the framebuffer, write a PNG, exit.

Exists so the UI can be looked at without a human in the loop -- which for an
immediate-mode UI is the only way to check that a change did what it claimed.
It runs the same code paths as a normal launch; it only adds a frame-count exit
and a glReadPixels.

``--scenario`` reaches the states a bare ``--fake`` run never shows: the launcher
modal and an opened board. A scenario is a generator advanced once per frame. It
moves the application by the route an operator's input takes -- the launcher's own
methods, intents on the bridge, key events -- and yields a name to have that frame
written as ``<out-dir>/<name>.png``. The two things it sets that no method exposes
are ImGui's per-id open flags for a header or a tree row, which is what a click on
the triangle writes, and the launcher's ``scanner`` seam, so the resume list holds
rows that do not depend on the transcripts of whoever runs it.

Every run is against a configuration directory made for it and named on the first
line of output, never the operator's: see ``_scratch_config``. So a capture shows
the default settings and the coded layout whoever takes it; what a run should
differ in goes through ``--argv``. The one thing left where it is, and only for a
run whose ``--argv`` names a ``--task``, is the CLI's own directory, since a real
session cannot start without the credentials in it -- and that run says so first.

PNG is written by hand via zlib rather than pulling in Pillow. The encoder is
twenty lines and this is a dev script -- a dependency here would be paid for by
everyone who installs the project.
"""

from __future__ import annotations

import argparse
import functools
import os
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def write_png(path: Path, width: int, height: int, rgb_rows: list[bytes]) -> None:
    """Minimal truecolour 8-bit PNG, filter type 0 on every row."""
    raw = b"".join(b"\x00" + row for row in rgb_rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def _scratch_config(real_session: bool = False) -> Path:
    """
    Point this process at a configuration nobody owns, and say where it is.

    ``app.main`` reads ``settings.json`` and hands hello_imgui an ini under the
    user's configuration directory before it looks at ``--fake``, and a start that
    is not ``--fake`` also moves every board log there and recalls the fleet
    (``app._start``). On the operator's own directory that is a capture reading
    their settings at best and rearranging their data at worst.

    Three things are moved, and all three before ``pptmstr.app`` is asked for
    anything: ``XDG_CONFIG_HOME``, which ``settings.config_dir`` and hello_imgui's
    ini both resolve from; ``CLAUDE_CONFIG_DIR``, which is where the SDK lists
    transcripts; and ``brief.default_root``, which is derived from the home
    directory and reads neither variable. An ``XDG_CONFIG_HOME`` already set is
    replaced like an unset one: nothing here can tell a disposable directory from
    the one the operator keeps.

    ``real_session`` leaves the last two alone, because the CLI a session spawns
    finds its credentials under ``CLAUDE_CONFIG_DIR`` and a team is told its
    brief's path. This application's own files -- settings, ini, session files,
    board logs -- are moved either way.

    The directory is left behind, since the ini and whatever a run recorded are
    the evidence of what it did.
    """
    from pptmstr import brief

    scratch = Path(tempfile.mkdtemp(prefix="pptmstr-shot-config-"))
    config, claude = scratch / "config", scratch / "claude"
    config.mkdir()
    os.environ["XDG_CONFIG_HOME"] = str(config)
    if real_session:
        print(
            "THIS RUN STARTS A REAL SESSION (--task): the CLI's own directory is the"
            " operator's, so its credentials are used and its transcript, and a team's"
            f" brief, are written there. Only XDG_CONFIG_HOME is moved, to {config},"
            " left in place",
            flush=True,
        )
        return scratch
    (claude / "projects").mkdir(parents=True)
    os.environ["CLAUDE_CONFIG_DIR"] = str(claude)
    brief.default_root = lambda: claude / "projects"
    print(
        f"configuration for this run is under {scratch}, left in place:"
        " XDG_CONFIG_HOME, CLAUDE_CONFIG_DIR and the brief root all point there",
        flush=True,
    )
    return scratch


def _starts_a_session(argv: list[str]) -> bool:
    """
    Whether ``app.main`` given ``argv`` launches a session. ``--fake`` beside a
    ``--task`` does not stop it: ``main`` reads the two separately.

    Spelled exactly. argparse would also take ``--ta`` for ``--task``, and a
    spelling this misses is a run with no credentials that fails to start, which is
    the direction to be wrong in.
    """
    return any(a == "--task" or a.startswith("--task=") for a in argv)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "shot.png")
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--focus", default=None, help="dockable window to bring to front")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default=None)
    ap.add_argument("--out-dir", type=Path, default=None, help="where a scenario writes")
    ap.add_argument("--base", type=Path, default=None, help="where a scenario builds fixtures")
    ap.add_argument("--argv", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args()
    if args.scenario and (args.out_dir is None):
        ap.error("--scenario needs --out-dir")
    argv = args.argv or ["--fake"]
    _scratch_config(real_session=_starts_a_session(argv))

    from imgui_bundle import hello_imgui, immapp

    from pptmstr import app as pptmstr_app

    real_run = immapp.run
    counter = {"n": 0}
    run = _install(args.scenario, args.out_dir, args.base) if args.scenario else None

    def patched_run(runner: object, *rest: object) -> object:
        user_after = runner.callbacks.after_swap  # type: ignore[attr-defined]

        def after_swap() -> None:
            if user_after:
                user_after()
            counter["n"] += 1
            if args.focus and counter["n"] == 2:
                hello_imgui.get_runner_params().docking_params.focus_dockable_window(args.focus)
            if run is not None:
                if run.shot is not None:
                    _grab(args.out_dir / f"{run.shot}.png")
                    run.shot = None
                if run.done or time.monotonic() > run.deadline:
                    hello_imgui.get_runner_params().app_shall_exit = True
                return
            if counter["n"] == args.frames:
                _grab(args.out)
                hello_imgui.get_runner_params().app_shall_exit = True

        runner.callbacks.after_swap = after_swap  # type: ignore[attr-defined]
        # Idling would make the frame count take wall-clock time proportional to the
        # idle rate, and a screenshot run has no operator to wake it.
        runner.fps_idling.enable_idling = False  # type: ignore[attr-defined]
        return real_run(runner, *rest)

    immapp.run = patched_run  # type: ignore[assignment]
    try:
        code = int(pptmstr_app.main(argv))
    finally:
        immapp.run = real_run  # type: ignore[assignment]
    if run is None:
        return code
    if run.error is not None:
        print(run.error, file=sys.stderr)
        return 1
    if not run.done:
        print(f"scenario {args.scenario} did not finish in {_BUDGET_SECONDS:.0f}s", file=sys.stderr)
        return 1
    return code


def _grab(out: Path) -> None:
    from imgui_bundle import imgui
    from OpenGL import GL

    io = imgui.get_io()
    width = int(io.display_size.x * io.display_framebuffer_scale.x)
    height = int(io.display_size.y * io.display_framebuffer_scale.y)

    GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
    # This runs after the swap, so the frame is in the front buffer -- except under
    # Xvfb with Mesa's software rasteriser, where the front buffer reads back as all
    # zero and the back buffer still holds what was just presented. A frame of this
    # application is never all zero, so that reading is what picks the other buffer.
    data = b""
    for buffer in (GL.GL_FRONT, GL.GL_BACK):
        GL.glReadBuffer(buffer)
        data = GL.glReadPixels(0, 0, width, height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        if not isinstance(data, bytes):
            data = bytes(data)
        if any(data):
            break

    stride = width * 3
    # OpenGL's origin is bottom-left and PNG's is top-left, so the rows come out
    # upside down unless reversed here.
    rows = [data[y * stride : (y + 1) * stride] for y in range(height - 1, -1, -1)]
    write_png(out, width, height, rows)
    print(f"wrote {out} ({width}x{height})")


# -- scenarios -----------------------------------------------------------------

# Wall-clock, not frames: a scenario waits on worker threads -- the session scan,
# ``claude --version`` -- and with idling off a frame count says nothing about time.
_BUDGET_SECONDS = 180.0

# What a scenario yields each frame: None to wait, or the name to write that frame as.
Steps = Iterator[str | None]


class _Run:
    """
    One scenario in flight. Read by ``after_swap``, written by the overlay hook.
    """

    def __init__(self) -> None:
        self.steps: Steps | None = None
        self.shot: str | None = None
        self.done = False
        self.error: str | None = None
        self.deadline = time.monotonic() + _BUDGET_SECONDS


def _install(name: str, out_dir: Path, base: Path | None) -> _Run:
    """
    Advance ``name`` once per frame, ahead of ``app._draw_overlays``.

    That function is the hook because it is the one callback handed the
    ``AppState`` that draws the launcher, and it runs after every docked window:
    what a step changes is drawn whole on the next frame and never half on this one.

    An exception is kept and ends the run. ``app.guarded`` wraps the callback this
    sits inside and logs what a panel raises, which for a scenario would be a run
    that never finishes and never says why.
    """
    from pptmstr import app as pptmstr_app

    out_dir.mkdir(parents=True, exist_ok=True)
    if base is None:
        base = Path(tempfile.mkdtemp(prefix="pptmstr-shot-"))
    base.mkdir(parents=True, exist_ok=True)
    fixtures = Path(tempfile.mkdtemp(prefix="fx-", dir=base))
    run = _Run()
    real = pptmstr_app._draw_overlays

    def overlays(state: Any) -> None:
        if not run.done:
            try:
                if run.steps is None:
                    run.steps = SCENARIOS[name](state, fixtures)
                run.shot = next(run.steps)
            except StopIteration:
                run.done = True
            except Exception:  # noqa: BLE001 - reported by main, with its frames
                run.error = traceback.format_exc()
                run.done = True
        real(state)

    pptmstr_app._draw_overlays = overlays
    return run


def _frames(count: int = 8) -> Steps:
    """
    Let that many frames draw. An auto-resizing window takes two to settle.
    """
    for _ in range(count):
        yield None


def _until(what: str, done: Callable[[], bool], seconds: float = 30.0) -> Steps:
    end = time.monotonic() + seconds
    while not done():
        if time.monotonic() > end:
            raise TimeoutError(f"waited {seconds:.0f}s for {what}")
        yield None


def _press(key: Any) -> Steps:
    from imgui_bundle import imgui

    imgui.get_io().add_key_event(key, True)
    yield None
    imgui.get_io().add_key_event(key, False)
    yield None


def _window(prefix: str) -> Any:
    """
    The ImGui window whose name starts with ``prefix``. A child's name is its
    parent's, a slash, its own label and a hash, so a child is found by the front.
    """
    from imgui_bundle import imgui

    for window in imgui.get_current_context().windows:
        if window.name.startswith(prefix):
            return window
    raise LookupError(f"no window named {prefix}*")


def _set_open(window: Any, label: str, opened: bool) -> None:
    """
    What a click on a header's or a tree row's triangle writes: the open flag ImGui
    keeps for that id in the window's own storage.
    """
    window.state_storage.set_int(window.get_id(label), int(opened))


def _pages(window: Any, name: str) -> Steps:
    """
    ``window`` from where it stands to its end: ``name`` as it is, then one more
    frame for each screenful under it, then back to the top.

    A page is what is on screen, which for a window hanging off the bottom of the
    display is less than the window is tall.
    """
    from imgui_bundle import imgui

    yield name
    page = 1
    while window.scroll.y < window.scroll_max.y - 1.0:
        bottom = min(window.inner_rect.max.y, imgui.get_io().display_size.y)
        step = max(40.0, (bottom - window.inner_rect.min.y) * 0.85)
        imgui.internal.set_scroll_y(window, min(window.scroll.y + step, window.scroll_max.y))
        yield from _frames(3)
        page += 1
        yield f"{name}-page-{page}"
    imgui.internal.set_scroll_y(window, 0.0)
    yield from _frames(3)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=shot", "-c", "user.email=shot@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _layouts(root: Path) -> dict[str, str]:
    """
    One directory per git layout the launcher words differently, built with real git.

    Named the length real ones are. The containment paragraph quotes these paths,
    and a fixture called ``a/b`` would say nothing about how it wraps.
    """
    source = root / "Source"
    plain = root / "scratch" / "notes-on-the-catalogue"
    repo = source / "orbital-mechanics-toolkit"
    beside = source / "orbital-mechanics-toolkit-worktrees" / "tle-checksum"
    nested = repo / ".claude" / "worktrees" / "tle-fixed-width"
    bare = source / "orbital-archive.git"
    of_bare = source / "orbital-archive-worktrees" / "hotfix"
    for made in (plain, repo, beside.parent, of_bare.parent):
        made.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "start")
    (repo / "tle").mkdir()
    _git(repo, "worktree", "add", "-q", "-b", "tle-checksum", str(beside))
    (beside / "tle").mkdir()
    relinked = source / "orbital-mechanics-toolkit-worktrees" / "tle-epoch"
    relinked.symlink_to(beside, target_is_directory=True)
    _git(repo, "worktree", "add", "-q", "-b", "tle-fixed-width", str(nested))
    _git(source, "clone", "-q", "--bare", str(repo), str(bare))
    _git(bare, "worktree", "add", "-q", "-b", "hotfix", str(of_bare))
    return {
        "no-git": str(plain),
        "repo-root": str(repo),
        "repo-subdirectory": str(repo / "tle"),
        "worktree-beside": str(beside),
        "worktree-beside-subdirectory": str(beside / "tle"),
        "worktree-nested": str(nested),
        "worktree-of-bare": str(of_bare),
        # A path that is a directory only through a link, to the worktree beside it.
        "relinked": str(relinked),
        # Never made: where a removed worktree's sessions say they were recorded.
        "gone": str(beside.parent / "removed-last-week"),
    }


def _session_rows(layouts: dict[str, str], live: str | None = None) -> tuple[Any, ...]:
    """
    A resume listing with one row per answer ``launcher.launch_directory`` has, and
    after those a row under the id ``live`` when one is given: a session the
    application is running, as the listing shows one.
    """
    from pptmstr.sessions import SessionRow

    now = int(time.time() * 1000)
    minute = 60_000

    def row(n: int, cwd: str | None, branch: str | None, label: str, age: int, **more: Any) -> Any:
        return SessionRow(
            session_id=more.pop("session_id", f"0c9d1f6e-5b7a-4e2c-9a41-{n:012d}"),
            cwd=cwd,
            git_branch=branch,
            summary=label,
            first_prompt=None,
            created_at=now - age * minute - minute,
            last_modified=now - age * minute,
            tag=None,
            **more,
        )

    running = (
        ()
        if live is None
        else (row(6, layouts["repo-root"], "main", "chase a flaky test", 0, session_id=live),)
    )
    return (
        row(1, layouts["repo-root"], "main", "wire the checksum beside the parser", 12),
        row(
            2,
            layouts["worktree-beside"],
            "tle-checksum",
            "The TLE parser is fixed-width. Every field is positional, so a split on "
            "whitespace is wrong\non the lines where a value runs into its neighbour.",
            95,
            bookmarked=True,
        ),
        row(3, layouts["gone"], "removed-last-week", "profile the hot loop", 60 * 26),
        row(4, None, None, "a transcript that recorded no directory", 60 * 24 * 9),
        row(5, layouts["relinked"], "tle-epoch", "read the epoch as a two-digit year", 60 * 5),
        *running,
    )


def _live_root(state: Any) -> str | None:
    """
    The id of a session the application is running, by the rule ``app._live_sessions``
    marks one: a root that has not ended. None before the fleet is seeded.
    """
    from pptmstr import app as pptmstr_app

    return next(iter(sorted(pptmstr_app._live_sessions(state.frame_snap))), None)


def _open_launcher(state: Any) -> Steps:
    state.launcher.request_open()
    yield from _until("the launcher to open", lambda: state.launcher.is_open)
    yield from _frames()


def _close_launcher(state: Any) -> Steps:
    from imgui_bundle import imgui

    yield from _press(imgui.Key.escape)
    yield from _until("the launcher to close", lambda: not state.launcher.is_open)
    yield from _frames(3)


def _resume(state: Any, fixtures: Path) -> Steps:
    """
    The resume section: the list, and the line under the header for each kind of
    picked row.
    """
    from imgui_bundle import imgui

    from pptmstr.ui import launcher

    layouts = _layouts(fixtures)
    # The live row is live by the application's own reading of its store, which is
    # what hands ``launcher.draw`` its ``live`` set: nothing here says which row is.
    yield from _until("a running session in the fake fleet", lambda: _live_root(state) is not None)
    rows = _session_rows(layouts, live=_live_root(state))
    here, elsewhere, gone, unrecorded, relinked, running = rows
    launcher.draw = functools.partial(  # type: ignore[assignment]
        launcher.draw, scanner=lambda cwd: (rows, {})
    )
    draft = state.launcher
    draft.cwd = layouts["repo-root"]
    yield from _open_launcher(state)
    yield from _until("the session scan", lambda: bool(draft.picker.rows))
    yield "01-launcher-as-opened"

    modal = imgui.internal.find_window_by_name(launcher.TITLE)
    _set_open(modal, launcher._RESUME_ID, True)
    yield from _frames()
    yield "02-resume-list-nothing-picked"
    for name, picked in (
        ("03-resume-picked-recorded-in-another-directory", elsewhere),
        ("04-resume-picked-recorded-in-the-field-directory", here),
        ("05-resume-picked-directory-gone", gone),
        ("06-resume-picked-no-directory-recorded", unrecorded),
        ("08-resume-picked-recorded-through-a-link", relinked),
        ("09-resume-picked-open-here-now", running),
    ):
        draft.picker.pick(None, draft.cwd)
        draft.picker.pick(picked, draft.cwd)
        yield from _frames()
        yield from _pages(modal, name)
    for name, picked in (
        ("07-resume-picked-directory-gone-header-closed", gone),
        ("09-resume-picked-open-here-now-header-closed", running),
    ):
        draft.picker.pick(None, draft.cwd)
        draft.picker.pick(picked, draft.cwd)
        _set_open(modal, launcher._RESUME_ID, False)
        yield from _frames()
        yield name


_TWO_LINE_TASK = (
    "Move the checksum out of the parser and beside it.\n"
    "A parser that validates cannot be reused on a record not yet checksummed."
)


def _containment(state: Any, fixtures: Path) -> Steps:
    """
    The top rung's section in every git layout the launcher words differently, in a
    modal that was opened on the ordinary rung first -- which is the order an
    operator meets it in, since a launch puts the dial back.
    """
    from imgui_bundle import imgui

    from pptmstr.approval import Policy
    from pptmstr.ui import launcher

    layouts = _layouts(fixtures)
    draft = state.launcher
    draft.task = _TWO_LINE_TASK
    draft.cwd = layouts["no-git"]
    yield from _open_launcher(state)
    modal = imgui.internal.find_window_by_name(launcher.TITLE)
    yield "10-gate-strict-before-the-dial-is-turned"
    draft.policy = Policy.AUTONOMOUS
    yield from _frames(3)
    yield "11-contain-no-git-while-the-cli-is-being-read"
    yield from _until("claude --version", lambda: draft.version_gate.answered, 60.0)
    yield from _frames()
    yield from _pages(modal, "12-contain-no-git-as-the-dial-left-it")

    # A cancel keeps the draft, the rung included, so the next open is the modal
    # appearing at its full height rather than growing to it. The layouts are read
    # from that one, where the whole section can be scrolled into view.
    yield from _close_launcher(state)
    yield from _open_launcher(state)
    for name, layout in (
        ("13-contain-no-git", "no-git"),
        ("14-contain-repo-root", "repo-root"),
        ("15-contain-repo-subdirectory", "repo-subdirectory"),
        ("16-contain-worktree-beside-root", "worktree-beside"),
        ("17-contain-worktree-beside-subdirectory", "worktree-beside-subdirectory"),
        ("18-contain-worktree-nested", "worktree-nested"),
        ("19-contain-worktree-of-bare", "worktree-of-bare"),
    ):
        draft.cwd = layouts[layout]
        yield from _frames()
        yield from _pages(modal, name)
    draft.cwd = "."
    yield from _frames()
    yield from _pages(modal, "20-contain-refused-own-checkout")

    yield from _close_launcher(state)
    draft.task = "Move the checksum out of the parser."
    draft.cwd = layouts["repo-root"]
    yield from _open_launcher(state)
    yield from _pages(modal, "21-contain-held-on-a-one-line-task")
    yield from _close_launcher(state)
    draft.task = _TWO_LINE_TASK
    rows = _session_rows(layouts)
    draft.picker.pick(rows[2], draft.cwd)
    yield from _open_launcher(state)
    yield from _pages(modal, "22-contain-resume-picked-directory-gone")
    draft.picker.pick(None, draft.cwd)
    draft.picker.pick(rows[1], draft.cwd)
    yield from _frames()
    yield from _pages(modal, "23-contain-resume-picked-worktree-beside")


def _refused_launch(state: Any, spec: Any, draft: Any, origin: str = "launch") -> None:
    """
    Put one launch through ``app._launch`` so that it starts nothing.

    The pool is detached for the call, which is the refusal ``_launch`` makes
    without constructing a session: no CLI is spawned and nothing is written. The
    reason on screen is the one that path gives, and ``_reap_launches`` reports it
    on the next frame as it reports any other.
    """
    from pptmstr import app as pptmstr_app

    pool, state.pool = state.pool, None
    try:
        pptmstr_app._launch(state, spec, draft, origin=origin)
    finally:
        state.pool = pool


def _failure(state: Any, fixtures: Path) -> Steps:
    """
    The notice over a launch that started nothing, through each thing it can say
    about that launch's draft.

    A press is replayed as the calls ``launcher.draw`` makes on one -- ``press``
    for the spec, the draft taken, ``launched``, the modal closed -- with
    ``_refused_launch`` standing where the callback is.
    """
    from imgui_bundle import imgui

    layouts = _layouts(fixtures)
    draft = state.launcher
    draft.task = _TWO_LINE_TASK
    draft.cwd = layouts["repo-root"]
    yield from _open_launcher(state)
    spec = draft.press()
    if spec is None:
        raise RuntimeError("the press found another rung than the one on screen")
    pressed = draft.draft()
    draft.launched()
    yield from _close_launcher(state)
    _refused_launch(state, spec, pressed)
    yield from _until("the failed launch to reopen the launcher", lambda: draft.is_open)
    yield from _frames()
    yield "30-failure-draft-loaded"

    imgui.get_io().add_input_characters_utf8(" Leave the propagator alone.")
    yield from _frames()
    yield "31-failure-typed-over-restore-offered"

    draft.launched()
    yield from _frames()
    yield "32-failure-pressed-past"

    imgui.get_io().add_input_characters_utf8(" And say which lines moved.")
    yield from _frames()
    draft.restore_failed()
    yield from _frames()
    yield "33-failure-restored-one-set-aside"

    draft.discard_failed()
    yield from _frames()
    yield "34-set-aside-only"

    _refused_launch(state, spec, None, origin="relaunch")
    _refused_launch(state, spec, pressed)
    yield from _until("two more failures", lambda: len(draft.failures) == 2)
    yield from _frames()
    yield "35-failure-not-from-this-window-one-waiting"


def _team(state: Any) -> Any:
    """
    The root of the fake fleet's one team session, or None before it is seeded.
    """
    snap = state.frame_snap
    if snap is None:
        return None
    return next((n for n, rec in snap.nodes.items() if rec.template == "feature"), None)


def _board(state: Any, fixtures: Path) -> Steps:
    """
    The board pane over finished rows in every standing a result and a not-run list
    can have and an incident, and the DIGEST pane docked with it, with the lead's
    mark part-way down it.

    Everything on the board gets there as an intent on the bridge.
    """
    from imgui_bundle import hello_imgui

    from pptmstr.intents import (
        ConcernPosted,
        DigestRead,
        TaskClaimRequested,
        TaskCompleted,
        TaskDeclared,
    )
    from pptmstr.model import Basis, Concern, Task, TaskState
    from pptmstr.ui.focus import Scope

    yield from _until("the fake team's board", lambda: _team(state) is not None)
    yield from _until("the fake board's own rows", lambda: "t5" in state.frame_snap.tasks)
    root = _team(state)
    builder = next(
        n
        for n, rec in state.frame_snap.nodes.items()
        if n[0] == root[0] and rec.agent_type == "builder"
    )
    emit = state.bridge.emit

    def finish(task: Task, **said: Any) -> None:
        emit(TaskDeclared(task, node_id=root))
        emit(TaskClaimRequested(builder, request_id=f"shot-claim-{task.id}", task_id=task.id))
        emit(TaskCompleted(builder, task.id, at=time.monotonic(), **said))

    finish(
        Task(
            id="t6",
            title="move the checksum beside the parser",
            detail=(
                "The parser validates as it reads, so it cannot be reused on a record "
                "that has not been checksummed yet. Split the two."
            ),
            # Files no other row names. A declared overlap is a derived dependency,
            # and one on t2 -- claimed for the whole run -- is a row that never
            # becomes claimable.
            touches=("tle/verify.py", "tle/columns.py"),
            declared_at=6.0,
            basis=Basis.RELAYED,
            property="parse() never raises on a line whose checksum is wrong.",
        ),
        result=(
            "verify.py holds verify(); parse.py no longer imports it. Established by "
            "reading both modules and the three call sites."
        ),
        basis=Basis.READ,
        not_run=(
            "The test suite, and the catalogue import that calls parse() on unchecked " "records."
        ),
    )
    finish(
        Task(id="t7", title="pin the column table", declared_at=7.0, basis=Basis.READ),
        result="The offsets are asserted against the format document, field by field.",
        not_run="",
    )
    finish(
        Task(id="t8", title="reject a short line", declared_at=8.0),
        result="A 68-character line is reported with its line number; 3 new tests pass.",
        basis=Basis.RUN,
    )
    finish(Task(id="t10", title="rename the fixtures directory", declared_at=10.0))
    emit(
        ConcernPosted(
            builder,
            Concern(
                id="shot-c1",
                sender=builder,
                recipient=root,
                subject="tests/test_parse.py was written outside t8's declared files",
                body=(
                    "t8 declares tle/parse.py only. The three new tests went into "
                    "tests/test_parse.py, which t3 owns and nobody holds."
                ),
                posted_at=time.monotonic(),
                task_id="t8",
                basis=Basis.RUN,
                incident=True,
            ),
        )
    )
    yield from _until(
        "the added rows to finish",
        lambda: all(
            state.frame_snap.tasks[t].state is TaskState.COMPLETED
            for t in ("t6", "t7", "t8", "t10")
            if t in state.frame_snap.tasks
        )
        and "t10" in state.frame_snap.tasks,
    )
    # The lead reads a digest, and then something else finishes: the mark is drawn
    # with entries on both sides of it.
    emit(DigestRead(root, request_id="shot-digest", at=time.monotonic()))
    yield from _frames(3)
    finish(
        Task(id="t11", title="note the split in the changelog", declared_at=11.0),
        result="One line under Unreleased.",
        basis=Basis.READ,
        not_run="",
    )
    yield from _frames(3)

    def show(label: str) -> Steps:
        hello_imgui.get_runner_params().docking_params.focus_dockable_window(label)
        state.focus.to_node(state.frame_snap, root, scope=Scope.SESSION)
        yield from _frames()

    def show_board() -> Steps:
        yield from show("BOARD")

    def digest(name: str) -> Steps:
        # A tab of its own in BOARD's space: brought to the front, shot, and the
        # board put back in front for the shots after it.
        yield from show("DIGEST")
        yield from _pages(_window("DIGEST/##digest"), name)
        yield from show_board()

    def opened(*labels: str) -> Steps:
        pane = _window("BOARD/##board")
        for label in ("t1", "t2", "t5", "t6", "t7", "t8", "t10", "t11"):
            _set_open(pane, f"###{label}", label in labels)
        yield from _frames()

    yield from show_board()
    yield from _pages(_window("BOARD/##board"), "40-board-triage-every-row-closed")
    yield from digest("41-digest-triage")
    yield from opened("t1", "t6")
    yield from _pages(_window("BOARD/##board"), "42-board-triage-t1-t6-open")
    yield from opened("t7", "t8", "t10")
    yield from _pages(_window("BOARD/##board"), "43-board-triage-t7-t8-t10-open")
    yield from opened("t2")
    yield "44-board-triage-t2-open-with-its-concern"
    state.board.editing = "t6"
    yield from opened("t6")
    yield "45-board-triage-t6-amend-editor"
    state.board.editing = None

    yield from opened()
    state.pending_layout = "FOCUS"
    yield from _frames()
    yield from show_board()
    yield from _pages(_window("BOARD/##board"), "46-board-focus-every-row-closed")
    yield from opened("t6", "t7")
    yield from _pages(_window("BOARD/##board"), "47-board-focus-t6-t7-open")
    yield from digest("48-digest-focus")


SCENARIOS: dict[str, Callable[[Any, Path], Steps]] = {
    "resume": _resume,
    "containment": _containment,
    "failure": _failure,
    "board": _board,
}


if __name__ == "__main__":
    raise SystemExit(main())
