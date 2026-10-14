"""
Small drawing helpers shared by the panes.

Nothing here owns state. Each function draws from values it is handed and returns
whatever the caller needs to react to, which is what keeps panels rebuildable from
a snapshot alone.
"""

from __future__ import annotations

import enum
import functools
import math
import os
import re
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from imgui_bundle import imgui

from ..approval import Disposition, Policy, classify
from ..model import AgentRecord, AgentState, ContextSnapshot, EndedHow, TranscriptPresence
from ..theme import STATE_GLYPH, STATE_LABEL, Color, P, dialog_colors, faded

# One representative call per class of thing an operator would ask about. The
# dispositions are asked of the real classifier rather than written down, so a rung
# that starts admitting one of these says so by itself. A prose list of what a
# policy admits is a second copy of the policy, and the copy is what goes stale --
# this file would still have been claiming egress was permitted an hour after it
# stopped being.
#
# `classify` does filesystem lookups: it places the paths a read or a write names,
# and the directory an admitted shell command runs in, against the records roots.
# So the answers are asked once per rung and kept (`_gate_answers`), which is sound
# because nothing a probe names reaches the records: each runs in `_PROBE_CWD` and
# names no path under a configuration directory. What a rung admits is then a fact
# about the classifier, and the classifier does not change while the process runs.
_GATE_PROBES: tuple[tuple[str, str, dict[str, str]], ...] = (
    ("shell reads", "Bash", {"command": "git status"}),
    ("writes", "Write", {"file_path": "/probe", "content": ""}),
    ("spawns", "Task", {"subagent_type": "builder", "description": "probe"}),
    ("the web", "WebFetch", {"url": "https://probe.invalid"}),
    ("messages", "mcp__pptmstr__post_concern", {"to": "lead", "subject": "probe"}),
    ("shell that writes", "Bash", {"command": "rm -rf /probe"}),
)


# Where the probes run: a file, so no directory is under it and no search a probe
# makes from it can contain the records. Left to this process's directory, a
# pptmstr started in the home directory would see every admitted shell read
# parked as a read of the records, and the dial would say ``PERMISSIVE`` adds
# nothing.
_PROBE_CWD = os.devnull


@functools.cache
def _gate_answers(policy: Policy) -> tuple[tuple[str, Disposition], ...]:
    return tuple(
        (label, classify(tool, arguments, policy, cwd=_PROBE_CWD))
        for label, tool, arguments in _GATE_PROBES
    )


def _gate_probe(policy: Policy, want: Disposition) -> tuple[str, ...]:
    return tuple(label for label, got in _gate_answers(policy) if got is want)


def gate_adds(policy: Policy) -> tuple[str, ...]:
    """
    What this rung lets run unattended that ``STRICT`` would have parked.

    The counterweight to the rung's name, and the reason it is this rather than a
    list of what still parks: a name is what an operator calibrates against, and
    ``PERMISSIVE`` reads far broader than the thing it names. Stating the whole of
    what it adds bounds the mode in one short line -- it is a small set, and if it
    ever stops being small that is exactly when the operator should see it grow.

    "That ``STRICT`` would have parked" is a property of the probe table rather
    than a subtraction done here: every probe parks under ``STRICT``, which is
    what makes an auto-approval under any other rung a widening by definition.
    ``test_every_probe_parks_under_the_default_rung`` is what keeps that true.
    """
    return _gate_probe(policy, Disposition.AUTO_APPROVE)


def gate_parks(policy: Policy) -> tuple[str, ...]:
    """
    Which classes of call still wait for the operator under a policy.

    Shown where there is room for it. It is the reassuring half and the less
    informative one -- under every rung built so far it is most of the list.
    """
    return _gate_probe(policy, Disposition.REQUIRE_APPROVAL)


# Ctrl+Enter sends, Enter breaks the line. One flag, not two: on a multiline box
# ImGui already treats Ctrl+Enter as the validation chord, and
# ``ctrl_enter_for_new_line`` is what *inverts* that into Enter-sends. Omitting it
# is the binding, not the absence of one (``InputTextEx``: the enter branch
# validates on ``!ctrl_enter_for_new_line && io.KeyCtrl``).
#
# It changes what the first element of ``multiline_input``'s result means: true on
# Ctrl+Enter only, never on an ordinary keystroke. A caller that stores its draft
# on that flag stores nothing until send, so callers using this mask must store the
# returned value unconditionally.
CTRL_ENTER_SUBMITS = int(imgui.InputTextFlags_.enter_returns_true)


def section(label: str) -> None:
    """
    A dim heading with a rule above it.

    Shared rather than copied: DETAIL and BOARD both divide themselves this way,
    and the panes are tab-mates, so a difference of one ``spacing()`` between them
    reads as one of the two being broken.
    """
    imgui.spacing()
    imgui.separator()
    imgui.text_colored(P.text_dim.vec4, label)
    imgui.spacing()


