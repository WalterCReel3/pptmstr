"""
The agent tree pane.

Renders ``snapshot.order`` directly -- a pre-order walk the store already did --
and indents by ``record.depth``, so this never walks the tree itself. Selection is
presentation state and lives in ViewState, not the store (design §6).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from imgui_bundle import imgui

from ..model import AgentState, NodeId, Snapshot
from ..theme import P
from .widgets import context_cell, elapsed_cell, short_model, state_cell

# Fixed widths, never auto-fit. An auto-sized column re-fits over three frames every
# time rows change (AutoFitQueue = (1 << 3) - 1), and these rows change constantly,
# so the table would be in permanent re-fit and the columns visibly breathe.
_COLUMNS: tuple[tuple[str, float], ...] = (
    ("agent", 240.0),
    ("state", 120.0),
    ("topic", 260.0),
    ("model", 130.0),
    ("context", 110.0),
    ("elapsed", 70.0),
)


@dataclass
class NodeViewState:
    """
    How a node is being *looked at*. Not application state (I1).

    ``last_frame_touched`` drives pruning: entries for nodes that have gone away are
    dropped rather than accumulating for the life of the process.
    """

    expanded: bool = True
    last_frame_touched: int = 0


@dataclass
class ViewState:
    """UI-side state for the whole pane."""

    selected: NodeId | None = None
    nodes: dict[NodeId, NodeViewState] = field(default_factory=dict)
    frame: int = 0

    def touch(self, node_id: NodeId) -> NodeViewState:
        state = self.nodes.get(node_id)
        if state is None:
            state = NodeViewState()
            self.nodes[node_id] = state
        state.last_frame_touched = self.frame
        return state

    def prune(self, frame: int) -> None:
        """Drop view state for nodes that were not touched this frame."""
        self.frame = frame
        stale = [nid for nid, st in self.nodes.items() if st.last_frame_touched < frame - 1]
        for nid in stale:
            del self.nodes[nid]


def _visible(snap: Snapshot, view: ViewState) -> list[NodeId]:
    """
    Rows to draw, honouring collapsed parents.

    Walks ``order`` once. Because it is pre-order, a collapsed node's descendants are
    exactly the following run of deeper rows, so skipping them is a depth comparison
    rather than a tree query.
    """
    out: list[NodeId] = []
    hide_deeper_than: int | None = None
    for node_id in snap.order:
        rec = snap.nodes[node_id]
        if hide_deeper_than is not None:
            if rec.depth > hide_deeper_than:
                continue
            hide_deeper_than = None
        out.append(node_id)
        if not view.touch(node_id).expanded:
            hide_deeper_than = rec.depth
    return out


def draw(snap: Snapshot, view: ViewState, now: float | None = None) -> None:
    """Build the pane from one snapshot. Never reads the store."""
    now = time.monotonic() if now is None else now

    if not snap.order:
        imgui.text_disabled("no agents")
        imgui.spacing()
        imgui.text_disabled("nothing is running, and nothing is waiting on you.")
        return

    flags = (
        imgui.TableFlags_.borders_inner_v
        | imgui.TableFlags_.row_bg
        | imgui.TableFlags_.scroll_y
        | imgui.TableFlags_.resizable
    )
    if not imgui.begin_table("##agents", len(_COLUMNS), flags):
        return

    for label, width in _COLUMNS:
        imgui.table_setup_column(label, imgui.TableColumnFlags_.width_fixed, width)
    imgui.table_setup_scroll_freeze(0, 1)
    imgui.table_headers_row()

    for node_id in _visible(snap, view):
        rec = snap.nodes[node_id]
        node_view = view.nodes[node_id]
        has_children = any(r.parent == node_id for r in snap.nodes.values())

        imgui.table_next_row()
        imgui.table_next_column()

        # Widget identity comes from NodeId, never row index. ImGui keys widget state
        # by hashed label, so an index-derived ID reassigns hover, focus and scroll to
        # a different agent the moment rows reorder (I6).
        imgui.push_id(f"{node_id[0]}:{node_id[1] or ''}")

        if rec.depth:
            imgui.indent(rec.depth * 14.0)

        if has_children:
            arrow = "v" if node_view.expanded else ">"
            if imgui.small_button(arrow):
                node_view.expanded = not node_view.expanded
            imgui.same_line()
        else:
            imgui.dummy(imgui.ImVec2(17, 1))
            imgui.same_line()

        label = rec.agent_type or ("session" if rec.parent is None else "agent")
        clicked, _ = imgui.selectable(
            f"{label}##row",
            view.selected == node_id,
            imgui.SelectableFlags_.span_all_columns | imgui.SelectableFlags_.allow_overlap,
        )
        if clicked:
            view.selected = node_id
        if imgui.is_item_hovered() and imgui.begin_tooltip():
            imgui.text(rec.task or "(no task)")
            imgui.text_disabled(f"{rec.node_id[0]} / {rec.node_id[1] or 'root'}")
            imgui.end_tooltip()

        if rec.depth:
            imgui.unindent(rec.depth * 14.0)

        imgui.table_next_column()
        state_cell(rec.state)

        imgui.table_next_column()
        if rec.error and rec.state is AgentState.FAILED:
            imgui.text_colored(P.danger.vec4, rec.error[:120])
        else:
            imgui.text_disabled(rec.topic)

        imgui.table_next_column()
        imgui.text_disabled(short_model(rec.model))

        imgui.table_next_column()
        context_cell(rec.context)

        imgui.table_next_column()
        elapsed_cell(rec.started_at, rec.ended_at, now)

        imgui.pop_id()

    imgui.end_table()
