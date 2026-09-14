"""
The working tree as the filesystem has it.

``repo_root`` decides the base every measured write is expressed against, so its
contract is load-bearing twice over: once for ``ui/projects`` which names a project
from it, and once for ``model.relative_write`` which prefix-matches absolute writes
against it. The second is the newer job and the one with no display to notice when
it goes wrong.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

from pptmstr.tree import repo_root


def test_a_git_root_resolves_to_itself(tmp_path: Path) -> None:
    repo = tmp_path / "orbital"
    (repo / ".git").mkdir(parents=True)
    assert repo_root(str(repo)) == str(repo.resolve())


def test_a_subdirectory_resolves_to_its_enclosing_root(tmp_path: Path) -> None:
    repo = tmp_path / "orbital"
    inner = repo / "pptmstr" / "ui"
    inner.mkdir(parents=True)
    (repo / ".git").mkdir()
    assert repo_root(str(inner)) == str(repo.resolve())


def test_a_worktree_resolves_to_itself(tmp_path: Path) -> None:
    """
    A worktree or submodule checkout has .git as a *file* holding a gitdir pointer.
    Testing for a directory would leave every worktree measuring in its own units
    rather than its repository's.
    """
    wt = tmp_path / "orbital-wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    assert repo_root(str(wt)) == str(wt.resolve())


def test_a_directory_in_no_repository_resolves_to_itself(tmp_path: Path) -> None:
    """
    The fallback is what makes adopting this a no-op: a session outside a repository
    has exactly one base anybody could mean, and making that class unmeasurable would
    buy nothing.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert repo_root(str(scratch)) == str(scratch.resolve())


def test_the_answer_is_always_absolute(tmp_path: Path) -> None:
    """
    **The property everything downstream rests on, and the one nothing else asserts.**

    ``relative_write`` puts a root through ``_absolute``, which returns None for a
    relative path -- so a relative answer here does not raise or diverge, it silently
    falls back to measuring against ``cwd``. That is the pre-``repo_root`` behaviour,
    which means the whole units fix would become a no-op with every other test in the
    suite still green. ``ui/projects`` would not notice either: it takes ``.name``,
    and a relative path has one.
    """
    repo = tmp_path / "orbital"
    (repo / "pptmstr").mkdir(parents=True)
    (repo / ".git").mkdir()
    for probe in (str(repo), str(repo / "pptmstr"), str(tmp_path / "not-a-repo")):
        assert posixpath.isabs(repo_root(probe)), probe


def test_a_missing_directory_still_answers_absolutely(tmp_path: Path) -> None:
    """
    The launcher's cwd field is free text and the directory may not exist. Returning
    something relative here would put the session in the silent fallback above.
    """
    gone = tmp_path / "gone" / "vendor-sync"
    assert posixpath.isabs(repo_root(str(gone)))
