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

NOT_DRAWN = "not sent - this call's body had not been drawn when approve was asked for; ask again"
NOT_WHOLE = (
    "not sent - the body was still being typeset when approve was asked for; "
    "ask again once it is all there"
)


@dataclass(frozen=True, slots=True)
class Refusal:
    """
    An approve that was asked for and not sent: the pending ids it covered, and why.

    Worded in the past tense on purpose. It is a record of one request, shown on the
    rows it covered until a decision is sent or it is cancelled, and it stays true
    after the body it was refused over has become whole.
    """

    asked: frozenset[str]
    text: str


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
    # What each pane last saw of each parked call's body: pending id -> pane ->
    # (the ImGui frame it drew the body in, source lines of it not typeset yet).
    # A reading and not a fact: it is as old as the pane's last draw of that body,
    # and ``handle_keys`` runs before any pane draws, so the newest it can be is
    # the frame before. Nothing in the snapshot implies it -- how much of a text is
    # built is the seam's and differs between two panes showing one call -- so it
    # is held here, written by ``body_shown`` and read only by ``unset_lines``.
    bodies: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    refusal: Refusal | None = None

    def body_shown(self, pane: str, pending_id: str, unbuilt: int, frame: int) -> None:
        """
        A pane drew this call's body in ``frame`` with ``unbuilt`` source lines of it
        still to typeset: zero for a whole body, and for any body in ImGui's own text.
        """
        self.bodies.setdefault(pending_id, {})[pane] = (frame, unbuilt)

    def prune(self, live_ids: set[str]) -> None:
        for stale in [k for k in self.reasons if k not in live_ids]:
            del self.reasons[stale]
        for stale in [k for k in self.edits if k not in live_ids]:
            del self.edits[stale]
        for stale in [k for k in self.diff_lines if k not in live_ids]:
            del self.diff_lines[stale]
        for stale in [k for k in self.bodies if k not in live_ids]:
            del self.bodies[stale]
        if self.refusal is not None and not (self.refusal.asked & live_ids):
            self.refusal = None
        if self.editing is not None and self.editing not in live_ids:
            self.editing = None
            self.edit_error = None


def unset_lines(state: ReviewState, pending_id: str) -> int | None:
    """
    How much of this call's body was missing when it was last on screen: ``None``
    when no pane has drawn it, zero when every pane showing it had all of it.

    The most any pane read, among the panes that drew it in the newest frame any of
    them did or the one before. Two panes show one call in views of their own, and
    one being whole does not make the other so; a pane that has stopped drawing
    the call -- its tab is behind another, the layout changed -- has a reading
    that says nothing about what is on screen now, and is passed over. The frame
    before counts because a click is settled part-way through a frame, after one
    pane has drawn and before the other has.
    """
    seen = state.bodies.get(pending_id)
    if not seen:
        return None
    newest = max(frame for frame, _ in seen.values())
    return max(unbuilt for frame, unbuilt in seen.values() if frame >= newest - 1)


def withheld(state: ReviewState, pending: PendingApproval, *, unread: bool = False) -> str | None:
    """
    Why this call may not be approved yet, or ``None`` when it may. Every approve
    path asks here and nowhere else.

    A long body is typeset over several frames, and the part that is up looks like
    all of it. So a call is approved only once its body was whole the last time it
    was drawn, and one never drawn is not approved at all: the keys are read before
    the panes draw, so on the frame the cursor lands on a row there is no reading
    for it, and "no reading" must not mean "nothing missing".

    ``unread`` is the batch forms' question. They approve calls nobody opened, by
    design and behind a count, so for them a body never drawn is not a reason; a
    body known to be short still is.
    """
    unset = unset_lines(state, pending.id)
    if unset is None:
        return None if unread else NOT_DRAWN
    return NOT_WHOLE if unset > 0 else None


def refusal_for(state: ReviewState, pending_id: str) -> str | None:
    """
    What to say on this call's row about an approve that was not sent, if one was.
    """
    refusal = state.refusal
    return refusal.text if refusal is not None and pending_id in refusal.asked else None


def _approve_batch(bridge: Bridge, state: ReviewState, batch: list[PendingApproval]) -> int:
    """
    Send all of ``batch`` or none of it. Returns how many were sent.

    None, when any body in it is known to be short: a batch that went out without
    the one call still being typeset would leave the operator believing the agent's
    queue was cleared, and the refusal names the calls that held it.
    """
    short = [p for p in batch if withheld(state, p, unread=True) is not None]
    if short:
        names = "; ".join(p.summary for p in short)
        state.refusal = Refusal(
            frozenset(p.id for p in batch),
            f"none sent - {len(short)} of these {len(batch)} calls had a body still being "
            f"typeset when approve all was asked for ({names}); open it until it is all "
            "there, then ask again",
        )
        return 0
    return sum(resolve(bridge, state, p, approved=True, unread=True) for p in batch)


