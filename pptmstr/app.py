"""
Application shell: runner setup, docking layout, and the frame loop (design §4.1).

The frame loop is the whole architecture in eight lines -- drain intents, apply
them, take exactly one snapshot, build from it, set the idle predicate. Everything
else in this package exists to keep those five steps honest.
"""

from __future__ import annotations

import argparse
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field

from imgui_bundle import hello_imgui, imgui, immapp

from . import settings as settings_mod
from . import theme
from .bridge import Bridge
from .driver import AgentSession
from .fake_driver import FakeDriver
from .log import LOG
from .model import Snapshot
from .pool import SessionPool
from .store import Store
from .theme import REQUIRED_THEMES, THEMES, P
from .ui import review, transcript_pane, tree


@dataclass
class AppState:
    """
    Everything the callbacks need. Not application state in the store's sense -- it
    is the wiring, plus the presentation state the store deliberately does not own.
    """

    store: Store
    bridge: Bridge
    settings: settings_mod.Settings
    view: tree.ViewState = field(default_factory=tree.ViewState)
    review: review.ReviewState = field(default_factory=review.ReviewState)
    transcripts: transcript_pane.TranscriptState = field(
        default_factory=transcript_pane.TranscriptState
    )
    frame: int = 0
    # Set when the palette changes; consumed at the top of the next frame. See
    # _apply_theme_if_dirty for why this cannot simply happen in post_init.
    theme_dirty: bool = True
    runner: hello_imgui.RunnerParams | None = None
    # The snapshot for the frame currently being built. Read once in pre_new_frame
    # and handed to every panel, so two panels can never disagree about the world.
    frame_snap: Snapshot | None = None
    frame_now: float = 0.0
    # When the parked-future count first exceeded what the queue is showing.
    # None while they agree. See _check_for_lost_approvals.
    lost_since: float | None = None
    lost_reported: bool = False
    driver: FakeDriver | None = None
    pool: SessionPool | None = None


def begin_frame(state: AppState) -> None:
    """
    Runs before any panel draws.

    Order is the invariant: intents are applied *before* the snapshot is taken (I4),
    and the snapshot is taken exactly once (I2). Calling store.snapshot() again in a
    panel is a bug even when it looks harmless, because the two results can differ
    and the frame then renders torn state.
    """
    state.frame += 1
    state.frame_now = time.monotonic()

    _apply_theme_if_dirty(state)
    state.store.apply_all(state.bridge.drain())
    state.frame_snap = state.store.snapshot()
    state.view.prune(state.frame)
    state.view.ensure_selection(state.frame_snap)
    state.transcripts.prune(state.frame)
    # Shortcuts are handled once per frame, before any panel draws, so they behave
    # the same whichever pane happens to have focus.
    review.handle_keys(state.frame_snap, state.review, state.bridge)
    _check_for_lost_approvals(state)


# Last error each panel reported, so a panel failing every frame logs once rather
# than 60 times a second and flushes the ring buffer of everything useful.
_PANEL_ERRORS: dict[str, str] = {}


def guarded(name: str, draw: Callable[[], None]) -> Callable[[], None]:
    """
    Wrap a panel so a bug in it cannot take the application down with it.

    This matters more here than in most UIs. The window owns every live agent
    session and every parked approval, so an unhandled exception in one panel's
    draw code destroys work that is not recoverable -- an operator loses running
    agents to a rendering bug in a pane they were not even looking at.

    Safe only because ImGui error recovery is enabled in post_init. Catching
    mid-draw leaves the ImGui stack unbalanced (a begin_child with no end_child),
    and with recovery asserts on that aborts the process -- trading one crash for
    another. With recovery on, ImGui unwinds the stack itself and logs what was
    left open. Verified before relying on it.
    """

    def wrapper() -> None:
        try:
            draw()
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if _PANEL_ERRORS.get(name) != detail:
                _PANEL_ERRORS[name] = detail
                LOG.error("ui", f"{name}: {detail}")
                LOG.debug("ui", traceback.format_exc())
            imgui.text_colored(P.danger.vec4, f"{name} failed to draw")
            imgui.text_wrapped(detail)
            imgui.text_disabled("the rest of the app, and every running agent, is unaffected")

    return wrapper


