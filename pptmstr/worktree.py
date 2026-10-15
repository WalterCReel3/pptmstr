"""
Linked git worktrees: what a directory is, where a new one goes, making one, and
listing them.

``planning/2026-10-07-a-session-can-run-in-its-own-worktree.md`` is the record, and
its "Git traps to design for" table is why the shapes below are what they are. That
pptmstr runs git itself, rather than handing the CLI ``--worktree``, is the lead's
proposal there (R3) and the operator has not ratified it; the placement is the
operator's decision D4. No surface calls this module.

**Constraints on whoever does call it.** These functions run git unsandboxed, as the
operator, in the directory they are given, and git runs other programs.

- *Hooks, from a directory a session can write.* Every git call for a placement runs
  in ``Placement.run_in``, the checkout the request came from. A relative
  ``core.hooksPath`` is resolved against the working tree git is run in, so with
  ``core.hooksPath=.githooks`` and a request from a linked worktree ``W``, the hooks
  are the files in ``W/.githooks``, tracked or not. Measured on git 2.39.5, and
  pinned by ``tests/test_worktree.py``: ``reference-transaction`` there runs, with
  ``W`` as its directory, twice for ``git branch`` and three times for the
  ``update-ref -d`` after a failed add, and ``post-checkout`` there runs in the new
  worktree for a ``worktree add`` that succeeds. No write to
  ``config`` or to the git directory is needed, only a file in a tree the session
  owns. Running in ``main_root`` instead would read the operator's own hooks, at the
  cost of what a base of ``HEAD`` means (``Placement``). With no ``core.hooksPath``
  the hooks are the shared ``hooks/`` of the git directory, the operator's own
  programs, and ``worktree add`` runs them all the same.
- *Filters.* A checkout runs the ``smudge`` or ``process`` command of every filter
  ``.gitattributes`` names and ``config`` defines -- git-lfs is one -- and that
  command is an arbitrary program with the operator's network. Measured: a
  ``filter.<name>.smudge`` in the repository's ``config`` runs during ``create``.
- *The listing is not evidence.* ``list_worktrees`` reports paths git reads from
  files inside the git directory (``worktrees/<id>/gitdir``). The record's D2
  measured a contained session creating a file in another worktree's
  ``worktrees/<id>/`` (n=1); it did not try that directory's ``gitdir``, ``HEAD`` or
  ``index``. What git does with a rewritten ``gitdir`` is measured here (2.39.5,
  pinned by ``tests/test_worktree.py``): the path it names is the path listed, and
  an empty ``.git`` file there is enough for it to be listed as an ordinary
  worktree. A listed path is a claim about where a worktree is, not a directory the
  operator chose, and a caller must not treat it as trusted.
- *Which* ``config`` *is read is whatever the worktree's* ``.git`` *file names.*
  Measured on git 2.39.5, and pinned by ``tests/test_worktree.py``: that one-line
  file is the only thing in a linked worktree saying which repository it belongs to,
  git reads the admin directory it names without asking anything of that directory's
  ``gitdir`` backlink, and leaves the backlink as it found it. So a session that can
  write its own worktree can aim it at another repository's admin directory, or at a
  repository it built, and ``git branch`` and ``worktree add`` then run under that
  repository's ``config`` -- a ``reference-transaction`` hook named by a decoy's
  ``core.hooksPath`` runs for both. ``LinkedWorktree.main_root`` is therefore a claim
  made by the directory asked about. ``detect`` refuses two shapes of it and no
  more: an admin directory whose backlink does not name the worktree, which is
  every repository the session did not author, and a main checkout inside the
  worktree. **A decoy outside the worktree that names it back is reported as**
  ``main_root``; nothing in the worktree says which repository is the real one, so
  a caller that runs git on the strength of ``main_root`` must hold it from
  somewhere a session cannot write. Unmeasured: whether a contained session can
  write the real admin directory's ``gitdir`` or ``commondir``.

So running any of this in a directory an autonomous session has written is running
the operator's git on that session's terms.

**Blocking, and split the way ``tree.py`` and ``cli_version.py`` split.** The
subprocess sits behind four names -- ``detect``, ``list_worktrees``, ``preflight`` and
``create`` -- and none of them may be called from the reducer, a draw call or the
asyncio loop. What is decided from git's answer is pure and takes text:
``classify_checkout``, ``parse_worktree_list``, ``place``, ``collision``.

**Nothing here raises, and every answer says which thing happened.** An argument no
process can be started with -- a NUL, a lone surrogate -- is a child that could not be
run, which is a value like any other failure. git's exit codes do not partition its
failures (128 is "not a repository", "inside a git directory", "bare", "no such
directory" and "not a valid branch name" at once; 255 is "branch exists" and "bad
base" at once) and its messages follow the locale, so a mistake the operator can
correct is established by asking git a narrower question first -- a pre-flight -- and
comes back as its own variant. What no pre-flight anticipated comes back as
``GitFailed`` carrying git's own words, and is never folded into one of the named
ones.

**What this module will not do.**

- *Delete a worktree, ever*, or a branch it did not create in the same call (the
  record's D8 keeps removal an operator action). No ``remove``, no ``prune``, no ``gc``.
- *Fetch.* Every child runs with ``GIT_ALLOW_PROTOCOL`` empty, which denies every
  transport, so a checkout in a partial clone fails on its first missing object
  instead of contacting the promisor remote. That covers git itself and nothing git
  starts: a hook or a filter command (above) reaches the network as any program
  does, so "never fetches" is a property of this module's git and not of a call.
- *Choose a base.* ``create`` and ``preflight`` take ``base`` with no default; which
  ref to offer is the record's O1 and is the caller's to answer.
- *Pass* ``--worktree`` *to the CLI*: the record measured a host-made worktree plus the
  CLI's own nesting a second one under the main checkout.

**Why the branch is created by its own call and not by** ``worktree add -b``. Measured
on git 2.39.5, and pinned by ``tests/test_worktree.py``: ``add -b`` creates the branch
first, and when the add then fails -- the path exists, the parent is unwritable, the
path is missing but still registered -- the branch stays behind, so the retry dies on
"a branch named X already exists" and the first cause is gone. Cleaning that up after
``add -b`` means inferring ownership from "absent a moment ago, present now", which is
a guess with a window in it, and a wrong guess deletes somebody's branch. ``git branch``
exits zero only when this call created the ref, so ``create`` runs it and then
``worktree add <path> <branch>``, and removes the branch on a failed add only on that
evidence. Re-measured for the two-step form on 2.39.5: the same four failures leave the
branch and register nothing.

**A ref write stopped at its limit is an unknown, and is reported as one.** ``git
branch`` and ``update-ref -d`` both run the ``reference-transaction`` hook, and its
``committed`` phase comes after the ref is on disk: a hook that blocks there gets git
stopped at ``_REF_WRITE_TIMEOUT`` with the write already made (measured, 2.39.5). So
neither is evidence of anything. A stopped ``git branch`` is ``BranchTimedOut``, with
nothing deleted, and a stopped deletion says in ``kept`` that the branch may or may
not be gone.

**Why a failed add does not always remove the branch.** ``worktree add`` exits with a
``post-checkout`` hook's status. A hook that exits 3 produces a complete, registered
worktree with the branch checked out in it and an exit code of 3 (measured, 2.39.5).
``update-ref -d`` on that branch succeeds and leaves the worktree on a ref that no
longer exists, reporting "No commits yet". So ``create`` reads the registration after
a failure and reports ``AddFailedWorktreeLeft`` with everything intact, and deletes
only a branch no worktree has checked out.

**The deletion is compare-and-delete, of the ref itself.** ``update-ref --no-deref -d
refs/heads/<branch> <oid>`` with the object id the branch was created at: git refuses
it when the ref has moved, so a branch somebody has since pointed elsewhere is kept
and reported. ``--no-deref`` because without it a ref that has become symbolic is
followed and the branch it points at is the one deleted (measured, 2.39.5). With it
git compares the old value through the symbolic ref and deletes the symbolic ref, so
``create`` asks ``symbolic-ref`` first and keeps one it finds. git 2.39 has no single
command that both refuses a symbolic ref and compares a value, so between that
question and the deletion there is a window; what can be lost in it is a symbolic
ref somebody put at this call's branch name, and never the branch it points at.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass, replace

__all__ = [
    "AddFailed",
    "AddFailedBranchLeft",
    "AddFailedPathHeld",
    "AddFailedWorktreeLeft",
    "AddTimedOut",
    "BaseNotACommit",
    "BranchExists",
    "BranchTimedOut",
    "Checkout",
    "Cleared",
    "CreateOutcome",
    "Created",
    "GitFailed",
    "InvalidBranch",
    "LinkedWorktree",
    "MainCheckout",
    "NotARepository",
    "NothingToName",
    "PathExists",
    "PathRegistered",
    "Placed",
    "Placement",
    "Refusal",
    "SlugTaken",
    "SlugUnjudged",
    "Unclassified",
    "WorktreeEntry",
    "classify_checkout",
    "collision",
    "create",
    "detect",
    "list_worktrees",
    "parse_worktree_list",
    "place",
    "preflight",
    "sanitised",
    "slug_for",
]


# ---------------------------------------------------------------------------
# The child process
# ---------------------------------------------------------------------------

# The only `GIT_*` variables a child inherits. Every other one is dropped, whatever it
# is: a process started from a hook or from `rebase -x` carries GIT_INDEX_FILE,
# GIT_PREFIX and their kin, each of which redirects where a repository is looked for or
# what it is taken to contain, and git's own list of them (`rev-parse
# --local-env-vars`) grows and omits GIT_NAMESPACE. What is kept says how the
# operator's git is installed and configured, and nothing about which repository:
#
# - GIT_CONFIG_GLOBAL, GIT_CONFIG_SYSTEM, GIT_CONFIG_NOSYSTEM: which configuration
#   files are the operator's.
# - GIT_EXEC_PATH: where this git keeps its own subprograms.
# - GIT_CEILING_DIRECTORIES: a boundary the operator put on repository discovery.
#   Dropping it would find a repository they fenced off.
# - GIT_DISCOVERY_ACROSS_FILESYSTEM: the other half of that boundary, whether
#   discovery crosses a mount point. It names no repository either, and dropping it
#   would lose a checkout the operator's own git finds.
_KEPT_GIT_ENV = frozenset(
    {
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_EXEC_PATH",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    }
)

# A question git answers from the repository alone. Generous for the same reason
# `cli_version._DEFAULT_TIMEOUT` is: a cold cache must not read as a broken repository.
_QUERY_TIMEOUT = 10.0

# `worktree add` checks out every file and then runs the operator's `post-checkout`
# hook. The record measured 1.8 s for 20,000 files warm, and 2.09 s with a hook that
# sleeps two seconds.
_ADD_TIMEOUT = 120.0

# `git branch` and `update-ref -d` write one ref and run the `reference-transaction`
# hook. Its own name, and read when the call is made, because what such a call reports
# when it is stopped is this number.
_REF_WRITE_TIMEOUT = _QUERY_TIMEOUT


def _child_env() -> dict[str, str]:
    """
    The environment every git child runs in.

    ``LC_ALL=C`` so that the one message this module reads is in the language it is
    matched in, and ``LANGUAGE`` removed because gettext consults it ahead of the
    locale on some builds. No inherited ``GIT_*`` variable outside ``_KEPT_GIT_ENV``
    survives, so the directory argument is the only thing that says which repository,
    index and work tree are meant. An empty ``GIT_ALLOW_PROTOCOL`` allows no transport
    at all, and ``GIT_TERMINAL_PROMPT=0`` turns a credential prompt nobody can see into
    a failure.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k != "LANGUAGE" and (not k.startswith("GIT_") or k in _KEPT_GIT_ENV)
    }
    env["LC_ALL"] = "C"
    env["GIT_ALLOW_PROTOCOL"] = ""
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


