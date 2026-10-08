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

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import assert_never

from imgui_bundle import imgui

from ..approval import diff_line_kind
from ..bridge import Bridge
from ..model import (
    AgentState,
    ApprovalNeeded,
    LaunchSpec,
    NodeId,
    Obligation,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from ..theme import OBLIGATION_GLYPH, STATE_GLYPH, Face, P, face, faded
from . import projects, review, splash, splash_art, typeset
from .compose import ComposeState, wants_send
from .focus import FocusState
from .widgets import CTRL_ENTER_SUBMITS, ellipsis, format_elapsed, multiline_input

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

# How much of a turn's prose the expanded question row previews. The box is a
# fixed 110px, so this only has to be comfortably more than fits.
_PREVIEW_CHARS = 1200
_QUALIFIER_W = 150.0

_SMALL_FONT = 12.5

# The two spellings the CLI uses for "start a sub-agent". Both are in
# ``approval._REVIEW``, so both park, and a marker that knew only one would be
# silently absent for half the spawns.
_SPAWN_TOOLS = frozenset({"Agent", "Task"})

_SUFFIX = {1: "st", 2: "nd", 3: "rd"}

# The tallest an expanded approval's body gets: lines of the UI font for the native
# diff child and for a typeset document, rows of the diff view for a typeset diff.
# The two are the same height at the UI's own size, so a row costs the queue no more
# under either renderer; under ``Settings.typeset_scale`` the diff keeps its count of
# rows. The seam ends a capped view on a whole line of whatever pitch its text has.
_BODY_LINES = 24.0

# The typeset views this pane holds between frames, keyed ``(obligation.key, part)``.
#
# Module-level for the reason ``_ART_FRAMES`` below is: ``draw`` is handed no
# presentation-state object of this pane's own, ``review.ReviewState`` is shared
# with DETAIL -- which draws the same parked call in the same frame and must not
# share a view with this pane, because a view holds one scroll position and one
# origin -- and hanging it on either would mean editing a file this pane does not
# own. Pruned in ``draw`` by the obligations still in the queue, the rule
# ``ReviewState.prune`` applies to everything else remembered about an approval.
_VIEWS = typeset.ViewCache()
_VIEW_PARTS = ("diff", "body", "arguments")

# The parts last made for a view, beside what they were made from, under the view's
# key and pruned with it. Kept because the seam's body classifier reads every line
# of its text -- 1.5 ms for 1,200 lines, measured, against a frame's 6 ms -- and
# because a tuple handed to ``typeset.document`` again unchanged is compared by
# identity and not part by part.
_PARTS: dict[Hashable, tuple[object, tuple[typeset.Part, ...]]] = {}

_NO_DIFF = "no diff for this call - arguments:"
_WITHHELD = "shown as the agent sent it - approve sends YOUR EDIT, which differs"
_STILL_TYPESETTING = "still typesetting - the body above is not all of this call yet"

# The bus tools' names as the CLI spells them. ``approval.py`` owns the spelling and
# keeps it private; tests/test_inbox_typeset.py fails if these two drift from it.
_POST_CONCERN = "mcp__pptmstr__post_concern"
_DECLARE_TASK = "mcp__pptmstr__declare_task"

# Calls whose substance is prose rather than a file change: for each, the argument
# that is the prose and the arguments that head it, in reading order. Any argument
# named in neither place is still shown, after these -- see ``describe``.
_DESCRIBED: dict[str, tuple[str, tuple[str, ...]]] = {
    _POST_CONCERN: ("body", ("to", "subject", "task_id")),
    _DECLARE_TASK: ("detail", ("task_id", "title", "depends_on", "touches")),
    "Agent": ("prompt", ("subagent_type", "description")),
    "Task": ("prompt", ("subagent_type", "description")),
}
assert _SPAWN_TOOLS <= _DESCRIBED.keys()
_LIST_FIELDS = frozenset({"depends_on", "touches"})

# Where a field's value starts, from the left edge of the block. Wide enough for
# the longest name in ``_DESCRIBED`` in either prose face.
_X_FIELD_VALUE = 120.0


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
    # task, model, cwd, template. The template is carried rather than defaulted:
    # dropping it turns a retry of a team session into a solo one, and nothing on
    # screen would say so.
    relaunch: Callable[[LaunchSpec], None]


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
    if len(_VIEWS) or _PARTS:
        live = {(o.key, part) for o in snap.needs_you for part in _VIEW_PARTS}
        _VIEWS.prune(live)
        for stale in _PARTS.keys() - live:
            del _PARTS[stale]

    # Before the empty-queue branch, and the ordering is the whole distinction.
    # ``_zero_state`` answers "a fleet exists and owes you nothing"; this answers
    # "there is no fleet". An early return is what makes them mutually exclusive
    # structurally rather than by the two conditions happening to disagree -- an
    # empty node table also implies an empty ``needs_you``, so a later check would
    # be reachable only by luck of ordering.
    #
    # ``nodes`` rather than ``order``, though ``store._preorder`` makes the two
    # equivalent: it re-attaches orphans at the root specifically so nothing in the
    # node table can be missing from the walk, and the lengths are therefore always
    # equal. ``nodes`` is asked anyway because it is the primary fact and ``order``
    # is a projection of it. If that recovery ever regressed, keying on the
    # projection would put a cold-start splash over a live fleet -- silently, and
    # over exactly the orphaned sub-agent that was carrying a pending approval.
    #
    # Note this is *not* the same question as ``_zero_state``'s "is anything live":
    # a fleet of five crashed sessions is not an empty application. Those sessions
    # still need to be seen and dismissed, and they keep the queue's empty state.
    if not snap.nodes:
        _splash(now)
        return

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


def spawn_marker(snap: Snapshot, obligation: Obligation) -> str | None:
    """
    Where a pending spawn sits in its session's fleet, and how much of that fleet is
    running. ``None`` for every obligation that is not a spawn.

    The row this qualifies reads ``spawn builder: <description>``, which is a
    reasonable thing to say yes to twelve times in a row while nobody ever decides
    to run twelve. ``approval.summarize`` cannot supply the number -- it is pure,
    and is handed a tool name and its arguments and nothing else -- so the shell
    renders it from the snapshot it already holds.

    **The ordinal.** ``len(fleet) + (spawns queued ahead of this one) + 1``, where
    the fleet is every descendant record of the session and "ahead" is position in
    ``snap.approvals``, which is ordered by how long each has waited. The second
    term is what makes the number honest while the board is still being built: an
    approval nobody has answered has no ``AgentRecord`` yet, so a lead that issues
    three ``Agent`` calls in one breath parks three rows that the record count alone
    would call the first sub-agent three times.

    **The live count.** Fleet members that are not terminal. A queued spawn is not
    running -- it is what the operator is being asked about -- so the two numbers
    move independently, and a session whose sub-agents have all finished reads
    ``4th sub-agent · 0 running``. Terminality is the whole predicate here because
    the question is "is it still working": ``_zero_state`` below spells FAILED out
    separately for a different question, and ``rail._density`` deliberately files a
    crashed agent as blocked rather than ended because a crash is an obligation.

    Scoped to ``obligation.node[0]``. The store holds every session at once, so a
    fleet-wide count would tell the operator about work they are not being asked to
    consent to.
    """
    if not isinstance(obligation, ApprovalNeeded):
        return None
    pending = obligation.approval
    if pending.tool_name not in _SPAWN_TOOLS:
        return None

    session = pending.node[0]
    fleet = snap.subagents_of((session, None))
    queued = [p.id for p in snap.approvals if p.tool_name in _SPAWN_TOOLS and p.node[0] == session]
    ahead = queued.index(pending.id) if pending.id in queued else 0
    running = sum(1 for rec in fleet if not rec.state.is_terminal)
    return f"{_ordinal(len(fleet) + ahead + 1)} sub-agent · {running} running"


def _ordinal(n: int) -> str:
    # 11th, 12th and 13th are the exceptions the last digit gets wrong.
    suffix = "th" if n % 100 in (11, 12, 13) else _SUFFIX.get(n % 10, "th")
    return f"{n}{suffix}"


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

    # The marker goes in the summary zone, ahead of the summary, for two reasons.
    # It is the only zone whose width is "whatever the window has left" rather than
    # a fixed slot, so a string that varies in length can live in it without moving
    # any other field along (see the column comment above). And it is the column
    # being scanned, which is where a running total has to be if it is going to
    # change the decision the row is asking for. Accent rather than warn: it is a
    # live value, not an alarm, and the threshold at which twelve becomes too many
    # is the operator's to hold, not this pane's.
    summary_x = _X_SUMMARY
    marker = spawn_marker(snap, obligation)
    if marker is not None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + summary_x, origin.y + 3.0))
        imgui.text_colored(P.accent.vec4, marker)
        summary_x += imgui.calc_text_size(marker).x + 10.0

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + summary_x, origin.y + 3.0))
    imgui.text_colored(
        P.text.vec4, ellipsis(obligation.summary, max(width - summary_x - 12.0, 40.0))
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
    # Lines of the body the seam has not set yet: a long body is built over several
    # frames, and until it is whole the row is showing less than the call.
    unset = 0

    if state.editing == pending.id:
        # The draft, in ImGui's own input and all of it: ``unset`` stays zero.
        _editor(state, pending, wrap=wrap)
    else:
        # Both above the body under either renderer: the native one is also what is
        # drawn when the faces did not load.
        if edit_withheld(state, pending):
            _warning(_WITHHELD)
        # The gate predicts this from its own read of the file at park time; the
        # tool is what decides, on the file as it is when the call runs.
        #
        # Said here, from the record, and by nothing else: the typeset diff is asked
        # to leave its copy out (``warning=False``). This line is the same one under
        # either renderer and is up while a large diff's model is still being made.
        # The view's copy is neither, and at a width its header does not fit it is
        # said twice, above the view and cut off inside it.
        for reason in refusals(pending):
            _warning(f"may not apply: {reason}")
        if typeset.enabled():
            unset = _typeset_body(pending, obligation.key)
        else:
            _native_body(state, pending)
    # What every approve path is answered from (``review.withheld``), the keys'
    # on the next frame and this row's own button on this one.
    state.body_shown("inbox", pending.id, unset, int(imgui.get_frame_count()))

    imgui.spacing()
    if state.edit_error:
        imgui.text_colored(P.danger.vec4, state.edit_error)
    not_sent = review.refusal_for(state, pending.id)
    if not_sent is not None:
        _warning(not_sent)

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
    # Not clickable while this row's body is short of its text, which is the
    # button showing what ``review.resolve`` would answer; the decision is made
    # there, for the click as for the ``a`` key.
    imgui.begin_disabled(unset > 0)
    if imgui.button("approve"):
        review.resolve(bridge, state, pending, approved=True)
    imgui.end_disabled()
    imgui.same_line()
    if imgui.button("reject"):
        review.resolve(bridge, state, pending, approved=False)
    imgui.same_line()
    if state.editing == pending.id:
        if imgui.button("cancel edit"):
            state.editing = None
            state.edits.pop(pending.id, None)
            state.edit_error = None
            # This frame's reading was of the draft in its editor. With the draft
            # gone approve sends the agent's call, whose body this row has not
            # drawn since: there is no reading of that until the next frame.
            state.bodies.pop(pending.id, None)
    elif imgui.button("edit"):
        state.editing = pending.id
        state.edits.setdefault(pending.id, review.pretty_args(pending))
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "a / r / e")
    _normal()
    if unset > 0:
        _warning(_STILL_TYPESETTING)


