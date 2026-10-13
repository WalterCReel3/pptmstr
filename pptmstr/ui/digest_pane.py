"""
DIGEST: the round of the session under the cursor, read off its board.

**A pane of its own because it is not a task and not a concern.** The board is
those two tables; this is a reading of them and of the queue -- what changed, what
is waiting on the operator, what nobody said was run, what went wrong -- and it is
the same value ``read_digest`` hands the lead (``digest.digest``), rendered for the
person the lead reports to. It is docked in BOARD's space because it is read with
the board, and it shows a digest exactly when BOARD shows a board
(``board_pane.shows_board``), so the two tabs never disagree about whether there
is a team here.

Always open. The pane is the disclosure: an operator who does not want the round
leaves the tab behind BOARD.

**The whole session, not a round.** The operator has no mark of their own -- a
second "what have I seen" would be a second cursor -- so everything is here and
the lead's mark is drawn where it falls (``digest_rows``).

Follows the cursor, like every pane in this space, and no line of it moves the
cursor. Nothing is bounded (``digest_rows`` says why).

The row type, the tone colours and the author's bar are ``board_pane``'s. A body
quoted here is drawn by the same ``author`` as a body on a board row, so "not the
application's own words" is one bar in both panes.
"""

from __future__ import annotations

from imgui_bundle import imgui

from ..board import board_concerns, board_tasks, has_board
from ..digest import (
    EARLIER_INCIDENTS,
    Digest,
    change_label,
    changed_heading,
    digest,
    digest_mark,
    incident_label,
    incidents_heading,
    owed_counts,
    unverified_heading,
    unverified_label,
)
from ..model import Snapshot
from .board_pane import Line, Tone, absent_line, author, shows_board, tone_colour
from .focus import FocusState
from .widgets import one_line


def draw(snap: Snapshot, focus: FocusState) -> None:
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
        imgui.push_text_wrap_pos(0.0)
        for row in digest_rows(digest(snap, session_id), digest_mark(snap, session_id)):
            if row.tone is Tone.AUTHOR:
                author(row.text)
            else:
                imgui.text_colored(tone_colour(row.tone).vec4, row.text)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


# -- pure helpers, testable without a GL context -------------------------------

# What the lead's mark is drawn as, between what it has reported and what it has not.
DIGEST_MARK = "-- the lead last read a digest here --"


def digest_rows(d: Digest, mark: float | None) -> tuple[Line, ...]:
    """
    The digest as the lines the pane draws, in order, with the lead's mark placed.

    Pure, so the property the pane exists for is checkable without a frame: every
    unverified statement and every incident in ``d`` is a row here. Nothing is
    bounded. The board's task table caps its rows and says so; a cap here would be
    the digest showing fewer unverified items than the board holds, on the surface
    whose reader is the one who has to act on them.

    **The mark is a divider inside the two slots that are in time order**, changed
    and incidents: above it is what the lead's last digest already covered, below
    it what has happened since. It is drawn only where the lead has read one, and
    it is drawn at the end of a list everything in which is old -- "nothing new
    since" is what that placement says.

    The waiting slot shows each queue row. This is the operator's surface and the
    queue is the operator's own.

    **A row is the pane's or an agent's, never part of each.** A label the pane
    composes around a title or a subject is flattened to one line, so the field
    cannot end the pane's line and begin another; a body is a row of its own with
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

    rows.append(Line(incidents_heading(d), Tone.HEADING))
    reported = (*d.earlier_incidents, *d.incidents)
    placed = mark is None or not reported
    if d.earlier_incidents:
        rows.append(Line(f"{EARLIER_INCIDENTS}:", Tone.PROSE))
    for incident in reported:
        if not placed and mark is not None and incident.at >= mark:
            rows.append(Line(DIGEST_MARK, Tone.DIVIDER))
            placed = True
        rows.append(Line(one_line(incident_label(incident)), Tone.ALARM))
        if incident.concern.body:
            rows.append(Line(incident.concern.body, Tone.AUTHOR))
    if not placed:
        rows.append(Line(DIGEST_MARK, Tone.DIVIDER))
    return tuple(rows)
