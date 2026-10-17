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
from ..model import AgentRecord, AgentState, EndedHow, LaunchSpec, NodeId, Snapshot
from ..theme import STATE_GLYPH, STATE_LABEL, Color, P
from . import gate_menu, projects, session_details, session_menu
from .widgets import (
    ago,
    closed_line,
    context_cell,
    ellipsis,
    ended_how_label,
    flat_line,
    format_elapsed,
    gate_adds,
    one_line,
    short_model,
)

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


# In place of a title the session file did not record.
NO_TASK = "(task not recorded)"
NO_MODEL = "its session file records no model, so there is nothing to fork it on"


def narrow_note(policy: Policy | None) -> str | None:
    """
    What "narrow to strict" does to a session answering with ``policy``, or None
    where the control is not drawn.

    **The card menu's sentence for the same move, by asking for that row**
    (``gate_menu.rows``), so the two controls cannot come to describe one lever
    differently. It ends on the part a button's label cannot carry: the agent's
    briefing goes on describing the rung it started under. Whether the process
    is contained is not asked, because no narrowing depends on it.
    """
    lowest = tuple(Policy)[0]
    if policy is None or policy is lowest:
        return None
    return next(
        row.consequence
        for row in gate_menu.rows(policy, False, True, adds=gate_adds)
        if row.rung is lowest
    )


def fork_refusal(root: AgentRecord) -> str | None:
    """
    Why Fork is greyed on a closed session, or None when it may be pressed.

    Fork launches ``LaunchSpec.from_record`` in one step, with the record's task
    as the new session's prompt. Whether that task is one is the card menu's
    question and is asked through its predicate (``session_menu.task_missing``),
    in its words, so the two surfaces grey the same cards for the same reason: a
    title that is only the CLI's label for the conversation is not a task.
    """
    if session_menu.task_missing(root):
        return session_menu.TASK_NOT_RECORDED
    if not root.model.strip():
        return NO_MODEL
    return None


def recalled_lines(root: AgentRecord, now_wall: float | None) -> tuple[str, ...]:
    """
    Everything HEALTH says about a session recalled from its file, below the
    title, one line each. () for any other record.

    **Only what the file said, each under its own name.** The record beside it
    is field defaults -- DONE, a start at zero on the store's clock, zero usage,
    no context reading -- and the ordinary pane draws those as "done", a
    duration since boot, "$0.00" and "0 in". So there is no state label, no
    duration, no context cell and no token line here; the cost is the file's
    estimate or is left out; and the gate is the rung the file last recorded
    (``AgentRecord.granted_policy``), "not known" when it recorded none this
    build knows. ``now_wall`` is the wall clock, or None when the caller has
    none, and then the age is said to be not known.
    """
    recalled = root.recalled
    if recalled is None:
        return ()
    lines = [
        closed_line(root) or "",
        f"last active: {ago(recalled.last_active, now_wall)}",
        ended_how_label(recalled.ended_how),
    ]
    if recalled.ended_how is EndedHow.FAILED and recalled.error:
        lines.append(f"it said: {flat_line(recalled.error)}")
    elif session_details.failed_then_closed(recalled):
        lines.append(f"it had failed before it was closed: {flat_line(recalled.error or '')}")
    lines.append(short_model(root.model) if root.model else "model not known")
    lines.append(flat_line(root.cwd) if root.cwd else "directory not known")
    granted = root.granted_policy
    lines.append(
        "gate: not known"
        if granted is None
        else f"gate: {granted.value}, as the session file last recorded"
    )
    if recalled.cost_usd is not None:
        lines.append(
            f"${recalled.cost_usd:,.2f} as the session file recorded, an estimate; "
            "it may double-count across resumes"
        )
    return tuple(lines)