# A modal's spacing, in ems of the font in force when the scope is entered, each
# pair (x, y). The docks are set in ``theme.apply_style`` and are denser on every
# one of these: at the 16px body face they are item spacing (8, 5), frame padding
# (7, 4), inner spacing (4, 4) and window padding (8, 8).
#
# The window's edge is of the order of the gaps inside it -- an em at the sides, and
# above and below what a rule between two groups gets on each side
# (``_DIALOG_GROUP_EMS``): an edge much wider than the gaps it encloses frames a
# crowded form without relieving it.
DIALOG_ITEM_SPACING_EMS = (0.625, 0.5)
DIALOG_FRAME_PADDING_EMS = (0.625, 0.375)
DIALOG_INNER_SPACING_EMS = (0.5, 0.375)
DIALOG_WINDOW_PADDING_EMS = (1.0, 0.75)
# Vertical gaps inside a dialog, in ems, smallest to largest: a caption under the
# control it describes, the room between two fields of one group, and the room on
# each side of the rule between two groups. The ordinary gap between two items
# (``DIALOG_ITEM_SPACING_EMS``) sits between the first and the second.
_DIALOG_CAPTION_EMS = 0.1875
_DIALOG_FIELD_EMS = 0.75
_DIALOG_GROUP_EMS = 0.75


def _px(ems: float) -> float:
    """Whole pixels: a fractional padding blurs the frame borders drawn against it."""
    return float(round(ems * imgui.get_font_size()))


@contextmanager
def dialog_style() -> Iterator[None]:
    """
    The style every modal form is drawn under: room between items, room inside
    frames, room between a control and its label, and an edge in proportion to
    those -- and its colours, ``theme.dialog_colors``: one surface, with a child
    window showing the popup's own, and every frame a well in a second colour.

    Entered before ``begin_popup_modal``, because a window reads its padding when
    it begins, and left after ``end_popup``. A context manager so that every way
    out of a draw -- the popup not opening, an early return, an exception -- pops
    what was pushed. A push left on the stack is an assertion in Dear ImGui
    (``Missing PopStyleVar()``), and until it fires it is every dock drawn at a
    modal's density and in a modal's colours.

    Ems and not pixels, so the proportions hold at whatever size the body face is
    loaded or scaled to.
    """
    pushed = 0
    coloured = 0
    try:
        for var, (x, y) in (
            (imgui.StyleVar_.item_spacing, DIALOG_ITEM_SPACING_EMS),
            (imgui.StyleVar_.frame_padding, DIALOG_FRAME_PADDING_EMS),
            (imgui.StyleVar_.item_inner_spacing, DIALOG_INNER_SPACING_EMS),
            (imgui.StyleVar_.window_padding, DIALOG_WINDOW_PADDING_EMS),
        ):
            imgui.push_style_var(var, imgui.ImVec2(_px(x), _px(y)))
            pushed += 1
        for slot, colour in dialog_colors():
            imgui.push_style_color(slot, colour)
            coloured += 1
        yield
    finally:
        imgui.pop_style_color(coloured)
        imgui.pop_style_var(pushed)


def _advance_to(gap: float) -> None:
    """
    Leave ``gap`` pixels between the last item and the next, where the style would
    leave ``item_spacing.y``. Wider is an item of the difference, so the window's
    content bounds follow it; narrower moves the cursor back over spacing already
    added.
    """
    spacing = imgui.get_style().item_spacing
    extra = gap - spacing.y
    if extra > 0.0:
        imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(spacing.x, 0.0))
        imgui.dummy(imgui.ImVec2(0.0, extra))
        imgui.pop_style_var()
    elif extra < 0.0:
        imgui.set_cursor_pos_y(imgui.get_cursor_pos_y() + extra)


def dialog_caption() -> None:
    """
    Before the dim line that describes the control above it: closes the gap, so the
    line reads as belonging to that control and not to the one after it.
    """
    _advance_to(_px(_DIALOG_CAPTION_EMS))


def dialog_field() -> None:
    """Before a control that starts a new field within a group."""
    _advance_to(_px(_DIALOG_FIELD_EMS))


def dialog_group(*, rule: bool = True) -> None:
    """
    Between two groups of controls in a dialog -- what the task is, where it runs,
    the gate, the buttons: the same room on each side of a rule, or that room once
    with no rule.
    """
    gap = _px(_DIALOG_GROUP_EMS)
    _advance_to(gap)
    if not rule:
        return
    spacing = imgui.get_style().item_spacing
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(spacing.x, 0.0))
    imgui.separator()
    imgui.dummy(imgui.ImVec2(0.0, gap))
    imgui.pop_style_var()


# One set of widths for every modal form, in pixels: the window, a combo or a
# spinner with its label beside it, and the column a fact's name is drawn in.
DIALOG_WIDTH = 620.0
DIALOG_FIELD_WIDTH = 240.0
DIALOG_LABEL_COLUMN = 92.0


def dialog_body_room(
    work_height: float, top: float, padding: float, footer: float, floor: float
) -> float:
    """
    How tall a dialog's scrolling body may be for the buttons under it to be on
    the display: what is left of ``work_height`` after the ``top`` of the window
    (its title bar and the padding under it), the ``padding`` at its bottom and the
    ``footer`` the buttons take. Never less than ``floor``: on a display too short
    for both, a body with no height would be a window of buttons over nothing.
    """
    return max(floor, work_height - top - padding - footer)