@dataclass(frozen=True, slots=True)
class _Ran:
    """
    One git child, as it ended. ``returncode`` is None when there is no exit status to
    report -- git could not be started, or was stopped at the timeout -- and ``stderr``
    then holds this module's account of why.
    """

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


def _git(directory: str, *args: str, timeout: float = _QUERY_TIMEOUT) -> _Ran:
    """
    Run ``git -C directory args``. **Blocking.** Never raises.

    ``-C`` rather than the child's cwd, so a directory that does not exist is git's
    exit 128 with a sentence, and the only ``OSError`` left is git itself being
    missing or not executable. ``ValueError`` is an argument no process can be given:
    a NUL, or a lone surrogate, which has no encoding.

    **git's exit is what is waited for, and its output goes to files.** A hook that
    leaves a child running hands that child git's stdout and stderr. Read through
    pipes, the answer would not arrive until the child let go of them, and a command
    that had already succeeded would be reported as out of time; a file has no end to
    wait for. Whatever such a child writes after git has exited is not read.

    stdout is decoded the way the filesystem decodes names (``os.fsdecode``), because
    paths in it go back to the filesystem and a name that is not UTF-8 has to survive
    the round trip. stderr is only ever shown, so its bad bytes are replaced.
    """
    try:
        with (
            tempfile.TemporaryFile() as out,
            tempfile.TemporaryFile() as err,
            subprocess.Popen(
                ["git", "-C", directory, *args],
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                env=_child_env(),
            ) as proc,
        ):
            try:
                returncode = proc.wait(timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                return _Ran(None, "", f"did not finish within {timeout:g}s", timed_out=True)
            stdout = os.pread(out.fileno(), os.fstat(out.fileno()).st_size, 0)
            stderr = os.pread(err.fileno(), os.fstat(err.fileno()).st_size, 0)
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        return _Ran(None, "", f"git could not be run: {type(exc).__name__}: {exc}")
    return _Ran(returncode, os.fsdecode(stdout), stderr.decode("utf-8", errors="replace"))


def _words(ran: _Ran) -> str:
    """
    What a failed child said, as one line for an operator: its stderr without the
    progress line ``worktree add`` prints first, or its exit status when it said
    nothing.
    """
    lines = [ln.strip() for ln in ran.stderr.splitlines() if ln.strip()]
    said = [ln for ln in lines if not ln.startswith("Preparing worktree")]
    if said:
        return " ".join(said)
    return "(no output)" if ran.returncode is None else f"exit {ran.returncode}, no output"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NotARepository:
    """
    git looked at the directory and every parent and found no repository, and none of
    the directories it looked in holds a ``.git`` entry.
    """


@dataclass(frozen=True, slots=True)
class MainCheckout:
    """
    The directory is in the main working tree of a repository whose git directory is
    that tree's own ``.git``. ``root`` is the top of the tree, which need not be the
    directory asked about.
    """

    root: str


@dataclass(frozen=True, slots=True)
class LinkedWorktree:
    """
    The directory is in a linked worktree. ``root`` is the top of that worktree and
    ``main_root`` the top of the main checkout whose git directory it shares.

    ``main_root`` **is a claim made by the directory asked about, not a defence.** It
    is read off the worktree's own ``.git`` file, which whoever can write the
    worktree can aim at a repository they built, and ``detect`` reports such a
    repository here when it sits outside the worktree and names it back. A caller
    that runs git on the strength of ``main_root`` holds it from somewhere a
    session cannot write (the module docstring, "Which config is read").
    """

    root: str
    main_root: str


@dataclass(frozen=True, slots=True)
class Unclassified:
    """
    git answered, or failed, in a way that is none of the three above. ``reason`` says
    what was seen, for the operator.

    This is where the arrangements land that the plain reading mislabels: a submodule,
    a bare repository and its worktrees, the inside of a git directory, a separated git
    directory, a broken ``gitdir:`` pointer, a path with a newline in it, a ``.git``
    git would not take as a repository, and a linked worktree whose main repository
    is one of those. A caller that needs "main" or
    "linked" to place a worktree has neither here, and that is the point -- none of
    these has a main checkout to name a worktree after.
    """

    reason: str


Checkout = NotARepository | MainCheckout | LinkedWorktree | Unclassified

_DETECT_ARGS = (
    "rev-parse",
    "--path-format=absolute",
    "--is-inside-work-tree",
    "--show-toplevel",
    "--git-dir",
    "--git-common-dir",
)

# The head of git's sentence for "no repository here or above", under LC_ALL=C. The
# remainder varies: "(or any of the parent directories): .git", or "(or any parent up
# to mount point /x)". A broken `gitdir:` pointer says "not a git repository: <path>"
# and is deliberately not matched.
_NO_REPOSITORY = "fatal: not a git repository (or any"


def classify_checkout(returncode: int | None, stdout: str, stderr: str) -> Checkout:
    """
    What ``git rev-parse`` with ``_DETECT_ARGS`` said about a directory. Pure.

    The plain reading is "git-dir equals git-common-dir means main, different means
    linked, and the shared root is the common dir's parent". Each rule below is a case
    the record measured that reading getting wrong.

    - **Exit 128 is three situations.** Not a repository prints nothing on stdout;
      inside a git directory and a bare repository both print ``false`` before the
      fatal, and this call cannot tell those two apart, so they share one reason that
      names both.
    - **Exit 128 with nothing on stdout is not always "no repository".** A ``.git``
      file pointing nowhere, a directory that does not exist and a repository git
      refuses on ownership all look the same by status and stdout. So the one sentence
      that means "nothing here or above" is matched, under a locale this module set.
      The match can only fail towards ``Unclassified`` with git's words in it.
    - **A submodule's git-dir equals its common dir**, and so does a repository made
      with ``--separate-git-dir``. A main checkout is therefore also required to have
      its git directory at ``<toplevel>/.git``.
    - **A bare repository's worktree has a common dir whose parent is not a
      checkout.** A linked worktree is required to hang off a common dir named
      ``.git``, under its ``worktrees/``. A bare repository that is itself named
      ``.git`` passes this test and is not distinguished.
    - **A path containing a newline** breaks the four-line answer, since ``rev-parse``
      has no NUL-separated form. Anything other than four lines of the expected kinds
      is unclassified.
    """
    if returncode is None:
        return Unclassified(stderr.strip() or "git could not be run")

    lines = stdout.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    if returncode == 128:
        if not lines and any(ln.startswith(_NO_REPOSITORY) for ln in stderr.splitlines()):
            return NotARepository()
        if lines == ["false"]:
            return Unclassified(
                "inside a git directory or a bare repository; this is not a working tree"
            )
        first = stderr.strip().splitlines()
        return Unclassified(first[0] if first else "git exited 128 and said nothing")
    if returncode != 0:
        first = stderr.strip().splitlines()
        return Unclassified(first[0] if first else f"git exited {returncode} and said nothing")

    if len(lines) != 4 or not all(os.path.isabs(p) for p in lines[1:]):
        return Unclassified(
            "git's answer is not a flag and three absolute paths; "
            "a path with a newline in it reads this way"
        )
    inside, top, git_dir, common = lines
    if inside != "true":
        return Unclassified(f"git reports {top} is not a working tree")

    if git_dir == common:
        if git_dir == os.path.join(top, ".git"):
            return MainCheckout(top)
        return Unclassified(
            f"the git directory of {top} is {git_dir}, not its own .git: "
            "a submodule, or a repository with a separated git directory"
        )

    hangs_off_checkout = os.path.basename(common) == ".git" and os.path.dirname(
        git_dir
    ) == os.path.join(common, "worktrees")
    if not hangs_off_checkout:
        return Unclassified(
            f"{top} is a worktree of {common}, which is not the .git of a main checkout: "
            "a bare repository's worktree, or a submodule's"
        )
    return LinkedWorktree(root=top, main_root=os.path.dirname(common))


def _passed_over(directory: str, answered_for: str | None) -> str | None:
    """
    The nearest ``.git`` entry at or above ``directory`` that git's answer does not
    account for, or None. ``answered_for`` is the top of the working tree git
    answered with, whose own ``.git`` is the one it accepted, and None when git found
    no repository.

    The walk covers the directories git's discovery looks in: upwards from the
    directory, stopping where ``GIT_CEILING_DIRECTORIES`` stops git -- a listed
    directory is not looked in when the walk started below it -- because the child
    keeps that variable and an entry git was fenced off from is not one it passed
    over. Otherwise to the filesystem root, since every directory on the way is one
    whose ``.git`` git would have used had it been sound. git also stops at a mount
    point, unless ``GIT_DISCOVERY_ACROSS_FILESYSTEM`` says otherwise, and this does
    not, so an entry above a mount point is reported although git never saw it: the
    error is towards ``Unclassified``, and ``detect`` words that answer for both.
    """
    try:
        here = os.path.realpath(directory)
        fences = {
            os.path.realpath(fence)
            for fence in os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep)
            if os.path.isabs(fence)
        }
    except (OSError, ValueError):
        return None
    started = here
    while here != answered_for and (here == started or here not in fences):
        entry = os.path.join(here, ".git")
        if os.path.lexists(entry):
            return entry
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return None