# How long the counts may disagree before it is treated as a lost approval rather
# than the ordinary lag between a gate parking and the UI applying its intent.
_LOST_APPROVAL_GRACE_S = 3.0


def _check_for_lost_approvals(state: AppState) -> None:
    """
    Every parked agent must be visible as something the operator can answer.

    The Bridge knows how many agents are blocked on a future; the store knows how
    many approvals are on screen. They should agree. When the Bridge is holding
    more than the queue shows, an agent is blocked behind an approval nobody can
    reach -- which is a permanent hang that looks like the agent simply stopped.

    Worth checking rather than trusting, because every way this can happen is a
    silent one: an intent for a node the store never learned about, a dropped
    emit, an ordering assumption that does not hold. The failure has no natural
    symptom, so it needs an unnatural one.

    Debounced: parking registers the future before the intent reaches the store,
    so the counts legitimately disagree for a frame or two.
    """
    snap = state.frame_snap
    if snap is None:
        return
    parked = state.bridge.parked_count
    visible = len(snap.review_queue)

    if parked <= visible:
        state.lost_since = None
        state.lost_reported = False
        return

    if state.lost_since is None:
        state.lost_since = state.frame_now
        return
    if state.frame_now - state.lost_since < _LOST_APPROVAL_GRACE_S:
        return
    if not state.lost_reported:
        state.lost_reported = True
        LOG.error(
            "gate",
            f"{parked - visible} agent(s) blocked on an approval that is not in the "
            "queue - they cannot be answered and will hang until the hook times out",
        )


def _apply_theme_if_dirty(state: AppState) -> None:
    """
    Push the palette into ImGuiStyle, at most once per theme change.

    This cannot live in ``post_init``, which is the obvious home for it.
    hello_imgui applies its own built-in theme (``tweaked_theme``, defaulting to
    darcula_darker) as part of runner setup, and there is no "none" among the
    options -- so a style set in post_init is overwritten before the first frame
    and the app silently wears hello_imgui's theme instead of ours. Doing it at the
    top of a frame lands after that.

    Still a per-change operation, not a per-frame one: ImGuiStyle persists across
    frames, and rewriting fifty colours every frame to achieve nothing would be a
    real cost on a path that runs at 60fps.
    """
    if not state.theme_dirty:
        return
    theme.apply_style()
    if state.runner is not None:
        state.runner.imgui_window_params.background_color = P.bg.vec4
    state.theme_dirty = False


def _menus(state: AppState) -> None:
    if imgui.begin_menu("Theme"):
        for name, palette in THEMES.items():
            required = name in REQUIRED_THEMES
            label = palette.display_name if required else f"{palette.display_name} *"
            clicked, _ = imgui.menu_item(label, "", state.settings.theme == name)
            if clicked:
                _switch_theme(state, name)
        imgui.separator()
        imgui.text_disabled("* discretionary")
        imgui.end_menu()


def _switch_theme(state: AppState, name: str) -> None:
    theme.set_theme(name)
    state.theme_dirty = True
    state.settings = state.settings.merged(theme=name)
    settings_mod.save(state.settings)
    LOG.info("theme", f"switched to {name}")