def begin_dialog_body(body_id: str, footer: float, floor: float) -> None:
    """
    Begin the child a dialog's fields are drawn in: as tall as what is drawn in
    it, up to ``dialog_body_room``, and scrolling past that. The buttons are items
    of the window itself, drawn after ``imgui.end_child``, so they stay where they
    are and on the display however tall the body's content is.

    ``footer`` is how tall the buttons were on the last frame they were drawn, or
    zero before the first, which is taken as one row of them.

    The height to fit in is the one auto-resize fits the window to: the work area
    less the style's safe-area margin at each end (measured on Dear ImGui 1.92.8:
    694 of a 700-pixel area). Bounded to the whole area instead, the window is
    that margin short of its content and scrolls by it, buttons included.
    """
    style = imgui.get_style()
    viewport = imgui.get_main_viewport()
    room = dialog_body_room(
        viewport.work_size.y - 2.0 * style.display_safe_area_padding.y,
        imgui.get_cursor_pos().y,
        style.window_padding.y,
        footer or imgui.get_frame_height_with_spacing() + style.item_spacing.y,
        floor,
    )
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(0.0, 0.0), imgui.ImVec2(imgui.FLT_MAX, room)
    )
    imgui.begin_child(body_id, imgui.ImVec2(0.0, 0.0), imgui.ChildFlags_.auto_resize_y)


# -- one line, and the explanation behind a question mark --------------------------


class Tone(enum.Enum):
    """How one line under a control is drawn."""

    # A caption: dim.
    QUIET = "quiet"
    # Something is not as the control above would lead an operator to expect.
    WARN = "warn"
    # Something did not happen, or cannot.
    DANGER = "danger"
    # The one line that is an alarm: the alarm colour on a band of it. For a
    # session that will act without asking, and for nothing else -- a second
    # use is what stops it reading as an alarm.
    LOUD = "loud"


@dataclass(frozen=True, slots=True)
class Help:
    """
    An explanation an operator asks for: ``title`` is what it explains and
    ``paragraphs`` the explanation. ``mark`` tells this one's question mark from
    the others in the same window.
    """

    mark: str
    title: str
    paragraphs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Notice:
    """
    One line a dialog says without being asked, and the explanation behind its
    question mark, if it has one. A value, so what a dialog says in a given state
    can be listed and tested without a window.
    """

    line: str
    tone: Tone = Tone.QUIET
    help: Help | None = None


# What the button reads. The visible half of its label, so a test finds every
# question mark by it.
HELP_MARK = "(?)"
# Every explanation's window id starts with this, after the ``###`` that keeps
# the id apart from the title in front of it. ``help_showing`` finds one by it.
_HELP_TAG = "help:"
_HELP_WIDTH = 560.0
# The least an explanation's scrolling body is given, on a display too short
# for it.
_HELP_FLOOR = 120.0


def help_showing() -> bool:
    """
    Whether an explanation opened by ``help_mark`` is up, as Dear ImGui's popup
    stack has it at the moment of asking.

    For the dialog under it, asked before it draws anything: the application
    runs with keyboard navigation off, so a key is read by whoever asks
    ``is_key_pressed``, and the Escape that closes an explanation would close
    the dialog under it on the same frame. Asked after the explanation was
    drawn it would be too late -- the popup that key closed is already off the
    stack.
    """
    stack = imgui.get_current_context().open_popup_stack
    for at in range(stack.size()):
        window = stack[at].window
        if window is not None and f"###{_HELP_TAG}" in window.name:
            return True
    return False


def help_mark_width() -> float:
    """
    The room a question mark takes on a line, the gap before it included: what a
    caller leaves when it cuts text to fit beside one.
    """
    style = imgui.get_style()
    return imgui.calc_text_size(HELP_MARK).x + 2.0 * style.frame_padding.x + style.item_spacing.x