# A backlink is one path. Read to a bound because the file may be one a session
# wrote: PATH_MAX is 4096 on Linux, and this is room for that and its newline.
_BACKLINK_LIMIT = 8192


def _backlink(admin: str) -> str | None:
    """
    The path a worktree's admin directory names in its ``gitdir`` file -- where git
    recorded that worktree's ``.git`` to be -- or None when there is no such file to
    read. A relative one is against the admin directory, as git reads it.

    **Opened without blocking and read only if it is a regular file.** The admin
    directory is whatever the worktree's pointer named, so this file may be a
    session's, and a FIFO there would hold an ordinary open for as long as nobody
    wrote to it.
    """
    try:
        fd = os.open(os.path.join(admin, "gitdir"), os.O_RDONLY | os.O_NONBLOCK)
    except (OSError, ValueError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, _BACKLINK_LIMIT + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    named = os.fsdecode(raw).rstrip("\r\n")
    if not named or len(raw) > _BACKLINK_LIMIT or "\0" in named:
        return None
    return os.path.normpath(os.path.join(admin, named))


def _same_entry(one: str, other: str) -> bool:
    """
    Whether two paths name the same directory entry: the same name in the same
    resolved directory. The last component is deliberately not resolved, so a
    symlink to an entry is not that entry.
    """
    try:
        return os.path.basename(one) == os.path.basename(other) and os.path.realpath(
            os.path.dirname(one)
        ) == os.path.realpath(os.path.dirname(other))
    except (OSError, ValueError):
        return False


def _lies_inside(path: str, directory: str) -> bool:
    """Whether ``path`` is ``directory`` or below it, both resolved."""
    try:
        inner, outer = os.path.realpath(path), os.path.realpath(directory)
        return os.path.commonpath([inner, outer]) == outer
    except (OSError, ValueError):
        return False


def detect(directory: str, *, timeout: float = _QUERY_TIMEOUT) -> Checkout:
    """
    What ``directory`` is to git. **Blocking**: one child process, a second for a
    linked worktree, and a look at the disk for a ``.git`` git did not use.

    The record measured 3-4 ms warm; a cold cache was not measured.

    **git passes over a** ``.git`` **it cannot read as a repository** -- an empty
    directory, one whose ``HEAD`` is not a ref -- and carries on to the parent, with
    no word about it. So a damaged repository with nothing above it gets the same
    sentence as a plain directory, and one inside another checkout is answered for as
    part of that checkout (both measured, 2.39.5). The record measured a contained
    session rewriting a repository's ``HEAD``, and "not a repository" is the answer
    with the fewest rules attached. Neither answer is passed on when a ``.git`` entry
    sits between the directory and whatever git answered with: that is
    ``Unclassified``, naming the entry. A sound ``.git`` above a mount point git
    stopped at is found the same way and gets the same answer, so the reason given
    offers both.

    **git answers for a linked worktree without reading the main repository's**
    ``HEAD`` (measured, 2.39.5): the worktree's own admin directory is what it
    validates, and ``git branch`` and ``worktree add`` go on working from it. So
    ``main_root`` is asked about as well, by the same question, and a linked worktree
    whose main checkout git does not answer for as a main checkout at that path is
    ``Unclassified``. That is git's judgement of the main repository and not a second
    walk: no ``.git`` is looked for around ``main_root``.

    **Nor does git ask whether the repository names the worktree back** (measured,
    2.39.5): it follows the worktree's ``.git`` file to whatever admin directory that
    names. So the admin directory's ``gitdir`` is read here, and a linked worktree it
    does not name is ``Unclassified`` -- a worktree moved without ``git worktree
    repair`` reads this way, and so does a pointer rewritten to another repository's
    worktree. And a main checkout that lies inside the worktree is ``Unclassified``
    whatever it names, because ``place`` would put a new worktree inside the one the
    request came from.

    Neither makes ``main_root`` evidence. A repository built to name the worktree
    back, anywhere outside it, passes all of this (the module docstring, "Which
    config is read").
    """
    ran = _git(directory, *_DETECT_ARGS, timeout=timeout)
    answer = classify_checkout(ran.returncode, ran.stdout, ran.stderr)
    if isinstance(answer, Unclassified):
        return answer
    answered_for = None if isinstance(answer, NotARepository) else answer.root
    entry = _passed_over(directory, answered_for)
    if entry is not None and answered_for is None:
        return Unclassified(
            f"git finds no repository, and {entry} exists: "
            "a damaged or empty git directory reads this way, and so does a sound one "
            "above a mount point with GIT_DISCOVERY_ACROSS_FILESYSTEM unset"
        )
    if entry is not None:
        return Unclassified(
            f"git answers for {answered_for}, and {entry} is nearer: "
            "a damaged or empty git directory is passed over this way"
        )
    if isinstance(answer, LinkedWorktree):
        if _lies_inside(answer.main_root, answer.root):
            return Unclassified(
                f"{answer.root} is a worktree of {answer.main_root}, which lies inside it: "
                "a worktree placed beside that checkout would be inside this one"
            )
        # The third line of the answer `classify_checkout` has just accepted.
        admin = ran.stdout.split("\n")[2]
        backlink = os.path.join(admin, "gitdir")
        named = _backlink(admin)
        if named is None or not _same_entry(named, os.path.join(answer.root, ".git")):
            return Unclassified(
                f"{answer.root} is a worktree of {answer.main_root}, whose record of it, "
                f"{backlink}, "
                + ("is not a file naming a path" if named is None else f"names {named}")
                + ": a worktree moved without `git worktree repair` reads this way, and "
                "so does a .git file rewritten to point at another repository"
            )
        main = _git(answer.main_root, *_DETECT_ARGS, timeout=timeout)
        if classify_checkout(main.returncode, main.stdout, main.stderr) != MainCheckout(
            answer.main_root
        ):
            return Unclassified(
                f"{answer.root} is a worktree of {answer.main_root}, "
                "which git does not read as a main checkout: "
                f"{_words(main) if main.returncode != 0 else 'it answers for something else'}"
            )
    return answer


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WorktreeEntry:
    """
    One record of ``git worktree list --porcelain``.

    ``branch`` is the full ref as git prints it (``refs/heads/feat``), and is None for
    a detached ``HEAD`` and for a bare main entry. ``head`` is None only for a bare
    entry.

    ``locked`` and ``prunable`` are None when the worktree is neither, and otherwise
    the reason git gives -- the empty string for a lock taken without one. One field
    each, so "locked" and "why" cannot disagree.
    """

    path: str
    head: str | None = None
    branch: str | None = None
    detached: bool = False
    bare: bool = False
    locked: str | None = None
    prunable: str | None = None


def parse_worktree_list(text: str) -> tuple[WorktreeEntry, ...]:
    """
    The records in the output of ``git worktree list --porcelain -z``. Pure and total.

    With ``-z`` each attribute ends in NUL and each record in a second NUL, and nothing
    is quoted, so a path or a lock reason holding a newline arrives as itself. That is
    the reason for ``-z``: the line form cannot carry such a path.

    An attribute is a label, then optionally a space and a value. A label this does not
    know is skipped, so a newer git that reports more still parses. A record with no
    ``worktree`` attribute is not a worktree and is dropped.
    """
    entries: list[WorktreeEntry] = []
    fields: dict[str, str] = {}

    def close() -> None:
        if "worktree" in fields:
            entries.append(
                WorktreeEntry(
                    path=fields["worktree"],
                    head=fields.get("HEAD"),
                    branch=fields.get("branch"),
                    detached="detached" in fields,
                    bare="bare" in fields,
                    locked=fields.get("locked"),
                    prunable=fields.get("prunable"),
                )
            )
        fields.clear()

    for attribute in text.split("\0"):
        if not attribute:
            close()
            continue
        label, _, value = attribute.partition(" ")
        fields[label] = value
    close()
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class GitFailed:
    """
    A git call ended in a way no pre-flight names. ``step`` is the subcommand,
    ``returncode`` its exit status or None when it never produced one, and ``detail``
    git's own words.

    The catch-all, and deliberately not a refusal: nothing here says the operator made
    a mistake, only that git did not do what was asked.
    """

    step: str
    returncode: int | None
    detail: str


def list_worktrees(
    directory: str, *, timeout: float = _QUERY_TIMEOUT
) -> tuple[WorktreeEntry, ...] | GitFailed:
    """
    Every worktree of the repository ``directory`` belongs to, main first. **Blocking.**

    Every one: the operator's own and any made by hand as well as any ``create`` made.
    No branch prefix or marker is imposed, so nothing in a record says which is which.

    Read from git each time and kept nowhere: an index written by this application
    would have this application's failure modes (the record's "Clean up", after
    ``2026-08-31`` D1). ``-z`` needs git 2.36; an older git exits with a usage error,
    which comes back as ``GitFailed``.
    """
    ran = _git(directory, "worktree", "list", "--porcelain", "-z", timeout=timeout)
    if ran.returncode != 0:
        return GitFailed("worktree list", ran.returncode, _words(ran))
    return parse_worktree_list(ran.stdout)


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

_NOT_SLUG = re.compile(r"[^A-Za-z0-9-]")
_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]")