def _warning(text: str) -> None:
    """
    One line the approver must not miss, wrapped to the pane and not clipped by it.
    """
    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.warn.vec4, text)
    imgui.pop_text_wrap_pos()


def _native_body(state: review.ReviewState, pending: PendingApproval) -> None:
    """
    What the call would do, in ImGui's own text: its unified diff a line to a row,
    or its arguments.
    """
    line_h = imgui.get_text_line_height_with_spacing()
    lines = state.diff_lines.get(pending.id)
    if lines is None:
        # Split once per pending item, not once per frame. A whole-file Write
        # produces a diff as long as the file, and re-splitting it sixty times a
        # second is work proportional to the change being reviewed -- backwards,
        # since the large diffs are the ones stared at longest.
        lines = (pending.diff.splitlines() or [""]) if pending.diff else []
        state.diff_lines[pending.id] = lines
    if not lines:
        _native_arguments(pending)
        return

    # Fit the diff up to a cap. A short diff that still needs scrolling is the
    # thing that makes an operator stop reading them.
    body_h = min(_BODY_LINES * line_h, line_h * len(lines) + 10.0)
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


def argument_text(value: object) -> str:
    """
    One argument as the approver reads it: a string is itself, never its ``repr``.

    A ``repr`` escapes a command's quotes and folds its line breaks into ``\\n``, so
    what is read is not what would run. Anything that is not a string is its
    ``repr``, and so is a string with nothing visible in it, which would otherwise
    be drawn as nothing at all.
    """
    return value if isinstance(value, str) and value.strip() else repr(value)


