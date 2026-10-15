"""
DIGEST: the round of the session under the cursor, read off its board.

**A pane of its own because it is not a task and not a concern.** The board is
those two tables; this is a reading of them and of the queue, for the person the
lead reports to. It is docked in BOARD's space because it is read with the
board, and it shows a digest exactly when BOARD shows a board
(``board_pane.shows_board``), so the two tabs never disagree about whether there
is a team here.

**Ordered by what the operator has to act on, not by the lead's report.** Top to
bottom:

1. *Taken without you*: every decision of the session its asker filed as
   already taken (``digest.taken_decisions``), the open ones first. Each is work
   an agent went on from that the operator has not ratified, and an answer that
   disagrees means work to undo. An open one is a line that puts the cursor on
   its NEEDS YOU row, as a click on that row does.
2. *Incidents*: those marked since the lead last read a digest first, then the
   earlier ones (``digest.digest`` from the lead's mark, which splits them).
3. Everything else -- changed, waiting, unverified -- behind one line that
   counts them, closed until the operator opens it. Open, it is the lead's
   slots as ``rest_rows`` draws them. Its open state is ImGui's, per session,
   for the life of the window.

``read_digest`` hands the lead the same ``digest.Digest`` in its own order and
with none of this; the reorganisation is this pane's alone.

**The whole session, not a round.** The operator has no mark of their own -- a
second "what have I seen" would be a second cursor -- so the lead's mark is the
one drawn, and it is what "new" means in the incident section.

**At most one line per item in the first two sections**, and nothing in them
wraps. An agent's text on such a line -- a question, a subject -- is flattened,
cut to what is left of the row, and drawn behind the author's bar
(``board_pane.author_inline``) after the pane's words, never among them.

The row type, the tone colours and the author's bar are ``board_pane``'s, so "not
the application's own words" is one bar in both panes.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from imgui_bundle import imgui

from ..board import board_concerns, board_tasks, has_board
from ..digest import (
    Digest,
    Reported,
    change_label,
    changed_heading,
    digest,
    digest_mark,
    incident_attribution,
    owed_counts,
    taken_decisions,
    unverified_heading,
    unverified_label,
)
from ..model import Answered, Decision, DecisionOwed, Dismissed, Snapshot
from ..theme import P
from .board_pane import (
    Line,
    Tone,
    absent_line,
    author,
    author_inline,
    shows_board,
    tone_colour,
)
from .decision_view import asker_role
from .focus import FocusState
from .widgets import ellipsis, ellipsis_by, flat_line, format_elapsed, one_line


def draw(snap: Snapshot, focus: FocusState, now: float) -> None:
    """
    Render the digest of the session under the cursor. Never reads the store.

    The one-line refusals are the board pane's, word for word and on the same
    conditions (``board_pane.absent_line``, ``shows_board``): a tab that said
    "not a team" beside one drawing a board would be two answers to one question.
    """
    node = focus.node(snap)
    if node is None:
        imgui.text_disabled("no session under the cursor")
        return

    session_id = node[0]
    absent = absent_line(snap, session_id)
    if absent is not None:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(absent)
        imgui.pop_text_wrap_pos()
        return
    if not shows_board(
        has_board(snap, session_id),
        len(board_tasks(snap, session_id)),
        len(board_concerns(snap, session_id)),
    ):
        imgui.text_disabled("this session is not a team")
        return

    if imgui.begin_child("##digest"):
        mark = digest_mark(snap, session_id)
        whole = digest(snap, session_id)
        marked = whole if mark is None else digest(snap, session_id, mark)
        for item in (*taken_items(snap, session_id, now), *incident_items(marked, mark)):
            _item(item, focus)
        imgui.spacing()
        imgui.push_text_wrap_pos(0.0)
        # Cut short of the arrow and the frame's padding: a tree node's label
        # does not wrap.
        label = ellipsis(
            rest_label(whole), imgui.get_content_region_avail().x - 2.5 * imgui.get_font_size()
        )
        if imgui.tree_node_ex(
            f"{label}###{rest_id(session_id)}",
            int(imgui.TreeNodeFlags_.span_avail_width),
        ):
            for row in rest_rows(whole, mark):
                if row.tone is Tone.AUTHOR:
                    author(row.text)
                else:
                    imgui.text_colored(tone_colour(row.tone).vec4, row.text)
            imgui.tree_pop()
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def rest_id(session_id: str) -> str:
    """
    The id of the folded line, one per session: ImGui keeps a tree node's open
    flag by id, so each session's is its own and lasts as long as the window.
    """
    return f"rest-{session_id}"


def _item(item: Item, focus: FocusState) -> None:
    """
    One item on one line: the pane's words, then each quoted text behind the
    bar, cut so the whole fits the row. An item that opens a row is a button the
    width of the pane under the text.
    """
    width = imgui.get_content_region_avail().x
    origin = imgui.get_cursor_screen_pos()
    if item.opens is not None:
        imgui.push_id(item.opens.key)
        height = imgui.get_text_line_height()
        if imgui.invisible_button("##open", imgui.ImVec2(max(width, 1.0), height)):
            # What a click on the row in NEEDS YOU does: the row is the one
            # under the cursor, which is the one that is drawn open.
            focus.to_obligation(item.opens)
        if imgui.is_item_hovered():
            imgui.get_window_draw_list().add_rect_filled(
                origin,
                imgui.ImVec2(origin.x + width, origin.y + height),
                P.panel_alt.u32,
            )
        imgui.pop_id()
        imgui.set_cursor_screen_pos(origin)

    spacing = imgui.get_style().item_spacing.x
    texts = fit(
        item.parts,
        width,
        lambda text: float(imgui.calc_text_size(text).x),
        spacing=spacing,
        gutter=imgui.get_font_size(),
    )
    colour = tone_colour(item.tone)
    imgui.push_text_wrap_pos(-1.0)
    for index, (part, text) in enumerate(zip(item.parts, texts, strict=True)):
        if index:
            imgui.same_line()
        if part.quoted:
            author_inline(text, colour if item.tone is Tone.PROSE else None)
        else:
            imgui.text_colored(colour.vec4, text)
    imgui.pop_text_wrap_pos()


# -- pure helpers, testable without a GL context -------------------------------

# What the lead's mark is drawn as, between what it has reported and what it has not.
DIGEST_MARK = "-- the lead last read a digest here --"

TAKEN_HEADING = "TAKEN WITHOUT YOU"
NOTHING_TAKEN = "nothing taken without you"
INCIDENTS_HEADING = "INCIDENTS"
NO_INCIDENTS = "no incidents marked"
# Above the incidents from before the lead's mark.
EARLIER = "before the lead's last digest:"
NO_SUBJECT = "(no subject)"


# The least of a row a quoted run is left, as a share of the row, when the
# pane's words on it would take more. An incident's attribution can be longer
# than a narrow dock; cut whole, it would leave the subject no room at all.
QUOTED_SHARE = 0.45


def fit(
    parts: Sequence[Part],
    width: float,
    measure: Callable[[str], float],
    *,
    spacing: float,
    gutter: float,
) -> tuple[str, ...]:
    """
    Each part's text, cut (``widgets.ellipsis_by``) so the whole row fits
    ``width``: the pane's words first, out of what the quoted runs' floor
    leaves, then the quoted runs out of what is left after them.

    Within each of the two, a run takes at most an even share of what remains
    and the shortest are placed first, so a run that fits whole gives what it
    does not use to the longer ones -- "you:" survives a long role beside it.

    A quoted run costs its ``gutter`` -- where the bar goes -- as well as its
    text, and every run after the first costs ``spacing``.
    """
    quoted = [i for i, part in enumerate(parts) if part.quoted]
    own = [i for i, part in enumerate(parts) if not part.quoted]
    room = width - spacing * max(len(parts) - 1, 0) - gutter * len(quoted)
    cut = [""] * len(parts)

    def share(indices: list[int], left: float) -> float:
        ordered = sorted(indices, key=lambda i: measure(flat_line(parts[i].text)))
        for count, i in enumerate(ordered):
            cut[i] = ellipsis_by(parts[i].text, left / (len(ordered) - count), measure)
            left -= measure(cut[i])
        return left

    floor = room * QUOTED_SHARE if quoted else 0.0
    unused = share(own, room - floor)
    share(quoted, floor + unused)
    return tuple(cut)


@dataclass(frozen=True, slots=True)
class Part:
    """
    A run of one line: the pane's words, or with ``quoted`` an agent's or the
    operator's, drawn behind the author's bar. Already one line.
    """

    text: str
    quoted: bool = False


@dataclass(frozen=True, slots=True)
class Item:
    """One line of the first two sections."""

    parts: tuple[Part, ...]
    tone: Tone
    # The row in NEEDS YOU a click on this line puts the cursor on, or None.
    opens: DecisionOwed | None = None

    @property
    def text(self) -> str:
        """The line as read, for a test or a reader without the bar."""
        return " ".join(part.text for part in self.parts)


def _quoted(text: str) -> Part:
    return Part(flat_line(text), quoted=True)


def taken_items(snap: Snapshot, session_id: str, now: float) -> tuple[Item, ...]:
    """
    The first section: a heading and a line per decision taken without the
    operator, or one dim line saying there is none.

    An open one is in the warning colour, as its standing is in the decision's
    own body (``decision_view.standing_line``), and opens its NEEDS YOU row when
    that row exists. An ended one is dim, with the operator's answer when there
    is one.
    """
    taken = taken_decisions(snap, session_id)
    if not taken:
        return (Item((Part(NOTHING_TAKEN),), Tone.PROSE),)
    rows = {o.decision.id: o for o in snap.needs_you if isinstance(o, DecisionOwed)}
    still = sum(1 for d in taken if d.is_open)
    heading = f"{TAKEN_HEADING} ({still} open, {len(taken) - still} closed)"
    items = [Item((Part(heading),), Tone.HEADING)]
    for d in taken:
        role = asker_role(snap, d)
        if d.is_open:
            age = format_elapsed(now - d.raised_at)
            items.append(
                Item(
                    (Part(f"{role}, {age} ago:"), _quoted(d.question)),
                    Tone.ALARM,
                    rows.get(d.id),
                )
            )
        else:
            items.append(Item(_ended(d, role), Tone.PROSE))
    return tuple(items)


def _ended(d: Decision, role: str) -> tuple[Part, ...]:
    match d.outcome:
        case Answered(text=answer):
            said = _quoted(answer) if answer else Part("(nothing)")
            return (Part(f"{role}, answered:"), _quoted(d.question), Part("you:"), said)
        case Dismissed():
            return (Part(f"{role}, dismissed unanswered:"), _quoted(d.question))
        case _:
            return (Part(f"{role}, withdrawn by its asker:"), _quoted(d.question))


def incident_items(d: Digest, mark: float | None) -> tuple[Item, ...]:
    """
    The second section, from a digest counted from ``mark``: a heading, the
    incidents since the mark, then the ones before it under a line that says so;
    or one dim line saying none is marked.

    Every incident in ``d`` is a line here, which is the digest's invariant on
    this surface: never fewer than the board holds.
    """
    if not d.incidents and not d.earlier_incidents:
        return (Item((Part(NO_INCIDENTS),), Tone.PROSE),)
    new, earlier = len(d.incidents), len(d.earlier_incidents)
    tally = f"{new}" if mark is None else f"{new} new, {earlier} earlier"
    items = [Item((Part(f"{INCIDENTS_HEADING} ({tally})"),), Tone.HEADING)]
    items += [_incident(r) for r in d.incidents]
    if d.earlier_incidents:
        items.append(Item((Part(EARLIER),), Tone.PROSE))
        items += [_incident(r) for r in d.earlier_incidents]
    return tuple(items)


def _incident(reported: Reported) -> Item:
    subject = reported.concern.subject
    said = _quoted(subject) if subject else Part(NO_SUBJECT)
    return Item((Part(incident_attribution(reported)), said), Tone.ALARM)


def _counted(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def rest_label(d: Digest) -> str:
    """The folded line: how many of each slot it holds."""
    return " · ".join(
        (
            _counted(len(d.changed), "change", "changes"),
            f"{len(d.waiting)} waiting",
            f"{len(d.unverified)} unverified",
        )
    )


def rest_rows(d: Digest, mark: float | None) -> tuple[Line, ...]:
    """
    The folded slots as the lines the pane draws, in order, with the lead's mark
    placed: changed, waiting, unverified.

    Pure, so the property the pane exists for is checkable without a frame: every
    unverified statement in ``d`` is a row here. Nothing is bounded. The board's
    task table caps its rows and says so; a cap here would be the digest showing
    fewer unverified items than the board holds, on the surface whose reader is
    the one who has to act on them.

    **The mark is a divider inside the changed slot**, which is in time order:
    above it is what the lead's last digest already covered, below it what has
    happened since. It is drawn only where the lead has read one, and it is drawn
    at the end of a list everything in which is old -- "nothing new since" is what
    that placement says.

    The waiting slot shows each queue row. This is the operator's surface and the
    queue is the operator's own.

    **A row is the pane's or an agent's, never part of each.** A label the pane
    composes around a title is flattened to one line, so the field cannot end the
    pane's line and begin another; a body is a row of its own with
    ``Tone.AUTHOR``, which is the only tone drawn behind the author's bar.
    """
    rows: list[Line] = [Line(changed_heading(d.changed), Tone.HEADING)]
    placed = mark is None or not d.changed
    for change in d.changed:
        if not placed and mark is not None and change.at >= mark:
            rows.append(Line(DIGEST_MARK, Tone.DIVIDER))
            placed = True
        rows.append(Line(one_line(change_label(change)), Tone.ITEM))
        if change.line:
            rows.append(Line(change.line, Tone.AUTHOR))
    if not placed:
        rows.append(Line(DIGEST_MARK, Tone.DIVIDER))

    if d.waiting:
        rows.append(
            Line(
                f"WAITING ON YOU ({len(d.waiting)}), oldest first: {owed_counts(d.waiting)}",
                Tone.HEADING,
            )
        )
    else:
        rows.append(Line("WAITING ON YOU (0): nothing in this session.", Tone.HEADING))
    for owed in d.waiting:
        rows.append(Line(one_line(f"{owed.kind.value}, {owed.who}: {owed.summary}"), Tone.ITEM))

    rows.append(Line(unverified_heading(d.unverified), Tone.HEADING))
    for item in d.unverified:
        rows.append(Line(one_line(unverified_label(item)), Tone.ALARM))
        if item.text:
            rows.append(Line(item.text, Tone.AUTHOR))
    return tuple(rows)
