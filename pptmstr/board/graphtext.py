"""
Words for ``board.graph``'s answers: ``why_blocked``, ``ready`` and a dry-run
``declare_task``.

Every one is computed from the one ``Snapshot`` a single board read delivered
(``BoardDelivered.snapshot``), so a reply never mixes two moments of the board.
Cut from ``bus`` unchanged (``planning/2026-11-07-the-bus-is-a-table-of-tools.md``,
B7). Pure: no SDK, no clock, no IO.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import assert_never

from ..effects import between_quotes
from ..model import ALL_WRITERS, Snapshot, TaskId, TaskState
from ..replies import _MAX_BOARD_CHARS, _QUOTE_LEGEND, _named
from .graph import DryRun, Edge, Hop, Ready, Root, RootKind, Verdict, WhyBlocked, is_open
from .rows import BoardTask
from .text import _already_behind_text, _auto_depends_text, _declare_refusal_text

# What a cut result or list ends with, at most. Reserved out of
# ``bus.MAX_TEXT_CHARS`` so the text and the sentence saying it was cut are
# together inside the bound.
_CUT_NOTE_ROOM = 200


def _loop_has_a_gate(
    cycle: tuple[TaskId, ...], named: Sequence[TaskId], snap: Snapshot | None
) -> bool:
    """
    Whether a task on ``cycle`` waits on every writer: the one being declared,
    by ``named`` holding ``ALL_WRITERS``, or one already on the board.

    True when ``snap`` is None, a board read the store never answered: the
    sentence it decides is a true rule about the word, and leaving it out of a
    loop that does run through a gate would hide the edge the declarer cannot
    see in any ``depends_on``.
    """
    if ALL_WRITERS in named:
        return True
    if snap is None:
        return True
    return any(t in snap.tasks and snap.tasks[t].after_all_writers for t in cycle[1:-1])


# How many roots a why_blocked reply lists. A gate that waits on every writer
# has most of the board as roots, and a reply that grew with the board would be
# the board read this tool exists to replace. Nearest first, so what is cut is
# the far end of the chain, and the reply says how many it left out.
_MAX_ROOTS_LISTED = 20


_OPEN_BOARD_READ = "read_board(open_only=true) lists every open task and what each waits on."


def _shared_note(edge: Edge) -> str:
    """
    The files an edge's two tasks both touch, said beside the edge, and whether
    they are all test files. Empty for an edge with nothing shared.
    """
    if not edge.shared:
        return ""
    paths = ", ".join(_named(p) for p in edge.shared)
    return f", sharing {paths}{' (test files only)' if edge.test_only else ''}"


def _hop_state(hop: Hop, snap: Snapshot) -> str:
    """What a hop on a chain is, in the words a reader acts on."""
    if hop.state is None:
        if hop.id in snap.tasks:
            return "a task of another session's board, which nothing here can finish"
        return "never declared on any board in this window"
    if hop.state is TaskState.CLAIMED:
        return f"held by {_named(hop.holder or 'an agent this board cannot address')}"
    if hop.state is TaskState.PENDING:
        return "pending, unheld"
    return "finished"


def _root_needs(root: Root, snap: Snapshot) -> str:
    """What has to happen at a root for the chain to move."""
    match root.kind:
        case RootKind.UNCLAIMED:
            return "it waits on nothing unfinished, so it needs a claimer"
        case RootKind.HELD:
            return "it is held, so the chain waits on its holder finishing"
        case RootKind.NOT_ON_BOARD if root.id in snap.tasks:
            return (
                "its id is taken by another session's task, so it can be neither "
                "finished nor declared from here, and the task waiting on it never "
                "becomes claimable; the operator can close that task from the board pane"
            )
        case RootKind.NOT_ON_BOARD:
            return (
                "nothing will finish it until a task under this id is declared on "
                "your board and finished: a typo in a depends_on, or work not yet "
                "declared"
            )
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(root.kind)


def _chain_lines(answer: WhyBlocked, snap: Snapshot) -> list[str]:
    """
    The roots of a chain, nearest first and at most ``_MAX_ROOTS_LISTED`` of
    them, each once with the path to it: one line per wait, naming the task
    that waits, the one it waits on, that one's state and holder, and the
    files the two share.
    """
    roots = sorted(answer.roots, key=lambda r: len(r.path))
    lines: list[str] = []
    for root in roots[:_MAX_ROOTS_LISTED]:
        last = root.path[-1]
        lines.append(f"- {_named(root.id)} ({_hop_state(last, snap)}): {_root_needs(root, snap)}.")
        for hop in root.path:
            lines.append(
                f"    {_named(hop.edge.waiting)} waits on {_named(hop.id)} "
                f"({_hop_state(hop, snap)}){_shared_note(hop.edge)}"
            )
    unlisted = len(roots) - _MAX_ROOTS_LISTED
    if unlisted > 0:
        lines.append(f"... and {unlisted} more not listed. " f"{_OPEN_BOARD_READ}")
    if answer.cycle:
        lines.append(
            "These tasks wait on themselves through the chain, so none of them can "
            f"be claimed until the loop is broken: "
            f"{', '.join(_named(t) for t in answer.cycle)}. The operator can close one "
            f"of them from the board pane."
        )
    return lines


def _why_blocked_text(answer: WhyBlocked, snap: Snapshot) -> str:
    """
    The reply to ``why_blocked``: what the task is, and for a task that waits,
    every unfinished root it waits on.

    **An unknown id is two mistakes, named apart** (STYLE.md section 3): an id no
    session in the window has, which is a typo or a task not yet declared, and
    the id of a task on another session's board, which is right and out of reach.
    ``board.graph`` answers both UNKNOWN, as a claim of either is refused; the
    snapshot holds every session's tasks, so the second is told by the id
    being in it.
    """
    named = _named(answer.task_id)
    match answer.verdict:
        case Verdict.UNKNOWN:
            if answer.task_id in snap.tasks:
                return (
                    f"Task {named} is not on your board: it is a task of another "
                    f"session in this window. Its chain cannot be read from here, and "
                    f"nothing on your board can claim or finish it."
                )
            return (
                f"No task with the id {named} is on your board, or on any board in "
                f"this window. It is not blocked and not ready: it does not exist. "
                f"Check the spelling; read_board lists every id on your board."
            )
        case Verdict.CLAIMABLE:
            return f"Task {named} can be claimed now: it waits on nothing unfinished."
        case Verdict.COMPLETED:
            return f"Task {named} is finished. Nothing is left to wait for."
        case Verdict.CLAIMED:
            holder = _named(answer.holder or "an agent this board cannot address")
            if not answer.roots and not answer.cycle:
                return f"Task {named} is held by {holder}, and waits on nothing unfinished."
            head = (
                f"Task {named} is held by {holder}, and still waits on "
                f"{len(answer.roots)} unfinished task(s) at the end of its chain:"
            )
        case Verdict.BLOCKED:
            head = (
                f"Task {named} cannot be claimed yet. It waits on {len(answer.roots)} "
                f"unfinished task(s) at the end of its chain, nearest first:"
            )
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(answer.verdict)
    return "\n".join([head, *_chain_lines(answer, snap)])


def _ready_text(ready: Sequence[Ready], rows: Sequence[BoardTask]) -> str:
    """
    The reply to ``ready``: the claimable tasks, the one the most pending tasks
    wait on first, directly or through other tasks, each with that count. Not
    what its completion alone would free: a task waiting on two of them waits
    on each. Bounded by
    ``_MAX_BOARD_CHARS`` like a board read, and says how many it left out.
    """
    if not rows:
        return "Your board has no tasks on it yet."
    if not ready:
        still_open = sum(1 for r in rows if is_open(r))
        if not still_open:
            return "Nothing on your board can be claimed: every task on it is finished."
        return (
            f"Nothing on your board can be claimed right now: each of its {still_open} "
            f"open task(s) is held or waits on unfinished work. why_blocked(task_id) "
            f"says on what."
        )
    lines = [
        f"{len(ready)} task(s) on your board can be claimed now, the one that "
        f"the most pending tasks wait on first, directly or through other tasks. "
        f"{_QUOTE_LEGEND}"
    ]
    used = len(lines[0])
    for shown, r in enumerate(ready):
        line = (
            f"- {_named(r.task_id)} {between_quotes(r.title)} -- "
            f"{r.unblocks} pending task(s) wait on it"
        )
        if used + len(line) + _CUT_NOTE_ROOM > _MAX_BOARD_CHARS:
            lines.append(
                f"... and {len(ready) - shown} more that fewer wait on, not listed here. "
                f"{_OPEN_BOARD_READ}"
            )
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _dry_run_text(
    dry: DryRun, asked: TaskId, named: Sequence[TaskId], snap: Snapshot, on_my_board: bool
) -> str:
    """
    The reply to ``declare_task(dry_run=true)``: what the real declaration would
    do, in its words, said as what would happen.

    A refusal is worded by ``text._declare_refusal_text``, which the real declaration
    uses. A landing lists every edge, marked as the declarer's own or added by
    the board; then the sentences the real reply would carry about the edges the
    board adds -- the advice to narrow ``touches`` is in those, so it is given
    about those edges and no others, since narrowing cannot remove an edge the
    declarer wrote -- then whether it could be claimed at once, and its chain.

    ``named`` is the declaration's ``depends_on``, the word ``ALL_WRITERS``
    included when it was given.
    """
    gate = ALL_WRITERS in named
    opening = "DRY RUN: nothing was declared, and the board is unchanged."
    if dry.refusal is not None:
        why = _declare_refusal_text(
            dry.refusal,
            dry.held_off_fleet,
            dry.cycle,
            on_my_board,
            named,
            _loop_has_a_gate(dry.cycle, named, snap),
        )
        return f"{opening} Declared now, task {_named(asked)} would NOT be on the board. {why}"
    lines = [f"{opening} Declared now, task {_named(asked)} would be on the board."]
    if dry.edges:
        lines.append("It would wait on:")
        for edge in dry.edges:
            by = "added by the board" if edge.auto else "named in depends_on"
            lines.append(f"- {_named(edge.on)}, {by}{_shared_note(edge)}")
    elif not gate:
        lines.append("It would wait on no task.")
    if gate:
        now = ", ".join(_named(w) for w in dry.writers[:_MAX_ROOTS_LISTED]) or "none unfinished"
        more = len(dry.writers) - _MAX_ROOTS_LISTED
        lines.append(
            f"It would also wait on every unfinished writer on this board ({ALL_WRITERS!r}), "
            f"declared before or after it (now: {now}"
            f"{f', and {more} more' if more > 0 else ''})."
        )
    if dry.added:
        lines.append(f"The real reply would say: {_auto_depends_text(dry.added)}")
    if dry.already_behind:
        lines.append(f"The real reply would say: {_already_behind_text(dry.already_behind)}")
    lines.append(f"It could be claimed at once: {'yes' if dry.claimable else 'no'}.")
    if dry.chain is not None and dry.chain.roots:
        lines.append("Its chain, nearest first:")
        lines += _chain_lines(dry.chain, snap)
    return "\n".join(lines)
