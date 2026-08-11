"""
The session rail: one card per root session, grouped by project.

Two orderings exist over the same set of sessions and only one of them can be
position. The inbox is urgency order and reorders constantly; the rail is **stable
spatial order** -- project, then spawn order, never re-sorted. A card grid earns its
space only if position is stable enough to build muscle memory, and sorting cards by
urgency would produce motion instead of a map, leaving two inboxes with the worse
one on the left. Urgency rides on a card as a badge, never as position.

The rail is a second input device onto one cursor, not a second selection. The
highlight is derived from the cursor; clicking a card moves the cursor. It does not
filter -- see ``FocusState.to_node`` for why a filtered inbox is defect 2 in a new
coat.

Sub-agents are pips inside their parent's card rather than indented rows, which is
what lets this pane drop the tree widget entirely and keeps its height proportional
to sessions rather than to agents.
"""

from __future__ import annotations

from dataclasses import dataclass

from imgui_bundle import imgui

from ..model import AgentRecord, AgentState, NodeId, Obligation, ObligationKind, Snapshot
from ..theme import OBLIGATION_GLYPH, STATE_GLYPH, STATE_LABEL, P
from . import projects
from .focus import FocusState
from .widgets import context_cell, ellipsis, format_elapsed, short_model

# Lines of text each density class gets. Fixed per class, not per card: variable
# heights would rule out ListClipper, and the rail is the one pane that has to stay
# cheap as N grows.
#
# Three classes, not two. Splitting on terminality was wrong at scale -- at twenty
# sessions most cards are working-but-not-blocking, and giving those the same height
# as a blocked one fills the rail with the sessions least likely to be acted on.
# Height tracks obligation, so the vertical budget goes to whatever is waiting.
_LINES = {"blocked": 3.0, "blocked_subs": 3.8, "active": 2.0, "ended": 1.0}

_PAD = 6.0
_SMALL_FONT = 12.5


@dataclass
class RailState:
    """Presentation state for the pane. The cursor is not here -- there is one."""

    scroll_to_focus: bool = True


def _small() -> None:
    imgui.push_font(None, _SMALL_FONT)


def _normal() -> None:
    imgui.pop_font()


def _money(usd: float) -> str:
    return f"${usd:,.2f}"


def _by_session(snap: Snapshot) -> dict[str, list[Obligation]]:
    """
    Obligations indexed by session, built once per frame.

    Everything on a card that counts or badges reads this. Nothing re-derives from
    ``pending``: counting approvals alone is how a project header came to read
    "6 sessions" with no waiting count while that project held a question and a
    crashed session -- defect 1 reproducing itself inside the fix for defect 1.
    """
    out: dict[str, list[Obligation]] = {}
    for obligation in snap.needs_you:
        out.setdefault(obligation.node[0], []).append(obligation)
    return out


def _density(rec: AgentRecord, owed: list[Obligation], has_subs: bool) -> str:
    if owed:
        return "blocked_subs" if has_subs else "blocked"
    if rec.state in (AgentState.DONE, AgentState.CANCELLED):
        return "ended"
    return "active"


def _badge_kind(owed: list[Obligation]) -> ObligationKind:
    kinds = {o.kind for o in owed}
    return kinds.pop() if len(kinds) == 1 else ObligationKind.APPROVAL


def draw(snap: Snapshot, focus: FocusState, state: RailState, now: float) -> None:
    """Build the rail from one snapshot. Never reads the store."""
    if not snap.order:
        imgui.text_disabled("no sessions")
        imgui.spacing()
        imgui.text_disabled("start one from the launcher below.")
        return

    owed_by_session = _by_session(snap)
    focused_node = focus.node(snap)
    focused_session = focused_node[0] if focused_node else None

    for project, roots in projects.group_roots(snap):
        _project_header(snap, project, roots, owed_by_session)
        for root in roots:
            _card(
                snap,
                focus,
                state,
                rec=snap.nodes[root],
                owed=owed_by_session.get(root[0], []),
                focused=root[0] == focused_session,
                now=now,
            )


