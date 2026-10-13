"""
Approval decisions: the draft state behind a review, and the way one leaves.

Not a pane. The queue and the diff live in ``inbox.py``, where an approval is one
of three kinds of obligation and its diff opens inside the row rather than in a
second pane with a second cursor. What is left here is the part that is about
*deciding* rather than about drawing: the half-typed rejection reason, the edited
arguments, the batch forms, and the keyboard.

Driven by the keyboard. The layout matters less than not reaching for the mouse
forty times an hour: j/k move, a approves, r rejects, e edits. Buttons stay for
discovery.

Everything here is presentation state (design §6). The decision leaves through the
Bridge, and the store learns about it when the gate emits ApprovalResolved -- so
what is rendered is always what the agent actually saw, never an optimistic guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from imgui_bundle import imgui

from ..bridge import Bridge, Decision
from ..model import ApprovalNeeded, NodeId, PendingApproval, Snapshot
from .focus import FocusState

# Keys that act on the selected item. Kept in one place because the help line and
# the handler must not drift apart.
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("j / k", "next / previous"),
    ("a", "approve"),
    ("r", "reject"),
    ("e", "edit arguments"),
    ("A", "approve all from this agent"),
    ("esc", "cancel edit / confirm; after typing, makes these keys act again"),
)


@dataclass
class Hold:
    """
    Whether the letter keys are the operator's shortcuts or the operator's prose.

    A text box can stop existing under the person typing in it: the decision is
    withdrawn by its asker, the parked call is settled by its agent ending, the
    session closes. The queue's cursor then lands on the next row, Dear ImGui lets
    go of the keyboard one frame later, and nothing the operator did marks the
    moment -- the next ``a`` of a sentence would approve a call nobody read. So
    having the keyboard free is not enough to act on a key.

    **The rule.** Once a text box has had the keyboard, no shortcut acts until the
    operator has shown they are no longer typing, and there are two ways to show
    it: Escape, or a mouse click. Neither can be part of a sentence. It is one rule
    for every text box in the application and for every way a box goes, because
    it asks only what the keyboard was last doing and never which box or why.

    What it costs: an operator who types anything -- a reason, a reply, an edit --
    presses Escape or clicks once before ``j``/``k``/``a`` answer again, including
    after a reply sent with Ctrl+Enter, since sending puts the caret back in the
    box, and after ``r`` or ``e``, which open a box (``begin``). ``held`` is what
    the inbox reads to say so, so dead keys are explained.

    A click made while the box still has the keyboard counts if the box is not
    there on the next frame: that is clicking a button to leave it. A click made
    in a box that then stays is followed by a frame of typing, which re-arms.
    """

    # A text box has had the keyboard and nothing since has shown the typing over.
    typing: bool = False
    # ``typing``, in a frame where no text box has the keyboard: the keys are dead
    # and the operator is owed the reason.
    held: bool = False
    # Whether the last frame a text box had the keyboard carried Escape or a click.
    _leaving: bool = False
    # What the last ``step`` answered: whether a bare key may act on this frame.
    # Kept because the step is taken once a frame and more than its caller acts
    # on keys (``app._handle_keys``), and no reading of ``typing`` afterwards
    # tells the frame that ends a hold from one nothing was typed before.
    free: bool = True

    def step(self, *, text_input: bool, gesture: bool) -> bool:
        """
        Once per frame. Whether a shortcut may act on this frame's keys, which is
        also left in ``free`` until the next step.

        ``text_input`` is whether a text box has the keyboard; ``gesture`` is
        whether Escape or a mouse button went down this frame. The frame that ends
        a hold acts on nothing either: the Escape that ends it is not also the
        Escape that cancels an edit on a row the cursor was moved to.
        """
        self.free = self._stepped(text_input, gesture)
        return self.free

    def begin(self) -> None:
        """
        A shortcut has just opened a text box: the hold starts at that keypress.

        A box asked for on this frame has the keyboard a frame or more later, or
        only once it is clicked, and until then no text box has it -- so the
        first letters of what the operator types next would be read as
        shortcuts, and a reason beginning with ``a`` approved the call it was
        rejecting. From here the keys are prose until Escape or a click, as
        after any typing, whether or not the box ever takes the keyboard.
        """
        self.typing = True
        self.held = False
        self._leaving = False
        self.free = False

    def _stepped(self, text_input: bool, gesture: bool) -> bool:
        if text_input:
            self.typing = True
            self.held = False
            self._leaving = gesture
            return False
        if not self.typing:
            return True
        if gesture or self._leaving:
            self.typing = False
            self.held = False
            self._leaving = False
        else:
            self.held = True
        return False


@dataclass
class ReviewState:
    """
    UI-side state for the review loop, keyed by pending id and pruned when an item
    leaves the queue.

    The edit buffer is deliberately here rather than in the store: a half-typed
    correction is not a fact about the world, and an approval that gets cancelled
    must take its draft with it.
    """

    reasons: dict[str, str] = field(default_factory=dict)
    edits: dict[str, str] = field(default_factory=dict)
    # Diff text split into lines, cached per pending id. Pruned with the rest.
    diff_lines: dict[str, list[str]] = field(default_factory=dict)
    editing: str | None = None
    edit_error: str | None = None
    focus_reason: bool = False
    # Set when a global approve-all has been asked for but not yet confirmed.
    # Approving everything across every agent is the one action here that can
    # write things the operator never looked at, so it does not happen on one
    # keystroke -- see confirm_global_approve.
    confirming_global: bool = False
    # Whether the keys are shortcuts yet. Here and not in ``handle_keys``' caller
    # because the inbox header says when they are not, and reads it from this.
    hold: Hold = field(default_factory=Hold)

    def prune(self, live_ids: set[str]) -> None:
        for stale in [k for k in self.reasons if k not in live_ids]:
            del self.reasons[stale]
        for stale in [k for k in self.edits if k not in live_ids]:
            del self.edits[stale]
        for stale in [k for k in self.diff_lines if k not in live_ids]:
            del self.diff_lines[stale]
        if self.editing is not None and self.editing not in live_ids:
            self.editing = None
            self.edit_error = None


def approve_all_for_node(bridge: Bridge, snap: Snapshot, state: ReviewState, node: NodeId) -> int:
    """
    Approve every pending call from one agent.

    Scoped batching is defensible in a way the global form is not: one agent's
    queue is usually one coherent piece of work, and the operator has just been
    reading its diffs. Returns how many were sent.
    """
    sent = 0
    for pending in snap.approvals:
        if pending.node == node:
            resolve(bridge, state, pending, approved=True)
            sent += 1
    return sent


def approve_everything(bridge: Bridge, snap: Snapshot, state: ReviewState) -> int:
    """
    Approve the entire queue across every agent. Only reachable after a confirm.

    This is in genuine tension with the premise -- "nothing is written until a human
    approves it" and "approve twelve things with one key" do not fully coexist. It
    exists because being the bottleneck for a fleet defeats the tool, and it is
    gated behind an explicit confirmation showing the count because the failure mode
    is silent and unrecoverable.
    """
    sent = 0
    for pending in snap.approvals:
        resolve(bridge, state, pending, approved=True)
        sent += 1
    state.confirming_global = False
    return sent


def resolve(
    bridge: Bridge, state: ReviewState, pending: PendingApproval, *, approved: bool
) -> None:
    """
    Send the decision. The store is not touched here -- the gate emits
    ApprovalResolved once the agent has actually been released, so the queue never
    shows an approval the agent did not receive.
    """
    edited = None
    if approved and pending.id in state.edits:
        parsed = _parse_edit(state.edits[pending.id])
        if parsed is None:
            state.edit_error = "arguments are not valid JSON - not sent"
            return
        edited = parsed
    bridge.resolve(
        pending.id,
        Decision(
            approved=approved,
            reason=state.reasons.get(pending.id) or None,
            edited_args=edited,
        ),
    )
    state.edit_error = None


def _parse_edit(text: str) -> dict[str, object] | None:
    import json

    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def handle_keys(snap: Snapshot, focus: FocusState, state: ReviewState, bridge: Bridge) -> None:
    """
    Keyboard shortcuts, active only when the keys cannot be somebody's prose.

    Two guards, and they answer different questions. ``want_capture_keyboard`` is
    this frame: a widget has the keyboard, so typing the letter 'a' into a
    rejection reason does not approve the thing being rejected. ``Hold`` is the
    frames after: a text box that had the keyboard may have been removed from
    under the typist, and the keyboard being free again says nothing about
    whether they have noticed.

    j/k walk the whole inbox, not just the approvals in it. Skipping over questions
    and failures would make the two obligation kinds that have no other surface
    unreachable from the keyboard, which is most of the way back to them having no
    surface at all. The action keys below then do nothing on a row that is not an
    approval, which is the right kind of nothing: the cursor is still where the
    operator put it, and the expanded row shows what it *can* do.
    """
    io = imgui.get_io()
    free = state.hold.step(text_input=bool(io.want_text_input), gesture=_stopped_typing())
    if not free or io.want_capture_keyboard:
        return

    if imgui.is_key_pressed(imgui.Key.j):
        focus.move(snap, 1)
    if imgui.is_key_pressed(imgui.Key.k):
        focus.move(snap, -1)

    if imgui.is_key_pressed(imgui.Key.escape):
        state.editing = None
        state.confirming_global = False
        state.edit_error = None

    current = focus.obligation(snap)
    if not isinstance(current, ApprovalNeeded):
        return
    pending = current.approval

    # The keys that open a text box come first and end the frame's shortcuts:
    # what is typed next is for the box, and an ``a`` that shares the frame with
    # one of them is the start of it (``Hold.begin``).
    opened = False
    if imgui.is_key_pressed(imgui.Key.r):
        state.focus_reason = True
        opened = True
    if imgui.is_key_pressed(imgui.Key.e):
        state.editing = pending.id
        state.edits.setdefault(pending.id, pretty_args(pending))
        opened = True
    if opened:
        state.hold.begin()
        return

    if imgui.is_key_pressed(imgui.Key.a):
        # Shift+A is the batch form, scoped to the focused agent. Tested as one
        # branch rather than two ifs: an unguarded pair fires both, so Shift+A
        # would approve the focused call *and* everything beside it.
        if imgui.get_io().key_shift:
            approve_all_for_node(bridge, snap, state, pending.node)
        else:
            resolve(bridge, state, pending, approved=True)
    if imgui.is_key_pressed(imgui.Key.escape):
        state.edits.pop(pending.id, None)


def _stopped_typing() -> bool:
    """Whether this frame carries Escape or a mouse press: see ``Hold``."""
    return bool(imgui.is_key_pressed(imgui.Key.escape)) or any(
        bool(imgui.is_mouse_clicked(button))
        for button in (imgui.MouseButton_.left, imgui.MouseButton_.right, imgui.MouseButton_.middle)
    )


def pretty_args(pending: PendingApproval) -> str:
    import json

    return json.dumps(dict(pending.raw_args), indent=2, sort_keys=True)
