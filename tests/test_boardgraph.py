"""
Graph queries over a board: ``why_blocked``, ``ready``, ``open_tasks`` and
``dry_run_declare``.

Boards are built by applying intents through ``store._apply``, so every edge in
them is one the store would have written, auto edges included. The two that are
not -- a held task with a dependency outstanding, and a cycle -- are states the
store does not enter, built by hand and said to be. The task ids copy the
ui-rework board the planning record
(``planning/2026-10-16-the-board-needs-graph-queries.md``) draws its scenarios
from.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

import pytest

from pptmstr import drawable
from pptmstr.boardgraph import (
    DryRun,
    Edge,
    RootKind,
    Verdict,
    dry_run_declare,
    is_test_path,
    open_tasks,
    ready,
    why_blocked,
)
from pptmstr.effects import TaskWriteSettled
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    Intent,
    SessionDetached,
    TaskClaimRequested,
    TaskClosed,
    TaskCompleted,
    TaskDeclared,
    TaskWithdrawn,
)
from pptmstr.model import (
    ALL_WRITERS,
    AgentState,
    NodeId,
    Snapshot,
    Task,
    TaskRefusal,
    TaskState,
)
from pptmstr.store import _apply

S = "sess-ui"
OTHER = "sess-other"
LEAD: NodeId = (S, None)
OTHER_LEAD: NodeId = (OTHER, None)


class Board:
    """
    A snapshot grown one intent at a time through the real reducer, with a
    clock that moves so declaration order is the order of the calls.
    """

    def __init__(self) -> None:
        self.snap = Snapshot.empty()
        self.now = 1.0
        self.builders: list[NodeId] = []
        self.apply(AgentSpawned(node_id=LEAD, parent=None, task="", model="opus", started_at=0))
        self.apply(
            AgentSpawned(node_id=OTHER_LEAD, parent=None, task="", model="opus", started_at=0)
        )

    def apply(self, intent: Intent) -> None:
        self.snap, _ = _apply(self.snap, intent, self.now)
        self.now += 1.0

    def builder(self, n: int) -> NodeId:
        """The node addressed ``builder-n`` (``builder`` for the first)."""
        while len(self.builders) < n:
            node: NodeId = (S, f"agent-b{len(self.builders) + 1}")
            self.apply(
                AgentSpawned(
                    node_id=node,
                    parent=LEAD,
                    task="",
                    model="opus",
                    started_at=0,
                    agent_type="builder",
                )
            )
            self.builders.append(node)
        return self.builders[n - 1]

    def declare(
        self,
        tid: str,
        *,
        touches: tuple[str, ...] = (),
        deps: tuple[str, ...] = (),
        by: NodeId = LEAD,
    ) -> None:
        self.apply(
            TaskDeclared(
                task=Task(
                    id=tid, title=tid, touches=touches, depends_on=deps, declared_at=self.now
                ),
                node_id=by,
            )
        )
        assert tid in self.snap.tasks

    def detach_other(self) -> None:
        """Take OTHER's session off the fleet, as the operator does."""
        self.apply(AgentFinished(OTHER_LEAD, AgentState.DONE, ended_at=self.now))
        self.apply(SessionDetached(OTHER, request_id="detach"))
        assert self.snap.off_fleet(OTHER)

    def withdraw(self, tid: str, reason: str = "superseded") -> None:
        task = self.snap.tasks[tid]
        assert task.declared_by is not None
        self.apply(TaskWithdrawn(node_id=task.declared_by, task_id=tid, reason=reason))
        assert self.snap.tasks[tid].withdrawn

    def claim(self, tid: str, node: NodeId) -> None:
        self.apply(TaskClaimRequested(node_id=node, request_id=f"claim-{tid}", task_id=tid))
        assert self.snap.tasks[tid].claimed_by == node

    def complete(self, tid: str) -> None:
        node = self.snap.tasks[tid].claimed_by
        assert node is not None
        self.apply(TaskCompleted(node_id=node, task_id=tid, at=self.now))
        assert self.snap.tasks[tid].state is TaskState.COMPLETED

    def finish(self, tid: str) -> None:
        self.claim(tid, self.builder(1))
        self.complete(tid)

    def hand_edit(self, tid: str, **changes: object) -> None:
        """A change the store would not make, for a state it does not enter."""
        tasks = dict(self.snap.tasks)
        tasks[tid] = dataclasses.replace(tasks[tid], **changes)  # type: ignore[arg-type]
        self.snap = dataclasses.replace(self.snap, tasks=MappingProxyType(tasks))


