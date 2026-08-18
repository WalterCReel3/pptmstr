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
from . import templates, theme
from .bridge import Bridge
from .driver import AgentSession
from .fake_driver import FakeDriver
from .intents import FailureAcknowledged
from .log import LOG
from .model import Snapshot
from .pool import SessionPool
from .store import Store
from .theme import REQUIRED_THEMES, THEMES, P
from .ui import board_pane, compose, detail, health, inbox, launcher, rail, review, transcript_pane
from .ui import focus as focus_mod
from .ui.widgets import format_elapsed

# The two arrangements of C. Same panels in both; the rail is in the same place in
# each, and the cursor survives the switch. That shared anchor is the whole
# mitigation for mode disorientation -- without it this is worse than either
# arrangement alone.
TRIAGE = "TRIAGE"
FOCUS = "FOCUS"


@dataclass
class AppState:
    """
    Everything the callbacks need. Not application state in the store's sense -- it
    is the wiring, plus the presentation state the store deliberately does not own.
    """

    store: Store
    bridge: Bridge
    settings: settings_mod.Settings
    # One cursor. It is either on an obligation or on a node, and whichever it is
    # on, the other is derived -- see ui/focus.py for why two were unfixable.
    focus: focus_mod.FocusState = field(default_factory=focus_mod.FocusState)
    review: review.ReviewState = field(default_factory=review.ReviewState)
    detail_pane_state: detail.DetailState = field(default_factory=detail.DetailState)
    compose: compose.ComposeState = field(default_factory=compose.ComposeState)
    launcher: launcher.LauncherState = field(default_factory=launcher.LauncherState)
    rail: rail.RailState = field(default_factory=rail.RailState)
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
    # Same idea for the bus's crossing. See _check_for_stranded_requests.
    stranded_since: float | None = None
    stranded_reported: bool = False
    driver: FakeDriver | None = None
    pool: SessionPool | None = None
    # Layout asked for on the command line, applied once the runner is up.
    # switch_layout needs a live runner, so it cannot happen during setup.
    pending_layout: str | None = None


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
    if state.pending_layout is not None:
        hello_imgui.switch_layout(state.pending_layout)
        state.pending_layout = None
    # One clock for the whole batch, the same one the frame is built against, so a
    # node's state_since and the wait times drawn from it agree to the frame.
    effects = state.store.apply_all(state.bridge.drain(), now=state.frame_now)
    # Answered here, in the same breath as the apply that produced them. Every
    # effect corresponds to an intent this batch applied, so nothing is replied to
    # speculatively and nothing survives the frame -- which is what keeps a bus
    # request from becoming a second kind of lost approval.
    for effect in effects:
        state.bridge.settle(effect)
    state.frame_snap = state.store.snapshot()
    state.transcripts.prune(state.frame)
    # Before anything draws, so no pane can render a cursor pointing at an
    # obligation that was answered between frames.
    state.focus.settle(state.frame_snap)
    # Shortcuts are handled once per frame, before any panel draws, so they behave
    # the same whichever pane happens to have focus.
    review.handle_keys(state.frame_snap, state.focus, state.review, state.bridge)
    _handle_layout_keys(state)
    _check_for_lost_approvals(state)
    _check_for_stranded_requests(state)


def _handle_layout_keys(state: AppState) -> None:
    """
    Tab triages, Enter focuses, Esc returns.

    Esc is shared with "cancel the edit I am in the middle of", and the edit wins:
    yanking the layout out from under someone mid-diff is the failure mode this
    whole two-mode design has to avoid, and an operator pressing Esc over an open
    argument editor means the editor.

    The launcher modal wins over both. ``want_capture_keyboard`` covers the usual
    case -- its task box has the keyboard -- but not a modal whose focus sits on the
    model combo, where Esc would otherwise dismiss the modal *and* change layout in
    the same frame. ``is_open`` here is last frame's value, which is the one that
    matches the frame the operator was looking at when they pressed the key.
    """
    snap = state.frame_snap
    if snap is None or state.launcher.is_open or imgui.get_io().want_capture_keyboard:
        return
    current = hello_imgui.current_layout_name()
    if imgui.is_key_pressed(imgui.Key.tab) and current != TRIAGE:
        hello_imgui.switch_layout(TRIAGE)
    elif imgui.is_key_pressed(imgui.Key.enter) and current != FOCUS:
        hello_imgui.switch_layout(FOCUS)
    elif (
        imgui.is_key_pressed(imgui.Key.escape) and current == FOCUS and state.review.editing is None
    ):
        hello_imgui.switch_layout(TRIAGE)


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

