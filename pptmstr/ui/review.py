"""
The review queue: the surface where approvals are actually worked.

Split in two on purpose. The **list** is a cross-agent work queue ordered by how
long each item has been blocked -- the tree tells you *that* something needs you,
this tells you *what*, and approving one agent at a time through the tree is the
bottleneck §5.4 exists to remove. The **detail** pane shows one item in full,
which is the only way a diff gets the width and height to be readable.

Driven by the keyboard. The layout matters less than not reaching for the mouse
forty times an hour: j/k move, a approves, r rejects, e edits. Buttons stay for
discovery.

Everything here is presentation state (design §6). The decision leaves through the
Bridge, and the store learns about it when the gate emits ApprovalResolved -- so
what is rendered is always what the agent actually saw, never an optimistic guess.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from imgui_bundle import imgui

from ..approval import diff_line_kind
from ..bridge import Bridge, Decision
from ..model import NodeId, PendingApproval, Snapshot
from ..theme import P

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

    selected: str | None = None
    reasons: dict[str, str] = field(default_factory=dict)
    edits: dict[str, str] = field(default_factory=dict)
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
        if self.editing is not None and self.editing not in live_ids:
            self.editing = None
            self.edit_error = None
        if self.selected is not None and self.selected not in live_ids:
            self.selected = None

    def ensure_selection(self, queue: tuple[PendingApproval, ...]) -> None:
        if queue and self.selected is None:
            self.selected = queue[0].id

    def move(self, queue: tuple[PendingApproval, ...], delta: int) -> None:
        if not queue:
            return
        ids = [p.id for p in queue]
        try:
            index = ids.index(self.selected or "")
        except ValueError:
            index = 0 if delta > 0 else len(ids) - 1
        else:
            index = max(0, min(len(ids) - 1, index + delta))
        self.selected = ids[index]


def _selected(snap: Snapshot, state: ReviewState) -> PendingApproval | None:
    return next((p for p in snap.review_queue if p.id == state.selected), None)


def approve_all_for_node(bridge: Bridge, snap: Snapshot, state: ReviewState, node: NodeId) -> int:
    """
    Approve every pending call from one agent.

    Scoped batching is defensible in a way the global form is not: one agent's
    queue is usually one coherent piece of work, and the operator has just been
    reading its diffs. Returns how many were sent.
    """
    sent = 0
    for pending in snap.review_queue:
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
    for pending in snap.review_queue:
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


def handle_keys(snap: Snapshot, state: ReviewState, bridge: Bridge) -> None:
    """
    Keyboard shortcuts, active only when no text field has the keyboard.

    ``want_capture_keyboard`` is the guard that matters: without it, typing the
    letter 'a' into a rejection reason would approve the thing you are explaining
    why you rejected.
    """
    if imgui.get_io().want_capture_keyboard:
        return
    pending = _selected(snap, state)
    if imgui.is_key_pressed(imgui.Key.j):
        state.move(snap.review_queue, 1)
    if imgui.is_key_pressed(imgui.Key.k):
        state.move(snap.review_queue, -1)
    if pending is None:
        return
    if imgui.is_key_pressed(imgui.Key.a):
        # Shift+A is the batch form, scoped to the selected agent. Tested as one
        # branch rather than two ifs: an unguarded pair fires both, so Shift+A
        # would approve the selection *and* everything beside it.
        if imgui.get_io().key_shift:
            approve_all_for_node(bridge, snap, state, pending.node)
        else:
            resolve(bridge, state, pending, approved=True)
    if imgui.is_key_pressed(imgui.Key.r):
        state.focus_reason = True
    if imgui.is_key_pressed(imgui.Key.e):
        state.editing = pending.id
        state.edits.setdefault(pending.id, _pretty_args(pending))
    if imgui.is_key_pressed(imgui.Key.escape):
        state.editing = None
        state.confirming_global = False
        state.edits.pop(pending.id, None)
        state.edit_error = None


def _pretty_args(pending: PendingApproval) -> str:
    import json

    return json.dumps(dict(pending.raw_args), indent=2, sort_keys=True)


def draw_queue(
    snap: Snapshot, state: ReviewState, bridge: Bridge, now: float | None = None
) -> None:
    """The work list. Compact by design: this is scanned, not read."""
    now = time.time() if now is None else now
    state.prune({p.id for p in snap.review_queue})
    state.ensure_selection(snap.review_queue)

    if not snap.review_queue:
        imgui.text_disabled("nothing awaiting review")
        imgui.spacing()
        imgui.text_disabled("agents run until they need to change something.")
        return

    imgui.text_colored(P.state_awaiting.vec4, f"{len(snap.review_queue)} awaiting review")
    imgui.same_line()
    imgui.text_disabled("  " + "   ".join(f"{k} {label}" for k, label in SHORTCUTS[:3]))
    imgui.separator()

    flags = imgui.TableFlags_.row_bg | imgui.TableFlags_.scroll_y
    # Reserve the footer's height. A scrolling table otherwise claims the whole
    # remaining pane and pushes the batch controls below the fold, where an action
    # that needs deliberate discovery becomes one that cannot be found at all.
    if not imgui.begin_table("##queue", 3, flags, imgui.ImVec2(0, -32)):
        return
    imgui.table_setup_column("agent", imgui.TableColumnFlags_.width_fixed, 130.0)
    imgui.table_setup_column("waiting", imgui.TableColumnFlags_.width_fixed, 70.0)
    imgui.table_setup_column("call", imgui.TableColumnFlags_.width_stretch)
    imgui.table_setup_scroll_freeze(0, 1)
    imgui.table_headers_row()

    for pending in snap.review_queue:
        imgui.table_next_row()
        imgui.table_next_column()
        imgui.push_id(pending.id)

        record = snap.nodes.get(pending.node)
        label = (record.agent_type if record else None) or "session"
        clicked, _ = imgui.selectable(
            f"{label}##row",
            state.selected == pending.id,
            imgui.SelectableFlags_.span_all_columns,
        )
        if clicked:
            state.selected = pending.id

        imgui.table_next_column()
        # How long an agent has been blocked is the queue's ordering key and the
        # only number here that gets worse on its own.
        imgui.text_disabled(_waited(pending.requested_at, now))

        imgui.table_next_column()
        imgui.text(pending.summary)
        imgui.pop_id()

    imgui.end_table()
    _draw_batch_controls(snap, state, bridge)


def _draw_batch_controls(snap: Snapshot, state: ReviewState, bridge: Bridge) -> None:
    """
    Batch actions, with the global one behind a confirm.

    The scoped button is safe enough to be one click. The global one is not: it can
    write things the operator never looked at, and that failure is silent and
    unrecoverable, so it costs a second click that states the count.
    """
    selected = _selected(snap, state)
    if selected is not None:
        record = snap.nodes.get(selected.node)
        label = (record.agent_type if record else None) or "this agent"
        count = sum(1 for p in snap.review_queue if p.node == selected.node)
        if imgui.button(f"approve all {count} from {label}"):
            approve_all_for_node(bridge, snap, state, selected.node)
        imgui.same_line()

    total = len(snap.review_queue)
    if state.confirming_global:
        imgui.text_colored(P.danger.vec4, f"approve all {total} without reading them?")
        imgui.same_line()
        if imgui.button("yes, approve all"):
            approve_everything(bridge, snap, state)
        imgui.same_line()
        if imgui.button("cancel"):
            state.confirming_global = False
    elif imgui.button(f"approve all {total}..."):
        state.confirming_global = True


def _waited(requested_at: float, now: float) -> str:
    seconds = max(0.0, now - requested_at)
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m{int(seconds) % 60:02d}s"
    return f"{seconds / 3600:.0f}h"


def draw_detail(snap: Snapshot, state: ReviewState, bridge: Bridge) -> None:
    """One item in full: the diff, the reason field, and the actions."""
    pending = _selected(snap, state)
    if pending is None:
        imgui.text_disabled("select a pending call to review it")
        imgui.spacing()
        for key, label in SHORTCUTS:
            imgui.text_disabled(f"  {key:<6} {label}")
        return

    record = snap.nodes.get(pending.node)
    imgui.text_colored(P.text_strong.vec4, pending.tool_name)
    imgui.same_line()
    imgui.text_disabled(f"  {(record.agent_type if record else None) or 'session'}")
    imgui.text_disabled(pending.summary)
    imgui.separator()

    if state.editing == pending.id:
        _draw_editor(state, pending)
    elif pending.diff:
        _draw_diff(pending.diff)
    else:
        # No diff is a real answer, not a gap: a Bash command has nothing
        # diff-shaped to show, and inventing one would be worse than the arguments.
        imgui.text_disabled("no diff for this call - arguments:")
        imgui.spacing()
        for key, value in pending.raw_args.items():
            imgui.text_wrapped(f"{key} = {value!r}")

    imgui.separator()
    _draw_actions(state, pending, bridge)


def _draw_diff(diff: str) -> None:
    if not imgui.begin_child("##diff", imgui.ImVec2(0, -110)):
        imgui.end_child()
        return
    for line in diff.splitlines():
        kind = diff_line_kind(line)
        colour = {
            "add": P.diff_add,
            "remove": P.diff_remove,
            "meta": P.accent,
            "context": P.diff_context,
        }[kind]
        # The +/- gutter stays in the text regardless of colour. That is the
        # non-hue channel, and it is what keeps a diff readable in high contrast
        # and to a colour-deficient operator (§6.1).
        imgui.text_colored(colour.vec4, line if line else " ")
    imgui.end_child()


def _draw_editor(state: ReviewState, pending: PendingApproval) -> None:
    imgui.text_colored(P.focus.vec4, "editing arguments - approve runs the corrected call")
    text = state.edits.get(pending.id, "")
    changed, new_text = imgui.input_text_multiline(
        "##edit", text, imgui.ImVec2(-1, -110), imgui.InputTextFlags_.allow_tab_input
    )
    if changed:
        state.edits[pending.id] = new_text
        state.edit_error = None


def _draw_actions(state: ReviewState, pending: PendingApproval, bridge: Bridge) -> None:
    if state.edit_error:
        imgui.text_colored(P.danger.vec4, state.edit_error)

    if state.focus_reason:
        imgui.set_keyboard_focus_here()
        state.focus_reason = False
    reason = state.reasons.get(pending.id, "")
    changed, new_reason = imgui.input_text_with_hint(
        "##reason", "reason (shown to the agent on reject)", reason
    )
    if changed:
        state.reasons[pending.id] = new_reason

    if imgui.button("approve"):
        resolve(bridge, state, pending, approved=True)
    imgui.same_line()
    if imgui.button("reject"):
        resolve(bridge, state, pending, approved=False)
    imgui.same_line()
    if state.editing == pending.id:
        if imgui.button("cancel edit"):
            state.editing = None
            state.edits.pop(pending.id, None)
            state.edit_error = None
    elif imgui.button("edit"):
        state.editing = pending.id
        state.edits.setdefault(pending.id, _pretty_args(pending))

    imgui.same_line()
    edited = pending.id in state.edits and state.editing == pending.id
    imgui.text_disabled("  approve runs the edited arguments" if edited else "  a / r / e")