def ids(path: tuple[object, ...]) -> list[str]:
    return [h.id for h in path]  # type: ignore[attr-defined]


# --- test paths -------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_style_probe.py", True),
        ("tests/fixtures/board.json", True),
        ("pkg/tests/helpers.py", True),
        ("tests/conftest.py", True),
        ("conftest.py", True),
        ("pptmstr/test_thing.py", True),
        ("pptmstr/thing_test.py", True),
        ("pptmstr/launcher.py", False),
        ("pptmstr/contest.py", False),
        ("pptmstr/latest_tests.py", False),
        ("tests.py", False),
        ("test_data.json", False),
        ("pptmstr/testsuite/x.py", False),
        # A touches entry naming the directory itself, as normalised_touches
        # leaves "tests/".
        ("tests", True),
        ("pkg/tests", True),
        ("pkg/testsuite", False),
    ],
)
def test_a_test_file_is_one_under_tests_or_named_as_one(path: str, expected: bool) -> None:
    assert is_test_path(path) is expected


# --- why_blocked ------------------------------------------------------------


def test_b1_a_three_deep_chain_is_returned_in_order_and_ends_at_the_unheld_root() -> None:
    b = Board()
    b.declare("delete-rich-pane")
    b.declare("baseline-sites", deps=("delete-rich-pane",))
    b.declare("theme-fix-2", deps=("baseline-sites",))
    b.declare("turn-boundary", deps=("theme-fix-2",))

    answer = why_blocked(b.snap, S, "turn-boundary")

    assert answer.verdict is Verdict.BLOCKED
    assert len(answer.roots) == 1
    (root,) = answer.roots
    assert root.id == "delete-rich-pane"
    assert root.kind is RootKind.UNCLAIMED
    assert ids(root.path) == ["theme-fix-2", "baseline-sites", "delete-rich-pane"]
    assert [h.state for h in root.path] == [TaskState.PENDING] * 3
    assert [h.holder for h in root.path] == [None] * 3
    assert [(h.edge.waiting, h.edge.on) for h in root.path] == [
        ("turn-boundary", "theme-fix-2"),
        ("theme-fix-2", "baseline-sites"),
        ("baseline-sites", "delete-rich-pane"),
    ]
    assert answer.cycle == ()


def test_b2_every_root_comes_once_with_its_shortest_path() -> None:
    b = Board()
    b.declare("ini-isolation")
    b.declare("digest-store")
    b.claim("digest-store", b.builder(3))
    b.declare("digest-wire", deps=("digest-store",))
    # Two routes to digest-store: two hops through history-to-digest and
    # wake-asker, three through detail-header. The longer route is the
    # dependency declared last, which is the one a depth-first walk takes
    # first.
    b.declare("history-to-digest", deps=("digest-store",))
    b.declare("wake-asker", deps=("digest-store",))
    b.declare("detail-header", deps=("ini-isolation", "digest-wire"))
    b.declare("detail-actions", deps=("history-to-digest", "wake-asker", "detail-header"))

    answer = why_blocked(b.snap, S, "detail-actions")

    assert answer.verdict is Verdict.BLOCKED
    by_id = {r.id: r for r in answer.roots}
    assert [r.id for r in answer.roots] == ["digest-store", "ini-isolation"]
    assert by_id["digest-store"].kind is RootKind.HELD
    assert ids(by_id["digest-store"].path) == ["history-to-digest", "digest-store"]
    assert by_id["digest-store"].path[-1].holder == "builder-3"
    assert by_id["ini-isolation"].kind is RootKind.UNCLAIMED
    assert ids(by_id["ini-isolation"].path) == ["detail-header", "ini-isolation"]


def test_b3_a_claimable_task_is_answered_claimable_with_no_chain() -> None:
    b = Board()
    b.declare("baseline-sites")
    b.declare("theme-fix-2", deps=("baseline-sites",))
    b.finish("baseline-sites")

    answer = why_blocked(b.snap, S, "theme-fix-2")

    assert answer.verdict is Verdict.CLAIMABLE
    assert answer.roots == ()


