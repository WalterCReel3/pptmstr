"""
Talking to sessions that are already running.

Until this existed the application could only be talked to in one direction. An
agent that asked a question ended its turn and there was nowhere to answer it,
which made the thing unusable for the interactive work it was built for.

Send another prompt to a live session, interrupt it, or close it.
``AWAITING_INPUT`` is the state this pane exists to answer. Starting a session is
``ui/launcher.py`` -- a different act on a session that does not exist yet.

Everything leaves through ``Bridge.submit`` onto the asyncio thread. Nothing here
touches a session object directly, so the pool stays single-threaded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from imgui_bundle import imgui

from ..model import AgentState, NodeId, Snapshot
from ..theme import P
from . import transcript_pane, widgets


@dataclass
class ComposeState:
    """Draft reply text, per session."""

    # Reply drafts, keyed by node. Kept per node so switching selection mid-sentence
    # does not silently discard what was being typed to a different agent.
    replies: dict[NodeId, str] = field(default_factory=dict)
    focus_reply: bool = False

    def prune(self, snap: Snapshot) -> None:
        for node in [n for n in self.replies if n not in snap.nodes]:
            del self.replies[node]


@dataclass(frozen=True)
class TextEdges:
    """
    Where the log's text ran in the frame being drawn, as screen x: its left edge
    and its right one. SESSION reads them off the log's own window after drawing it
    (``app._log_text_edges``), so they hold whatever that log used -- its margin
    for its text mode, and its vertical bar where it showed one.
    """

    left: float
    right: float


def wants_send(submitted: bool, clicked: bool, draft: str) -> bool:
    """
    Whether this frame's input means "send it".

    Both composers -- this pane and the inbox's expanded question -- ask the same
    question of two different signals, and the whitespace rule is the part that
    matters: a session handed an empty turn spends a turn on nothing, and a draft
    of only newlines is exactly what a box bound to Enter accumulates.
    """
    return (submitted or clicked) and bool(draft.strip())


def draw_conversation(
    snap: Snapshot,
    state: ComposeState,
    selected: NodeId | None,
    *,
    send: Callable[[NodeId, str], None],
    interrupt: Callable[[NodeId], None],
    close: Callable[[NodeId], None],
    wrap: bool = True,
    log: TextEdges | None = None,
) -> None:
    """
    Reply to, interrupt, or close the selected session, between the log's text edges.

    ``log`` is where the log's text ran this frame, so the box starts where that
    text does and stops where it stops: short of the log's bar when it shows one,
    and at the margin its own text mode set. Without one -- no log was drawn, as
    for a session that is not loaded -- the composer keeps
    ``transcript_pane.margin_px`` at the UI font's size on both sides. Every line
    wraps at the right edge: a pane is as narrow as the operator drags it, and a
    line ImGui does not wrap runs under its edge.
    """
    state.prune(snap)
    origin = imgui.get_cursor_screen_pos().x
    if log is None:
        side = transcript_pane.margin_px(imgui.get_font_size())
        left, right = origin + side, origin + imgui.get_content_region_avail().x - side
    else:
        left, right = log.left, log.right
    # Indent(0) is ImGui's default indent spacing, not none.
    indent = left - origin
    if indent:
        imgui.indent(indent)
    # Where the box and every line stop, as the window-local x the wrap takes.
    imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + right - imgui.get_cursor_screen_pos().x)
    try:
        if selected is None or (record := snap.get(selected)) is None:
            imgui.text_disabled("select an agent to talk to it")
            return

        if selected[1] is not None:
            # Sub-agents have no input channel of their own; they are driven by their
            # parent. Saying so beats offering a box that would silently do nothing.
            imgui.text_disabled("sub-agents cannot be messaged directly.")
            imgui.text_disabled("talk to the session that spawned this one.")
            return

        closed = widgets.closed_line(record, unloaded="")
        if closed is not None:
            # No box and no buttons. There is no process to message, interrupt or
            # close, and a disabled composer under "done" reads as a session that
            # ran here and finished.
            imgui.text_disabled(closed)
            return

        waiting = record.state is AgentState.AWAITING_INPUT
        if waiting:
            imgui.text_colored(P.state_awaiting_input.vec4, "this session is waiting for you")
        elif record.state is AgentState.SUPERVISING:
            # Measured, not assumed: a prompt sent while sub-agents are running is
            # dispatched as its own turn and answered without waiting for the fan-out
            # (scripts/verify_lead_turn_via_agent_session.py, ANSWERED-IN-WAIT-LOOP).
            # This line used to say the message would be read after the turn, which was
            # the operator's only information about a capability they already had.
            imgui.text_colored(
                P.state_supervising.vec4,
                "supervising its sub-agents - a message will be read now, not after them",
            )
        elif record.state.is_terminal:
            imgui.text_disabled(f"session {record.state.value} - it can no longer be messaged")
        else:
            imgui.text_disabled(f"{record.state.value} - a message will be read after this turn")

        imgui.spacing()
        draft = state.replies.get(selected, "")
        if state.focus_reply:
            imgui.set_keyboard_focus_here()
            state.focus_reply = False

        disabled = record.state.is_terminal
        if disabled:
            imgui.begin_disabled()
        submitted, draft = widgets.multiline_input(
            "##reply",
            draft,
            # As wide as the log's text, from where the box starts to its right edge.
            imgui.ImVec2(right - imgui.get_cursor_screen_pos().x, 80),
            wrap=wrap,
            flags=int(imgui.InputTextFlags_.allow_tab_input) | widgets.CTRL_ENTER_SUBMITS,
        )
        # Stored every frame, not on a change flag: under CTRL_ENTER_SUBMITS the
        # returned bool reports submission, so there is no per-keystroke signal to
        # gate on.
        state.replies[selected] = draft
        imgui.text_disabled("Ctrl+Enter sends, Enter for a new line")

        if wants_send(submitted, imgui.button("send"), draft):
            send(selected, draft.strip())
            state.replies[selected] = ""
            # Sending deactivates the box (ImGui clears the active id on validate), and
            # a reply is rarely the last thing said to a session that is still running.
            # Put the caret back.
            state.focus_reply = True
        if disabled:
            imgui.end_disabled()

        imgui.same_line()
        # Interrupt is the recoverable lever: it stops the current turn and keeps the
        # session and its context. Closing is what actually reclaims the subprocess.
        if imgui.button("interrupt"):
            interrupt(selected)
        imgui.same_line()
        if imgui.button("close session"):
            close(selected)
        imgui.text_disabled("interrupt stops this turn; close ends the session")
    finally:
        imgui.pop_text_wrap_pos()
        if indent:
            imgui.unindent(indent)