# A bus request is answered by the frame that applies its intent, so it should be
# outstanding for a fraction of one frame. This is generous by three orders of
# magnitude on purpose: it is a wedge detector, not a latency budget.
_STRANDED_REQUEST_GRACE_S = 5.0


def _check_for_stranded_requests(state: AppState) -> None:
    """
    A bus request that outlives several frames was dropped.

    Unlike a parked approval, this has no surface at all -- an agent awaiting
    ``claim_task`` is not an obligation and appears nowhere in ``needs_you``, by
    design. So there is nothing on screen that would look wrong.

    Worth a watchdog because the store commits the domain change at *apply* time
    while the answer travels separately: a lost effect leaves the board reading
    "claimed" while the agent that claimed it was eventually told, at shutdown,
    that there was nothing to claim. The two disagree and neither side complains.

    Found by the `research` template reviewing this codebase, which is the first
    thing the team feature has been used for.
    """
    outstanding = state.bridge.asking_count
    if outstanding == 0:
        state.stranded_since = None
        state.stranded_reported = False
        return
    if state.stranded_since is None:
        state.stranded_since = state.frame_now
        return
    waited = state.frame_now - state.stranded_since
    if waited >= _STRANDED_REQUEST_GRACE_S and not state.stranded_reported:
        state.stranded_reported = True
        LOG.error(
            "bus",
            f"{outstanding} bus request(s) unanswered for {waited:.0f}s - "
            "an agent is blocked on a reply the frame loop never settled",
        )


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
    # Approvals only, not the whole obligation list: this compares parked futures to
    # what the operator can reach, and a question or a crash has no future behind it.
    visible = len(snap.approvals)

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


def _launch(state: AppState, task: str, model: str, cwd: str, template: str | None = None) -> None:
    """
    Start a session. Safe from the UI thread; the pool is touched on the loop.

    ``template`` None means solo, and the default is spelled here rather than at
    each call site for a reason worth the line: ``relaunch`` and ``fork`` pass
    ``AgentRecord.template``, which is None on any record that is not a session
    root, and a caller that had to remember the fallback is a caller that can
    forget it.
    """
    pool = state.pool
    if pool is None:
        return
    # An unknown name falls back to solo rather than refusing the launch: the task
    # the operator typed is worth more than the team shape they mistyped, and the
    # log line says which one ran.
    shape = (templates.by_name(template) if template else None) or templates.SOLO

    async def go() -> None:
        pool.submit(
            AgentSession(
                state.bridge,
                task,
                model=model,
                cwd=cwd,
                template=shape,
                subagent_cap=state.settings.subagent_cap,
            )
        )

    state.bridge.submit(go())
    LOG.info("app", f"launched in {cwd} as {shape.name}: {task[:60]}")


def _session_action(state: AppState, coro_factory: Callable[[SessionPool], object]) -> None:
    """Run a pool operation on the asyncio thread."""
    pool = state.pool
    if pool is None:
        return
    state.bridge.submit(coro_factory(pool))  # type: ignore[arg-type]


def _menus(state: AppState) -> None:
    if imgui.begin_menu("Session"):
        # The discoverable half of Ctrl+N. A shortcut with no menu entry is a
        # shortcut nobody who did not read the README will ever press.
        clicked, _ = imgui.menu_item("New Task...", "Ctrl+N", False)
        if clicked:
            state.launcher.request_open()
        imgui.end_menu()

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

    # Named "Text" rather than "View" -- hello_imgui owns a built-in View menu for
    # docking and layout, and a second one would be two menus with one name.
    if imgui.begin_menu("Text"):
        clicked, _ = imgui.menu_item("Wrap composers", "", state.settings.wrap_inputs)
        if clicked:
            _switch_wrap(state, not state.settings.wrap_inputs)
        imgui.end_menu()


def _switch_theme(state: AppState, name: str) -> None:
    theme.set_theme(name)
    state.theme_dirty = True
    state.settings = state.settings.merged(theme=name)
    settings_mod.save(state.settings)
    LOG.info("theme", f"switched to {name}")


