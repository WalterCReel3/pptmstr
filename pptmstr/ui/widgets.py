"""
Small drawing helpers shared by the panes.

Nothing here owns state. Each function draws from values it is handed and returns
whatever the caller needs to react to, which is what keeps panels rebuildable from
a snapshot alone.
"""

from __future__ import annotations

import math
import zlib

from imgui_bundle import imgui

from ..model import AgentState, ContextSnapshot
from ..theme import STATE_GLYPH, STATE_LABEL, Color, P, faded

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


def ellipsis(text: str, width_px: float) -> str:
    """
    Clip ``text`` to ``width_px``, marking the cut with an ellipsis.

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
