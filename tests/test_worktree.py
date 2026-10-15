"""
``pptmstr/worktree.py``: what a directory is to git, where a worktree goes, making one.

The parsers are tested against literal git output, copied from git 2.39.5, because the
cases worth pinning are the ones the plain reading gets wrong and each is a specific
shape of text. The blocking functions are tested against real repositories built per
test under pytest's temporary directory, because every claim about them is a claim
about what git then has on disk -- a branch that survived, a worktree that is
registered -- and a stand-in for git would agree with whatever the module assumed.

The tests named ``test_git_...`` pin git's own behaviour rather than this module's.
They are the measurements the module's shape and its stated exposures rest on; if a
newer git stops doing these things the design can be simpler, and these are what will
say so.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from claude_agent_sdk._internal import sessions

from pptmstr import worktree
from pptmstr.worktree import (
    AddFailed,
    AddFailedBranchLeft,
    AddFailedPathHeld,
    AddFailedWorktreeLeft,
    AddTimedOut,
    BaseNotACommit,
    BranchExists,
    BranchTimedOut,
    Cleared,
    Created,
    GitFailed,
    InvalidBranch,
    LinkedWorktree,
    MainCheckout,
    NotARepository,
    NothingToName,
    PathExists,
    PathRegistered,
    Placement,
    SlugTaken,
    SlugUnjudged,
    Unclassified,
    WorktreeEntry,
    classify_checkout,
    collision,
    create,
    detect,
    list_worktrees,
    parse_worktree_list,
    place,
    preflight,
    sanitised,
    slug_for,
)

_HERE = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    No operator configuration reaches a test: a global ``core.hooksPath`` or
    ``init.defaultBranch`` would change what these repositories do. And no test runs
    against this repository or any other: the temporary directory is outside this
    checkout, and git, asked, finds no repository at it or above it.
    """
    assert not tmp_path.is_relative_to(_HERE)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for name in list(os.environ):
        if name.startswith("GIT_") and name not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
            monkeypatch.delenv(name)
    assert detect(str(tmp_path)) == NotARepository()


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """
    The fixture's own git, with an identity so it can commit. Not the module's child:
    the transport is left open, because a fixture has to be able to clone.
    """
    env = dict(os.environ)
    env.update(
        LC_ALL="C",
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env, check=check
    )


def commit(repo: Path, name: str, text: str = "x\n") -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """
    A main checkout with one commit on ``main``, at ``<tmp>/src/repo``. Resolved, so
    its path is the one git prints.
    """
    root = tmp_path / "src" / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    commit(root, "tracked.txt")
    return root


def has_branch(repo: Path, branch: str) -> bool:
    done = git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    return done.returncode == 0


def made_at(repo: Path) -> str:
    return git(repo, "rev-parse", "main").stdout.strip()


def placed(directory: Path, branch: str) -> Placement:
    checkout = detect(str(directory))
    assert isinstance(checkout, MainCheckout | LinkedWorktree), checkout
    where = place(checkout, branch)
    assert isinstance(where, Placement), where
    return where


# ---------------------------------------------------------------------------
# classify_checkout: literal output
# ---------------------------------------------------------------------------

NO_REPO = "fatal: not a git repository (or any of the parent directories): .git\n"
NOT_A_TREE = "fatal: this operation must be run in a work tree\n"


def test_no_stdout_and_gits_own_sentence_is_not_a_repository() -> None:
    assert classify_checkout(128, "", NO_REPO) == NotARepository()
    bounded = "fatal: not a git repository (or any parent up to mount point /mnt)\n"
    assert classify_checkout(128, "", bounded) == NotARepository()


def test_exit_128_that_printed_false_is_not_reported_as_no_repository() -> None:
    """
    Inside ``.git`` and in a bare repository: the same exit status as a non-repository,
    and a repository is right there.
    """
    got = classify_checkout(128, "false\n", NOT_A_TREE)
    assert isinstance(got, Unclassified)
    assert "bare" in got.reason and "git directory" in got.reason


@pytest.mark.parametrize(
    "stderr",
    [
        "fatal: not a git repository: /nonexistent/x\n",
        "fatal: cannot change to 'nodir': No such file or directory\n",
        "fatal: detected dubious ownership in repository at '/srv/x'\n",
        "",
    ],
)
def test_exit_128_for_another_reason_keeps_gits_words(stderr: str) -> None:
    """
    A broken ``gitdir:`` pointer, a missing directory and a refused owner all exit 128
    with nothing on stdout, exactly as a non-repository does.
    """
    got = classify_checkout(128, "", stderr)
    assert isinstance(got, Unclassified)
    assert got.reason == (stderr.strip() or "git exited 128 and said nothing")


def test_a_main_checkout_is_named_by_its_toplevel() -> None:
    out = "true\n/src/repo\n/src/repo/.git\n/src/repo/.git\n"
    assert classify_checkout(0, out, "") == MainCheckout("/src/repo")


def test_a_linked_worktree_carries_the_main_root_it_shares() -> None:
    out = "true\n/src/repo-worktrees/feat\n/src/repo/.git/worktrees/feat\n/src/repo/.git\n"
    assert classify_checkout(0, out, "") == LinkedWorktree(
        root="/src/repo-worktrees/feat", main_root="/src/repo"
    )


def test_a_submodule_is_not_read_as_a_main_checkout() -> None:
    """
    Its git-dir equals its common dir, which is the whole of the plain test for "main".
    """
    out = "true\n/src/repo/sub\n/src/repo/.git/modules/sub\n/src/repo/.git/modules/sub\n"
    got = classify_checkout(0, out, "")
    assert isinstance(got, Unclassified) and "submodule" in got.reason


def test_a_bare_repositorys_worktree_has_no_main_root() -> None:
    """
    The parent of its common dir is whatever directory the bare repository sits in.
    """
    out = "true\n/src/bw\n/src/bare.git/worktrees/bw\n/src/bare.git\n"
    got = classify_checkout(0, out, "")
    assert isinstance(got, Unclassified) and "bare" in got.reason


def test_a_submodules_worktree_has_no_main_root() -> None:
    out = "true\n/src/sw\n/src/repo/.git/modules/sub/worktrees/sw\n/src/repo/.git/modules/sub\n"
    assert isinstance(classify_checkout(0, out, ""), Unclassified)


def test_a_path_with_a_newline_is_unclassified_rather_than_misread() -> None:
    out = "true\n/src/nl\ndir/r\n/src/nl\ndir/r/.git\n/src/nl\ndir/r/.git\n"
    got = classify_checkout(0, out, "")
    assert isinstance(got, Unclassified) and "newline" in got.reason


def test_other_endings_are_unclassified() -> None:
    assert classify_checkout(None, "", "git could not be run: x") == Unclassified(
        "git could not be run: x"
    )
    assert classify_checkout(129, "", "usage: git rev-parse\n") == Unclassified(
        "usage: git rev-parse"
    )
    not_tree = classify_checkout(0, "false\n/a\n/a/.git\n/a/.git\n", "")
    assert isinstance(not_tree, Unclassified)


# ---------------------------------------------------------------------------
# detect: real repositories
# ---------------------------------------------------------------------------


def test_detect_names_a_main_checkout_from_its_root_and_from_below(repo: Path) -> None:
    (repo / "deep" / "er").mkdir(parents=True)
    assert detect(str(repo)) == MainCheckout(str(repo))
    assert detect(str(repo / "deep" / "er")) == MainCheckout(str(repo))


def test_detect_names_a_linked_worktree_and_its_main_root(repo: Path) -> None:
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    assert detect(str(linked)) == LinkedWorktree(root=str(linked), main_root=str(repo))


def test_detect_through_a_symlink_answers_with_the_real_root(repo: Path) -> None:
    link = repo.parent / "link"
    link.symlink_to(repo)
    assert detect(str(link)) == MainCheckout(str(repo))
    # A linked worktree reached the same way still names both of its real roots.
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    (repo.parent / "linked-link").symlink_to(linked)
    assert detect(str(repo.parent / "linked-link")) == LinkedWorktree(str(linked), str(repo))