def _switch_wrap(state: AppState, wrap: bool) -> None:
    """
    Toggle composer wrapping, persisting immediately.

    Saved at the point of mutation rather than on exit, as the theme is: there is no
    ``before_exit`` callback registered, and a preference that survives only a clean
    shutdown is one that silently resets after the first crash.
    """
    state.settings = state.settings.merged(wrap_inputs=wrap)
    settings_mod.save(state.settings)
    LOG.info("text", f"composer wrapping {'on' if wrap else 'off'}")


def _status_bar(state: AppState) -> None:
    snap = state.frame_snap
    if snap is None:
        return
    waiting = len(snap.needs_you)
    total = len(snap.nodes)
    active = snap.any_active

    imgui.text_disabled(f"{total} agents")
    imgui.same_line()
    imgui.text_disabled("|")
    imgui.same_line()
    if waiting:
        # The one number worth colouring here: it is the only thing in this bar that
        # is waiting on the operator rather than reporting on the machine.
        #
        # It counts obligations, not approvals. Counting approvals is what let this
        # read "nothing awaiting review" while a session sat on an unanswered
        # question and another had crashed -- the bar was reporting on one of the
        # three ways an agent can be blocked and implying it had checked all three.
        oldest = format_elapsed(state.frame_now - min(o.since for o in snap.needs_you))
        imgui.text_colored(P.state_awaiting.vec4, f"{waiting} need you")
        imgui.same_line()
        imgui.text_disabled(f"(oldest {oldest})")
    else:
        imgui.text_colored(P.ok.vec4, "nothing needs you")
        if hello_imgui.current_layout_name() == TRIAGE and snap.order:
            # Suggest, never switch. Yanking the layout out from under someone
            # mid-diff is the failure mode a two-mode design has to avoid, and an
            # emptying queue is exactly when the operator is most likely to be
            # still reading the thing they just answered.
            imgui.same_line()
            imgui.text_disabled("- Enter to focus a session")
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
            f"| {state.bridge.parked_count - len(snap.approvals)} blocked, not in queue",
        )
    if state.pool is not None:
        imgui.same_line()
        imgui.text_disabled("|")
        imgui.same_line()
        queued = state.pool.queued_count
        running, cap = state.pool.running_count, state.pool.cap
        text = f"{running}/{cap} sessions"
        if queued:
            text += f", {queued} queued"
        if running >= cap:
            # At cap a launch silently becomes a queue entry rather than a session.
            # Not a refusal, but the one reading of this counter the operator cannot
            # afford to skim, so it is the one reading that is not grey.
            imgui.text_colored(P.warn.vec4, text)
        else:
            imgui.text_disabled(text)


def _split(initial: str, new: str, direction: imgui.Dir, ratio: float) -> hello_imgui.DockingSplit:
    """
    Splits are declared parent-first: each carves a new named space off one that
    already exists, so a space has to be created before it can be split again.
    """
    s = hello_imgui.DockingSplit()
    s.initial_dock = initial
    s.new_dock = new
    s.direction = direction
    s.ratio = ratio
    return s


def _window(label: str, dock: str, fn: Callable[[], None]) -> hello_imgui.DockableWindow:
    w = hello_imgui.DockableWindow()
    w.label = label
    w.dock_space_name = dock
    w.gui_function = guarded(label, fn)
    return w


