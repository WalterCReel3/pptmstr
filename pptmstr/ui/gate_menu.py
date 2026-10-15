"""
The Gate menu's rows: for each rung, what choosing it would do to this session.

A function of three facts -- the rung the session answers with now, whether its CLI
process was started with containment, and whether it is running -- and of the ladder
in ``approval.py``. It draws nothing and calls nothing, so the table can be tested
whole, and against the driver's own guard, without a window.

**A rung the running process cannot take is offered as a resume, never as a live
act.** ``AgentSession.set_policy`` refuses a rung that ``approval.requires_containment``
on a session started without containment, because containment is ``--settings`` on the
CLI's argv and cannot be added to a process that is already running. The same question
is asked of the same function here, so the row that would be refused is the row that
says "Continue as ...": the menu never offers a click whose only outcome is a raise.

**What a live move is, exactly.** ``set_policy`` assigns one field, and the gate reads
that field each time it classifies a tool call. So a move in either direction is in
force for the next call the gate judges and for nothing before it: a call already let
through keeps running after a narrow, and a call already waiting for the operator keeps
waiting after a widen. It also does not reach what the agents were told. The system
prompt and the worker prompts read the rung once, when the run builds its options, so
they go on describing the rung the session started under; a resume is what delivers a
new briefing. Every live row's ``consequence`` says both, because neither is visible
from the rung's name.

**A row is three things, and the menu draws the shortest.** ``short`` is the line in
the menu: the rung's name, a word after it where it is the rung in force, and a
suffix where choosing it does something other than switch at once. ``sentence`` is the one
sentence the question in front of a move shows. ``consequence`` is the whole of what
choosing the row changes and what it does not, which ``sentence`` is part of; nothing
draws it, and it is what a guide to this menu would be written from.

**Rung order is ``Policy``'s definition order**, lowest first, which that enum documents
as ordered by how much is released. Nothing here names a rung.

**There is no "unavailable" kind.** With these inputs every rung is reachable one way
or the other. Whether a resume under a rung is granted -- its directory, its
containment -- is asked when the resume form is pressed, of facts this function is not
given.

Imports no imgui. What a rung releases is ``widgets.gate_adds``'s answer, which is
asked of the real classifier; that module imports imgui, so the caller hands the
function in rather than this one importing it.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass

from ..approval import Policy, requires_containment

__all__ = ["ASKS", "LAST_RUN", "NOW", "RESTARTS", "Kind", "Row", "rows"]

# What a rung lets run unattended, as short labels: ``widgets.gate_adds``.
Adds = Callable[[Policy], tuple[str, ...]]

_BRIEFING = "The agent's briefing still describes the rung it started under."

# After the rung a running session is on.
NOW = " (now)"
# After the current row of a session that is not running: the rung is the one its
# last run had, and nothing is in force.
LAST_RUN = " (last run)"
# After a rung that is not switched to at once: a question or a form comes first.
ASKS = "…"
# After a rung the running process cannot take: reaching it ends this run.
RESTARTS = "  restart…"


class Kind(enum.Enum):
    """What choosing a row does."""

    # The rung the session answers with now. Shown, not chosen.
    CURRENT = "current"
    # A lower rung, on the running process, at once. Never refused.
    NARROW_LIVE = "narrow_live"
    # A higher rung, on the running process, at once: one the guard admits.
    WIDEN_LIVE = "widen_live"
    # Through the resume form. For a running session it is the only way to a rung
    # the process cannot take; for an ended one it is the only way to any rung.
    CONTINUE_AS = "continue_as"


@dataclass(frozen=True, slots=True)
class Row:
    """
    One rung, and what is said about choosing it.

    ``short`` is the menu's line. ``label`` names the act in words, for the
    question asked before it. ``sentence`` is the one sentence of ``consequence``
    that question shows: what the move does to the session. ``consequence`` is
    the whole account, and is drawn nowhere.
    """

    rung: Policy
    kind: Kind
    label: str
    consequence: str
    short: str
    sentence: str

    @property
    def acts(self) -> bool:
        """Whether choosing this row does anything."""
        return self.kind is not Kind.CURRENT


def rows(policy_now: Policy, contained: bool, live: bool, *, adds: Adds) -> tuple[Row, ...]:
    """
    One row per rung, lowest first.

    ``policy_now`` is the rung the session answers with: the session object's for a
    running one, the last one granted for an ended one. ``contained`` is whether the
    running process was started with containment settings, the fact
    ``AgentSession.set_policy`` is refused on; it is not read for an ended session,
    whose next process has not been started with anything yet. ``live`` is whether
    there is a running process to move.

    ``adds`` is ``widgets.gate_adds``.
    """
    ladder = tuple(Policy)
    here = ladder.index(policy_now)
    return tuple(
        _row(rung, policy_now, _direction(index, here), contained, live, adds)
        for index, rung in enumerate(ladder)
    )


def _direction(index: int, here: int) -> int:
    return (index > here) - (index < here)


def _row(rung: Policy, now: Policy, direction: int, contained: bool, live: bool, adds: Adds) -> Row:
    name = rung.value
    if direction == 0:
        return _current(rung, live, adds)
    if not live:
        sentence = f"Opens the resume form under {name}; nothing starts until you send from it."
        return Row(
            rung,
            Kind.CONTINUE_AS,
            f"Resume as {name}…",
            f"{sentence} {_standing(rung, adds)}",
            f"{name}{ASKS}",
            sentence,
        )
    if direction < 0:
        returned = _without(adds(now), adds(rung))
        back = f"{_listed(returned)} wait for you again" if returned else f"{name} is in force"
        sentence = (
            f"From the next tool call the gate judges, {back}; a call already let through "
            "is not stopped."
        )
        return Row(
            rung,
            Kind.NARROW_LIVE,
            f"Narrow to {name}",
            f"{sentence} {_BRIEFING}",
            f"{name}",
            sentence,
        )
    if requires_containment(rung) and not contained:
        sentence = (
            "Opens the resume form; resuming ends the running "
            "session where it stands -- a turn in flight is not finished, and a tool call "
            f"or an approval it is waiting on goes with it -- and starts it under {name} "
            "with a briefing for that rung."
        )
        return Row(
            rung,
            Kind.CONTINUE_AS,
            f"Continue as {name}…",
            f"This process was started without the containment {name} requires, so it "
            f"cannot be moved there. {sentence}",
            f"{name}{RESTARTS}",
            sentence,
        )
    added = _without(adds(rung), adds(now))
    more = f"{_listed(added)} run without asking" if added else f"{name} is in force"
    sentence = (
        f"From the next tool call the gate judges, {more}; a call already waiting for "
        "you still waits."
    )
    return Row(
        rung,
        Kind.WIDEN_LIVE,
        f"Widen to {name}",
        f"{sentence} {_BRIEFING}",
        # A widening is asked about before it is made (``session_menu``), which
        # is the second step the suffix promises.
        f"{name}{ASKS}",
        sentence,
    )


def _current(rung: Policy, live: bool, adds: Adds) -> Row:
    standing = _standing(rung, adds)
    if live:
        return Row(
            rung,
            Kind.CURRENT,
            f"{rung.value} (now)",
            f"The gate this session answers with. {standing}",
            f"{rung.value}{NOW}",
            standing,
        )
    return Row(
        rung,
        Kind.CURRENT,
        f"{rung.value} (as last granted)",
        f"The gate this session last ran under; it is not running. {standing}",
        f"{rung.value}{LAST_RUN}",
        standing,
    )


def _standing(rung: Policy, adds: Adds) -> str:
    """What ``rung`` releases, as a sentence about the rung and not about a move."""
    released = adds(rung)
    if not released:
        return f"Under {rung.value} nothing off the standing allowlist runs without asking."
    return f"Under {rung.value}, {_listed(released)} run without asking."


def _without(labels: tuple[str, ...], taken: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(label for label in labels if label not in taken)


def _listed(labels: tuple[str, ...]) -> str:
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"
