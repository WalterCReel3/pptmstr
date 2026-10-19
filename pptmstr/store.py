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
import itertools
import math
import re
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import assert_never

from . import drawable
from .approval import Policy
from .board import ROOT_NAMES, board_concerns, board_tasks, role_name
from .boardlog import Board, Party, RestoredConcern, RestoredTask
from .command import (
    MAX_OPEN_PER_ASKER,
    MAX_OPEN_SUBAGENTS,
    Command,
    CommandId,
    CommandState,
    End,
    EndUnknown,
    Refused,
    RequestRefusal,
    Run,
    is_valid_id,
    request_refusal,
)
from .command import Withdrawn as CommandTakenBack
from .digest import digest
from .effects import (
    BoardDelivered,
    ClaimSettled,
    CommandsDelivered,
    CommandSettled,
    CommandToRun,
    CommandToStop,
    DecisionsDelivered,
    DecisionSettled,
    DigestDelivered,
    Effect,
    InboxDelivered,
    SessionDetachSettled,
    SessionWoken,
    TaskWriteSettled,
    WakeReason,
)
from .intents import (
    AgentFinished,
    AgentRemoved,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    BoardRead,
    BoardRestored,
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
    DecisionWithdrawn,
    DigestRead,
    FailedSessionClosed,
    FailureAcknowledged,
    InboxRead,
    Intent,
    SessionDetached,
    SessionRecalled,
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
)
from .model import (
    ALL_WRITERS,
    FINAL_STATES,
    INTERRUPTED_TOPIC,
    MAX_ANSWER_CHARS,
    MAX_CLOSE_REASON_CHARS,
    MAX_COMMAND_REFUSAL_CHARS,
    MAX_OPEN_DECISIONS_PER_ASKER,
    MAX_OPEN_SUBAGENT_DECISIONS,
    MAX_WITHDRAWAL_REASON_CHARS,
    RECALLED_TOPIC,
    AgentRecord,
    AgentState,
    Answered,
    ApprovalNeeded,
    ApprovedWrites,
    AskerFate,
    Basis,
    ClaimRefusal,
    CommandOwed,
    CommandRefusal,
    Concern,
    ConcernId,
    ConcernState,
    Decision,
    DecisionId,
    DecisionOutcome,
    DecisionOwed,
    DecisionRefusal,
    Dismissed,
    NodeId,
    NotPlaced,
    Obligation,
    PendingApproval,
    QuestionPending,
    Recalled,
    Restore,
    SessionFailed,
    Snapshot,
    Standing,
    Task,
    TaskId,
    TaskRefusal,
    TaskState,
    TaskVersion,
    Unplaced,
    Withdrawn,
    approved_write,
    closing_notice,
    closing_result,
    is_operator,
    normalised_property,
    normalised_touches,
    restored_party,
    stored_as_sent,
    withdrawal_notice,
    withdrawal_result,
)
from .transcript import Transcript