@pytest.mark.parametrize("around", ["nothing", "another-checkout"])
def test_a_linked_worktree_whose_main_checkout_git_cannot_read_has_no_main_root(
    tmp_path: Path, around: str
) -> None:
    """
    The hazard: git answers for a linked worktree from that worktree's own admin
    directory and never looks at the main repository's ``HEAD``, so a ``main_root``
    comes back that git, asked there, does not call a checkout. The control arms are
    both of those answers from git itself: with nothing around the main repository it
    says no repository, and inside another checkout it answers for that one.
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    if around == "another-checkout":
        git(outer, "init", "-q", "-b", "main")
    main = outer / "repo"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    commit(main, "tracked.txt")
    linked = tmp_path / "linked"
    git(main, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    assert detect(str(linked)) == LinkedWorktree(root=str(linked), main_root=str(main))
    (main / ".git" / "HEAD").write_text("garbage\n")
    assert git(linked, "rev-parse", "--show-toplevel").stdout.strip() == str(linked)
    said = git(main, "rev-parse", "--show-toplevel", check=False)
    if around == "another-checkout":
        assert said.stdout.strip() == str(outer)
    else:
        assert said.returncode == 128 and worktree._NO_REPOSITORY in said.stderr

    got = detect(str(linked))

    assert isinstance(got, Unclassified), got
    assert str(linked) in got.reason and str(main) in got.reason


def _decoy(inside: Path, name: str, worktree_root: Path) -> Path:
    """
    A repository a session could build from files alone, with an admin directory
    ``worktrees/x`` that names ``worktree_root`` back, and a ``reference-transaction``
    hook that records every time git runs it. Returns the decoy's root.
    """
    decoy = inside / name
    decoy.mkdir(parents=True)
    git(decoy, "init", "-q", "-b", "main")
    commit(decoy, "decoy.txt")
    admin = decoy / ".git" / "worktrees" / "x"
    admin.mkdir(parents=True)
    (admin / "HEAD").write_text("ref: refs/heads/main\n")
    (admin / "commondir").write_text("../..\n")
    (admin / "gitdir").write_text(f"{worktree_root / '.git'}\n")
    hooks = inside / f"{name}-hooks"
    hooks.mkdir()
    hook = hooks / "reference-transaction"
    hook.write_text(f'#!/bin/sh\necho "$1" >> "{inside / (name + "-ran")}"\n')
    hook.chmod(0o755)
    git(decoy, "config", "core.hooksPath", str(hooks))
    return decoy


def test_git_follows_a_rewritten_pointer_to_a_repository_the_worktree_never_belonged_to(
    repo: Path, tmp_path: Path
) -> None:
    """
    git's own behaviour, and the reason ``main_root`` is a claim: the ``.git`` file of
    a linked worktree is the only thing saying which repository it belongs to, git
    reads whatever it names, and it asks nothing of the admin directory's ``gitdir``
    backlink -- which it also leaves as it found it.
    """
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(other, "tracked.txt")
    theirs = tmp_path / "theirs"
    git(other, "worktree", "add", "-q", "-b", "elsewhere", str(theirs), "main")
    admin = other / ".git" / "worktrees" / "theirs"
    backlink = (admin / "gitdir").read_text()
    assert backlink == f"{theirs / '.git'}\n"

    (linked / ".git").write_text(f"gitdir: {admin}\n")

    said = git(
        linked, "rev-parse", "--path-format=absolute", "--git-common-dir", "--abbrev-ref", "HEAD"
    )
    assert said.stdout.split() == [str(other / ".git"), "elsewhere"]
    git(linked, "status")
    assert (admin / "gitdir").read_text() == backlink


def test_a_pointer_aimed_at_another_repositorys_worktree_is_not_given_that_repository(
    repo: Path, tmp_path: Path
) -> None:
    """
    The hazard: a session that can write its own worktree rewrites one line, and
    every later git call for a placement runs against a repository the operator never
    pointed it at. The other repository's admin data is not the session's to write,
    and it names its own worktree, so that is what refuses. The control is the same
    worktree before the rewrite.
    """
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(other, "tracked.txt")
    theirs = tmp_path / "theirs"
    git(other, "worktree", "add", "-q", "-b", "elsewhere", str(theirs), "main")
    assert detect(str(linked)) == LinkedWorktree(root=str(linked), main_root=str(repo))

    (linked / ".git").write_text(f"gitdir: {other / '.git' / 'worktrees' / 'theirs'}\n")
    got = detect(str(linked))

    assert isinstance(got, Unclassified), got
    assert str(other) in got.reason and str(theirs / ".git") in got.reason
    # The worktree that repository does name is still read as its own.
    assert detect(str(theirs)) == LinkedWorktree(root=str(theirs), main_root=str(other))


def test_a_pointer_that_is_a_symlink_to_another_worktrees_pointer_is_not_named_back(
    repo: Path, tmp_path: Path
) -> None:
    """
    The backlink names ``<theirs>/.git``. A ``.git`` that is a symlink to that file
    resolves to it, so the comparison must not resolve the last component or the
    rewrite above passes by being spelled as a link.
    """
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(other, "tracked.txt")
    theirs = tmp_path / "theirs"
    git(other, "worktree", "add", "-q", "-b", "elsewhere", str(theirs), "main")

    (linked / ".git").unlink()
    (linked / ".git").symlink_to(theirs / ".git")
    assert git(linked, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "elsewhere"

    assert isinstance(detect(str(linked)), Unclassified)


@pytest.mark.parametrize("backlink", ["missing", "a-directory", "a-fifo", "a-fed-fifo", "relative"])
def test_a_backlink_that_cannot_be_read_as_this_worktree_refuses_and_never_waits(
    repo: Path, backlink: str
) -> None:
    """
    The admin directory may be one a session wrote, so its ``gitdir`` is read as an
    untrusted file: absent, not a file, or a pipe -- fed or not -- are all "does not
    name this worktree", and none of them may hold the call. A relative one that does
    name it is git's own spelling under ``worktree.useRelativePaths`` and is accepted.
    """
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    admin = repo / ".git" / "worktrees" / "linked"
    (admin / "gitdir").unlink()
    if backlink == "a-directory":
        (admin / "gitdir").mkdir()
    elif backlink in ("a-fifo", "a-fed-fifo"):
        os.mkfifo(admin / "gitdir")
    elif backlink == "relative":
        (admin / "gitdir").write_text(f"{os.path.relpath(linked / '.git', admin)}\n")
    assert git(linked, "rev-parse", "--show-toplevel").stdout.strip() == str(linked)

    fed = -1
    if backlink == "a-fed-fifo":
        # A pipe with the right answer already in it is still not a file.
        fed = os.open(admin / "gitdir", os.O_RDWR | os.O_NONBLOCK)
        os.write(fed, os.fsencode(f"{linked / '.git'}\n"))
    try:
        got = detect(str(linked), timeout=5.0)
    finally:
        if fed != -1:
            os.close(fed)

    if backlink == "relative":
        assert got == LinkedWorktree(root=str(linked), main_root=str(repo))
    else:
        assert isinstance(got, Unclassified), got
        assert str(admin / "gitdir") in got.reason


def test_a_main_checkout_inside_its_own_worktree_has_no_sibling_to_place_beside(
    repo: Path,
) -> None:
    """
    A decoy: a repository built inside the worktree, the pointer aimed at it, and a
    backlink that names the worktree back. git accepts all of it. What
    refuses is where it is: a worktree placed beside that main checkout would land
    inside the worktree the request came from. Without the refusal ``create`` runs
    the decoy's hook as the operator, which the last lines establish by doing it with
    a hand-built placement.
    """
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    decoy = _decoy(linked, "decoy", linked)
    (linked / ".git").write_text(f"gitdir: {decoy / '.git' / 'worktrees' / 'x'}\n")
    assert git(
        linked, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ).stdout.strip() == str(decoy / ".git")

    got = detect(str(linked))

    assert isinstance(got, Unclassified), got
    assert str(decoy) in got.reason and "inside" in got.reason
    assert not (linked / "decoy-ran").exists()

    forced = place(LinkedWorktree(root=str(linked), main_root=str(decoy)), "probe")
    assert isinstance(forced, Placement)
    assert forced.path == str(linked / "decoy-worktrees" / "probe")
    assert isinstance(create(forced, "HEAD"), Created)
    assert "committed" in (linked / "decoy-ran").read_text()


def test_a_decoy_outside_the_worktree_that_names_it_back_is_still_reported_as_main_root(
    repo: Path, tmp_path: Path
) -> None:
    """
    What ``detect`` does NOT establish, pinned so nobody reads it as a defence. A
    decoy in any other directory its author can write names the worktree back as well
    as the real repository does, and nothing in the worktree says which is real. This
    is the answer today; a caller must hold ``main_root`` from somewhere a session
    cannot write before it runs git on the strength of one.
    """
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    elsewhere = tmp_path / "writable-elsewhere"
    decoy = _decoy(elsewhere, "decoy", linked)
    (linked / ".git").write_text(f"gitdir: {decoy / '.git' / 'worktrees' / 'x'}\n")

    assert detect(str(linked)) == LinkedWorktree(root=str(linked), main_root=str(decoy))
    # The repository it was made from still lists it, and was never consulted.
    assert (repo / ".git" / "worktrees" / "linked" / "gitdir").read_text() == f"{linked / '.git'}\n"


def test_detect_separates_no_repository_from_the_other_exit_128s(
    repo: Path, tmp_path: Path
) -> None:
    """
    All four exit 128. Only the first has no repository.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    bare = tmp_path / "bare.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(bare))

    assert detect(str(plain)) == NotARepository()
    assert isinstance(detect(str(repo / ".git")), Unclassified)
    assert isinstance(detect(str(bare)), Unclassified)
    missing = detect(str(tmp_path / "nowhere"))
    assert isinstance(missing, Unclassified) and "nowhere" in missing.reason


