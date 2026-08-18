"""
Starting a session: a modal invoked from anywhere, not a pane that is always there.

This replaces the omnibox that lived along the bottom of TRIAGE. Two things were
wrong with that arrangement, and only the second is about screen space.

The first is conceptual. The unit this application deals in is *intent per
session* -- a task, the directory it runs in, the model that runs it. An
always-present strip implies the opposite: that dispatch is an ambient property of
one arrangement of the screen. It was also absent from FOCUS entirely, so starting
work while attending to a running session meant leaving the session first. A modal
is reachable identically from both arrangements, which is what a per-session intent
actually needs.

The second is that one line of chrome bought a cramped single-line task field. The
prompt is the part of a launch worth the most care and it had the least room.

Imports imgui only for ``draw``; ``LauncherState`` and its decisions are pure, so
the parts worth testing do not need a GL context.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from .. import templates
from ..model import LaunchSpec
from ..theme import P
from . import widgets

# Verified against the model-config docs at build time (design §7, trap 9). Listed
# rather than free-text so a typo cannot become a session that fails on first turn.
MODELS: tuple[str, ...] = (
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
)

TITLE = "New Task"

# Ctrl+N. Key members are plain ints here and do not support ``|`` as enums, so the
# chord is built from int() -- ``imgui.Key.mod_ctrl | imgui.Key.n`` raises.
CHORD = int(imgui.Key.mod_ctrl) | int(imgui.Key.n)

_WIDTH = 620.0
_TASK_HEIGHT = 132.0


@dataclass
class LauncherState:
    """
    Draft intent for a session that does not exist yet.

    The draft survives a cancel on purpose: dismissing the modal to go look up a
    directory should not cost the paragraph already typed. It is cleared on launch,
    which is the only point at which the intent has gone somewhere.
    """

    task: str = ""
    cwd: str = "."
    # Path to a directory of premises, or empty for a session with only its task.
    # Optional on purpose: most sessions are solo with a one-line task, and making
    # a brief mandatory would tax the common case for a problem it does not have.
    brief: str = ""
    model_index: int = 0
    # Index into templates.names(). "solo" is first and is the default, so the
    # launcher behaves exactly as it did before teams existed unless asked otherwise.
    template_index: int = 0
    # True from the frame the modal is drawn until the frame it stops being drawn.
    # Read by the global key handler, which runs before any drawing and therefore
    # sees the previous frame's value -- which is the correct one, because the key
    # it is deciding about was pressed while that frame was on screen.
    is_open: bool = False
    _open_requested: bool = False
    _focus_task: bool = False

    def request_open(self) -> None:
        """Ask for the modal next time it draws. Safe to call when already open."""
        if not self.is_open:
            self._open_requested = True

    @property
    def ready(self) -> bool:
        return bool(self.task.strip())

    def spec(self) -> LaunchSpec:
        """
        The draft as the value the driver is handed. Empty cwd means the repo root.

        A ``LaunchSpec`` rather than a tuple of strings, which is the correction row
        9 paid for: the callback took four loose positionals, so dropping one still
        typechecked and a relaunched team started solo. Adding the brief to a
        positional list would be the same defect waiting on the same signature.
        """
        return LaunchSpec(
            task=self.task.strip(),
            model=MODELS[self.model_index],
            cwd=self.cwd.strip() or ".",
            template=templates.names()[self.template_index],
            brief=self.brief.strip() or None,
        )


def handle_shortcut(state: LauncherState) -> None:
    """
    Ctrl+N, from either layout and from inside a text field.

    ``route_over_active`` is the load-bearing flag. The other keyboard handlers in
    this application bail on ``want_capture_keyboard``, which is right for bare
    letter keys -- typing "a" into a rejection reason must not approve anything --
    but it would make this shortcut dead in exactly the place it is most wanted,
    the reply box of a session that just prompted the operator for something new.

    Must be called inside a frame, which is why it does not live with the other
    shortcut handling in ``begin_frame``.
    """
    if imgui.shortcut(CHORD, imgui.InputFlags_.route_global | imgui.InputFlags_.route_over_active):
        state.request_open()


def draw(
    state: LauncherState,
    *,
    running: int,
    queued: int,
    cap: int,
    launch: Callable[[LaunchSpec], None],
    wrap: bool,
) -> None:
    """
    Draw the modal if it has been asked for.

    Belongs at root level -- ``post_render_dockable_windows`` -- rather than inside
    a panel. A popup opened from a docked window is scoped to that window, and the
    panel set differs between the two layouts, so a launcher parented to a TRIAGE
    pane would vanish on the switch to FOCUS.
    """
    if state._open_requested:
        state._open_requested = False
        state.is_open = True
        state._focus_task = True
        imgui.open_popup(TITLE)

    if not state.is_open:
        return

    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size(imgui.ImVec2(_WIDTH, 0.0), imgui.Cond_.appearing)
    opened, _ = imgui.begin_popup_modal(TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        # Dismissed by something other than this function -- a click on the blocked
        # background, or ImGui's own Escape handling. Resync so the key handler
        # stops deferring to a modal that is gone.
        state.is_open = False
        return

    if state._focus_task:
        imgui.set_keyboard_focus_here()
        state._focus_task = False

    # The same chord the reply boxes use: Ctrl+Enter launches, Enter breaks the
    # line. Sharing it is the point -- a task box and a reply box are both prompts,
    # and the binding that differed here was the one whose misfire costs the most,
    # since a stray Enter mid-sentence spawns a session on half a sentence.
    # escape_clears_all is deliberately absent on top of it: it would eat the key
    # that dismisses the modal.
    submitted, state.task = widgets.multiline_input(
        "##task",
        state.task,
        imgui.ImVec2(-1, _TASK_HEIGHT),
        wrap=wrap,
        flags=widgets.CTRL_ENTER_SUBMITS,
    )
    imgui.text_disabled("what should a new session do?  Ctrl+Enter launches, Enter for a new line")

    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    # The working directory is the field worth the label. An orchestrator whose
    # agents all share the launching shell's cwd cannot run work across projects,
    # which is most of the point -- and it is the FLEET rail's grouping key, so a
    # typo does not fail, it quietly files the session under a project that does
    # not exist.
    imgui.set_next_item_width(-1)
    _, state.cwd = imgui.input_text_with_hint(
        "##cwd", "working directory (agents run here)", state.cwd
    )
    imgui.text_disabled("working directory")

    # Under the working directory because it is the same kind of field -- a path the
    # operator supplies and nothing validates -- and because a brief is read
    # relative to nothing: it is an absolute directory, outside the tree, which the
    # 08-17 probe measured a worker can read.
    #
    # Empty is the common case and must stay cheap to leave empty. Most sessions are
    # solo with a one-line task, and a brief is the shape a team needs.
    imgui.spacing()
    imgui.set_next_item_width(-1)
    _, state.brief = imgui.input_text_with_hint(
        "##brief", "brief directory (optional, premises the team can read)", state.brief
    )
    imgui.text_disabled("brief")

    imgui.spacing()
    imgui.set_next_item_width(240.0)
    _, state.model_index = imgui.combo("model", state.model_index, list(MODELS))
    _, state.template_index = imgui.combo("team", state.template_index, list(templates.names()))
    # The shape is not obvious from a one-word name, and picking the wrong one is
    # only visible several turns later when workers start appearing -- so the
    # description is on screen rather than a tooltip away.
    imgui.text_disabled(templates.BUILT_IN[state.template_index].description)

    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    ready = state.ready
    if not ready:
        imgui.begin_disabled()
    clicked = imgui.button("launch", imgui.ImVec2(110.0, 0.0))
    if not ready:
        imgui.end_disabled()

    imgui.same_line()
    cancelled = imgui.button("cancel", imgui.ImVec2(90.0, 0.0))

    # Only at cap, and only as a consequence. The running/cap counter itself is in
    # the status bar, which stays visible behind the modal -- repeating it here
    # would be the same duplication the omnibox was carrying. What the status bar
    # cannot say is what happens to *this* launch, and a session that sits in
    # SPAWNING while others work looks broken to whoever just pressed the button.
    if running >= cap:
        imgui.same_line()
        imgui.text_colored(P.warn.vec4, f"at cap - this one will queue ({queued} ahead of it)")

    # Escape is handled explicitly rather than left to ImGui so that the precedence
    # against the layout shortcuts is stated in one place instead of split between
    # this and NavUpdate's behaviour.
    dismissed = cancelled or imgui.is_key_pressed(imgui.Key.escape)

    if ready and (clicked or submitted):
        launch(state.spec())
        state.task = ""
        dismissed = True

    if dismissed:
        state.is_open = False
        imgui.close_current_popup()

    imgui.end_popup()
