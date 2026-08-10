"""
Composing work: starting sessions, and talking to the ones that are running.

Until this existed the application could only be driven from the command line and
could only be talked to in one direction. An agent that asked a question ended its
turn and there was nowhere to answer it, which made the thing unusable for the
interactive work it was built for.

Two surfaces:

* **Launch** — a new session, with the directory and model chosen per session
  rather than per process. Working directory is the interesting one: an
  orchestrator whose agents all share the launching shell's cwd cannot run work
  across projects, which is most of the point.

* **Conversation** — send another prompt to a live session, interrupt it, or close
  it. ``AWAITING_INPUT`` is the state this pane exists to answer.

Everything leaves through ``Bridge.submit`` onto the asyncio thread. Nothing here
touches a session object directly, so the pool stays single-threaded.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from imgui_bundle import imgui

from ..model import AgentState, NodeId, Snapshot
from ..theme import P

# Verified against the model-config docs at build time (design §7, trap 9). Listed
# rather than free-text so a typo cannot become a session that fails on first turn.
MODELS: tuple[str, ...] = (
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
)


@dataclass
class ComposeState:
    """Draft text for the launcher and per-session reply boxes."""

    task: str = ""
    cwd: str = "."
    model_index: int = 0
    # Reply drafts, keyed by node. Kept per node so switching selection mid-sentence
    # does not silently discard what was being typed to a different agent.
    replies: dict[NodeId, str] = field(default_factory=dict)
    focus_reply: bool = False

    def prune(self, snap: Snapshot) -> None:
        for node in [n for n in self.replies if n not in snap.nodes]:
            del self.replies[node]


def draw_launcher(
    state: ComposeState,
    snap: Snapshot,
    *,
    running: int,
    queued: int,
    cap: int,
    launch: Callable[[str, str, str], None],
) -> None:
    """The new-session form."""
    imgui.text_disabled("start a session")
    imgui.spacing()

    imgui.text("task")
    changed, state.task = imgui.input_text_multiline(
        "##task", state.task, imgui.ImVec2(-1, 90), imgui.InputTextFlags_.allow_tab_input
    )

    imgui.set_next_item_width(220 * imgui.get_font_size() / 16.0)
    _, state.model_index = imgui.combo("model", state.model_index, list(MODELS))

    imgui.set_next_item_width(-1)
    _, state.cwd = imgui.input_text_with_hint(
        "##cwd", "working directory (agents run here)", state.cwd
    )

    ready = bool(state.task.strip())
    if not ready:
        imgui.begin_disabled()
    if imgui.button("launch"):
        launch(state.task.strip(), MODELS[state.model_index], state.cwd.strip() or ".")
        state.task = ""
    if not ready:
        imgui.end_disabled()

    imgui.same_line()
    if running >= cap:
        # Not a refusal -- the pool queues -- but worth saying, because a session
        # that sits in SPAWNING while others work otherwise looks broken.
        imgui.text_colored(P.warn.vec4, f"{running}/{cap} live; this one will queue")
    else:
        imgui.text_disabled(f"{running}/{cap} live" + (f", {queued} queued" if queued else ""))


def draw_conversation(
    snap: Snapshot,
    state: ComposeState,
    selected: NodeId | None,
    *,
    send: Callable[[NodeId, str], None],
    interrupt: Callable[[NodeId], None],
    close: Callable[[NodeId], None],
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
    changed, draft = imgui.input_text_multiline(
        "##reply", draft, imgui.ImVec2(-1, 80), imgui.InputTextFlags_.allow_tab_input
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