def _native_arguments(pending: PendingApproval) -> None:
    """
    The arguments of a call with nothing diff-shaped to show, each wrapped to the
    pane and all of it reachable: the block is as tall as its text up to the body
    cap and scrolls past it.

    No diff is a real answer, not a gap: a Bash command has nothing diff-shaped to
    show, and inventing one would be worse than the args.
    """
    style = imgui.get_style()
    shown = [f"  {name} = {argument_text(value)}" for name, value in pending.raw_args.items()]
    # Measured at the width the text will have, so that the block is sized by the
    # lines wrapping makes. The scrollbar's width is left out of the measure: it is
    # there only when the text is already taller than the cap.
    inner = max(imgui.get_content_region_avail().x - 8.0 - 2.0 * style.window_padding.x, 40.0)
    tall = imgui.get_text_line_height() + sum(
        style.item_spacing.y + imgui.calc_text_size(text, None, False, inner).y for text in shown
    )
    cap = _BODY_LINES * imgui.get_text_line_height_with_spacing()
    body_h = min(cap, tall + 2.0 * style.window_padding.y)
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, body_h), imgui.ChildFlags_.borders):
        imgui.text_colored(P.text_dim.vec4, _NO_DIFF)
        imgui.push_text_wrap_pos(0.0)
        for text in shown:
            imgui.text_colored(P.text.vec4, text)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def argument_parts(raw_args: Mapping[str, object]) -> tuple[typeset.Part, ...]:
    """
    A call's arguments as one document: each name dim and tight over its value,
    which is set whitespace-exact and broken to the window at any column.

    That form for every value and not only a command: an argument of a call this
    pane has no reading for is approved as it is, so none of it may be collapsed
    and none of it may run past the edge of a window that does not scroll sideways.
    """
    parts: list[typeset.Part] = []
    for name, value in raw_args.items():
        parts.append(typeset.Part(name or '""', typeset.Kind.LITERAL, "text_dim", tight=True))
        parts.append(typeset.Part(argument_text(value), typeset.Kind.WRAPPED_EXACT))
    return tuple(parts)