def help_mark(mark: str, title: str, paragraphs: Sequence[str]) -> bool:
    """
    A small ``(?)`` beside the item drawn before it, and behind it a modal whose
    title is what it explains, whose body is ``paragraphs`` and whose one button
    closes it. Escape closes it too. True while the modal is up.

    **The one way an explanation is drawn**, so each looks and behaves as the
    others do, and so that nothing but a press of this button puts a paragraph
    of explanation in front of an operator.

    **The modal is opened from where the button is**, which inside a dialog is
    inside that dialog's popup: Dear ImGui stacks a popup opened there over the
    one it was opened from, and closing it returns to that one. The dialog is
    drawn under it on every frame it is up and keeps whatever it held. What the
    dialog has to do for itself is not read Escape meanwhile (``help_showing``).

    ``mark`` is unique among the question marks of the window it is drawn in.
    """
    name = f"{title}###{_HELP_TAG}{mark}"
    imgui.same_line()
    if imgui.small_button(f"{HELP_MARK}##{mark}"):
        imgui.open_popup(name)
    if not imgui.is_popup_open(name):
        return False
    viewport = imgui.get_main_viewport()
    width = min(_HELP_WIDTH, viewport.work_size.x)
    # Its own scope, for a mark drawn outside any dialog; inside one the values
    # pushed are the ones already in force.
    with dialog_style():
        # Centred in the work area on every frame and not only the first: the
        # window is fitted to its text a frame after it appears, and one that
        # grew from where it first stood would grow off the display with its
        # button. No taller than the area, so centred is wholly inside it.
        imgui.set_next_window_pos(
            imgui.ImVec2(
                viewport.work_pos.x + viewport.work_size.x / 2.0,
                viewport.work_pos.y + viewport.work_size.y / 2.0,
            ),
            imgui.Cond_.always,
            imgui.ImVec2(0.5, 0.5),
        )
        imgui.set_next_window_size_constraints(
            imgui.ImVec2(width, 0.0), imgui.ImVec2(width, viewport.work_size.y)
        )
        opened, _ = imgui.begin_popup_modal(
            name,
            None,
            imgui.WindowFlags_.always_auto_resize | imgui.WindowFlags_.no_move,
        )
        if not opened:
            return False
        # The footer is the gap ``dialog_group`` leaves, with its rule, and one
        # row of buttons.
        style = imgui.get_style()
        footer = (
            2.0 * _px(_DIALOG_GROUP_EMS)
            + style.separator_size
            + imgui.get_frame_height_with_spacing()
        )
        begin_dialog_body("##explanation", footer, _HELP_FLOOR)
        imgui.push_text_wrap_pos(0.0)
        for at, paragraph in enumerate(paragraphs):
            if at:
                dialog_field()
            imgui.text_colored(P.text.vec4, author_text(paragraph))
        imgui.pop_text_wrap_pos()
        imgui.end_child()
        dialog_group()
        closed = imgui.button("close", imgui.ImVec2(90.0, 0.0))
        if closed or imgui.is_key_pressed(imgui.Key.escape, False):
            imgui.close_current_popup()
        imgui.end_popup()
    return True


def notice(said: Notice) -> None:
    """
    Draw one ``Notice``: its line on one row, cut with an ellipsis where the
    window is too narrow for it, and its question mark beside it.

    **One row whatever the line holds.** A line that wrapped would be the
    paragraph under a control this exists to replace; what does not fit is behind
    the mark, which is where a line whose length is not known in advance -- it
    quotes a path, or a reason something else gave -- has to keep the whole of
    what it says.

    ``Tone.LOUD`` is drawn on a band of the alarm colour the full width of the
    window, with a bar of it at the leading edge: hue is never the only channel
    here, and the band is there on a palette whose alarm colour is close to its
    text colour.
    """
    room = imgui.get_content_region_avail().x
    if said.help is not None:
        room -= help_mark_width()
    if said.tone is Tone.LOUD:
        _loud(said, room)
        return
    text = ellipsis(said.line, room)
    match said.tone:
        case Tone.QUIET:
            imgui.text_disabled(text)
        case Tone.WARN:
            imgui.text_colored(P.warn.vec4, text)
        case Tone.DANGER:
            imgui.text_colored(P.danger.vec4, text)
    if said.help is not None:
        help_mark(said.help.mark, said.help.title, said.help.paragraphs)


# The bar at the leading edge of the loud band, in pixels.
_LOUD_BAR = 3.0
_LOUD_FILL = 0.18


def _loud(said: Notice, room: float) -> None:
    style = imgui.get_style()
    pad = style.frame_padding
    origin = imgui.get_cursor_screen_pos()
    width = imgui.get_content_region_avail().x
    height = imgui.get_text_line_height() + 2.0 * pad.y
    draw = imgui.get_window_draw_list()
    draw.add_rect_filled(
        origin, imgui.ImVec2(origin.x + width, origin.y + height), faded(P.danger, _LOUD_FILL)
    )
    draw.add_rect_filled(
        origin, imgui.ImVec2(origin.x + _LOUD_BAR, origin.y + height), P.danger.u32
    )
    inset = _LOUD_BAR + pad.x
    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + inset, origin.y + pad.y))
    imgui.text_colored(P.danger.vec4, ellipsis(said.line, room - inset - pad.x))
    if said.help is not None:
        help_mark(said.help.mark, said.help.title, said.help.paragraphs)
    # The band is taller than the line in it: the next item starts under the
    # band and not under the text.
    imgui.set_cursor_screen_pos(origin)
    imgui.dummy(imgui.ImVec2(width, height))


def path_tail(path: str, chars: int) -> str:
    """
    ``path`` in at most ``chars`` characters, keeping its end: the whole of it
    when it fits, and otherwise an ellipsis, a separator and as many of its last
    components as do. The last component is kept whole whatever its length.

    The end and not the start, because two directories an operator has to tell
    apart -- a checkout and a worktree of it -- share everything but that.
    """
    if len(path) <= chars:
        return path
    parts = [part for part in path.split("/") if part]
    if not parts:
        return path
    kept = [parts[-1]]
    for part in reversed(parts[:-1]):
        if len("…/" + "/".join([part, *kept])) > chars:
            break
        kept.insert(0, part)
    return "…/" + "/".join(kept)


