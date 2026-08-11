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


def test_a_subdirectory_files_under_its_repo(tmp_path: Path) -> None:
    """
    The reason the derivation walks up at all.

    An operator running one session in a repo and another in that repo's tools
    directory is working on one project; splitting them into two lanes would be a
    distinction the layout invented rather than one the operator made.
    """
    repo = tmp_path / "orbital"
    (repo / ".git").mkdir(parents=True)
    deep = repo / "tools" / "parsers"
    deep.mkdir(parents=True)
    assert project_key(str(deep)) == "orbital"


def test_a_worktree_files_under_its_repo(tmp_path: Path) -> None:
    """
    A worktree or submodule checkout has .git as a *file* holding a gitdir pointer.

    Testing for a directory would file every worktree under its own name, which is
    wrong in the one situation where an operator most wants two checkouts of the
    same project grouped together.
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
