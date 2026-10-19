"""
Graph queries over one session's board: why a task is blocked, what is ready,
which tasks are open, and what a declaration would do before it is made.

Every function here is pure over one ``Snapshot`` and returns frozen values. The
shell (``bus.py``) owns the words; this owns the graph.

**The graph is ``Task.blocked_on``, and nothing else.** Every edge walked here is
one ``blocked_on`` reports and every node is looked up with ``Task.dependency``,
so a query cannot call a task blocked that ``Task.is_claimable`` would hand out,
or the reverse. A wider notion of waiting added to ``blocked_on`` reaches every
query here with no change to this module.

**A dry run is the real arm, discarded.** ``dry_run_declare`` applies the
``TaskDeclared`` intent through ``store._apply`` and reads the effect it returns,
rather than re-deriving which edges the store would add. A second derivation
would be a copy of ``_auto_depends``, ``_already_behind`` and
``_declaration_refusal`` that agrees with them by convention, and the dry run's
whole value is that it cannot disagree with the declaration it predicts.
"""

from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass
from pathlib import PurePosixPath

from pptmstr.board import BoardTask, board_tasks, role_name
from pptmstr.effects import TaskWriteSettled
from pptmstr.intents import TaskDeclared
from pptmstr.model import (
    NodeId,
    Snapshot,
    Task,
    TaskId,
    TaskRefusal,
    TaskState,
    normalised_touches,
)
from pptmstr.store import _apply


def is_test_path(path: str) -> bool:
    """
    Whether ``path`` is a test file: inside a directory named ``tests``, the
    ``tests`` directory itself, or a basename of ``test_*.py``, ``*_test.py`` or
    ``conftest.py``.

    A ``tests`` directory anywhere in the path counts, not only at its start:
    ``touches`` are relative to the session's directory, and a package's own
    ``tests/`` is as much a test file as the top-level one. A path that ends at
    one is a declarer claiming the whole directory (``normalised_touches``
    writes ``tests/`` as ``tests``), which holds only test files.
    """
    p = PurePosixPath(path)
    name = p.name
    if "tests" in p.parts:
        return True
    return name == "conftest.py" or (
        name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))
    )


@dataclass(frozen=True, slots=True)
class Edge:
    """
    One task waiting on another.

    ``shared`` is the files both tasks touch, in the waiting task's order,
    whatever caused the edge. ``test_only`` is true when there is a shared file
    and every one of them is a test file (``is_test_path``). Neither says the
    edge came from the overlap: a declarer may have written it by hand between
    two tasks that also share a file, and ``Task`` does not record which of its
    dependencies the board added.

    ``auto`` is what does say so, where it is known. A dry run knows, because
    the declaration's reply names the edges the board added
    (``TaskWriteSettled.auto_depends``): true for those, false for the
    declarer's own. On a landed task nothing records it, so ``why_blocked``
    leaves it None rather than guess. Only an ``auto`` edge is one that
    narrowing ``touches`` would remove.
    """

    waiting: TaskId
    on: TaskId
    shared: tuple[str, ...]
    test_only: bool
    auto: bool | None = None


def _edge(waiting: Task, on: Task | None, on_id: TaskId, auto: bool | None = None) -> Edge:
    if on is None:
        return Edge(waiting=waiting.id, on=on_id, shared=(), test_only=False, auto=auto)
    theirs = set(normalised_touches(on.touches))
    shared = tuple(p for p in normalised_touches(waiting.touches) if p in theirs)
    return Edge(
        waiting=waiting.id,
        on=on_id,
        shared=shared,
        test_only=bool(shared) and all(is_test_path(p) for p in shared),
        auto=auto,
    )


@dataclass(frozen=True, slots=True)
class Hop:
    """
    One task on a path from the task asked about to a root.

    ``state`` and ``holder`` are None for an id this board has no task for.
    ``holder`` is the name a reader can address (``board.role_name``), never a
    NodeId. ``edge`` is the wait that led here from the hop before it.
    """

    id: TaskId
    state: TaskState | None
    holder: str | None
    edge: Edge


