"""
The board's tasks: declaration, the dependency graph, claims, and the refusals
each board call answers with.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

import dataclasses
from collections import deque
from collections.abc import Mapping

from ..effects import Effect, TaskWriteSettled
from ..intents import TaskReleased
from ..model import (
    ALL_WRITERS,
    MAX_WITHDRAWAL_REASON_CHARS,
    ClaimRefusal,
    NodeId,
    Snapshot,
    Task,
    TaskId,
    TaskRefusal,
    TaskState,
    stored_as_sent,
)


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
    an id another session holds (``board.rows.foreign_wait_label``), and no
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
    see. This and ``board.rows.board_tasks`` both ask ``Task.belongs_to``, which makes
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
    started. Not ``board.rows._is_gone``, which is also true of an agent of the
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