def follow_tail(following: bool) -> bool:
    """
    Pin the current window to its bottom until the operator scrolls away.

    Returns the new following state; the caller owns the flag. Shared rather than
    written twice because the non-obvious half is easy to get wrong in the same way
    independently: **disengage on an upward wheel, not on scroll position.**
    ``set_scroll_here_y`` re-pins every frame while following, so the view snaps
    back to the bottom before any position test could notice the operator trying to
    leave it. Position is only a sound signal for the *re*-engage, once nothing is
    pinning it any more.

    Scrolling up is an intent to read something, and yanking the view back on the
    next token is the single most annoying thing a streaming pane can do.

    Must be called inside the window or child it scrolls, after its content.
    """
    hovered = imgui.is_window_hovered()
    wheel = imgui.get_io().mouse_wheel

    if following:
        if hovered and wheel > 0.0:
            return False
        imgui.set_scroll_here_y(1.0)
        return True

    if imgui.get_scroll_max_y() > 0.0 and imgui.get_scroll_y() >= imgui.get_scroll_max_y() - 1.0:
        return True
    return False


def multiline_input(
    label: str,
    value: str,
    size: imgui.ImVec2,
    *,
    wrap: bool,
    flags: int = 0,
) -> tuple[bool, str]:
    """
    The one place a composer is drawn, so wrapping is decided once.

    Every multi-line field in the app goes through here. The alternative -- each
    pane passing its own flags -- is how the reply box and the argument editor end
    up disagreeing about whether long lines wrap, which reads as a bug in whichever
    one the operator noticed second.

    ``wrap`` is threaded in rather than read from a module global because this
    module owns no state; it is ``settings.wrap_inputs``, carried down from the menu.

    Single-line fields cannot participate. ``word_wrap`` is asserted by ImGui to be
    multi-line only (``IM_ASSERT`` in ``InputTextEx``), so it is not merely ignored
    on an ``InputText`` -- it aborts. Wrapping a single-line field means making it
    multi-line first, which is a layout decision, not a flag.
    """
    if wrap:
        flags |= int(imgui.InputTextFlags_.word_wrap)
    return imgui.input_text_multiline(label, value, size, flags)


def state_cell(state: AgentState) -> None:
    """
    Draw an agent state as glyph + label, both tinted.

    The label is not decoration and must not be dropped to save width: colour alone
    is unreadable in high contrast and to a colour-deficient operator, and the glyph
    alone degrades to tofu if the FontAwesome merge failed. Three channels, any two
    of which can fail (design §6.1).
    """
    color = P.state(state).vec4
    imgui.text_colored(color, STATE_GLYPH[state])
    imgui.same_line()
    imgui.text_colored(color, STATE_LABEL[state])


# The activity throbber: three columns of falling cells, each column on its own
# period so the field never resolves into a marching row.
#
# Sizing is in whole pixels because the cells are two pixels wide -- a fractional
# pitch would put some columns on a half-pixel boundary and render them dimmer than
# their neighbours, which reads as a lit cell rather than as antialiasing.
_RAIN_COLS = 3
_RAIN_ROWS = 5
_RAIN_CELL_W = 2.0
_RAIN_PITCH = 3.0
# Rows still lit behind the head. Fractional so the oldest cell in the trail is
# part-faded rather than snapping off, and strictly less than _RAIN_ROWS so the gap
# between the tail and the next head stays visible -- at trail == rows every cell is
# lit and the moving feature inverts into a travelling dark cell.
_RAIN_TRAIL = 2.2
# Seconds per fall, per column. Deliberately not ratios of each other: equal or
# harmonic periods resynchronise into a marching row.
_RAIN_FALL = (1.35, 0.94, 1.13)
_RAIN_PHASE = (0.0, 0.41, 0.73)

RAIN_WIDTH = (_RAIN_COLS - 1) * _RAIN_PITCH + _RAIN_CELL_W


def phase_seed(key: str) -> float:
    """
    A stable animation offset in [0, 1) for a string key.

    CRC32 rather than ``hash()``: string hashing is salted per process, so the same
    session would start its throbber at a different phase on every launch and no
    test could pin the arithmetic. The point of the offset is only that two cards
    stacked in the rail do not fall in lockstep.
    """
    return (zlib.crc32(key.encode("utf-8")) % 997) / 997.0


def rain_cells(now: float, seed: float = 0.0) -> list[tuple[int, int, float, bool]]:
    """
    Which cells of the throbber are lit at ``now``, how brightly, and which is the head.

    Returns ``(column, row, intensity, is_head)``, intensity falling from the head to
    0.0 at the end of the trail. Split out from the drawing so the motion can be
    tested without a GL context.

    The trail wraps around the top of the column rather than the drop falling off the
    bottom and the column waiting empty for the next one. That makes "something is
    always moving" structural: every column has exactly one head every frame, so no
    combination of periods can leave the field dark. The version this replaces let a
    column idle for ``lead / span`` of its cycle, and with three columns that
    coincided about 0.7% of the time -- a 0.18s blackout, which is long enough to read
    as the app having stalled and is the exact opposite of what a throbber is for.

    The head is flagged rather than inferred from intensity: the head lands on a
    fractional offset, so its intensity is only ``1.0`` at the instant it sits exactly
    on a row, and a brightness threshold would drop it on most frames.

    ``now`` is the frame's ``time.monotonic()``, threaded down like every other clock
    reading in this app rather than taken from ``imgui.get_time()``. A panel that
    reads the clock itself cannot be driven from a snapshot.
    """
    out: list[tuple[int, int, float, bool]] = []
    for c in range(_RAIN_COLS):
        phase = ((now / _RAIN_FALL[c]) + _RAIN_PHASE[c] + seed) % 1.0
        head = phase * _RAIN_ROWS
        for r in range(_RAIN_ROWS):
            behind = (head - r) % _RAIN_ROWS
            if behind <= _RAIN_TRAIL:
                out.append((c, r, 1.0 - behind / _RAIN_TRAIL, behind < 1.0))
    return out