def slug_for(branch: str) -> str:
    """
    The directory name for a branch: every character outside ``[A-Za-z0-9-]`` becomes
    ``-``, and dashes at either end are dropped. Empty when nothing is left.

    The CLI's own transcript-directory sanitiser applied early, so the name on disk is
    already the name it would be given. Many branches share a slug (``feat/x``,
    ``feat.x``, ``feat_x``); ``collision`` and the path check in ``preflight`` are what
    notice.
    """
    return _NOT_SLUG.sub("-", branch).strip("-")


def sanitised(path: str) -> str:
    """
    ``path`` as the CLI and the SDK name its transcript directory: every character
    that is not an ASCII letter or digit becomes ``-``.

    Reproduced from ``claude_agent_sdk._internal.sessions._sanitize_path`` because that
    name is private. Up to ``_SLUG_LIMIT`` characters this is the directory's name, and
    two paths that agree here share a transcript directory, which is the fact
    uniqueness is judged on. Past it the name is the first ``_SLUG_LIMIT`` characters
    of this and a hash of the path, and the hash is not reproduced: the SDK's own
    ``_find_project_dir`` records that the CLI and the SDK compute different ones, and
    finds a long path's directory by that prefix alone. ``collision`` is what treats
    the two lengths differently. tests/test_worktree.py holds this and the SDK's
    together.
    """
    return _NOT_ALNUM.sub("-", path)


