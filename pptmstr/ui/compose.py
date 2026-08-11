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
from . import widgets


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


def draw_conversation(
    snap: Snapshot,
    state: ComposeState,
    selected: NodeId | None,
    *,
    send: Callable[[NodeId, str], None],
    interrupt: Callable[[NodeId], None],
    close: Callable[[NodeId], None],
    wrap: bool = True,
) -> None:
    """Reply to, interrupt, or close the selected session."""
    state.prune(snap)

    if selected is None or (record := snap.get(selected)) is None:
        imgui.text_disabled("select an agent to talk to it")
        return

    if selected[1] is not None:
        # Sub-agents have no input channel of their own; they are driven by their
        # parent. Saying so beats offering a box that would silently do nothing.
        imgui.text_disabled("sub-agents cannot be messaged directly.")
        imgui.text_disabled("talk to the session that spawned this one.")
        return

    waiting = record.state is AgentState.AWAITING_INPUT
    if waiting:
        imgui.text_colored(P.state_awaiting_input.vec4, "this session is waiting for you")
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
    changed, draft = widgets.multiline_input(
        "##reply",
        draft,
        imgui.ImVec2(-1, 80),
        wrap=wrap,
        flags=int(imgui.InputTextFlags_.allow_tab_input),
    )
    if changed:
        state.replies[selected] = draft

    if imgui.button("send") and draft.strip():
        send(selected, draft.strip())
        state.replies[selected] = ""
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
    imgui.same_line()
    imgui.text_disabled("interrupt stops this turn; close ends the session")
