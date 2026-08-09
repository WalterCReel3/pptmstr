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
from ..model import PendingApproval, Snapshot
from ..theme import P

# Keys that act on the selected item. Kept in one place because the help line and
# the handler must not drift apart.
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("j / k", "next / previous"),
    ("a", "approve"),
    ("r", "reject"),
    ("e", "edit arguments"),
    ("esc", "cancel edit"),
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
        resolve(bridge, state, pending, approved=True)
    if imgui.is_key_pressed(imgui.Key.r):
        state.focus_reason = True
    if imgui.is_key_pressed(imgui.Key.e):
        state.editing = pending.id
        state.edits.setdefault(pending.id, _pretty_args(pending))
    if imgui.is_key_pressed(imgui.Key.escape):
        state.editing = None
        state.edits.pop(pending.id, None)
        state.edit_error = None


def _pretty_args(pending: PendingApproval) -> str:
    import json

    return json.dumps(dict(pending.raw_args), indent=2, sort_keys=True)


def draw_queue(snap: Snapshot, state: ReviewState, now: float | None = None) -> None:
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
    if not imgui.begin_table("##queue", 3, flags):
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