def _parts(
    view: Hashable, source: object, make: Callable[[], tuple[typeset.Part, ...]]
) -> tuple[typeset.Part, ...]:
    """
    The parts of ``view``, made again only when ``source`` is another object.

    Identity is the test because a parked call is a frozen record: the same
    arguments are the same objects for as long as the call is in the queue.
    """
    held = _PARTS.get(view)
    if held is None or held[0] is not source:
        held = _PARTS[view] = (source, make())
    return held[1]


@dataclass(frozen=True, slots=True)
class Description:
    """
    A parked call read as a short header and a piece of prose.

    ``fields`` are ``(argument name, value as shown)``. ``body`` is the prose, or
    ``""`` when the call carries none.
    """

    fields: tuple[tuple[str, str], ...]
    body: str


def describe(pending: PendingApproval) -> Description | None:
    """
    The header fields and the prose of a concern, a task declaration or a spawn.
    ``None`` for any other call.

    **Every argument lands somewhere.** The ones ``_DESCRIBED`` names come first, in
    its order; the rest follow in the call's own. An argument this pane has never
    heard of is still part of what an approval releases, and a header that listed
    only the expected names would be a place to put one where the approver cannot
    see it. For the same reason the prose argument is the body only when it is a
    non-blank string: anything else is shown among the fields as its ``repr``.

    Pure, and cheap enough per frame: it copies a handful of references and joins
    two short lists. The prose itself is not copied.
    """
    shape = _DESCRIBED.get(pending.tool_name)
    if shape is None:
        return None
    prose, leading = shape
    args = pending.raw_args
    raw = args.get(prose)
    body = raw if isinstance(raw, str) and raw.strip() else ""
    names = [name for name in leading if name in args]
    names += [name for name in args if name not in leading and (name != prose or not body)]
    return Description(tuple((name, _field_text(name, args[name])) for name in names), body)