class RootKind(enum.Enum):
    """
    Why the walk stopped at a task, which is what somebody has to act on.

    Three members because the three recoveries differ: an unheld task with
    nothing outstanding needs a claimer, a held one needs its holder to finish,
    and an id this board has no task for will never be finished by anything on
    it -- a typo, or an id another session's board holds.
    """

    UNCLAIMED = "unclaimed"
    HELD = "held"
    NOT_ON_BOARD = "not on this board"


@dataclass(frozen=True, slots=True)
class Root:
    """
    An unfinished task the asked task ultimately waits on, with the shortest
    path to it. ``path`` starts at a direct blocker of the asked task and ends
    at this root, so ``path[-1].id == id``.
    """

    id: TaskId
    kind: RootKind
    path: tuple[Hop, ...]


class Verdict(enum.Enum):
    """
    What ``why_blocked`` found the asked task to be.

    ``UNKNOWN`` is its own answer and not a blocked task with no chain: an id
    this board has no task for is a mistake in the question, and answering it
    as blocked or as ready would send the asker after the wrong problem.
    """

    UNKNOWN = "unknown"
    CLAIMABLE = "claimable"
    BLOCKED = "blocked"
    CLAIMED = "claimed"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class WhyBlocked:
    """
    The answer to "why can this task not be claimed".

    ``roots`` holds each unfinished root once, in the order a breadth-first walk
    from the asked task meets it, each with its shortest path. Empty for an
    unknown or claimable task.

    ``cycle`` holds the tasks reached that wait on themselves through the
    graph, in the order the walk met them. The store refuses a declaration that
    would close a cycle, so this is empty on any board the store built; it is
    here so that a board built some other way is answered, and the walk ends,
    rather than reporting a blocked task with nothing to blame.

    Its size is bounded by the part of the graph the asked task reaches, never
    by the board.
    """

    task_id: TaskId
    verdict: Verdict
    holder: str | None = None
    roots: tuple[Root, ...] = ()
    cycle: tuple[TaskId, ...] = ()


def _board_task(snap: Snapshot, session_id: str, task_id: TaskId) -> Task | None:
    task = snap.tasks.get(task_id)
    return task if task is not None and task.belongs_to(session_id) else None


def _holder(snap: Snapshot, session_id: str, task: Task) -> str | None:
    if task.state is not TaskState.CLAIMED or task.claimed_by is None:
        return None
    return role_name(snap, task.claimed_by, session_id)


def why_blocked(snap: Snapshot, session_id: str, task_id: TaskId) -> WhyBlocked:
    """
    The unfinished roots ``task_id`` waits on, each once, with the shortest path
    to it.

    A root is a blocker the walk does not go past: one this board has no task
    for, one that is held (the actionable fact is its holder, whatever it waits
    on in turn), or one with nothing outstanding of its own. A task on another
    session's board is answered as unknown, as a claim of it would be.

    Roots are found for a task in any state, because ``Task.blocked_on`` answers
    for any state: a CLAIMED task whose dependency was released afterwards is
    still told what it waits on.
    """
    asked = _board_task(snap, session_id, task_id)
    if asked is None:
        return WhyBlocked(task_id=task_id, verdict=Verdict.UNKNOWN)
    if asked.is_claimable(snap.tasks):
        return WhyBlocked(task_id=task_id, verdict=Verdict.CLAIMABLE)
    verdict = {
        TaskState.PENDING: Verdict.BLOCKED,
        TaskState.CLAIMED: Verdict.CLAIMED,
        TaskState.COMPLETED: Verdict.COMPLETED,
    }[asked.state]

    # Breadth-first, so the first path to reach a task is a shortest one. Each
    # task is entered once, which is both what makes a root appear once when
    # two paths reach it and what ends the walk on a cycle.
    paths: dict[TaskId, tuple[Hop, ...]] = {asked.id: ()}
    # The outstanding blockers of every task the walk went past, for the cycle
    # check after it.
    past: dict[TaskId, tuple[TaskId, ...]] = {}
    roots: list[Root] = []
    frontier: deque[Task] = deque([asked])
    while frontier:
        current = frontier.popleft()
        blockers = current.blocked_on(snap.tasks)
        past[current.id] = blockers
        for dep_id in blockers:
            if dep_id in paths:
                continue
            dep = current.dependency(snap.tasks, dep_id)
            hop = Hop(
                id=dep_id,
                state=None if dep is None else dep.state,
                holder=None if dep is None else _holder(snap, session_id, dep),
                edge=_edge(current, dep, dep_id),
            )
            path = (*paths[current.id], hop)
            paths[dep_id] = path
            if dep is None:
                roots.append(Root(id=dep_id, kind=RootKind.NOT_ON_BOARD, path=path))
            elif dep.state is TaskState.CLAIMED:
                roots.append(Root(id=dep_id, kind=RootKind.HELD, path=path))
            elif not dep.blocked_on(snap.tasks):
                roots.append(Root(id=dep_id, kind=RootKind.UNCLAIMED, path=path))
            else:
                frontier.append(dep)

    return WhyBlocked(
        task_id=task_id,
        verdict=verdict,
        holder=_holder(snap, session_id, asked),
        roots=tuple(roots),
        cycle=_on_a_cycle(past),
    )