def activity_rain(
    now: float, colour: Color, seed: float = 0.0, height: float | None = None
) -> None:
    """
    A throbber that says an agent is working, in the state's own colour.

    Motion is a redundant channel here, never the only one: the glyph, the label and
    the hue already say ``thinking``, and this only answers "is it still going" --
    the question a static card cannot answer at all, since a stuck session and a
    working one render identically. That redundancy is also why it is safe to ignore
    for an operator who cannot see it (design §6.1).

    Cost is bounded by the same rule that governs the frame rate: draw this only for
    ``AgentState.is_active``, and it can never animate while the runner is idling,
    because those are exactly the states that hold the loop at full speed. An
    animation on a parked session would be a throbber that lies at 9fps *and* burns
    the CPU I8 says a waiting session must not.
    """
    draw = imgui.get_window_draw_list()
    box_h = height if height is not None else imgui.get_text_line_height()
    origin = imgui.get_cursor_screen_pos()

    # Dummy, not invisible_button: the card underneath owns the click, and a second
    # interactive item stacked on it would take the hover and kill the card's
    # highlight wherever the throbber happens to sit.
    imgui.dummy(imgui.ImVec2(RAIN_WIDTH, box_h))

    row_h = box_h / _RAIN_ROWS
    cell_h = max(row_h - 1.0, 1.0)
    for c, r, intensity, head in rain_cells(now, seed):
        x = origin.x + c * _RAIN_PITCH
        y = origin.y + r * row_h
        draw.add_rect_filled(
            imgui.ImVec2(x, y),
            imgui.ImVec2(x + _RAIN_CELL_W, y + cell_h),
            # The trail fades on a curve, not linearly. Linear alpha over three cells
            # puts the last one at 9%, which on the panel colour is indistinguishable
            # from unlit -- the trail collapses to a blinking dot and the state's own
            # colour, the channel that says *which* kind of work, drops out with it.
            faded(P.text_strong if head else colour, 1.0 if head else intensity**0.6),
        )


def short_model(model: str) -> str:
    """
    Drop a trailing date stamp from a model id.

    "claude-haiku-4-5-20251001" truncates in a narrow column to "claude-haiku-4-5-",
    where the eight characters that got cut are the eight that never distinguish two
    models on screen. Only a trailing 8-digit run is removed, so an id that carries
    real information in that position is left alone.
    """
    head, sep, tail = model.rpartition("-")
    if sep and len(tail) == 8 and tail.isdigit():
        return head
    return model


