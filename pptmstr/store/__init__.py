"""
The store: single source of truth (I1).

Confined to the main/UI thread. It holds one ``Snapshot`` reference; applying an
intent builds a replacement and swaps it. ``snapshot()`` is therefore an attribute
read (I3), which is what lets the UI take a consistent view once per frame (I2)
without deep-copying the world at frame rate.

Cost model, stated plainly because it is the load-bearing tradeoff here: applying
one intent is O(nodes), since it copies the node dict. Taking a snapshot is O(1).
That is the right way round -- snapshots happen every frame, intents happen when
something actually changes -- and it holds for the tens-of-agents scale this tool
targets. It would need revisiting in the thousands, where a persistent map would
earn its complexity; at this scale it would only add indirection.

Tree order is recomputed only when the shape of the tree changes, not on every
field update, because a pre-order walk is the one genuinely superlinear thing in
here.
"""

from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable, Iterable
from types import MappingProxyType
from typing import assert_never

from .. import drawable
from ..approval import Policy
from ..board.digest import digest
from ..board.rows import board_concerns, board_tasks
from ..channels.records import EVERY_CHANNEL, Channel, PeerRefusal, PeerWithdrawRefusal
from ..command import Command, CommandState, End, EndUnknown, Refused, Run
from ..command import Withdrawn as CommandTakenBack
from ..effects import (
    BoardDelivered,
    ClaimSettled,
    CommandsDelivered,
    CommandToRun,
    CommandToStop,
    DecisionsDelivered,
    DigestDelivered,
    Effect,
    InboxDelivered,
    PeerInboxDelivered,
    PeerSettled,
    PeerWithdrawSettled,
    SessionDetachSettled,
    WakeReason,
    peer_identity,
)
from ..intents import (
    AgentFinished,
    AgentRemoved,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    BoardRead,
    BoardRestored,
    ChannelClosed,
    ChannelLinked,
    ChannelsRestored,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandRequested,
    CommandsRead,
    CommandsRestored,
    CommandStopAsked,
    CommandWithdrawn,
    CompactionObserved,
    ConcernEdited,
    ConcernPosted,
    ConcernWithdrawn,
    ContextPolled,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionsRead,
    DecisionWakeSent,
    DecisionWithdrawn,
    DigestRead,
    FailedSessionClosed,
    FailureAcknowledged,
    HeldMailDismissed,
    InboxRead,
    Intent,
    PeerInboxRead,
    PeerQuestionAged,
    PeerQuestionDismissed,
    PeerSent,
    PeerWakeSent,
    SessionDetached,
    SessionRecalled,
    SessionSettled,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TaskAmended,
    TaskClaimRequested,
    TaskClosed,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TaskVersionHanded,
    TaskWithdrawn,
    TopicChanged,
    UsageAccrued,
    WakeChainReleased,
)
from ..model import (
    MAX_CLOSE_REASON_CHARS,
    MAX_COMMAND_REFUSAL_CHARS,
    MAX_WITHDRAWALS_LISTED,
    RECALLED_TOPIC,
    AgentRecord,
    AgentState,
    Answered,
    ApprovedWrites,
    Concern,
    ConcernState,
    DecisionRefusal,
    Dismissed,
    NodeId,
    PendingApproval,
    Recalled,
    Snapshot,
    TaskId,
    TaskRefusal,
    TaskState,
    TaskVersion,
    Withdrawn,
    approved_write,
    closing_notice,
    closing_result,
    normalised_property,
    normalised_touches,
    peer_addressed,
    withdrawal_notice,
    withdrawal_result,
)
from ..transcript import Transcript
from .approvals import _claimed_tasks, _peer_row, _peer_rows, _redescribed
from .commands import (
    _approved,
    _closed,
    _command_settled,
    _command_since,
    _command_withdrawal_refusal,
    _request_refusal,
    _with_command,
    _with_restored,
)
from .decisions import (
    _decided,
    _decision_settled,
    _ended_by_operator,
    _filed,
    _raise_refusal,
    _withdrawal_refusal,
)
from .mail import _concern_wake, _message_withdrawal_refusal, _withdraw_settled
from .nodes import _FINAL_STATES, _back_on_fleet, _no_longer_recalled, _preorder, _subtree
from .owing import _needs_you, _owing_commands, _owing_decisions, _woken
from .peers import _link_peer, _peer_fields_hold, _read_covers
from .restore import _restored
from .tasks import (
    _already_behind,
    _amender_refusal,
    _as_asked,
    _auto_depends,
    _declaration_refusal,
    _may_reclaim,
    _ownership_refusal,
    _pick_claim,
    _settled,
    _withdrawer_refusal,
)
from .waking import _owing_peer_mail, _owing_wake_chains, _peer_waking

# What a run says about its own node: where it is, what it is doing, what it
# spent, that it ended. Dropped for a recalled record (``AgentRecord.recalled``),
# which no run in this process stands behind. A run announces its root before it
# says anything else, and that announce replaces the recalled record whole, so
# one of these reaching a record that is still recalled is not about a run this
# process has, and applying it would put an observed-looking fact on a record
# whose whole statement is that nothing was observed.
#
# ``ApprovalRequested`` is not here: an approval is never dropped, and a record
# that comes to carry one stops being recalled (``_no_longer_recalled``).
_RUN_REPORTS = (
    StateChanged,
    SubagentProgress,
    SubagentDelivered,
    TopicChanged,
    UsageAccrued,
    ContextPolled,
    CompactionObserved,
    AgentFinished,
    AgentResumed,
)


# What ``Store.apply_all`` calls after each intent it applies: the snapshot
# before, the intent, the snapshot after, and the instant the step was given --
# ``boardfacts.outcomes``'s arguments, in its order.
Observer = Callable[[Snapshot, Intent, Snapshot, float], None]


class Store:
    """
    Not thread-safe, and deliberately so.

    Making it thread-safe would invite writes from the asyncio thread, which is
    exactly the design this project rejected (see intents.py). A lock here would
    make the wrong thing possible rather than making anything safe.
    """

    __slots__ = ("_snapshot",)

    def __init__(self) -> None:
        self._snapshot = Snapshot.empty()

    def snapshot(self) -> Snapshot:
        """
        O(1). Call exactly once per frame (§4.1) -- calling it twice is a bug even
        when it looks harmless, because the two results can differ and the frame
        then renders torn state.
        """
        return self._snapshot

    def apply(
        self, intent: Intent, now: float | None = None, observe: Observer | None = None
    ) -> tuple[Effect, ...]:
        """
        Apply one intent, producing a new snapshot and any answers it owes.

        The return is ignored by every caller that only mutates, which is most of
        them; it matters for the bus intents that an agent is parked on.

        A batch of one, through ``apply_all``: there is one way an intent reaches
        ``_apply``, so there is none an observer does not see.
        """
        return self.apply_all((intent,), now, observe)

    def apply_all(
        self,
        intents: Iterable[Intent],
        now: float | None = None,
        observe: Observer | None = None,
    ) -> tuple[Effect, ...]:
        """
        Apply a batch, rebuilding derived state once at the end.

        Draining a frame's worth of intents this way keeps the per-intent
        bookkeeping from running N times when one pass would do.

        ``now`` is the frame clock, passed in rather than read here so ``_apply``
        stays a pure function of (snapshot, intent, time) and a test can hand it a
        fixed instant. The whole batch shares one reading, which is also what the
        frame does with its snapshot.

        Effects accumulate in application order and are returned together, so the
        frame answers exactly what it applied -- no more, and never something whose
        intent is still sitting in the queue.

        ``observe`` is called once per intent, in order, straight after that
        intent is applied and whether or not it changed anything: the board log is
        derived from what it is shown (``boardfacts.outcomes``), and a step it did
        not see is a fact that is never written. It is given the snapshots and
        must not reach back into this store, which still holds the one from before
        the batch. If it raises, the batch is abandoned as it would be had
        ``_apply`` raised: nothing is swapped in and no effect is returned.
        """
        clock = _clock(now)
        snap = self._snapshot
        out: list[Effect] = []
        for intent in intents:
            before = snap
            snap, effects = _apply(snap, intent, clock)
            out.extend(effects)
            if observe is not None:
                observe(before, intent, snap, clock)
        self._snapshot = snap
        return tuple(out)