def approve_all_for_node(bridge: Bridge, snap: Snapshot, state: ReviewState, node: NodeId) -> int:
    """
    Approve every pending call from one agent.

    Scoped batching is defensible in a way the global form is not: one agent's
    queue is usually one coherent piece of work, and the operator has just been
    reading its diffs. Returns how many were sent.
    """
    return _approve_batch(bridge, state, [p for p in snap.approvals if p.node == node])


def approve_everything(bridge: Bridge, snap: Snapshot, state: ReviewState) -> int:
    """
    Approve the entire queue across every agent. Only reachable after a confirm.

    This is in genuine tension with the premise -- "nothing is written until a human
    approves it" and "approve twelve things with one key" do not fully coexist. It
    exists because being the bottleneck for a fleet defeats the tool, and it is
    gated behind an explicit confirmation showing the count because the failure mode
    is silent and unrecoverable.
    """
    sent = _approve_batch(bridge, state, list(snap.approvals))
    state.confirming_global = False
    return sent


def resolve(
    bridge: Bridge,
    state: ReviewState,
    pending: PendingApproval,
    *,
    approved: bool,
    unread: bool = False,
) -> bool:
    """
    Send the decision, and answer whether it was sent. The store is not touched
    here -- the gate emits ApprovalResolved once the agent has actually been
    released, so the queue never shows an approval the agent did not receive.

    An approval is not sent while ``withheld`` has a reason, and the reason is left
    in ``state.refusal`` for the panes to say. A rejection is always sent: refusing
    a call does not need its body whole.
    """
    if approved:
        reason = withheld(state, pending, unread=unread)
        if reason is not None:
            state.refusal = Refusal(frozenset({pending.id}), reason)
            return False
    edited = None
    if approved and pending.id in state.edits:
        parsed = _parse_edit(state.edits[pending.id])
        if parsed is None:
            state.edit_error = "arguments are not valid JSON - not sent"
            return False
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
    state.refusal = None
    return True


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

    The letters are bindings for the bare key only. ``want_capture_keyboard`` says
    whether an ImGui *item* has the keyboard, and a view that handles its own
    chords without submitting one -- select-all and copy over a diff -- leaves it
    false, so Ctrl+A would otherwise arrive here as ``a`` and approve what was
    being selected. Shift is not a chord in this sense: it is the difference
    between ``a`` and ``A``. Escape is exempt because cancelling is the safe
    direction and should not depend on which modifiers happen to be down.

    This runs before any pane draws, so ``a`` is answered from what the panes showed
    up to the frame before (``withheld``): a body short of its text, or one never
    drawn -- the row the cursor reached on this very frame -- is not approved.
    ``r`` and ``e`` ask nothing of the body.
    """
    io = imgui.get_io()
    if io.want_capture_keyboard:
        return
    chord = io.key_ctrl or io.key_alt or io.key_super

    def letter(key: imgui.Key) -> bool:
        return not chord and imgui.is_key_pressed(key)

    if letter(imgui.Key.j):
        focus.move(snap, 1)
    if letter(imgui.Key.k):
        focus.move(snap, -1)

    if imgui.is_key_pressed(imgui.Key.escape):
        state.editing = None
        state.confirming_global = False
        state.edit_error = None
        state.refusal = None

    current = focus.obligation(snap)
    if not isinstance(current, ApprovalNeeded):
        return
    pending = current.approval

    if letter(imgui.Key.a):
        # Shift+A is the batch form, scoped to the focused agent. Tested as one
        # branch rather than two ifs: an unguarded pair fires both, so Shift+A
        # would approve the focused call *and* everything beside it.
        if io.key_shift:
            approve_all_for_node(bridge, snap, state, pending.node)
        else:
            resolve(bridge, state, pending, approved=True)
    if letter(imgui.Key.r):
        state.focus_reason = True
    if letter(imgui.Key.e):
        state.editing = pending.id
        state.edits.setdefault(pending.id, pretty_args(pending))
    if imgui.is_key_pressed(imgui.Key.escape):
        state.edits.pop(pending.id, None)


def pretty_args(pending: PendingApproval) -> str:
    import json

    return json.dumps(dict(pending.raw_args), indent=2, sort_keys=True)