def _panels(state: AppState) -> dict[str, Callable[[], None]]:
    """
    Every pane's draw function, built once and shared by both layouts.

    The same objects appear in TRIAGE and FOCUS. A pane that existed twice would be
    two panes that happened to look alike, and the scroll position, the half-typed
    reply and the expanded row would all reset on every mode switch.
    """

    def rail_pane() -> None:
        # frame_snap is set in pre_new_frame, which always runs before a panel
        # draws. Guarded anyway rather than asserted: a panel is not the place to
        # discover a callback-ordering change.
        if state.frame_snap is not None:
            rail.draw(state.frame_snap, state.focus, state.rail, state.frame_now)

    def inbox_pane() -> None:
        if state.frame_snap is None or state.pool is None:
            return
        inbox.draw(
            state.frame_snap,
            state.focus,
            state.review,
            state.compose,
            state.bridge,
            inbox.InboxActions(
                send=lambda node, text: _session_action(state, lambda p: p.send(node, text)),
                interrupt=lambda node: _session_action(state, lambda p: p.interrupt(node)),
                close=lambda node: _session_action(state, lambda p: p.close(node)),
                dismiss=lambda node: state.bridge.emit(FailureAcknowledged(node)),
                relaunch=lambda task, model, cwd, template: _launch(
                    state, task, model, cwd, template
                ),
            ),
            state.frame_now,
            wrap=state.settings.wrap_inputs,
        )

    def context_pane() -> None:
        # Follows the cursor. Not independently selectable -- that second selection
        # is exactly what let an operator approve one agent's write while reading
        # another agent's transcript.
        if state.frame_snap is not None:
            transcript_pane.draw(
                state.frame_snap, state.transcripts, state.focus.node(state.frame_snap)
            )

    def detail_pane() -> None:
        # Follows the cursor, exactly as CONTEXT does, and for the same reason: a
        # pane here that could be pointed somewhere else is the old DETAIL, and the
        # old DETAIL is how an operator approved one agent's write while reading
        # another agent's diff.
        if state.frame_snap is not None:
            detail.draw(
                state.frame_snap,
                state.focus,
                state.review,
                state.detail_pane_state,
                state.frame_now,
            )

    def board_pane_fn() -> None:
        # Follows the cursor, like DETAIL and CONTEXT. A board that held still while
        # the cursor moved would need a pointer of its own, and that second pointer
        # is what this layout deleted.
        if state.frame_snap is not None:
            board_pane.draw(state.frame_snap, state.focus)

    def session_pane() -> None:
        """FOCUS: the conversation, with its composer, in one pane."""
        if state.frame_snap is None:
            return
        node = state.focus.node(state.frame_snap)
        session = (node[0], None) if node else None
        avail = imgui.get_content_region_avail().y
        if imgui.begin_child("##scrollback", imgui.ImVec2(0, max(avail - 150.0, 80.0))):
            transcript_pane.draw(state.frame_snap, state.transcripts, node)
        imgui.end_child()
        imgui.separator()
        compose.draw_conversation(
            state.frame_snap,
            state.compose,
            session,
            send=lambda n, text: _session_action(state, lambda p: p.send(n, text)),
            interrupt=lambda n: _session_action(state, lambda p: p.interrupt(n)),
            close=lambda n: _session_action(state, lambda p: p.close(n)),
            wrap=state.settings.wrap_inputs,
        )

    def health_pane() -> None:
        if state.frame_snap is None:
            return
        health.draw(
            state.frame_snap,
            state.focus.node(state.frame_snap),
            health.HealthActions(
                interrupt=lambda node: _session_action(state, lambda p: p.interrupt(node)),
                close=lambda node: _session_action(state, lambda p: p.close(node)),
                fork=lambda task, model, cwd, template: _launch(state, task, model, cwd, template),
            ),
            state.frame_now,
        )

    return {
        "FLEET": rail_pane,
        "NEEDS YOU": inbox_pane,
        "CONTEXT": context_pane,
        "DETAIL": detail_pane,
        "BOARD": board_pane_fn,
        "SESSION": session_pane,
        "HEALTH": health_pane,
        "LOG": _log_panel,
    }


def _draw_overlays(state: AppState) -> None:
    """
    Root-level drawing, after every docked window.

    The launcher lives here rather than in a panel for two reasons: a popup opened
    inside a docked window is scoped to that window, and the shortcut that opens it
    has to be evaluated inside a frame, which rules out ``begin_frame`` where the
    rest of the key handling lives.
    """
    launcher.handle_shortcut(state.launcher)
    pool = state.pool
    if pool is None:
        return
    launcher.draw(
        state.launcher,
        running=pool.running_count,
        queued=pool.queued_count,
        cap=pool.cap,
        launch=lambda task, model, cwd, team: _launch(state, task, model, cwd, team),
        wrap=state.settings.wrap_inputs,
    )


