"""
A board from its log: what ``BoardRestored`` places, and what it reports it
could not.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import assert_never

from .. import drawable
from ..boardlog import Board, Party, RestoredConcern, RestoredDecision, RestoredTask
from ..channels.records import Channel, ChannelId, channel_as_linked, logged_charter
from ..intents import BoardRestored
from ..model import (
    MAX_ANSWER_CHARS,
    Answered,
    Basis,
    Concern,
    ConcernId,
    Decision,
    DecisionId,
    DecisionOutcome,
    Dismissed,
    NodeId,
    NotPlaced,
    Provenance,
    Restore,
    Snapshot,
    Standing,
    Task,
    TaskId,
    TaskState,
    TaskVersion,
    Unplaced,
    Withdrawn,
    normalised_property,
    normalised_touches,
    peer_addressed,
    restored_party,
    stated_provenance,
)
from ..naming import ROOT_NAMES
from .peers import _peer_fields_hold
from .tasks import _auto_depends, _would_cycle


def _restored(snap: Snapshot, intent: BoardRestored) -> (
    tuple[
        dict[TaskId, Task],
        dict[ConcernId, Concern],
        Mapping[DecisionId, Decision],
        Mapping[str, Restore],
    ]
    | None
):
    """
    The tasks, concerns, decisions and restore reports after ``intent``, or None
    when it changes none of them.

    **Everything the board holds is placed or is named in the report with why
    not.** A task, concern or decision is placed when its id is free. One the
    store already holds for this session exactly as the log has it is counted
    and left. Each of the rest is a ``NotPlaced``.

    **An id another session's board has is never written over.** ``tasks`` is one
    map for every session and leads choose ids by hand, so a resumed session's
    ``t1`` can meet a live ``t1`` it has nothing to do with. The logged one is
    left out and reported. That is a board restored short by a name collision,
    which keying tasks by session and id would remove; that is a change to every
    reader of the map and is not made here.

    **A store that holds a different board for the session takes nothing.** If
    it has a task, concern or decision of this session that the log has differently, or
    does not have at all, the store is ahead of the log or beside it -- a
    session still running in this process, a second window on one session id --
    and laying the log's board over part of it would make a board neither
    writer held. The whole restore is reported as refused
    (``Restore.refused``), with what differs.

    **The same board twice changes nothing**, which follows from the two rules
    above and from the report stating what the store holds and not what one
    application did: the second time everything is found as logged and the
    report is the one already there. Times are left out of "as logged": they are
    rebuilt from a clock reading, and a second reading of the same log is not a
    different board.

    **Nothing arrives that the other arms would not have produced.** The board
    is a value any caller can build, so each task is put through what
    ``TaskDeclared`` and the arms after it enforce (``_restored_task``), in the
    log's order so that a dependency cycle is met where a declaration would
    have met it (``_as_declared`` says which cycles that is).
    """
    session_id, board, clock = intent.session_id, intent.board, intent.clock
    prior = snap.restores.get(session_id)
    # A name the log holds that no store wrote is no name: the party is placed as
    # one the board had no name for.
    known = {
        node: address
        for node, address in board.addresses
        if not drawable.undrawable((node, address))
    }
    parties: dict[NodeId, str] = {}
    # Strictly before the reading, so "before the restore" is a comparison with
    # one instant: a record the wall clock puts in the future is clamped to the
    # reading itself, and what the session does next is stamped at or after it.
    latest = math.nextafter(clock.monotonic, -math.inf)

    def node_of(party: Party) -> NodeId:
        if isinstance(party, str):
            if party in ROOT_NAMES:
                return (session_id, None)
            node, address = restored_party(session_id, party), party
            while node in snap.nodes:
                node = (node[0], f"{node[1]}/")
        else:
            node, address = party, known.get(party, "")
        if node[1] is not None and node[0] == session_id:
            parties.setdefault(node, address)
        return node

    def at_of(wall: float) -> float:
        return min(clock.monotonic_at(wall), latest)

    tasks = dict(snap.tasks)
    concerns = dict(snap.concerns)
    unplaced: list[NotPlaced] = []
    differing: list[NotPlaced] = []
    new_tasks: list[Task] = []
    new_concerns: list[Concern] = []
    held = [0, 0, 0]

    logged: set[str] = set()
    for restored_task in board.tasks:
        if drawable.undrawable(restored_task):
            logged.add(restored_task.id)
            unplaced.append(NotPlaced("task", _said_id(restored_task.id), Unplaced.UNDRAWABLE))
            continue
        if restored_task.id in logged:
            unplaced.append(NotPlaced("task", restored_task.id, Unplaced.REPEATED))
            continue
        logged.add(restored_task.id)
        task = _restored_task(restored_task, session_id, node_of, at_of)
        standing = snap.tasks.get(restored_task.id)
        if isinstance(task, Unplaced):
            unplaced.append(NotPlaced("task", restored_task.id, task))
        elif standing is None:
            if _would_cycle(tasks, _as_declared(tasks, task)):
                unplaced.append(NotPlaced("task", task.id, Unplaced.WOULD_CYCLE))
            else:
                tasks[task.id] = task
                new_tasks.append(task)
        elif not standing.belongs_to(session_id):
            unplaced.append(
                NotPlaced(
                    "task",
                    task.id,
                    Unplaced.OTHER_SESSION_HAS_ID,
                    holder=None if standing.declared_by is None else standing.declared_by[0],
                )
            )
        elif _timeless_task(standing) == _timeless_task(task):
            held[0] += 1
        else:
            differing.append(NotPlaced("task", task.id, Unplaced.STORE_DIFFERS))
    differing += [
        NotPlaced("task", t.id, Unplaced.STORE_ONLY)
        for t in snap.tasks.values()
        if t.belongs_to(session_id) and t.id not in logged
    ]

    filed: set[str] = set()
    for restored_concern in board.concerns:
        if drawable.undrawable(restored_concern):
            filed.add(restored_concern.id)
            unplaced.append(
                NotPlaced("concern", _said_id(restored_concern.id), Unplaced.UNDRAWABLE)
            )
            continue
        if restored_concern.id in filed:
            unplaced.append(NotPlaced("concern", restored_concern.id, Unplaced.REPEATED))
            continue
        filed.add(restored_concern.id)
        concern = _restored_concern(restored_concern, session_id, node_of, at_of, snap.channels)
        filed_already = snap.concerns.get(restored_concern.id)
        if isinstance(concern, Unplaced):
            unplaced.append(NotPlaced("concern", restored_concern.id, concern))
        elif filed_already is None:
            concerns[concern.id] = concern
            new_concerns.append(concern)
        elif filed_already.sender[0] != session_id:
            unplaced.append(
                NotPlaced(
                    "concern",
                    concern.id,
                    Unplaced.OTHER_SESSION_HAS_ID,
                    holder=filed_already.sender[0],
                )
            )
        elif _timeless_concern(filed_already) == _timeless_concern(concern):
            held[2 if concern.is_peer else 1] += 1
        else:
            differing.append(NotPlaced("concern", concern.id, Unplaced.STORE_DIFFERS))
    differing += [
        NotPlaced("concern", c.id, Unplaced.STORE_ONLY)
        for c in snap.concerns.values()
        if c.sender[0] == session_id and c.id not in filed
    ]

    decisions = dict(snap.decisions)
    new_decisions: list[Decision] = []
    raised: set[str] = set()
    held_decisions = 0
    for restored_decision in board.decisions:
        # The operator's answer is held verbatim (``_VERBATIM``) and may hold
        # what no other logged text does; every other text is the store's
        # drawable form.
        if drawable.undrawable(dataclasses.replace(restored_decision, outcome=None)):
            raised.add(restored_decision.id)
            unplaced.append(
                NotPlaced("decision", _said_id(restored_decision.id), Unplaced.UNDRAWABLE)
            )
            continue
        if restored_decision.id in raised:
            unplaced.append(NotPlaced("decision", restored_decision.id, Unplaced.REPEATED))
            continue
        raised.add(restored_decision.id)
        decision = _restored_decision(restored_decision, session_id, node_of, at_of, snap.channels)
        raised_already = snap.decisions.get(restored_decision.id)
        if isinstance(decision, Unplaced):
            unplaced.append(NotPlaced("decision", restored_decision.id, decision))
        elif raised_already is None:
            decisions[decision.id] = decision
            new_decisions.append(decision)
        elif raised_already.session_id != session_id:
            unplaced.append(
                NotPlaced(
                    "decision",
                    decision.id,
                    Unplaced.OTHER_SESSION_HAS_ID,
                    holder=raised_already.session_id,
                )
            )
        elif _timeless_decision(raised_already) == _timeless_decision(decision):
            held_decisions += 1
        else:
            differing.append(NotPlaced("decision", decision.id, Unplaced.STORE_DIFFERS))
    differing += [
        NotPlaced("decision", d.id, Unplaced.STORE_ONLY)
        for d in snap.decisions.values()
        if d.session_id == session_id and d.id not in raised
    ]

    report = Restore(
        session_id=session_id,
        log_short=board.short,
        log_damage=_damage_text(board),
        log_stray=len(board.stray),
        log_unrecognised=len(board.unrecognised),
        log_contested=len(board.contested),
        log_stopped=len(board.stopped),
    )
    if differing:
        withheld = [NotPlaced("task", t.id, Unplaced.WITHHELD) for t in new_tasks]
        withheld += [NotPlaced("concern", c.id, Unplaced.WITHHELD) for c in new_concerns]
        withheld += [NotPlaced("decision", d.id, Unplaced.WITHHELD) for d in new_decisions]
        report = dataclasses.replace(
            report,
            at=None if prior is None else prior.at,
            tasks=held[0],
            concerns=held[1],
            peer_messages=held[2],
            decisions=held_decisions,
            unplaced=(*differing, *withheld, *unplaced),
            parties=MappingProxyType({}) if prior is None else prior.parties,
        )
        tasks, concerns, decisions = dict(snap.tasks), dict(snap.concerns), dict(snap.decisions)
    else:
        fresh = (
            bool(new_tasks or new_concerns or new_decisions) or prior is None or prior.at is None
        )
        report = dataclasses.replace(
            report,
            # The reading the newest of what is held was converted with. A later
            # reading that placed nothing leaves the earlier instant standing:
            # moving it would put whatever the session did in between on the far
            # side of "before the restore".
            at=clock.monotonic if fresh or prior is None else prior.at,
            tasks=held[0] + len(new_tasks),
            concerns=held[1] + sum(1 for c in new_concerns if not c.is_peer),
            peer_messages=held[2] + sum(1 for c in new_concerns if c.is_peer),
            decisions=held_decisions + len(new_decisions),
            unplaced=tuple(unplaced),
            parties=MappingProxyType({**({} if prior is None else prior.parties), **parties}),
        )
    if report == prior:
        return None
    return (
        tasks,
        concerns,
        snap.decisions if decisions == snap.decisions else MappingProxyType(decisions),
        MappingProxyType({**snap.restores, session_id: report}),
    )


def _as_declared(tasks: dict[TaskId, Task], task: Task) -> Task:
    """
    ``task`` without the dependencies the store may have added itself.

    A logged task holds the dependencies its declarer named and the ones added
    for overlapping files in one tuple, and nothing in the record says which is
    which. A declaration is turned away for a cycle among the first kind only.
    So a dependency the store could have added -- on an unfinished task of the
    same board that writes a file this one writes -- is left out of the check,
    and a restore turns away only a cycle a declaration would certainly have
    been turned away for.

    The overlap is taken whole, without the ``_already_behind`` filter the
    declaration arm applies: this reducer adds no edge that closes a cycle, but
    a log is whatever was written to it, and a pair waiting on each other
    through a shared file is restored as it was logged and not dropped.
    """
    added = set(_auto_depends(tasks, dataclasses.replace(task, depends_on=())))
    return dataclasses.replace(
        task, depends_on=tuple(dep for dep in task.depends_on if dep not in added)
    )


def _said_id(logged: str) -> str:
    """
    The id of a logged task, concern or decision that is not placed for holding what
    no store writes, as its report names it: drawable, since the id may be what held
    it.
    """
    return drawable.text(logged) if drawable.undrawable(logged) else logged


def _damage_text(board: Board) -> str | None:
    """``Board.damage`` as the one line a reader is shown, or None for no damage."""
    damage = board.damage
    if damage is None:
        return None
    behind = f", {damage.unread} record(s) behind it unread" if damage.unread else ""
    # Said by the log's reader, of a file: drawn, and made drawable here because a
    # board restore is the one intent ``_drawable`` leaves whole.
    return drawable.text(f"unreadable from record {damage.ordinal}: {damage.reason}{behind}")


def _stated(text: str | None) -> Basis | None:
    """
    A logged basis as the member it names. One this build does not define reads
    as unstated: a log keeps the text so a newer build's member does not end it,
    and "unstated" is the one answer that puts no claim in the author's mouth.
    """
    try:
        return Basis.stated(text)
    except ValueError:
        return None


def _restored_task(
    logged: RestoredTask,
    session_id: str,
    node_of: Callable[[Party], NodeId],
    at_of: Callable[[float], float],
) -> Task | Unplaced:
    """
    ``logged`` as the task the reducer's own arms would have left, or why there
    is no such task.

    What a declaration normalises is normalised again -- touches, the property
    on one line -- and what only one arm writes is kept only in the state that
    arm leaves: a holder and the version it was handed on a task somebody has, a
    completion's time, result, basis and unrun list on a finished one. A board
    that says more than that is not corrected into something plausible; the task
    is left out as ``INCOHERENT`` wherever no reading of it is a state the store
    reaches:

    - held or finished with no holder, or by a holder that never claimed it;
    - held, given back or amended by an agent of another session, which no
      claim reaches (``_pick_claim``);
    - finished at no time;
    - handed a version it has not had;
    - amended by an agent that did not declare it, or that has held it
      (``_amender_refusal``, and ``_claim_refusal`` from the other side);
    - saying more than its state has: a holder or a handed version on a task
      nobody holds, a completion's time, result, basis or unrun list on one that
      is not finished. Placing it without them would be a board the log does
      not describe, reported as placed whole;
    - a provenance with no channel -- a blank one, or a message answered or a
      charter marked with none -- or with a name the bus would have refused or
      trimmed: a channel that is not one drawable directory name, an ``re`` not
      shaped as a concern id, an empty one included (``stated_provenance``).

    A task the operator closed and one its declarer withdrew are the finished
    tasks with no holder (``TaskClosed``, ``TaskWithdrawn``), and each is placed
    only in the shape its arm leaves.

    A task whose declarer is another session's is not this board's to place.
    """
    if not logged.versions:
        return Unplaced.INCOHERENT
    declarer = node_of(logged.declared_by)
    if declarer[0] != session_id:
        return Unplaced.NOT_THIS_SESSIONS
    held_by = tuple(dict.fromkeys(node_of(party) for party in logged.held_by))
    released_by = tuple(dict.fromkeys(node_of(party) for party in logged.released_by))
    if any(node[0] != session_id for node in (*held_by, *released_by)):
        return Unplaced.INCOHERENT
    finished = (logged.completed_at, logged.result_basis, logged.not_run) != (None, None, None)
    if logged.state is not TaskState.COMPLETED and (finished or logged.result):
        return Unplaced.INCOHERENT
    if logged.state is TaskState.PENDING and (
        logged.holder is not None or logged.claimed_version is not None
    ):
        return Unplaced.INCOHERENT
    provenance: Provenance | None = None
    if (logged.channel, logged.re, logged.inside_charter) != (None, None, None):
        # Read by the bus's own rule (``stated_provenance``), which no
        # declaration the bus took fails and which is what the names are taken
        # to have passed: a channel that is one drawable directory name, an re
        # with a concern id's shape. The mark is read as an unknown basis is
        # (``records.logged_charter``), so it is not given to the rule.
        try:
            checked = stated_provenance(logged.channel, logged.re, None)
        except ValueError:
            return Unplaced.INCOHERENT
        # None for a blank channel, or a message answered or a charter marked on
        # no channel; a different value for a name the bus would have trimmed,
        # or an empty message id. The bus logs none of those.
        if checked is None or (checked.channel, checked.re) != (logged.channel, logged.re):
            return Unplaced.INCOHERENT
        provenance = dataclasses.replace(
            checked, inside_charter=logged_charter(logged.inside_charter)
        )

    history: list[TaskVersion] = []
    for prior, following in itertools.pairwise(logged.versions):
        amender = None if following.by is None else node_of(following.by)
        if amender is not None and (amender != declarer or amender in held_by):
            return Unplaced.INCOHERENT
        history.append(
            TaskVersion(
                detail=prior.detail,
                property=normalised_property(prior.property),
                basis=_stated(prior.basis),
                replaced_at=at_of(following.at),
                replaced_by=amender,
            )
        )

    task = Task(
        id=logged.id,
        title=logged.title,
        detail=logged.detail,
        depends_on=logged.depends_on,
        touches=normalised_touches(logged.touches),
        declared_at=at_of(logged.declared_at),
        declared_by=declarer,
        released_by=released_by,
        writes=logged.writes,
        basis=_stated(logged.basis),
        property=normalised_property(logged.property),
        released_at=None if logged.released_at is None else at_of(logged.released_at),
        superseded=tuple(history),
        held_by=held_by,
        provenance=provenance,
        after_all_writers=logged.after_all_writers,
    )
    if (logged.withdrawn_by_lead or logged.withdrawal_edited) and not logged.withdrawn:
        # Facts about a withdrawal, on a task nobody withdrew.
        return Unplaced.INCOHERENT
    if logged.closed_by_operator or logged.withdrawn:
        # The close arm's shape and no other, which is the withdrawal arm's too:
        # finished at a time, nobody's, and with nothing a claimer states beside
        # the result the arm composed.
        if (
            logged.closed_by_operator == logged.withdrawn
            or logged.state is not TaskState.COMPLETED
            or logged.completed_at is None
            or logged.holder is not None
            or logged.claimed_version is not None
            or (logged.result_basis, logged.not_run) != (None, None)
        ):
            return Unplaced.INCOHERENT
        return dataclasses.replace(
            task,
            state=TaskState.COMPLETED,
            completed_at=at_of(logged.completed_at),
            result=logged.result,
            closed_by_operator=logged.closed_by_operator,
            withdrawn=logged.withdrawn,
            withdrawn_by_lead=logged.withdrawn_by_lead,
            withdrawal_edited=logged.withdrawal_edited,
        )
    if logged.state is TaskState.PENDING:
        return task

    holder = None if logged.holder is None else node_of(logged.holder)
    handed = logged.claimed_version
    if holder is None or holder not in held_by:
        return Unplaced.INCOHERENT
    if handed is not None and not 1 <= handed <= task.version:
        return Unplaced.INCOHERENT
    task = dataclasses.replace(
        task, state=TaskState.CLAIMED, claimed_by=holder, claimed_version=handed
    )
    if logged.state is TaskState.CLAIMED:
        return task

    if logged.completed_at is None:
        return Unplaced.INCOHERENT
    # The completion arm's rules for a blank result and what stands beside one.
    result = logged.result if logged.result.strip() else ""
    listed = logged.not_run is not None and bool(logged.not_run.strip())
    return dataclasses.replace(
        task,
        state=TaskState.COMPLETED,
        completed_at=at_of(logged.completed_at),
        result=result,
        result_basis=_stated(logged.result_basis) if result else None,
        not_run=logged.not_run if listed or (logged.not_run == "" and result) else None,
    )


def _restored_concern(
    logged: RestoredConcern,
    session_id: str,
    node_of: Callable[[Party], NodeId],
    at_of: Callable[[float], float],
    channels: Mapping[ChannelId, Channel],
) -> Concern | Unplaced:
    """
    ``logged`` as the concern the store files, or why it is not this board's.

    Nothing about its state is refused. ``ConcernPosted`` files the record it is
    handed, already delivered or withdrawn included, so there is no concern a
    board can describe that the store could not have held.

    A recipient in another session is placed like any other, because the store
    holds such a concern when it is handed one: ``ConcernPosted`` does not ask
    whose the recipient is, and a restore that left it out would be a board
    short of something the reducer filed.

    **A peer message's fields are asked again by the rules ``send_to_peer``
    put them through**: a channel that is a name, an ``re`` in the concern-id
    shape, and, where ``channels.log`` holds its link, the link's own name. The
    read prints the channel inside its own words and the ``re`` beside it, so a
    record that fails one did not come from that tool, and is not placed.
    """
    sender = node_of(logged.sender)
    if sender[0] != session_id:
        return Unplaced.NOT_THIS_SESSIONS
    if logged.channel is None and (
        logged.needs_answer or logged.re is not None or logged.quiet or logged.link is not None
    ):
        # ``Concern`` refuses to be built so; a log's fold never produces it.
        return Unplaced.INCOHERENT
    if logged.channel is not None and not logged.link:
        # Nor this: ``PeerSent`` names the link it routed over, and a message
        # without one could be handed over by no link, or by whichever link
        # later took its name.
        return Unplaced.INCOHERENT
    if logged.channel is not None and (
        isinstance(logged.recipient, str) or not peer_addressed(sender, logged.recipient)
    ):
        # No step files any other (``model.peer_addressed``). A recipient named
        # by address alone would resolve inside this session -- "lead" to this
        # session's own lead -- and hand a peer's message to the wrong team.
        return Unplaced.INCOHERENT
    if logged.channel is not None and not _peer_fields_hold(
        logged.channel, logged.link, logged.re, channels
    ):
        return Unplaced.INCOHERENT
    return Concern(
        id=logged.id,
        sender=sender,
        recipient=node_of(logged.recipient),
        subject=logged.subject,
        body=logged.body,
        posted_at=at_of(logged.posted_at),
        state=logged.state,
        edited=logged.edited,
        delivered_at=None if logged.delivered_at is None else at_of(logged.delivered_at),
        task_id=logged.task_id,
        basis=_stated(logged.basis),
        incident=logged.incident,
        channel=logged.channel,
        needs_answer=logged.needs_answer,
        re=logged.re,
        quiet=logged.quiet,
        link=logged.link,
    )


def _restored_decision(
    logged: RestoredDecision,
    session_id: str,
    node_of: Callable[[Party], NodeId],
    at_of: Callable[[float], float],
    channels: Mapping[ChannelId, Channel],
) -> Decision | Unplaced:
    """
    ``logged`` as the decision the store holds, or why it is not this board's.

    The raise arm's limits on how many may be open are not asked: they bound
    what one agent can put in front of the operator at once, and a decision the
    store filed was within them when it was filed. Neither is ``blocks``
    checked against the board, since a task it names may be one this restore
    could not place, and leaving the decision out for that would lose the
    operator's answer over a link.

    What only one arm writes is refused where no arm writes it: an answer longer
    than ``DecisionAnswered`` takes, and a first delivery of a decision that was
    neither answered nor dismissed. A standing this build does not define has
    no member to restore as.

    A decision on a channel is a lead's (``DecisionRefusal.NOT_A_LEAD``), so one
    whose asker is a sub-agent is refused, and its channel must be its link's
    name where the store holds the link (``records.channel_as_linked``).
    """
    asker = node_of(logged.asker)
    if asker[0] != session_id:
        return Unplaced.NOT_THIS_SESSIONS
    try:
        standing = Standing(logged.standing)
    except ValueError:
        return Unplaced.INCOHERENT
    outcome: DecisionOutcome | None
    match logged.outcome:
        case None:
            outcome = None
        case Answered():
            if len(logged.outcome.text) > MAX_ANSWER_CHARS:
                return Unplaced.INCOHERENT
            outcome = Answered(text=logged.outcome.text, at=at_of(logged.outcome.at))
        case Dismissed():
            outcome = Dismissed(at=at_of(logged.outcome.at))
        case Withdrawn():
            outcome = Withdrawn(at=at_of(logged.outcome.at))
        case _:
            assert_never(logged.outcome)
    delivered = logged.first_delivered_at
    if delivered is not None and not isinstance(outcome, Answered | Dismissed):
        return Unplaced.INCOHERENT
    # The raise arm sets a channel exactly with the link it named, and a reply
    # draws the name inside its own words, so a record saying otherwise did not
    # come from that arm.
    if (not logged.link) != (logged.channel is None):
        return Unplaced.INCOHERENT
    if logged.channel is not None and (
        asker[1] is not None or not channel_as_linked(logged.channel, logged.link, channels)
    ):
        return Unplaced.INCOHERENT
    return Decision(
        id=logged.id,
        asker=asker,
        question=logged.question,
        blocks=logged.blocks,
        if_unanswered=logged.if_unanswered,
        evidence=logged.evidence,
        recommendation=logged.recommendation,
        standing=standing,
        reversible=logged.reversible,
        raised_at=at_of(logged.raised_at),
        outcome=outcome,
        first_delivered_at=None if delivered is None else at_of(delivered),
        channel=logged.channel,
        link=logged.link,
    )


def _timeless_decision(decision: Decision) -> Decision:
    """
    ``decision`` with every time flattened, keeping which of them it has.
    """
    outcome = decision.outcome
    return dataclasses.replace(
        decision,
        raised_at=0.0,
        outcome=None if outcome is None else dataclasses.replace(outcome, at=0.0),
        first_delivered_at=None if decision.first_delivered_at is None else 0.0,
    )


def _timeless_task(task: Task) -> Task:
    """``task`` with every time flattened, keeping which of them it has."""

    def flat(at: float | None) -> float | None:
        return None if at is None else 0.0

    return dataclasses.replace(
        task,
        declared_at=0.0,
        completed_at=flat(task.completed_at),
        released_at=flat(task.released_at),
        superseded=tuple(dataclasses.replace(prior, replaced_at=0.0) for prior in task.superseded),
    )


def _timeless_concern(concern: Concern) -> Concern:
    """
    ``concern`` as a log holds it, for telling whether the store holds a
    restored one already: without its times, and without what a log never
    keeps -- a withdrawal's reason and whether it was told -- so a log read
    again in the run that wrote it is the board the store holds.
    """
    return dataclasses.replace(
        concern,
        posted_at=0.0,
        delivered_at=None if concern.delivered_at is None else 0.0,
        withdrawn_reason=None,
        withdrawal_told=False,
    )