def _field_text(name: str, value: object) -> str:
    """
    A string as itself and a list of names joined, each only where the bus declares
    that type; anything else as its ``repr``, so a value of a type its argument
    should not have looks like what it is rather than like the value it resembles.
    """
    if name in _LIST_FIELDS:
        if isinstance(value, list | tuple) and value:
            items = [item for item in value if isinstance(item, str)]
            if len(items) == len(value):
                return ", ".join(items)
        return repr(value)
    return value if isinstance(value, str) else repr(value)


def edit_withheld(state: review.ReviewState, pending: PendingApproval) -> bool:
    """
    Whether the operator holds an edit of these arguments that the row is not showing.

    The body under a closed editor is drawn from the call as the agent made it,
    while ``review.resolve`` sends the draft. They differ whenever a draft outlives
    its editor -- ``e`` on another row moves the editor and keeps this one's text --
    and then ``a`` approves something other than the diff above it.

    Serialises the arguments to compare, which for a whole-file Write is the file;
    only reached while a draft exists.
    """
    draft = state.edits.get(pending.id)
    if draft is None or state.editing == pending.id:
        return False
    return draft != review.pretty_args(pending)


def refusals(pending: PendingApproval) -> tuple[str, ...]:
    """
    Each distinct reason the gate's read gives for this call's edit having no place
    to land, in the order the parts give them.
    """
    reasons = (text.will_not_apply for text in pending.texts or ())
    return tuple(dict.fromkeys(reason for reason in reasons if reason))