def test_b4_the_chain_stops_at_a_held_task_and_names_its_holder() -> None:
    b = Board()
    b.declare("delete-rich-pane")
    b.declare("baseline-sites")
    b.claim("baseline-sites", b.builder(45))
    # Held while a dependency is outstanding: the store does not get here (a
    # claim needs the task claimable), but ``blocked_on`` answers for it, and
    # the walk must still stop at the holder rather than go past it.
    b.hand_edit("baseline-sites", depends_on=("delete-rich-pane",))
    b.declare("theme-fix-2", deps=("baseline-sites",))
    b.declare("turn-boundary", deps=("theme-fix-2",))

    answer = why_blocked(b.snap, S, "turn-boundary")

    (root,) = answer.roots
    assert root.id == "baseline-sites"
    assert root.kind is RootKind.HELD
    assert ids(root.path) == ["theme-fix-2", "baseline-sites"]
    assert root.path[-1].state is TaskState.CLAIMED
    assert root.path[-1].holder == "builder-45"
    assert "delete-rich-pane" not in repr(answer)


def test_a_claimed_task_is_answered_as_claimed_and_by_whom() -> None:
    b = Board()
    b.declare("baseline-sites")
    b.claim("baseline-sites", b.builder(2))

    answer = why_blocked(b.snap, S, "baseline-sites")

    assert answer.verdict is Verdict.CLAIMED
    assert answer.holder == "builder-2"
    assert answer.roots == ()


def test_a_completed_task_is_answered_as_completed() -> None:
    b = Board()
    b.declare("baseline-sites")
    b.finish("baseline-sites")

    assert why_blocked(b.snap, S, "baseline-sites").verdict is Verdict.COMPLETED


def test_f3_an_unknown_id_is_unknown_and_not_blocked_or_ready() -> None:
    b = Board()
    b.declare("theme-fix-2")

    answer = why_blocked(b.snap, S, "theme-fix-3")

    assert answer.verdict is Verdict.UNKNOWN
    assert answer.roots == ()


def test_f3_another_sessions_task_is_unknown_here() -> None:
    b = Board()
    b.declare("their-task", by=OTHER_LEAD)

    assert why_blocked(b.snap, S, "their-task").verdict is Verdict.UNKNOWN
    assert why_blocked(b.snap, OTHER, "their-task").verdict is Verdict.CLAIMABLE


def test_a_dependency_the_board_has_no_task_for_is_a_root_of_its_own_kind() -> None:
    b = Board()
    b.declare("their-task", by=OTHER_LEAD)
    b.declare("gate-2", deps=("theme-fix-3", "their-task"))

    answer = why_blocked(b.snap, S, "gate-2")

    assert [(r.id, r.kind) for r in answer.roots] == [
        ("theme-fix-3", RootKind.NOT_ON_BOARD),
        ("their-task", RootKind.NOT_ON_BOARD),
    ]
    for root in answer.roots:
        (hop,) = root.path
        assert hop.state is None
        assert hop.holder is None
        assert hop.edge.shared == ()


def test_a_cycle_ends_the_walk_and_is_reported() -> None:
    b = Board()
    b.declare("ini-isolation")
    b.declare("composer-margins")
    b.declare("empty-bookmarks", deps=("composer-margins", "ini-isolation"))
    # The store refuses a declaration that closes a cycle; built by hand.
    b.hand_edit("composer-margins", depends_on=("empty-bookmarks",))
    b.declare("gate-2", deps=("empty-bookmarks",))

    answer = why_blocked(b.snap, S, "gate-2")

    assert [r.id for r in answer.roots] == ["ini-isolation"]
    assert set(answer.cycle) == {"empty-bookmarks", "composer-margins"}


def test_a_diamond_is_not_a_cycle() -> None:
    b = Board()
    b.declare("baseline-sites")
    b.declare("theme-fix-2", deps=("baseline-sites",))
    b.declare("ini-isolation", deps=("baseline-sites",))
    b.declare("turn-boundary", deps=("theme-fix-2", "ini-isolation"))

    answer = why_blocked(b.snap, S, "turn-boundary")

    assert [r.id for r in answer.roots] == ["baseline-sites"]
    assert answer.cycle == ()


def test_an_overlap_edge_carries_its_shared_files_and_test_only_mark() -> None:
    b = Board()
    b.declare("theme-fix", touches=("pptmstr/theme.py", "tests/test_style_probe.py"))
    b.declare("font-raster-probe", touches=("./tests/test_style_probe.py", "scripts/probe.py"))
    b.declare("launcher-place", touches=("pptmstr/theme.py",))

    probe = why_blocked(b.snap, S, "font-raster-probe")
    place = why_blocked(b.snap, S, "launcher-place")

    assert probe.roots[0].path[0].edge == Edge(
        waiting="font-raster-probe",
        on="theme-fix",
        shared=("tests/test_style_probe.py",),
        test_only=True,
    )
    assert place.roots[0].path[0].edge == Edge(
        waiting="launcher-place", on="theme-fix", shared=("pptmstr/theme.py",), test_only=False
    )


