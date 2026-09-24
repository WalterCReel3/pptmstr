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

from ..approval import Policy
from ..model import AgentState, LaunchSpec, NodeId, Snapshot
from ..theme import STATE_GLYPH, STATE_LABEL, Color, P
from . import projects
from .widgets import context_cell, ellipsis, format_elapsed, gate_adds, short_model

_SMALL_FONT = 12.5


@dataclass
class HealthActions:
    interrupt: Callable[[NodeId], None]
    close: Callable[[NodeId], None]
    # task, model, cwd, template -- see InboxActions.relaunch for why the last one
    # is carried and not defaulted.
    fork: Callable[[LaunchSpec], None]
    # Put a session back on the strict gate, for one that is wandering rather than
    # converging. Narrowing only: the session exposes no widening write, so there
    # is no lever back from here.
    revoke_policy: Callable[[NodeId], None]


def _small() -> None:
    imgui.push_font(None, _SMALL_FONT)


def _normal() -> None:
    imgui.pop_font()


def gate_line(policy: Policy | None) -> tuple[str, Color] | None:
    """
    What to say about this session's gate, and how loudly, or None to say nothing.

    Nothing is the honest answer for a session no longer running: the policy is
    held by the session, so once it is gone there is no reading to report and the
    last one is not a fact about anything.

    ``STRICT`` is stated rather than left blank, because a variable nobody displays
    is one the UI can be silently wrong about. It is dimmed and every other rung is
    not -- a session running under a widened gate is the case the operator has to
    be able to notice without looking for it.
    """
    if policy is None:
        return None
    return f"gate: {policy.value}", P.text_dim if policy is Policy.STRICT else P.warn


def _fmt_tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count // 1000}k"
    return str(count)


def draw(
    snap: Snapshot,
    node: NodeId | None,
    actions: HealthActions,
    now: float,
    policy: Policy | None = None,
) -> None:
    """
    Health for the session under the cursor. Never independently selectable.

    ``policy`` is the live reading taken from the session this frame, or None when
    no session is holding this node any more. It is a parameter rather than
    something read off the record because the record does not carry it: the policy
    moves inside the session after launch, and the only value that is not a guess
    is the one the session is answering with right now.
    """
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
    gate = gate_line(policy)
    if gate is not None:
        imgui.text_colored(gate[1].vec4, gate[0])
        adds = gate_adds(policy) if policy is not None else ()
        if adds:
            # The whole extent of the widening, under the name of it. A rung called
            # PERMISSIVE invites the reading that writes are ungated, and the name
            # is what an operator calibrates against -- this is the line that says
            # how far it actually goes.
            _small()
            imgui.text_colored(P.warn.vec4, f"adds {', '.join(adds)} - everything else parks")
            _normal()
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

    subs = snap.subagents_of(session)
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
        actions.fork(LaunchSpec.from_record(root))

    if policy is not None and policy is not Policy.STRICT:
        # Only while there is something to narrow. Absent rather than disabled: a
        # greyed control reads as a lever this session has lost, and under STRICT
        # there is simply nothing for it to do.
        imgui.same_line()
        if imgui.button("narrow to strict"):
            actions.revoke_policy(session)

    _small()
    if root.state is AgentState.AWAITING_INPUT:
        imgui.text_colored(P.text_dim.vec4, "this session holds a slot until it is closed")
    elif root.state is AgentState.SUPERVISING:
        # The measured fact, and the only one worth the line: a prompt sent now is
        # dispatched now rather than queued behind the fan-out. What interrupt does
        # to a lead's outstanding sub-agents has not been measured, so this does not
        # say.
        imgui.text_colored(
            P.text_dim.vec4, "a prompt sent now is read now, without waiting for its sub-agents"
        )
    else:
        imgui.text_colored(P.text_dim.vec4, "interrupt keeps context; close ends the session")
    _normal()