def _on_a_cycle(past: dict[TaskId, tuple[TaskId, ...]]) -> tuple[TaskId, ...]:
    """
    The tasks of ``past`` that reach themselves through ``past``'s edges, in
    ``past``'s order. Quadratic in the walked set, which is the chain's size.
    """
    out: list[TaskId] = []
    for start in past:
        seen: set[TaskId] = set()
        stack = list(past[start])
        while stack:
            current = stack.pop()
            if current == start:
                out.append(start)
                break
            if current in seen or current not in past:
                continue
            seen.add(current)
            stack.extend(past[current])
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Ready:
    """
    A task that can be claimed now, and how many PENDING tasks on the board
    wait on it, directly or through others, each counted once.
    """

    task_id: TaskId
    title: str
    unblocks: int


def _board_order(snap: Snapshot, session_id: str) -> list[Task]:
    """The board's tasks in declaration order, as ``board.board_tasks`` orders them."""
    return [
        t
        for t in sorted(snap.tasks.values(), key=lambda t: (t.declared_at, t.id))
        if t.belongs_to(session_id)
    ]


def ready(snap: Snapshot, session_id: str) -> tuple[Ready, ...]:
    """
    The claimable tasks on the board, the one the most PENDING tasks wait on
    first, ties in declaration order.

    The count is of distinct tasks, so a task reached by two paths -- a diamond
    -- is counted once. It walks every outstanding wait, through tasks of any
    state, and counts only the PENDING ones it reaches: a CLAIMED task further
    along is not work waiting to be handed out.
    """
    tasks = _board_order(snap, session_id)
    waiters: dict[TaskId, list[Task]] = {}
    for t in tasks:
        for dep_id in t.blocked_on(snap.tasks):
            waiters.setdefault(dep_id, []).append(t)

    def unblocks(root: TaskId) -> int:
        seen: set[TaskId] = {root}
        stack = [root]
        count = 0
        while stack:
            for w in waiters.get(stack.pop(), ()):
                if w.id in seen:
                    continue
                seen.add(w.id)
                stack.append(w.id)
                if w.state is TaskState.PENDING:
                    count += 1
        return count

    found = [
        Ready(task_id=t.id, title=t.title, unblocks=unblocks(t.id))
        for t in tasks
        if t.is_claimable(snap.tasks)
    ]
    # ``sorted`` is stable, which is what keeps declaration order among ties.
    return tuple(sorted(found, key=lambda r: -r.unblocks))


def is_open(row: BoardTask) -> bool:
    """
    Whether a board row is open: not COMPLETED. A task the operator closed, and
    one its declarer withdrew, is COMPLETED, so neither is open.
    """
    return row.state is not TaskState.COMPLETED


def open_tasks(snap: Snapshot, session_id: str) -> tuple[BoardTask, ...]:
    """
    The board's rows that are open (``is_open``), as ``board.board_tasks``
    builds them.

    The rows themselves and not a summary, so an open-only board read renders
    with the same row text as the whole one.
    """
    return tuple(row for row in board_tasks(snap, session_id) if is_open(row))