def test_an_edge_sharing_a_test_file_and_a_source_file_is_not_test_only() -> None:
    b = Board()
    b.declare("theme-fix", touches=("pptmstr/theme.py", "tests/test_style_probe.py"))
    b.declare("theme-fix-2", touches=("tests/test_style_probe.py", "pptmstr/theme.py"))

    edge = why_blocked(b.snap, S, "theme-fix-2").roots[0].path[0].edge

    assert edge.shared == ("tests/test_style_probe.py", "pptmstr/theme.py")
    assert edge.test_only is False


def _ui_rework_chain(b: Board) -> None:
    b.declare("delete-rich-pane")
    b.declare("baseline-sites", deps=("delete-rich-pane",))
    b.claim("delete-rich-pane", b.builder(1))
    b.declare("theme-fix-2", deps=("baseline-sites",))
    b.declare("turn-boundary", deps=("theme-fix-2",))


def test_b5_the_answer_is_bounded_by_the_chain_and_not_by_the_board() -> None:
    small = Board()
    _ui_rework_chain(small)
    large = Board()
    _ui_rework_chain(large)
    for n in range(66):
        large.declare(f"other-{n}", touches=(f"pptmstr/other_{n}.py",))
    for n in range(0, 66, 3):
        large.claim(f"other-{n}", large.builder(2 + n // 3))
    assert sum(t.belongs_to(S) for t in large.snap.tasks.values()) == 70

    on_small = why_blocked(small.snap, S, "turn-boundary")
    on_large = why_blocked(large.snap, S, "turn-boundary")

    # Equal, so the same length rendered: nothing about the 66 other tasks is
    # in the answer, and a reply built from it grows with the chain alone.
    assert on_large == on_small
    assert len(repr(on_large)) == len(repr(on_small))
    assert "other-" not in repr(on_large)


# --- ready -----------------------------------------------------------------


def _mid_session(b: Board) -> None:
    """
    The ui-rework board mid-session: diff-parity is ready and unblocks nothing,
    theme-fix-2 is ready and six pending tasks wait on it, two of them through
    a diamond, and one claimed task sits on the way.
    """
    b.declare("diff-parity")
    b.declare("baseline-sites")
    b.declare("theme-fix-2", deps=("baseline-sites",))
    b.declare("turn-boundary", deps=("theme-fix-2",))
    b.declare("ini-isolation", deps=("theme-fix-2",))
    b.declare("composer-margins", deps=("turn-boundary", "ini-isolation"))
    b.declare("empty-bookmarks", deps=("composer-margins",))
    b.declare("launcher-place")
    b.declare("detail-header", deps=("theme-fix-2", "launcher-place"))
    b.declare("gate-2", deps=("empty-bookmarks", "detail-header"))
    b.declare("held-one")
    b.declare("detail-actions")
    b.finish("baseline-sites")
    b.claim("held-one", b.builder(2))


def test_e1_ready_is_exactly_the_claimable_tasks_and_none_held() -> None:
    b = Board()
    _mid_session(b)

    got = {r.task_id for r in ready(b.snap, S)}

    claimable = {
        t.id for t in b.snap.tasks.values() if t.belongs_to(S) and t.is_claimable(b.snap.tasks)
    }
    assert got == claimable
    assert got == {"diff-parity", "theme-fix-2", "launcher-place", "detail-actions"}


def test_e2_the_task_that_unblocks_more_ranks_first_and_ties_keep_declaration_order() -> None:
    b = Board()
    _mid_session(b)

    ranked = ready(b.snap, S)

    # theme-fix-2: turn-boundary, ini-isolation, composer-margins (reached
    # through both, counted once), empty-bookmarks, detail-header, gate-2.
    assert [(r.task_id, r.unblocks) for r in ranked] == [
        ("theme-fix-2", 6),
        ("launcher-place", 2),
        ("diff-parity", 0),
        ("detail-actions", 0),
    ]


def test_ready_counts_only_pending_waiters() -> None:
    b = Board()
    b.declare("baseline-sites")
    b.declare("theme-fix-2", deps=("baseline-sites",))
    b.declare("turn-boundary", deps=("theme-fix-2",))
    b.finish("baseline-sites")
    b.claim("theme-fix-2", b.builder(2))
    # A claimed waiter is not pending work, but what waits behind it is.
    b.hand_edit("theme-fix-2", depends_on=("baseline-sites", "fresh"))
    b.declare("fresh")

    assert [(r.task_id, r.unblocks) for r in ready(b.snap, S)] == [("fresh", 1)]


def test_ready_is_scoped_to_the_board() -> None:
    b = Board()
    b.declare("theirs", by=OTHER_LEAD)
    b.declare("mine")

    assert [r.task_id for r in ready(b.snap, S)] == ["mine"]


# --- open_tasks -------------------------------------------------------------


def test_e3_open_tasks_omit_completed_and_closed_ones() -> None:
    b = Board()
    b.declare("baseline-sites")
    b.declare("delete-rich-pane")
    b.declare("theme-fix-2", deps=("baseline-sites",))
    b.declare("turn-boundary")
    b.declare("theirs", by=OTHER_LEAD)
    b.finish("baseline-sites")
    b.apply(TaskClosed(task_id="delete-rich-pane", reason="withdrawn on the operator's word"))
    b.claim("turn-boundary", b.builder(2))

    assert [r.id for r in open_tasks(b.snap, S)] == ["theme-fix-2", "turn-boundary"]


# --- dry_run_declare --------------------------------------------------------


def _real(snap: Snapshot, task: Task, node: NodeId) -> tuple[Snapshot, TaskWriteSettled]:
    after, effects = _apply(snap, TaskDeclared(task=task, node_id=node, request_id="real"), 99.0)
    (settled,) = effects
    assert isinstance(settled, TaskWriteSettled)
    return after, settled


def _assert_unchanged(b: Board, run: object) -> DryRun:
    before = b.snap
    tasks_before = dict(before.tasks)
    out = run()  # type: ignore[operator]
    assert b.snap is before
    assert dict(before.tasks) == tasks_before
    return out  # type: ignore[no-any-return]


def test_a1_a_shared_source_file_shows_the_edge_the_chain_and_not_claimable() -> None:
    b = Board()
    b.declare("baseline-sites")
    b.claim("baseline-sites", b.builder(45))
    b.declare("theme-fix-2", touches=("pptmstr/launcher.py",), deps=("baseline-sites",))
    count = len(b.snap.tasks)
    new = Task(id="launcher-place", title="t", touches=("pptmstr/launcher.py",))

    dry = _assert_unchanged(b, lambda: dry_run_declare(b.snap, S, new, LEAD, b.now))

    assert dry.refusal is None
    assert dry.added == ("theme-fix-2",)
    assert dry.edges == (
        Edge(
            waiting="launcher-place",
            on="theme-fix-2",
            shared=("pptmstr/launcher.py",),
            test_only=False,
            auto=True,
        ),
    )
    assert dry.chain is not None
    assert dry.chain.verdict is Verdict.BLOCKED
    (root,) = dry.chain.roots
    assert ids(root.path) == ["theme-fix-2", "baseline-sites"]
    assert root.path[-1].holder == "builder-45"
    assert dry.claimable is False
    assert len(b.snap.tasks) == count
    assert "launcher-place" not in b.snap.tasks
    assert b.snap.tasks["theme-fix-2"].depends_on == ("baseline-sites",)


def test_a2_an_edge_caused_by_a_test_file_only_is_marked_test_only() -> None:
    b = Board()
    b.declare("theme-fix", touches=("pptmstr/theme.py", "tests/test_style_probe.py"))
    new = Task(
        id="font-raster-probe",
        title="t",
        touches=("scripts/font_raster_probe.py", "tests/test_style_probe.py"),
    )

    dry = dry_run_declare(b.snap, S, new, LEAD, b.now)

    assert dry.added == ("theme-fix",)
    (edge,) = dry.edges
    assert edge.shared == ("tests/test_style_probe.py",)
    assert edge.test_only is True


def test_a4_a_replacement_with_the_same_files_would_wait_on_the_task_it_replaces() -> None:
    b = Board()
    b.declare("composer-margins", touches=("pptmstr/app.py", "pptmstr/ui/composer.py"))
    new = Task(
        id="composer-margins-2",
        title="t",
        touches=("pptmstr/app.py", "pptmstr/ui/composer.py"),
    )

    dry = dry_run_declare(b.snap, S, new, LEAD, b.now)

    assert dry.added == ("composer-margins",)
    assert dry.edges[0].shared == ("pptmstr/app.py", "pptmstr/ui/composer.py")
    assert dry.chain is not None
    assert [(r.id, r.kind) for r in dry.chain.roots] == [("composer-margins", RootKind.UNCLAIMED)]
    assert dry.claimable is False


def test_a5_no_overlap_means_no_edges_and_claimable() -> None:
    b = Board()
    b.declare("composer-margins", touches=("pptmstr/app.py",))
    new = Task(id="link-probe", title="t", touches=("scripts/link_probe.py",))

    dry = dry_run_declare(b.snap, S, new, LEAD, b.now)

    assert dry.refusal is None
    assert dry.edges == ()
    assert dry.added == ()
    assert dry.claimable is True
    assert dry.chain is not None and dry.chain.verdict is Verdict.CLAIMABLE


def test_a_dry_run_lists_declared_edges_before_the_ones_the_board_adds() -> None:
    b = Board()
    b.declare("baseline-sites")
    b.declare("theme-fix-2", touches=("pptmstr/launcher.py",))
    new = Task(
        id="launcher-place",
        title="t",
        touches=("pptmstr/launcher.py",),
        depends_on=("baseline-sites",),
    )

    dry = dry_run_declare(b.snap, S, new, LEAD, b.now)

    assert [(e.on, e.shared) for e in dry.edges] == [
        ("baseline-sites", ()),
        ("theme-fix-2", ("pptmstr/launcher.py",)),
    ]
    assert dry.added == ("theme-fix-2",)


def _agreement_cases() -> list[tuple[str, Board, Task]]:
    cases = []

    overlap = Board()
    overlap.declare("done", touches=("pptmstr/launcher.py",))
    overlap.finish("done")
    overlap.declare("theme-fix-2", touches=("pptmstr/launcher.py",))
    overlap.declare("baseline-sites", touches=("pptmstr/launcher.py", "pptmstr/theme.py"))
    overlap.declare("theirs", touches=("pptmstr/launcher.py",), by=OTHER_LEAD)
    cases.append(
        (
            "overlap",
            overlap,
            Task(id="launcher-place", title="t", touches=("./pptmstr/launcher.py",)),
        )
    )

    behind = Board()
    behind.declare("composer-margins", touches=("pptmstr/app.py",), deps=("empty-bookmarks",))
    behind.declare("ini-isolation", touches=("pptmstr/layout_ini.py",))
    cases.append(
        (
            "already-behind",
            behind,
            Task(
                id="empty-bookmarks",
                title="t",
                touches=("pptmstr/app.py", "pptmstr/layout_ini.py"),
            ),
        )
    )

    dup = Board()
    dup.declare("theme-fix-2")
    cases.append(("duplicate", dup, Task(id="theme-fix-2", title="t")))

    cycle = Board()
    cycle.declare("baseline-sites", deps=("theme-fix-3",))
    cases.append(
        ("cycle", cycle, Task(id="theme-fix-3", title="t", depends_on=("baseline-sites",)))
    )

    # Ids the reducer would rewrite on the way in (drawable.text): the printable
    # backslash-u0000, a real NUL and a lone surrogate. Both arms refuse each
    # (TaskRefusal.ESCAPED_ID), though each overlaps a task that would have
    # given it an edge.
    for name, raw in (
        ("escaped-id", "fix-\\u0000-escapes"),
        ("nul-id", "fix-\x00-nul"),
        ("surrogate-id", "fix-\udc80-surrogate"),
    ):
        escaped = Board()
        escaped.declare("theme-fix-2", touches=("pptmstr/drawable.py",))
        cases.append((name, escaped, Task(id=raw, title="t", touches=("pptmstr/drawable.py",))))

    # A dependency spelled with a NUL names a task no declaration can land, so
    # both arms refuse it (TaskRefusal.ESCAPED_ID).
    escaped_dep = Board()
    cases.append(
        ("escaped-dependency", escaped_dep, Task(id="on-it", title="t", depends_on=("base-\x00",)))
    )

    off_fleet = Board()
    off_fleet.declare("theme-fix-2", by=OTHER_LEAD)
    off_fleet.detach_other()
    cases.append(("duplicate-off-fleet", off_fleet, Task(id="theme-fix-2", title="t")))

    landing_gate = Board()
    landing_gate.declare("theme-fix-2", touches=("pptmstr/theme.py",))
    landing_gate.declare("done", touches=("pptmstr/done.py",))
    landing_gate.finish("done")
    cases.append(
        ("all-writers-gate", landing_gate, Task(id="gate-3", title="t", depends_on=(ALL_WRITERS,)))
    )

    gate = Board()
    gate.declare("gate-2", deps=(ALL_WRITERS,))
    cases.append(
        (
            "all-writers-cycle",
            gate,
            Task(id="late-writer", title="t", touches=("pptmstr/x.py",), depends_on=("gate-2",)),
        )
    )
    return cases


@pytest.mark.parametrize(
    ("board", "task"),
    [(b, t) for _, b, t in _agreement_cases()],
    ids=[name for name, _, _ in _agreement_cases()],
)
def test_a_dry_run_reports_exactly_what_the_real_declaration_does(board: Board, task: Task) -> None:
    dry = _assert_unchanged(board, lambda: dry_run_declare(board.snap, S, task, LEAD, board.now))
    after, settled = _real(board.snap, task, LEAD)

    assert dry.refusal == settled.refusal
    assert dry.added == settled.auto_depends
    assert dry.already_behind == settled.already_behind
    assert dry.held_off_fleet == settled.held_off_fleet
    assert dry.cycle == settled.cycle
    if settled.refusal is None:
        # The id the real arm landed it under, found independently of how the
        # dry run finds it.
        (landed_id,) = set(after.tasks) - set(board.snap.tasks)
        landed = after.tasks[landed_id]
        assert dry.task_id == landed_id
        assert tuple(e.on for e in dry.edges) == landed.depends_on
        assert tuple(e.on for e in dry.edges if e.auto) == settled.auto_depends
        assert dry.claimable == landed.is_claimable(after.tasks)
        assert dry.writers == landed.writers(after.tasks)
        assert dry.chain == why_blocked(after, S, landed_id)
    else:
        assert dry.edges == ()
        assert dry.chain is None
        assert dry.claimable is False


def test_the_agreement_cases_cover_each_outcome() -> None:
    by_name = {
        name: dry_run_declare(b.snap, S, t, LEAD, b.now) for name, b, t in _agreement_cases()
    }

    assert by_name["overlap"].added == ("theme-fix-2", "baseline-sites")
    assert by_name["already-behind"].already_behind == ("composer-margins",)
    assert by_name["already-behind"].added == ("ini-isolation",)
    assert by_name["duplicate"].refusal is TaskRefusal.DUPLICATE_ID
    assert by_name["duplicate"].held_off_fleet is None
    assert by_name["duplicate-off-fleet"].refusal is TaskRefusal.DUPLICATE_ID
    assert by_name["duplicate-off-fleet"].held_off_fleet == OTHER
    assert by_name["cycle"].refusal is TaskRefusal.WOULD_CYCLE
    assert by_name["cycle"].cycle == ("theme-fix-3", "baseline-sites", "theme-fix-3")
    assert by_name["all-writers-cycle"].refusal is TaskRefusal.WOULD_CYCLE
    assert by_name["all-writers-cycle"].cycle == ("late-writer", "gate-2", "late-writer")
    assert by_name["all-writers-gate"].writers == ("theme-fix-2",)
    assert by_name["all-writers-gate"].edges == ()
    for name in ("escaped-id", "nul-id", "surrogate-id"):
        assert by_name[name].refusal is TaskRefusal.ESCAPED_ID
        assert by_name[name].added == ()
    assert by_name["escaped-dependency"].refusal is TaskRefusal.ESCAPED_ID
    assert by_name["escaped-dependency"].edges == ()


def test_a_dry_run_lands_a_task_under_the_id_it_was_given() -> None:
    """
    An id the board would rewrite is refused, so the id a dry run names is the
    one it was handed, and so is its chain's.
    """
    b = Board()
    raw = "fix-\\u1234-escapes"

    dry = dry_run_declare(b.snap, S, Task(id=raw, title="t"), LEAD, b.now)
    refused = dry_run_declare(b.snap, S, Task(id="fix-\\u0000", title="t"), LEAD, b.now)

    assert drawable.text(raw) == raw
    assert dry.task_id == raw
    assert dry.chain is not None
    assert dry.chain.task_id == raw
    assert dry.chain.verdict is Verdict.CLAIMABLE
    assert dry.claimable is True
    assert refused.refusal is TaskRefusal.ESCAPED_ID and refused.chain is None


def test_only_the_edges_the_board_adds_are_marked_auto() -> None:
    """
    Both edges share the test file, so ``test_only`` marks both. Only the
    added one is ``auto``: narrowing touches cannot remove the declarer's own.
    """
    b = Board()
    b.declare("theme-fix", touches=("tests/test_x.py",))
    b.declare("font-probe", touches=("tests/test_x.py",))
    new = Task(
        id="raster-probe",
        title="t",
        touches=("tests/test_x.py",),
        depends_on=("theme-fix",),
    )

    dry = dry_run_declare(b.snap, S, new, LEAD, b.now)

    assert [(e.on, e.test_only, e.auto) for e in dry.edges] == [
        ("theme-fix", True, False),
        ("font-probe", True, True),
    ]


def test_why_blocked_does_not_claim_to_know_which_edges_were_automatic() -> None:
    b = Board()
    b.declare("theme-fix", touches=("tests/test_x.py",))
    b.declare("raster-probe", touches=("tests/test_x.py",))

    (root,) = why_blocked(b.snap, S, "raster-probe").roots

    assert root.path[0].edge.auto is None
    assert root.path[0].edge.test_only is True


def test_a_dry_run_for_a_declarer_of_another_session_is_refused_as_a_caller_mistake() -> None:
    b = Board()
    with pytest.raises(ValueError):
        dry_run_declare(b.snap, S, Task(id="x", title="x"), OTHER_LEAD, b.now)


# --- purity -----------------------------------------------------------------


def test_f1_no_query_changes_the_snapshot_it_is_given() -> None:
    b = Board()
    _mid_session(b)
    snap = b.snap
    tasks = dict(snap.tasks)
    nodes = dict(snap.nodes)

    why_blocked(snap, S, "gate-2")
    why_blocked(snap, S, "nope")
    ready(snap, S)
    open_tasks(snap, S)
    dry_run_declare(snap, S, Task(id="new", title="n", touches=("pptmstr/x.py",)), LEAD, b.now)
    dry_run_declare(snap, S, Task(id="gate-2", title="dup"), LEAD, b.now)

    assert b.snap is snap
    assert dict(snap.tasks) == tasks
    assert dict(snap.nodes) == nodes


def test_f2_a_closed_task_drops_out_of_every_chain_without_an_unblocking_step() -> None:
    """
    A withdrawn task is COMPLETED (the withdraw-task decision), as an operator
    close is; this pins the close, and the withdrawal itself is pinned where
    its tool is wired.
    """
    b = Board()
    b.declare("empty-bookmarks", touches=("pptmstr/app.py",))
    b.declare("composer-margins", touches=("pptmstr/app.py",))
    assert why_blocked(b.snap, S, "composer-margins").roots[0].id == "empty-bookmarks"

    b.apply(TaskClosed(task_id="empty-bookmarks", reason="withdrawn"))

    assert why_blocked(b.snap, S, "composer-margins").verdict is Verdict.CLAIMABLE
    assert [r.task_id for r in ready(b.snap, S)] == ["composer-margins"]


def test_a3_a_withdrawn_task_is_not_in_any_chain_at_all() -> None:
    """
    The record's A3 wording (a chain "ending at the withdrawn task") predates
    withdrawal being a state: a withdrawn task is COMPLETED, so nothing waits on
    it and no path reaches it, as a hop or as a root.
    """
    b = Board()
    b.declare("delete-rich-pane")
    b.declare("empty-bookmarks", touches=("pptmstr/app.py",), deps=("delete-rich-pane",))
    b.declare("composer-margins", touches=("pptmstr/app.py",))
    b.declare("turn-boundary", deps=("composer-margins",))
    before = why_blocked(b.snap, S, "turn-boundary")
    assert "empty-bookmarks" in {h.id for r in before.roots for h in r.path}

    b.withdraw("empty-bookmarks")

    after = why_blocked(b.snap, S, "turn-boundary")
    assert after.verdict is Verdict.BLOCKED
    assert [(r.id, r.kind) for r in after.roots] == [("composer-margins", RootKind.UNCLAIMED)]
    assert "empty-bookmarks" not in {h.id for r in after.roots for h in r.path}
    assert "delete-rich-pane" not in {h.id for r in after.roots for h in r.path}
    dry = dry_run_declare(
        b.snap, S, Task(id="composer-2", title="t", touches=("pptmstr/app.py",)), LEAD, b.now
    )
    assert "empty-bookmarks" not in {e.on for e in dry.edges}
    assert dry.chain is not None
    assert "empty-bookmarks" not in {h.id for r in dry.chain.roots for h in r.path}