def _status_bar(state: AppState) -> None:
    snap = state.frame_snap
    if snap is None:
        return
    pending = len(snap.review_queue)
    total = len(snap.nodes)
    active = snap.any_active

    imgui.text_disabled(f"{total} agents")
    imgui.same_line()
    imgui.text_disabled("|")
    imgui.same_line()
    if pending:
        # The one number worth colouring in the status bar: it is the only thing here
        # that is waiting on the operator rather than reporting on the machine.
        imgui.text_colored(P.state_awaiting.vec4, f"{pending} awaiting review")
    else:
        imgui.text_disabled("nothing awaiting review")
    imgui.same_line()
    imgui.text_disabled("|")
    imgui.same_line()
    imgui.text_disabled("running" if active else f"idle @ {state.settings.fps_idle:g}fps")
    if state.lost_reported:
        # Loud on purpose: this means an agent is stuck somewhere the operator
        # cannot reach it, and the whole point is that it has no other symptom.
        imgui.same_line()
        imgui.text_colored(
            P.danger.vec4,
            f"| {state.bridge.parked_count - len(snap.review_queue)} blocked, not in queue",
        )
    if state.pool is not None:
        imgui.same_line()
        imgui.text_disabled("|")
        imgui.same_line()
        queued = state.pool.queued_count
        text = f"{state.pool.running_count}/{state.pool.cap} sessions"
        if queued:
            text += f", {queued} queued"
        imgui.text_disabled(text)


def _docking_params(state: AppState) -> hello_imgui.DockingParams:
    """
    Splits are declared parent-first: each carves a new named space off one that
    already exists, so RightSpace has to be created before it can be split again.
    """
    params = hello_imgui.DockingParams()
    params.layout_name = "pptmstr"

    def split(
        initial: str, new: str, direction: imgui.Dir, ratio: float
    ) -> hello_imgui.DockingSplit:
        s = hello_imgui.DockingSplit()
        s.initial_dock = initial
        s.new_dock = new
        s.direction = direction
        s.ratio = ratio
        return s

    d = imgui.Dir
    params.docking_splits = [
        split("MainDockSpace", "RightSpace", d.right, 0.36),
        split("MainDockSpace", "BottomSpace", d.down, 0.34),
    ]

    def window(label: str, dock: str, fn: Callable[[], None]) -> hello_imgui.DockableWindow:
        w = hello_imgui.DockableWindow()
        w.label = label
        w.dock_space_name = dock
        w.gui_function = fn
        return w

    def draw_agents() -> None:
        # frame_snap is set in pre_new_frame, which always runs before a panel
        # draws. Guarded anyway rather than asserted: a panel is not the place to
        # discover a callback-ordering change.
        if state.frame_snap is not None:
            tree.draw(state.frame_snap, state.view, state.frame_now)

    def draw_queue() -> None:
        if state.frame_snap is not None:
            review.draw_queue(state.frame_snap, state.review, state.bridge)

    def draw_review_detail() -> None:
        if state.frame_snap is not None:
            review.draw_detail(state.frame_snap, state.review, state.bridge)

    def draw_transcript() -> None:
        if state.frame_snap is not None:
            transcript_pane.draw(state.frame_snap, state.transcripts, state.view.selected)

    params.dockable_windows = [
        window("AGENTS", "MainDockSpace", guarded("AGENTS", draw_agents)),
        window("REVIEW", "BottomSpace", guarded("REVIEW", draw_queue)),
        window("DETAIL", "RightSpace", guarded("DETAIL", draw_review_detail)),
        window("TRANSCRIPT", "RightSpace", guarded("TRANSCRIPT", draw_transcript)),
        window("LOG", "BottomSpace", guarded("LOG", _log_panel)),
    ]
    return params


