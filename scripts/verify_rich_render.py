#!/usr/bin/env python3
"""
Does the rich renderer actually work against a live frame?

blocks.py, inline.py and span_layout.py are plain data, unit-tested without
ImGui. rich_pane.py is the one module in the markdown stack
(planning/archive/2026-08-10-transcript-markdown.md, step 5) that draws, and
drawing is exactly
where a wrong ``add_text`` overload, a mismatched ``begin_table``/``end_table``
pair, or a bad assumption about what "the last item" is after ``EndTable()``
would surface -- none of that shows up until something actually renders a frame.

Four things are worth pinning:

  1. **Smoke.** A document exercising every ``BlockKind`` -- heading, styled
     paragraph, list items, blockquote, a balanced fence, an unbalanced one (cut
     off by a kind change), a thematic break, a table, and a still-streaming
     live paragraph -- renders for several frames without raising. This is the
     highest-value check: most of what could be wrong here is "throws", not
     "draws the wrong pixel".
  2. **Wrap.** A long paragraph in a narrow child actually produces more than
     one row -- checked by content height, not by reading pixels.
  3. **Block hover + copy.** The same interaction model verify_transcript_copy.py
     already proved for the RAW path, exercised here for a block's
     invisible_button: hovering latches ``RichState.context_block``, and
     right-click -> "copy ..." puts that block's verbatim source on the
     clipboard.
  4. **Table hover + copy.** The riskiest untested assumption in rich_pane.py:
     that ``begin_popup_context_item`` right after ``EndTable()`` binds to the
     table widget as "the last item", the same way it does for an ordinary
     widget. If this is wrong, a table silently gets no context menu at all --
     not a crash, so nothing but a targeted check would catch it.

The pointer is driven by warping it (``io.want_set_mouse_pos``), matching
verify_transcript_copy.py -- queued positions lose to the real cursor on a live
desktop, warping does not. **It therefore moves the physical pointer** for the
few seconds each stage runs.

Usage:  .venv/bin/python scripts/verify_rich_render.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SMOKE_DOC = (
    "# Heading one\n"
    "\n"
    "A paragraph with **bold**, *em*, `code`, and a [link](http://example.com)\n"
    "that is long enough to wrap at a narrow width across more than one row.\n"
    "\n"
    "- item one\n"
    "- item two\n"
    "\n"
    "1. first\n"
    "2. second\n"
    "\n"
    "> a quoted line\n"
    "\n"
    "```py\n"
    "def f():\n"
    "    return 1\n"
    "```\n"
    "\n"
    "---\n"
    "\n"
    "a|b\n"
    "-|-\n"
    "1|2\n"
    "3|4\n"
    "\n"
)

# A single short paragraph, drawn alone -- so "the last item" after draw() is this
# block's own invisible_button, unambiguous for the hover/copy stage.
HOVER_DOC = "a short paragraph\n\n"

TABLE_DOC = "a|b\n-|-\n1|2\n\n"  # trailing blank line: a table only finalises like a paragraph does

LONG_PARAGRAPH = "word " * 60 + "\n\n"


@dataclass
class Stage:
    name: str
    failures: list[str] = field(default_factory=list)
    done: bool = False


def point_at(imgui: object, x: float, y: float) -> None:
    io = imgui.get_io()  # type: ignore[attr-defined]
    io.mouse_pos = imgui.ImVec2(x, y)  # type: ignore[attr-defined]
    io.want_set_mouse_pos = True


def main() -> int:
    from imgui_bundle import ImVec2, hello_imgui, imgui, immapp

    # A fixed offset from the right-click position (what verify_transcript_copy.py
    # uses) turned out to be too fragile here: on a real (non-Xvfb) X11 desktop,
    # cursor-warp latency drifts the observed mouse position by several pixels
    # frame to frame, and a popup's first row is often under 20px tall -- easy to
    # overshoot past. Tracking where menu_item_simple() actually rendered and
    # aiming there instead removes the guesswork entirely.
    menu_item_rect: list[tuple[float, float, float, float] | None] = [None]
    _orig_menu_item = imgui.menu_item_simple

    def _tracking_menu_item(label: str, *args: object, **kwargs: object) -> bool:
        clicked = _orig_menu_item(label, *args, **kwargs)  # type: ignore[arg-type]
        mn, mx = imgui.get_item_rect_min(), imgui.get_item_rect_max()
        menu_item_rect[0] = (mn.x, mn.y, mx.x, mx.y)
        return clicked

    imgui.menu_item_simple = _tracking_menu_item  # type: ignore[assignment]

    from pptmstr import theme
    from pptmstr.transcript import SegmentKind, Transcript
    from pptmstr.ui import rich_pane
    from pptmstr.ui.blocks import BlockCursor
    from pptmstr.ui.transcript_pane import NodeTranscript

    all_failures: list[str] = []
    all_notes: list[str] = []

    # -- stage 1: smoke -----------------------------------------------------

    def cursor_for(text: str, live_tail: str = "") -> tuple[NodeTranscript, BlockCursor]:
        t = Transcript()
        t.append(SegmentKind.OUTPUT, text)
        cache = NodeTranscript()
        cache.sync(t, t.published_length)
        if live_tail:
            t.append(SegmentKind.OUTPUT, live_tail)
            cache.sync(t, t.published_length)
        cursor = BlockCursor()
        stable = len(cache.lines) - (1 if cache.open_line else 0)
        cursor.feed(cache.lines[:stable])
        return cache, cursor

    smoke_cache, smoke_cursor = cursor_for(SMOKE_DOC, live_tail="a live paragraph still ")
    smoke_state = rich_pane.RichState()
    smoke_frames = [0]
    smoke_failures: list[str] = []

    def gui_smoke() -> None:
        smoke_frames[0] += 1
        imgui.set_next_window_pos(ImVec2(0.0, 0.0))
        imgui.set_next_window_size(ImVec2(700.0, 900.0))
        imgui.begin("smoke")
        try:
            imgui.begin_child("##smoke_child")
            live = smoke_cursor.live_block(smoke_cache.lines)
            rich_pane.draw(smoke_state, smoke_cursor.blocks, live)
            imgui.end_child()
        except Exception as exc:  # noqa: BLE001 -- the whole point is to catch anything
            smoke_failures.append(f"{type(exc).__name__}: {exc}")
        imgui.end()
        if smoke_frames[0] >= 6:
            hello_imgui.get_runner_params().app_shall_exit = True

    _run(theme, imgui, hello_imgui, immapp, "verify_rich_render smoke", gui_smoke)
    if smoke_failures:
        all_failures.append(f"smoke: raised on frame(s): {smoke_failures}")
    else:
        all_notes.append(
            f"smoke: {smoke_frames[0]} frames, {len(smoke_cursor.blocks)} blocks, no exception"
        )

    # -- stage 2: wrap --------------------------------------------------------

    wrap_cache, wrap_cursor = cursor_for(LONG_PARAGRAPH)
    wrap_height = [0.0]
    wrap_frames = [0]

    def gui_wrap() -> None:
        wrap_frames[0] += 1
        imgui.set_next_window_pos(ImVec2(0.0, 0.0))
        imgui.set_next_window_size(ImVec2(220.0, 300.0))
        imgui.begin("wrap")
        imgui.begin_child("##wrap_child")
        top = imgui.get_cursor_pos().y
        rich_pane.draw(rich_pane.RichState(), wrap_cursor.blocks, None)
        wrap_height[0] = imgui.get_cursor_pos().y - top
        imgui.end_child()
        imgui.end()
        if wrap_frames[0] >= 3:
            hello_imgui.get_runner_params().app_shall_exit = True

    _run(theme, imgui, hello_imgui, immapp, "verify_rich_render wrap", gui_wrap)
    line_h_estimate = 22.0  # generous floor; exact value needs a live font, checked loosely
    if wrap_height[0] < line_h_estimate * 2:
        h = wrap_height[0]
        all_failures.append(f"wrap: a 60-word paragraph at 220px used only {h:.0f}px, unwrapped?")
    else:
        all_notes.append(f"wrap: 60-word paragraph used {wrap_height[0]:.0f}px (wrapped)")

    def make_copy_stage(
        window_title: str,
        draw_call: object,
        want_clip: str,
        hover_check: object | None = None,
        clip_hint: str = "",
    ) -> Stage:
        """
        Right-click the last item ``draw_call`` produces, wait for
        ``menu_item_simple`` to actually render (tracked via the monkeypatch
        above -- no offset guessing), click it, and check the clipboard.
        """
        result = Stage(window_title)
        item_rect: list[tuple[float, float, float, float] | None] = [None]
        holder = {"stage": "settle"}
        menu_item_rect[0] = None

        def gui() -> None:
            io = imgui.get_io()
            imgui.set_next_window_pos(ImVec2(0.0, 0.0))
            imgui.set_next_window_size(ImVec2(500.0, 300.0))
            imgui.begin(window_title)
            imgui.begin_child("##child")
            draw_call()
            # Captured once, before any click -- once a popup is open, "last
            # item" belongs to it instead.
            if item_rect[0] is None:
                mn, mx = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                item_rect[0] = (mn.x, mn.y, mx.x, mx.y)
            imgui.end_child()
            imgui.end()

            if result.done or item_rect[0] is None:
                return
            x0, y0, x1, y1 = item_rect[0]
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

            stage = holder["stage"]
            if stage == "settle":
                holder["stage"] = "aim"
            elif stage == "aim":
                point_at(imgui, cx, cy)
                holder["stage"] = "check_hover"
            elif stage == "check_hover":
                if hover_check is not None:
                    hover_check(result)
                io.add_mouse_button_event(1, True)
                holder["stage"] = "release"
            elif stage == "release":
                io.add_mouse_button_event(1, False)
                holder["stage"] = "wait_menu"
            elif stage == "wait_menu":
                # The popup opens on this same release, per begin_popup_context_
                # item's own docs ("triggers on mouse released"), but give it a
                # couple of frames rather than assume exactly one.
                if menu_item_rect[0] is not None:
                    holder["stage"] = "aim_menu"
            elif stage == "aim_menu":
                mx0, my0, mx1, my1 = menu_item_rect[0]  # type: ignore[misc]
                point_at(imgui, (mx0 + mx1) / 2.0, (my0 + my1) / 2.0)
                holder["stage"] = "pick"
            elif stage == "pick":
                io.add_mouse_button_event(0, True)
                holder["stage"] = "picked"
            elif stage == "picked":
                io.add_mouse_button_event(0, False)
                holder["stage"] = "read"
            elif stage == "read":
                clip = imgui.get_clipboard_text()
                if clip != want_clip:
                    result.failures.append(
                        f"clipboard held {clip!r}, expected {want_clip!r}{clip_hint}"
                    )
                result.done = True

            if result.done:
                hello_imgui.get_runner_params().app_shall_exit = True

        _run(theme, imgui, hello_imgui, immapp, f"verify_rich_render {window_title}", gui)
        return result

    # -- stage 3: block hover + copy -------------------------------------------

    hover_cache, hover_cursor = cursor_for(HOVER_DOC)
    hover_state = rich_pane.RichState()
    hover_ok = [True]

    def check_hover(result: Stage) -> None:
        if hover_state.context_block != 0:
            hover_ok[0] = False
            result.failures.append(f"hover did not latch block 0, got {hover_state.context_block}")

    hover = make_copy_stage(
        "hover",
        lambda: rich_pane.draw(hover_state, hover_cursor.blocks, None),
        "\n".join(hover_cursor.blocks[0].lines),
        hover_check=check_hover,
    )
    if hover.failures:
        all_failures.extend(f"hover: {f}" for f in hover.failures)
    else:
        all_notes.append("hover: latch + right-click copy agree")

    # -- stage 4: table hover + copy --------------------------------------------

    table_cache, table_cursor = cursor_for(TABLE_DOC)
    table_state = rich_pane.RichState()
    tstage = make_copy_stage(
        "table",
        lambda: rich_pane.draw(table_state, table_cursor.blocks, None),
        "\n".join(table_cursor.blocks[0].lines),
        clip_hint=" -- begin_popup_context_item after end_table() may not bind to the table",
    )
    if tstage.failures:
        all_failures.extend(f"table: {f}" for f in tstage.failures)
    else:
        all_notes.append("table: right-click copy works on the table widget itself")

    # -- stage 5: DETAIL draws a question through the same renderer ---------------
    #
    # DETAIL is the renderer's second caller (planning/2026-08-11-what-it-said-is-a-
    # byte-tail.md, step 3). Two things here are not reachable from the stages
    # above, because both are about a *second* caller rather than about the
    # renderer: whether rich_pane.draw survives being called with DETAIL's wrap
    # position pushed, and whether two panes drawing blocks in one frame keep their
    # hover latches apart -- the block ids are per-index, so if the two windows did
    # not scope them, CONTEXT and DETAIL would fight over context_block every frame.

    from types import MappingProxyType

    from pptmstr.model import AgentRecord, AgentState, QuestionPending, Snapshot
    from pptmstr.ui import detail, review
    from pptmstr.ui.focus import FocusState, OnNode, OnObligation

    detail_node = ("verify-detail", None)
    detail_record = AgentRecord(
        node_id=detail_node,
        parent=None,
        depth=0,
        state=AgentState.AWAITING_INPUT,
        topic="asking",
        task="audit the TLE parser",
        model="claude-sonnet-5",
        cwd="/x/orbital",
    )
    detail_record.transcript.append(SegmentKind.SYSTEM, "\n> audit the TLE parser\n")
    detail_record.transcript.append(SegmentKind.TOOL_RESULT, "Grep -> " + "hit\n" * 200)
    detail_record.transcript.append(SegmentKind.OUTPUT, SMOKE_DOC + "Fix it, or only report it?")

    detail_obligation = QuestionPending(node=detail_node, since=0.0, summary="ended its turn")
    detail_snap = Snapshot(
        seq=1,
        nodes=MappingProxyType({detail_node: detail_record}),
        order=(detail_node,),
        needs_you=(detail_obligation,),
        any_active=False,
    )
    detail_focus = FocusState(target=OnObligation(key=detail_obligation.key))
    detail_pane = detail.DetailState()
    context_state = rich_pane.RichState()
    ctx_cache, ctx_cursor = cursor_for(HOVER_DOC)

    detail_failures: list[str] = []
    detail_frames = [0]
    detail_height = [0.0]
    detail_blocks = [0]
    latches: list[tuple[int | None, int | None]] = []

    def gui_detail() -> None:
        detail_frames[0] += 1

        # CONTEXT's rich pane, drawn first and in its own window, so the frame has
        # two independent block renderers in it exactly as the real layout does.
        imgui.set_next_window_pos(ImVec2(0.0, 0.0))
        imgui.set_next_window_size(ImVec2(420.0, 900.0))
        imgui.begin("context")
        imgui.begin_child("##ctx_child")
        rich_pane.draw(context_state, ctx_cursor.blocks, ctx_cursor.live_block(ctx_cache.lines))
        imgui.end_child()
        imgui.end()

        imgui.set_next_window_pos(ImVec2(430.0, 0.0))
        imgui.set_next_window_size(ImVec2(460.0, 900.0))
        imgui.begin("detail")
        try:
            top = imgui.get_cursor_pos().y
            detail.draw(detail_snap, detail_focus, review.ReviewState(), detail_pane, 1.0)
            detail_height[0] = imgui.get_cursor_pos().y - top
            detail_blocks[0] = len(detail_pane.prose_blocks(detail_node, detail_record.transcript))
        except Exception as exc:  # noqa: BLE001 -- the whole point is to catch anything
            detail_failures.append(f"{type(exc).__name__}: {exc}")
        imgui.end()

        # Park the pointer over CONTEXT's only block. DETAIL must not latch from it.
        point_at(imgui, 60.0, 60.0)
        if detail_frames[0] >= 4:
            latches.append((context_state.context_block, detail_pane.rich.context_block))
        if detail_frames[0] >= 6:
            hello_imgui.get_runner_params().app_shall_exit = True

    _run(theme, imgui, hello_imgui, immapp, "verify_rich_render detail", gui_detail)

    if detail_failures:
        all_failures.append(f"detail: raised on frame(s): {detail_failures}")
    elif detail_blocks[0] == 0:
        all_failures.append("detail: the question turn parsed to zero blocks -- nothing rendered")
    elif detail_height[0] < 100.0:
        h = detail_height[0]
        all_failures.append(f"detail: a full markdown turn used only {h:.0f}px -- did it draw?")
    else:
        all_notes.append(
            f"detail: {detail_blocks[0]} blocks, {detail_height[0]:.0f}px, no exception"
        )
    crossed = [pair for pair in latches if pair[1] is not None]
    if crossed:
        all_failures.append(
            f"detail: hovering CONTEXT latched DETAIL's context_block too ({crossed[0]}) "
            "-- the two panes' block ids are colliding"
        )
    elif latches:
        all_notes.append("detail: CONTEXT and DETAIL hover latches stay independent")

    # -- stage 6: DETAIL narrates a running turn ---------------------------------
    #
    # The empty state (step 4) renders wrapped text rather than blocks, and pins
    # itself to the newest line. Two things need a live frame: that the pin actually
    # moves the scroll to the bottom as prose arrives, and that it lets go when the
    # operator wheels up -- the disengage cannot be tested by scroll position, which
    # is the whole reason widgets.follow_tail exists.

    narrate_node = ("verify-narrate", None)
    narrate_record = AgentRecord(
        node_id=narrate_node,
        parent=None,
        depth=0,
        state=AgentState.THINKING,  # is_active -> live narration
        topic="working",
        task="audit the TLE parser",
        model="claude-sonnet-5",
        cwd="/x/orbital",
    )
    narrate_record.transcript.append(SegmentKind.SYSTEM, "\n> audit the TLE parser\n")
    narrate_snap = Snapshot(
        seq=1,
        nodes=MappingProxyType({narrate_node: narrate_record}),
        order=(narrate_node,),
        needs_you=(),
        any_active=True,
    )
    # No obligation, so focus resolves to the node -- the branch step 4 fills in.
    narrate_focus = FocusState(target=OnNode(node=narrate_node))
    narrate_pane = detail.DetailState()

    narrate_failures: list[str] = []
    narrate_frames = [0]
    scroll_seen: list[tuple[float, float]] = []
    followed_after_wheel: list[bool] = []

    def gui_narrate() -> None:
        narrate_frames[0] += 1
        # A line of prose per frame, as a running turn would arrive.
        narrate_record.transcript.append(
            SegmentKind.OUTPUT, f"line {narrate_frames[0]} of narration prose\n"
        )

        # Injected *before* the draw, and re-injected every frame: the backend
        # rewrites io.mouse_wheel during NewFrame, so a value written after the
        # widget has already read it is discarded before the next one looks.
        wheeling = narrate_frames[0] > 32
        if narrate_frames[0] >= 30:
            point_at(imgui, 240.0, 240.0)
        if wheeling:
            imgui.get_io().mouse_wheel = 1.0

        imgui.set_next_window_pos(ImVec2(0.0, 0.0))
        imgui.set_next_window_size(ImVec2(480.0, 320.0))
        imgui.begin("narrate")
        try:
            detail.draw(narrate_snap, narrate_focus, review.ReviewState(), narrate_pane, 1.0)
        except Exception as exc:  # noqa: BLE001 -- the whole point is to catch anything
            narrate_failures.append(f"{type(exc).__name__}: {exc}")
        imgui.end()

        if wheeling:
            followed_after_wheel.append(narrate_pane.narration_follow)

        # The child is gone by now, so read the scroll it reached via its own window.
        if imgui.begin("narrate"):
            if imgui.begin_child("##narration"):
                scroll_seen.append((imgui.get_scroll_y(), imgui.get_scroll_max_y()))
            imgui.end_child()
        imgui.end()

        if narrate_frames[0] >= 36:
            hello_imgui.get_runner_params().app_shall_exit = True

    _run(theme, imgui, hello_imgui, immapp, "verify_rich_render narration", gui_narrate)

    if narrate_failures:
        all_failures.append(f"narration: raised on frame(s): {narrate_failures}")
    else:
        pinned = [s for s, mx in scroll_seen if mx > 0.0 and s >= mx - 1.0]
        scrollable = [1 for _, mx in scroll_seen if mx > 0.0]
        if not scrollable:
            all_failures.append("narration: never overflowed, so the pin was never exercised")
        elif len(pinned) < len(scrollable) // 2:
            all_failures.append(
                f"narration: pinned to the bottom on only {len(pinned)} of "
                f"{len(scrollable)} scrollable frames -- follow_tail not holding"
            )
        else:
            all_notes.append(
                f"narration: pinned to newest on {len(pinned)}/{len(scrollable)} scrollable frames"
            )
        if followed_after_wheel and all(followed_after_wheel):
            all_failures.append(
                "narration: wheeling up never disengaged the pin -- the pane fights the operator"
            )
        elif followed_after_wheel:
            all_notes.append("narration: wheeling up releases the pin")

    for note in all_notes:
        print(note)
    for line in all_failures:
        print(f"FAIL: {line}")
    if not all_failures:
        print("OK: smoke, wrap, block copy, table copy, DETAIL and narration all agree")
    return 1 if all_failures else 0


def _run(
    theme: object, imgui: object, hello_imgui: object, immapp: object, title: str, gui: object
) -> None:
    params = hello_imgui.RunnerParams()  # type: ignore[attr-defined]
    params.app_window_params.window_geometry.size = (900, 950)
    params.app_window_params.window_title = title
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window  # type: ignore[attr-defined]
    )
    params.callbacks.load_additional_fonts = theme.load_fonts  # type: ignore[attr-defined]
    params.callbacks.show_gui = gui
    params.fps_idling.enable_idling = False
    immapp.run(params)  # type: ignore[attr-defined]


if __name__ == "__main__":
    raise SystemExit(main())