def _typeset_body(pending: PendingApproval, key: str) -> int:
    """
    What the call would do, set through the seam: its diff, or its description, or
    failing both its arguments. Answers how many lines of it are not set yet.

    **A body short of its text says so above itself** (``notice_above``), and the
    row holds its approve button until the answer here is zero: under the seam's
    budget a long body is whole some frames after it is first on screen -- a
    100,000-character concern took 27 frames, measured -- and the part that is
    there looks like all of it.

    **No child of this pane's own around a view.** A capped diff and a capped
    document each open one when they are taller than the cap, and a second scrolling
    child around that would be two wheels and two scrollbars for one body. A body
    under the cap is drawn straight into the queue's window, which scrolls, so the
    view culls to it.

    **The diff carries no copy of the gate's prediction** (``warning=False``): the
    row says it, above this body, under either renderer. **A large diff's model is
    made off the frame** (``background=True``); until it arrives the seam draws a
    line saying so and answers that line's height, so the row has its body, and
    its warning, on every frame in between.
    """
    line_h = imgui.get_text_line_height_with_spacing()
    wide = imgui.get_content_region_avail().x - 8.0
    cap = _BODY_LINES * line_h

    # Asked of the record before the seam: texts with no parts make a diff model
    # with nothing in it, and the seam's answer for that is "nothing to show".
    diffed = bool(pending.diff) if pending.texts is None else bool(pending.texts)
    if diffed and (
        typeset.diff(
            _VIEWS,
            (key, "diff"),
            pending,
            height_cap=_BODY_LINES * typeset.diff_row_height(),
            width=wide,
            warning=False,
            background=True,
        )
        is not None
    ):
        return _VIEWS.unbuilt((key, "diff")) or 0
    described = describe(pending)
    if described is None:
        imgui.text_colored(P.text_dim.vec4, _NO_DIFF)
        raw = pending.raw_args
        typeset.document(
            _VIEWS,
            (key, "arguments"),
            _parts((key, "arguments"), raw, lambda: argument_parts(raw)),
            width=wide,
            height_cap=cap - line_h,
            notice_above=True,
        )
        return _VIEWS.unbuilt((key, "arguments")) or 0

    # The fields are text on the pane's own ground, with no band of their own to
    # stand off the row's focus rectangle the way a diff's file header does.
    imgui.spacing()
    top = imgui.get_cursor_pos_y()
    left = imgui.get_cursor_pos_x()
    for name, value in described.fields:
        typeset.label(name, weight=typeset.Weight.REGULAR, colour=P.text_dim)
        imgui.same_line(left + _X_FIELD_VALUE)
        # Wrapped, not ellipsized: a subject is short enough that this is one line,
        # and when it is not, the rest of it is still something being approved.
        imgui.push_text_wrap_pos(left + wide)
        with typeset.font(typeset.Weight.REGULAR):
            imgui.text_colored(P.text_strong.vec4, value or '""')
        imgui.pop_text_wrap_pos()
    body = described.body
    if body:
        # The fields come out of the cap, down to a floor: a call with many
        # arguments still gets a readable window onto its prose.
        used = imgui.get_cursor_pos_y() - top
        room = max(cap - used, 8.0 * line_h)
        # ``prose_parts`` and not markdown outright: the seam's one rule for what a
        # body somebody wrote is read as, so this body reads here as it does in
        # DETAIL, on the board and in the brief.
        typeset.document(
            _VIEWS,
            (key, "body"),
            _parts((key, "body"), body, lambda: typeset.prose_parts(body)),
            width=wide,
            height_cap=room,
            notice_above=True,
        )
        return _VIEWS.unbuilt((key, "body")) or 0
    return 0


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

    # The turn's prose, clipped from the end: the ask is what a turn finishes on,
    # and this box is scanned rather than read. DETAIL carries the whole of it.
    tail = record.transcript.turn_prose()[-_PREVIEW_CHARS:]
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
    submitted, draft = multiline_input(
        "##reply",
        draft,
        imgui.ImVec2(-8.0, 54.0),
        wrap=wrap,
        flags=int(imgui.InputTextFlags_.allow_tab_input) | CTRL_ENTER_SUBMITS,
    )
    # Stored every frame: CTRL_ENTER_SUBMITS makes the returned bool mean "sent",
    # so there is no per-keystroke signal left to gate the draft on.
    compose.replies[node] = draft
    _small()
    imgui.text_colored(P.text_dim.vec4, "Ctrl+Enter sends, Enter for a new line")
    _normal()

    if wants_send(submitted, imgui.button("send"), draft):
        actions.send(node, draft.strip())
        compose.replies[node] = ""
        compose.focus_reply = True
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
        # Same task, same directory, same model, same team shape. A crashed session
        # cannot be resumed -- its subprocess is gone -- so the honest offer is a
        # fresh one carrying the same instructions, not a "resume" that would be a
        # new session wearing the old one's name.
        actions.relaunch(LaunchSpec.from_record(record))
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
    # A refused approve is said on the open row of a call it covered. Here when
    # there is no such row: the cursor is on a question, a failure, or nothing.
    refusal = state.refusal
    current = focus.obligation(snap)
    on_row = isinstance(current, ApprovalNeeded) and (
        refusal is not None and current.approval.id in refusal.asked
    )
    if refusal is not None and not on_row:
        _warning(refusal.text)
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


# -- the cold start ----------------------------------------------------------------
#
# Two empty states live in this pane and they are not the same emptiness.
# ``_zero_state`` below is a fleet that exists and owes nothing -- it answers "what is
# everyone doing". This one is a fleet that does not exist, which has no "everyone"
# to report on and exactly one useful thing to say: how to start.

_INTRO = "pptmstr runs a fleet of Claude agents, and hands you every decision they reach."

# The chord is spelled out, not merely coloured. STYLE.md's first rule is that hue is
# never the only channel, and an operator on ``high_contrast`` -- where accent and text
# collapse toward each other -- would otherwise have nothing marking this line as an
# instruction. The accent makes it findable; the literal "Ctrl+N" is what makes it
# actionable, and that survives every palette.
#
# Phrased as a suggestion for the same reason app.py:411 phrases its neighbour that
# way: the pane offers the next step, it does not take it.
_HINT_CHORD = "Ctrl+N"
_HINT_TEXT = "- start a session"

_ART_ROWS = len(splash_art.ART)
_ART_COLS = max(len(row) for row in splash_art.ART)

