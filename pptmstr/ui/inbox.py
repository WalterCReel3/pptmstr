"""
The inbox: everything waiting on the operator, oldest first, in one pane.

This is the application. The operator is a bottleneck by design, so the screen is
the bottleneck's work surface -- approvals, questions and failures merged into a
single queue rather than split across a pane, a tree badge and nothing at all.

**The row under the cursor expands in place.** A parked tool call opens its own
diff and approve/reject/edit, a question opens a composer, a failure opens the
error and a way to clear it. Every decision is made here, at the row, so there is
never a diff on screen belonging to a different agent than the one an ``a`` would
approve.

Everything in a row is clipped -- fixed columns, ``ellipsis``, fixed-height bodies
-- because this pane is scanned rather than read. The unclipped, wrapped rendering
of the same obligation lives in ``ui/detail.py``, which follows this pane's cursor
and cannot move it. Two surfaces, one cursor, opposite rules about width.

Identity on every row is the session's **task**, qualified by project and, for a
sub-agent, its name. Every root session is called "session"; at twenty sessions the
old queue read "session" on six rows of eight. The fix that landed for the rail's
cards did not generalise here on its own, because the inbox had only ever been
looked at with seven sessions in one project.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from ..approval import diff_line_kind
from ..bridge import Bridge
from ..model import (
    AgentState,
    ApprovalNeeded,
    NodeId,
    Obligation,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from ..theme import OBLIGATION_GLYPH, STATE_GLYPH, P
from . import projects, review
from .compose import ComposeState
from .focus import FocusState
from .widgets import ellipsis, format_elapsed, multiline_input

# Column origins, measured from the row's left edge. Fixed zones with each field
# ellipsized into its own, rather than laid out with same_line(): letting the fields
# push each other along means a long agent name shifts the wait time under the
# summary, and the summary is the column being scanned down.
_X_GLYPH = 10.0
_X_TITLE = 34.0
_X_QUALIFIER = 254.0
_X_WAIT_END = 458.0
_X_SUMMARY = 476.0

_TITLE_W = _X_QUALIFIER - _X_TITLE - 10.0
_QUALIFIER_W = 150.0

_SMALL_FONT = 12.5


@dataclass
class InboxActions:
    """
    What the expanded row can do. Passed in so this pane touches no session object
    and no event loop -- everything leaves through the Bridge or the pool, on the
    thread that owns them.
    """

    send: Callable[[NodeId, str], None]
    interrupt: Callable[[NodeId], None]
    close: Callable[[NodeId], None]
    dismiss: Callable[[NodeId], None]
    relaunch: Callable[[str, str, str], None]


def _small() -> None:
    imgui.push_font(None, _SMALL_FONT)


def _normal() -> None:
    imgui.pop_font()


def draw(
    snap: Snapshot,
    focus: FocusState,
    state: review.ReviewState,
    compose: ComposeState,
    bridge: Bridge,
    actions: InboxActions,
    now: float,
    wrap: bool = True,
) -> None:
    """Build the inbox from one snapshot. Never reads the store."""
    state.prune({p.id for p in snap.approvals})
    compose.prune(snap)

    if not snap.needs_you:
        _zero_state(snap, now)
        return

    imgui.spacing()
    oldest = format_elapsed(now - min(o.since for o in snap.needs_you))
    imgui.text_colored(P.state_awaiting.vec4, f"{len(snap.needs_you)} need you")
    imgui.same_line()
    imgui.text_disabled(f"· oldest {oldest} · j/k move · a approve · r reject · e edit")
    imgui.separator()
    imgui.spacing()

    current = focus.obligation(snap)
    for obligation in snap.needs_you:
        _row(
            snap,
            focus,
            state,
            compose,
            bridge,
            actions,
            obligation=obligation,
            expanded=current is not None and obligation.key == current.key,
            now=now,
            wrap=wrap,
        )

    _batch_controls(snap, focus, state, bridge)


def identity(snap: Snapshot, obligation: Obligation) -> tuple[str, str]:
    """
    The two strings that say whose obligation this is.

    Title identifies, qualifier disambiguates. A sub-agent's parked call belongs to
    the sub-agent but is only findable via the session that spawned it, so the
    session's task is the headline and the sub-agent's name rides alongside it.

    Public because DETAIL heads its pane with the same two strings. One rule, in the
    pane that owns the queue -- two panes naming the same obligation differently is
    a small version of the defect the single cursor exists to prevent.
    """
    session: NodeId = (obligation.node[0], None)
    root = snap.nodes.get(session)
    node = snap.nodes.get(obligation.node)
    title = (root.task if root else None) or "session"
    project = projects.project_key(root.cwd if root else None)
    if obligation.node == session:
        return title, project
    sub = (node.agent_type if node else None) or "sub-agent"
    return title, f"{project} / {sub}"


def _row(
    snap: Snapshot,
    focus: FocusState,
    state: review.ReviewState,
    compose: ComposeState,
    bridge: Bridge,
    actions: InboxActions,
    *,
    obligation: Obligation,
    expanded: bool,
    now: float,
    wrap: bool = True,
) -> None:
    draw = imgui.get_window_draw_list()
    line_h = imgui.get_text_line_height_with_spacing()
    colour = P.obligation(obligation.kind)

    imgui.push_id(obligation.key)
    origin = imgui.get_cursor_screen_pos()
    width = imgui.get_content_region_avail().x
    imgui.invisible_button("##row", imgui.ImVec2(max(width, 40.0), line_h + 6.0))
    hovered = imgui.is_item_hovered()
    if imgui.is_item_clicked():
        focus.to_obligation(obligation)

    lo = imgui.ImVec2(origin.x, origin.y)
    hi = imgui.ImVec2(origin.x + width, origin.y + line_h + 6.0)
    if expanded or hovered:
        draw.add_rect_filled(lo, hi, P.panel_alt.u32)
    if expanded:
        draw.add_rect(lo, hi, P.focus.u32, 0.0, 2.0)
        draw.add_rect_filled(lo, imgui.ImVec2(lo.x + 3.0, hi.y), P.focus.u32)

    title, qualifier = identity(snap, obligation)

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + _X_GLYPH, origin.y + 3.0))
    imgui.text_colored(colour.vec4, OBLIGATION_GLYPH[obligation.kind])

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + _X_TITLE, origin.y + 3.0))
    imgui.text_colored(P.text_strong.vec4, ellipsis(title, _TITLE_W))

    _small()
    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + _X_QUALIFIER, origin.y + 5.0))
    imgui.text_colored(P.text_dim.vec4, ellipsis(qualifier, _QUALIFIER_W))
    _normal()

    # The only number here that gets worse on its own, and the queue's sort key.
    wait = format_elapsed(now - obligation.since)
    imgui.set_cursor_screen_pos(
        imgui.ImVec2(origin.x + _X_WAIT_END - imgui.calc_text_size(wait).x, origin.y + 3.0)
    )
    imgui.text_colored(colour.vec4, wait)

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + _X_SUMMARY, origin.y + 3.0))
    imgui.text_colored(
        P.text.vec4, ellipsis(obligation.summary, max(width - _X_SUMMARY - 12.0, 40.0))
    )

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x, hi.y))

    if expanded:
        imgui.indent(10.0)
        match obligation:
            case ApprovalNeeded():
                _expand_approval(state, bridge, obligation, wrap=wrap)
            case QuestionPending():
                _expand_question(snap, compose, actions, obligation, wrap=wrap)
            case SessionFailed():
                _expand_failure(snap, actions, obligation)
        imgui.unindent(10.0)
        imgui.spacing()

    imgui.pop_id()


def _expand_approval(
    state: review.ReviewState,
    bridge: Bridge,
    obligation: ApprovalNeeded,
    *,
    wrap: bool = True,
) -> None:
    """
    The diff and the three decisions, at the point in the queue where the call sits.

    This is what removes the second pane: the row *is* the detail, so there is no
    cursor in a diff view that can be pointed at a different agent than the queue's.
    """
    pending = obligation.approval
    line_h = imgui.get_text_line_height_with_spacing()

    if state.editing == pending.id:
        _editor(state, pending, wrap=wrap)
    else:
        lines = state.diff_lines.get(pending.id)
        if lines is None:
            # Split once per pending item, not once per frame. A whole-file Write
            # produces a diff as long as the file, and re-splitting it sixty times a
            # second is work proportional to the change being reviewed -- backwards,
            # since the large diffs are the ones stared at longest.
            lines = (pending.diff.splitlines() or [""]) if pending.diff else []
            state.diff_lines[pending.id] = lines

        if lines:
            # Fit the diff up to a cap. A short diff that still needs scrolling is
            # the thing that makes an operator stop reading them.
            body_h = min(24.0 * line_h, line_h * len(lines) + 10.0)
            if imgui.begin_child("##body", imgui.ImVec2(-8.0, body_h), imgui.ChildFlags_.borders):
                colours = {
                    "add": P.diff_add,
                    "remove": P.diff_remove,
                    "meta": P.accent,
                    "context": P.diff_context,
                }
                clipper = imgui.ListClipper()
                clipper.begin(len(lines))
                while clipper.step():
                    for index in range(clipper.display_start, clipper.display_end):
                        line = lines[index]
                        # The +/- gutter stays in the text regardless of colour: the
                        # non-hue channel is what keeps a diff readable in high
                        # contrast and to a colour-deficient operator (6.1).
                        imgui.text_colored(colours[diff_line_kind(line)].vec4, line or " ")
                clipper.end()
            imgui.end_child()
        else:
            # No diff is a real answer, not a gap: a Bash command has nothing
            # diff-shaped to show, and inventing one would be worse than the args.
            body_h = line_h * (len(pending.raw_args) + 1) + 10.0
            if imgui.begin_child("##body", imgui.ImVec2(-8.0, body_h), imgui.ChildFlags_.borders):
                imgui.text_colored(P.text_dim.vec4, "no diff for this call - arguments:")
                for key, value in pending.raw_args.items():
                    imgui.text_colored(P.text.vec4, f"  {key} = {value!r}")
            imgui.end_child()

    imgui.spacing()
    if state.edit_error:
        imgui.text_colored(P.danger.vec4, state.edit_error)

    if state.focus_reason:
        imgui.set_keyboard_focus_here()
        state.focus_reason = False
    imgui.set_next_item_width(360.0)
    changed, reason = imgui.input_text_with_hint(
        "##reason", "reason (shown to the agent on reject)", state.reasons.get(pending.id, "")
    )
    if changed:
        state.reasons[pending.id] = reason

    imgui.same_line()
    if imgui.button("approve"):
        review.resolve(bridge, state, pending, approved=True)
    imgui.same_line()
    if imgui.button("reject"):
        review.resolve(bridge, state, pending, approved=False)
    imgui.same_line()
    if state.editing == pending.id:
        if imgui.button("cancel edit"):
            state.editing = None
            state.edits.pop(pending.id, None)
            state.edit_error = None
    elif imgui.button("edit"):
        state.editing = pending.id
        state.edits.setdefault(pending.id, review.pretty_args(pending))
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "a / r / e")
    _normal()


def _editor(state: review.ReviewState, pending: object, *, wrap: bool = True) -> None:
    pid = pending.id  # type: ignore[attr-defined]
    imgui.text_colored(P.focus.vec4, "editing arguments - approve runs the corrected call")
    changed, text = multiline_input(
        "##edit",
        state.edits.get(pid, ""),
        imgui.ImVec2(-8.0, 140.0),
        wrap=wrap,
        flags=int(imgui.InputTextFlags_.allow_tab_input),
    )
    if changed:
        state.edits[pid] = text
        state.edit_error = None


def _expand_question(
    snap: Snapshot,
    compose: ComposeState,
    actions: InboxActions,
    obligation: QuestionPending,
    *,
    wrap: bool = True,
) -> None:
    """
    A composer, and a way out.

    ``close`` sits here beside ``send`` because replying is not the only resolution
    and often not the likely one. A batch of parallel research sessions all end
    their turn at once, and the dominant action on each is to read it and close it
    -- and until the session is closed its pool slot is not freed, so the next
    queued session never starts. An inbox offering only a reply box would mean
    leaving the pane to reap every finished session, for a reason the inbox does
    not show.
    """
    node = obligation.node
    record = snap.nodes.get(node)
    if record is None:
        return

    tail = record.transcript.tail(1200)
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, 110.0), imgui.ChildFlags_.borders):
        if tail.strip():
            imgui.push_text_wrap_pos(imgui.get_content_region_avail().x)
            imgui.text_colored(P.text.vec4, tail)
            imgui.pop_text_wrap_pos()
        else:
            imgui.text_colored(P.text_dim.vec4, "no output on this turn")
    imgui.end_child()

    imgui.spacing()
    draft = compose.replies.get(node, "")
    if compose.focus_reply:
        imgui.set_keyboard_focus_here()
        compose.focus_reply = False
    changed, draft = multiline_input(
        "##reply",
        draft,
        imgui.ImVec2(-8.0, 54.0),
        wrap=wrap,
        flags=int(imgui.InputTextFlags_.allow_tab_input),
    )
    if changed:
        compose.replies[node] = draft

    if imgui.button("send") and draft.strip():
        actions.send(node, draft.strip())
        compose.replies[node] = ""
    imgui.same_line()
    if imgui.button("interrupt"):
        actions.interrupt(node)
    imgui.same_line()
    if imgui.button("close session"):
        actions.close(node)
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "closing frees the slot; finishing a turn does not")
    _normal()


def _expand_failure(snap: Snapshot, actions: InboxActions, obligation: SessionFailed) -> None:
    record = snap.nodes.get(obligation.node)
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, 70.0), imgui.ChildFlags_.borders):
        imgui.push_text_wrap_pos(imgui.get_content_region_avail().x)
        imgui.text_colored(P.danger.vec4, obligation.error or "no detail recorded")
        imgui.pop_text_wrap_pos()
    imgui.end_child()

    imgui.spacing()
    if record is not None and imgui.button("retry in a new session"):
        # Same task, same directory, same model. A crashed session cannot be
        # resumed -- its subprocess is gone -- so the honest offer is a fresh one
        # carrying the same instructions, not a "resume" that would be a new
        # session wearing the old one's name.
        actions.relaunch(record.task, record.model, record.cwd or ".")
        actions.dismiss(obligation.node)
    imgui.same_line()
    if imgui.button("dismiss"):
        actions.dismiss(obligation.node)
    imgui.same_line()
    _small()
    imgui.text_colored(
        P.text_dim.vec4, "dismiss clears it from here; the session stays in the rail"
    )
    _normal()


def _batch_controls(
    snap: Snapshot, focus: FocusState, state: review.ReviewState, bridge: Bridge
) -> None:
    """
    Batch approval, scoped by default and global behind a confirm.

    Only ever counts approvals, and says so. This is the one place where saying
    "3 pending" while the inbox says "5 need you" is correct rather than a
    regression -- the button acts on tool calls, and a question is not one.
    """
    approvals = snap.approvals
    if not approvals:
        return

    imgui.separator()
    node = focus.node(snap)
    if node is not None:
        scoped = sum(1 for p in approvals if p.node == node)
        if scoped:
            record = snap.nodes.get(node)
            label = (record.agent_type if record else None) or "this agent"
            if imgui.button(f"approve all {scoped} from {label}"):
                review.approve_all_for_node(bridge, snap, state, node)
            imgui.same_line()

    total = len(approvals)
    if state.confirming_global:
        imgui.text_colored(P.danger.vec4, f"approve all {total} without reading them?")
        imgui.same_line()
        if imgui.button("yes, approve all"):
            review.approve_everything(bridge, snap, state)
        imgui.same_line()
        if imgui.button("cancel"):
            state.confirming_global = False
    elif imgui.button(f"approve all {total} pending calls..."):
        state.confirming_global = True


def _zero_state(snap: Snapshot, now: float) -> None:
    """
    An empty queue is the feature, not a blank pane.

    It turns the centre into "nothing needs you -- here is what everyone is doing",
    which is the other half of the operator's loop and the moment they are deciding
    what to start next.
    """
    imgui.spacing()
    imgui.text_colored(P.ok.vec4, f"{STATE_GLYPH[AgentState.DONE]}  nothing needs you")
    imgui.text_colored(P.text_dim.vec4, "agents run until they need to change something.")
    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    live = [
        snap.nodes[n]
        for n in projects.roots(snap)
        if not snap.nodes[n].state.is_terminal and snap.nodes[n].state is not AgentState.FAILED
    ]
    if not live:
        imgui.text_colored(P.text_dim.vec4, "nothing is running either. start something below.")
        return

    imgui.text_colored(P.text_strong.vec4, "what everyone is doing")
    imgui.spacing()
    for record in live:
        imgui.text_colored(P.state(record.state).vec4, f"  {STATE_GLYPH[record.state]}")
        imgui.same_line()
        imgui.text_colored(P.text.vec4, ellipsis(record.task, 320.0))
        imgui.same_line()
        imgui.text_colored(P.text_dim.vec4, f"- {record.topic}")

    imgui.spacing()
    imgui.separator()
    imgui.spacing()
    spend = sum(r.usage.total_cost_usd for r in snap.nodes.values())
    imgui.text_colored(P.text_dim.vec4, f"{len(snap.nodes)} agents this run · ${spend:,.2f}")