# The ends an ordinary status update cannot undo. ``model.FINAL_STATES`` says
# which and why; the arms below ask it under this name.
_FINAL_STATES = FINAL_STATES

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
#
# A board log is not here because no field of it is: it is the store's own board,
# written drawable and read back so (``_drawable``).
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
    own board was written to, so its text is drawable already, and a second pass
    would write each backslash ``text`` wrote once more: the board would read as
    nothing an agent wrote, and over the store that wrote it, as a different board
    (``Unplaced.STORE_DIFFERS``), withholding the whole restore. A log that holds
    what the store never writes is checked for where it is placed
    (``Unplaced.UNDRAWABLE``).
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
    so the match below is the single audit point for every mutation in the system.

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

        case ConcernEdited():
            c = concerns.get(intent.concern_id)
            if c is not None and c.state is ConcernState.POSTED:
                concerns[intent.concern_id] = dataclasses.replace(c, body=intent.body, edited=True)

        case ConcernWithdrawn():
            c = concerns.get(intent.concern_id)
            if c is not None and c.state is ConcernState.POSTED:
                concerns[intent.concern_id] = dataclasses.replace(c, state=ConcernState.WITHDRAWN)

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
            # Provenance comes from the intent, never from the record handed
            # in: the intent's node_id is the sender the gate authenticated,
            # and a declared_by a caller set on the Task is not evidence of
            # anything. Stamping unconditionally leaves one writer for the
            # field rather than two that have to agree.
            #
            # Touches are normalised here for the same reason: the overlap
            # check compares them, so a caller that could supply its own
            # spelling could evade the check by writing `./x.py`.
            #
            # A result is blanked on the same grounds as the provenance is
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
            tasks, concerns, restores = placed

        case DecisionRaised():
            unfiled = _raise_refusal(snap, intent)
            if unfiled is None:
                # Everything an arm other than this one writes is set here and
                # not read off the record handed in, as ``TaskDeclared`` does: the
                # asker is the node the gate authenticated, and a decision being
                # raised has no outcome and has been delivered to nobody.
                decisions = _filed(
                    decisions,
                    dataclasses.replace(
                        intent.decision,
                        asker=intent.node_id,
                        raised_at=now if intent.at is None else intent.at,
                        outcome=None,
                        first_delivered_at=None,
                    ),
                )
            effects = _decision_settled(intent.request_id, unfiled)

        case DecisionAnswered():
            ended = _ended_by_operator(
                decisions,
                intent,
                Answered(text=intent.answer, at=now if intent.at is None else intent.at),
            )
            if ended is None:
                return snap, ()
            decisions = ended
            effects = _woken(
                decisions[intent.decision_id].asker,
                WakeReason.DECISION_ANSWERED,
                intent.decision_id,
            )

        case DecisionDismissed():
            ended = _ended_by_operator(
                decisions, intent, Dismissed(at=now if intent.at is None else intent.at)
            )
            if ended is None:
                return snap, ()
            decisions = ended
            effects = _woken(
                decisions[intent.decision_id].asker,
                WakeReason.DECISION_DISMISSED,
                intent.decision_id,
            )

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
            effects = (
                DecisionsDelivered(
                    request_id=intent.request_id,
                    decisions=snap.decisions_readable_by(intent.node_id),
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

    order = _preorder(nodes) if reorder else snap.order
    return (
        _back_on_fleet(
            _owing_commands(
                _owing_decisions(
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
                    )
                )
            )
        ),
        effects,
    )


def _no_longer_recalled(
    nodes: Mapping[NodeId, AgentRecord],
    decisions: Mapping[DecisionId, Decision],
    commands: Mapping[CommandId, Command],
) -> list[NodeId]:
    """
    The recalled records something is owed for: one carrying an approval, or the
    asker of an open decision or an open command.

    Each stops being recalled. An approval is a call some process is parked on
    and a decision or a command is something some process asked, so the record
    is no longer one that nothing here stands behind, and a card that says it
    has no process while it asks for a review is the worse of the two wrong
    cards. None is dropped: an approval never is.

    These are the obligations a record in DONE can come to have, which is why
    they are the ones looked for. Asked once per step and not in the arms
    that add them, for the reason ``_back_on_fleet`` gives.
    """
    recalled = [nid for nid, rec in nodes.items() if rec.recalled is not None]
    if not recalled:
        return []
    asking = {d.asker for d in decisions.values() if d.is_open}
    asking |= {c.asker for c in commands.values() if c.is_open}
    return [nid for nid in recalled if nodes[nid].pending or nid in asking]


def _back_on_fleet(world: Snapshot) -> Snapshot:
    """
    ``world`` with ``detached`` cleared on every root whose session may no longer
    be off the fleet (``Snapshot.detach_refusal``).

    A step over the built snapshot for the reason ``state_since`` is stamped in
    one place: a session comes back by an agent of it running again, by an
    approval or a decision landing on it, by a new sub-agent, and an arm that
    has not been written yet cannot forget to clear a flag it never reads.

    ``Snapshot.off_fleet`` would already answer False for such a session. The
    flag is cleared as well so that the session does not go back off the fleet
    by itself when the agent that revived it ends: it left because the operator
    said so about a session that was over, and that is no longer the session
    they said it about.
    """
    back = [
        nid
        for nid, rec in world.nodes.items()
        if rec.detached and world.detach_refusal(nid[0]) is not None
    ]
    if not back:
        return world
    nodes = dict(world.nodes)
    for nid in back:
        nodes[nid] = nodes[nid].with_(detached=False)
    return dataclasses.replace(world, nodes=MappingProxyType(nodes))


def _settled(
    request_id: str | None,
    refusal: TaskRefusal | None,
    auto_depends: tuple[TaskId, ...] = (),
    already_behind: tuple[TaskId, ...] = (),
    held_off_fleet: str | None = None,
    cycle: tuple[TaskId, ...] = (),
) -> tuple[Effect, ...]:
    """
    The reply to a board write, or nothing when nobody asked.

    Emitted on *both* outcomes when a request id is present, for the reason the
    ``TaskClaimRequested`` arm gives: an agent parked on a future over a write the
    reducer silently dropped is the same hang, and the only difference is that the
    write's caller used to invent a success instead of waiting.

    ``auto_depends`` rides on the success answer because a dependency the store
    added is a change to what the declarer asked for. Applying it silently would
    make the board disagree with the lead's own plan with nothing saying so -- and
    "your task is waiting" arriving later, from a worker that cannot claim it, is
    the same information at the worst moment. ``already_behind`` rides with it for
    the reason ``effects.TaskWriteSettled`` gives.
    """
    if request_id is None:
        return ()
    return (
        TaskWriteSettled(
            request_id=request_id,
            refusal=refusal,
            auto_depends=auto_depends,
            already_behind=already_behind,
            held_off_fleet=held_off_fleet,
            cycle=cycle,
        ),
    )


def _auto_depends(tasks: dict[TaskId, Task], new: Task) -> tuple[TaskId, ...]:
    """
    Unfinished tasks on the same board that will write a file ``new`` also writes.

    **The collision becomes a wait rather than a refusal**, which is the whole
    design. Refusing would need the declarer to notice, re-plan and re-declare, and
    would put a second thing in the reply channel that can be ignored; appending a
    dependency produces exactly the sequencing the lead should have written by hand,
    and produces it whether or not the lead was paying attention. Nothing is
    rejected, so nothing is lost.

    That matters more than it sounds. ``depends_on`` is the *entire* mechanism
    keeping two agents out of one file, it is mechanical and unbypassable, and it
    was also opt-in and invoked by hand by the one participant whose mistakes
    nothing else checks. This is what stops it being opt-in.

    **An edge to one of these can close a cycle**, and ``_would_cycle`` passing
    on the declared dependencies does not rule it out. ``new`` is not in
    ``tasks`` (a duplicate is refused before this runs), but a dependency may
    name an id before it is declared, so an older task can already be waiting on
    ``new`` -- and an edge from ``new`` to that task is a loop. This function
    answers only which tasks overlap. The declaration arm takes out the ones
    ``_already_behind`` names before it adds any edge, and the restore
    (``_as_declared``) wants the overlap whole.

    Scoped to the declarer's own session, like everything else about a board. An
    edge to another session's task would be one nothing could ever satisfy: a
    dependency is looked up on the task's own board (``model.Task.dependency``),
    so an id only another board has reads as missing, the row says it waits on
    an id another session holds (``board.foreign_wait_label``), and no
    completion over there releases it. Adding that automatically would strand
    the task being declared, which is worse than the concurrent write. The
    honest limit: **two sessions in one working directory get no protection from
    this**, and that is a real gap rather than a solved case.

    COMPLETED tasks are skipped because a finished task is not writing anything.
    Ordering is by declaration so the added edges are stable across runs.
    """
    if not new.touches or new.declared_by is None:
        return ()
    session_id = new.declared_by[0]
    mine = set(new.touches)
    already = set(new.depends_on)
    overlapping = [
        t
        for t in tasks.values()
        if t.id != new.id
        and t.id not in already
        and t.state is not TaskState.COMPLETED
        and t.belongs_to(session_id)
        and mine.intersection(t.touches)
    ]
    return tuple(t.id for t in sorted(overlapping, key=lambda t: (t.declared_at, t.id)))


def _already_behind(
    tasks: dict[TaskId, Task], new: Task, overlapping: tuple[TaskId, ...]
) -> tuple[TaskId, ...]:
    """
    Those of ``overlapping`` that already wait on ``new``, directly or through
    other tasks, in the order given.

    ``new`` may not be made to wait on any of them: each such edge is a cycle,
    and every task on it is unclaimable for good with nothing refused. Nor does
    it need to. A task that waits on ``new`` cannot be claimed until ``new`` is
    finished, so the two are already kept out of the shared file at once -- in
    the order the declarer's own dependencies set, which is the reverse of the
    one ``_auto_depends`` would have added.

    Each is asked separately and that is enough: a cycle through ``new`` has to
    come back to ``new``, and whether a task reaches it does not depend on which
    edges ``new`` is about to be given.
    """
    return tuple(
        dep
        for dep in overlapping
        if _would_cycle(tasks, dataclasses.replace(new, depends_on=(dep,)))
    )


def _as_asked(task: Task) -> Task:
    """
    ``task`` with ``ALL_WRITERS`` taken out of ``depends_on`` and stated as
    ``after_all_writers``. A task that does not name it is returned unchanged.

    The word names a set and not a task, so it is not kept as a dependency id:
    left in, ``blocked_on`` would look it up and report a missing task. Done in
    the arm, so every emitter of ``TaskDeclared`` gets the same reading, a dry
    run included.
    """
    if ALL_WRITERS not in task.depends_on:
        return task
    return dataclasses.replace(
        task,
        depends_on=tuple(d for d in task.depends_on if d != ALL_WRITERS),
        after_all_writers=True,
    )


def _declaration_refusal(
    tasks: dict[TaskId, Task], new: Task
) -> tuple[TaskRefusal | None, tuple[TaskId, ...]]:
    """
    Why ``new`` may not go on the board, or None if it may, and beside a
    ``WOULD_CYCLE`` the cycle ``_cycle`` found.

    Re-declaring an existing id is refused rather than merged: the only way it
    happens is a retry, and overwriting would silently unclaim work somebody is
    doing. A cycle is refused for the reason ``_would_cycle`` gives. The id
    ``ALL_WRITERS`` is refused because ``depends_on`` could never name it, and
    an id the store would not hold as sent because no later call could
    (``model.stored_as_sent``; ``new.id`` has been through ``drawable.text``
    already, which that predicate answers for alike). So is such an id in
    ``depends_on``: it names a task no declaration can put on the board, so
    ``new`` would wait on it for good.

    ``new`` is the task as it would land, so what is checked is what is kept.

    The answer is a value the caller has to look at, rather than a dropped write
    the caller reports as a success.
    """
    if new.id == ALL_WRITERS:
        return TaskRefusal.RESERVED_ID, ()
    if not stored_as_sent(new.id) or not all(stored_as_sent(d) for d in new.depends_on):
        return TaskRefusal.ESCAPED_ID, ()
    if new.id in tasks:
        return TaskRefusal.DUPLICATE_ID, ()
    cycle = _cycle(tasks, new)
    if cycle:
        return TaskRefusal.WOULD_CYCLE, cycle
    return None, ()


def _amender_refusal(task: Task, amender: NodeId | None) -> TaskRefusal | None:
    """
    Why ``amender`` may not replace ``task``'s specification, or None if it may.

    None is the operator, who always may. An agent may when it declared the task
    and has never held it; ``intents.TaskAmended`` argues why. **The first two
    lines after the operator's are the whole of what lets an agent amend at
    all**: returning ``NOT_THE_DECLARER`` for every agent restores an
    operator-only amendment.

    "Held" is ``Task.held_by``: the claim it has now, a claim it finished under
    -- so a declarer that built the task cannot afterwards rewrite the
    specification to match what it built -- and a claim it gave back, however
    long ago and whoever has amended the task since.

    A task on another session's board is answered as an id nothing has, which
    is what a claim of it and a read of it are answered: "you did not declare
    it" would report what is on a board the asker was never shown. A task with
    no recorded declarer is on no session's board (``Task.belongs_to``), so it
    gets the same answer from every agent, and the operator still can amend it.
    Nothing in the application produces one.
    """
    if amender is None:
        return None
    if not task.belongs_to(amender[0]):
        return TaskRefusal.NO_SUCH_TASK
    if task.declared_by != amender:
        return TaskRefusal.NOT_THE_DECLARER
    if amender in task.held_by:
        return TaskRefusal.AMENDER_HELD_IT
    return None


def _withdrawer_refusal(
    snap: Snapshot, task: Task, withdrawer: NodeId, reason: str
) -> TaskRefusal | None:
    """
    Why ``withdrawer`` may not withdraw ``task`` with ``reason``, or None if it may.

    In the order an agent can act on the answers: is it on my board, is it mine
    to withdraw, does somebody hold it that I may not take it from, is there
    anything left to withdraw, did I say why.

    **The second and third lines are the whole of who may.** The declarer as the gate stamped
    it -- not "has never held it", which is the amendment's rule and would refuse
    the declarer that amended the task and so cannot claim it, the agent this
    exists for. Nor the operator's close, which takes no node and is a different
    intent. And the root of the task's own session once the declarer has
    stopped (``_root_of_its_session``, ``_has_stopped``): otherwise a builder
    that declared follow-up work and returned leaves rows that only a person can
    retire. A declarer that is still running keeps the sole right, and no other
    sub-agent may ever withdraw another's task.

    **The root standing in takes no claim it could not take back.** A
    withdrawal ends the holder's claim and refuses its next ``complete_task``,
    which is more than a release does, so the root's withdrawal is held to the
    release's rule (``_may_take_claim``): refused while an agent it could not
    reclaim from holds the task -- the declarer itself included, when it
    claimed its own task and returned to wait -- unless the holder is the root.
    The declarer's own withdrawal is not: the task was its to call off, and
    its holder is told (``withdrawal_notice``).

    A task on another session's board is answered as an id nothing has, for the
    reason ``_amender_refusal`` gives.
    """
    if not task.belongs_to(withdrawer[0]):
        return TaskRefusal.NO_SUCH_TASK
    if task.declared_by != withdrawer and not (
        task.declared_by is not None
        and _root_of_its_session(task, withdrawer)
        and _has_stopped(snap, task.declared_by)
    ):
        return TaskRefusal.NOT_THE_WITHDRAWER
    if (
        task.declared_by != withdrawer
        and task.state is TaskState.CLAIMED
        # The root's own claim is no one else's work taken: it is the agent
        # acting, and the holder this protects is another one.
        and task.claimed_by != withdrawer
        and not _may_take_claim(snap, task, withdrawer)
    ):
        return TaskRefusal.WITHDRAW_HELD_LIVE
    if task.state is TaskState.COMPLETED:
        return TaskRefusal.WITHDRAW_FINISHED
    if not reason.strip() or len(reason) > MAX_WITHDRAWAL_REASON_CHARS:
        return TaskRefusal.NO_WITHDRAWAL_REASON
    return None


def _has_stopped(snap: Snapshot, node: NodeId) -> bool:
    """
    Whether ``node`` is an agent nothing will hear from again without somebody
    starting it: of an earlier run (``Snapshot.of_earlier_run``, the reclaim's
    rule), or a sub-agent of this run whose record is terminal.

    **Wider than the reclaim's rule, and deliberately.** A sub-agent of this run
    that finished its turn can be woken, and may be holding a task across a
    wait: that is why ``_may_reclaim`` does not take a claim from it, since that
    would take the work out of a live holder's hands. A declarer that holds
    nothing gives nothing up when its task is withdrawn -- it is not bound by
    its own task -- so for it, what stands between the root and the row is only
    whether it is still there to do it itself. A declarer that holds its own
    task is a holder as well, and ``_withdrawer_refusal`` asks the reclaim's
    rule of every holder. A node with no record that no log named has not been
    announced yet, and may be working, so it is not stopped.
    """
    if snap.of_earlier_run(node):
        return True
    record = snap.nodes.get(node)
    return node[1] is not None and record is not None and record.state.is_terminal


def _claim_refusal(task: Task, claimer: NodeId) -> TaskRefusal | None:
    """
    Why ``claimer`` may not take ``task`` although it is there and ready, or None.

    ``_amender_refusal`` from the other side: an agent that wrote a version of a
    task's specification may not then be the one bound by it. Without this the
    pair is one rule with a way round it -- amend first, claim second.

    One reason today. It is a function of its own so that a claim turned away for
    a reason an agent can act on has somewhere to be named apart from "nothing is
    claimable".
    """
    return TaskRefusal.CLAIMER_AMENDED_IT if task.was_amended_by(claimer) else None


def _ownership_refusal(
    tasks: dict[TaskId, Task], task_id: TaskId, node_id: NodeId
) -> TaskRefusal | None:
    """
    Why ``node_id`` may not finish or release ``task_id``, or None if it may.

    Shared by the two arms that have the same guard, so the refusal a worker is
    given for completing someone else's task and the one it is given for releasing
    it cannot drift apart.

    The order of the tests is the order of the questions: does it exist, is it over,
    is anyone holding it, is that me. A COMPLETED task is separated from a PENDING
    one because both would otherwise read as "not claimed" while meaning opposite
    things -- one says stop, the other says claim it first.
    """
    t = tasks.get(task_id)
    if t is None:
        return TaskRefusal.NO_SUCH_TASK
    if t.state is TaskState.COMPLETED:
        # To anyone, and not only to whoever held it: the row says who closed
        # it, and "already complete" would have a second agent conclude the work
        # was done.
        if t.closed_by_operator:
            return TaskRefusal.CLOSED_BY_OPERATOR
        if t.withdrawn:
            return TaskRefusal.WITHDRAWN
        return TaskRefusal.ALREADY_COMPLETE
    if t.state is not TaskState.CLAIMED:
        return TaskRefusal.NOT_CLAIMED
    if t.claimed_by != node_id:
        return TaskRefusal.NOT_YOURS
    return None


def _claimed_tasks(tasks: dict[TaskId, Task], node_id: NodeId) -> tuple[Task, ...]:
    """
    Every task ``node_id`` is holding open. Empty and many are both ordinary.

    **Holding none is not an error.** A lead editing a file outside any claimed task
    is the common case, and there is nothing here to hang a measurement on; inventing
    a task to carry it would put a row on the board that no agent declared.

    CLAIMED only. ``TaskCompleted`` keeps ``claimed_by`` -- who did the work is worth
    reading after the fact, and ``BoardTask.owner_gone`` relies on it -- so the state
    rather than the owner is what says the work is still open. Filtering on
    ``claimed_by`` alone would return the node's first-ever task, completed, forever,
    since ``tasks`` is in declaration order.

    **Every match is returned rather than one being chosen.** Nothing in the snapshot
    says which of two open tasks a given write was for, and picking by declaration
    order would put a made-up attribution into the one series this measurement exists
    to produce. The caller records the ambiguity instead
    (``Snapshot.unattributed_writes``); how often it fires is itself a reading, since
    "one task at a time" is prose in ``templates.py`` that ``_pick_claim`` does not
    enforce.
    """
    return tuple(
        t for t in tasks.values() if t.state is TaskState.CLAIMED and t.claimed_by == node_id
    )


def _pick_claim(
    tasks: dict[TaskId, Task], task_id: TaskId | None, node: NodeId
) -> tuple[Task | None, ClaimRefusal | None]:
    """
    The task this claim wins and None, or None and why it won nothing.

    One function decides both, so the reason an agent is given cannot be a
    second opinion about a claim the first one turned away. For a named task the
    questions are asked in the order an agent can act on the answers: is it on my
    board, is it over, does somebody have it, is it waiting, may I.

    Serialization is what makes this safe, and it is the store's confinement to one
    thread that provides it -- not a lock, and not the file lock agent teams needs
    for the same job across processes. Two workers racing for the last task are two
    intents in a queue, and the second one reads the first one's result.

    **Scoped to the claimer's own session.** ``tasks`` is fleet-wide -- one ``Store``
    serves every session -- and a worker handed a task from outside its session
    would be working something that appears on no board it or its operator can
    see. This and ``board.board_tasks`` both ask ``Task.belongs_to``, which makes
    the two answers one answer rather than two that agree by inspection.

    Blocked-ness is scoped the same way, by the task and not by narrowing the
    map: ``Task.blocked_on`` is handed the whole of ``tasks`` and resolves each
    dependency on the task's own board (``Task.dependency``). A dependency on an
    id only another session has is missing, so the task waits and its row says
    on what. It is not satisfied by the stranger's completion: that would make a
    task claimable because of work its board never showed.

    **A bare claim skips what this node has already released, and a claim by id
    does not.** Releasing does not change a task's age, so without the skip the
    oldest claimable task is the one the caller just gave back and a bare claim
    hands it straight over again. Two agents can hold that between them
    indefinitely and neither can see it from its own transcript, because each sees
    an ordinary claim of an ordinary task.

    The skip is asymmetric on purpose. Naming the id is an agent saying it means
    this one, which is the only way back to a task it declined and the reason a
    declined task is never lost: it stays on the board, claimable by every other
    agent and takeable by name by anyone. A filter that applied to both would trade
    a livelock for a task nothing can pick up, and an unattended board cannot
    report that.

    **A task the claimer amended is skipped by both forms** (``_claim_refusal``).
    The agents that turns away are the ones that wrote a version of the
    specification, and every other agent of the session can still take the task.
    **So the task strands exactly when no other agent does**: its amenders are
    the only agents left that would work it, as a lead that amended and has no
    worker to hand it to is. It then stays PENDING, and every task ``_auto_depends``
    queued behind its ``touches`` stays blocked with it. The operator's
    amendment does not end that: it adds a version and leaves the agent's in
    ``Task.superseded``, which is what ``Task.was_amended_by`` reads. Two things
    do: another agent of the same session claiming it by id, and the operator
    closing it (``TaskClosed``), which lets go of what waits on it without
    anybody working it.
    """
    session_id = node[0]
    if task_id is not None:
        t = tasks.get(task_id)
        if t is None or not t.belongs_to(session_id):
            return None, ClaimRefusal.NOT_ON_YOUR_BOARD
        if t.state is TaskState.COMPLETED:
            # Apart for ``_ownership_refusal``'s reason: only the first was done.
            if t.closed_by_operator:
                return None, ClaimRefusal.CLOSED
            if t.withdrawn:
                return None, ClaimRefusal.WITHDRAWN
            return None, ClaimRefusal.COMPLETE
        if t.state is TaskState.CLAIMED:
            mine = t.claimed_by == node
            return None, ClaimRefusal.ALREADY_YOURS if mine else ClaimRefusal.HELD
        if not t.is_claimable(tasks):
            return None, ClaimRefusal.WAITING
        if _claim_refusal(t, node) is not None:
            return None, ClaimRefusal.AMENDED_BY_YOU
        return t, None
    claimable = [
        t
        for t in tasks.values()
        if t.belongs_to(session_id)
        and t.is_claimable(tasks)
        and node not in t.released_by
        and _claim_refusal(t, node) is None
    ]
    # Oldest first, so a self-claiming pool drains the board in declaration order
    # rather than in dict order, which would be arbitrary but reproducible -- the
    # worst kind, because it looks deliberate.
    if not claimable:
        return None, ClaimRefusal.NOTHING_CLAIMABLE
    return min(claimable, key=lambda t: (t.declared_at, t.id)), None


def _root_of_its_session(task: Task, asker: NodeId) -> bool:
    """
    Whether ``asker`` is the root of ``task``'s own session: the one agent that
    may act on a task in place of an agent of that session that has stopped
    (``_may_reclaim``, ``_withdrawer_refusal``).
    """
    return asker[1] is None and task.belongs_to(asker[0])


def _may_reclaim(snap: Snapshot, intent: TaskReleased) -> bool:
    """
    Whether this release, which is not the holder's, is one the store takes: the
    root of the task's own session giving back a claim whose holder is an agent
    of an earlier run of that session.

    **Which holders those are is** ``Snapshot.of_earlier_run``, and nothing is
    restated here: a party a restored board names that this process has no
    record of, or a terminal sub-agent spawned before the session was last
    started. Not ``board._is_gone``, which is also true of an agent of the
    current run that finished its turn -- that agent can be woken, and may be
    holding the task across a wait; under that reading a root could take work
    from any builder that had returned to it. And not merely "no record", which
    is also an agent whose start was never announced.

    A release and nothing more. The holder is not written down as having given
    the task up (``boardfacts`` records a reclaim, naming both), and nothing
    here lets the root finish the task in the holder's place.

    The operator has not ruled on this: it is question 6 of
    `planning/2026-10-12-session-control-and-the-inter-team-record-in-one-sequence.md`,
    built as the lead recommended there.
    """
    task = snap.tasks.get(intent.task_id)
    return task is not None and _may_take_claim(snap, task, intent.node_id)


def _may_take_claim(snap: Snapshot, task: Task, asker: NodeId) -> bool:
    """
    Whether ``asker`` may end ``task``'s claim in its holder's place: it is the
    root of the task's session, and the holder is of an earlier run of it.
    ``_may_reclaim`` says why that and nothing wider; ``_withdrawer_refusal``
    asks the same of a root's withdrawal.
    """
    if task.claimed_by is None:
        return False
    return _root_of_its_session(task, asker) and snap.of_earlier_run(task.claimed_by)


def _would_cycle(tasks: dict[TaskId, Task], new: Task) -> bool:
    """
    Would admitting ``new`` make some task permanently unclaimable?

    Checked at declare time because the symptom is otherwise invisible and awful to
    diagnose: every task in the cycle is unclaimable forever, so workers idle while
    the board plainly shows outstanding work. Dependencies point at prerequisites,
    so a cycle exists iff ``new`` is reachable from one of its own.

    Walked over ``new``'s own board (``Task.dependency``). An id another
    session's task answers to is a dependency that is missing here, and what
    that stranger waits on is no path back to ``new``.
    """
    return bool(_cycle(tasks, new))


def _cycle(tasks: Mapping[TaskId, Task], new: Task) -> tuple[TaskId, ...]:
    """
    A shortest cycle through ``new`` once it is on the board, from ``new`` back
    to ``new``, or empty when there is none.

    The edges are what a task waits on: its ``depends_on``, and for an
    after-all-writers task its ``writers``. Those are taken over the board with
    ``new`` on it, because ``new`` may be one of a gate's writers. So a writer
    that waits on a gate, directly or through other tasks, is a cycle, in
    either order of declaration. A gate's writers are counted in every state
    but COMPLETED (``Task.writers``). A claimed gate is held and its holder can
    still give it back, and the released gate would then wait on that writer
    for good.

    Breadth first, so the cycle named is a shortest one. ``new``'s own board
    only, as ``_would_cycle`` says.
    """
    pool = {**tasks, new.id: new}
    parent: dict[TaskId, TaskId] = {}
    queue: deque[TaskId] = deque([new.id])
    while queue:
        current = queue.popleft()
        task = new if current == new.id else new.dependency(pool, current)
        if task is None:
            continue
        for following in (*task.depends_on, *task.writers(pool)):
            if following == new.id:
                path = [current]
                while path[-1] != new.id:
                    path.append(parent[path[-1]])
                return (*reversed(path), new.id)
            if following not in parent:
                parent[following] = current
                queue.append(following)
    return ()


def _subtree(snap: Snapshot, root: NodeId) -> tuple[NodeId, ...]:
    """``root`` and every descendant."""
    out = [root]
    frontier = [root]
    while frontier:
        current = frontier.pop()
        for nid, rec in snap.nodes.items():
            if rec.parent == current:
                out.append(nid)
                frontier.append(nid)
    return tuple(out)


def _preorder(nodes: dict[NodeId, AgentRecord]) -> tuple[NodeId, ...]:
    """
    Parents immediately followed by their descendants.

    Siblings keep insertion order, which is spawn order -- stable across frames, so
    a newly spawned sub-agent appears beneath its siblings rather than shuffling the
    rows above it and scrambling the widget state keyed to them (I6).
    """
    children: dict[NodeId | None, list[NodeId]] = {}
    for nid, rec in nodes.items():
        children.setdefault(rec.parent, []).append(nid)
    # Recalled roots after the ones this process launched, each group in its own
    # insertion order (``Snapshot.order``). A recalled root has no children.
    roots = children.get(None, [])
    roots.sort(key=lambda nid: nodes[nid].recalled is not None)

    out: list[NodeId] = []

    def walk(parent: NodeId | None) -> None:
        for nid in children.get(parent, ()):
            out.append(nid)
            walk(nid)

    walk(None)

    # An orphan -- a sub-agent whose parent has been removed, or whose spawn intent
    # arrived before its parent's -- would otherwise vanish from the tree while
    # still sitting in the node table, taking any pending approval with it.
    # Surfacing it at the root is worse-looking and better-behaved than losing it.
    if len(out) != len(nodes):
        seen = set(out)
        out.extend(nid for nid in nodes if nid not in seen)
    return tuple(out)


def _needs_you(
    nodes: dict[NodeId, AgentRecord], order: tuple[NodeId, ...]
) -> tuple[Obligation, ...]:
    """
    Everything a node's own state says is waiting on the operator, oldest first:
    the approvals, the questions and the failures. The other two kinds, a decision
    and a command, are records and not states: ``_owing_decisions`` and
    ``_owing_commands`` add those.

    One walk producing all three kinds. Ordered by how long each has been waiting
    rather than by tree position, because the operator works a backlog and the thing
    that has been blocked longest is the thing costing the most. Ties keep tree
    order, since the walk is over ``order`` and the sort is stable.

    **These three** are mutually exclusive per node by construction and not by luck,
    and it takes two constructions rather than one. While a node is live, the
    ``pending`` guard in StateChanged and SubagentProgress holds anything carrying an
    approval in AWAITING_APPROVAL, which is neither of the states the other two kinds
    read. Once an end is declared, ``_FINAL_STATES`` stops those same arms from moving
    the node at all, so a finished node still carrying an approval sits in DONE or
    CANCELLED -- again neither. FAILED is the one terminal state that can be entered
    with an approval outstanding, and ``AgentFinished`` clears ``pending`` as it sets
    it, so that pair cannot coexist either.

    **That is not a property of ``needs_you``.** A node with an open decision or
    an open command can be in any state, can have any one of these three beside
    it, and can have several of either open. So nothing may read "this node is in the list" as
    "this node has stopped", or take one obligation's kind for the kind of
    everything its node or its session owes.
    """
    out: list[Obligation] = []
    for nid in order:
        rec = nodes.get(nid)
        if rec is None:
            continue

        for p in rec.pending:
            out.append(
                ApprovalNeeded(node=nid, since=p.requested_at, summary=p.summary, approval=p)
            )

        if rec.state is AgentState.AWAITING_INPUT:
            # Deliberately not the agent's last sentence. Naming the two things the
            # operator can do about it is useful and honest; summarising what was
            # asked would mean guessing whether the turn ended in a question at all.
            #
            # Whether the turn was interrupted is a different kind of fact and does
            # belong here: the driver reads it off the result's terminal_reason, so
            # it is the CLI reporting what it did rather than a guess about what was
            # said. An interrupt that does not read as one in the list the operator
            # actually works from tells them their interrupt did nothing.
            interrupted = rec.topic == INTERRUPTED_TOPIC
            out.append(
                QuestionPending(
                    node=nid,
                    since=rec.state_since,
                    summary=(
                        "interrupted - reply or close"
                        if interrupted
                        else "ended its turn - reply or close"
                    ),
                )
            )

        elif rec.state is AgentState.FAILED and not rec.acknowledged:
            # The record holds how the run failed as it was said, for its session
            # file (``_VERBATIM``); the queue row draws it.
            error = None if rec.error is None else drawable.text(rec.error)
            first_line = (error or "").strip().splitlines()
            out.append(
                SessionFailed(
                    node=nid,
                    # A failure's wait starts when it died. ended_at is set by the
                    # same intent that set FAILED, so state_since only stands in for
                    # a record that reached FAILED some other way.
                    since=rec.ended_at if rec.ended_at is not None else rec.state_since,
                    summary=first_line[0][:120] if first_line else "session failed",
                    error=error,
                )
            )

    return tuple(sorted(out, key=lambda o: o.since))


# -- decisions ---------------------------------------------------------------------


def _filed(
    decisions: Mapping[DecisionId, Decision], decision: Decision
) -> Mapping[DecisionId, Decision]:
    """``decisions`` with ``decision`` under its id, as a new map."""
    return MappingProxyType({**decisions, decision.id: decision})


def _decision_settled(
    request_id: str | None, refusal: DecisionRefusal | None
) -> tuple[Effect, ...]:
    """The reply to a raise or a withdrawal, or nothing when nobody asked."""
    if request_id is None:
        return ()
    return (DecisionSettled(request_id=request_id, refusal=refusal),)


def _raise_refusal(snap: Snapshot, intent: DecisionRaised) -> DecisionRefusal | None:
    """
    Why a decision may not be filed, or None if it may.

    An id already held is refused before anything is counted, so a retry of a
    raise that landed is told that and not that an allowance is spent.

    ``blocks`` is checked here and not only where it was typed: this is the
    instant the board is known, and a row that says "blocks t3" in the queue's
    own voice has to be naming a task that is there.

    **Two bounds, and the root meets only the first.** An agent may have
    ``MAX_OPEN_DECISIONS_PER_ASKER`` open. The sub-agents of a session may have
    ``MAX_OPEN_SUBAGENT_DECISIONS`` open between them, counting only those whose
    asker has not ended (``AskerFate.has_ended``). Counted across the whole
    session, one worker could spend the allowance of its lead -- the agent with
    the most reason to ask, and the one with nobody to pass the question up to.
    And a finished worker's open decisions are freed only by the operator, so
    counting them would let agents that no longer exist refuse the ones that do.
    """
    asker = intent.node_id
    if intent.decision.id in snap.decisions:
        return DecisionRefusal.ALREADY_RAISED
    if blocked_tasks(snap, intent.decision, asker[0]) != tuple(
        dict.fromkeys(intent.decision.blocks)
    ):
        return DecisionRefusal.UNKNOWN_TASK
    still_open = [d for d in snap.decisions.values() if d.is_open and d.session_id == asker[0]]
    if sum(1 for d in still_open if d.asker == asker) >= MAX_OPEN_DECISIONS_PER_ASKER:
        return DecisionRefusal.TOO_MANY_OPEN
    if asker[1] is None:
        return None
    among_workers = sum(
        1
        for d in still_open
        if d.asker[1] is not None and (d.asker == asker or not _asker_fate(snap, d.asker).has_ended)
    )
    if among_workers >= MAX_OPEN_SUBAGENT_DECISIONS:
        return DecisionRefusal.SESSION_FULL
    return None


def _woken(asker: NodeId, reason: WakeReason, subject_id: str) -> tuple[Effect, ...]:
    """
    The order to wake ``asker``'s session about ``subject_id``. A sub-agent's
    session is woken through its lead, the only one with a turn to wake into.
    """
    return (SessionWoken(session_id=asker[0], reason=reason, subject_id=subject_id),)


def _concern_wake(concern: Concern) -> tuple[Effect, ...]:
    """
    The order to wake a lead for a concern one of its own workers addressed to
    it, and nothing for any other concern.

    Only worker to its own lead, so no agent can wake another into a loop: a
    lead's concern to a worker, a worker's to a worker, and anything across
    sessions are read with ``read_inbox`` when the recipient next looks.
    """
    sender, recipient = concern.sender, concern.recipient
    if sender[1] is None or recipient[1] is not None or sender[0] != recipient[0]:
        return ()
    if is_operator(sender):
        # The store's own notice on the operator's action, not a worker's.
        return ()
    return (
        SessionWoken(
            session_id=recipient[0],
            reason=WakeReason.CONCERN_TO_LEAD,
            subject_id=concern.id,
            sender=sender,
        ),
    )


def _ended_by_operator(
    decisions: Mapping[DecisionId, Decision],
    intent: DecisionAnswered | DecisionDismissed,
    outcome: DecisionOutcome,
) -> Mapping[DecisionId, Decision] | None:
    """
    ``decisions`` with the one ``intent`` names ended as ``outcome``, or None
    when the intent changes nothing.

    **This is the whole of who may answer or dismiss**: an intent that names a
    node is an agent's and is not applied, and the bus builds no intent without
    one. A decision that has ended is left as it ended, so no later intent --
    the operator's included -- replaces an answer.

    **An answer over ``MAX_ANSWER_CHARS`` is not applied, and is not cut to
    fit.** The decision stays open and in the queue. An answer is stored byte
    for byte or not at all, and one too long for ``read_decisions`` to return
    whole is one its asker could never read. Nobody is waiting on this intent to
    be told, so the surface that sends it refuses the draft first.
    """
    decision = decisions.get(intent.decision_id)
    if intent.node_id is not None or decision is None or not decision.is_open:
        return None
    if isinstance(outcome, Answered) and len(outcome.text) > MAX_ANSWER_CHARS:
        return None
    return _filed(decisions, dataclasses.replace(decision, outcome=outcome))


def _withdrawal_refusal(
    decisions: Mapping[DecisionId, Decision], intent: DecisionWithdrawn
) -> DecisionRefusal | None:
    decision = decisions.get(intent.decision_id)
    if decision is None:
        return DecisionRefusal.NO_SUCH_DECISION
    if decision.asker != intent.node_id:
        return DecisionRefusal.NOT_THE_ASKER
    if not decision.is_open:
        return DecisionRefusal.ALREADY_ENDED
    return None


# -- break-glass commands ----------------------------------------------------------
#
# ``command.py`` refuses, with ``ValueError``, a record that would say something
# its parts do not. Nothing here lets one out of ``_apply``: each arm tests the
# state before it builds, and what is built from an intent's own values goes
# through ``_closed`` or ``_approved``, which answer None instead.


def _with_command(
    commands: Mapping[CommandId, Command], command: Command
) -> Mapping[CommandId, Command]:
    """``commands`` with ``command`` under its id, as a new map."""
    return MappingProxyType({**commands, command.id: command})


def _command_settled(
    request_id: str | None, command_id: CommandId, refusal: CommandRefusal | RequestRefusal | None
) -> tuple[Effect, ...]:
    """The reply to a request or a withdrawal, or nothing when nobody asked."""
    if request_id is None:
        return ()
    return (CommandSettled(request_id=request_id, command_id=command_id, refusal=refusal),)


def _request_refusal(
    snap: Snapshot, intent: CommandRequested
) -> CommandRefusal | RequestRefusal | None:
    """
    Why a command may not be put in front of the operator, or None if it may.

    The id first: it names a directory, and one the store holds is a retry to
    be told so and not that an allowance is spent. Then the string and the two
    texts, by ``command.request_refusal`` -- the handler asks it too, and this
    is the asking that counts.

    **Two bounds, and the root meets only the first**, as for a decision
    (``_raise_refusal``) and for its reasons: ``MAX_OPEN_PER_ASKER`` open for
    one agent, and ``MAX_OPEN_SUBAGENTS`` between the sub-agents of a session,
    counting only those whose asker has not ended. Open is asked or running.
    """
    asker = intent.node_id
    handed = intent.command
    if not is_valid_id(handed.id):
        return CommandRefusal.BAD_ID
    if handed.id in snap.commands:
        return CommandRefusal.ALREADY_ASKED
    unaskable = request_refusal(handed.asked, handed.reason, handed.expects)
    if unaskable is not None:
        return unaskable
    # The caps are on what an agent has asked and not had an end to: a command
    # left running unsupervised owes a row, and is no request of anybody's.
    still_open = [c for c in snap.commands_awaiting(asker[0]) if c.is_open]
    if sum(1 for c in still_open if c.asker == asker) >= MAX_OPEN_PER_ASKER:
        return CommandRefusal.TOO_MANY_OPEN
    if asker[1] is None:
        return None
    among_workers = sum(
        1
        for c in still_open
        if c.asker[1] is not None and (c.asker == asker or not _asker_fate(snap, c.asker).has_ended)
    )
    if among_workers >= MAX_OPEN_SUBAGENTS:
        return CommandRefusal.SESSION_FULL
    return None


def _approved(snap: Snapshot, intent: CommandApproved) -> Command | None:
    """
    The command ``intent`` names with its run recorded, or None when the
    approval is not applied.

    **This is the whole of who may start a command, and of what starts**: an
    intent that names a node is an agent's and is not applied, and every field
    of the ``Run`` is the intent's. The string the agent asked for is not read.
    """
    if intent.node_id is not None:
        return None
    if snap.command_approval_refusal(intent.command_id, intent.text, intent.timeout_s) is not None:
        return None
    try:
        return dataclasses.replace(
            snap.commands[intent.command_id],
            run=Run(
                text=intent.text,
                shell=intent.shell,
                cwd=intent.cwd,
                timeout_s=intent.timeout_s,
                started_at=intent.at,
            ),
        )
    except ValueError:
        # A run the record cannot hold: no shell named.
        return None


def _closed(command: Command, end: End) -> Command | None:
    """
    ``command`` ended as ``end``, or None when the record cannot say that: an
    outcome that only ends an unapproved command, handed over for one that has
    a run, or the reverse.
    """
    try:
        return dataclasses.replace(command, end=end)
    except ValueError:
        return None


def _command_withdrawal_refusal(
    commands: Mapping[CommandId, Command], intent: CommandWithdrawn
) -> CommandRefusal | None:
    held = commands.get(intent.command_id)
    if held is None:
        return CommandRefusal.NO_SUCH_COMMAND
    if held.asker != intent.node_id:
        return CommandRefusal.NOT_THE_ASKER
    if held.state is not CommandState.ASKED:
        return CommandRefusal.NOT_ASKED
    return None


def _with_restored(
    commands: Mapping[CommandId, Command], intent: CommandsRestored
) -> Mapping[CommandId, Command] | None:
    """
    ``commands`` with the ones read from a session's files added, or None when
    nothing is applied.

    The batch is taken whole or not at all. One that holds an open command, a
    command of another session, or an id that is not a command id did not come
    from ``commandfile.restore``, and the rest of it is not worth more than the
    part that shows so. **So no file puts a command in front of the operator to
    be run, or leaves one running.**

    A command already held is skipped and never replaced: what this process saw
    of it is worth more than the file's account, and a second restore of one
    session changes nothing.
    """
    if intent.node_id is not None:
        return None
    if any(
        c.is_open or c.session_id != intent.session_id or not is_valid_id(c.id)
        for c in intent.commands
    ):
        return None
    added: dict[CommandId, Command] = {}
    for restored in intent.commands:
        if restored.id not in commands:
            added.setdefault(restored.id, restored)
    if not added:
        return None
    return MappingProxyType({**commands, **added})


def _command_since(
    snap: Snapshot, commands: Mapping[CommandId, Command], now: float
) -> Mapping[CommandId, float]:
    """
    ``Snapshot.command_since`` for the step that leaves ``commands``: an entry
    for every command that owes a row (``Command.owes_row``) and for no other.

    A command with no entry yet became open in this step, and ``now`` is when.
    Asked once per step, of the records, and not in the arm that files a
    request or the three that end one: an arm cannot forget to stamp or to
    clear what it never writes. Made from the records on every step and not
    only when they change, so every open command has an entry whatever
    snapshot the step began from, and ``_owing_commands`` reads it unguarded.
    """
    held = snap.command_since
    since = {c.id: held.get(c.id, now) for c in commands.values() if c.owes_row}
    return held if since == held else MappingProxyType(since)


def _owing_commands(world: Snapshot) -> Snapshot:
    """
    ``world`` with every command that is asked or running in ``needs_you``, at
    its place by age (``Snapshot.commands_awaiting``).

    A step over a built snapshot for the reasons ``_owing_decisions`` is one:
    the row names its asker's role, and "in the queue exactly while it is open"
    then has no arm that could forget to remove a row. The common case -- no
    command open -- returns ``world`` itself.

    ``since`` is ``Snapshot.command_since`` and never ``Command.asked_at``,
    which is a wall-clock time among monotonic ones.
    """
    awaiting = world.commands_awaiting()
    if not awaiting:
        return world
    owed = [
        CommandOwed(
            node=c.asker,
            since=world.command_since[c.id],
            summary=_command_summary(world, c),
            command=c,
            asker=_asker_fate(world, c.asker),
        )
        for c in awaiting
    ]
    # Stable, so among equal waits everything else a node owes stays ahead of
    # its commands and commands keep the order they were asked in.
    return dataclasses.replace(
        world, needs_you=tuple(sorted((*world.needs_you, *owed), key=lambda o: o.since))
    )


_COMMAND_STATE_TEXT = {CommandState.ASKED: "asked", CommandState.RUNNING: "running"}
_COMMAND_FATE_TEXT: dict[AskerFate, str | None] = {
    AskerFate.LIVE: None,
    AskerFate.IDLE: "its asker has ended its turn, and the result wakes its session",
    AskerFate.STOPPED: "its asker has stopped",
    AskerFate.FAILED: "its asker failed",
    AskerFate.UNANNOUNCED: "its asker has not announced itself yet",
    AskerFate.GONE: "its asker is gone",
}


def _command_summary(world: Snapshot, command: Command) -> str:
    """
    The queue row for a command that is asked or running, built from structure
    and from nothing its asker wrote.

    For the reason ``_decision_summary`` gives, and with more at stake: this
    row is drawn in the queue's own voice, and the row it stands for runs a
    string outside the sandbox. A first line of the command, or of the reason
    for it, would be an agent's sentence saying what the operator is about to
    approve. So the row says that a command is waiting, whose, in which state
    and how big; the string is on the record, for the one surface that draws it
    whole and escaped.

    The size is of the string that would run or is running: what was asked,
    or the run's own text once there is one.

    The id is last, for the reason a decision's is.
    """
    text = command.asked if command.run is None else command.run.text
    lines = text.count("\n") + 1
    parts = [f"{lines} line{'' if lines == 1 else 's'}, {len(text):,} characters"]
    if command.run is not None and command.stop_asked_at is not None:
        parts.append("cancel asked")
    fate = _COMMAND_FATE_TEXT[_asker_fate(world, command.asker)]
    if fate is not None:
        parts.append(fate)
    if _PLAIN_ID.fullmatch(command.id):
        parts.append(command.id)
    who = " ".join(role_name(world, command.asker, command.session_id).split())
    left = command.unsupervised
    if left is not None:
        # Before the size: it is the one row of this kind that is an alarm.
        parts.insert(
            0,
            (
                f"its shell's session {left.pid} is alive and nothing bounds it"
                if left.shell
                else f"its shell is gone, {left.alive} of its processes"
                f" {'is' if left.alive == 1 else 'are'} alive and nothing bounds"
                f" {'it' if left.alive == 1 else 'them'}"
            ),
        )
        return f"command STILL RUNNING, unsupervised, from {who}: " + "; ".join(parts)
    state = _COMMAND_STATE_TEXT.get(command.state, command.state.value)
    return f"command {state} from {who}: " + "; ".join(parts)


def _owing_decisions(world: Snapshot) -> Snapshot:
    """
    ``world`` with every open decision in ``needs_you``, at its place by age.

    A second step over a built snapshot and not a branch of ``_needs_you``,
    because a decision's row names its asker's role and the tasks it blocks, and
    both are projections of a whole snapshot (``board.role_name``,
    ``Task.belongs_to``). The common case -- no decision open -- returns
    ``world`` itself.

    Derived from the records on every step, so "in the queue exactly while it is
    open" has no arm that could forget to remove a row.
    """
    owed = [
        DecisionOwed(
            node=d.asker,
            since=d.raised_at,
            summary=_decision_summary(world, d),
            decision=d,
            asker=_asker_fate(world, d.asker),
        )
        for d in world.decisions.values()
        if d.is_open
    ]
    if not owed:
        return world
    # Stable, so among equal waits a node's own obligations stay ahead of its
    # decisions and decisions keep the order they were raised in.
    return dataclasses.replace(
        world, needs_you=tuple(sorted((*world.needs_you, *owed), key=lambda o: o.since))
    )


def _asker_fate(world: Snapshot, asker: NodeId) -> AskerFate:
    """
    What has become of ``asker``, as ``AskerFate`` words it.

    **No record is two different things.** A sub-agent can call the bus before
    its start is reported, so one with no record under a root that has one may
    be working, and telling the operator to dismiss its decision would be
    telling them to close a live agent's question. A root always has a record
    from the moment its session is launched, and removing a root removes its
    whole subtree, so an asker with no record AND no root's record is one whose
    session has left the store.

    **An agent of an earlier run of its session is gone, with or without a
    record** (``Snapshot.of_earlier_run``, asked first): another process's,
    which has none, and this process's from before the session was started
    again, which has a terminal one. The second would otherwise read as
    stopped -- "can still be woken" -- and nothing in the run that is going now
    wakes it. It is the reading every other surface gives the same agent.

    A sub-agent removed on its own would read as unannounced. Nothing emits
    ``AgentRemoved`` for one; whatever starts to has this to settle.
    """
    if world.of_earlier_run(asker):
        return AskerFate.GONE
    record = world.get(asker)
    if record is None:
        announced_later = asker[1] is not None and world.get((asker[0], None)) is not None
        return AskerFate.UNANNOUNCED if announced_later else AskerFate.GONE
    if record.state is AgentState.FAILED:
        return AskerFate.FAILED
    if record.state in _FINAL_STATES:
        return AskerFate.STOPPED
    if record.state is AgentState.AWAITING_INPUT:
        return AskerFate.IDLE
    return AskerFate.LIVE


# What an id has to look like to be drawn in the operator's queue: no spaces and
# nothing that could finish or start a phrase of the queue's own.
_PLAIN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SHOWN_TASKS = 3

_STANDING_TEXT = {
    Standing.OPERATORS: "yours to take",
    Standing.TAKEN: "already taken, wants ratifying",
}
_REVERSIBLE_TEXT = {True: "yes", False: "no", None: "not stated"}
_FATE_TEXT: dict[AskerFate, str | None] = {
    AskerFate.LIVE: None,
    AskerFate.IDLE: "its asker has ended its turn, and answering wakes its session",
    AskerFate.STOPPED: "its asker has stopped",
    AskerFate.FAILED: "its asker failed",
    AskerFate.UNANNOUNCED: "its asker has not announced itself yet",
    AskerFate.GONE: "its asker is gone",
}


def _decision_summary(world: Snapshot, decision: Decision) -> str:
    """
    The queue row for an open decision, built from structure and from nothing the
    asker wrote as prose.

    The queue draws a summary in its own colour and font beside
    ``approval.summarize``'s rows, the detail pane puts it on the clipboard and
    the digest draws it as one of its own items. A question shown there -- or its
    first line -- is an agent's sentence in the operator's own voice, and
    "Bash  git push --force  (approved)" is a sentence an agent can write. So the
    row says that a decision is waiting and what kind; the question is on the
    record, for a surface that marks it as the asker's.

    A role name is on one line here whatever its agent type holds, for the
    reason a board row's owner is.

    **The id is last.** It is a random token, and a row cut to a narrow pane
    loses its end: what is cut should be the token and not whose decision it is,
    whether it can be undone, or what it holds up. Only an id that is one plain
    word is shown at all.
    """
    parts = [
        _STANDING_TEXT[decision.standing],
        f"reversible: {_REVERSIBLE_TEXT[decision.reversible]}",
        _blocks_text(world, decision),
    ]
    fate = _FATE_TEXT[_asker_fate(world, decision.asker)]
    if fate is not None:
        parts.append(fate)
    if _PLAIN_ID.fullmatch(decision.id):
        parts.append(decision.id)
    who = " ".join(role_name(world, decision.asker, decision.session_id).split())
    return f"decision from {who}: " + "; ".join(parts)


def _blocks_text(world: Snapshot, decision: Decision) -> str:
    blocked = blocked_tasks(world, decision)
    if not blocked:
        return "blocks nothing on the board"
    plain = [task_id for task_id in blocked if _PLAIN_ID.fullmatch(task_id)]
    shown = plain[:_SHOWN_TASKS]
    rest = len(blocked) - len(shown)
    if not shown:
        return f"blocks {rest} task(s)"
    return "blocks " + ", ".join(shown) + (f" and {rest} more" if rest else "")


def blocked_tasks(
    world: Snapshot, decision: Decision, session_id: str | None = None
) -> tuple[TaskId, ...]:
    """
    The tasks ``decision.blocks`` names that are on its asker's board, each once,
    in the order it names them.

    ``session_id`` is the board to look on when ``decision`` has not been filed
    yet and its ``asker`` is still whatever the handler wrote; None reads it off
    the record.

    Every id a filed decision names was on that board when it was filed
    (``_raise_refusal``), so this differs from ``decision.blocks`` only by a
    repeat.
    """
    board = decision.session_id if session_id is None else session_id
    found: list[TaskId] = []
    for task_id in decision.blocks:
        task = world.tasks.get(task_id)
        if task is not None and task.belongs_to(board) and task_id not in found:
            found.append(task_id)
    return tuple(found)


# -- a board from its log ------------------------------------------------------------


def _restored(
    snap: Snapshot, intent: BoardRestored
) -> tuple[dict[TaskId, Task], dict[ConcernId, Concern], Mapping[str, Restore]] | None:
    """
    The tasks, concerns and restore reports after ``intent``, or None when it
    changes none of them.

    **Everything the board holds is placed or is named in the report with why
    not.** A task or concern is placed when its id is free. One the store
    already holds for this session exactly as the log has it is counted and left.
    Each of the rest is a ``NotPlaced``.

    **An id another session's board has is never written over.** ``tasks`` is one
    map for every session and leads choose ids by hand, so a resumed session's
    ``t1`` can meet a live ``t1`` it has nothing to do with. The logged one is
    left out and reported. That is a board restored short by a name collision,
    which keying tasks by session and id would remove; that is a change to every
    reader of the map and is not made here.

    **A store that holds a different board for the session takes nothing.** If
    it has a task or concern of this session that the log has differently, or
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
    held = [0, 0]

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
        concern = _restored_concern(restored_concern, session_id, node_of, at_of)
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
            held[1] += 1
        else:
            differing.append(NotPlaced("concern", concern.id, Unplaced.STORE_DIFFERS))
    differing += [
        NotPlaced("concern", c.id, Unplaced.STORE_ONLY)
        for c in snap.concerns.values()
        if c.sender[0] == session_id and c.id not in filed
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
        report = dataclasses.replace(
            report,
            at=None if prior is None else prior.at,
            tasks=held[0],
            concerns=held[1],
            unplaced=(*differing, *withheld, *unplaced),
            parties=MappingProxyType({}) if prior is None else prior.parties,
        )
        tasks, concerns = dict(snap.tasks), dict(snap.concerns)
    else:
        fresh = bool(new_tasks or new_concerns) or prior is None or prior.at is None
        report = dataclasses.replace(
            report,
            # The reading the newest of what is held was converted with. A later
            # reading that placed nothing leaves the earlier instant standing:
            # moving it would put whatever the session did in between on the far
            # side of "before the restore".
            at=clock.monotonic if fresh or prior is None else prior.at,
            tasks=held[0] + len(new_tasks),
            concerns=held[1] + len(new_concerns),
            unplaced=tuple(unplaced),
            parties=MappingProxyType({**({} if prior is None else prior.parties), **parties}),
        )
    if report == prior:
        return None
    return tasks, concerns, MappingProxyType({**snap.restores, session_id: report})


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
    The id of a logged task or concern that is not placed for holding what no store
    writes, as its report names it: drawable, since the id may be what held it.
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
      not describe, reported as placed whole.

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
    """
    sender = node_of(logged.sender)
    if sender[0] != session_id:
        return Unplaced.NOT_THIS_SESSIONS
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
    return dataclasses.replace(
        concern,
        posted_at=0.0,
        delivered_at=None if concern.delivered_at is None else 0.0,
    )