# Module-level, which is the one place this departs from ``splash.ArtFrames``' own
# advice to let the pane own it. ``inbox.draw`` is handed no presentation-state object
# to hang it on, and adding a parameter would mean editing app.py's call site, which
# belongs to nobody on this task.
#
# The deviation is affordable because there is nothing here to invalidate: the key is
# the step index, the art and the ranking are module constants, and colour is applied
# at draw time rather than baked into the memo -- so a theme switch cannot make an
# entry stale and a pane teardown leaks one tuple of 61 strings.
#
# What it buys, measured rather than estimated: ``time.perf_counter`` either side of
# ``splash.art_frame``, once per step across one whole 81-step cycle, gives 0.55ms mean
# and 0.99ms worst against 0.0002ms for a memo hit (2000 hits, same clock, one machine).
# The step rate is 16/s while a window being dragged is awake at 60, so 44 of every 60
# frames ask for a picture that has not changed -- about 24ms a second of re-derivation
# the render thread does not do. That is roughly 4% of the frame budget, not the quarter
# an earlier figure here claimed; still worth a module global, and worth stating at its
# real size, because a number nobody can reproduce is the one that gets built on.
_ART_FRAMES = splash.ArtFrames()

# The three luminance levels, as named palette entries.
#
# The obvious triple -- text_dim, text, text_strong -- must not ship. ``text`` and
# ``text_strong`` are the same bytes on high_contrast and on win311, so the line and the
# row behind it would be one flat bar there; and on cde (1.08:1) and turbo (1.07:1) the
# two separate in hue alone, which theme.py's first rule forbids as a sole channel.
#
# So TRAIL is RASTER's own ink at one step of ``theme.faded``, and the step is forced
# from both sides of faded's twelve-step grid. Step 9 collapses TRAIL into DIM on
# high_contrast -- white at 191/255 over black is #BFBFBF against text_dim's #C0C0C0,
# 1.01:1 -- and step 11 drops TRAIL to within 1.19:1 of RASTER on dark. Step 10 is the
# step that maximises the *smaller* of the two separations, which is the quantity that
# matters, because either one collapsing costs the same thing: a one-row band.
#
# And that is why the triple is pinned next door rather than here.
# ``splash.RASTER_ROWS_PER_SECOND`` is 16 only because the luminance band is two rows,
# and the band is two rows only where TRAIL is seen as part of the line instead of as
# more background. splash.py cannot check that -- it emits an ordinal and imports no
# palette by design -- so its rate constant is an assertion about the three lines below,
# and tests/test_theme.py holds it over all nine palettes.
_TRAIL_ALPHA = 10.0 / 12.0


def _ink(level: splash.Luminance) -> int:
    """
    The packed colour a row at ``level`` is drawn in.

    A chain closed by ``assert_never`` rather than a table, so a new member of
    ``splash.Luminance`` is a mypy error here rather than a KeyError at frame time or,
    worse, a ``.get`` default that renders the new level as an ordinary dim row. That
    totality is what the enum's own docstring asks the pane for, and a dict cannot
    give it.
    """
    if level is splash.Luminance.DIM:
        return P.text_dim.u32
    if level is splash.Luminance.TRAIL:
        return faded(P.text_strong, _TRAIL_ALPHA)
    if level is splash.Luminance.RASTER:
        return P.text_strong.u32
    assert_never(level)


def _splash(now: float) -> None:
    """
    The cold start: what this is, the quote, how to begin, and the art under it.

    Ordered so the two lines an operator has to *act* on are above the fold. The art
    is the last thing drawn and the first thing dropped, because it is the only part
    that can fail to fit.
    """
    imgui.spacing()
    imgui.text_colored(P.text_dim.vec4, _INTRO)
    imgui.spacing()
    _quote(now)
    imgui.spacing()
    imgui.text_colored(P.accent.vec4, _HINT_CHORD)
    imgui.same_line()
    imgui.text_disabled(_HINT_TEXT)
    imgui.spacing()
    _art(now)