# ``claude_agent_sdk._internal.sessions.MAX_SANITIZED_LENGTH``: the longest sanitised
# path that is a transcript directory's whole name.
_SLUG_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Placement:
    """
    Where a new worktree for ``branch`` goes, and where git is run to make it.

    ``run_in`` is the top of the checkout the request came from, and it is where every
    git call for this placement runs. Refs are shared by every worktree of a
    repository and ``HEAD`` is not, so a base of ``HEAD`` means the launching
    checkout's -- the same reading ``scripts/worktree.py`` gives it.
    """

    run_in: str
    main_root: str
    branch: str
    path: str


@dataclass(frozen=True, slots=True)
class NothingToName:
    """
    The branch name has no letter or digit in it, so there is no directory name to
    make from it.
    """

    branch: str


Placed = Placement | NothingToName


def place(
    checkout: MainCheckout | LinkedWorktree, branch: str, *, home: str | None = None
) -> Placed:
    """
    The directory a worktree for ``branch`` goes in. No filesystem, no git; ``home``
    is the user's, from the environment, when it is not given.

    ``<home>/.pptmstr/worktrees/<repository's directory name>/<slug>`` -- outside
    every repository, under one home for all of them (D4), from whichever of its
    worktrees the request came. The answer is final as text before anything exists
    on disk, which is what lets a launch resolve its cwd from it ahead of the
    directory being made.

    "The repository" is whatever ``checkout`` says it is. For a ``LinkedWorktree``
    that is ``main_root``, a claim made by the directory that was asked about and
    not a defence: the name is the name of the repository the worktree's ``.git``
    file names, and ``Placement.main_root`` carries the same claim on. Nothing here
    can tell that repository from the one the operator means.

    Two repositories with one directory name share ``<repository>``, so a branch of
    each with one slug is one path. Whether the directory is free is not asked here;
    that needs the disk and the worktree list, and is ``preflight``'s, which refuses
    a path that exists.
    """
    slug = slug_for(branch)
    if not slug:
        return NothingToName(branch)
    main_root = checkout.root if isinstance(checkout, MainCheckout) else checkout.main_root
    base = os.path.expanduser("~") if home is None else home
    path = os.path.join(base, ".pptmstr", "worktrees", os.path.basename(main_root), slug)
    return Placement(run_in=checkout.root, main_root=main_root, branch=branch, path=path)