# The request id a dry run's declaration carries. The arm emits its reply only
# for a declaration that has one, and the reply is where the edges are read from.
DRY_RUN_REQUEST = "boardgraph-dry-run"


@dataclass(frozen=True, slots=True)
class DryRun:
    """
    What declaring a task would do, from the real arm applied and discarded.

    ``refusal`` None means it would land. Then ``task_id`` is the id it would
    land under, which is the id asked: the arm refuses an id, or an entry of
    ``depends_on``, that ``store._drawable`` would rewrite
    (``TaskRefusal.ESCAPED_ID``). ``edges`` is every
    wait the landed task would have, the declarer's own first, then the ones the
    board would add (``added``, each edge marked ``auto``) -- the same tuple the
    real reply's ``auto_depends`` carries. ``already_behind`` is the
    overlapping tasks the board would not queue it behind, because they already
    wait on it. ``writers`` is, for a task declared after every writer
    (``model.ALL_WRITERS``), the writers it would wait on now
    (``Task.writers``): waits that are not in ``depends_on`` and so not edges
    here, and that a later writer joins. ``chain`` is ``why_blocked`` of the
    task as it would land.

    On a refusal nothing would land, so ``task_id`` is the id asked, ``edges``,
    ``added`` and ``already_behind`` are empty, ``chain`` is None, and
    ``claimable`` is false. ``held_off_fleet`` and ``cycle`` are the real
    reply's, carried so the dry run's refusal can be worded as the real one is:
    the session holding a duplicate id from off the fleet, and the loop a
    ``WOULD_CYCLE`` declaration would close.
    """

    task_id: TaskId
    refusal: TaskRefusal | None
    edges: tuple[Edge, ...] = ()
    added: tuple[TaskId, ...] = ()
    already_behind: tuple[TaskId, ...] = ()
    chain: WhyBlocked | None = None
    claimable: bool = False
    held_off_fleet: str | None = None
    cycle: tuple[TaskId, ...] = ()
    writers: tuple[TaskId, ...] = ()


def dry_run_declare(
    snap: Snapshot, session_id: str, task: Task, node_id: NodeId, now: float
) -> DryRun:
    """
    What ``TaskDeclared(task, node_id)`` would do to ``snap``, leaving ``snap``
    as it was.

    ``node_id`` is the declarer, and must be of ``session_id``: the arm scopes
    the declaration to the declarer's board, so a mismatch would answer for a
    board the caller did not ask about. That is a caller's mistake and raises.
    """
    if node_id[0] != session_id:
        raise ValueError(f"declarer {node_id!r} is not of session {session_id!r}")
    after, effects = _apply(
        snap, TaskDeclared(task=task, node_id=node_id, request_id=DRY_RUN_REQUEST), now
    )
    settled = next(
        e for e in effects if isinstance(e, TaskWriteSettled) and e.request_id == DRY_RUN_REQUEST
    )
    if settled.refusal is not None:
        return DryRun(
            task_id=task.id,
            refusal=settled.refusal,
            held_off_fleet=settled.held_off_fleet,
            cycle=settled.cycle,
        )
    # The landed id is read off the result, as the edges are: the one key the
    # apply added. It equals ``task.id`` because the arm refuses an id it would
    # store as another; taking ``task.id`` instead would restate that rule here,
    # agreeing with the arm by convention rather than reading what it did.
    (landed_id,) = after.tasks.keys() - snap.tasks.keys()
    landed = after.tasks[landed_id]
    return DryRun(
        task_id=landed_id,
        refusal=None,
        edges=tuple(
            _edge(landed, landed.dependency(after.tasks, d), d, auto=d in settled.auto_depends)
            for d in landed.depends_on
        ),
        added=settled.auto_depends,
        already_behind=settled.already_behind,
        chain=why_blocked(after, session_id, landed_id),
        claimable=landed.is_claimable(after.tasks),
        writers=landed.writers(after.tasks),
    )
