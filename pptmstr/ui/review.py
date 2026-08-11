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
    ("esc", "cancel edit / confirm"),
)


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
    Keyboard shortcuts, active only when no text field has the keyboard.

    ``want_capture_keyboard`` is the guard that matters: without it, typing the
    letter 'a' into a rejection reason would approve the thing you are explaining
    why you rejected.

    j/k walk the whole inbox, not just the approvals in it. Skipping over questions
    and failures would make the two obligation kinds that have no other surface
    unreachable from the keyboard, which is most of the way back to them having no
    surface at all. The action keys below then do nothing on a row that is not an
    approval, which is the right kind of nothing: the cursor is still where the
    operator put it, and the expanded row shows what it *can* do.
    """
    if imgui.get_io().want_capture_keyboard:
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

    if imgui.is_key_pressed(imgui.Key.a):
        # Shift+A is the batch form, scoped to the focused agent. Tested as one
        # branch rather than two ifs: an unguarded pair fires both, so Shift+A
        # would approve the focused call *and* everything beside it.
        if imgui.get_io().key_shift:
            approve_all_for_node(bridge, snap, state, pending.node)
        else:
            resolve(bridge, state, pending, approved=True)
    if imgui.is_key_pressed(imgui.Key.r):
        state.focus_reason = True
    if imgui.is_key_pressed(imgui.Key.e):
        state.editing = pending.id
        state.edits.setdefault(pending.id, pretty_args(pending))
    if imgui.is_key_pressed(imgui.Key.escape):
        state.edits.pop(pending.id, None)


def pretty_args(pending: PendingApproval) -> str:
    import json

    return json.dumps(dict(pending.raw_args), indent=2, sort_keys=True)
