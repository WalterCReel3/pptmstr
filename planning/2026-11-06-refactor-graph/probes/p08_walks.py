"""
Claim 8 (plan B8): the five walks over Task.blocked_on (store._cycle,
store._already_behind, boardgraph.why_blocked, boardgraph._on_a_cycle, the counting
walk inside boardgraph.ready) differ only in successor/edge selection.

Mostly READ: the characterisation table below is from reading the source. Two
executed counter-examples back the parts of it that decide the verdict:

  X1. _cycle's edges are not blocked_on. A COMPLETED dependency is an edge for
      _cycle (it walks depends_on + writers) and not for blocked_on, so _cycle
      reports a cycle that a blocked_on walk (why_blocked's _on_a_cycle) does not.
  X2. why_blocked stops at a CLAIMED dependency (a 'HELD' root) and does not walk
      past it, although blocked_on edges continue beyond it; ready's counting walk
      goes through tasks of any state. Same edges, different termination.
"""

from __future__ import annotations

import dataclasses

import _common  # noqa: F401
from _common import verdict

from pptmstr import boardgraph, store
from pptmstr.model import Snapshot, Task, TaskState

S = "sess-1"
ROOT = (S, None)


def task(tid: str, deps: tuple[str, ...] = (), state: TaskState = TaskState.PENDING, at: float = 0.0, **kw):  # type: ignore[no-untyped-def]
    return Task(id=tid, title=tid, depends_on=deps, state=state, declared_at=at, declared_by=ROOT, **kw)


def snap_of(*tasks: Task) -> Snapshot:
    return dataclasses.replace(Snapshot.empty(), tasks={t.id: t for t in tasks})


print("== X1: an edge through a COMPLETED dependency")
done = task("done", deps=("new",), state=TaskState.COMPLETED, at=1.0)
new = task("new", deps=("done",), at=2.0)
tasks = {"done": done}
print(f"  done: COMPLETED, depends_on={done.depends_on}; new: PENDING, depends_on={new.depends_on}")
print(f"  store._cycle(tasks, new)          = {store._cycle(tasks, new)!r}")
print(f"  store._already_behind(.., new, ('done',)) = {store._already_behind(tasks, task('new', at=2.0), ('done',))!r}")
sn = snap_of(done, new)
print(f"  new.blocked_on(tasks)             = {new.blocked_on(sn.tasks)!r}")
wb = boardgraph.why_blocked(sn, S, "new")
print(f"  boardgraph.why_blocked(new)       = verdict={wb.verdict.name} roots={wb.roots} cycle={wb.cycle}")
x1 = bool(store._cycle(tasks, new)) and not wb.cycle

print("\n== X2: why_blocked stops at a held task; ready counts through any state")
a = task("a", at=1.0)
b = task("b", deps=("a",), state=TaskState.CLAIMED, claimed_by=(S, "w1"), at=2.0)
c = task("c", deps=("b",), at=3.0)
d = task("d", deps=("c",), at=4.0)
sn2 = snap_of(a, b, c, d)
for t in (a, b, c, d):
    print(f"  {t.id}: {t.state.name:<9} depends_on={t.depends_on} blocked_on={t.blocked_on(sn2.tasks)}")
wb2 = boardgraph.why_blocked(sn2, S, "d")
print(f"  why_blocked(d): roots={[ (r.id, r.kind.name, [h.id for h in r.path]) for r in wb2.roots]}")
print(f"  -> 'a' is reachable over blocked_on edges (d->c->b->a) but the walk stopped at b")
rd = boardgraph.ready(sn2, S)
print(f"  ready: {[(r.task_id, r.unblocks) for r in rd]}  (a's count walks reverse edges through CLAIMED b)")
x2 = all(r.id != "a" for r in wb2.roots) and any(r.task_id == "a" and r.unblocks == 2 for r in rd)

print("""
== Characterisation (READ, not executed)

walk                     start              successor (edges)                         direction   order          stops / prunes                              result
store._cycle             {new.id}           depends_on + writers(pool), ANY state;    forward     BFS (deque)    returns at first edge back to new.id;      shortest cycle path
                                            lookup via new.dependency (new's board)                              node w/o task on board: no successors
store._already_behind    -- not a walk --   calls _would_cycle(tasks, new') once per  forward     (per call)     --                                          filtered tuple of candidates
                                            candidate, new' = new with depends_on=(dep,)
boardgraph.why_blocked   {asked}            current.blocked_on(snap.tasks): undone   forward     BFS (deque)    does NOT expand: missing, CLAIMED, or a     roots (kind, shortest path)
                                            deps + writers; current.dependency                      dep with no blockers -> those become roots;  + cycle via _on_a_cycle(past)
                                                                                                    each id entered once
boardgraph._on_a_cycle   each key of past   past[id] (blocked_on edges recorded by    forward     DFS (stack),   only within past's keys; one walk per       ids that reach themselves
                         (separately)       why_blocked, only for EXPANDED tasks)                   per start      start; quadratic
boardgraph.ready (inner) {claimable t}      waiters[id] = board tasks whose           REVERSE     DFS (stack)    seen set; no pruning by state               count of PENDING reached
                                            blocked_on contains id (precomputed)

Differences beyond the edge function: direction (ready walks reverse edges), order
(BFS vs DFS -- matters for _cycle's 'shortest' and why_blocked's shortest paths),
termination (early return on target; pruning at held/leaf/missing roots), what is
accumulated (parent map / paths+roots+past / seen / count), and the graph itself
(_cycle uses a pool with new inserted; _on_a_cycle runs over a recorded sub-graph,
not over Task.blocked_on). _already_behind is not a fifth walk but a client of
_cycle.""")

print(f"X1 (_cycle edge set != blocked_on edge set) shown: {x1}")
print(f"X2 (why_blocked prunes at a held task; ready does not) shown: {x2}")
if x1 and x2:
    verdict(
        "REFUTED",
        "they do not differ only in successor selection. _cycle does not walk blocked_on at "
        "all (X1); why_blocked prunes where ready does not (X2); ready walks reverse edges; "
        "BFS vs DFS; _already_behind is a client of _cycle, not a walk; _on_a_cycle walks a "
        "recorded sub-graph. A shared traversal needs at least (start set, successors, "
        "expand-predicate, order, visitor/accumulator). Characterisation is READ; X1/X2 RUN.",
    )
else:
    verdict("CANNOT-DETERMINE", f"counter-examples did not behave as read: x1={x1} x2={x2}")