def _triage_layout(panels: dict[str, Callable[[], None]]) -> hello_imgui.DockingParams:
    """
    A: the queue is the application.

    The operator is a bottleneck by design, so the screen is the bottleneck's work
    surface. One centre pane merging approvals, questions and failures, and a rail
    that is scanned rather than browsed. Every *decision* is made at the row under
    the cursor, so no second pane can be pointed at a different agent than the one
    an ``a`` would approve -- DETAIL is a wider rendering of that same row, not a
    second selection. Dispatch is not here at all: it is Ctrl+N, from either layout,
    because intent belongs to a session rather than to an arrangement of the screen.
    """
    params = hello_imgui.DockingParams()
    params.layout_name = TRIAGE
    d = imgui.Dir
    params.docking_splits = [
        _split("MainDockSpace", "RailSpace", d.left, 0.21),
        # 0.40 starved the inbox: identity, wait and call summary all have to fit
        # between the rail and this pane, and the summary is what got cut.
        _split("MainDockSpace", "ContextSpace", d.right, 0.32),
    ]
    params.dockable_windows = [
        _window("FLEET", "RailSpace", panels["FLEET"]),
        _window("NEEDS YOU", "MainDockSpace", panels["NEEDS YOU"]),
        # First tab in the space, and deliberately in front of CONTEXT: the pane
        # answering "what exactly am I approving" outranks the one answering "what
        # was this agent doing beforehand", and the queue is what the operator is
        # here for. A tab-mate rather than a fourth split -- the 0.32 width was
        # already chosen so the inbox keeps room for identity, wait and summary.
        _window("DETAIL", "ContextSpace", panels["DETAIL"]),
        # Behind DETAIL, and that ordering is the whole of the operator's
        # constraint: the queue is what the operator is here for, and a team's
        # board is context for a decision rather than the decision. Absent in the
        # common case without any layout help -- most sessions are solo, and the
        # pane draws its own "not a team" line rather than an empty table.
        _window("BOARD", "ContextSpace", panels["BOARD"]),
        _window("CONTEXT", "ContextSpace", panels["CONTEXT"]),
        # Reachable, not resident. LOG is a debugging surface, and its old status as
        # tab-mate to the review queue -- in front of it, at that -- was precisely
        # the inversion this layout exists to correct.
        _window("LOG", "ContextSpace", panels["LOG"]),
    ]
    return params


def _focus_layout(panels: dict[str, Callable[[], None]]) -> hello_imgui.DockingParams:
    """
    B: the conversation is the application.

    The unit of work is a session and an approval is part of that conversation. The
    rail stays exactly where it is in TRIAGE, which is the shared anchor that keeps
    the mode switch from being disorienting.
    """
    params = hello_imgui.DockingParams()
    params.layout_name = FOCUS
    d = imgui.Dir
    params.docking_splits = [
        _split("MainDockSpace", "RailSpace", d.left, 0.21),
        _split("MainDockSpace", "HealthSpace", d.right, 0.26),
    ]
    params.dockable_windows = [
        _window("FLEET", "RailSpace", panels["FLEET"]),
        _window("SESSION", "MainDockSpace", panels["SESSION"]),
        _window("HEALTH", "HealthSpace", panels["HEALTH"]),
        # Behind HEALTH here, in front of CONTEXT in TRIAGE. The pane is the same
        # object either way; what differs is what the arrangement is for. FOCUS is
        # for steering one session, and its facts -- cost, cwd, context headroom --
        # are the ones worth resident space.
        _window("DETAIL", "HealthSpace", panels["DETAIL"]),
        # FOCUS is for steering one session, and a team's board is a fact about
        # that session in the same class as its cost and its context headroom --
        # which is why it sits with HEALTH here rather than with the queue.
        _window("BOARD", "HealthSpace", panels["BOARD"]),
        _window("LOG", "HealthSpace", panels["LOG"]),
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
    ap.add_argument("--cwd", default=".", help="working directory for --task sessions")
    ap.add_argument(
        "--template",
        default="solo",
        choices=templates.names(),
        help="team shape for --task sessions",
    )
    ap.add_argument("--fps-idle", type=float, default=None)
    ap.add_argument(
        "--layout",
        default=None,
        choices=(TRIAGE, FOCUS),
        help=f"start in {TRIAGE} (the queue) or {FOCUS} (one conversation)",
    )
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
    state.pool = SessionPool(state.bridge, cap=state.settings.concurrency_cap)
    state.pending_layout = args.layout

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
    panels = _panels(state)
    runner.docking_params = _triage_layout(panels)
    runner.alternative_docking_layouts = [_focus_layout(panels)]
    runner.remember_selected_alternative_layout = True
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
    runner.callbacks.post_render_dockable_windows = guarded(
        "launcher", lambda: _draw_overlays(state)
    )

    state.bridge.start()
    if args.fake:
        state.driver = FakeDriver(state.bridge)
        state.bridge.submit(state.driver.run())
    if args.task:

        async def launch_initial() -> None:
            # Submitting from the loop thread keeps every mutation of the pool on
            # one thread, so it needs no lock of its own.
            for task_text in args.task:
                _launch(
                    state,
                    task_text,
                    args.model or launcher.MODELS[0],
                    args.cwd,
                    args.template,
                )

        state.bridge.submit(launch_initial())
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