def _clock(now: float | None) -> float:
    """
    The caller's instant, or this one.

    Defaulted at the entry point rather than inside ``_apply`` so the pure core
    keeps a required argument and cannot silently read a clock mid-batch.
    """
    return time.monotonic() if now is None else now


# What ``_drawable`` leaves exactly as it arrived: what something is *run* with,
# *reused* for, or *written back* to pptmstr's own files from. ``drawable.text`` is
# not idempotent, so a field held drawable and then reused or written back is
# escaped again the next time it comes in; and a field run with its escape is run
# with something nobody asked for. Every one of them is drawn and sent only
# through a helper that takes any ``str`` -- ``widgets.author_text`` and the
# helpers built on it, ``commandtext.visible`` -- and ``tests/test_verbatim_drawn.py``
# draws every pane over them and reads ``pptmstr/ui`` for a raw draw of one.
#
# Four of them also reach another lead's tool replies and wakes, and are made
# drawable with ``drawable.text`` on the way there, from the verbatim field, each
# time: a root's task and its directories (``session_base``, else ``cwd``) as its
# card title and project, in ``effects.peer_identity``; and a channel's charter,
# in ``channels.text._links_lines``.
#
# - The arguments a parked call's editor opens on, which approve sends back to
#   the tool (with the drawable twin ``PendingApproval.args`` beside them).
# - A session's launch: its directories, task, team and brief. A relaunch and a
#   fork start from them (``LaunchSpec.from_record``), and its session file is
#   written from them -- a recalled one's too, since its resume is launched in
#   them and writes them back.
# - How a run failed, which its session file is written from; the obligation
#   that shows it holds it drawable (``_needs_you``).
# - The message a resume form opens on, which is sent as it stands.
# - A break-glass command: the string asked or approved, run in the shell and
#   directory approved; the operator's answer or reason, handed to its asker byte
#   for byte; and what the command wrote, which is shown and sent through
#   ``commandtext.visible`` and whose ``\x00`` is only right of the bytes
#   themselves. A command's texts are also held to a refusal the reducer asks of
#   them (``command.request_refusal``, ``Snapshot.command_approval_refusal``), which
#   has to see a NUL or a lone surrogate to refuse it.
# - A channel's charter, which is written to the channel's log from the store
#   and read back from it (``channels.facts.outcomes``): the operator's text, kept as
#   typed.
#
# A board restore is not here because it never meets this table: ``_drawable``
# leaves ``BoardRestored`` whole. The log is written from the store's board, so it
# holds each field in the form the store holds it -- drawable, except the
# operator's answer to a decision (``Answered.text`` above), which the log carries
# verbatim (``boardlog.Answered``).
_VERBATIM: drawable.Verbatim = {
    PendingApproval: frozenset({"raw_args", "shown_args"}),
    AgentSpawned: frozenset({"cwd", "session_base", "task", "template", "brief"}),
    SessionRecalled: frozenset({"cwd", "session_base", "task", "template", "brief"}),
    Recalled: frozenset({"last_message"}),
    AgentFinished: frozenset({"error"}),
    Command: frozenset({"asked", "reason", "expects"}),
    Run: frozenset({"text", "shell", "cwd"}),
    End: frozenset({"tail"}),
    CommandApproved: frozenset({"text", "shell", "cwd"}),
    DecisionAnswered: frozenset({"answer"}),
    Answered: frozenset({"text"}),
    CommandRefused: frozenset({"reason"}),
    Refused: frozenset({"reason"}),
    ChannelLinked: frozenset({"charter"}),
    Channel: frozenset({"charter"}),
}


def _drawable(intent: Intent) -> Intent:
    """
    The intent with every string a session wrote in a form a text call can be
    handed (``drawable``): the same object unless it held a lone surrogate, a NUL,
    or a backslash spelling the escape of one.

    **Here, at the reducer's door, and so once for everything the store will ever
    hold.** A record is drawn by whichever panes come to read it, through several
    hundred text calls; a string that raises in one of them is that pane not
    drawn, on every frame the record lives. Everything in a snapshot got there
    through an intent, and what the reducer derives it derives from this one.

    An identifier is rewritten with the rest. The mapping is one to one, every
    path by which an identifier reaches the store is this one, and the ones a
    reply is matched on outside the store -- a pending id, a request id -- are
    made by the host from an id the API issued, not written by a model. A task id
    this would rewrite is refused at its declaration, as the task's id or an
    entry of its ``depends_on`` (``TaskRefusal.ESCAPED_ID``): a task id is also
    read outside the store --
    the graph queries, a board row an agent copies an id from -- and there the
    sent form and the stored one would not meet, nor would a stored one copied
    back in, since ``text`` is not idempotent.

    A parked call keeps ``raw_args`` as the agent made them and gains the drawable
    form beside them where the two differ (``PendingApproval.args``).

    **A board restore is the one intent left whole.** Its board is a log the store's
    own board was written to, so every text in it is already in the form the store
    holds: drawable, and the operator's answer to a decision verbatim. A pass here
    would write each backslash ``text`` wrote once more and escape every NUL and
    lone surrogate in a restored answer: the board would read as nothing an agent
    wrote, the answer would no longer be the one typed, and over the store that
    wrote it the board would be a different one (``Unplaced.STORE_DIFFERS``),
    withholding the whole restore. A log that holds what the store never writes
    -- such a character anywhere but an answer -- is checked for where it is
    placed (``Unplaced.UNDRAWABLE``).
    """
    if isinstance(intent, BoardRestored):
        return intent
    intent = drawable.shown(intent, _VERBATIM)
    if isinstance(intent, ApprovalRequested):
        pending = intent.pending
        twin = drawable.shown(pending.raw_args)
        if twin is not pending.raw_args:
            intent = dataclasses.replace(
                intent, pending=dataclasses.replace(pending, shown_args=twin)
            )
    return intent


