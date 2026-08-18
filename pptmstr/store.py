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
from collections.abc import Iterable
from types import MappingProxyType
from typing import assert_never

from .board import board_tasks
from .effects import BoardDelivered, ClaimSettled, Effect, InboxDelivered, TaskWriteSettled
from .intents import (
    AgentFinished,
    AgentRemoved,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    BoardRead,
    CompactionObserved,
    ConcernEdited,
    ConcernPosted,
    ConcernWithdrawn,
    ContextPolled,
    FailureAcknowledged,
    InboxRead,
    Intent,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TaskAmended,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TopicChanged,
    UsageAccrued,
)
from .model import (
    INTERRUPTED_TOPIC,
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    ConcernState,
    NodeId,
    Obligation,
    QuestionPending,
    SessionFailed,
    Snapshot,
    Task,
    TaskId,
    TaskRefusal,
    TaskState,
    normalised_touches,
)
from .transcript import Transcript

# The ends that cannot be undone by an ordinary status update.
#
# A narrower question than ``AgentState.is_terminal``, and deliberately not the
# same set. ``is_terminal`` asks whether another transition is expected without
# the operator; this asks who is allowed to contradict the end, and only
# ``AgentFinished`` and ``AgentResumed`` are.
#
# FAILED is absent on purpose. A session that errored and then answered is an
# ordinary session again (see the StateChanged arm), and that recovery arrives as
# a plain StateChanged because nothing announces it -- the driver re-asserts a
# standing failure afterwards rather than suppressing the recovery.
#
# DONE and CANCELLED make the opposite statement: somebody ended this agent. A
# sub-agent's SubagentStop fires once, and the wake that can follow it comes in as
# a second SubagentStart, which the driver translates to ``AgentResumed`` -- so
# nothing legitimate reaches a finished sub-agent through StateChanged. What does
# reach it is its own last AssistantMessage: the message stream is buffered while
# the control frame carrying the stop is dispatched straight away, so the answer
# translates into a THINKING that lands after the end it preceded.
_FINAL_STATES = frozenset({AgentState.DONE, AgentState.CANCELLED})


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

    def apply(self, intent: Intent, now: float | None = None) -> tuple[Effect, ...]:
        """
        Apply one intent, producing a new snapshot and any answers it owes.

        The return is ignored by every caller that only mutates, which is most of
        them; it matters for the bus intents that an agent is parked on.
        """
        self._snapshot, effects = _apply(self._snapshot, intent, _clock(now))
        return effects

    def apply_all(self, intents: Iterable[Intent], now: float | None = None) -> tuple[Effect, ...]:
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
        """
        clock = _clock(now)
        snap = self._snapshot
        out: list[Effect] = []
        for intent in intents:
            snap, effects = _apply(snap, intent, clock)
            out.extend(effects)
        self._snapshot = snap
        return tuple(out)


def _clock(now: float | None) -> float:
    """
    The caller's instant, or this one.

    Defaulted at the entry point rather than inside ``_apply`` so the pure core
    keeps a required argument and cannot silently read a clock mid-batch.
    """
    return time.monotonic() if now is None else now


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
    nodes = dict(snap.nodes)
    concerns = dict(snap.concerns)
    tasks = dict(snap.tasks)
    effects: tuple[Effect, ...] = ()
    reorder = False

    match intent:
        case AgentSpawned():
            parent_rec = nodes.get(intent.parent) if intent.parent else None
            depth = parent_rec.depth + 1 if parent_rec else 0
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
                started_at=intent.started_at,
                transcript=intent.transcript or Transcript(),
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
                    # rather than in an "unknown" one of its own.
                    cwd=nodes[parent].cwd if adopted else None,
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
            if rec is None or rec.pending_by_id(intent.pending_id) is None:
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
            # A COMPLETED task is amended like any other. It reads as revising
            # history and is the honest option: the alternative is a spec that
            # disagrees with the record of what was built, and the board is what a
            # later reader has.
            amended = tasks.get(intent.task_id)
            if amended is not None:
                tasks[intent.task_id] = dataclasses.replace(amended, detail=intent.detail)

        case TaskDeclared():
            refused = _declaration_refusal(tasks, intent.task)
            auto: tuple[TaskId, ...] = ()
            if refused is None:
                # Provenance comes from the intent, never from the record handed
                # in: the intent's node_id is the sender the gate authenticated,
                # and a declared_by a caller set on the Task is not evidence of
                # anything. Stamping unconditionally leaves one writer for the
                # field rather than two that have to agree.
                #
                # Touches are normalised here for the same reason: the overlap
                # check compares them, so a caller that could supply its own
                # spelling could evade the check by writing `./x.py`.
                landed = dataclasses.replace(
                    intent.task,
                    declared_by=intent.node_id,
                    touches=normalised_touches(intent.task.touches),
                )
                auto = _auto_depends(tasks, landed)
                if auto:
                    landed = dataclasses.replace(landed, depends_on=(*landed.depends_on, *auto))
                tasks[landed.id] = landed
            effects = _settled(intent.request_id, refused, auto)

        case TaskClaimRequested():
            # The claimer's own session, taken from the sender the gate
            # authenticated rather than from anything the model can set.
            won = _pick_claim(tasks, intent.task_id, intent.node_id[0])
            if won is not None:
                won = dataclasses.replace(won, state=TaskState.CLAIMED, claimed_by=intent.node_id)
                tasks[won.id] = won
            # Emitted on both outcomes. A claim that found nothing must still be
            # answered, or the asking agent stays parked on a future forever over
            # an empty board -- which is the failure the whole effect channel
            # exists to make unstateable.
            effects = (ClaimSettled(request_id=intent.request_id, task=won),)

        case TaskCompleted():
            # Guarded on the claimer so a stale completion from a worker that
            # released the task cannot finish it out from under its new owner.
            refused = _ownership_refusal(tasks, intent.task_id, intent.node_id)
            if refused is None:
                tasks[intent.task_id] = dataclasses.replace(
                    tasks[intent.task_id], state=TaskState.COMPLETED, completed_at=intent.at
                )
            effects = _settled(intent.request_id, refused)

        case TaskReleased():
            refused = _ownership_refusal(tasks, intent.task_id, intent.node_id)
            if refused is None:
                tasks[intent.task_id] = dataclasses.replace(
                    tasks[intent.task_id], state=TaskState.PENDING, claimed_by=None
                )
            effects = _settled(intent.request_id, refused)

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

    order = _preorder(nodes) if reorder else snap.order
    return (
        Snapshot(
            seq=snap.seq + 1,
            nodes=MappingProxyType(nodes),
            order=order,
            needs_you=_needs_you(nodes, order),
            any_active=any(r.state.is_active for r in nodes.values()),
            concerns=MappingProxyType(concerns),
            tasks=MappingProxyType(tasks),
        ),
        effects,
    )


def _settled(
    request_id: str | None,
    refusal: TaskRefusal | None,
    auto_depends: tuple[TaskId, ...] = (),
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
    the same information at the worst moment.
    """
    if request_id is None:
        return ()
    return (TaskWriteSettled(request_id=request_id, refusal=refusal, auto_depends=auto_depends),)


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

    **This cannot close a cycle**, so it is safe to run after ``_would_cycle`` has
    already passed on the declared dependencies. A cycle through ``new`` needs
    something to reach ``new``, and nothing can: its id is not in ``tasks`` (a
    duplicate is refused before this runs), so no existing ``depends_on`` names it.
    Every edge added here therefore points from a brand-new node at an old one.

    Scoped to the declarer's own session, like everything else about a board. A
    dependency on another session's task would be a blocker that never appears on
    the board that is waiting for it -- unresolvable and invisible at once, which is
    worse than the concurrent write. The honest limit: **two sessions in one working
    directory get no protection from this**, and that is a real gap rather than a
    solved case.

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