def _fmt_tokens(count: int) -> str:
    """Compact token count. Terse on purpose -- this sits in a narrow column."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 10_000:
        return f"{count // 1000}k"
    if count >= 1_000:
        return f"{count / 1000:.1f}k"
    return str(count)


def context_ring(ctx: ContextSnapshot | None, radius: float = 7.0, thickness: float = 3.0) -> None:
    """
    Ring throbber: a track that fills as headroom is consumed, blue -> orange -> red.

    Fill fraction and hue are independent channels carrying the same signal, which is
    what satisfies §6.1 -- it matters most for the orange/red pair, since that is the
    distinction that collapses under red-deficiency. A fixed-size coloured dot is not
    an acceptable substitute: it drops the redundant channel.

    Two states here are not decoration and must not be collapsed into the normal
    case, because both would render as a confident, wrong number:

    * **No threshold.** ``tokens_until_compaction`` is None when autocompact is off,
      and the model deliberately refuses to fall back to the window size. Drawn as a
      hollow ring so it cannot be misread as "plenty of headroom".

    * **Already compacted.** A freshly compacted session has a nearly empty window,
      so an honest headroom ring fills blue and reads healthy at the exact moment the
      session is most damaged. The ring stays honest about headroom and the
      compaction count rides alongside it as a persistent mark, because the count is
      the stronger retire signal and must not be erased by a healthy-looking ring.
    """
    draw = imgui.get_window_draw_list()
    size = radius * 2.0 + 2.0
    origin = imgui.get_cursor_screen_pos()
    centre = imgui.ImVec2(origin.x + size * 0.5, origin.y + size * 0.5)

    # Reserve the space as a real item so hover works and layout advances.
    imgui.invisible_button("##ctxring", imgui.ImVec2(size, size))
    hovered = imgui.is_item_hovered()

    draw.add_circle(centre, radius, P.ring_track.u32, 0, thickness)

    if ctx is None:
        _ring_tooltip(hovered, None)
        return

    headroom = ctx.tokens_until_compaction
    if headroom is None:
        # Hollow ring: occupancy is known, headroom is not. Deliberately no arc.
        if hovered:
            _ring_tooltip(hovered, ctx)
        return

    threshold = ctx.auto_compact_threshold or 0
    consumed = 1.0 - (headroom / threshold) if threshold else 0.0
    consumed = max(0.0, min(1.0, consumed))
    colour = P.pressure(ctx.pressure()).u32

    if consumed > 0.0:
        start = -math.pi * 0.5
        draw.path_arc_to(centre, radius, start, start + consumed * math.tau, 0)
        # path_stroke is (col, thickness, flags) in this binding, not the C++
        # (col, flags, thickness). Passing the C++ order typechecks as ints and
        # renders a hairline.
        draw.path_stroke(colour, thickness)

    if ctx.compactions:
        # Persistent mark: this session has already lost reasoning, regardless of how
        # empty the window looks now.
        draw.add_circle_filled(
            imgui.ImVec2(centre.x + radius, centre.y - radius), 2.5, P.pressure_compacted.u32, 0
        )

    _ring_tooltip(hovered, ctx)


def _ring_tooltip(hovered: bool, ctx: ContextSnapshot | None) -> None:
    if not hovered:
        return
    if imgui.begin_tooltip():
        if ctx is None:
            imgui.text_disabled("context not yet polled")
        else:
            headroom = ctx.tokens_until_compaction
            if headroom is None:
                imgui.text("autocompact off - no compaction threshold")
                imgui.text_disabled(
                    f"{_fmt_tokens(ctx.used_tokens)} of {_fmt_tokens(ctx.max_tokens)} resident"
                )
            else:
                imgui.text(f"{_fmt_tokens(headroom)} before this session compacts")
                imgui.text_disabled(
                    f"{_fmt_tokens(ctx.used_tokens)} resident, "
                    f"threshold {_fmt_tokens(ctx.auto_compact_threshold or 0)}"
                )
            if ctx.compactions:
                imgui.text_colored(
                    P.pressure_compacted.vec4,
                    f"compacted {ctx.compactions}x - reasoning already discarded",
                )
            imgui.text_disabled(f"model {ctx.model}")
        imgui.end_tooltip()


def context_cell(ctx: ContextSnapshot | None) -> None:
    """
    Ring plus the countdown as plain text.

    The text is a bare number and is not labelled "to compaction". It falls as the
    session fills, which reads as a countdown once learned, and this is a developer
    tool where learning it once is cheap. The tooltip on the ring carries the full
    reading for anyone who has not.
    """
    context_ring(ctx)
    imgui.same_line()

    if ctx is None:
        imgui.text_disabled("--")
        return

    headroom = ctx.tokens_until_compaction
    if headroom is None:
        # No countdown exists, so show occupancy and mark it as a different quantity
        # rather than letting a number sit where a countdown normally does.
        imgui.text_disabled(f"{_fmt_tokens(ctx.used_tokens)} in")
        return

    colour = P.pressure(ctx.pressure()).vec4
    label = _fmt_tokens(headroom)
    if ctx.compactions:
        label += f"  x{ctx.compactions}"
    imgui.text_colored(colour, label)


# What U+0000 is drawn as. ImGui takes its text as a C string, so everything from
# the first one on would otherwise not be drawn and nothing would say so.
NUL_SHOWN = "<NUL>"


_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def surrogate_shown(char: str) -> str:
    """What stands in for one lone surrogate: its code point, spelled out."""
    return f"<U+{ord(char):04X}>"


def author_text(text: str) -> str:
    """
    ``text`` as it can be drawn whole: every U+0000 replaced by ``NUL_SHOWN``
    and every lone surrogate by ``surrogate_shown``.

    Total: no ``str`` makes the binding raise or stop early. A lone surrogate
    (U+D800 to U+DFFF) is not a character and cannot be encoded as UTF-8, so
    Dear ImGui's binding refuses the whole string with ``TypeError`` -- and a
    row that raises while it draws is a row that can be neither read nor
    answered. JSON can spell one (``"\\ud800"``), so a tool argument or a record
    read from a file can hold one.
    """
    return _LONE_SURROGATE.sub(
        lambda found: surrogate_shown(found.group()), text.replace("\x00", NUL_SHOWN)
    )


def one_line(text: str) -> str:
    """
    ``text`` with every line break replaced by a space, and drawable whole.

    For a field a pane lays out on a line of its own making and whose spacing is
    otherwise the author's to keep -- an id, a subject, a role name, a path, a
    property. None is checked for line breaks where it is written, and one that
    carries a break would end the pane's line and start the next in whatever form
    its author chose.

    The rule is ``bus._one_line``'s, spelled again because importing the bus would
    bring the SDK into the UI layer. A test holds the two to one answer.
    """
    return author_text(" ".join(text.splitlines()))


def flat_line(text: str) -> str:
    """
    ``text`` as one drawable line: every run of whitespace, line breaks included,
    collapsed to a single space, and the ends trimmed.

    For free text drawn in a slot one line high -- a task on a card, a topic, a
    summary. A task is whatever was typed into a multi-line composer, and ImGui
    draws a line break as one: the second line lands on whatever the card laid out
    beneath the first. ``one_line`` would leave a blank line as two spaces, which in
    a slot measured in pixels is width spent on nothing.

    Everything ``str.splitlines`` breaks on is whitespace to ``str.split``, so this
    removes every break ``one_line`` does.
    """
    return author_text(" ".join(text.split()))


def ellipsis(text: str, width_px: float) -> str:
    """
    ``text`` on one line (``flat_line``), clipped to ``width_px`` with the cut marked
    by an ellipsis.

    Flattened here rather than by each caller because a clipped string is by
    construction one going into a one-line slot, and ``calc_text_size`` measures a
    string with a break in it by its widest line: unflattened, the cut lands in a
    later line and every line before it is drawn whole.

    Table cells clip mid-glyph and give the reader nothing to tell a clipped string
    from a short one. That matters most for the field this is mostly used on -- the
    task, which is the only string that distinguishes one session from another.

    Binary search rather than shrinking a character at a time. This runs per row per
    frame, and a linear walk costs one ``calc_text_size`` per character trimmed: a
    200-character task on twenty rows is four thousand text measurements a frame for
    a string that has not changed. Valid because glyph advances are non-negative, so
    width is monotonic in prefix length.
    """
    if width_px <= 0.0:
        return ""
    text = flat_line(text)
    if imgui.calc_text_size(text).x <= width_px:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if imgui.calc_text_size(text[:mid] + "…").x <= width_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


def format_elapsed(seconds: float) -> str:
    """
    A duration in whichever unit keeps it to a few characters.

    Every field truncates rather than rounding. Formatting the minutes as
    ``seconds / 60`` with ``:.0f`` rounds to nearest, which renders 3m40s as
    "4m40s" -- the leading field then disagrees with the one beside it, and always
    in the direction that overstates the duration.

    Separate from ``elapsed_cell`` so the arithmetic can be tested without an
    ImGui context.
    """
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total // 60) % 60:02d}m"


def elapsed_cell(started_at: float, ended_at: float | None, now: float) -> None:
    """
    Elapsed time for one agent row, dimmed once the agent has ended.

    All three arguments must come from ``time.monotonic()``. Deliberately not
    sanity-clamped: mixing in a ``time.time()`` value renders as a duration in the
    hundreds of thousands of hours, which is obvious on sight. Clamping it would
    turn a loud bug into a plausible-looking wrong number.
    """
    if started_at <= 0.0:
        imgui.text_disabled("--")
        return
    text = format_elapsed((ended_at if ended_at is not None else now) - started_at)
    if ended_at is not None:
        imgui.text_disabled(text)
    else:
        imgui.text(text)


# -- a session recalled from its file -------------------------------------------

# The card menu's word for the act that loads a closed session
# (``session_menu``), so a pane sending the operator there names what they will
# find.
RESUME_ITEM = "Resume…"

_CLOSED_GONE = "closed — its transcript is gone, so it cannot be resumed, only removed"


def closed_line(record: AgentRecord | None, unloaded: str = "not loaded") -> str | None:
    """
    The one line a pane draws in place of its contents for a session recalled
    from its file (``AgentRecord.recalled``), or None for any other record.

    A recalled record is the field defaults of a session nothing here observed:
    DONE, zero usage, an empty transcript. Each of those is a pane's ordinary
    reading of an ordinary session, so a pane that draws the record draws
    "done", "$0.00" and an empty scrollback as though they had been measured.
    This is what it says instead: that the session is closed, what of it the
    pane does not hold, and the act that loads it.

    ``unloaded`` is the pane's own half -- what is missing here -- and "" for a
    pane with nothing to load. A session whose transcript the CLI no longer has
    is not sent to a resume that cannot happen.
    """
    if record is None or record.recalled is None:
        return None
    if record.recalled.transcript is TranscriptPresence.GONE:
        return _CLOSED_GONE
    missing = f"{unloaded}; " if unloaded else ""
    return f"closed — {missing}{RESUME_ITEM} to continue"


def ago(then: float | None, now_wall: float | None) -> str:
    """
    How long before ``now_wall`` the wall-clock instant ``then`` was, or "not
    known" when either is missing.

    **Both are wall-clock seconds**, which is what a session file holds
    (``model.Recalled``). Neither may be a ``time.monotonic()`` reading: the
    difference would be a number of the right shape and no meaning. A ``then``
    ahead of ``now_wall`` is a clock that was moved, and reads as no time ago.
    """
    if then is None or now_wall is None:
        return "not known"
    return f"{format_elapsed(now_wall - then)} ago"


_ENDED_HOW = {
    EndedHow.CLOSED: "it was closed",
    EndedHow.FAILED: "it failed",
    EndedHow.SHUTDOWN: "pptmstr was shut down under it",
}


def ended_how_label(how: EndedHow | None) -> str:
    """
    How a recalled session's last run ended, in words. None is a run that was
    killed or a file that does not say, which the file does not tell apart, and
    neither is "closed".
    """
    if how is None:
        return "not recorded how its last run ended (killed, or the file does not say)"
    return f"its last run ended when {_ENDED_HOW[how]}"
