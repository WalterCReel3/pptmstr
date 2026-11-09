"""
Waking a lead for its peer mail (Decided 10): ``Snapshot.peer_wakes``, the
wakes a step orders, and the obligations a link owes the operator.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from types import MappingProxyType

from ..approval import peer_channel
from ..channels.records import WITHDRAW_PEER_TOOL, Channel, ChannelId
from ..effects import Effect, SessionWoken, WakeReason
from ..intents import (
    ApprovalResolved,
    BoardRestored,
    ConcernEdited,
    ConcernWithdrawn,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionWakeSent,
    Intent,
    PeerInboxRead,
    PeerSent,
    PeerWakeSent,
    SessionSettled,
    WakeChainReleased,
)
from ..model import (
    PEER_WAKE_CHAIN,
    Answered,
    Concern,
    Decision,
    DecisionId,
    Dismissed,
    Obligation,
    PeerMailHeld,
    PeerQuestionUnanswered,
    PeerWakes,
    Snapshot,
    WakeChainHeld,
    why_held,
)
from .peers import _read_covers


def _peer_waking(
    before: Snapshot, intent: Intent, world: Snapshot, now: float
) -> tuple[Snapshot, tuple[Effect, ...]]:
    """
    ``world`` with what ``intent`` did to waking (``Snapshot.peer_wakes``), and
    the wakes it orders. **Delivery first**: a message that is not ``quiet``
    wakes the lead it reaches, so no message waits on a lead remembering to
    look (Decided 10).

    - **What wakes.** A message filed by ``PeerSent`` that is not quiet wakes
      its recipient's session. So does mail already waiting, at a session's
      ``SessionSettled`` (its restore is in the store) and at a sender's
      ``BoardRestored`` (its restore brought mail back for a running peer).
    - **A decision raised on a channel wakes the other members' leads**
      through the same per-link wake (``PeerWakes.untold``), whose line names
      it by id beside the unread count: an agent's act like a message, so it
      is coalesced with the link's mail, counted toward the chain and held at
      its bound. The decisions still open on its links are named again to a
      lead at its ``SessionSettled``, and to its peers at an asker's
      ``BoardRestored``. The operator's answer is not this: it wakes every
      member apart from this, uncounted (``_decided``).
    - **An answer a lead was down for is named at its resume.** A channel
      decision the other lead raised, answered or dismissed while this lead's
      session could not be woken, is named to it at its ``SessionSettled``
      with a wake of the answer's own reason, uncounted. A lead is marked as
      told (``PeerWakes.heard``) when the shell sends it a wake naming one
      (``DecisionWakeSent``), whether that was the operator's answer or a
      settle, so each is named once a run; a wake the shell dropped, its
      session failing or closed first, marks nothing. An asker's
      ``BoardRestored`` that brings back ones it raised names them to its
      running peers in the same way. Nothing is logged, so
      after a restart every ended decision on a live link is named again at
      the lead's first resume: repetition bounded by resumes, taken over
      growing the log with a read receipt. How many one wake names is bounded
      where its text is written (``waketext.WAKE_DECISIONS_NAMED``).
    - **Coalesced per link, between the order and the send.** A session
      pending on a link -- woken, and the shell not yet done with that wake --
      is not woken again on it: the wake line counts what is waiting when the
      shell sends it (``waketext.wake_line``). The shell's ``PeerWakeSent`` ends
      it, and so does a ``PeerInboxRead`` by its lead for the links the read
      covered and that owe it no decision, or the link having nothing left
      for it to read (a withdrawal).
      So a lead woken that ends its turn without reading is woken again by the
      next message: a forgotten read costs a turn, never a stalled message.
    - **Who can be woken** is ``_wakeable``'s: what a session that is not
      there to be woken was owed is forgotten, and asked again at its next
      ``SessionSettled``. Its mail stays waiting in the store.
    - **A resume clears what its lead was owed on every link and wakes it.**
      At ``SessionSettled`` the session is let go of (``PeerWakes.without``):
      pending, owed decisions, and a held chain's row too. Its wake is then
      asked again uncounted, whatever the link's chain stands at. The chain
      itself is left as it is. Resuming a session is taken as an operator
      act, so a held lead's row ends with it, though the act is on the
      session and not the link.
    - **A question the lead read and has not answered is named at its
      settle.** A lead that owes an answer on a live link
      (``Snapshot.owed_answers``) is woken on it at its ``SessionSettled``,
      uncounted, with nothing else waiting, and the wake's line names those
      questions (``PeerWakes.owing``). So is a running lead at an asker's
      ``BoardRestored`` that brings back a question it owes: the question
      and its delivery are in the asker's log, so after a restart that
      resumes the recipient first, its own settle finds nothing owed. So is
      a lead whose held wake on the link the operator's act on it wakes
      (``_moved``): the operator is looking at that link. Only these ask
      it: the shell's ``PeerWakeSent`` lets it go and asks nothing again for
      it, so a lead that ends its turn without answering is not woken for
      them before its next settle, such a restore or such an act.
    - **The backstop.** Each wake an agent's message brings about counts
      toward its link's chain; at ``PEER_WAKE_CHAIN`` the next is held, and
      owed to the operator as a ``WakeChainHeld`` row. A wake the system
      orders on its own -- at a ``SessionSettled`` or a sender's
      ``BoardRestored`` -- is not a turn of the exchange, and is neither
      counted nor held. The operator acting on the link (``_moved``) starts
      the chain again and wakes what was held at once.

    A wake is only ever ordered here for a session with a message on the link
    that is not quiet and that the link would hand over now, a decision on
    it still owed, or, at its settle, an asker's restore or the operator's
    release of its held wake, a question on it owed an answer. Quiet mail is
    read with the next wake or read, and wakes nobody.
    """
    wakes = {
        lid: kept
        for lid, held in world.peer_wakes.items()
        if (kept := _still_owed(world, lid, held)) is not None
    }
    # Each ask is a session, its link, whether an agent's message brought it
    # about (and so counts toward the chain and can be held), and whether it
    # names the questions the lead owes an answer to (``PeerWakes.owing``).
    asked: list[tuple[str, Channel, bool, bool]] = []
    woken: list[Effect] = []
    match intent:
        case PeerSent():
            filed = world.concerns.get(intent.concern_id)
            if (
                intent.concern_id not in before.concerns
                and filed is not None
                and not filed.quiet
                and filed.link is not None
                and filed.link in world.channels
            ):
                asked.append((filed.recipient[0], world.channels[filed.link], True, False))
        case DecisionRaised():
            raised = world.decisions.get(intent.decision.id)
            if intent.decision.id not in before.decisions and raised is not None:
                asked.extend(
                    (sid, link, True, False) for sid, link in _to_tell(world, wakes, (raised,))
                )
        case PeerInboxRead():
            if intent.node_id[1] is None:
                reader = intent.node_id[0]
                for link in _read_covers(world, reader, intent.channel):
                    # A read of the link's mail is not a read of the decisions
                    # raised on it: a wake still owed for one stays as it is,
                    # though no longer for the questions the read listed again.
                    if link.id not in wakes:
                        continue
                    if wakes[link.id].untold_to(reader):
                        wakes[link.id] = dataclasses.replace(
                            wakes[link.id], owing=wakes[link.id].owing - {reader}
                        )
                    else:
                        wakes[link.id] = wakes[link.id].without(reader)
        case PeerWakeSent():
            spent_on = world.channels.get(intent.link_id)
            if spent_on is not None:
                if spent_on.id in wakes:
                    spent = wakes[spent_on.id]
                    wakes[spent_on.id] = dataclasses.replace(
                        spent,
                        pending=spent.pending - {intent.session_id},
                        owing=spent.owing - {intent.session_id},
                        untold=tuple(
                            (sid, did)
                            for sid, did in spent.untold
                            if sid != intent.session_id or did not in intent.told
                        ),
                    )
                if any(
                    c.id not in intent.covered and not c.quiet
                    for c in world.peer_unread(intent.session_id, spent_on)
                ) or wakes.get(spent_on.id, PeerWakes()).untold_to(intent.session_id):
                    asked.append((intent.session_id, spent_on, True, False))
        case SessionSettled():
            # A resume clears a held chain's row and wakes the lead (the
            # docstring's "A resume" item). The decisions still open on its
            # links are named to it again: nothing records what an earlier run
            # was told or read. So are the questions it was handed and owes an
            # answer to, once: its own log holds any reply it sent in an earlier
            # run, restored ahead of this.
            for link in world.channels_of(intent.session_id):
                if link.id in wakes:
                    wakes[link.id] = wakes[link.id].without(intent.session_id)
                owes = bool(world.owed_answers(intent.session_id, link))
                asked.append((intent.session_id, link, False, owes))
            _to_tell(
                world,
                wakes,
                tuple(d for d in world.decisions.values() if d.is_open),
                only=intent.session_id,
            )
            woken.extend(_unheard(world, wakes, intent.session_id))
        case DecisionWakeSent():
            _heard(world, wakes, intent.session_id, intent.decisions)
        case BoardRestored():
            restored = frozenset(world.decisions) - frozenset(before.decisions)
            for link in world.channels_of(intent.session_id):
                for peer in sorted(link.members - {intent.session_id}):
                    # The asker's log holds a question and its delivery, so a
                    # restore can bring back one a running lead owes.
                    #
                    # By the shell's order, nothing makes ``peer`` pending on
                    # this link before the asker's restore: only the asker's
                    # log or its live acts can, and a live act comes after its
                    # restore (``app._open_board_log``). So a question brought
                    # back is woken for below and never coalesced into a wake
                    # already pending, which would not name it.
                    owes = bool(
                        frozenset(c.id for c in world.owed_answers(peer, link))
                        - frozenset(c.id for c in before.owed_answers(peer, link))
                    )
                    asked.append((peer, link, False, owes))
                    woken.extend(_unheard(world, wakes, peer, among=restored))
            _to_tell(
                world,
                wakes,
                tuple(
                    d
                    for did, d in world.decisions.items()
                    if did not in before.decisions and d.is_open
                ),
            )
        case _:
            pass
    for lid in _moved(before, intent, world, wakes):
        moved = wakes[lid]
        wakes[lid] = dataclasses.replace(moved, chain=0, held=())
        # The operator is looking at this link, so a held lead's wake names
        # what it owes there too. Counted, and the chain is 0 again, so it is
        # not held a second time.
        on = world.channels[lid]
        asked.extend((sid, on, True, bool(world.owed_answers(sid, on))) for sid, _ in moved.held)
    for session_id, link, counted, owes in asked:
        owed = wakes.get(link.id, PeerWakes())
        if (
            session_id in owed.pending
            or owed.is_held(session_id)
            or not _wakeable(world, session_id)
            or (
                all(c.quiet for c in world.peer_unread(session_id, link))
                and not owed.untold_to(session_id)
                and not owes
            )
        ):
            continue
        if counted and owed.chain >= PEER_WAKE_CHAIN:
            wakes[link.id] = dataclasses.replace(owed, held=(*owed.held, (session_id, now)))
            continue
        wakes[link.id] = dataclasses.replace(
            owed,
            chain=owed.chain + counted,
            pending=owed.pending | {session_id},
            owing=owed.owing | {session_id} if owes else owed.owing,
        )
        woken.append(
            SessionWoken(session_id=session_id, reason=WakeReason.PEER_MESSAGE, subject_id=link.id)
        )
    kept_wakes = {lid: w for lid, w in wakes.items() if w != PeerWakes()}
    if kept_wakes == dict(world.peer_wakes):
        return world, tuple(woken)
    return dataclasses.replace(world, peer_wakes=MappingProxyType(kept_wakes)), tuple(woken)


def _to_tell(
    world: Snapshot,
    wakes: dict[ChannelId, PeerWakes],
    decisions: Iterable[Decision],
    only: str | None = None,
) -> list[tuple[str, Channel]]:
    """
    Put each of ``decisions`` raised on a link in ``wakes`` as owed to every
    member's lead across the link that reads it and can be woken
    (``PeerWakes.untold``), or to ``only``'s alone, and return each session and
    link a wake is now owed on. A decision already owed is not owed twice.
    """
    owing: list[tuple[str, Channel]] = []
    for decision in sorted(decisions, key=lambda d: (d.raised_at, d.id)):
        link = None if decision.link is None else world.channels.get(decision.link)
        if link is None or not link.live:
            continue
        for member in sorted(link.members - {decision.session_id}):
            if only is not None and member != only:
                continue
            if not world.channel_decision_readable(decision, member):
                continue
            if not _wakeable(world, member):
                continue
            owed = wakes.get(link.id, PeerWakes())
            if (member, decision.id) in owed.untold:
                continue
            wakes[link.id] = dataclasses.replace(owed, untold=(*owed.untold, (member, decision.id)))
            owing.append((member, link))
    return owing


def _heard(
    world: Snapshot,
    wakes: dict[ChannelId, PeerWakes],
    session_id: str,
    named: AbstractSet[DecisionId],
) -> None:
    """
    Mark each of ``named`` that is a channel decision the lead across a live
    link raised, and that has ended, as told to ``session_id``'s lead
    (``PeerWakes.heard``): the shell sent it a wake naming them
    (``intents.DecisionWakeSent``).

    Marked at the send and not at the order, as ``PeerWakes.untold`` is let
    go of: the shell holds a wake for its window, and longer for a session
    still starting, and drops it if the session fails or is closed meanwhile.
    A decision whose wake was dropped stays unmarked, and ``_unheard`` names
    it at the session's next ``SessionSettled``.
    """
    for did in sorted(named):
        decision = world.decisions.get(did)
        if decision is None or decision.is_open or decision.session_id == session_id:
            continue
        link = None if decision.link is None else world.channels.get(decision.link)
        if (
            link is None
            or not link.live
            or not world.channel_decision_readable(decision, session_id)
        ):
            continue
        owed = wakes.get(link.id, PeerWakes())
        wakes[link.id] = dataclasses.replace(owed, heard=owed.heard | {(session_id, did)})


def _unheard(
    world: Snapshot,
    wakes: dict[ChannelId, PeerWakes],
    session_id: str,
    among: AbstractSet[DecisionId] | None = None,
) -> list[SessionWoken]:
    """
    A wake for each channel decision on ``session_id``'s live links that the
    lead across the link raised and the operator answered or dismissed, and
    that ``session_id``'s lead has not been told of in this run, oldest
    ending first. Uncounted, like the open decisions a settle names: the
    operator's act, read at a resume or a restore. ``among`` limits it to
    those decisions: an asker's restore names only what it brought back.

    Naming one does not mark it told: the shell does, once it sends the wake
    (``intents.DecisionWakeSent``, ``_heard``). A second settle or restore
    before then names it again, and the shell sends a session's held wakes
    as one message that names each decision once (``waketext.wake_message``).

    A withdrawn decision is not named: its asker took it back, and nothing
    was decided for the other lead to act on. How many one wake names is
    ``waketext.wake_message``'s to bound.
    """
    if not _wakeable(world, session_id):
        return []
    ended: list[tuple[float, Decision]] = []
    for link in world.channels_of(session_id):
        owed = wakes.get(link.id, PeerWakes())
        for decision in world.decisions.values():
            match decision.outcome:
                case Answered(at=at) | Dismissed(at=at):
                    pass
                case _:
                    continue
            if (
                decision.link == link.id
                and (among is None or decision.id in among)
                and decision.session_id != session_id
                and (session_id, decision.id) not in owed.heard
                and world.channel_decision_readable(decision, session_id)
            ):
                ended.append((at, decision))
    named: list[SessionWoken] = []
    for _, decision in sorted(ended, key=lambda e: (e[0], e[1].id)):
        reason = (
            WakeReason.DECISION_ANSWERED
            if isinstance(decision.outcome, Answered)
            else WakeReason.DECISION_DISMISSED
        )
        named.append(SessionWoken(session_id=session_id, reason=reason, subject_id=decision.id))
    return named


def _still_owed(world: Snapshot, link_id: ChannelId, owed: PeerWakes) -> PeerWakes | None:
    """
    ``owed`` with every session that can no longer be woken let go of, and
    every pending session with nothing left on the link to wake it for no
    longer pending, or None when its link is no longer live: a closed link
    hands nothing over, so it has nothing to wake anybody for.

    Nothing left to wake it for is a withdrawal of the last message that is
    not quiet, which is the last the wake was for: quiet mail still unread
    wakes nobody. The shell drops that wake unsent (``app._still_to_read``),
    and a session left pending for it would have every later message on the
    link coalesced into a wake nobody sends.

    A decision owed (``PeerWakes.untold``) that has ended -- answered,
    dismissed or withdrawn -- or that its lead can no longer read is owed no
    longer: an answer or a dismissal orders a wake of its own (``_decided``),
    and a withdrawn question is nothing to wake a lead for.

    A settle's wake for questions owed (``PeerWakes.owing``) is owed no longer
    once its lead owes none of them: a message from its session answers each.
    """
    link = world.channels.get(link_id)
    if link is None or not link.live:
        return None
    still = tuple(
        (sid, did)
        for sid, did in owed.untold
        if (decided := world.decisions.get(did)) is not None
        and decided.is_open
        and world.channel_decision_readable(decided, sid)
    )
    if still != owed.untold:
        owed = dataclasses.replace(owed, untold=still)
    owing = frozenset(sid for sid in owed.owing if world.owed_answers(sid, link))
    if owing != owed.owing:
        owed = dataclasses.replace(owed, owing=owing)
    for session_id in sorted(
        owed.pending | {sid for sid, _ in owed.held} | {sid for sid, _ in owed.untold}
    ):
        if not _wakeable(world, session_id):
            owed = owed.without(session_id)
        elif (
            session_id in owed.pending
            and all(c.quiet for c in world.peer_unread(session_id, link))
            and not owed.untold_to(session_id)
            and session_id not in owed.owing
        ):
            owed = dataclasses.replace(owed, pending=owed.pending - {session_id})
    return owed


def _wakeable(world: Snapshot, session_id: str) -> bool:
    """
    Whether ``session_id`` has a run a wake can reach (``Snapshot.wakeable``).
    The same test the shell sends a wake under (``app._sendable``), asked here
    so that a wake the shell would drop is never left pending.
    """
    return world.wakeable(session_id)


def _moved(
    before: Snapshot, intent: Intent, world: Snapshot, wakes: Mapping[ChannelId, PeerWakes]
) -> tuple[ChannelId, ...]:
    """
    Every link among ``wakes`` that ``intent`` moved, which starts its chain
    again. **Only the operator acting on that link moves it**: an approval or
    a refusal of a parked call that reaches across it (``approval.peer_channel``,
    or for a withdrawal, which names no channel, the withdrawn message's link
    as ``Snapshot.parked_link`` finds it), an answer or a dismissal of a decision raised
    on it, an edit or a withdrawal of a message sent over it that changed the
    message, and a release of its held wakes (``WakeChainReleased``).

    - **Only the operator's.** A park the CLI aborted at its timeout, or one a
      teardown cancelled, settles with nobody having looked
      (``ApprovalResolved.from_operator``). An edit or a withdrawal counts
      only with no node on it, which is how the operator's is told from an
      agent's (as on ``TaskAmended``), and only when it changed the message's
      body or state: one of a message already handed over is not applied,
      and nobody saw anything new.
    - **Scoped to the link** and not to its members' sessions: the operator
      approving a builder's write in one of them has not seen what the two
      leads are saying to each other. What the operator types into a lead's
      session is not a fact of the store, and is not counted.

    Nothing an agent does moves it, because the backstop is there for two
    agents that keep each other busy, and anything they alone can produce
    they can produce every round. That includes a completed task naming the
    link in its provenance: a lead can declare, claim and complete one with no
    other agent and no operator involved. Nor do claims, declarations, or
    either team's other work, which would keep two leads talking past each
    other while their workers build from ever being surfaced. A refused call
    changes nothing, and is not progress either.
    """
    if not wakes:
        return ()
    match intent:
        case WakeChainReleased():
            released = wakes.get(intent.link_id)
            return (intent.link_id,) if released is not None and released.held else ()
        case ApprovalResolved():
            if not intent.from_operator:
                return ()
            rec = before.nodes.get(intent.node_id)
            parked = rec.pending_by_id(intent.pending_id) if rec is not None else None
            if parked is None:
                return ()
            if parked.tool_name == WITHDRAW_PEER_TOOL:
                # It names no channel: its link is the withdrawn message's.
                on = before.parked_link(parked)
                return (on,) if on is not None and on in wakes else ()
            name = peer_channel(parked.tool_name, parked.raw_args)
            return tuple(
                lid for lid in wakes if name is not None and world.channels[lid].name == name
            )
        case DecisionAnswered() | DecisionDismissed():
            decided = world.decisions.get(intent.decision_id)
            on = None if decided is None else decided.link
            return (on,) if on is not None and on in wakes else ()
        case ConcernEdited() | ConcernWithdrawn():
            if intent.node_id is not None:
                return ()
            was, message = before.concerns.get(intent.concern_id), world.concerns.get(
                intent.concern_id
            )
            if message is None or was is None:
                return ()
            if (was.body, was.state) == (message.body, message.state):
                return ()
            over = message.link
            return (over,) if over is not None and over in wakes else ()
        case _:
            return ()


def _owing_wake_chains(world: Snapshot) -> Snapshot:
    """
    ``world`` with a ``WakeChainHeld`` row in ``needs_you`` for every wake a
    link holds, at its place by age. Derived from ``peer_wakes`` on every step,
    as decisions are (``_owing_decisions``), so a row leaves exactly when its
    wake does.
    """
    owed = [
        WakeChainHeld(
            node=(session_id, None),
            since=since,
            summary=(
                f"#{world.channels[lid].name}: {PEER_WAKE_CHAIN} wakes in a row between "
                f"{_peer_pair(world, lid)} with no operator act on it - this "
                "lead's wake is held"
            ),
            link=lid,
            channel=world.channels[lid].name,
        )
        for lid, wakes in world.peer_wakes.items()
        for session_id, since in wakes.held
    ]
    if not owed:
        return world
    return dataclasses.replace(
        world, needs_you=tuple(sorted((*world.needs_you, *owed), key=lambda o: o.since))
    )


def _owing_peer_mail(world: Snapshot) -> Snapshot:
    """
    ``world`` with a ``PeerQuestionUnanswered`` row for every question still
    owed an answer that the shell said is past its age or whose recipient's
    session is not running (``_wakeable``), and for every question not yet
    read whose recipient's session is not running (Decided 4: read or not,
    a lead that is not running cannot answer); and a ``PeerMailHeld`` row
    for every lead and link with held peer mail the operator has not
    dismissed, each at its place by age. Derived on every step, as a held
    wake's row is, so a row leaves exactly when what raised it does.

    **The two kinds do not overlap.** A question row is for a question its
    own link hands over (``Snapshot.questions_unread``,
    ``Snapshot.awaiting_answer``), and a held-mail row for mail it does not
    (``Snapshot.peer_held``): an unread question on a link this run does not
    hold is counted in its held-mail row and raises no question row.

    **Mail on a link closed in its log raises no row.** A link in the store
    and not live has a close in its log, in this run or an earlier one:
    either the operator's (``ChannelClosed``) or the one the writer's sweep
    writes into every earlier log under a name when the operator links that
    name again (``channels.ChannelWriter._sweep``). The store cannot tell the
    two apart (``LinkClosed`` carries no provenance), so a swept link that was
    whole and live, one withheld at startup or another window's, goes silent
    too, though nobody closed it on purpose. Either way a closed link is never
    live again, so its mail can never be handed over and its row would return
    after every restart. The mail stays in the
    store and its sender's log, and ``read_peer_messages`` still reports it
    held. Mail on a link this run does not hold (withheld, or not restored)
    or on a live link that does not hand it over still raises one.

    ``questions_aged`` and ``held_dismissed`` are cut here to what still
    stands: a question answered or whose link closed, and mail handed over or
    withdrawn, are let go of. So a question whose only answer is withdrawn
    later is owed again, and the shell measures its age afresh from delivery,
    which has passed: its row returns at once.

    ``questions_dismissed`` is cut to the rows that would still be raised for
    the reason they were dismissed for (``PeerQuestionUnanswered.running``). A
    row dismissed while its lead's session was down lets go at a resume, which
    ends that row, so the question raises a row again if its age then passes.
    """
    awaiting = {c.id: c for c in world.awaiting_answer()}
    aged = frozenset(cid for cid in world.questions_aged if cid in awaiting)
    held = world.peer_held()
    dismissed = frozenset(c.id for c in held if c.id in world.held_dismissed)
    asked: list[PeerQuestionUnanswered] = []
    for question in world.questions_unread():
        assert question.link is not None and question.channel is not None
        if _wakeable(world, question.recipient[0]):
            # A running lead is woken for it, and the age runs from its read.
            continue
        asked.append(
            PeerQuestionUnanswered(
                node=question.recipient,
                since=question.posted_at,
                summary=(
                    f"#{question.channel}: a question from the lead linked to "
                    f"{question.recipient[0][:8]} is unread and unanswered - its session "
                    "is not running"
                ),
                question=question.id,
                link=question.link,
                channel=question.channel,
                running=False,
                delivered=False,
            )
        )
    for cid, question in sorted(awaiting.items()):
        assert question.link is not None and question.channel is not None
        # A recipient whose session cannot be woken will not answer however long
        # the age is, so its question is shown at once (Decided 4). Asked of the
        # session state on every step, so a resume puts the question back under
        # the age, which the shell measures from delivery.
        running = _wakeable(world, question.recipient[0])
        if running and cid not in aged:
            continue
        # Not "unanswered" while the session is down: its lead read the
        # question, and an answer it sent is in its own log, which is not read
        # until the session is resumed (``Snapshot.awaiting_answer``).
        why = (
            "is unanswered past its age"
            if running
            else "has no answer in this run - its session is not running"
        )
        asked.append(
            PeerQuestionUnanswered(
                node=question.recipient,
                since=question.delivered_at or 0.0,
                summary=(
                    f"#{question.channel}: a question from the lead linked to "
                    f"{question.recipient[0][:8]} {why}"
                ),
                question=cid,
                link=question.link,
                channel=question.channel,
                running=running,
            )
        )
    raised = {(row.question, row.running) for row in asked}
    shut = frozenset(d for d in world.questions_dismissed if d in raised)
    owed: list[Obligation] = [row for row in asked if (row.question, row.running) not in shut]
    by_lead: dict[tuple[str, ChannelId], list[Concern]] = {}
    for message in held:
        if message.link is None or message.id in dismissed:
            continue
        over = world.channels.get(message.link)
        if over is None or over.live:
            by_lead.setdefault((message.recipient[0], message.link), []).append(message)
    for (session_id, lid), messages in sorted(by_lead.items()):
        # A link the store holds is named by its own name; only one it does
        # not hold is known by the name its messages give it.
        over = world.channels.get(lid)
        channel = over.name if over is not None else messages[0].channel or ""
        why = why_held(world, lid, messages)
        owed.append(
            PeerMailHeld(
                node=(session_id, None),
                since=messages[0].posted_at,
                summary=(
                    f"#{channel}: {len(messages)} message(s) for {session_id[:8]} held - "
                    f"the link {why}, so nothing hands them over"
                ),
                link=lid,
                channel=channel,
                messages=tuple(m.id for m in messages),
                why=why,
            )
        )
    if (
        aged == world.questions_aged
        and dismissed == world.held_dismissed
        and shut == world.questions_dismissed
        and not owed
    ):
        return world
    return dataclasses.replace(
        world,
        questions_aged=aged,
        held_dismissed=dismissed,
        questions_dismissed=shut,
        needs_you=tuple(sorted((*world.needs_you, *owed), key=lambda o: o.since)),
    )


def _peer_pair(world: Snapshot, link_id: ChannelId) -> str:
    """The two leads of a link, by their session ids' heads, in a stable order."""
    return " and ".join(sid[:8] for sid in sorted(world.channels[link_id].members))