def _log_panel() -> None:
    """Orchestrator diagnostics. Agent output belongs in that agent's transcript."""
    entries, _ = LOG.snapshot()
    for entry in entries[-400:]:
        colour = {
            "DEBUG": P.text_dim,
            "INFO": P.text_dim,
            "WARN": P.warn,
            "ERROR": P.danger,
        }[entry.level.name]
        imgui.text_colored(colour.vec4, f"[{entry.source}] {entry.text}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pptmstr - multi-agent orchestrator")
    ap.add_argument("--theme", default=None, help="override the persisted theme")
    ap.add_argument("--fake", action="store_true", help="run the fake driver (no SDK, no cost)")
    ap.add_argument(
        "--task",
        action="append",
        default=None,
        help="run a real agent on this task; repeat for concurrent sessions",
    )
    ap.add_argument("--model", default=None, help="model for --task")
    ap.add_argument("--cap", type=int, default=None, help="max concurrent sessions")
    ap.add_argument("--fps-idle", type=float, default=None)
    args = ap.parse_args(argv)

    loaded = settings_mod.load()
    if args.theme:
        loaded = loaded.merged(theme=args.theme)
    if args.fps_idle is not None:
        loaded = loaded.merged(fps_idle=args.fps_idle)
    if args.cap is not None:
        loaded = loaded.merged(concurrency_cap=args.cap)
    theme.set_theme(loaded.theme)

    state = AppState(store=Store(), bridge=Bridge(), settings=loaded)

    runner = hello_imgui.RunnerParams()
    runner.app_window_params.window_title = "pptmstr"
    runner.app_window_params.window_geometry.size = (1500, 900)
    runner.app_window_params.restore_previous_geometry = True
    runner.imgui_window_params.show_menu_bar = True
    runner.imgui_window_params.show_menu_app = False
    runner.imgui_window_params.show_menu_view = True
    runner.imgui_window_params.show_menu_view_themes = False
    runner.imgui_window_params.show_status_bar = True
    runner.imgui_window_params.show_status_fps = False
    runner.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    )
    runner.imgui_window_params.background_color = P.bg.vec4
    runner.docking_params = _docking_params(state)
    state.runner = runner
    runner.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder

    runner.fps_idling.enable_idling = True
    runner.fps_idling.fps_idle = state.settings.fps_idle

    def post_init() -> None:
        # Recover from an unbalanced ImGui stack instead of aborting the process.
        # This is what makes `guarded` safe: a panel that raises mid-draw leaves a
        # child or table open, and the default behaviour is to assert and die.
        io = imgui.get_io()
        io.config_error_recovery = True
        io.config_error_recovery_enable_assert = False
        io.config_error_recovery_enable_debug_log = True
        io.config_error_recovery_enable_tooltip = False
        LOG.info("app", f"theme {state.settings.theme}")

    def pre_frame() -> None:
        begin_frame(state)
        # Full speed while anything is genuinely working; idle while everything is
        # done or parked on the operator. AWAITING_APPROVAL counts as idle, which is
        # I8 showing up as a CPU number rather than a claim.
        snap = state.frame_snap
        runner.fps_idling.enable_idling = not (snap is not None and snap.any_active)

    runner.callbacks.load_additional_fonts = theme.load_fonts
    runner.callbacks.post_init = post_init
    # Guarded too: an exception here would stop intents being applied, which is
    # bad, but killing the window loses every session, which is worse.
    runner.callbacks.pre_new_frame = guarded("frame", pre_frame)
    runner.callbacks.show_menus = guarded("menu", lambda: _menus(state))
    runner.callbacks.show_status = guarded("status bar", lambda: _status_bar(state))

    state.bridge.start()
    if args.fake:
        state.driver = FakeDriver(state.bridge)
        state.bridge.submit(state.driver.run())
    if args.task:
        pool = SessionPool(state.bridge, cap=state.settings.concurrency_cap)
        state.pool = pool

        async def launch() -> None:
            # Submitting from the loop thread keeps every mutation of the pool on
            # one thread, so it needs no lock of its own.
            for task_text in args.task:
                pool.submit(AgentSession(state.bridge, task_text, model=args.model))

        state.bridge.submit(launch())
        LOG.info("app", f"{len(args.task)} task(s), cap {state.settings.concurrency_cap}")

    try:
        immapp.run(runner)
    finally:
        if state.driver is not None:
            state.driver.stop()
        if state.pool is not None:
            # Cancel sessions before the loop dies, so no CLI subprocess outlives
            # the window that was supervising it.
            state.bridge.submit(state.pool.shutdown()).result(timeout=10)
        state.bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
