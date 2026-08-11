"""
Session health: the facts a node carries, and the levers that act on them.

These are not new features so much as facts that had no pane to live in. The old
DETAIL pane was misnamed -- it showed the selected *pending approval*, not the
detail of the selection -- so everything a session actually knows about itself had
nowhere to go. ``UsageRollup`` accrued on every message and was rendered by no
widget; ``cwd`` was chosen at launch and then invisible; ``transcript_path`` was
recorded and never read.

Context and cost sit side by side and are never merged. They answer different
questions -- "should I retire this session before it compacts" versus "what has
this cost" -- and a widget blending them answers neither (design §2.4).

The interrupt/close/fork buttons live here, next to the numbers an operator would
base those decisions on, rather than at the bottom of a pane that shows none of
them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from ..model import AgentState, NodeId, Snapshot
from ..theme import STATE_GLYPH, STATE_LABEL, P
from . import projects
from .widgets import context_cell, ellipsis, format_elapsed, short_model

_SMALL_FONT = 12.5


@dataclass
class HealthActions:
    interrupt: Callable[[NodeId], None]
    close: Callable[[NodeId], None]
    fork: Callable[[str, str, str], None]


def _small() -> None:
    imgui.push_font(None, _SMALL_FONT)


def _normal() -> None:
    imgui.pop_font()


def _fmt_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count // 1000}k"
    return str(count)


def draw(snap: Snapshot, node: NodeId | None, actions: HealthActions, now: float) -> None:
    """Health for the session under the cursor. Never independently selectable."""
    if node is None or (record := snap.get(node)) is None:
        imgui.text_disabled("nothing selected")
        return

    # A sub-agent's health is its session's: it shares the subprocess, the context
    # window and the bill. Showing a separate reading would invent three numbers.
    session: NodeId = (node[0], None)
    root = snap.get(session) or record

    imgui.text_colored(P.text_strong.vec4, ellipsis(root.task, imgui.get_content_region_avail().x))
    _small()
    imgui.text_colored(P.text_dim.vec4, projects.project_key(root.cwd))
    _normal()
    imgui.separator()
    imgui.spacing()

    imgui.text_colored(P.state(root.state).vec4, STATE_GLYPH[root.state])
    imgui.same_line()
    imgui.text_colored(P.state(root.state).vec4, STATE_LABEL[root.state])
    imgui.same_line()
    imgui.text_disabled(format_elapsed((root.ended_at or now) - root.started_at))

    imgui.text_disabled(short_model(root.model))
    imgui.text_disabled(root.cwd or "(the orchestrator's directory)")
    imgui.spacing()

    # -- health, then money. Adjacent, never combined.
    context_cell(root.context)
    imgui.same_line()
    imgui.text_disabled("to compaction")
    if root.context is not None and root.context.compactions:
        imgui.text_colored(
            P.pressure_compacted.vec4,
            f"compacted {root.context.compactions}x - reasoning already discarded",
        )

    usage = root.usage
    imgui.spacing()
    imgui.text_colored(P.text.vec4, f"${usage.total_cost_usd:,.2f}")
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "estimated, not billing")
    _normal()
    imgui.text_disabled(
        f"{_fmt_tokens(usage.input_tokens)} in · {_fmt_tokens(usage.output_tokens)} out · "
        f"{_fmt_tokens(usage.cache_read_input_tokens)} cached"
    )

    subs = projects.subagents_of(snap, session)
    if subs:
        imgui.spacing()
        imgui.separator()
        imgui.text_disabled("sub-agents")
        for sub in subs:
            imgui.text_colored(P.state(sub.state).vec4, f"  {STATE_GLYPH[sub.state]}")
            imgui.same_line()
            imgui.text_colored(P.text.vec4, sub.agent_type or sub.task)
            imgui.same_line()
            imgui.text_disabled(STATE_LABEL[sub.state])

    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    terminal = root.state.is_terminal
    if terminal:
        imgui.begin_disabled()
    # Interrupt is the recoverable lever: it stops the current turn and keeps the
    # session and its context. Closing is what actually reclaims the subprocess --
    # and the only thing that frees a pool slot, since a finished turn no longer
    # ends a session.
    if imgui.button("interrupt"):
        actions.interrupt(session)
    imgui.same_line()
    if imgui.button("close"):
        actions.close(session)
    if terminal:
        imgui.end_disabled()

    imgui.same_line()
    if imgui.button("fork"):
        # A fresh session on the same task and directory. Offered next to the
        # compaction count because that count is the reason to reach for it: a
        # session that has compacted has already lost the reasoning that got it
        # here, and continuing it is worse than restarting it.
        actions.fork(root.task, root.model, root.cwd or ".")

    _small()
    if root.state is AgentState.AWAITING_INPUT:
        imgui.text_colored(P.text_dim.vec4, "this session holds a slot until it is closed")
    else:
        imgui.text_colored(P.text_dim.vec4, "interrupt keeps context; close ends the session")
    _normal()