def _project_header(
    snap: Snapshot,
    project: str,
    roots: list[NodeId],
    owed_by_session: dict[str, list[Obligation]],
) -> None:
    imgui.spacing()
    _small()
    imgui.text_colored(P.text_strong.vec4, project.upper())
    imgui.same_line()
    imgui.text_colored(P.text_dim.vec4, f"{len(roots)} sessions")
    # The union, not the approvals. See _by_session.
    waiting = sum(len(owed_by_session.get(root[0], ())) for root in roots)
    if waiting:
        imgui.same_line()
        imgui.text_colored(P.state_awaiting.vec4, f"· {waiting} waiting")
    _normal()
    imgui.spacing()


def _card(
    snap: Snapshot,
    focus: FocusState,
    state: RailState,
    *,
    rec: AgentRecord,
    owed: list[Obligation],
    focused: bool,
    now: float,
) -> None:
    subs = projects.subagents_of(snap, rec.node_id)
    density = _density(rec, owed, bool(subs))

    draw = imgui.get_window_draw_list()
    line_h = imgui.get_text_line_height_with_spacing()
    width = imgui.get_content_region_avail().x
    height = line_h * _LINES[density] + _PAD * 2

    origin = imgui.get_cursor_screen_pos()
    imgui.push_id(f"{rec.node_id[0]}:{rec.node_id[1] or ''}")
    imgui.invisible_button("##card", imgui.ImVec2(max(width, 40.0), height))
    hovered = imgui.is_item_hovered()
    if imgui.is_item_clicked():
        focus.to_node(snap, rec.node_id)

    lo = imgui.ImVec2(origin.x, origin.y)
    hi = imgui.ImVec2(origin.x + width, origin.y + height)

    # Focus is an outline and an edge bar, never a fill.
    #
    # Filling the focused card with P.selection is the obvious choice and it is
    # wrong: every other mark on a card is a saturated state colour, and in
    # high_contrast the selection fill is saturated too, so the sub-agent pips and
    # the state label lose their figure/ground the moment a card becomes current.
    # Constant fill means a card reads identically whether or not it is focused,
    # which is the property that matters when the colours on it are load-bearing
    # (design 6.1). Only rendering all three required themes showed this.
    draw.add_rect_filled(lo, hi, (P.panel_alt if hovered else P.panel).u32)
    # add_rect is (col, rounding, thickness) in this binding, not the C++
    # (col, rounding, flags, thickness) -- the same transposition widgets.py
    # documents for path_stroke. Passing the C++ order typechecks and throws.
    draw.add_rect(lo, hi, (P.focus if focused else P.border).u32, 0.0, 2.0 if focused else 1.0)
    if focused:
        draw.add_rect_filled(lo, imgui.ImVec2(lo.x + 3.0, hi.y), P.focus.u32)
        if state.scroll_to_focus:
            # Drag the rail to the current card. Without this the derived highlight
            # is a lie at any real N: the inbox happily selects an obligation whose
            # session is entirely below the fold, and the one pane meant to say
            # "here is where this came from" shows nothing. It is also the failure
            # that makes the single-cursor design *look* broken while being correct.
            imgui.set_scroll_here_y(0.45)

    inner_x = origin.x + _PAD + 4.0
    y = origin.y + _PAD
    colour = P.state(rec.state)

    imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x, y))
    imgui.text_colored(colour.vec4, STATE_GLYPH[rec.state])
    imgui.same_line()

    if density == "ended":
        imgui.text_colored(P.text_dim.vec4, ellipsis(rec.task, width - 150.0))
        _small()
        imgui.set_cursor_screen_pos(imgui.ImVec2(hi.x - 96.0, y + 2.0))
        imgui.text_colored(P.text_dim.vec4, STATE_LABEL[rec.state])
        imgui.same_line()
        imgui.text_colored(P.text_dim.vec4, _money(rec.usage.total_cost_usd))
        _normal()
        _end(origin, height)
        imgui.pop_id()
        return

    # The task, not the node name. Every root is called "session", so the name is
    # the one string on a card that cannot tell two of them apart.
    title_room = width - (_PAD * 2 + 30.0) - (52.0 if owed else 42.0)
    imgui.text_colored(P.text_strong.vec4, ellipsis(rec.task, title_room))

    if owed:
        badge = f"{OBLIGATION_GLYPH[_badge_kind(owed)]} {len(owed)}"
        badge_w = imgui.calc_text_size(badge).x + 10.0
        bx = hi.x - _PAD - badge_w
        draw.add_rect_filled(
            imgui.ImVec2(bx, y - 1.0),
            imgui.ImVec2(bx + badge_w, y + line_h - 3.0),
            P.obligation(_badge_kind(owed)).u32,
            2.0,
        )
        imgui.set_cursor_screen_pos(imgui.ImVec2(bx + 5.0, y))
        imgui.text_colored(P.bg.vec4, badge)
    else:
        elapsed = format_elapsed(
            (rec.ended_at if rec.ended_at is not None else now) - rec.started_at
        )
        _small()
        imgui.set_cursor_screen_pos(
            imgui.ImVec2(hi.x - _PAD - imgui.calc_text_size(elapsed).x, y + 2.0)
        )
        imgui.text_colored(P.text_dim.vec4, elapsed)
        _normal()

    topic_colour = P.danger if rec.state is AgentState.FAILED else P.text_dim
    spend = _money(rec.usage.total_cost_usd)

    if density == "active":
        # One line for everything: ring, state, topic, spend. A session that wants
        # nothing gets a reading, not a dossier.
        y += line_h
        imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x, y + 2.0))
        context_cell(rec.context)
        imgui.same_line()
        _small()
        imgui.text_colored(colour.vec4, STATE_LABEL[rec.state])
        imgui.same_line()
        room = hi.x - _PAD - imgui.calc_text_size(spend).x - 8.0 - imgui.get_cursor_screen_pos().x
        imgui.text_colored(topic_colour.vec4, ellipsis(rec.topic, max(room, 20.0)))
        imgui.set_cursor_screen_pos(
            imgui.ImVec2(hi.x - _PAD - imgui.calc_text_size(spend).x, y + 3.0)
        )
        imgui.text_colored(P.text_dim.vec4, spend)
        _normal()
        _end(origin, height)
        imgui.pop_id()
        return

    y += line_h
    imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x, y))
    _small()
    imgui.text_colored(colour.vec4, STATE_LABEL[rec.state])
    imgui.same_line()
    avail = width - (_PAD * 2 + 8.0) - (imgui.get_cursor_screen_pos().x - inner_x)
    imgui.text_colored(topic_colour.vec4, ellipsis(rec.topic, avail))
    _normal()

    # Health and spend side by side but never merged: context answers "should I
    # retire this session", cost answers "what has it spent", and a widget that
    # blends them answers neither (design 2.4).
    y += line_h
    imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x, y + 2.0))
    context_cell(rec.context)
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, short_model(rec.model).replace("claude-", ""))
    imgui.set_cursor_screen_pos(imgui.ImVec2(hi.x - _PAD - imgui.calc_text_size(spend).x, y + 3.0))
    imgui.text_colored(P.text.vec4, spend)
    _normal()

    if subs:
        y += line_h
        imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x + 6.0, y + 1.0))
        _small()
        owed_nodes = [o.node for o in owed]
        for i, sub in enumerate(subs):
            if i:
                imgui.same_line()
            waiting = sum(1 for n in owed_nodes if n == sub.node_id)
            label = f"{STATE_GLYPH[sub.state]} {sub.agent_type or sub.task}"
            if waiting:
                label += f" {waiting}"
            imgui.text_colored(P.state(sub.state).vec4, label)
        _normal()

    _end(origin, height)
    imgui.pop_id()


def _end(origin: imgui.ImVec2, height: float) -> None:
    """
    Return the cursor to just after the card and claim the inter-card gap.

    The gap has to be a real item (Dummy) rather than a cursor offset: moving the
    cursor past the last submitted item does not grow the window's content bounds,
    and ImGui asserts on that rather than silently clipping.
    """
    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x, origin.y + height))
    imgui.dummy(imgui.ImVec2(1.0, 4.0))