@dataclass(frozen=True, slots=True)
class PathRegistered:
    """
    git already has a worktree registered at exactly this path. ``missing`` is true
    when the directory itself is gone, which is the case a check of the disk alone
    cannot see.

    From ``collision``, which has only the listing, ``missing`` is whether git calls
    the entry prunable -- and git never calls a locked one that, so a locked worktree
    whose directory is gone reads as present there. From ``preflight`` it is the
    disk's answer.
    """

    path: str
    missing: bool


@dataclass(frozen=True, slots=True)
class SlugTaken:
    """
    A different worktree of this repository, at ``other``, sanitises to the same
    transcript directory as ``path``. Sessions in the two would share transcripts and
    briefs.
    """

    path: str
    other: str


@dataclass(frozen=True, slots=True)
class SlugUnjudged:
    """
    ``path`` and a different worktree of this repository, at ``other``, both sanitise
    to more than ``_SLUG_LIMIT`` characters and agree on the first ``_SLUG_LIMIT``.
    Whether sessions in the two would share transcripts is not something this module
    can say.

    Their transcript directories are that shared prefix and a hash each. The hashes
    are the CLI's, which this module does not compute, and the SDK looks a long path's
    directory up by the prefix when its own hash names nothing -- which either
    directory satisfies.
    """

    path: str
    other: str


def collision(
    placement: Placement, entries: tuple[WorktreeEntry, ...]
) -> PathRegistered | SlugTaken | SlugUnjudged | None:
    """
    Whether a registered worktree already has ``placement``'s path, or its sanitised
    form. Pure: paths are compared as text, so a caller holding a path with a symlink
    in it resolves it first (``preflight`` does), because git lists resolved ones.

    Uniqueness is judged on the sanitised form because that is what names the
    transcript directory: ``…/repo-worktrees/feat_x``, made by hand, and
    ``…/repo-worktrees/feat-x`` are different directories and one transcript slug. Only
    worktrees of this repository are known here; a directory that is not one and
    happens to sanitise the same is not seen.

    The sanitised form is the whole name only up to ``_SLUG_LIMIT`` characters
    (``sanitised``). Two forms both past it are ``SlugUnjudged`` when they share the
    prefix and distinct when they do not; one past it and one within it never name the
    same directory, the longer name being the longer.
    """
    for entry in entries:
        if entry.path == placement.path:
            return PathRegistered(placement.path, missing=entry.prunable is not None)
    wanted = sanitised(placement.path)
    for entry in entries:
        theirs = sanitised(entry.path)
        if len(wanted) > _SLUG_LIMIT and len(theirs) > _SLUG_LIMIT:
            if theirs[:_SLUG_LIMIT] == wanted[:_SLUG_LIMIT]:
                return SlugUnjudged(placement.path, entry.path)
        elif theirs == wanted:
            return SlugTaken(placement.path, entry.path)
    return None


# ---------------------------------------------------------------------------
# Pre-flight and creation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InvalidBranch:
    """
    git will not take ``branch`` as a branch name. ``would_be`` is the name git would
    have used instead when it accepted the text and read it as something else --
    ``@{-1}`` names the previously checked-out branch -- and None when it rejected it
    outright.
    """

    branch: str
    would_be: str | None


@dataclass(frozen=True, slots=True)
class BranchExists:
    """
    ``refs/heads/<branch>`` is already there, as a branch or as a symbolic ref --
    including a symbolic ref whose target does not exist, which ``git branch`` would
    write through to create the target. This module only makes new branches.
    """

    branch: str


@dataclass(frozen=True, slots=True)
class BaseNotACommit:
    """
    ``base`` does not name a commit in this repository: no such ref, a ref to
    something that is not one, or text git reads as something other than one object
    (``^main``).
    """

    base: str


@dataclass(frozen=True, slots=True)
class PathExists:
    """
    Something is already at the target path -- a file, a directory, a dangling link.
    An empty directory counts: git would accept one, and whose it is is not known.
    """

    path: str


Refusal = (
    InvalidBranch
    | BranchExists
    | BaseNotACommit
    | PathExists
    | PathRegistered
    | SlugTaken
    | SlugUnjudged
)


@dataclass(frozen=True, slots=True)
class Cleared:
    """
    Every pre-flight passed. ``commit`` is the object id ``base`` resolved to, and is
    what the branch is created at.
    """

    commit: str


_OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


# A name `check-ref-format --branch` takes in any directory git can enter. It is the
# control that separates "this name is refused" from "nothing is answered here".
_CONTROL_BRANCH = "x"

# git's exit status for a `die()`: what `check-ref-format --branch` exits with for a
# name it refuses, and what git exits with for a directory it cannot enter.
_DIED = 128


def _as_listed(run_in: str, path: str) -> str:
    """
    ``path`` as ``git worktree list`` prints it once a worktree is there: absolute,
    taken from the directory git runs in, with its symlinks followed. A path the
    filesystem cannot resolve is returned as given, which matches no entry.
    """
    try:
        return os.path.realpath(os.path.join(run_in, path))
    except (OSError, ValueError):
        return path