def _quote(now: float) -> None:
    """
    The quote, one character at a time, each at its own alpha.

    Draws ``frame.glyphs`` and measures ``frame.lines``, and that split is the whole of
    what this pane has to get right about the glitch. The glyphs carry this frame's
    corrupted characters; the prose carries the block's extent, which must not move when
    a character is corrupted. Drawing the prose would produce a correct, still quote with
    no glitch in it, and measuring the glyphs would tie the space claimed below to an
    animation.

    Through the draw list rather than as a chain of ``text_colored``/``same_line``
    calls, for two reasons that both matter here. ``theme.faded`` returns the *packed*
    colour and memoises it, so a per-character tint is a dict hit rather than the
    per-frame colour arithmetic theme.py's header rules out of panels; and a hundred
    characters as a hundred layout items would put a hundred entries in this window's
    ID stack every frame to draw two lines of prose.

    Positioning is by multiplying out a single advance, which is sound only because
    the UI face is monospace -- the same property the art's column alignment rests on.
    """
    frame = splash.quote_frame(now)
    draw = imgui.get_window_draw_list()
    origin = imgui.get_cursor_screen_pos()
    advance = imgui.calc_text_size("M").x
    pitch = imgui.get_text_line_height_with_spacing()

    for row, (line, alphas) in enumerate(zip(frame.glyphs, frame.alphas, strict=True)):
        y = origin.y + row * pitch
        for column, (char, alpha) in enumerate(zip(line, alphas, strict=True)):
            if char == " ":
                continue
            draw.add_text(imgui.ImVec2(origin.x + column * advance, y), faded(P.text, alpha), char)

    # The draw list writes pixels without advancing the cursor, so the space has to be
    # claimed explicitly or everything below would be drawn on top of the quote.
    imgui.dummy(imgui.ImVec2(advance * max(map(len, frame.lines)), pitch * len(frame.lines)))


def _art(now: float) -> None:
    """
    The art, centred, at the largest size the remaining region allows, a colour a row.

    Returns without drawing when it does not fit. ``fit_size`` clamps up to its floor
    rather than reporting failure, so the answer still has to be checked against the
    room -- a clipped half-silhouette reads as a rendering fault, where nothing reads
    as deliberate.

    Through the draw list, which ``_quote`` also does but for a different reason. Here
    it is the only way to get a colour per row: the whole picture as one text item takes
    one pushed style colour, and one ``text()`` per row would insert
    ``style.item_spacing.y`` between rows and stand the art 60 spacings taller than
    ``required_extent`` just promised the pane it would be. Placing the rows by hand
    moves the pitch onto ``splash.LINE_PITCH_EM``, the constant ``fit_size`` and
    ``required_extent`` are already written against, so the three cannot disagree about
    where row 60 is. ImGui's own multi-line block advances by exactly that same pitch --
    ``calc_text_size`` on the 61-row art returns 61.0000 x size at every even size in
    the clamp range, measured in a live frame -- so this is a reimplementation of what
    the one call was already doing, not a new geometry.

    A row is one ``add_text``, not one per cell as in ``_quote``. ImGui emits no
    vertices for a glyph with no visible rect, so the art's spaces cost nothing to pass
    through and 61 calls draw what 4392 would. Nothing on this route is a printf path,
    so the '%' in the art needs no guarding here.
    """
    avail = imgui.get_content_region_avail()
    size = splash.fit_size(avail.x, avail.y, _ART_ROWS, _ART_COLS)
    width, height = splash.required_extent(size, _ART_ROWS, _ART_COLS)
    if width > avail.x or height > avail.y:
        return

    frame = _ART_FRAMES.frame(splash_art.ART, splash_art.RANK_OF, splash_art.RANKS, now)

    # Centring folds into the x every row is drawn at, so the cursor moves first and the
    # origin is read after it. That keeps the dummy below starting where the ink starts
    # rather than claiming a block offset half a pane to the left of the picture.
    imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + max(0.0, (avail.x - width) * 0.5))
    draw = imgui.get_window_draw_list()
    origin = imgui.get_cursor_screen_pos()
    pitch = size * splash.LINE_PITCH_EM

    imgui.push_font(face(Face.BODY), size)
    for row, (line, level) in enumerate(zip(frame.lines, frame.luminance, strict=True)):
        draw.add_text(imgui.ImVec2(origin.x, origin.y + row * pitch), _ink(level), line)
    imgui.pop_font()

    # The draw list writes pixels without advancing the cursor, so the space has to be
    # claimed explicitly or everything below would be drawn on top of the art. Exactly
    # ``required_extent``, which is the number already checked against the pane above.
    imgui.dummy(imgui.ImVec2(width, height))


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