def _recalled(root: AgentRecord, actions: HealthActions, now_wall: float | None) -> None:
    """
    HEALTH for a closed session: what its file said, and Fork.

    Fork is the one lever that needs no process: it starts a new session from
    the task and directory on the record. Interrupt, close and narrowing act on
    a run, and there is none. It is greyed, with the reason, where the record
    has nothing to start a session from (``fork_refusal``).
    """
    title = root.task if root.task.strip() else NO_TASK
    imgui.text_colored(P.text_strong.vec4, ellipsis(title, imgui.get_content_region_avail().x))
    _small()
    imgui.text_colored(P.text_dim.vec4, one_line(projects.project_key(root.cwd)))
    _normal()
    imgui.separator()
    imgui.spacing()
    imgui.push_text_wrap_pos(0.0)
    for line in recalled_lines(root, now_wall):
        imgui.text_disabled(line)
    imgui.pop_text_wrap_pos()
    imgui.spacing()
    imgui.separator()
    imgui.spacing()
    refusal = fork_refusal(root)
    if refusal is not None:
        imgui.begin_disabled()
    if imgui.button("fork") and refusal is None:
        actions.fork(LaunchSpec.from_record(root))
    if refusal is not None:
        imgui.end_disabled()
        imgui.text_disabled(refusal)


SESSION_SPEND = "the session's, estimated, not billing"
SESSION_TOKENS = "all agents"


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
    now_wall: float | None = None,
) -> None:
    """
    Health for the session under the cursor. Never independently selectable.

    ``policy`` is the live reading taken from the session this frame, or None when
    no session is holding this node any more. It is a parameter rather than
    something read off the record because the record does not carry it: the policy
    moves inside the session after launch, and the only value that is not a guess
    is the one the session is answering with right now.

    ``now`` is the store's clock and ``now_wall`` the wall clock, both read once
    by the shell for the frame. The second is used only for a session recalled
    from its file, whose times are wall-clock (``model.Recalled``); None draws
    its age as not known, which is what a caller that passes no wall clock
    knows. This pane reads no clock of its own.
    """
    if node is None or (record := snap.get(node)) is None:
        imgui.text_disabled("nothing selected")
        return

    # A sub-agent's health is its session's: it shares the subprocess, the context
    # window and the bill. Showing a separate reading would invent three numbers.
    session: NodeId = (node[0], None)
    root = snap.get(session) or record

    if root.recalled is not None:
        _recalled(root, actions, now_wall)
        return

    imgui.text_colored(P.text_strong.vec4, ellipsis(root.task, imgui.get_content_region_avail().x))
    _small()
    imgui.text_colored(P.text_dim.vec4, one_line(projects.project_key(root.cwd)))
    _normal()
    imgui.separator()
    imgui.spacing()

    imgui.text_colored(P.state(root.state).vec4, STATE_GLYPH[root.state])
    imgui.same_line()
    imgui.text_colored(P.state(root.state).vec4, STATE_LABEL[root.state])
    imgui.same_line()
    imgui.text_disabled(format_elapsed((root.ended_at or now) - root.started_at))

    imgui.text_disabled(short_model(root.model))
    imgui.text_disabled(one_line(root.cwd) if root.cwd else "(the orchestrator's directory)")
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

    # The session's dollars beside the session's tokens: the root's own tokens are
    # only its share of the calls the dollar figure covers.
    usage = session_details.session_usage(snap, root)
    imgui.spacing()
    imgui.text_colored(P.text.vec4, f"${usage.total_cost_usd:,.2f}")
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, SESSION_SPEND)
    _normal()
    imgui.text_disabled(
        f"{_fmt_tokens(usage.input_tokens)} in · {_fmt_tokens(usage.output_tokens)} out · "
        f"{_fmt_tokens(usage.cache_read_input_tokens)} cached"
    )
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, SESSION_TOKENS)
    _normal()

    subs = snap.subagents_of(session)
    if subs:
        imgui.spacing()
        imgui.separator()
        imgui.text_disabled("sub-agents")
        for sub in subs:
            imgui.text_colored(P.state(sub.state).vec4, f"  {STATE_GLYPH[sub.state]}")
            imgui.same_line()
            imgui.text_colored(P.text.vec4, flat_line(sub.agent_type or sub.task))
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
    note = narrow_note(policy)
    if note is not None:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(P.text_dim.vec4, note)
        imgui.pop_text_wrap_pos()
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
