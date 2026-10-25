"""
Effects: what the reducer has to tell the shell (§2.7).

``_apply`` is a pure function of (snapshot, intent, instant). That is what makes
the store testable without a UI or an event loop, and it is why the store can be
confined to one thread without a lock. But five of the six bus tools need an answer
back, and a pure reducer has no way to give one: it returns the next world, not a
reply to the agent parked on a future.

Two of the five -- ``claim_task`` and ``read_inbox`` -- are *questions*, and were
always obviously in this class. The other three -- ``declare_task``,
``complete_task``, ``release_task`` -- assert facts the reducer may refuse, which
is the same problem wearing different clothes: an assertion whose reply is composed
by the caller rather than by the reducer is a caller *guessing*, and all three
guessed "it worked". A cycle-rejected declaration told the lead its task was on the
board, and the lead then waited on a task that was not there.

The functional answer is to widen the return rather than to smuggle the reply
through the world. ``_apply`` returns ``(Snapshot, tuple[Effect, ...])``; the app
loop hands each effect to the ``Bridge``, which completes the waiting future. The
same shape as ``Intent`` in the other direction: a value, matched exhaustively,
inspectable in a test with no threads in it.

Two properties come from this rather than from care:

- **An effect exists only because an intent was applied.** The alternative --
  answering every outstanding request each frame -- is wrong, because a future is
  registered *before* its intent is queued, so a request can be outstanding a
  frame before the store has seen it. "Answer what you applied" is unstateable as
  a bug here; there is nothing else to answer.
- **No correlation token lands in the domain.** ``Task`` and ``Concern`` describe
  work and messages, not the requests that touched them, so nothing has to
  remember to clear one.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from pathlib import PurePath

from . import drawable
from .board import BoardConcern, BoardTask
from .command import Command, CommandId, RequestRefusal
from .digest import Digest
from .model import (
    Channel,
    CitedQuestion,
    ClaimRefusal,
    CommandRefusal,
    Concern,
    ConcernId,
    Decision,
    DecisionRefusal,
    DetachRefusal,
    NodeId,
    PeerRefusal,
    PeerWithdrawRefusal,
    Snapshot,
    Task,
    TaskId,
    TaskRefusal,
)


@dataclass(frozen=True, slots=True)
class ClaimSettled:
    """
    A worker asked for work and this is the answer.

    ``task`` None means nothing was claimable -- a real answer, and the common one
    on a board whose remaining items are all blocked. The agent is told so and can
    stop asking; it is not left parked.

    ``refusal`` is why, in the reducer's vocabulary, and the shell owns the
    sentence as it does for ``TaskWriteSettled``. The store sets it on every
    claim that won nothing and never beside a task. **None beside no task is not
    a reason**: it is the value the Bridge answers with when the store never saw
    the claim, so a reader that finds both None knows only that nothing was
    claimed.
    """

    request_id: str
    task: Task | None
    refusal: ClaimRefusal | None = None


@dataclass(frozen=True, slots=True)
class InboxDelivered:
    """
    A recipient read its inbox, and these are the concerns that went with the reply.

    Carries the records as they were delivered, which is not always as they were
    posted: an operator can edit a concern in flight, and this is the text the
    recipient actually received.
    """

    request_id: str
    concerns: tuple[Concern, ...]


@dataclass(frozen=True, slots=True)
class PeerIdentity:
    """
    Who a lead is, as another lead's link names it: its session id, its card's
    title and its project. Derived from the store's root record for the session
    (``peer_identity``) each time it is asked, never stored, so a session id
    names one lead alike in every reply and across a resume that restores the
    record.

    ``title`` is the card's title as the fleet draws it for a root
    (``ui.session_menu.card_title``): the record's task, typed by the operator or
    an agent, and never this tool's words. ``project`` is the last component of
    the directory the session's writes are measured from (``session_base``, or
    ``cwd`` on a record that has none) -- the name the fleet's project lane
    shows (``ui.projects.project_key``) without the filesystem read the lane
    makes. Either is empty where the store holds no such fact, and ``held`` is
    False when it holds no root record for the session at all.

    Both are drawable (``drawable.text``): the record holds its task and its
    directories verbatim (``store._VERBATIM``), and this value is what carries
    them into a lead's tool replies, its wakes and the operator's rows.
    """

    session: str
    title: str = ""
    project: str = ""
    held: bool = False


def peer_identity(snap: Snapshot, session_id: str) -> PeerIdentity:
    """
    ``session_id``'s lead as ``snap`` describes it (``PeerIdentity``).

    The title rule mirrors ``ui.session_menu.card_title``'s for a root, so the
    two are kept in step; it is copied here because the core does not import
    the UI. It is the task, or nothing where ``card_title`` falls back to the
    session id: the description says the id already.

    The title and the project are made drawable here, from the verbatim
    record, once: every reader of a ``PeerIdentity`` is handed text that any
    text call and any transport takes.
    """
    rec = snap.get((session_id, None))
    if rec is None:
        return PeerIdentity(session=session_id)
    base = rec.session_base or rec.cwd or ""
    return PeerIdentity(
        session=session_id,
        title=drawable.text(rec.task),
        project=drawable.text(PurePath(base).name),
        held=True,
    )


# How much of a peer's card title its description quotes (``peer_text``). A
# title is the session's first prompt, so it can be a paragraph; sixty
# characters tell two cards apart, and the cut is said.
PEER_TITLE_CHARS = 60

# What a session id may be made of and still be laid out bare in a peer's
# description: the characters ``bus._BARE_NAME`` admits. A minted id always is.
_BARE_SESSION = re.compile(r"[A-Za-z0-9_./*@+-]+")


def _quoted(text: str) -> str:
    """
    ``text`` on one line between double quotes, a quote and a backslash in it escaped.
    """
    flat = " ".join(text.splitlines()).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{flat}"'


def peer_text(
    peer: PeerIdentity, *, title_chars: int = PEER_TITLE_CHARS, project: bool = True
) -> str:
    """
    A lead on another team, named alike to the operator and to every lead:
    ``session <id head>, card "<title>", project "<project>"``. The one
    renderer of a ``PeerIdentity`` (``bus.peer_said`` is this), so a session
    reads the same in a lead's reply, in a wake, on the operator's queue row
    and on the card, and alike after a resume that restores its record.

    The head is the session id's first eight characters, bare, or quoted when
    it holds anything else. The title and the project are typed by an agent or
    the operator, so each is quoted with its quote marks escaped and on one
    line: such text cannot end its own field and go on as this function's
    words. A title past ``title_chars`` is cut, and the cut is said outside the
    quotes; ``title_chars`` 0 leaves the card out, and ``project`` False the
    project (``peer_forms``). A session with no root record in the store is
    said so.
    """
    head = " ".join(peer.session[:8].splitlines())
    head = f"session {head if _BARE_SESSION.fullmatch(head) else _quoted(head)}"
    if not peer.held:
        return f"{head} (its card is not in this run)"
    said = [head]
    title = " ".join(peer.title.split())
    if title and title_chars > 0:
        shown = title[:title_chars]
        said.append(f"card {_quoted(shown)}" + (" (title cut)" if shown != title else ""))
    if peer.project and project:
        said.append(f"project {_quoted(peer.project)}")
    return ", ".join(said)


# How much of a peer's card title its short description quotes (``peer_forms``).
PEER_SHORT_TITLE_CHARS = 16


def peer_forms(peer: PeerIdentity) -> tuple[str, ...]:
    """
    ``peer_text``'s descriptions of ``peer``, longest first and each distinct:
    the whole one, then the session head and a title cut to
    ``PEER_SHORT_TITLE_CHARS``, then the head alone. For a row that must keep
    something else whole and says whom it reaches in what room is left
    (``approval.summarize``).
    """
    forms = (
        peer_text(peer),
        peer_text(peer, title_chars=PEER_SHORT_TITLE_CHARS, project=False),
        peer_text(peer, title_chars=0, project=False),
    )
    return tuple(dict.fromkeys(forms))


@dataclass(frozen=True, slots=True)
class PeerSettled:
    """
    A lead sent a message to a linked lead, and this is what the store did.

    ``refusal`` None means it was filed, addressed to the lead of the link's
    other member. Anything else is why not, in the reducer's vocabulary, with
    the sentence left to the shell as on ``TaskWriteSettled``. Carries no
    message id: the caller minted it.

    ``recipient`` is the lead the message went to. The sender may name only
    the channel, and this is the one place it learns which lead that reached.
    On ``PeerRefusal.LINKED_AFTER`` it is the lead on the live link of that
    name now, which the message did not reach, so the refusal can say who
    that is; on every other refusal it is None.

    ``re_withdrawn`` says the message its ``re`` names is withdrawn, which
    does not stop the send: True when it was withdrawn after its lead read it
    (by its sender, the only one who can), False when before, and None when it
    is not withdrawn or there is no ``re``.
    """

    request_id: str
    refusal: PeerRefusal | None = None
    recipient: PeerIdentity | None = None
    re_withdrawn: bool | None = None


@dataclass(frozen=True, slots=True)
class PeerWithdrawSettled:
    """
    A lead withdrew a peer message it sent, and this is what the store did.

    ``refusal`` None means it was withdrawn; anything else is why not, with the
    sentence left to the shell as on ``PeerSettled``. On a withdrawal,
    ``recipient`` is the lead it was addressed to, ``channel`` the link's name,
    ``read`` whether that lead had been handed it -- and so is told of the
    withdrawal at its next read -- and ``answers`` the message it answered
    (its ``re``), with ``cited`` where that message stands now that nothing
    withdrawn answers it (``model.Snapshot.cited_question``): owed an answer
    again only when it is ``CitedQuestion.OWED_AGAIN``.
    """

    request_id: str
    refusal: PeerWithdrawRefusal | None = None
    recipient: PeerIdentity | None = None
    channel: str | None = None
    read: bool = False
    answers: ConcernId | None = None
    cited: CitedQuestion | None = None


@dataclass(frozen=True, slots=True)
class PeerInboxDelivered:
    """
    A lead read its peer mail, and these went with the reply.

    ``concerns`` are the messages handed over, as delivered, oldest first.
    None is the answer to a request the store never saw, which is not "no
    mail".

    ``held`` are messages waiting for the reader whose own link does not hand
    them over: the link they were sent over is not live in this run -- closed,
    or not restored at this start (``model.Snapshot.peer_deliverable``). They
    are held, waiting in the store and kept in their sender's log if that
    session was recording its board when they were sent, and are carried here
    so the reply can say they exist; their text is not the reader's while
    their link is not live.

    ``links`` are the live links the reader's session is a member of when it
    read, so a reply can tell "nothing arrived" from "you are on no such
    channel".

    ``peers`` describe every other session those name: the other member of
    each link, and the sender of each message handed over, owed or withdrawn,
    once each.

    ``owed`` are the questions the reader was handed by an earlier read, over
    a live link the read covers, and owes an answer to
    (``model.Snapshot.owed_answers``), oldest handed over first. Listed again
    and marked as nothing: their delivery is as it was.

    ``withdrawn`` are the messages the reader was handed by an earlier read on
    a channel the read covers and their sender has since withdrawn
    (``model.Snapshot.withdrawn_untold``), oldest handed over first, at most
    ``model.MAX_WITHDRAWALS_LISTED``. Each is listed by the one read in a run
    that carries it: the read marks it told. ``withdrawn_more`` counts those
    past the bound, left for the next read.
    """

    request_id: str
    concerns: tuple[Concern, ...] | None
    held: tuple[Concern, ...] = ()
    links: tuple[Channel, ...] = ()
    peers: tuple[PeerIdentity, ...] = ()
    owed: tuple[Concern, ...] = ()
    withdrawn: tuple[Concern, ...] = ()
    withdrawn_more: int = 0


@dataclass(frozen=True, slots=True)
class TaskWriteSettled:
    """
    An agent asserted something about the board, and this is what the store did.

    ``refusal`` None means it took effect. Anything else is why it did not, in the
    reducer's vocabulary rather than in prose -- ``bus.py`` turns the member into
    the sentence the agent reads, so the words a worker is given stay in the shell
    with every other piece of presentation.

    One effect for all three writes rather than three near-identical ones. They
    differ only in which refusals they can produce, and that is a property of the
    intent, not of the reply: nothing downstream branches on which tool asked, and
    the request id already tells each handler that the answer is its own.

    Carries no task id. The caller knows it -- ``declare_task`` generates the id
    before it emits -- so putting it here would be a second copy of a fact one side
    already holds, kept in agreement by nothing.

    ``auto_depends`` is the one member only a declaration can populate, which bends
    the paragraph above and is worth saying why. It is not a fourth refusal and not
    a property of the intent: it is the store reporting that what landed is *not
    what the caller asked for* -- dependencies it added, because the task overlaps
    files an unfinished task already claims. A completion or a release cannot
    change the caller's own request, so the tuple is empty there rather than
    meaningless. Splitting a second effect out for the single write that can carry
    it would give three handlers two shapes to await for one question.

    Empty on refusal, and that is not a special case: nothing landed, so nothing was
    added to it.

    ``already_behind`` is the other half of the same report: unfinished tasks
    that write a file the new one writes and that the store did *not* queue it
    behind, because each already waits on the new task -- directly or through
    others -- and the edge would have left both waiting on each other. The two
    are still kept out of the file at once, in the order the board already had:
    the new task first. The declarer is told because that order is the reverse
    of the one every other overlap gets, and a plan that assumed the older task
    writes first is wrong about it.

    ``held_off_fleet`` is set beside ``DUPLICATE_ID`` only, and only when the
    task already under the id is on the board of a session the operator took off
    the fleet (``Snapshot.off_fleet``): it is that session's id. Task ids are
    fleet-wide, so such a board goes on refusing ids, and the declarer cannot
    read it and the operator cannot see it. Computed at the apply, as the
    refusal is, because the answer is about that instant.

    ``cycle`` is set beside ``WOULD_CYCLE`` only: a shortest loop the
    declaration would close, from the new task back to it, through declared
    dependencies and through every-writer waits (``model.Task.writers``).
    Computed at the apply for ``held_off_fleet``'s reason, so the declarer is
    told which loop was refused and not one a later read finds.
    """

    request_id: str
    refusal: TaskRefusal | None = None
    auto_depends: tuple[TaskId, ...] = ()
    already_behind: tuple[TaskId, ...] = ()
    held_off_fleet: str | None = None
    cycle: tuple[TaskId, ...] = ()


@dataclass(frozen=True, slots=True)
class BoardDelivered:
    """
    What is on the asking agent's board, as the rows the operator is also shown.

    ``board.BoardTask`` rather than ``model.Task`` on purpose. A worker needs the
    same three derived facts a reader does -- who holds it under the address the bus
    routes to, what it is waiting on, and which of those dependencies were never
    declared -- and none of them are on the record. Handing over raw ``Task`` values
    would put that derivation in ``bus.py``, which is a second implementation of the
    projection ``board.py`` already owns, free to disagree with the pane.

    Carries the concern rows as well as the tasks, and carries *all* of them --
    the same projection the pane draws. Which of them an agent is shown is the
    tool's decision, not the store's: question 2 was answered "start wider, then
    narrow if required", and a narrowing here would be a second rule about whose
    reasoning is visible, kept in agreement with ``bus.py`` by nothing.

    This is the one effect whose intent changes nothing. It is still an effect
    rather than a store method the handler calls, because the handler is on the
    asyncio thread.

    ``snapshot`` is the world the rows were projected from, for the bus's graph
    queries (``boardgraph``: why a task is blocked, what is ready, what a
    declaration would do), which walk ``Task`` records the rows do not carry.
    Handed over and not read, and the handler never reaches the store. What
    makes that safe is narrower than "a frozen value": the snapshot's maps are
    built fresh by every apply and nothing writes them afterwards, and the
    queries read only ``tasks`` and ``nodes`` -- ``Task`` records, frozen and
    made of tuples, strings, numbers and other frozen records, and of an
    ``AgentRecord`` its state and its session's standing. An ``AgentRecord``
    also reaches what is not frozen, its ``Transcript`` (append-only and
    synchronised on its own) and ``PendingApproval.raw_args`` (a plain dict
    nothing writes once parked), and no query reads either. None on an answer
    the store did not make.
    """

    request_id: str
    tasks: tuple[BoardTask, ...]
    concerns: tuple[BoardConcern, ...] = ()
    snapshot: Snapshot | None = None


@dataclass(frozen=True, slots=True)
class DigestDelivered:
    """
    The digest of the asking agent's round, as the value the pane also draws.

    The ``Digest`` and not text, for ``BoardDelivered``'s reason: the words an
    agent reads are the shell's, and one of the four slots holds text an agent
    must not be shown at all (``digest.Owed.summary``), which is a decision about
    presentation that a string built here would have already made.

    ``digest`` None is the answer to a request the store never saw. It is the
    fallback the handler parks with, and it is not an empty digest: four empty
    slots say nothing changed and nothing is unverified, which at shutdown is a
    claim about a board nobody consulted.
    """

    request_id: str
    digest: Digest | None


@dataclass(frozen=True, slots=True)
class DecisionSettled:
    """
    An agent raised or withdrew a decision, and this is what the store did.

    ``refusal`` None means it took effect; anything else is why not, in the
    reducer's vocabulary, with the sentence left to the shell as on
    ``TaskWriteSettled``. Carries no decision id for the reason that effect
    carries no task id: the caller minted it or named it.

    ``peer`` is set only on a raise over a link: the lead at its other end,
    which can read the decision, when it was filed; on
    ``DecisionRefusal.LINKED_AFTER``, the lead on the live link of that name
    now, which the asker may not have meant. None otherwise.
    """

    request_id: str
    refusal: DecisionRefusal | None = None
    peer: PeerIdentity | None = None


@dataclass(frozen=True, slots=True)
class DecisionsDelivered:
    """
    Every decision the asking agent may read, oldest first, open and ended
    (``Snapshot.decisions_readable_by``): its own, and for the root of a session
    every decision raised in that session.

    The records as they stood before this read was marked. The mark,
    ``first_delivered_at``, says when a reply first carried a decision's answer
    to the agent that raised it, and only a read that named that decision sets
    it (``intents.DecisionsRead``). So None on an answered or dismissed record
    is not "this reply hands it over": a read that named no decision is a list
    that shows no answer, and the root reading another agent's decision is not
    the asker. Which reply this is, the shell knows from the request it sent.

    The records and not text: an answer is the operator's and the rest is the
    asker's own, and which of them is quoted how is the shell's to decide.

    ``decisions`` None is the answer to a request the store never saw, as
    ``DigestDelivered.digest`` None is. It is not the empty tuple, which says
    the asker may read none.

    ``peers`` describe the lead of every other session one of ``decisions``
    was raised in over a link, once each, so the reply names which lead
    raised it and not only the link.
    """

    request_id: str
    decisions: tuple[Decision, ...] | None
    peers: tuple[PeerIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandSettled:
    """
    An agent asked for a command or withdrew one, and this is what the store
    did.

    ``refusal`` None means it took effect. Otherwise it is why not: a
    ``RequestRefusal`` when the string or a text around it is one that is never
    put in front of the operator, a ``CommandRefusal`` when the reason is the
    store's. Both have a ``sentence``.

    ``command_id`` is the one the intent named, held or not.
    """

    request_id: str
    command_id: CommandId
    refusal: CommandRefusal | RequestRefusal | None = None


@dataclass(frozen=True, slots=True)
class CommandsDelivered:
    """
    Every command the asking agent may read, oldest first, open and ended
    (``Snapshot.commands_readable_by``), as they stood before this read was
    marked.

    The records and not text: the asked string is an agent's, an edited run
    string is the operator's and the tail is a program's, and how each is
    marked is the shell's to decide.

    ``commands`` None is the answer to a request the store never saw. It is not
    the empty tuple, which says the asker may read none.
    """

    request_id: str
    commands: tuple[Command, ...] | None


@dataclass(frozen=True, slots=True)
class CommandToRun:
    """
    An order to the shell: the operator approved ``command``, and it is to be
    handed to the runner. ``command.run`` is set and is the whole of what runs.

    Not an answer to anybody. ``request_id`` is empty, as on a
    ``SessionDetachSettled`` nobody is parked on, so that an effect is one shape
    to the code that routes them.
    """

    command: Command
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class CommandToStop:
    """
    An order to the shell: the operator asked for ``command_id``, which is
    running, to be killed. May be given more than once for one command.
    """

    command_id: CommandId
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class SessionDetachSettled:
    """
    The operator asked to take a session off the fleet, and this is what the
    store did.

    ``refusal`` None means it is off the fleet, including when it already was.
    Anything else is why not, and the shell owns the sentence.

    ``request_id`` is the intent's, empty when nobody is parked on it.
    """

    request_id: str
    session_id: str
    refusal: DetachRefusal | None = None


class WakeReason(enum.Enum):
    """What changed for a session that is woken (``SessionWoken``)."""

    DECISION_ANSWERED = "decision answered"
    DECISION_DISMISSED = "decision dismissed"
    COMMAND_ENDED = "command ended"
    CONCERN_TO_LEAD = "concern to the lead"
    # A linked lead's message is waiting on a link (Decided 10). The subject is
    # the link's id, and the wake carries no message text.
    PEER_MESSAGE = "peer message"


@dataclass(frozen=True, slots=True)
class SessionWoken:
    """
    An order to the shell: something the session ``session_id`` asked for, or a
    worker's message to it, has arrived, and the session is to be told so with
    a message of pptmstr's own (``bus.wake_lines``).

    ``subject_id`` is what to read: the decision, command or concern id, or
    for ``PEER_MESSAGE`` the id of the link the mail waits on.
    ``sender`` is the worker that posted a concern, None for the rest. The
    session is the asker's session and its lead is what is woken, a sub-agent
    asker included: a sub-agent has no turn of its own to be woken into, and it
    relays through its lead already.

    Not an answer to anybody, so ``request_id`` is empty, as on ``CommandToRun``.
    Whether the session is live, and coalescing a burst into one wake, are the
    shell's: neither is a fact of the store. The one exception is a peer
    message's wake, which the store itself coalesces per link and counts
    toward the link's chain (``model.PeerWakes``), because what it holds back
    is owed to the operator as a row.
    """

    session_id: str
    reason: WakeReason
    subject_id: str
    sender: NodeId | None = None
    request_id: str = ""


# Explicit union, for the same reason ``Intent`` is one: the app loop matches over
# it and mypy reports an unhandled member as a type error rather than letting an
# agent stay parked on a future nobody completes.
Effect = (
    ClaimSettled
    | InboxDelivered
    | PeerSettled
    | PeerWithdrawSettled
    | PeerInboxDelivered
    | TaskWriteSettled
    | BoardDelivered
    | DigestDelivered
    | DecisionSettled
    | DecisionsDelivered
    | CommandSettled
    | CommandsDelivered
    | CommandToRun
    | CommandToStop
    | SessionDetachSettled
    | SessionWoken
)