def _declaration_refusal(tasks: dict[TaskId, Task], new: Task) -> TaskRefusal | None:
    """
    Why ``new`` may not go on the board, or None if it may.

    Re-declaring an existing id is refused rather than merged: the only way it
    happens is a retry, and overwriting would silently unclaim work somebody is
    doing. A cycle is refused for the reason ``_would_cycle`` gives.

    Both were already the reducer's behaviour. What is new is that the answer is a
    value the caller has to look at, rather than a dropped write the caller reported
    as a success.
    """
    if new.id in tasks:
        return TaskRefusal.DUPLICATE_ID
    if _would_cycle(tasks, new):
        return TaskRefusal.WOULD_CYCLE
    return None


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
        return TaskRefusal.ALREADY_COMPLETE
    if t.state is not TaskState.CLAIMED:
        return TaskRefusal.NOT_CLAIMED
    if t.claimed_by != node_id:
        return TaskRefusal.NOT_YOURS
    return None


def _pick_claim(tasks: dict[TaskId, Task], task_id: TaskId | None, session_id: str) -> Task | None:
    """
    The task this claim wins, or None.

    Serialization is what makes this safe, and it is the store's confinement to one
    thread that provides it -- not a lock, and not the file lock agent teams needs
    for the same job across processes. Two workers racing for the last task are two
    intents in a queue, and the second one reads the first one's result.

    **Scoped to the claimer's own session.** ``tasks`` is fleet-wide -- one ``Store``
    serves every session -- and this function applied no session filter at all while
    ``board.board_tasks`` filtered by declarer, so a worker could be handed a task
    that appeared on no board it or its operator could see. Both now ask
    ``Task.belongs_to``, which makes the two answers one answer rather than two that
    agree by inspection.

    Blocked-ness is still evaluated against the **whole** map, not the session's
    slice, matching ``board_tasks``. A dependency that does not exist counts as
    unsatisfied, so narrowing the map first would invent a blocker out of a
    cross-session dependency and wedge a task that is genuinely ready.
    """
    if task_id is not None:
        t = tasks.get(task_id)
        if t is None or not t.belongs_to(session_id) or not t.is_claimable(tasks):
            return None
        return t
    claimable = [t for t in tasks.values() if t.belongs_to(session_id) and t.is_claimable(tasks)]
    # Oldest first, so a self-claiming pool drains the board in declaration order
    # rather than in dict order, which would be arbitrary but reproducible -- the
    # worst kind, because it looks deliberate.
    return min(claimable, key=lambda t: (t.declared_at, t.id)) if claimable else None