def test_detect_does_not_call_a_broken_pointer_no_repository(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text("gitdir: /nonexistent/x\n")
    got = detect(str(broken))
    assert isinstance(got, Unclassified) and "/nonexistent/x" in got.reason


@pytest.mark.parametrize("damage", ["head", "emptied"])
def test_a_damaged_git_directory_is_not_reported_as_no_repository(repo: Path, damage: str) -> None:
    """
    git says the same sentence for both as for a plain directory, so the control arm
    is that sentence and the claim is that ``detect`` does not pass it on.
    """
    (repo / "deep").mkdir()
    if damage == "head":
        (repo / ".git" / "HEAD").write_text("garbage\n")
    else:
        os.rename(repo / ".git", repo / "was-git")
        (repo / ".git").mkdir()
    said = git(repo, "rev-parse", "--git-dir", check=False)
    assert said.returncode == 128 and worktree._NO_REPOSITORY in said.stderr

    for directory in (repo, repo / "deep"):
        got = detect(str(directory))
        assert isinstance(got, Unclassified), got
        assert str(repo / ".git") in got.reason and "no repository" in got.reason


def test_a_damaged_repository_inside_a_checkout_is_not_read_as_that_checkout(repo: Path) -> None:
    """
    git passes over the damaged ``.git`` and answers for the checkout around it, which
    would place the inner directory's worktree beside somebody else's repository.
    """
    inner = repo / "inner"
    inner.mkdir()
    git(inner, "init", "-q", "-b", "main")
    assert detect(str(inner)) == MainCheckout(str(inner))
    (inner / ".git" / "HEAD").write_text("garbage\n")
    assert git(inner, "rev-parse", "--show-toplevel").stdout.strip() == str(repo)

    got = detect(str(inner))

    assert isinstance(got, Unclassified), got
    assert str(inner / ".git") in got.reason and str(repo) in got.reason
    # The checkout around it is still what it was, from anywhere outside the damage.
    (repo / "beside").mkdir()
    assert detect(str(repo / "beside")) == MainCheckout(str(repo))


def test_a_git_entry_git_was_fenced_off_from_is_not_one_it_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``GIT_CEILING_DIRECTORIES`` reaches the child, so the look at the disk stops where
    git stops. The first assertion is the same directory without the fence.
    """
    (tmp_path / ".git").mkdir()
    plain = tmp_path / "fence" / "plain"
    plain.mkdir(parents=True)
    assert isinstance(detect(str(plain)), Unclassified)

    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path / "fence"))
    assert detect(str(plain)) == NotARepository()
    # A fence does not hide the directory asked about, to git or to this.
    (plain / ".git").mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(plain))
    assert isinstance(detect(str(plain)), Unclassified)


def test_a_checkout_git_stopped_below_at_a_mount_point_is_not_called_damaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A healthy checkout spanning a mount point, asked about from below the mount: git
    stops at the boundary unless ``GIT_DISCOVERY_ACROSS_FILESYSTEM`` is set, and the
    ``.git`` above is sound. **git is a stand-in here**, because nothing in a test can
    mount a filesystem: the sentence is the one this git's binary holds for the case
    (``strings`` of 2.39.5), and what is pinned is only that the reason given for it
    offers the mount point and not damage alone.
    """
    (tmp_path / ".git").mkdir()
    below = tmp_path / "mnt" / "deep"
    below.mkdir(parents=True)
    stopped = (
        f"fatal: not a git repository (or any parent up to mount point {tmp_path / 'mnt'})\n"
        "Stopping at filesystem boundary (GIT_DISCOVERY_ACROSS_FILESYSTEM not set).\n"
    )
    monkeypatch.setattr(
        worktree, "_git", lambda directory, *args, timeout=10.0: worktree._Ran(128, "", stopped)
    )

    got = detect(str(below))

    assert isinstance(got, Unclassified), got
    assert str(tmp_path / ".git") in got.reason
    assert "mount point" in got.reason and "GIT_DISCOVERY_ACROSS_FILESYSTEM" in got.reason


def test_detect_does_not_give_a_bare_repositorys_worktree_a_main_root(
    repo: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "bare.git"
    git(tmp_path, "clone", "-q", "--bare", str(repo), str(bare))
    tree = tmp_path / "bw"
    git(bare, "worktree", "add", "-q", "-b", "bw", str(tree), "main")
    assert isinstance(detect(str(tree)), Unclassified)


def test_detect_does_not_read_a_submodule_as_a_main_checkout(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(other, "o.txt")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(other), "sub")
    got = detect(str(repo / "sub"))
    assert isinstance(got, Unclassified) and "submodule" in got.reason
    # The superproject around it is still what it was.
    assert detect(str(repo)) == MainCheckout(str(repo))


def test_detect_does_not_read_a_separated_git_directory_as_a_main_checkout(
    tmp_path: Path,
) -> None:
    git(tmp_path, "init", "-q", "--separate-git-dir", str(tmp_path / "elsewhere"), "sep")
    assert isinstance(detect(str(tmp_path / "sep")), Unclassified)


def test_an_inherited_git_dir_does_not_make_a_plain_directory_a_repository(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    With ``GIT_DIR`` exported, git answers for that repository from anywhere. The
    control arm shows the variable really does that here, so the assertion after it is
    about the module's child environment and not about this git ignoring the variable.
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_DIR", str(repo / ".git"))
    control = subprocess.run(
        ["git", "-C", str(plain), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    assert control.stdout.strip() == "true"

    assert detect(str(plain)) == NotARepository()


def test_no_repository_variable_reaches_a_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Every variable this git says redirects a repository, one it does not list
    (``GIT_NAMESPACE``), and one that does not exist yet: all dropped, because the rule
    is an allowlist. What is kept is kept.
    """
    named = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert "GIT_INDEX_FILE" in named and "GIT_DIR" in named
    assert not set(named) & worktree._KEPT_GIT_ENV
    for name in [*named, "GIT_NAMESPACE", "GIT_NOT_INVENTED_YET", "LANGUAGE"]:
        monkeypatch.setenv(name, "/somewhere/else")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", "/fence")
    monkeypatch.setenv("GIT_DISCOVERY_ACROSS_FILESYSTEM", "1")

    env = worktree._child_env()

    assert {k: v for k, v in env.items() if k.startswith("GIT_")} == {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CEILING_DIRECTORIES": "/fence",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
        "GIT_ALLOW_PROTOCOL": "",
        "GIT_TERMINAL_PROMPT": "0",
    }
    assert env["LC_ALL"] == "C" and "LANGUAGE" not in env


def test_a_git_child_is_given_the_environment_as_it_stands_when_it_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The entry point takes the library's variables back out of ``os.environ``
    (``pairing.release``) after this module is imported, so what a git child is
    given is read at each start.
    """
    monkeypatch.setenv("IMTYPESET_PROBE", "set")
    assert worktree._child_env()["IMTYPESET_PROBE"] == "set"
    monkeypatch.delenv("IMTYPESET_PROBE")
    assert "IMTYPESET_PROBE" not in worktree._child_env()


def test_an_inherited_index_file_does_not_become_the_new_worktrees_index(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    What a process started from a hook or ``rebase -x`` carries. The control arm is
    git's own add under the same variable: it writes the checkout's index to the
    foreign file and the new tree then reports every file as deleted.
    """
    foreign = tmp_path / "foreign-index"
    monkeypatch.setenv("GIT_INDEX_FILE", str(foreign))
    control = tmp_path / "control"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "control", str(control), "main"],
        check=True,
        capture_output=True,
    )
    assert foreign.exists()
    foreign.unlink()

    got = create(placed(repo, "feat"), "main")

    assert isinstance(got, Created), got
    assert not foreign.exists()
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert git(Path(got.path), "status", "--porcelain").stdout == ""
    assert git(control, "status", "--porcelain").stdout != ""


def test_detect_does_not_misread_a_path_with_a_newline(tmp_path: Path) -> None:
    odd = tmp_path / "nl\ndir"
    odd.mkdir()
    git(odd, "init", "-q", "-b", "main")
    assert isinstance(detect(str(odd)), Unclassified)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

LISTING = (
    "worktree /src/repo\0HEAD 5e657152e63d0c1cc68033c1cd8de6c6c30dc72e\0"
    "branch refs/heads/main\0\0"
    "worktree /src/w\0HEAD 24bdc21cf6c65d584e6b4125008b8b900b05bbcf\0"
    "branch refs/heads/feat\0locked why\ntwo\0\0"
    "worktree /src/gone\0HEAD 5e657152e63d0c1cc68033c1cd8de6c6c30dc72e\0"
    "branch refs/heads/gone\0prunable gitdir file points to non-existent location\0\0"
    "worktree /src/odd\nname\0HEAD 5e657152e63d0c1cc68033c1cd8de6c6c30dc72e\0detached\0\0"
)


def test_the_listing_parses_into_one_record_per_worktree() -> None:
    main, locked, gone, odd = parse_worktree_list(LISTING)
    assert main == WorktreeEntry(
        path="/src/repo",
        head="5e657152e63d0c1cc68033c1cd8de6c6c30dc72e",
        branch="refs/heads/main",
    )
    assert locked.locked == "why\ntwo" and locked.prunable is None
    assert gone.prunable == "gitdir file points to non-existent location"
    assert gone.locked is None
    assert odd.path == "/src/odd\nname" and odd.detached and odd.branch is None


def test_a_lock_without_a_reason_is_still_a_lock() -> None:
    (entry,) = parse_worktree_list("worktree /w\0HEAD abc\0detached\0locked\0\0")
    assert entry.locked == ""


def test_a_bare_main_entry_and_an_unknown_label_parse() -> None:
    bare, tree = parse_worktree_list(
        "worktree /src/bare.git\0bare\0\0"
        "worktree /src/bw\0HEAD abc\0branch refs/heads/bw\0future thing\0\0"
    )
    assert bare == WorktreeEntry(path="/src/bare.git", bare=True)
    assert tree == WorktreeEntry(path="/src/bw", head="abc", branch="refs/heads/bw")


def test_text_with_no_records_is_no_worktrees() -> None:
    assert parse_worktree_list("") == ()
    assert parse_worktree_list("HEAD abc\0\0") == ()
    assert parse_worktree_list("\0\0\0") == ()


def test_a_final_record_git_did_not_terminate_is_still_a_record() -> None:
    assert parse_worktree_list("worktree /w\0HEAD abc") == (WorktreeEntry("/w", head="abc"),)


def test_list_worktrees_reads_real_git_including_a_newline_and_a_missing_tree(
    repo: Path,
) -> None:
    odd = repo.parent / "odd\nname"
    gone = repo.parent / "gone"
    git(repo, "worktree", "add", "-q", "-b", "a", str(odd), "main")
    git(repo, "worktree", "add", "-q", "-b", "b", str(gone), "main")
    git(repo, "worktree", "lock", "--reason", "held", str(odd))
    os.rename(gone, repo.parent / "moved-away")

    listed = list_worktrees(str(repo))
    assert not isinstance(listed, GitFailed)
    by_path = {entry.path: entry for entry in listed}
    assert listed[0].path == str(repo) and listed[0].branch == "refs/heads/main"
    assert by_path[str(odd)].locked == "held"
    assert by_path[str(odd)].branch == "refs/heads/a"
    assert by_path[str(gone)].prunable is not None
    # The same list from inside a linked worktree.
    assert list_worktrees(str(odd)) == listed


def test_git_lists_whatever_path_a_worktrees_gitdir_file_names(repo: Path, tmp_path: Path) -> None:
    """
    git's behaviour, and why the module docstring calls a listed path a claim: the
    path of a linked worktree is read from ``worktrees/<id>/gitdir``, a file in a
    directory the record's D2 measured a contained session writing into. Rewritten to
    name a directory git never made a worktree at, that directory is what is listed --
    as prunable while nothing is at the named ``.git``, and as an ordinary worktree
    once an empty file is.
    """
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(linked), "main")
    head = made_at(repo)
    main = WorktreeEntry(str(repo), head=head, branch="refs/heads/main")
    assert list_worktrees(str(repo)) == (
        main,
        WorktreeEntry(str(linked), head=head, branch="refs/heads/feat"),
    )
    elsewhere = tmp_path / "never" / "a-worktree"
    elsewhere.mkdir(parents=True)

    (repo / ".git" / "worktrees" / "linked" / "gitdir").write_text(f"{elsewhere / '.git'}\n")

    listed = list_worktrees(str(repo))
    assert not isinstance(listed, GitFailed)
    assert listed[0] == main
    assert (listed[1].path, listed[1].branch) == (str(elsewhere), "refs/heads/feat")
    assert listed[1].prunable is not None
    (elsewhere / ".git").write_text("")
    assert list_worktrees(str(repo)) == (
        main,
        WorktreeEntry(str(elsewhere), head=head, branch="refs/heads/feat"),
    )


def test_listing_outside_a_repository_is_gits_failure_and_not_an_empty_list(
    tmp_path: Path,
) -> None:
    got = list_worktrees(str(tmp_path))
    assert isinstance(got, GitFailed)
    assert got.step == "worktree list" and got.returncode == 128
    assert "not a git repository" in got.detail


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_a_slug_holds_only_letters_digits_and_dashes() -> None:
    assert slug_for("enhanced-sessions") == "enhanced-sessions"
    assert slug_for("feat/x") == slug_for("feat.x") == slug_for("feat_x") == "feat-x"
    assert slug_for("/.hidden/") == "hidden"
    assert slug_for("naïve") == "na-ve"


def test_sanitised_is_the_sdks_own_transcript_slug_up_to_the_limit() -> None:
    """
    The copy against the original, which is imported at the top of this file so that
    a moved private module fails the run. At the limit the two agree; one character
    past it the SDK's is the copy's first ``_SLUG_LIMIT`` characters and a hash.
    """
    assert worktree._SLUG_LIMIT == sessions.MAX_SANITIZED_LENGTH
    at_limit = "/" + "p" * (worktree._SLUG_LIMIT - 8) + "/feat_x"
    for path in (
        "/home/w/Source/pptmstr-worktrees/feat-a",
        "/home/w.r/.pptmstr/my_tree",
        "/src/naïve dir/x",
        at_limit,
    ):
        assert sanitised(path) == sessions._sanitize_path(path)

    past = at_limit + "y"
    theirs = sessions._sanitize_path(past)
    assert theirs != sanitised(past)
    assert theirs.startswith(sanitised(past)[: worktree._SLUG_LIMIT] + "-")


def test_a_worktree_goes_beside_the_main_checkout_from_either_kind_of_checkout() -> None:
    from_main = place(MainCheckout("/src/pptmstr"), "feat/x")
    assert from_main == Placement(
        run_in="/src/pptmstr",
        main_root="/src/pptmstr",
        branch="feat/x",
        path="/src/pptmstr-worktrees/feat-x",
    )
    from_linked = place(
        LinkedWorktree(root="/src/pptmstr-worktrees/a", main_root="/src/pptmstr"), "feat/x"
    )
    assert isinstance(from_linked, Placement)
    assert from_linked.path == "/src/pptmstr-worktrees/feat-x"
    # git runs where the request came from: HEAD is per worktree.
    assert from_linked.run_in == "/src/pptmstr-worktrees/a"


def test_a_branch_with_nothing_nameable_is_its_own_answer() -> None:
    assert place(MainCheckout("/src/pptmstr"), "///") == NothingToName("///")


def test_a_dotted_or_underscored_path_is_placed_and_names_its_odd_components() -> None:
    """
    Not refused: a repository under such a path could otherwise have no worktree. The
    parts are the warning.
    """
    got = place(MainCheckout("/home/w.r/my_src/pptmstr"), "feat")
    assert isinstance(got, Placement)
    assert got.path == "/home/w.r/my_src/pptmstr-worktrees/feat"
    assert got.unsanitary == ("w.r", "my_src")
    dotted_repo = place(MainCheckout("/src/pptmstr.git"), "feat")
    assert isinstance(dotted_repo, Placement)
    assert dotted_repo.unsanitary == ("pptmstr.git-worktrees",)
    clean = place(MainCheckout("/src/pptmstr"), "feat.x")
    assert isinstance(clean, Placement) and clean.unsanitary == ()


def _placement(path: str) -> Placement:
    return Placement(run_in="/src/repo", main_root="/src/repo", branch="b", path=path)


def test_collision_is_judged_on_the_sanitised_path() -> None:
    entries = (
        WorktreeEntry("/src/repo", head="a", branch="refs/heads/main"),
        WorktreeEntry("/src/repo-worktrees/feat_x", head="a", branch="refs/heads/feat_x"),
    )
    assert collision(_placement("/src/repo-worktrees/feat-x"), entries) == SlugTaken(
        "/src/repo-worktrees/feat-x", "/src/repo-worktrees/feat_x"
    )
    assert collision(_placement("/src/repo-worktrees/other"), entries) is None


def test_past_the_slug_limit_a_shared_prefix_is_unjudged_and_never_taken() -> None:
    """
    The hazard both ways: two different trees under a long parent that the copy calls
    distinct while the SDK's prefix lookup cannot tell them apart, and ``feat_x``
    beside ``feat-x`` called one directory when the SDK hashes them apart. The SDK's
    own names are the control for each.
    """
    parent = "/" + "q" * 220 + "/repo-worktrees/"
    one, two = parent + "one", parent + "two"
    limit = worktree._SLUG_LIMIT
    assert sessions._sanitize_path(one) != sessions._sanitize_path(two)
    assert sessions._sanitize_path(one)[:limit] == sessions._sanitize_path(two)[:limit]
    assert collision(_placement(two), (WorktreeEntry(one),)) == SlugUnjudged(two, one)

    under, dash = parent + "feat_x", parent + "feat-x"
    assert sanitised(under) == sanitised(dash)
    assert sessions._sanitize_path(under) != sessions._sanitize_path(dash)
    assert collision(_placement(dash), (WorktreeEntry(under),)) == SlugUnjudged(dash, under)

    # Past the limit with different prefixes, and one side within it: both judged.
    elsewhere = "/" + "z" * 220 + "/repo-worktrees/two"
    assert collision(_placement(two), (WorktreeEntry(elsewhere),)) is None
    short = two[: limit - 5]
    assert len(sanitised(short)) <= limit < len(sanitised(two))
    assert collision(_placement(two), (WorktreeEntry(short),)) is None
    # At the limit exactly, the sanitised form is still the whole name.
    edge_a = "/" + "p" * (limit - 8) + "/feat_x"
    edge_b = "/" + "p" * (limit - 8) + "/feat-x"
    assert collision(_placement(edge_b), (WorktreeEntry(edge_a),)) == SlugTaken(edge_b, edge_a)


def test_an_exact_path_is_registered_not_merely_similar() -> None:
    """
    Both mistakes are possible for one path; the exact one is the one reported, and it
    says whether the directory is still there.
    """
    there = (WorktreeEntry("/src/repo-worktrees/feat", head="a"),)
    gone = (WorktreeEntry("/src/repo-worktrees/feat", head="a", prunable="gitdir file ..."),)
    assert collision(_placement("/src/repo-worktrees/feat"), there) == PathRegistered(
        "/src/repo-worktrees/feat", missing=False
    )
    assert collision(_placement("/src/repo-worktrees/feat"), gone) == PathRegistered(
        "/src/repo-worktrees/feat", missing=True
    )


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def test_preflight_clears_a_new_branch_and_resolves_the_base(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert preflight(placed(repo, "feat/x"), "main") == Cleared(head)


@pytest.mark.parametrize("name", ["a..b", "-x", "x.lock", "HEAD", "a b"])
def test_a_name_git_rejects_is_an_invalid_branch(repo: Path, name: str) -> None:
    where = Placement(str(repo), str(repo), name, str(repo.parent / "repo-worktrees" / "x"))
    assert preflight(where, "main") == InvalidBranch(name, would_be=None)


def test_a_name_git_reads_as_another_branch_is_invalid_and_says_which(repo: Path) -> None:
    """
    ``check-ref-format --branch '@{-1}'`` exits zero and prints the previous branch.
    Taken at its exit status, the worktree would be made on a name nobody typed.
    """
    git(repo, "checkout", "-q", "-b", "previous")
    git(repo, "checkout", "-q", "main")
    where = Placement(str(repo), str(repo), "@{-1}", str(repo.parent / "repo-worktrees" / "x"))
    assert preflight(where, "main") == InvalidBranch("@{-1}", would_be="previous")


def test_an_existing_branch_is_reported_as_existing(repo: Path) -> None:
    git(repo, "branch", "taken")
    assert preflight(placed(repo, "taken"), "main") == BranchExists("taken")


@pytest.mark.parametrize(
    "base",
    [
        "nosuchref",
        "--all",
        "--path-format=x",
        "--abbrev-ref=x",
        "--end-of-options",
        "main^{tree}",
        "",
        "^main",
        "main..main",
    ],
)
def test_a_base_that_is_not_a_commit_is_reported_as_the_base(repo: Path, base: str) -> None:
    """
    ``--path-format=x`` and ``--abbrev-ref=x`` are options ``rev-parse`` dies on with
    exit 128 when it reads them as options, which is what a mistyped base must not be.
    """
    assert preflight(placed(repo, "feat"), base) == BaseNotACommit(base)


@pytest.mark.parametrize("kind", ["empty-dir", "file", "dangling-link"])
def test_anything_at_the_path_is_path_exists(repo: Path, kind: str) -> None:
    where = placed(repo, "feat")
    target = Path(where.path)
    target.parent.mkdir()
    if kind == "empty-dir":
        target.mkdir()
    elif kind == "file":
        target.write_text("x")
    else:
        target.symlink_to(target.parent / "nothing-here")
    assert preflight(where, "main") == PathExists(where.path)


def test_a_deleted_but_registered_worktree_is_reported_as_registered(repo: Path) -> None:
    """
    The disk says the path is free and git refuses it.
    """
    where = placed(repo, "feat")
    git(repo, "worktree", "add", "-q", "-b", "earlier", where.path, "main")
    os.rename(where.path, repo.parent / "moved-away")
    assert preflight(where, "main") == PathRegistered(where.path, missing=True)


def test_a_registered_path_reached_through_a_symlink_is_still_registered(
    repo: Path, tmp_path: Path
) -> None:
    """
    git lists the resolved path, so the placement's own spelling matches no entry.
    The answer carries the path the caller asked about.
    """
    real_parent = tmp_path / "elsewhere"
    real_parent.mkdir()
    (repo.parent / "repo-worktrees").symlink_to(real_parent)
    where = placed(repo, "feat")
    git(repo, "worktree", "add", "-q", "-b", "earlier", where.path, "main")
    listed = list_worktrees(str(repo))
    assert not isinstance(listed, GitFailed)
    assert [entry.path for entry in listed] == [str(repo), str(real_parent / "feat")]
    assert where.path != str(real_parent / "feat")
    os.rename(real_parent / "feat", real_parent / "moved-away")

    assert preflight(where, "main") == PathRegistered(where.path, missing=True)


def test_a_slug_taken_through_a_symlink_names_the_path_that_was_asked_about(
    repo: Path, tmp_path: Path
) -> None:
    real_parent = tmp_path / "elsewhere"
    real_parent.mkdir()
    (repo.parent / "repo-worktrees").symlink_to(real_parent)
    where = placed(repo, "feat-x")
    by_hand = str(real_parent / "feat_x")
    git(repo, "worktree", "add", "-q", "-b", "feat_x", by_hand, "main")
    # The placement's own spelling sanitises to something else entirely.
    assert sanitised(where.path) != sanitised(by_hand)

    assert preflight(where, "main") == SlugTaken(where.path, by_hand)


def test_a_locked_worktree_whose_directory_is_gone_is_reported_missing(repo: Path) -> None:
    """
    git does not call a locked entry prunable, which is all the listing has to go on.
    """
    where = placed(repo, "feat")
    git(repo, "worktree", "add", "-q", "-b", "earlier", where.path, "main")
    git(repo, "worktree", "lock", where.path)
    os.rename(where.path, repo.parent / "moved-away")
    listed = list_worktrees(str(repo))
    assert not isinstance(listed, GitFailed)
    assert listed[1].locked == "" and listed[1].prunable is None
    assert collision(where, listed) == PathRegistered(where.path, missing=False)

    assert preflight(where, "main") == PathRegistered(where.path, missing=True)


def test_a_launching_directory_that_is_gone_is_not_an_invalid_branch(repo: Path) -> None:
    """
    ``check-ref-format --branch`` exits 128 for a name it refuses and for a directory
    it cannot enter. The name here is a good one.
    """
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "stack", str(linked), "main")
    where = placed(linked, "feat")
    git(repo, "worktree", "remove", str(linked))
    refused = git(repo, "check-ref-format", "--branch", "a..b", check=False)
    gone = git(linked, "check-ref-format", "--branch", "feat", check=False)
    assert refused.returncode == gone.returncode == 128

    for got in (preflight(where, "main"), create(where, "main")):
        assert isinstance(got, GitFailed), got
        assert got.step == "check-ref-format" and got.returncode == 128
        assert "cannot change to" in got.detail
    assert not has_branch(repo, "feat")


def test_a_name_check_killed_by_a_signal_is_not_an_invalid_branch(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hazard: the question about the name ends without an answer, the control
    question then passes, and a good name is reported as one git refuses. The ``git``
    on ``PATH`` here kills itself for that one question and is the real one for every
    other, so the status is a real child's.
    """
    real = shutil.which("git")
    assert real is not None
    shim = tmp_path / "bin" / "git"
    shim.parent.mkdir()
    shim.write_text(
        "#!/bin/sh\n"
        'case "$*" in *"check-ref-format --branch feat") kill -KILL $$ ;; esac\n'
        f'exec {real} "$@"\n'
    )
    shim.chmod(0o755)
    where = placed(repo, "feat")
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")
    killed = worktree._git(str(repo), "check-ref-format", "--branch", "feat")
    assert killed.returncode == -9
    assert worktree._git(str(repo), "check-ref-format", "--branch", "x").returncode == 0

    for got in (preflight(where, "main"), create(where, "main")):
        assert got == GitFailed("check-ref-format", -9, "exit -9, no output")
    assert not has_branch(repo, "feat")


def test_a_dangling_symbolic_ref_at_the_name_is_a_branch_that_exists(repo: Path) -> None:
    """
    The hazard: ``show-ref`` calls it absent, and ``git branch`` then writes through
    it and creates the branch it points at -- a branch nobody asked for, with the
    worktree checked out on it. The control arm is git doing exactly that.
    """
    git(repo, "symbolic-ref", "refs/heads/feat", "refs/heads/other")
    assert not has_branch(repo, "feat")
    where = placed(repo, "feat")

    assert preflight(where, "main") == BranchExists("feat")
    assert create(where, "main") == BranchExists("feat")
    assert not has_branch(repo, "other") and not os.path.lexists(where.path)

    git(repo, "branch", "--no-track", "feat", "main")
    assert has_branch(repo, "other")


def test_a_hand_made_worktree_with_the_same_transcript_slug_is_slug_taken(repo: Path) -> None:
    where = placed(repo, "feat-x")
    by_hand = str(Path(where.path).parent / "feat_x")
    git(repo, "worktree", "add", "-q", "-b", "feat_x", by_hand, "main")
    assert preflight(where, "main") == SlugTaken(where.path, by_hand)


def test_preflight_outside_a_repository_is_gits_failure(tmp_path: Path) -> None:
    """
    Not ``BranchExists`` and not "absent": ``show-ref`` exits 128 there, and only 0 and
    1 are answers.
    """
    plain = str(tmp_path)
    got = preflight(Placement(plain, plain, "feat", plain + "-worktrees/feat"), "main")
    assert isinstance(got, GitFailed) and got.step == "show-ref" and got.returncode == 128


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_create_makes_the_worktree_on_a_new_branch_at_the_base(repo: Path) -> None:
    first = git(repo, "rev-parse", "HEAD").stdout.strip()
    commit(repo, "second.txt")
    where = placed(repo, "feat/x")

    got = create(where, first)

    assert got == Created(where.path, "feat/x", first)
    assert where.path == str(repo.parent / "repo-worktrees" / "feat-x")
    assert detect(where.path) == LinkedWorktree(root=where.path, main_root=str(repo))
    assert git(Path(where.path), "rev-parse", "HEAD").stdout.strip() == first
    assert git(Path(where.path), "symbolic-ref", "HEAD").stdout.strip() == "refs/heads/feat/x"
    assert (Path(where.path) / "tracked.txt").exists()
    assert not (Path(where.path) / "second.txt").exists()


def test_a_remote_tracking_base_sets_up_no_upstream(repo: Path, tmp_path: Path) -> None:
    """
    An upstream is a write to the ``config`` every worktree shares. The control arm is
    git's own ``add -b`` from the same base, which does write one.
    """
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(repo), str(clone))
    git(clone, "worktree", "add", "-q", "-b", "control", str(tmp_path / "control"), "origin/main")
    assert git(clone, "config", "branch.control.merge", check=False).returncode == 0

    got = create(placed(clone, "feat"), "origin/main")

    assert isinstance(got, Created)
    assert git(clone, "config", "branch.feat.merge", check=False).returncode == 1


def test_head_as_a_base_is_the_launching_worktrees_head(repo: Path) -> None:
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "stack", str(linked), "main")
    ahead = commit(linked, "stacked.txt")
    assert ahead != git(repo, "rev-parse", "HEAD").stdout.strip()

    got = create(placed(linked, "on-top"), "HEAD")

    assert isinstance(got, Created) and got.commit == ahead
    # Beside the main checkout, not beside the worktree it was asked from.
    assert got.path == str(repo.parent / "repo-worktrees" / "on-top")


def test_a_refused_create_makes_nothing(repo: Path) -> None:
    where = placed(repo, "feat")
    assert create(where, "nosuchref") == BaseNotACommit("nosuchref")
    assert not has_branch(repo, "feat")
    assert not os.path.lexists(where.path)


def test_a_branch_that_was_already_there_survives_a_refused_create(repo: Path) -> None:
    earlier = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "branch", "mine")
    commit(repo, "later.txt")

    assert create(placed(repo, "mine"), "main") == BranchExists("mine")
    assert git(repo, "rev-parse", "refs/heads/mine").stdout.strip() == earlier


def test_a_branch_that_appears_after_the_preflight_is_never_deleted(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hazard: the pre-flight saw no branch, somebody then made one, and the cleanup
    after a failure takes "absent before, present now" as proof of ownership.

    The pre-flight is replaced by one that clears unconditionally, which is the race
    with its window held open. The other branch sits at the very commit this call
    would have used, so the compare-and-delete would take it, and the path is
    occupied, so an add would fail and the cleanup would run: the one thing between
    that branch and deletion is that ``create`` stops when ``git branch`` fails.
    """
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "branch", "raced")
    where = placed(repo, "raced")
    Path(where.path).mkdir(parents=True)
    (Path(where.path) / "occupied").write_text("x")
    monkeypatch.setattr(worktree, "preflight", lambda placement, base_: Cleared(base))
    ran: list[str] = []
    real = worktree._git

    def recording(directory: str, *args: str, timeout: float = 10.0) -> worktree._Ran:
        ran.append(args[0])
        return real(directory, *args, timeout=timeout)

    monkeypatch.setattr(worktree, "_git", recording)

    got = create(where, "main")

    assert isinstance(got, GitFailed) and got.step == "branch"
    assert "already exists" in got.detail
    assert ran == ["branch"]
    assert git(repo, "rev-parse", "refs/heads/raced").stdout.strip() == base


@pytest.fixture
def stuck(repo: Path) -> Iterator[Placement]:
    """
    A placement that passes every pre-flight and whose ``worktree add`` then fails:
    nothing is at the path, and its parent cannot be written. One of the record's four
    after-the-branch failures, and the one a pre-flight does not see.
    """
    if os.geteuid() == 0:
        pytest.skip("root writes through directory permissions")
    where = placed(repo, "feat")
    parent = Path(where.path).parent
    parent.mkdir()
    parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        yield where
    finally:
        parent.chmod(stat.S_IRWXU)


def test_git_leaves_the_branch_when_add_b_fails_after_creating_it(
    repo: Path, stuck: Placement
) -> None:
    """
    git's behaviour, not this module's: the trap ``create`` is shaped around. The
    retry then reports the leftover and not the cause.
    """
    first = git(repo, "worktree", "add", "-b", "trap", stuck.path, "main", check=False)
    assert first.returncode != 0 and "Permission denied" in first.stderr
    assert has_branch(repo, "trap")

    retry = git(repo, "worktree", "add", "-b", "trap", stuck.path, "main", check=False)
    assert "a branch named 'trap' already exists" in retry.stderr


def test_a_failed_add_takes_its_own_branch_with_it(repo: Path, stuck: Placement) -> None:
    assert preflight(stuck, "main") == Cleared(git(repo, "rev-parse", "HEAD").stdout.strip())

    got = create(stuck, "main")

    assert isinstance(got, AddFailed), got
    assert got.returncode == 128 and "Permission denied" in got.detail
    assert not has_branch(repo, "feat")
    # So the retry reports the cause again, not a leftover.
    again = create(stuck, "main")
    assert isinstance(again, AddFailed) and "Permission denied" in again.detail


def test_git_accepts_an_empty_directory_as_a_worktree_path(repo: Path) -> None:
    """
    git's behaviour: why ``PathExists`` is asked of the disk and not left to the add.
    """
    target = repo.parent / "already-here"
    target.mkdir()
    done = git(repo, "worktree", "add", "-q", "-b", "into-empty", str(target), "main", check=False)
    assert done.returncode == 0


def _hook(repo: Path, body: str) -> None:
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\n{body}\n")
    hook.chmod(0o755)


def test_git_reports_a_hook_failure_for_a_worktree_it_finished(repo: Path) -> None:
    """
    git's behaviour: a non-zero exit from ``worktree add`` does not mean no worktree.
    """
    _hook(repo, "exit 3")
    target = repo.parent / "hooked"
    done = git(repo, "worktree", "add", "-q", "-b", "hooked", str(target), "main", check=False)
    assert done.returncode == 3
    assert (target / "tracked.txt").exists()


def test_a_failure_that_left_a_worktree_keeps_its_branch(repo: Path) -> None:
    """
    The hazard: "the add failed, so remove the branch" applied to a worktree that is
    complete and has that branch checked out.
    """
    _hook(repo, "exit 3")
    where = placed(repo, "feat")

    got = create(where, "main")

    assert isinstance(got, AddFailedWorktreeLeft), got
    assert got.returncode == 3 and got.path == where.path
    assert has_branch(repo, "feat")
    assert git(Path(where.path), "symbolic-ref", "HEAD").stdout.strip() == "refs/heads/feat"
    assert (Path(where.path) / "tracked.txt").exists()


def test_an_unreadable_registration_keeps_the_branch_and_says_so(
    repo: Path, stuck: Placement, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Not knowing whether a worktree is there is not knowing that the branch is unused.
    The listing fails on its second call: the pre-flight's succeeds, the one after the
    failed add does not.
    """
    real = worktree.list_worktrees
    calls: list[str] = []

    def flaky(directory: str) -> tuple[WorktreeEntry, ...] | GitFailed:
        calls.append(directory)
        if len(calls) == 1:
            return real(directory)
        return GitFailed("worktree list", 1, "index.lock exists")

    monkeypatch.setattr(worktree, "list_worktrees", flaky)

    got = create(stuck, "main")

    assert isinstance(got, AddFailedBranchLeft), got
    assert "Permission denied" in got.detail and "index.lock exists" in got.kept
    assert len(calls) == 2
    assert has_branch(repo, "feat")


def _after_the_failed_add(monkeypatch: pytest.MonkeyPatch, meanwhile: Callable[[], object]) -> None:
    """
    Run ``meanwhile`` in the window between a failed add and the cleanup's look at the
    registration: the listing's second call, the first being the pre-flight's.
    """
    real = worktree.list_worktrees
    calls: list[str] = []

    def listing(directory: str) -> tuple[WorktreeEntry, ...] | GitFailed:
        calls.append(directory)
        if len(calls) == 2:
            meanwhile()
        return real(directory)

    monkeypatch.setattr(worktree, "list_worktrees", listing)


def test_a_branch_that_moved_since_it_was_made_is_not_deleted(
    repo: Path, stuck: Placement, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hazard: this call made the branch, and by the time the add has failed somebody
    has pointed it at work of their own.
    """
    made_at = git(repo, "rev-parse", "HEAD").stdout.strip()
    moved_to = commit(repo, "later.txt")
    assert moved_to != made_at
    assert preflight(stuck, "HEAD~1") == Cleared(made_at)
    _after_the_failed_add(monkeypatch, lambda: git(repo, "update-ref", "refs/heads/feat", moved_to))

    got = create(stuck, "HEAD~1")

    assert isinstance(got, AddFailedBranchLeft), got
    assert "Permission denied" in got.detail and "would not delete" in got.kept
    assert git(repo, "rev-parse", "refs/heads/feat").stdout.strip() == moved_to


def test_a_branch_another_worktree_has_checked_out_is_not_deleted(
    repo: Path, stuck: Placement, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``update-ref -d`` does not look at checkouts, so nothing in git would stop this
    one: the ref has not moved and a tree is sitting on it.
    """
    elsewhere = repo.parent / "elsewhere"
    _after_the_failed_add(
        monkeypatch, lambda: git(repo, "worktree", "add", "-q", str(elsewhere), "feat")
    )

    got = create(stuck, "main")

    assert isinstance(got, AddFailedBranchLeft), got
    assert str(elsewhere) in got.kept
    assert has_branch(repo, "feat")
    assert git(elsewhere, "symbolic-ref", "HEAD").stdout.strip() == "refs/heads/feat"


def test_a_branch_made_symbolic_since_it_was_made_is_kept_with_its_target(
    repo: Path, stuck: Placement, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hazard: the name this call created now points at another branch, at the same
    commit, so the compare-and-delete's comparison passes.
    """
    git(repo, "branch", "other")
    _after_the_failed_add(
        monkeypatch, lambda: git(repo, "symbolic-ref", "refs/heads/feat", "refs/heads/other")
    )

    got = create(stuck, "main")

    assert isinstance(got, AddFailedBranchLeft), got
    assert "Permission denied" in got.detail and "refs/heads/other" in got.kept
    assert has_branch(repo, "other")
    assert git(repo, "symbolic-ref", "refs/heads/feat").stdout.strip() == "refs/heads/other"


def test_the_deletion_never_follows_a_symbolic_ref_to_another_branch(
    repo: Path, stuck: Placement, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The same swap, made in the window nothing can close: after ``create`` has asked
    whether the name is symbolic and before the deletion. What is lost there is the
    symbolic ref and not the branch behind it.
    """
    git(repo, "branch", "other")
    real = worktree._git

    def swapping(directory: str, *args: str, timeout: float = 10.0) -> worktree._Ran:
        if args[0] == "update-ref":
            git(repo, "symbolic-ref", "refs/heads/feat", "refs/heads/other")
        return real(directory, *args, timeout=timeout)

    monkeypatch.setattr(worktree, "_git", swapping)

    got = create(stuck, "main")

    assert isinstance(got, AddFailed), got
    assert has_branch(repo, "other")
    assert git(repo, "symbolic-ref", "-q", "refs/heads/feat", check=False).returncode != 0


def test_a_path_another_branchs_worktree_holds_is_not_reported_as_this_calls(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``feat/x`` and ``feat.x`` have one slug. With the pre-flight forced clear -- the
    two arriving together -- the second add finds the first one's tree at its path.
    That tree does not hold this call's branch, and this call's branch is not left
    behind unmentioned.
    """
    first = create(placed(repo, "feat/x"), "main")
    assert isinstance(first, Created)
    where = placed(repo, "feat.x")
    assert where.path == first.path
    monkeypatch.setattr(worktree, "preflight", lambda placement, base_: Cleared(first.commit))

    got = create(where, "main")

    assert got == AddFailedPathHeld(
        where.path,
        "feat.x",
        held_by="refs/heads/feat/x",
        returncode=128,
        detail=f"fatal: '{where.path}' already exists",
        kept=None,
    )
    assert not has_branch(repo, "feat.x")
    assert git(Path(first.path), "symbolic-ref", "HEAD").stdout.strip() == "refs/heads/feat/x"


def test_a_worktree_left_behind_a_symlinked_parent_is_still_this_calls(
    repo: Path, tmp_path: Path
) -> None:
    """
    The hook-failure case of ``test_a_failure_that_left_a_worktree_keeps_its_branch``
    with the placement spelled through a symlink, which git does not list it by.
    """
    real_parent = tmp_path / "elsewhere"
    real_parent.mkdir()
    (repo.parent / "repo-worktrees").symlink_to(real_parent)
    _hook(repo, "exit 3")
    where = placed(repo, "feat")

    got = create(where, "main")

    assert got == AddFailedWorktreeLeft(
        where.path, "feat", git(repo, "rev-parse", "main").stdout.strip(), 3, "exit 3, no output"
    )
    assert has_branch(repo, "feat")


def test_a_path_that_looks_like_an_option_is_a_path(repo: Path) -> None:
    """
    A placement built by hand, since ``place`` only makes absolute paths. Without a
    separator ``worktree add --detach feat`` makes a detached tree at ``feat``.
    """
    where = Placement(str(repo), str(repo), "feat", "--detach")

    got = create(where, "main")

    assert got == Created("--detach", "feat", git(repo, "rev-parse", "main").stdout.strip())
    listed = list_worktrees(str(repo))
    assert not isinstance(listed, GitFailed)
    assert listed[1] == WorktreeEntry(
        str(repo / "--detach"), head=got.commit, branch="refs/heads/feat"
    )
    # And relative to the directory git runs in, for the disk's answer too.
    assert preflight(dataclasses.replace(where, branch="again"), "main") == PathExists("--detach")


@pytest.mark.parametrize("bad", ["a\0b", "a\ud800b"], ids=["nul", "lone-surrogate"])
def test_text_no_process_can_be_given_is_an_answer_and_not_an_exception(
    repo: Path, bad: str
) -> None:
    """
    A NUL and a lone surrogate, in each argument of each blocking function. Nothing
    is left behind by the ones that got as far as making a branch.
    """
    good = placed(repo, "feat")

    assert isinstance(detect(str(repo / bad)), Unclassified)
    listed = list_worktrees(str(repo / bad))
    assert isinstance(listed, GitFailed) and listed.returncode is None
    for where, base, step in (
        (dataclasses.replace(good, run_in=bad), "main", "check-ref-format"),
        (dataclasses.replace(good, branch=bad), "main", "check-ref-format"),
        (good, bad, "rev-parse"),
    ):
        for got in (preflight(where, base), create(where, base)):
            assert isinstance(got, GitFailed), got
            assert got.step == step and got.returncode is None
            assert "could not be run" in got.detail
    odd_path = dataclasses.replace(good, path=good.path + bad)
    assert isinstance(preflight(odd_path, "main"), Cleared)
    made = create(odd_path, "main")
    assert isinstance(made, AddFailed) and made.returncode is None, made
    assert "could not be run" in made.detail

    assert not has_branch(repo, "feat")
    assert list_worktrees(str(repo)) == (
        WorktreeEntry(str(repo), head=made_at(repo), branch="refs/heads/main"),
    )


def test_a_process_a_hook_leaves_running_does_not_hold_the_answer(repo: Path) -> None:
    """
    The hazard: the hook's child inherits git's stdout and stderr, and an answer read
    to end-of-file waits for the child. ``sleep 4`` against a limit of 1: the add and
    the branch both finish at once and are reported as finished.
    """
    for name in ("post-checkout", "reference-transaction"):
        hook = repo / ".git" / "hooks" / name
        hook.write_text("#!/bin/sh\nsleep 4 &\n")
        hook.chmod(0o755)

    ran = worktree._git(str(repo), "branch", "--no-track", "direct", "main", timeout=1.0)
    assert (ran.returncode, ran.timed_out) == (0, False)
    where = placed(repo, "feat")
    got = create(where, "main", timeout=1.0)

    assert got == Created(where.path, "feat", made_at(repo))


def _hooks_in(linked: Path, log: Path) -> None:
    """
    Untracked ``reference-transaction`` and ``post-checkout`` hooks in
    ``linked/.githooks``, each appending its name, first argument and directory to
    ``log``.
    """
    (linked / ".githooks").mkdir()
    for name in ("reference-transaction", "post-checkout"):
        hook = linked / ".githooks" / name
        hook.write_text(f'#!/bin/sh\necho "{name} $1 $PWD" >> {log}\n')
        hook.chmod(0o755)


def _logged_by_step(monkeypatch: pytest.MonkeyPatch, log: Path) -> list[tuple[str, list[str]]]:
    """
    Each git subcommand the module runs from here on, with the lines ``log`` gained
    while it ran. Read when the module has returned.
    """
    steps: list[tuple[str, list[str]]] = []
    real = worktree._git

    def recording(directory: str, *args: str, timeout: float = 10.0) -> worktree._Ran:
        before = log.read_text().splitlines() if log.exists() else []
        ran = real(directory, *args, timeout=timeout)
        after = log.read_text().splitlines() if log.exists() else []
        steps.append((args[0], after[len(before) :]))
        return ran

    monkeypatch.setattr(worktree, "_git", recording)
    return steps


def test_git_runs_hooks_from_the_launching_worktree_under_a_relative_hooks_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    git's behaviour, and the exposure the module docstring states: untracked files in
    a linked worktree are run by a ``create`` that succeeds, for its branch and for
    its add. The failed add is the next test.
    """
    git(repo, "config", "core.hooksPath", ".githooks")
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "stack", str(linked), "main")
    log = repo.parent / "hook-log"
    _hooks_in(linked, log)
    where = placed(linked, "feat")
    steps = _logged_by_step(monkeypatch, log)

    assert isinstance(create(where, "main"), Created)

    assert [step for step in steps if step[1]] == [
        (
            "branch",
            [
                f"reference-transaction prepared {linked}",
                f"reference-transaction committed {linked}",
            ],
        ),
        ("worktree", [f"post-checkout {'0' * 40} {where.path}"]),
    ]
    # From the main checkout the same setting names a directory that is not there.
    log.unlink()
    assert isinstance(create(placed(repo, "from-main"), "main"), Created)
    assert not log.exists()


def test_git_runs_the_launching_worktrees_hook_for_the_cleanup_of_a_failed_add(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    git's behaviour, under the same relative ``core.hooksPath``: the ``update-ref -d``
    that removes the branch after a failed add runs the linked worktree's
    ``reference-transaction`` hook too, three times, and the failed add itself runs
    nothing. The add fails as the ``stuck`` fixture's does, on an unwritable parent.
    """
    if os.geteuid() == 0:
        pytest.skip("root writes through directory permissions")
    git(repo, "config", "core.hooksPath", ".githooks")
    linked = repo.parent / "linked"
    git(repo, "worktree", "add", "-q", "-b", "stack", str(linked), "main")
    log = repo.parent / "hook-log"
    _hooks_in(linked, log)
    where = placed(linked, "feat")
    parent = Path(where.path).parent
    parent.mkdir()
    parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    steps = _logged_by_step(monkeypatch, log)

    try:
        got = create(where, "main")
    finally:
        parent.chmod(stat.S_IRWXU)

    assert isinstance(got, AddFailed), got
    assert [step for step in steps if step[1]] == [
        (
            "branch",
            [
                f"reference-transaction prepared {linked}",
                f"reference-transaction committed {linked}",
            ],
        ),
        (
            "update-ref",
            [
                f"reference-transaction aborted {linked}",
                f"reference-transaction prepared {linked}",
                f"reference-transaction committed {linked}",
            ],
        ),
    ]
    # The pre-flight's listing, the add, and the listing after it.
    assert steps.count(("worktree", [])) == 3


def test_git_runs_a_configured_smudge_filter_during_a_checkout(repo: Path, tmp_path: Path) -> None:
    """
    git's behaviour, and the second exposure the module docstring states: an empty
    ``GIT_ALLOW_PROTOCOL`` says nothing to a program a filter names.
    """
    marker = tmp_path / "smudged"
    (repo / ".gitattributes").write_text("* filter=probe\n")
    git(repo, "add", ".gitattributes")
    git(repo, "commit", "-q", "-m", "attributes")
    git(repo, "config", "filter.probe.smudge", f"touch {marker}; cat")
    assert not marker.exists()

    assert isinstance(create(placed(repo, "feat"), "main"), Created)

    assert marker.exists()


def test_an_add_stopped_at_the_timeout_cleans_nothing(repo: Path) -> None:
    """
    The checkout may still be running, so the branch is not this module's to remove.
    """
    _hook(repo, "sleep 1")
    where = placed(repo, "feat")

    got = create(where, "main", timeout=0.2)

    assert got == AddTimedOut(where.path, "feat", 0.2)
    assert has_branch(repo, "feat")


def _committed_hook(repo: Path, when: str) -> None:
    """
    A ``reference-transaction`` hook that lets the transaction through and then, once
    the ref is on disk, sleeps three seconds -- for every committed transaction, or
    with ``when`` of ``deletion`` only for one that removes a ref.
    """
    test = '[ "$1" = committed ]'
    if when == "deletion":
        test += f' && [ "$new" = {"0" * 40} ]'
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f"#!/bin/sh\nread old new ref\nif {test}; then sleep 3; fi\n")
    hook.chmod(0o755)


def test_a_branch_step_stopped_at_its_limit_says_the_branch_may_be_there(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hazard: git is stopped after the ref is written, the outcome says only that
    git failed, and the retry meets a branch nobody mentioned. The hook holds git in
    the ``committed`` phase, so the ref is on disk when the limit arrives; the last
    two assertions are that the outcome named the branch the retry then finds.
    """
    _committed_hook(repo, "every")
    monkeypatch.setattr(worktree, "_REF_WRITE_TIMEOUT", 1.0)
    where = placed(repo, "feat")

    got = create(where, "main")

    assert got == BranchTimedOut("feat", 1.0)
    assert has_branch(repo, "feat")
    assert not os.path.lexists(where.path)
    assert create(where, "main") == BranchExists("feat")


def test_a_deletion_stopped_at_its_limit_is_not_reported_as_gits_refusal(
    repo: Path, stuck: Placement, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hazard: the cleanup's ``update-ref -d`` is stopped in the same phase, the
    branch is gone, and the outcome says git would not delete it. What is known is
    that the deletion was stopped, and that is what ``kept`` has to say.
    """
    _committed_hook(repo, "deletion")
    monkeypatch.setattr(worktree, "_REF_WRITE_TIMEOUT", 1.0)

    got = create(stuck, "main")

    assert isinstance(got, AddFailedBranchLeft), got
    assert "Permission denied" in got.detail
    assert "stopped at 1s" in got.kept and "may or may not" in got.kept
    assert "would not" not in got.kept
    assert not has_branch(repo, "feat")


def test_a_missing_git_is_a_failure_with_a_reason(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    where = placed(repo, "feat")
    monkeypatch.setenv("PATH", str(repo / "no-such-bin"))

    assert isinstance(detect(str(repo)), Unclassified)
    listed = list_worktrees(str(repo))
    assert isinstance(listed, GitFailed) and listed.returncode is None
    made = create(where, "main")
    assert isinstance(made, GitFailed) and "could not be run" in made.detail


def test_a_checkout_that_would_fetch_fails_instead(repo: Path, tmp_path: Path) -> None:
    """
    A partial clone is missing its blobs, and checking a commit out fetches them from
    the promisor remote. The remote here is a ``file://`` URL, which is a transport
    like any other to git, so a fetch that is prevented fails and one that is not
    succeeds. The control arm is git's own add in the same clone, which fetches.
    """
    git(repo, "config", "uploadpack.allowFilter", "true")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    partial = tmp_path / "partial"
    git(tmp_path, "clone", "-q", "--filter=blob:none", "--no-checkout", f"file://{repo}", "partial")
    missing = git(partial, "rev-list", "--objects", "--missing=print", "main").stdout
    assert "\n?" in missing, "the fixture is not missing any object"

    where = placed(partial, "feat")
    got = create(where, "main")

    assert isinstance(got, AddFailed), got
    assert "not allowed" in got.detail
    assert not has_branch(partial, "feat") and not os.path.lexists(where.path)

    control = tmp_path / "control"
    git(partial, "worktree", "add", "-q", "-b", "control", str(control), "main")
    assert (control / "tracked.txt").exists()
