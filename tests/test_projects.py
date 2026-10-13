"""
The project axis: deriving a lane name from a working directory.

Presentation-level derivation, so it lives in ``ui`` -- but it imports no imgui and
is tested here without a GL context, which is the point of keeping it out of the
drawing code.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from pptmstr.model import AgentRecord, AgentState, NodeId, Snapshot
from pptmstr.ui.projects import UNFILED, group_roots, project_key, roots

ROOT_A: NodeId = ("s1", None)
ROOT_B: NodeId = ("s2", None)
ROOT_C: NodeId = ("s3", None)
CHILD_A: NodeId = ("s1", "agent-a")


def record(node: NodeId, parent: NodeId | None = None, *, cwd: str | None = None) -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=parent,
        depth=0 if parent is None else 1,
        state=AgentState.THINKING,
        topic="working",
        task="a task",
        model="claude-sonnet-5",
        cwd=cwd,
    )


def snapshot(*records: AgentRecord) -> Snapshot:
    return Snapshot(
        seq=1,
        nodes=MappingProxyType({r.node_id: r for r in records}),
        order=tuple(r.node_id for r in records),
        needs_you=(),
        any_active=True,
    )


# -- derivation ----------------------------------------------------------------


def test_a_git_root_names_the_project(tmp_path: Path) -> None:
    repo = tmp_path / "orbital"
    (repo / ".git").mkdir(parents=True)
    assert project_key(str(repo)) == "orbital"


def test_a_subdirectory_names_itself(tmp_path: Path) -> None:
    """
    The lane is named after the directory the operator launched in, and the enclosing
    checkout is not consulted.

    This is a flatter grouping than the walk gave: a session in ``orbital`` and a
    session in ``orbital/tools/parsers`` are two lanes now, not one. It is the
    accepted cost of the lane agreeing with the units -- two derivations that answered
    differently would put a session in one project on screen and another in the base
    its writes are measured against, and the screen is the half that gets believed.
    """
    repo = tmp_path / "orbital"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "tools" / "parsers"
    deep.mkdir(parents=True)
    assert project_key(str(deep)) == "parsers"


def test_a_worktree_names_itself(tmp_path: Path) -> None:
    """
    A worktree or submodule checkout has .git as a *file* holding a gitdir pointer,
    and it is no more consulted than a .git directory is.
    """
    tree = tmp_path / "orbital-wt"
    tree.mkdir()
    (tree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    assert project_key(str(tree)) == "orbital-wt"


def test_a_plain_directory_names_itself(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert project_key(str(scratch)) == "scratch"


def test_a_missing_directory_still_yields_a_name(tmp_path: Path) -> None:
    """
    A cwd can be mistyped or deleted while a session is alive. The lane label is
    cosmetic, so it must degrade to the last path component rather than raise on
    the render path and take the rail down with it.
    """
    assert project_key(str(tmp_path / "gone" / "vendor-sync")) == "vendor-sync"


def test_no_directory_is_unfiled() -> None:
    assert project_key(None) == UNFILED


# -- grouping ------------------------------------------------------------------


def test_only_roots_get_cards() -> None:
    """Sub-agents are pips inside their parent's card, never cards of their own."""
    snap = snapshot(record(ROOT_A, cwd="/x/alpha"), record(CHILD_A, ROOT_A, cwd="/x/alpha"))
    assert roots(snap) == [ROOT_A]


def test_grouping_never_re_sorts(tmp_path: Path) -> None:
    """
    Projects appear in first-launch order and sessions in spawn order.

    The rail's whole claim on screen space is that position is stable enough to
    build muscle memory. Re-sorting it -- by urgency, by name, by anything -- turns
    a map into motion and leaves the operator with two inboxes.
    """
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    for path in (alpha, beta):
        (path / ".git").mkdir(parents=True)

    snap = snapshot(
        record(ROOT_A, cwd=str(alpha)),
        record(ROOT_B, cwd=str(beta)),
        record(ROOT_C, cwd=str(alpha)),
    )
    assert group_roots(snap) == [("alpha", [ROOT_A, ROOT_C]), ("beta", [ROOT_B])]


# -- a session off the fleet has no card; a closed one does -------------------------


def test_a_session_taken_off_the_fleet_is_in_no_lane_and_a_closed_card_stays() -> None:
    """
    Built by the reducer, so "off the fleet" is ``Snapshot.off_fleet``'s answer and
    not a flag this test set: the rail and the empty queue both list from here.
    """
    from pptmstr.store import Store
    from tests.test_ui_text import (
        CLOSED_ROOT,
        RAN_ROOT,
        REMOVED_ROOT,
        recall,
        run,
        run_and_remove,
    )

    store = Store()
    run_and_remove(store)
    run(store)
    recall(store)
    snap = store.snapshot()

    assert REMOVED_ROOT in snap.nodes and REMOVED_ROOT in snap.order
    assert roots(snap) == [RAN_ROOT, CLOSED_ROOT]
    assert [nid for _, lane in group_roots(snap) for nid in lane] == [RAN_ROOT, CLOSED_ROOT]


def test_a_removed_session_that_comes_back_to_life_is_listed_again() -> None:
    """
    The flag alone is not the answer. A session whose root is announced again is
    on the fleet, and reading ``AgentRecord.detached`` here would be one step of
    the store away from hiding a running session.
    """
    import dataclasses

    from pptmstr.store import Store
    from tests.test_ui_text import REMOVED_ROOT, run_and_remove

    store = Store()
    run_and_remove(store)
    snap = store.snapshot()
    assert roots(snap) == []

    # The stored half still says removed while something of the session is not
    # final: the predicate, and so this list, says it is on the fleet.
    awake = dataclasses.replace(snap.nodes[REMOVED_ROOT], state=AgentState.THINKING)
    assert awake.detached
    revived = dataclasses.replace(snap, nodes=MappingProxyType({**snap.nodes, REMOVED_ROOT: awake}))
    assert roots(revived) == [REMOVED_ROOT]