def _apply(snap: Snapshot, intent: Intent, now: float) -> tuple[Snapshot, tuple[Effect, ...]]:
    """
    Pure: old snapshot plus intent and an instant gives new snapshot, and whatever
    the shell has to be told about it.

    A free function rather than a method so it can be exercised without a Store, and
    so that it, whole, is the single audit point for every mutation in the system.
    The match below is the only dispatch on the intent into the twelve slices it
    copies first. An arm leaves it one of two ways: a ``return snap, ...``, which
    hands back the snapshot it was given and runs nothing after, or by falling
    through to the tail, which runs for every intent that does, whether or not its
    arm changed anything. The tail is the only other writer: it stamps
    ``state_since``, clears ``recalled`` (``nodes._no_longer_recalled``),
    re-describes the parked peer rows (``approvals._peer_rows``), re-orders the
    tree (``nodes._preorder``), derives ``needs_you``, ``any_active`` and
    ``command_since``, and then runs ``waking._peer_waking`` -- the only writer of
    ``Snapshot.peer_wakes`` -- and the obligation chain that ends in
    ``nodes._back_on_fleet``. ``waking._peer_waking`` and ``waking._moved`` each
    match on the intent again, each with an open default. Nothing outside this
    package calls into its modules.

    Most intents answer nobody and return no effects. The two that do -- a claim and
    an inbox read -- are questions from an agent parked on a future, and a reducer
    that could only return the next world would have to hide the reply inside it.
    See ``effects.py`` for why that was worth widening the signature over.
    """
    intent = _drawable(intent)
    nodes = dict(snap.nodes)
    concerns = dict(snap.concerns)
    tasks = dict(snap.tasks)
    unattributed = snap.unattributed_writes
    restores = snap.restores
    decisions = snap.decisions
    commands = snap.commands
    channels = snap.channels
    channel_since = snap.channel_since
    questions_aged = snap.questions_aged
    held_dismissed = snap.held_dismissed
    questions_dismissed = snap.questions_dismissed
    effects: tuple[Effect, ...] = ()
    reorder = False

    if isinstance(intent, _RUN_REPORTS):
        told = nodes.get(intent.node_id)
        if told is not None and told.recalled is not None:
            return snap, ()

    match intent:
        case AgentSpawned():
            parent_rec = nodes.get(intent.parent) if intent.parent else None
            depth = parent_rec.depth + 1 if parent_rec else 0
            replaced = nodes.get(intent.node_id)
            if replaced is not None and replaced.recalled is not None:
                # A resumed session takes its place among the sessions launched
                # in this process, at the moment it was: last. Left where the
                # recall put it, it would sit ahead of everything launched since
                # startup.
                del nodes[intent.node_id]
            nodes[intent.node_id] = AgentRecord(
                node_id=intent.node_id,
                parent=intent.parent,
                depth=depth,
                state=AgentState.SPAWNING,
                topic=intent.topic,
                task=intent.task,
                model=intent.model,
                agent_type=intent.agent_type,
                template=intent.template,
                brief=intent.brief,
                # Resolved here rather than at every emitter. A sub-agent's spawn
                # hook is not told a working directory but does run in its
                # session's, and making each emitter remember that is how the two
                # sides of a boundary drift apart.
                cwd=intent.cwd or (parent_rec.cwd if parent_rec else None),
                session_base=intent.session_base
                or (parent_rec.session_base if parent_rec else None),
                # Resolved here for the same reason and with sharper stakes: the
                # gate classifies a sub-agent's calls under its session's policy
                # (``driver._policy_for``), so a record that did not inherit would
                # say a node is gated more tightly than the gate judging it.
                policy=intent.policy or (parent_rec.policy if parent_rec else Policy.STRICT),
                started_at=intent.started_at,
                transcript=intent.transcript or Transcript(),
                task_is_label=intent.task_is_label,
            )
            reorder = True

        case StateChanged():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap, ()
            # A parked node stays parked. The CLI dispatches the PreToolUse hook
            # *before* it delivers the AssistantMessage carrying the ToolUseBlock,
            # so the gate parks the node and a StateChanged for the same tool call
            # arrives immediately afterwards. Letting it through overwrote
            # AWAITING_APPROVAL with CALLING_TOOL while the approval was still
            # pending and the agent still blocked -- the row read "thinking", the
            # state counted as active so the app never idled, and the whole thing
            # looked like a hang rather than a request for review.
            #
            # `pending` is the authority: while it is set, any other state is a lie.
            # The topic still updates, because naming the call being reviewed is
            # useful and not misleading.
            #
            # A node whose end was *declared* is a different case and comes first:
            # nothing that arrives afterwards describes the present, including the
            # topic. See _FINAL_STATES.
            if rec.state in _FINAL_STATES:
                return snap, ()
            state = AgentState.AWAITING_APPROVAL if rec.pending else intent.state
            changes: dict[str, object] = {"state": state}
            if intent.topic is not None:
                changes["topic"] = intent.topic
            if rec.state.is_terminal and not state.is_terminal:
                # A node that is running again has no end. ``ended_at`` stops the
                # elapsed clock (widgets.elapsed_cell, rail and health all prefer it
                # to ``now``) and dims it, so a session that errored and
                # then answered would sit in AWAITING_INPUT asking for a reply while
                # every other surface said it was over.
                #
                # ``acknowledged`` goes with it for a sharper reason: it suppresses
                # the SessionFailed obligation, and a stale True means the *next*
                # failure produces no obligation at all. Only ``error`` survives --
                # it is the record of what went wrong, nothing reads it except
                # through an obligation gated on FAILED, and clearing it would
                # destroy the only structured copy on the way through a recovery.
                changes["ended_at"] = None
                changes["acknowledged"] = False
            nodes[intent.node_id] = rec.with_(**changes)

        case SubagentProgress():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap, ()
            # Progress implies the sub-agent is working, which is the only signal
            # there is: sub-agent content does not stream (§2.5.1), so without this
            # the row would sit at SPAWNING until it finished.
            #
            # Same rule as StateChanged: a parked node stays parked, or a progress
            # report arriving while the sub-agent waits on the operator would hide
            # the fact that it is waiting. And a declared end wins outright -- the
            # `task_progress` system message this is built from rides the same
            # buffered stream as the AssistantMessage that motivates the guard
            # there, so one can land after the stop hook and relabel a finished
            # sub-agent with what it was doing before it answered.
            if rec.state in _FINAL_STATES:
                return snap, ()
            if rec.pending:
                state = AgentState.AWAITING_APPROVAL
            elif rec.state.is_terminal:
                state = rec.state
            else:
                state = AgentState.RUNNING_TOOL
            nodes[intent.node_id] = rec.with_(topic=intent.description, state=state)

        case SubagentDelivered():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap, ()
            # State is deliberately untouched. This says what the node produced, not
            # where it is: the AgentFinished that follows it on the same hook is what
            # settles the lifecycle, and having two intents from one hook both claim
            # the state is how an ordering assumption becomes a defect.
            #
            # A second delivery replaces the first rather than accumulating. A
            # sub-agent woken by a sibling's message answers again, and the answer it
            # just gave is the one the operator is being asked to read.
            nodes[intent.node_id] = rec.with_(deliverable=intent.text)

        case TopicChanged():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap, ()
            nodes[intent.node_id] = rec.with_(topic=intent.topic)

        case UsageAccrued():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap, ()
            nodes[intent.node_id] = rec.with_(usage=rec.usage.plus(intent.delta))

        case ContextPolled():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap, ()
            # A poll reports occupancy; it knows nothing about compaction history.
            # Carrying the previous counts forward is what keeps COMPACTED sticky --
            # letting a fresh poll reset them would erase the strongest retire signal
            # the moment the bar dropped.
            prev = rec.context
            ctx = intent.context
            if prev is not None and prev.compactions:
                ctx = ctx.with_compaction_history(prev.compactions, prev.last_compaction_at)
            nodes[intent.node_id] = rec.with_(context=ctx)

        case CompactionObserved():
            rec = nodes.get(intent.node_id)
            if rec is None or rec.context is None:
                # Nothing polled yet, so there is no occupancy record to annotate.
                # Dropping the event loses the count; that is preferable to
                # fabricating a ContextSnapshot whose token numbers would be
                # invented. The next poll establishes the baseline.
                return snap, ()
            nodes[intent.node_id] = rec.with_(
                context=rec.context.with_compaction_history(rec.context.compactions + 1, intent.at)
            )

        case ApprovalRequested():
            intent = dataclasses.replace(intent, pending=_peer_row(snap, intent.pending))
            rec = nodes.get(intent.node_id)
            if rec is None:
                # An approval must never be dropped. Every other intent for an
                # unknown node is a no-op, because a late status update about
                # something that does not exist is noise -- but this one has an
                # agent blocked behind it on a future only the operator can
                # complete. Dropping it hangs that agent permanently, with nothing
                # in the queue to explain why. A sub-agent whose SubagentStart hook
                # did not fire is the way this happens in practice.
                #
                # So a placeholder row is created instead. It is uglier than a
                # properly spawned node and infinitely better than a silent hang.
                parent = (intent.node_id[0], None)
                adopted = parent in nodes and parent != intent.node_id
                nodes[intent.node_id] = AgentRecord(
                    node_id=intent.node_id,
                    parent=parent if adopted else None,
                    depth=1 if adopted else 0,
                    state=AgentState.AWAITING_APPROVAL,
                    topic="awaiting approval",
                    task="(recovered from an approval for an unannounced agent)",
                    model="unknown",
                    # So a recovered node still lands in the right project lane
                    # rather than in an "unknown" one of its own, and so its
                    # writes are measured in the same units as its parent's. This
                    # record is built *because* an approval arrived, so it is the
                    # node most likely to be a writer -- omitting the base here
                    # would fail silently in exactly the recovered case.
                    cwd=nodes[parent].cwd if adopted else None,
                    session_base=nodes[parent].session_base if adopted else None,
                    # Adopted for the same reason as the pair above, and it is this
                    # arm's own gate that makes it necessary: the approval being
                    # recovered was classified under the session's policy, so a
                    # placeholder left at STRICT would show a tighter gate than the
                    # one that just judged the call it was built from.
                    policy=nodes[parent].policy if adopted else Policy.STRICT,
                    pending=(intent.pending,),
                    started_at=intent.pending.requested_at,
                )
                reorder = True
            else:
                # Appended, never replaced. Replacing is what orphaned the
                # earlier approval's future and hung its agent invisibly.
                #
                # The approval is recorded whatever state the node is in. An approval
                # is never dropped (see the arm above), and ``needs_you`` walks
                # ``pending`` rather than the state, so one that lands on a node
                # already reported finished is still answerable. Only the *state* is
                # withheld from a node whose end was declared: saying it waits on a
                # reviewer would be the revival this guard exists to stop, one arm
                # along.
                #
                # Defensive rather than measured. The gate emits ``ApprovalRequested``
                # synchronously on the PreToolUse hook path before it parks, and every
                # route to DONE needs the agent stopped or its stream closed, so the
                # driver has no ordering that reaches this branch in a final state.
                if rec.pending_by_id(intent.pending.id) is None:
                    nodes[intent.node_id] = rec.with_(
                        pending=rec.pending + (intent.pending,),
                        state=(
                            rec.state
                            if rec.state in _FINAL_STATES
                            else AgentState.AWAITING_APPROVAL
                        ),
                    )

        case ApprovalResolved():
            rec = nodes.get(intent.node_id)
            resolved = rec.pending_by_id(intent.pending_id) if rec is not None else None
            if rec is None or resolved is None:
                # Unknown or already settled: a double click, or a decision for an
                # approval that has since been cancelled. Both are normal.
                return snap, ()
            remaining = tuple(p for p in rec.pending if p.id != intent.pending_id)
            # Still parked if other calls from the same turn are outstanding. Moving
            # to RUNNING_TOOL here would hide them and re-create the bug this tuple
            # exists to fix, one layer up.
            #
            # A settled approval always leaves ``pending``, including on a node whose
            # end was declared -- otherwise the queue keeps a row for a decision
            # already made. The state does not move there: the two ways an approval
            # resolves after the end are the CLI cancelling the gate on its own
            # timeout and Bridge.stop rejecting every parked future at shutdown, and
            # both take the unapproved branch, which would otherwise report a
            # finished agent as thinking.
            if rec.state in _FINAL_STATES:
                state = rec.state
            elif remaining:
                state = AgentState.AWAITING_APPROVAL
            else:
                state = AgentState.RUNNING_TOOL if intent.approved else AgentState.THINKING
            nodes[intent.node_id] = rec.with_(pending=remaining, state=state)
            # The one instant the measurement, the writer and the work it is on are
            # all in hand. ``resolved`` leaves ``pending`` two lines up and its diff
            # is unrecoverable from any later snapshot.
            #
            # The arguments measured are the ones the operator approved: an edit
            # layered over the parked call, since edit-then-approve exists to correct
            # a wrong path (``driver._park``) and the original path would then name a
            # file nobody wrote. Merged rather than substituted, so an edit that
            # rewrites one key does not drop the rest.
            if intent.approved:
                owned = _claimed_tasks(tasks, intent.node_id)
                if len(owned) == 1:
                    #
                    # The drawable arguments, as the edit already is: a path measured
                    # here is kept in the ledger and drawn on the board.
                    args = (
                        {**resolved.args, **intent.edited_args}
                        if intent.edited_args is not None
                        else resolved.args
                    )
                    tasks[owned[0].id] = dataclasses.replace(
                        owned[0],
                        writes=owned[0].writes.plus(
                            approved_write(
                                resolved.tool_name,
                                args,
                                rec.cwd,
                                resolved.diff,
                                rec.session_base,
                            )
                        ),
                    )
                elif owned:
                    unattributed += 1

        case AgentFinished():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap, ()
            nodes[intent.node_id] = rec.with_(
                state=intent.state,
                ended_at=intent.ended_at,
                error=intent.error,
                pending=(),
            )

        case FailureAcknowledged():
            rec = nodes.get(intent.node_id)
            if rec is None:
                return snap, ()
            nodes[intent.node_id] = rec.with_(acknowledged=True)

        case AgentResumed():
            rec = nodes.get(intent.node_id)
            if rec is None:
                # A resume for a node we never had. Dropped rather than fabricated:
                # unlike ApprovalRequested, which invents a placeholder because an
                # orphaned approval hangs an agent, a resume carries no obligation
                # and a record built from it would have no history, no transcript
                # and a made-up parent.
                return snap, ()
            nodes[intent.node_id] = rec.with_(
                state=AgentState.THINKING,
                topic=intent.topic,
                # Only the liveness fields move. transcript, usage, started_at,
                # parent and depth are deliberately untouched -- rebuilding them is
                # exactly the defect this arm exists to prevent.
                ended_at=None,
                # A stale error on a node that is running again reads as a live
                # failure, and would keep it in needs_you as an unacknowledged one.
                error=None,
                acknowledged=False,
            )

        case AgentRemoved():
            if intent.node_id not in nodes:
                return snap, ()
            for nid in _subtree(snap, intent.node_id):
                nodes.pop(nid, None)
            reorder = True

        case ConcernPosted():
            # Filed even when the recipient is unknown to the store. A concern
            # addressed to a node that has not spawned yet is a real ordering, and
            # dropping it here would lose the operator's only view of a message the
            # sender believes it sent.
            #
            # A message between leads is filed only in the one shape a restore
            # places: between leads of two sessions (``model.peer_addressed``),
            # under a channel that is its link's name and with an ``re`` in the
            # concern-id shape (``_peer_fields_hold``). The bus sends one
            # through ``PeerSent`` and never here.
            posted = intent.concern
            if posted.is_peer and not (
                peer_addressed(posted.sender, posted.recipient)
                and _peer_fields_hold(posted.channel, posted.link, posted.re, channels)
            ):
                return snap, ()
            if intent.concern.id not in concerns:
                concerns[intent.concern.id] = intent.concern
                effects = _concern_wake(intent.concern)

        case BoardRead():
            # Answered from the pre-apply snapshot, which is the same view every
            # other arm reads and the one the effect is paired with. Nothing is
            # written, so there is no ordering question here -- but building the
            # rows from the dicts under construction would answer a question about
            # a world that does not exist until this function returns.
            effects = (
                BoardDelivered(
                    request_id=intent.request_id,
                    tasks=board_tasks(snap, intent.node_id[0]),
                    concerns=board_concerns(snap, intent.node_id[0]),
                    # The world the rows were projected from, for the bus's graph
                    # queries (``effects.BoardDelivered.snapshot``).
                    snapshot=snap,
                ),
            )

        case InboxRead():
            # Read from the pre-apply view, then mark. Withdrawn and
            # already-delivered concerns are excluded by inbox_of, so a second read
            # hands back nothing rather than re-delivering.
            waiting = snap.inbox_of(intent.node_id)
            for posted in waiting:
                concerns[posted.id] = dataclasses.replace(
                    posted, state=ConcernState.DELIVERED, delivered_at=intent.at
                )
            # The delivered forms, not the posted ones: an operator may have edited
            # a body in flight, and the effect must carry what was actually handed
            # over or the transcript and the store will disagree about it.
            effects = (
                InboxDelivered(
                    request_id=intent.request_id,
                    concerns=tuple(concerns[posted.id] for posted in waiting),
                ),
            )

        case PeerSent():
            # Routed here, through the live link, at the instant it is filed
            # (``intents.PeerSent``). The wake it owes the other lead is
            # ``_peer_waking``'s, decided over the built snapshot.
            route = snap.peer_route(intent.node_id, intent.channel)
            # Who a link that went live after the call reaches now, so the
            # refusal names the lead the sender may not have meant.
            now_on = None
            if not isinstance(route, PeerRefusal):
                if intent.concern_id in concerns:
                    route = PeerRefusal.ID_HELD
                else:
                    unsent = snap.peer_send_refusal(route[0], intent.called_at, intent.re)
                    if unsent is PeerRefusal.LINKED_AFTER:
                        now_on = peer_identity(snap, route[1][0])
                    route = unsent or route
            if isinstance(route, PeerRefusal):
                return snap, (
                    PeerSettled(request_id=intent.request_id, refusal=route, recipient=now_on),
                )
            link, recipient = route
            concerns[intent.concern_id] = Concern(
                id=intent.concern_id,
                sender=intent.node_id,
                recipient=recipient,
                subject=intent.subject,
                body=intent.body,
                posted_at=intent.at,
                edited=intent.edited,
                channel=intent.channel,
                needs_answer=intent.needs_answer,
                re=intent.re,
                quiet=intent.quiet,
                link=link.id,
            )
            cited = None if intent.re is None else snap.concerns.get(intent.re)
            effects = (
                PeerSettled(
                    request_id=intent.request_id,
                    recipient=peer_identity(snap, recipient[0]),
                    re_withdrawn=(
                        cited.delivered_at is not None
                        if cited is not None and cited.state is ConcernState.WITHDRAWN
                        else None
                    ),
                ),
            )

        case PeerInboxRead():
            # Read from the pre-apply view, then mark, as ``InboxRead`` does.
            # Only what a live link hands over now is marked; the rest stays
            # waiting, and the reply counts it as held.
            waiting = snap.peer_inbox_named(intent.node_id, intent.channel)
            handed = tuple(c for c in waiting if snap.peer_deliverable(c))
            handed_ids = {c.id for c in handed}
            for posted in handed:
                concerns[posted.id] = dataclasses.replace(
                    posted, state=ConcernState.DELIVERED, delivered_at=intent.at
                )
            reader = intent.node_id[0]
            links = snap.channels_of(reader) if intent.node_id[1] is None else ()
            # From the pre-apply view too, so what this read hands over is not
            # also listed as owed. Listed only: nothing about them is marked.
            # By the links the read covers, as ``_peer_waking`` lets go of the
            # wake naming them, so a question is listed wherever that is.
            covered = frozenset(link.id for link in _read_covers(snap, reader, intent.channel))
            owed = (
                tuple(c for c in snap.owed_answers(reader, EVERY_CHANNEL) if c.link in covered)
                if intent.node_id[1] is None
                else ()
            )
            # Listed once in a run: this read marks each it lists told, and
            # nothing else does. Past the bound they are counted and left untold,
            # for the next read to list.
            untold = snap.withdrawn_untold(reader, covered) if intent.node_id[1] is None else ()
            withdrawn = untold[:MAX_WITHDRAWALS_LISTED]
            for gone in withdrawn:
                concerns[gone.id] = dataclasses.replace(gone, withdrawal_told=True)
            # Each other session the reply names, once, in the order it first
            # names them: the link's other members, then the messages' senders.
            others = dict.fromkeys(
                [m for link in links for m in sorted(link.members) if m != reader]
                + [posted.sender[0] for posted in (*handed, *owed, *withdrawn)]
            )
            effects = (
                PeerInboxDelivered(
                    request_id=intent.request_id,
                    concerns=tuple(concerns[posted.id] for posted in handed),
                    held=tuple(c for c in waiting if c.id not in handed_ids),
                    links=links,
                    peers=tuple(peer_identity(snap, session) for session in others),
                    owed=owed,
                    withdrawn=withdrawn,
                    withdrawn_more=len(untold) - len(withdrawn),
                ),
            )

        case ConcernEdited():
            c = concerns.get(intent.concern_id)
            if c is not None and c.state is ConcernState.POSTED:
                concerns[intent.concern_id] = dataclasses.replace(c, body=intent.body, edited=True)

        case ConcernWithdrawn():
            # The operator's stops a concern still waiting and nothing else, and
            # is answered to nobody. An agent's is its sender's, as
            # ``_message_withdrawal_refusal`` rules it, and only a peer message
            # when the tool asked (``peer_only``). The reason is kept as "" when
            # none was given, so a reader tells it from one a restore did not
            # keep (``Concern.withdrawn_reason``).
            c = concerns.get(intent.concern_id)
            reason = intent.reason or ""
            if intent.node_id is None:
                if c is not None and c.state is ConcernState.POSTED:
                    concerns[intent.concern_id] = dataclasses.replace(
                        c, state=ConcernState.WITHDRAWN, withdrawn_reason=reason
                    )
            else:
                stays = _message_withdrawal_refusal(snap, intent.node_id, c)
                if stays is None and intent.peer_only and c is not None and not c.is_peer:
                    stays = PeerWithdrawRefusal.TEAM_MAIL
                if stays is None and c is not None:
                    concerns[intent.concern_id] = dataclasses.replace(
                        c, state=ConcernState.WITHDRAWN, withdrawn_reason=reason
                    )
                    after = dataclasses.replace(snap, concerns=MappingProxyType(concerns))
                    settled = _withdraw_settled(snap, after, intent.request_id or "", c)
                else:
                    settled = PeerWithdrawSettled(request_id=intent.request_id or "", refusal=stays)
                if intent.request_id is not None:
                    effects = (settled,)

        case TaskAmended():
            # Amends, never creates. A typo in a task id would otherwise put an
            # untitled, undeclared, unclaimable row on the board -- and it would be
            # indistinguishable from a real task nobody had got to yet.
            #
            # The claim is deliberately left alone. The worker holding this task is
            # the reader the amendment is *for*, so unclaiming to force a re-read
            # would take the work away from the one participant already doing it,
            # and `release_task` exists for the operator who actually wants that.
            #
            # A COMPLETED task is amended like any other. Its result keeps naming
            # the version it was written against (`Task.completed_version`), so
            # what a later reader has is a specification that moved on from what
            # was built, with both texts still there to compare.
            #
            # `released_by` is cleared, which is the one thing here that is not
            # simply left alone. An agent that gave this task back declined the
            # specification it was holding, and an amendment replaces exactly that
            # -- so the refusal it recorded was about an offer that no longer
            # exists, and continuing to skip the task on a bare claim would hold a
            # decision against a spec nobody can now read. It is also the only way
            # a task every agent has declined comes back into circulation without
            # somebody naming its id.
            #
            # The basis goes with the text it described, so it is replaced exactly
            # when that text is: the amender's own, which for the operator is
            # none. A marker left standing would be the previous author vouching
            # for words it did not write. An amendment carrying the specification
            # as it stands replaced none of them -- the editor always sends the
            # box, touched or not -- so the marker its author gave stays, and one
            # the amender sent with it is about no text of the amender's. An
            # amendment that carries no text at all is the same case, decided
            # against the text as it stands when this is applied.
            #
            # The property is replaced only when the amendment carries one. It is
            # the sentence the work is held to, the operator approved it at the
            # declaration, and rewriting the paragraph beside it does not withdraw
            # it; an amendment that means to change or remove it says so.
            #
            # A version is added only when the specification changed, text or
            # property. `claimed_version` is not touched: it is what the holder
            # was handed, and an amendment hands nobody anything.
            amended = tasks.get(intent.task_id)
            refused = (
                TaskRefusal.NO_SUCH_TASK
                if amended is None
                else _amender_refusal(amended, intent.node_id)
            )
            if amended is not None and refused is None:
                held_to = (
                    amended.property
                    if intent.property is None
                    else normalised_property(intent.property)
                )
                text = amended.detail if intent.detail is None else intent.detail
                reworded = text != amended.detail
                history = amended.superseded
                if reworded or held_to != amended.property:
                    history += (
                        TaskVersion(
                            detail=amended.detail,
                            property=amended.property,
                            basis=amended.basis,
                            replaced_at=now if intent.at is None else intent.at,
                            replaced_by=intent.node_id,
                        ),
                    )
                tasks[intent.task_id] = dataclasses.replace(
                    amended,
                    detail=text,
                    released_by=(),
                    basis=intent.basis if reworded else amended.basis,
                    property=held_to,
                    superseded=history,
                )
            effects = _settled(intent.request_id, refused)

        case TaskVersionHanded():
            # Only forward, only to a version the task has had, and only for the
            # node that holds it open: the stamp is a statement about that agent.
            # Versions count from 1, so a claim that recorded none bounds this
            # from below exactly as one that recorded the first does.
            shown = tasks.get(intent.task_id)
            if (
                shown is not None
                and shown.state is TaskState.CLAIMED
                and shown.claimed_by == intent.node_id
                and (shown.claimed_version or 1) <= intent.version <= shown.version
            ):
                tasks[intent.task_id] = dataclasses.replace(shown, claimed_version=intent.version)

        case TaskDeclared():
            # Who declared it comes from the intent, never from the record
            # handed in: the intent's node_id is the sender the gate
            # authenticated, and a declared_by a caller set on the Task is not
            # evidence of anything. Stamping unconditionally leaves one writer
            # for the field rather than two that have to agree.
            #
            # Touches are normalised here for the same reason: the overlap
            # check compares them, so a caller that could supply its own
            # spelling could evade the check by writing `./x.py`.
            #
            # A result is blanked on the same grounds as the declarer is
            # stamped: it is the claimer's account of finished work, a task
            # being declared has neither, and the completion arm is its one
            # writer. What that account left unrun goes with it.
            #
            # So is everything else an arm other than this one writes: the
            # state and the claimer, the completion stamp, who declined it and
            # when, the writes measured against it, and whether it was closed
            # or withdrawn and how that is attributed. A declaration is a
            # task nobody has touched, whatever the record handed in says.
            #
            # The property is put on one line here so every reader is handed
            # the same spelling whichever caller built the record.
            #
            # The refusal is asked of this same value. A cycle is looked for on
            # the declarer's board, and a gate's writers are read from touches
            # and state, so a check of the record as handed in would count a
            # touch of "." as a write, or pass a task said to be COMPLETED that
            # then lands PENDING inside a loop.
            requested = _as_asked(intent.task)
            landed = dataclasses.replace(
                requested,
                declared_by=intent.node_id,
                touches=normalised_touches(requested.touches),
                result="",
                result_basis=None,
                not_run=None,
                state=TaskState.PENDING,
                claimed_by=None,
                completed_at=None,
                released_by=(),
                released_at=None,
                writes=ApprovedWrites(),
                property=normalised_property(requested.property),
                superseded=(),
                claimed_version=None,
                held_by=(),
                closed_by_operator=False,
                withdrawn=False,
                withdrawn_by_lead=False,
                withdrawal_edited=False,
            )
            refused, cycle = _declaration_refusal(tasks, landed)
            auto: tuple[TaskId, ...] = ()
            behind: tuple[TaskId, ...] = ()
            if refused is None:
                # A dependency may name a task that is not declared yet, so an
                # overlapping task can already be waiting on this one. Queueing
                # this one behind it would leave both unclaimable for good, and
                # the order between them is already settled: this one first.
                overlapping = _auto_depends(tasks, landed)
                behind = _already_behind(tasks, landed, overlapping)
                auto = tuple(dep for dep in overlapping if dep not in behind)
                if auto:
                    landed = dataclasses.replace(landed, depends_on=(*landed.depends_on, *auto))
                tasks[landed.id] = landed
            held_off_fleet: str | None = None
            if refused is TaskRefusal.DUPLICATE_ID:
                holder = tasks[intent.task.id].declared_by
                if holder is not None and snap.off_fleet(holder[0]):
                    held_off_fleet = holder[0]
            effects = _settled(intent.request_id, refused, auto, behind, held_off_fleet, cycle)

        case TaskClaimRequested():
            # The claimer's own session, taken from the sender the gate
            # authenticated rather than from anything the model can set.
            won, turned_away = _pick_claim(tasks, intent.task_id, intent.node_id)
            if won is not None:
                # The version is stamped with the claim because the reply to this
                # intent is where the claimer is handed the specification, and
                # nothing later can say which one that was.
                holders = won.held_by
                if intent.node_id not in holders:
                    holders += (intent.node_id,)
                won = dataclasses.replace(
                    won,
                    state=TaskState.CLAIMED,
                    claimed_by=intent.node_id,
                    claimed_version=won.version,
                    held_by=holders,
                )
                tasks[won.id] = won
            # Emitted on both outcomes. A claim that found nothing must still be
            # answered, or the asking agent stays parked on a future forever over
            # an empty board -- which is the failure the whole effect channel
            # exists to make unstateable. A claim that won nothing says why,
            # because the reply is the only place the reducer's reason can go.
            effects = (ClaimSettled(request_id=intent.request_id, task=won, refusal=turned_away),)

        case TaskCompleted():
            # Guarded on the claimer so a stale completion from a worker that
            # released the task cannot finish it out from under its new owner.
            refused = _ownership_refusal(tasks, intent.task_id, intent.node_id)
            if refused is None:
                # The result is written here and by no other arm, under the same
                # guard as the state: it is the claimer's account, so it is taken
                # only from the node the guard has just established is the claimer.
                #
                # Blank is decided here, for whichever caller built the intent: a
                # result of nothing but whitespace is no result, and a list of
                # nothing but whitespace names nothing. Every reader tests these
                # two for truth, so a kept blank would be a heading over nothing
                # -- and, for the list, a row moved out of "unstated" by a claimer
                # that stated nothing.
                result = intent.result if intent.result.strip() else ""
                listed = intent.not_run is not None and bool(intent.not_run.strip())
                tasks[intent.task_id] = dataclasses.replace(
                    tasks[intent.task_id],
                    state=TaskState.COMPLETED,
                    completed_at=intent.at,
                    result=result,
                    # Kept only beside a result. A completion that reported
                    # nothing has no text for a standing to qualify.
                    result_basis=intent.basis if result else None,
                    # A stated "nothing was left unrun" is the empty string exactly
                    # and is about what the result names, so with no result it is
                    # dropped to unsaid. Text naming what was not run stands on its
                    # own and is kept.
                    not_run=(
                        intent.not_run if listed or (intent.not_run == "" and result) else None
                    ),
                )
            effects = _settled(intent.request_id, refused)

        case TaskReleased():
            refused = _ownership_refusal(tasks, intent.task_id, intent.node_id)
            # The one release that is not its holder's: a session's root giving
            # back a task whose holder is of an earlier run (``_may_reclaim``). Asked here and
            # not inside ``_ownership_refusal``, which the completion arm shares:
            # there it would let the root finish a gone holder's task, and the
            # row would carry the root's result under the holder's name. Deleting
            # these two lines is the whole of withdrawing it.
            if refused is TaskRefusal.NOT_YOURS and _may_reclaim(snap, intent):
                refused = None
            if refused is None:
                released = tasks[intent.task_id]
                # Recorded here because this arm is the only thing that knows both
                # the task and who is giving it back -- `claimed_by` is cleared on
                # the same line. Appended only if new, so an agent that claims and
                # releases the same task twice leaves one entry; see the field.
                #
                # On a reclaim that is the root and not the holder: the root is
                # the agent a bare claim would otherwise hand the task straight
                # back to, and the holder declined nothing.
                declined = released.released_by
                if intent.node_id not in declined:
                    declined += (intent.node_id,)
                tasks[intent.task_id] = dataclasses.replace(
                    released,
                    state=TaskState.PENDING,
                    claimed_by=None,
                    claimed_version=None,
                    released_by=declined,
                    # The emitter's clock when it gave one, so the stamp orders
                    # against a digest mark taken on the same clock in the same
                    # frame. The frame clock otherwise.
                    released_at=now if intent.at is None else intent.at,
                )
            effects = _settled(intent.request_id, refused)

        case TaskClosed():
            # No guard on who: the intent cannot name an agent
            # (``TaskClosed.node_id``). A finished task is left exactly as it is,
            # so a close cannot replace a claimer's result or an earlier close's
            # reason.
            #
            # A reason over the bound is not a close. Not cut either: the reason is
            # the task's result and what its holder is told, as typed or not at
            # all, and the box that sends this refuses a longer one first.
            closing = tasks.get(intent.task_id)
            if closing is None or closing.state is TaskState.COMPLETED:
                return snap, ()
            if len(intent.reason) > MAX_CLOSE_REASON_CHARS:
                return snap, ()
            closed_at = now if intent.at is None else intent.at
            tasks[intent.task_id] = dataclasses.replace(
                closing,
                state=TaskState.COMPLETED,
                completed_at=closed_at,
                result=closing_result(intent.reason),
                result_basis=None,
                not_run=None,
                # Nobody's completion, so nobody's row: a holder kept here would
                # be shown as the author of the operator's words, and
                # ``completed_version`` would name a text they were written
                # against. ``held_by`` still has the holder.
                claimed_by=None,
                claimed_version=None,
                closed_by_operator=True,
            )
            if closing.claimed_by is not None:
                # The holder is told, and by mail because that is the one thing
                # here a working agent comes to read: nothing in this arm, or
                # anywhere, stops it, and the row no longer names it. Filed as a
                # concern like any other, so it is delivered by ``InboxRead``,
                # shown on the board and written to the log.
                notice = closing_notice(closing, closing.claimed_by, intent.reason, closed_at)
                concerns.setdefault(notice.id, notice)

        case TaskWithdrawn():
            # The close's shape, under the declarer's guard instead of the
            # operator's construction. COMPLETED is what lets go of every task
            # waiting on this one: ``Task.blocked_on`` reads states, so there is
            # nothing else to update.
            #
            # The holder is dropped from the row as a close drops it, and for the
            # same reason: the declarer's words under a holder's name would be read
            # as the holder's completion. Its own later completion is refused
            # (``_ownership_refusal``), and ``held_by`` still has it.
            withdrawing = tasks.get(intent.task_id)
            refused = (
                TaskRefusal.NO_SUCH_TASK
                if withdrawing is None
                else _withdrawer_refusal(snap, withdrawing, intent.node_id, intent.reason)
            )
            if withdrawing is not None and refused is None:
                withdrawn_at = now if intent.at is None else intent.at
                # Past the guard, a withdrawer that is not the declarer is the
                # root standing in for one that stopped.
                by_lead = intent.node_id != withdrawing.declared_by
                tasks[intent.task_id] = dataclasses.replace(
                    withdrawing,
                    state=TaskState.COMPLETED,
                    completed_at=withdrawn_at,
                    result=withdrawal_result(intent.reason, by_lead, intent.edited),
                    result_basis=None,
                    not_run=None,
                    claimed_by=None,
                    claimed_version=None,
                    withdrawn=True,
                    withdrawn_by_lead=by_lead,
                    withdrawal_edited=intent.edited,
                )
                if withdrawing.claimed_by is not None:
                    # Told for the reason a close tells its holder: the files the
                    # task named are released while the holder may still be
                    # writing them, and the row no longer names it.
                    notice = withdrawal_notice(
                        withdrawing.id,
                        intent.node_id,
                        withdrawing.claimed_by,
                        intent.reason,
                        withdrawn_at,
                        by_lead,
                        intent.edited,
                    )
                    concerns.setdefault(notice.id, notice)
            effects = _settled(intent.request_id, refused)

        case DigestRead():
            # Projected from the pre-apply snapshot and from the asker's previous
            # mark, then the mark moves: the reply is the round that ends at this
            # read, and the next read starts where this one stopped. One arm for
            # the reason ``InboxRead`` is one.
            #
            # An asker with no record gets the whole session and leaves no mark,
            # since there is no record to keep one on. Nothing in the application
            # produces that -- a node that can call a bus tool was spawned -- so
            # it is the honest answer to a state only a test builds.
            asker = nodes.get(intent.node_id)
            effects = (
                DigestDelivered(
                    request_id=intent.request_id,
                    digest=digest(
                        snap, intent.node_id[0], None if asker is None else asker.digest_read_at
                    ),
                ),
            )
            if asker is not None:
                nodes[intent.node_id] = asker.with_(digest_read_at=intent.at)

        case BoardRestored():
            placed = _restored(snap, intent)
            if placed is None:
                return snap, ()
            tasks, concerns, decisions, restores = placed

        case DecisionRaised():
            unfiled = _raise_refusal(snap, intent)
            if unfiled is None:
                # Everything an arm other than this one writes is set here and
                # not read off the record handed in, as ``TaskDeclared`` does: the
                # asker is the node the gate authenticated, and a decision being
                # raised has no outcome and has been delivered to nobody. The
                # link is the live one the name resolves to now, which
                # ``_raise_refusal`` has just found joins the asker's session.
                raised_over = None if intent.channel is None else snap.live_channel(intent.channel)
                decisions = _filed(
                    decisions,
                    dataclasses.replace(
                        intent.decision,
                        asker=intent.node_id,
                        raised_at=now if intent.at is None else intent.at,
                        outcome=None,
                        first_delivered_at=None,
                        channel=None if raised_over is None else raised_over.name,
                        link=None if raised_over is None else raised_over.id,
                    ),
                )
            effects = _decision_settled(
                intent.request_id,
                unfiled,
                (
                    _link_peer(snap, intent.node_id[0], intent.channel)
                    if unfiled in (None, DecisionRefusal.LINKED_AFTER)
                    else None
                ),
            )

        case DecisionAnswered():
            ended = _ended_by_operator(
                decisions,
                intent,
                Answered(text=intent.answer, at=now if intent.at is None else intent.at),
            )
            if ended is None:
                return snap, ()
            decisions = ended
            effects = _decided(snap, decisions[intent.decision_id], WakeReason.DECISION_ANSWERED)

        case DecisionDismissed():
            ended = _ended_by_operator(
                decisions, intent, Dismissed(at=now if intent.at is None else intent.at)
            )
            if ended is None:
                return snap, ()
            decisions = ended
            effects = _decided(snap, decisions[intent.decision_id], WakeReason.DECISION_DISMISSED)

        case DecisionWithdrawn():
            kept = _withdrawal_refusal(decisions, intent)
            if kept is None:
                decisions = _filed(
                    decisions,
                    dataclasses.replace(
                        decisions[intent.decision_id],
                        outcome=Withdrawn(at=now if intent.at is None else intent.at),
                    ),
                )
            effects = _decision_settled(intent.request_id, kept)

        case DecisionsRead():
            # Answered from the pre-apply view, then marked, as ``InboxRead`` is.
            # The mark is a stamp and not a state: every later read is handed the
            # same records, so a reply that never reached the model costs the
            # asker nothing but a second call.
            #
            # Marked: the one decision the read named, and only when the reader
            # raised it. That is the one reply an answer is in. A list shows no
            # answer, and the root reading a sub-agent's is not the asker being
            # handed it -- a stamp from either would tell the operator an answer
            # had been read by an agent that never saw it.
            named = decisions.get(intent.decision_id) if intent.decision_id is not None else None
            if (
                named is not None
                and named.asker == intent.node_id
                and isinstance(named.outcome, Answered | Dismissed)
                and named.first_delivered_at is None
            ):
                decisions = _filed(
                    decisions, dataclasses.replace(named, first_delivered_at=intent.at)
                )
            readable = snap.decisions_readable_by(intent.node_id)
            effects = (
                DecisionsDelivered(
                    request_id=intent.request_id,
                    decisions=readable,
                    peers=tuple(
                        peer_identity(snap, session)
                        for session in dict.fromkeys(
                            d.session_id
                            for d in readable
                            if d.channel is not None and d.session_id != intent.node_id[0]
                        )
                    ),
                ),
            )

        case CommandRequested():
            unasked = _request_refusal(snap, intent)
            if unasked is None:
                # Built again from the id and the three texts and from nothing
                # else on the record handed in: the asker is the node the gate
                # authenticated, and a command being asked has no run, no stop,
                # no end, and has been delivered to nobody.
                commands = _with_command(
                    commands,
                    Command(
                        id=intent.command.id,
                        asker=intent.node_id,
                        asked=intent.command.asked,
                        reason=intent.command.reason,
                        expects=intent.command.expects,
                        asked_at=intent.at,
                    ),
                )
            effects = _command_settled(intent.request_id, intent.command.id, unasked)

        case CommandApproved():
            approved = _approved(snap, intent)
            if approved is None:
                return snap, ()
            commands = _with_command(commands, approved)
            effects = (CommandToRun(command=approved),)

        case CommandRefused():
            asked = commands.get(intent.command_id)
            if (
                intent.node_id is not None
                or asked is None
                or asked.state is not CommandState.ASKED
                or len(intent.reason) > MAX_COMMAND_REFUSAL_CHARS
            ):
                return snap, ()
            turned_down = _closed(asked, End(outcome=Refused(reason=intent.reason), at=intent.at))
            if turned_down is None:
                return snap, ()
            commands = _with_command(commands, turned_down)
            # A refusal ends the command as surely as a run does, and its asker
            # is waiting on it the same way.
            effects = _woken(asked.asker, WakeReason.COMMAND_ENDED, intent.command_id)

        case CommandWithdrawn():
            kept_open = _command_withdrawal_refusal(commands, intent)
            if kept_open is None:
                taken_back = _closed(
                    commands[intent.command_id],
                    End(outcome=CommandTakenBack(), at=intent.at),
                )
                if taken_back is None:
                    return snap, ()
                commands = _with_command(commands, taken_back)
            effects = _command_settled(intent.request_id, intent.command_id, kept_open)

        case CommandStopAsked():
            running = commands.get(intent.command_id)
            if (
                intent.node_id is not None
                or running is None
                or running.state is not CommandState.RUNNING
            ):
                return snap, ()
            effects = (CommandToStop(command_id=running.id),)
            if running.stop_asked_at is not None:
                # The first asking is the one recorded. The order is given
                # again: a kill is safe to repeat, and the operator pressing
                # twice is the operator not having seen it end.
                return snap, effects
            commands = _with_command(
                commands, dataclasses.replace(running, stop_asked_at=intent.at)
            )

        case CommandEnded():
            running = commands.get(intent.command_id)
            if intent.node_id is not None or running is None:
                return snap, ()
            # A running command ends, once. The one end that is replaced is
            # that of a command left running unsupervised, by another account
            # of the same kind: it was looked for again and found, or found
            # gone, or the operator released its session. Nothing else is said
            # twice about a command.
            looked_again = running.unsupervised is not None and isinstance(
                intent.end.outcome, EndUnknown
            )
            # A release lets go of what a survivor held, so only a command
            # found running unsupervised can be released.
            if (
                isinstance(intent.end.outcome, EndUnknown)
                and intent.end.outcome.released is not None
                and running.unsupervised is None
            ):
                return snap, ()
            if running.state is not CommandState.RUNNING and not looked_again:
                return snap, ()
            over = _closed(running, intent.end)
            if over is None or over == running:
                return snap, ()
            commands = _with_command(commands, over)
            # Woken once, when the command ends. Another account of an end
            # already given (``looked_again``) says nothing new to the asker.
            if running.state is CommandState.RUNNING:
                effects = _woken(running.asker, WakeReason.COMMAND_ENDED, intent.command_id)

        case CommandsRead():
            # Answered from the pre-apply view, then marked, as ``DecisionsRead``
            # is, and marked on the same terms: the one command the read named,
            # when the reader asked for it and it has ended.
            read = commands.get(intent.command_id) if intent.command_id is not None else None
            if (
                read is not None
                and read.asker == intent.node_id
                and read.state is CommandState.ENDED
                and read.first_delivered_at is None
            ):
                commands = _with_command(
                    commands, dataclasses.replace(read, first_delivered_at=intent.at)
                )
            effects = (
                CommandsDelivered(
                    request_id=intent.request_id,
                    commands=snap.commands_readable_by(intent.node_id),
                ),
            )

        case CommandsRestored():
            placed_commands = _with_restored(commands, intent)
            if placed_commands is None:
                return snap, ()
            commands = placed_commands

        case SessionRecalled():
            # Never over anything. A record under the id -- the root's, or a
            # sub-agent's that reached the store ahead of it -- is something
            # this process saw, and the file's account does not replace it.
            #
            # Nor where a decision or a command is open under the id with no
            # record to its name: somebody asked it, and a record built here
            # would be owed for at once and so not be a recalled one.
            if (
                any(nid[0] == intent.session_id for nid in nodes)
                or any(d.is_open and d.session_id == intent.session_id for d in decisions.values())
                or snap.commands_awaiting(intent.session_id)
            ):
                return snap, ()
            recalled_root: NodeId = (intent.session_id, None)
            nodes[recalled_root] = AgentRecord(
                node_id=recalled_root,
                parent=None,
                depth=0,
                # DONE and not FAILED whatever the file says of how it ended:
                # FAILED owes the operator a row and a status update revives it,
                # and neither is true of a session with no process.
                state=AgentState.DONE,
                topic=RECALLED_TOPIC,
                task=intent.task,
                model=intent.model,
                template=intent.template,
                brief=intent.brief,
                cwd=intent.cwd,
                session_base=intent.session_base,
                recalled=intent.recalled,
            )
            reorder = True

        case SessionDetached():
            unmoved = snap.detach_refusal(intent.session_id)
            effects = (
                SessionDetachSettled(
                    request_id=intent.request_id,
                    session_id=intent.session_id,
                    refusal=unmoved,
                ),
            )
            leaving = nodes.get((intent.session_id, None))
            if unmoved is not None or leaving is None or leaving.detached:
                return snap, effects
            nodes[leaving.node_id] = leaving.with_(detached=True)

        case FailedSessionClosed():
            failed = nodes.get(intent.node_id)
            if (
                intent.run_held
                or failed is None
                or intent.node_id[1] is not None
                or failed.state is not AgentState.FAILED
            ):
                return snap, ()
            # What ``AgentFinished`` makes of a close the pool reports, less the
            # time: the session ended when it failed. Closing it is also the
            # dismissal of its failure, as it is there. No approval can be
            # parked on it to be dropped: a node enters FAILED with ``pending``
            # cleared and leaves it when one arrives (``_needs_you``).
            nodes[intent.node_id] = failed.with_(
                state=AgentState.DONE, error=None, acknowledged=True
            )

        case ChannelLinked():
            if snap.link_refusal(intent.channel_id, intent.name, intent.members, intent.recording):
                return snap, ()
            channels = MappingProxyType(
                {
                    **channels,
                    intent.channel_id: Channel(
                        id=intent.channel_id,
                        name=intent.name,
                        members=intent.members,
                        charter=intent.charter,
                        created_at=intent.at,
                    ),
                }
            )
            channel_since = MappingProxyType({**channel_since, intent.channel_id: now})
            nodes = _peer_rows(dataclasses.replace(snap, channels=channels), nodes)

        case ChannelClosed():
            open_ = channels.get(intent.channel_id)
            if open_ is None or not open_.live:
                return snap, ()
            channels = MappingProxyType(
                {**channels, open_.id: dataclasses.replace(open_, closed_at=intent.at)}
            )
            nodes = _peer_rows(dataclasses.replace(snap, channels=channels), nodes)

        case ChannelsRestored():
            unheld = {c.id: c for c in intent.channels if c.id not in channels}
            if not unheld:
                return snap, ()
            channels = MappingProxyType({**channels, **unheld})
            channel_since = MappingProxyType({**channel_since, **{cid: now for cid in unheld}})
            nodes = _peer_rows(dataclasses.replace(snap, channels=channels), nodes)

        case SessionSettled() | WakeChainReleased() | PeerWakeSent() | DecisionWakeSent():
            # Nothing of the world moves: what each does to waking is
            # ``_peer_waking``'s, below.
            pass

        case PeerQuestionAged():
            # Kept only while the question is owed an answer: ``_owing_peer_mail``
            # drops it otherwise, so an answer that crossed this word wins.
            questions_aged = questions_aged | {intent.concern_id}

        case HeldMailDismissed():
            # Kept only while the mail is held (``_owing_peer_mail``).
            held_dismissed = held_dismissed | intent.concerns

        case PeerQuestionDismissed():
            # Kept only while the same row would still be raised
            # (``_owing_peer_mail``).
            questions_dismissed = questions_dismissed | {(intent.concern_id, intent.running)}

        case _:
            # Not reachable at runtime; it is here for mypy, which reports an
            # unhandled Intent member as a type error at this line. That turns
            # "someone added an intent and forgot to handle it" -- a silently
            # ignored state change, and a miserable thing to debug from the UI --
            # into a failed typecheck.
            assert_never(intent)

    # Stamp the clock once, here, rather than in each arm that assigns a state.
    #
    # Six arms can change a node's state and three of them do it as a side effect of
    # doing something else. Asking each to remember a timestamp is the same shape as
    # every sync bug in this codebase's history: a fact maintained in several places
    # by convention. Comparing before and after cannot be forgotten by an arm that
    # has not been written yet.
    # Guarded on None because the bus intents include operator actions, which name
    # no node. The guard lives here rather than in each new arm for the same reason
    # the stamp does: an arm that has not been written yet cannot forget it.
    node_id = intent.node_id
    touched = nodes.get(node_id) if node_id is not None else None
    if touched is not None and node_id is not None:
        was = snap.nodes.get(node_id)
        if was is None or was.state is not touched.state:
            nodes[node_id] = touched.with_(state_since=now)

    for owing in _no_longer_recalled(nodes, decisions, commands):
        nodes[owing] = nodes[owing].with_(recalled=None)
        reorder = True

    # After every arm, whatever the intent: a parked row describes its lead
    # from that lead's root record, and any step that adds, drops or rewrites a
    # root can change the description.
    if _redescribed(snap, nodes):
        nodes = _peer_rows(
            dataclasses.replace(snap, nodes=MappingProxyType(nodes), channels=channels), nodes
        )

    order = _preorder(nodes) if reorder else snap.order
    world, woken = _peer_waking(
        snap,
        intent,
        Snapshot(
            seq=snap.seq + 1,
            nodes=MappingProxyType(nodes),
            order=order,
            needs_you=_needs_you(nodes, order),
            any_active=any(r.state.is_active for r in nodes.values()),
            concerns=MappingProxyType(concerns),
            tasks=MappingProxyType(tasks),
            unattributed_writes=unattributed,
            restores=restores,
            decisions=decisions,
            commands=commands,
            command_since=_command_since(snap, commands, now),
            channels=channels,
            channel_since=channel_since,
            peer_wakes=snap.peer_wakes,
            questions_aged=questions_aged,
            held_dismissed=held_dismissed,
            questions_dismissed=questions_dismissed,
        ),
        now,
    )
    return (
        _back_on_fleet(
            _owing_peer_mail(_owing_wake_chains(_owing_commands(_owing_decisions(world))))
        ),
        effects + woken,
    )
