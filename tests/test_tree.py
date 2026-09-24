"""
The working tree as the filesystem has it.

``session_base`` decides the base every measured write is expressed against, so its
contract is load-bearing twice over: once for ``ui/projects`` which names a project
from it, and once for ``model.relative_write`` which prefix-matches absolute writes
against it. The second is the newer job and the one with no display to notice when
it goes wrong.

``lies_inside_checkout`` shares that resolution and asks a different question of it:
whether a directory about to be handed to an agent is pptmstr's own source. Its tests
build real symlinks, because a fixture of strings would agree with a string compare.
``source_tree_of`` is what supplies the checkout side of that question.
"""

from __future__ import annotations

import posixpath
from pathlib import Path

import pytest

from pptmstr.model import relative_write
from pptmstr.tree import lies_inside_checkout, session_base, source_tree_of


def test_a_git_root_resolves_to_itself(tmp_path: Path) -> None:
    """
    The common case and the one that must not move: a session launched at the root of
    a checkout measures against that root, as it did when the base was searched for
    rather than taken.
    """
    repo = tmp_path / "orbital"
    (repo / ".git").mkdir(parents=True)
    assert session_base(str(repo)) == str(repo.resolve())


@pytest.mark.parametrize("marker", ["git-dir", "git-file", "nothing"])
def test_nothing_inside_the_directory_moves_the_base(tmp_path: Path, marker: str) -> None:
    """
    The base is the directory the operator specified, and no property of that
    directory or of its parents can shift it somewhere else.

    The three fixtures are the three the old walk distinguished: a checkout root
    (``.git`` as a directory), a worktree or submodule (``.git`` as a *file* holding a
    gitdir pointer), and a directory that is neither. Each is a subdirectory of a
    checkout, which is the case that actually changed -- the walk answered
    ``orbital``, and the operator said ``orbital/tools/parsers``.
    """
    repo = tmp_path / "orbital"
    (repo / ".git").mkdir(parents=True)
    inner = repo / "tools" / "parsers"
    inner.mkdir(parents=True)
    if marker == "git-dir":
        (inner / ".git").mkdir()
    elif marker == "git-file":
        (inner / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")

    assert session_base(str(inner)) == str(inner.resolve())


def test_a_directory_in_no_repository_resolves_to_itself(tmp_path: Path) -> None:
    """
    Not every project is a checkout of anything. A directory that encloses nothing and
    is enclosed by nothing is its own base, which is the same answer every other
    directory gets.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert session_base(str(scratch)) == str(scratch.resolve())


def test_the_answer_is_always_absolute(tmp_path: Path) -> None:
    """
    **The property everything downstream rests on, and the one nothing else asserts.**

    ``relative_write`` puts a base through ``_absolute``, which returns None for a
    relative path -- so a relative answer here does not raise or diverge, it silently
    falls back to measuring against ``cwd``. That is the pre-``session_base``
    behaviour, which means the whole units fix would become a no-op with every other
    test in the suite still green. ``ui/projects`` would not notice either: it takes
    ``.name``, and a relative path has one.
    """
    repo = tmp_path / "orbital"
    (repo / "pptmstr").mkdir(parents=True)
    (repo / ".git").mkdir()
    for probe in (str(repo), str(repo / "pptmstr"), str(tmp_path / "not-a-repo")):
        assert posixpath.isabs(session_base(probe)), probe


def test_a_missing_directory_still_answers_absolutely(tmp_path: Path) -> None:
    """
    The launcher's cwd field is free text and the directory may not exist. Returning
    something relative here would put the session in the silent fallback above.
    """
    gone = tmp_path / "gone" / "vendor-sync"
    assert posixpath.isabs(session_base(str(gone)))


def test_the_base_it_returns_is_the_one_relative_write_can_strip(tmp_path: Path) -> None:
    """
    The integration the two halves are only correct together, at an ordinary path:
    one base feeds both of ``relative_write``'s branches, and an absolute write and a
    relative one typed in the same directory land on the same declared path. It
    constructs no symlink -- the resolution is pinned by the two tests below.
    """
    repo = tmp_path / "orbital"
    inner = repo / "pptmstr"
    inner.mkdir(parents=True)
    (repo / ".git").mkdir()
    base = session_base(str(inner))

    written = posixpath.join(str(repo.resolve()), "pptmstr", "store.py")
    assert relative_write(written, str(inner), base) == "store.py"
    # And a relative write, which is typed against cwd rather than the base.
    assert relative_write("store.py", str(inner), base) == "store.py"


def test_a_session_below_a_checkout_measures_against_where_it_was_launched(
    tmp_path: Path,
) -> None:
    """
    The units this change moves, stated as the consequence a lead reads.

    A session launched in ``orbital/pptmstr`` used to be told to declare
    ``pptmstr/store.py`` -- the walk's units -- and now declares ``store.py``. Both
    spellings are defensible; what is not defensible is the instruction naming one and
    the measurement using the other, which is the defect this whole change exists to
    close. A base that still climbed would make the second assertion fail and the
    first pass, so the pair is what discriminates.
    """
    repo = tmp_path / "orbital"
    inner = repo / "pptmstr"
    inner.mkdir(parents=True)
    (repo / ".git").mkdir()
    base = session_base(str(inner))

    assert base == str(inner.resolve())
    assert relative_write("store.py", str(inner), base) == "store.py"


def test_a_symlinked_directory_resolves_to_the_directory_it_points_at(tmp_path: Path) -> None:
    """
    ``session_base`` promises a *resolved* path, and nothing else in this suite can
    hold it to that. Both live callers hand it ``os.path.realpath`` output already --
    ``ui/launcher.LauncherState.spec`` and ``app``'s headless launch -- so the
    resolution here runs on a path that carries no symlink, and deleting it leaves
    every other assertion in this file green. The contract is pinned where it is
    promised rather than where it currently happens to hold anyway, because a caller
    reading the docstring is entitled to stop resolving on the strength of it.
    """
    real = tmp_path / "volume" / "orbital"
    real.mkdir(parents=True)
    link = tmp_path / "orbital-link"
    link.symlink_to(real, target_is_directory=True)

    assert session_base(str(link)) == str(real.resolve())
    # The spellings genuinely differ, so the assertion above is discriminating.
    assert str(link) != str(real.resolve())


def test_an_unresolved_base_would_place_a_write_nowhere(tmp_path: Path) -> None:
    """
    What the contract above buys, stated as the consequence rather than as the
    property. ``relative_write`` prefix-matches and resolves nothing itself, so a base
    still carrying a link's spelling shares no prefix with a write path spelled
    against the directory the link points at, and the caller records a compliant
    agent's write as unplaced -- silence, not a wrong answer.

    ``cwd`` is supplied already resolved, as both launch paths supply it, so
    ``session_base``'s own resolution is the only thing under test.
    """
    real = tmp_path / "volume" / "orbital"
    (real / "pptmstr").mkdir(parents=True)
    link = tmp_path / "orbital-link"
    link.symlink_to(real, target_is_directory=True)

    cwd = posixpath.join(str(real.resolve()), "pptmstr")
    base = session_base(str(link / "pptmstr"))
    assert relative_write(posixpath.join(cwd, "store.py"), cwd, base) == "store.py"


# --- is this directory inside that one -------------------------------------------


def _checkout(tmp_path: Path) -> Path:
    """
    A stand-in for pptmstr's own checkout, with a sibling whose name has the
    checkout's as a string prefix. The sibling exists in every fixture rather than
    only in the test that names it, so a predicate that swept it up would have several
    chances to be caught rather than one.
    """
    checkout = tmp_path / "pptmstr"
    (checkout / "pptmstr").mkdir(parents=True)
    (tmp_path / "pptmstr-scratch").mkdir()
    return checkout


def test_the_checkout_root_itself_counts_as_inside_it(tmp_path: Path) -> None:
    """
    The default-launch case: ``LaunchSpec.cwd`` is ``"."``, so the directory handed to
    an agent is the checkout root exactly. A predicate recognising only strict
    descendants would answer no to the one launch that most needs a yes.
    """
    checkout = _checkout(tmp_path)
    assert lies_inside_checkout(str(checkout), str(checkout))


def test_a_subdirectory_of_the_checkout_is_inside_it(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    assert lies_inside_checkout(str(checkout / "pptmstr"), str(checkout))


def test_a_sibling_sharing_a_string_prefix_is_not_inside(tmp_path: Path) -> None:
    """
    The likeliest defect here, so the trap is asserted before the answer is: the two
    paths really do share a prefix, and prefix matching really would answer yes. It
    would refuse every launch in a directory named after the checkout -- a worktree, a
    clone, the very scratch copy §8a.6's remedy would create.
    """
    checkout = _checkout(tmp_path)
    sibling = tmp_path / "pptmstr-scratch"
    assert str(sibling).startswith(str(checkout))
    assert not lies_inside_checkout(str(sibling), str(checkout))


def test_an_unrelated_directory_is_not_inside(tmp_path: Path) -> None:
    checkout = _checkout(tmp_path)
    elsewhere = tmp_path / "vendor-sync"
    elsewhere.mkdir()
    assert not lies_inside_checkout(str(elsewhere), str(checkout))


def test_a_symlink_reaching_into_the_checkout_is_inside_it(tmp_path: Path) -> None:
    """
    The typed form shares nothing with the checkout, which is exactly why the string
    comparison is not the one made.
    """
    checkout = _checkout(tmp_path)
    link = tmp_path / "work"
    link.symlink_to(checkout / "pptmstr")
    assert not str(link).startswith(str(checkout))
    assert lies_inside_checkout(str(link), str(checkout))


def test_a_symlink_leaving_the_checkout_is_not_inside_it(tmp_path: Path) -> None:
    """
    The other direction, and it is not the same test. What the sandbox may write is
    where the directory *is*: a path under the checkout whose real location is outside
    it exposes nothing in the checkout, and refusing it would be a cost with nothing
    bought.
    """
    checkout = _checkout(tmp_path)
    elsewhere = tmp_path / "vendor-sync"
    elsewhere.mkdir()
    (checkout / "scratch").symlink_to(elsewhere)
    assert not lies_inside_checkout(str(checkout / "scratch"), str(checkout))


def test_the_checkout_side_is_resolved_too(tmp_path: Path) -> None:
    """
    Resolving only the candidate leaves every other test here still green -- the
    fixtures hand a real path in as the checkout. It comes from ``source_tree_of``
    today and is resolved already; the moment it comes from anywhere else, a link or a
    relative form on that side answers no and the containment is gone.
    """
    checkout = _checkout(tmp_path)
    by_link = tmp_path / "checkout-link"
    by_link.symlink_to(checkout)
    assert lies_inside_checkout(str(checkout / "pptmstr"), str(by_link))


def test_a_directory_that_does_not_exist_is_placed_by_where_it_would_be(
    tmp_path: Path,
) -> None:
    """
    The cwd field is free text and the operator may name a directory not created yet.
    A missing path is not an unlocatable one, and both answers stay available for it.
    """
    checkout = _checkout(tmp_path)
    assert lies_inside_checkout(str(checkout / "gone" / "deeper"), str(checkout))
    assert not lies_inside_checkout(str(tmp_path / "gone" / "deeper"), str(checkout))


def test_a_relative_path_resolves_against_this_process_directory(
    tmp_path: Path, monkeypatch
) -> None:
    """
    ``"."`` is the default and it is the whole hazard: it names the checkout without
    spelling any part of it.
    """
    checkout = _checkout(tmp_path)
    monkeypatch.chdir(checkout)
    assert lies_inside_checkout(".", str(checkout))
    assert lies_inside_checkout("pptmstr", str(checkout))
    monkeypatch.chdir(tmp_path)
    assert not lies_inside_checkout("pptmstr-scratch", str(checkout))


def test_a_path_with_no_location_at_all_is_treated_as_inside(tmp_path: Path) -> None:
    """
    Fail closed, on either side. ``~`` for a user with no home leaves the filesystem
    unable to say where the directory is, and a no here would report a directory
    nobody can locate as safe to hand to an agent that writes unattended.
    """
    checkout = _checkout(tmp_path)
    nowhere = "~nosuchuser-8f3c1a/work"
    assert lies_inside_checkout(nowhere, str(checkout))
    assert lies_inside_checkout(str(checkout / "pptmstr"), nowhere)


def test_a_symlink_loop_answers_rather_than_raising(tmp_path: Path) -> None:
    """
    The loop is placed inside the checkout so the assertion holds whichever way this
    interpreter resolves one: 3.11's ``Path.resolve`` raises ``RuntimeError`` and the
    fail-closed branch answers yes, while a realpath-based resolve returns the path
    unfollowed -- still under the checkout, still yes.
    """
    checkout = _checkout(tmp_path)
    (checkout / "a").symlink_to(checkout / "b")
    (checkout / "b").symlink_to(checkout / "a")
    assert lies_inside_checkout(str(checkout / "a"), str(checkout))


# --- which tree is this package running out of -----------------------------------


def _package_in(parent: Path) -> Path:
    package = parent / "pptmstr"
    (package / "ui").mkdir(parents=True)
    return package


def test_a_package_in_a_source_tree_answers_with_the_checkout_root(tmp_path: Path) -> None:
    """
    The layout this repository has. Answering with the package directory instead would
    leave ``planning/``, ``scripts/``, ``tests/`` and ``CLAUDE.md`` outside the tree
    pptmstr refuses to hand to an unattended agent -- a narrowing with no error and no
    surface, which is why the sibling file is asserted to be inside as well.
    """
    checkout = tmp_path / "work" / "pptmstr"
    package = _package_in(checkout)
    (checkout / "pyproject.toml").write_text("[project]\nname = 'pptmstr'\n")
    (checkout / "planning").mkdir()

    answer = source_tree_of(str(package))
    assert answer == str(checkout.resolve())
    assert lies_inside_checkout(str(checkout / "planning"), answer)


def test_an_installed_package_answers_with_its_own_directory(tmp_path: Path) -> None:
    """
    Installed, there is no checkout at all: the parent is ``site-packages``, a
    directory of unrelated projects. Claiming it as pptmstr's tree would refuse every
    launch inside any of them, and what is actually being protected -- the
    ``approval.py`` that gates the next launch -- is inside the package either way.
    """
    site = tmp_path / "site-packages"
    package = _package_in(site)
    (site / "some_other_project").mkdir()

    answer = source_tree_of(str(package))
    assert answer == str(package.resolve())
    assert not lies_inside_checkout(str(site / "some_other_project"), answer)


def test_the_answer_is_resolved_so_the_checkout_side_can_be_compared(tmp_path: Path) -> None:
    """
    ``lies_inside_checkout`` resolves both sides, but it is handed this one and the
    docstring there says it arrives resolved. A link-spelled answer would still work
    there and would be wrong for any caller that took the promise at its word.
    """
    checkout = tmp_path / "work" / "pptmstr"
    package = _package_in(checkout)
    (checkout / "pyproject.toml").write_text("[project]\n")
    link = tmp_path / "by-link"
    link.symlink_to(checkout, target_is_directory=True)

    assert source_tree_of(str(link / "pptmstr")) == str(checkout.resolve())
    assert str(package) != str(link / "pptmstr")