def preflight(placement: Placement, base: str) -> Cleared | Refusal | GitFailed:
    """
    Ask git the questions whose answers a failed ``worktree add`` would blur.
    **Blocking**: five child processes and one look at the disk.

    In order, each with its own answer:

    1. ``check-ref-format --branch`` -- is the name one git accepts, *and* does git
       read it as that name. The second half is why stdout is compared: the command
       expands ``@{-1}`` to another branch and exits zero. A refused name and a
       directory git cannot enter both exit 128, so that exit is followed by the same
       question about ``_CONTROL_BRANCH``: only when that one passes was it the name.
       Any other ending -- a child killed by a signal has a negative status -- is no
       answer about the name, and is ``GitFailed`` without the control.
    2. ``show-ref --verify refs/heads/<branch>`` -- exit 0 is exists, 1 is absent.
    3. ``symbolic-ref -q refs/heads/<branch>`` -- exit 0 is a symbolic ref, 1 is not
       one. A symbolic ref whose target is missing is absent to ``show-ref``, and
       ``git branch`` creates the *target* through it.
    4. ``rev-parse --verify --end-of-options <base>^{commit}`` -- exit 1 is "no such
       commit", and so is exit 0 with anything but one object id (``^main`` prints a
       negated one). ``--end-of-options`` because a base that starts with a dash is
       otherwise read as an option, and some of those die with exit 128
       (``--path-format=x``, measured on 2.39.5): behind it such a base is a revision
       that does not exist. A git too old to know the flag answers exit 1 for every
       base; ``list_worktrees`` already needs a newer one than that.
    5. Nothing at the path. ``lexists``, so a dangling symlink is something.
    6. No registered worktree with this path or its sanitised form (``collision``),
       the path compared as git lists it (``_as_listed``). A worktree whose directory
       was deleted is still registered, and git refuses its path while the disk says
       it is free.

    A pass is a statement about the moment it was taken. ``create`` runs this itself;
    a caller that pre-flights early to show a refusal sooner still has to read
    ``create``'s outcome.
    """
    run_in, branch = placement.run_in, placement.branch

    named = _git(run_in, "check-ref-format", "--branch", branch)
    if named.returncode is None:
        return GitFailed("check-ref-format", None, _words(named))
    if named.returncode == _DIED:
        control = _git(run_in, "check-ref-format", "--branch", _CONTROL_BRANCH)
        if control.returncode != 0:
            return GitFailed("check-ref-format", named.returncode, _words(named))
        return InvalidBranch(branch, would_be=None)
    if named.returncode != 0:
        return GitFailed("check-ref-format", named.returncode, _words(named))
    read_as = named.stdout.removesuffix("\n")
    if read_as != branch:
        return InvalidBranch(branch, would_be=read_as)

    ref = f"refs/heads/{branch}"
    known = _git(run_in, "show-ref", "--verify", "--quiet", ref)
    if known.returncode == 0:
        return BranchExists(branch)
    if known.returncode != 1:
        return GitFailed("show-ref", known.returncode, _words(known))
    symbolic = _git(run_in, "symbolic-ref", "-q", ref)
    if symbolic.returncode == 0:
        return BranchExists(branch)
    if symbolic.returncode != 1:
        return GitFailed("symbolic-ref", symbolic.returncode, _words(symbolic))

    resolved = _git(
        run_in, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{base}^{{commit}}"
    )
    if resolved.returncode == 1:
        return BaseNotACommit(base)
    if resolved.returncode != 0:
        return GitFailed("rev-parse", resolved.returncode, _words(resolved))
    commit = resolved.stdout.strip()
    if not _OBJECT_ID.fullmatch(commit):
        return BaseNotACommit(base)

    if os.path.lexists(os.path.join(run_in, placement.path)):
        return PathExists(placement.path)

    entries = list_worktrees(run_in)
    if isinstance(entries, GitFailed):
        return entries
    listed = replace(placement, path=_as_listed(run_in, placement.path))
    taken = collision(listed, entries)
    if isinstance(taken, PathRegistered):
        # Step 5 found nothing at the path, which the listing cannot say of a locked
        # entry.
        return PathRegistered(placement.path, missing=True)
    if taken is not None:
        return replace(taken, path=placement.path)
    return Cleared(commit)


@dataclass(frozen=True, slots=True)
class Created:
    """
    The worktree exists at ``path`` with the new ``branch`` checked out at ``commit``.
    """

    path: str
    branch: str
    commit: str


@dataclass(frozen=True, slots=True)
class AddFailed:
    """
    ``worktree add`` failed and nothing of this call remains: no worktree is
    registered at ``path``, and the branch this call created has been removed again.
    A retry meets the same cause, not a leftover.
    """

    path: str
    branch: str
    returncode: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class AddFailedWorktreeLeft:
    """
    ``worktree add`` exited non-zero **and the worktree is registered at** ``path``
    with this call's branch checked out in it. Nothing was removed.

    The measured way to get here is a ``post-checkout`` hook exiting non-zero: the
    checkout is complete and the hook's status is the command's. Whether the tree is
    usable is not something this module can say, and it is not this module's to
    remove.
    """

    path: str
    branch: str
    commit: str
    returncode: int | None
    detail: str


@dataclass(frozen=True, slots=True)
class AddFailedPathHeld:
    """
    ``worktree add`` failed and a worktree is registered at ``path`` **that is not
    this call's**: it has ``held_by`` checked out -- a full ref, or None for a detached
    ``HEAD`` -- and not ``branch``. That tree is somebody else's and was not touched.

    ``kept`` says what became of the branch this call created: None when it was
    removed again, and otherwise why it is still there, in ``AddFailedBranchLeft``'s
    terms.

    Two branches with one slug arriving together get here: both pass the pre-flight,
    one add wins the directory, and the other finds it taken.
    """

    path: str
    branch: str
    held_by: str | None
    returncode: int | None
    detail: str
    kept: str | None


@dataclass(frozen=True, slots=True)
class AddFailedBranchLeft:
    """
    ``worktree add`` failed, no worktree is known to be at ``path``, and the branch
    this call created was not seen to go. ``detail`` is the add's failure and ``kept``
    is why: the registration could not be read, another worktree has it checked out,
    the name has become a symbolic ref, git refused the deletion because the ref had
    moved, or the deletion was stopped at its limit. In all but the last the branch is
    still there and a retry under the same name will meet ``BranchExists``; a stopped
    deletion may or may not have removed it, and ``kept`` says which case this is.
    """

    path: str
    branch: str
    returncode: int | None
    detail: str
    kept: str