def _would_cycle(tasks: dict[TaskId, Task], new: Task) -> bool:
    """
    Would admitting ``new`` make some task permanently unclaimable?

    Checked at declare time because the symptom is otherwise invisible and awful to
    diagnose: every task in the cycle is unclaimable forever, so workers idle while
    the board plainly shows outstanding work. Dependencies point at prerequisites,
    so a cycle exists iff ``new`` is reachable from one of its own.
    """
    if new.id in new.depends_on:
        return True
    frontier = list(new.depends_on)
    seen: set[TaskId] = set()
    while frontier:
        current = frontier.pop()
        if current == new.id:
            return True
        if current in seen:
            continue
        seen.add(current)
        existing = tasks.get(current)
        if existing is not None:
            frontier.extend(existing.depends_on)
    return False


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
    Everything waiting on the operator, oldest first.

    One walk producing all three kinds. Ordered by how long each has been waiting
    rather than by tree position, because the operator works a backlog and the thing
    that has been blocked longest is the thing costing the most. Ties keep tree
    order, since the walk is over ``order`` and the sort is stable.

    The three kinds are mutually exclusive per node by construction and not by luck,
    and it takes two constructions rather than one. While a node is live, the
    ``pending`` guard in StateChanged and SubagentProgress holds anything carrying an
    approval in AWAITING_APPROVAL, which is neither of the states the other two kinds
    read. Once an end is declared, ``_FINAL_STATES`` stops those same arms from moving
    the node at all, so a finished node still carrying an approval sits in DONE or
    CANCELLED -- again neither. FAILED is the one terminal state that can be entered
    with an approval outstanding, and ``AgentFinished`` clears ``pending`` as it sets
    it, so that pair cannot coexist either.
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
            first_line = (rec.error or "").strip().splitlines()
            out.append(
                SessionFailed(
                    node=nid,
                    # A failure's wait starts when it died. ended_at is set by the
                    # same intent that set FAILED, so state_since only stands in for
                    # a record that reached FAILED some other way.
                    since=rec.ended_at if rec.ended_at is not None else rec.state_since,
                    summary=first_line[0][:120] if first_line else "session failed",
                    error=rec.error,
                )
            )

    return tuple(sorted(out, key=lambda o: o.since))