@dataclass(frozen=True, slots=True)
class AddTimedOut:
    """
    ``worktree add`` was still running at ``seconds`` and was stopped. Nothing was
    cleaned up, on purpose.

    Stopping git does not stop the checkout it started: the record measured files
    still arriving a second after a ``kill -9``, with the entry left ``locked
    initializing`` -- which is also what a healthy add in flight looks like. So the
    branch and whatever is at ``path`` are in an unknown state, and both are left for
    the operator.

    ``seconds`` is the limit, and the call returns at it (measured: 2.03 s for a limit
    of 2). It is git's own exit that is timed, so a hook that leaves a process running
    and returns does not get here; a hook that itself runs past the limit does.
    """

    path: str
    branch: str
    seconds: float


@dataclass(frozen=True, slots=True)
class BranchTimedOut:
    """
    ``git branch`` was still running at ``seconds`` and was stopped, before any
    worktree was asked for. **The branch may be there**: git writes the ref and then
    runs the ``reference-transaction`` hook's ``committed`` phase, so a hook that
    blocks is stopped with the ref already on disk (measured, 2.39.5). Nothing was
    deleted, because a ref this call cannot show it created is not this call's to
    remove. A retry under the same name meets ``BranchExists`` when it is there.
    """

    branch: str
    seconds: float


CreateOutcome = (
    Created
    | Refusal
    | GitFailed
    | BranchTimedOut
    | AddFailed
    | AddFailedWorktreeLeft
    | AddFailedPathHeld
    | AddFailedBranchLeft
    | AddTimedOut
)


def create(placement: Placement, base: str, *, timeout: float = _ADD_TIMEOUT) -> CreateOutcome:
    """
    Make a linked worktree at ``placement.path`` on a new branch at ``base``.
    **Blocking**, for as long as a checkout and the operator's ``post-checkout`` hook
    take.

    ``base`` has no default. It is resolved to a commit once, by ``preflight``, and
    the branch is created at that object id: what was checked is what is used, and a
    remote-tracking base sets up no upstream -- which would be a write to the one
    ``config`` file every worktree shares, and the record measured those colliding.

    The sequence is ``preflight``, ``git branch --no-track <branch> <commit>``,
    ``git worktree add -- <path> <branch>``. The module docstring argues the two-step
    form. ``--`` in the add because the path is whatever the placement holds, and
    without it a path of ``--detach`` is a request for a detached worktree somewhere
    else. ``git branch`` needs none: the pre-flight has refused a name that starts
    with a dash, and the commit is an object id. After a failed add:

    - the worktree is registered with this branch in it → ``AddFailedWorktreeLeft``,
      nothing removed;
    - it is not, and no worktree has the branch checked out → ``update-ref -d`` the
      branch this call made, provided it is still a plain ref at ``commit``
      (``_drop_branch``), and ``AddFailed``;
    - the registration cannot be read, the branch is checked out somewhere, it has
      become a symbolic ref, or the deletion is refused or stopped at its limit →
      ``AddFailedBranchLeft``;
    - somebody else's worktree is registered at the path → ``AddFailedPathHeld``,
      after the same attempt on this call's branch, which it reports;
    - the add ran out of time → ``AddTimedOut``, nothing touched.

    A branch that existed before the call is never reached by any of this:
    ``preflight`` answers ``BranchExists``, and one that appears between the
    pre-flight and ``git branch`` makes ``git branch`` fail, which is ``GitFailed``
    with no deletion. ``git branch`` stopped at ``_REF_WRITE_TIMEOUT`` is
    ``BranchTimedOut``, also with no deletion: whether it made the ref is not known.
    Parent directories git makes on the way to a failed add are left.
    """
    cleared = preflight(placement, base)
    if not isinstance(cleared, Cleared):
        return cleared
    run_in, branch, path = placement.run_in, placement.branch, placement.path

    limit = _REF_WRITE_TIMEOUT
    made = _git(run_in, "branch", "--no-track", branch, cleared.commit, timeout=limit)
    if made.timed_out:
        return BranchTimedOut(branch, limit)
    if made.returncode != 0:
        return GitFailed("branch", made.returncode, _words(made))

    added = _git(run_in, "worktree", "add", "--", path, branch, timeout=timeout)
    if added.returncode == 0:
        return Created(path, branch, cleared.commit)
    if added.timed_out:
        return AddTimedOut(path, branch, timeout)
    detail = _words(added)

    entries = list_worktrees(run_in)
    if isinstance(entries, GitFailed):
        return AddFailedBranchLeft(
            path,
            branch,
            added.returncode,
            detail,
            kept=f"whether a worktree was registered could not be read: {entries.detail}",
        )
    ref = f"refs/heads/{branch}"
    listed = _as_listed(run_in, path)
    at_path = next((entry for entry in entries if entry.path == listed), None)
    if at_path is not None and at_path.branch == ref:
        return AddFailedWorktreeLeft(path, branch, cleared.commit, added.returncode, detail)

    kept = _drop_branch(run_in, ref, cleared.commit, entries)
    if at_path is not None:
        return AddFailedPathHeld(path, branch, at_path.branch, added.returncode, detail, kept)
    if kept is not None:
        return AddFailedBranchLeft(path, branch, added.returncode, detail, kept)
    return AddFailed(path, branch, added.returncode, detail)


def _drop_branch(
    run_in: str, ref: str, commit: str, entries: tuple[WorktreeEntry, ...]
) -> str | None:
    """
    Remove the branch a ``create`` made and could not use. **Blocking.** None when it
    is gone, and otherwise why it was kept.

    Kept when a worktree in ``entries`` has it checked out, when the name is now a
    symbolic ref, and when git refuses the compare-and-delete. A deletion stopped at
    ``_REF_WRITE_TIMEOUT`` is neither gone nor kept, and the answer says so. The
    module docstring argues ``--no-deref`` and names the window this leaves.
    """
    holder = next((entry.path for entry in entries if entry.branch == ref), None)
    if holder is not None:
        return f"it is checked out at {holder}"
    symbolic = _git(run_in, "symbolic-ref", "-q", ref)
    if symbolic.returncode == 0:
        return f"it is now a symbolic ref to {symbolic.stdout.strip()}"
    if symbolic.returncode != 1:
        return f"whether it is a symbolic ref could not be read: {_words(symbolic)}"
    limit = _REF_WRITE_TIMEOUT
    dropped = _git(run_in, "update-ref", "--no-deref", "-d", ref, commit, timeout=limit)
    if dropped.timed_out:
        return f"the deletion was stopped at {limit:g}s, and it may or may not be gone"
    if dropped.returncode != 0:
        return f"git would not delete it: {_words(dropped)}"
    return None
