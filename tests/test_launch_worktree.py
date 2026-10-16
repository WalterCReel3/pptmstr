"""
A launch with a branch typed runs in a new linked worktree (``planning/2026-10-15-ui-rework.md``
W1-W7).

Against real repositories under pytest's temporary directory, as
``tests/test_worktree.py`` is and for its reason: every claim here is about what git
then has on disk. ``HOME`` is redirected for every test, so a worktree is placed under
the temporary directory and never in the operator's ``~/.pptmstr``.

The property held throughout: an empty branch field is the launch as it was, and a
filled one never starts a session anywhere but the worktree it asked for -- a
failure holds the launch with the draft kept.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import threading
import time
import typing
from collections.abc import Iterator
from pathlib import Path

import pytest

from pptmstr import tree, worktree
from pptmstr.app import AppState, _launch, _reap_launches
from pptmstr.bridge import Bridge
from pptmstr.model import AgentRecord, AgentState, LaunchSpec, NewWorktree
from pptmstr.model_catalog import DEFAULT_MODEL
from pptmstr.pool import SessionPool
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.ui import launcher
from pptmstr.ui.launcher import CheckoutRead, LauncherState, WorktreeHeld
from tests.test_launcher import _notices, _row, _run_draw
from tests.test_worktree import commit, git

TIMEOUT = 10.0


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """
    No operator git configuration, no inherited ``GIT_*``, and a home under the
    temporary directory that does not exist yet -- so W4's "missing parents are made"
    is exercised by every test that makes a worktree.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    for name in list(os.environ):
        if name.startswith("GIT_") and name not in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM"):
            monkeypatch.delenv(name)
    home = tmp_path.resolve() / "home"
    monkeypatch.setenv("HOME", str(home))
    # This process's record of unfinished adds, one per test: a restart, as far as
    # the record is concerned.
    monkeypatch.setattr(launcher, "UNFINISHED", launcher.UnfinishedWorktrees())
    return home


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path.resolve() / "src" / "repo"
    root.mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    commit(root, "tracked.txt")
    return root


def _home() -> Path:
    return Path(os.environ["HOME"])


def _read(state: LauncherState, reader: launcher.CheckoutReader = launcher.read_checkout) -> None:
    """Let the read of the cwd field land, as the draw's worker and poll would."""
    worker = state.checkout.want(state.cwd.strip() or ".", reader=reader)
    if worker is not None:
        worker.join(timeout=TIMEOUT)
    state.checkout.poll()


def _drafted(cwd: Path, branch: str, task: str = "build the thing") -> LauncherState:
    state = LauncherState(task=task, cwd=str(cwd), branch=branch)
    _read(state)
    return state


def _head(directory: Path) -> str:
    return git(directory, "rev-parse", "HEAD").stdout.strip()


def _refs(directory: Path) -> str:
    return git(directory, "for-each-ref", "--format=%(refname) %(objectname)").stdout


def _listing(directory: Path) -> str:
    return git(directory, "worktree", "list", "--porcelain").stdout


def _refused(plan: NewWorktree) -> str | None:
    """``make_worktree``'s refusal in words, having asserted it is a refusal and not a hold."""
    said = launcher.make_worktree(plan)
    assert not isinstance(said, launcher.UncommittedFound), said
    return None if said is None else said.reason


# ---------------------------------------------------------------------------
# An empty branch field is today's launch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("branch", ["", "   ", "\t"])
def test_an_empty_branch_field_builds_the_spec_it_built_before_the_field(
    repo: Path, branch: str
) -> None:
    """
    Pinned field by field against the constructor call ``spec`` made before the field
    existed, so a default that moved would show here.
    """
    state = LauncherState(task="  do it  ", cwd=str(repo), branch=branch)

    spec = state.spec()

    resolved = os.path.realpath(str(repo))
    assert spec == LaunchSpec(
        task="do it",
        model=DEFAULT_MODEL,
        cwd=resolved,
        session_base=tree.session_base(resolved),
        template="solo",
        brief=None,
        containment=None,
        subagent_cap=None,
    )
    assert spec.worktree is None
    assert state.worktree_plan() is None
    assert state.launch_hold() is None
    assert state.press() == spec


def test_an_empty_branch_field_reads_no_checkout_when_drawn(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(launcher.worktree_mod, "detect", lambda d, **k: calls.append(d))
    state = LauncherState(task="x", cwd=str(repo))

    _run_draw(monkeypatch, state)
    time.sleep(0.05)

    assert calls == []
    assert state.checkout.asked is None and state.checkout.read is None


def test_a_draft_defaults_to_no_branch_and_a_launch_clears_one() -> None:
    state = LauncherState(task="x", branch="feat")
    assert launcher.left_by_launch(state.draft()).branch == ""
    state.launched()
    assert state.branch == ""


# ---------------------------------------------------------------------------
# From a main checkout
# ---------------------------------------------------------------------------


def test_from_a_main_checkout_the_spec_runs_in_the_placed_worktree(repo: Path) -> None:
    state = _drafted(repo, "feat/x")

    spec = state.spec()

    path = str(_home() / ".pptmstr" / "worktrees" / "repo" / "feat-x")
    assert spec.cwd == path
    assert spec.session_base == tree.session_base(path)
    assert spec.worktree == NewWorktree(
        run_in=str(repo), main_root=str(repo), branch="feat/x", path=path, base=_head(repo)
    )
    assert not os.path.exists(path), "nothing is made on the draw thread"
    assert not _home().exists()


def test_making_the_worktree_creates_the_branch_at_head_and_the_missing_parents(
    repo: Path,
) -> None:
    state = _drafted(repo, "feat/x")
    spec = state.spec()
    assert spec.worktree is not None
    assert not _home().exists()

    assert launcher.make_worktree(spec.worktree) is None

    made = Path(spec.cwd)
    assert made.is_dir()
    assert git(made, "symbolic-ref", "HEAD").stdout.strip() == "refs/heads/feat/x"
    assert _head(made) == _head(repo)
    assert worktree.detect(spec.cwd) == worktree.LinkedWorktree(spec.cwd, str(repo))


def test_the_modal_says_where_and_from_which_commit_before_the_launch(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    state = _drafted(repo, "feat-x")
    plan = state.worktree_plan()
    assert isinstance(plan, NewWorktree)
    (where,) = launcher.worktree_notices(plan, state.checkout.answer_for(str(repo)))
    assert where.line == f"runs in a new worktree at {plan.path}, from {_head(repo)[:12]}"

    fake, _ = _run_draw(monkeypatch, state)

    # The harness stands in for ``widgets``, so the path is the fake's stand-in.
    lines = [str(said.line) for said in _notices(fake)]
    assert sum(line.startswith("runs in a new worktree at") for line in lines) == 1, lines


def test_a_subdirectory_launches_at_the_new_worktrees_root_and_the_modal_says_so(
    repo: Path,
) -> None:
    (repo / "sub" / "deeper").mkdir(parents=True)
    state = _drafted(repo / "sub" / "deeper", "feat")
    spec = state.spec()

    assert spec.cwd == str(_home() / ".pptmstr" / "worktrees" / "repo" / "feat")
    plan = state.worktree_plan()
    assert isinstance(plan, NewWorktree)
    (where,) = launcher.worktree_notices(plan, state.checkout.answer_for(state.cwd))
    assert where.line.endswith(f"-- at its root, not at {os.path.join('sub', 'deeper')} below it")


# ---------------------------------------------------------------------------
# From a linked worktree (W7)
# ---------------------------------------------------------------------------


def test_from_a_linked_worktree_it_is_based_there_and_placed_under_the_main_name(
    repo: Path, tmp_path: Path
) -> None:
    linked = tmp_path.resolve() / "elsewhere" / "linked"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    side_head = commit(linked, "side.txt")
    assert side_head != _head(repo)

    state = _drafted(linked, "feat-y")
    spec = state.spec()

    path = str(_home() / ".pptmstr" / "worktrees" / "repo" / "feat-y")
    assert spec.cwd == path
    assert spec.worktree is not None
    assert spec.worktree.run_in == str(linked)
    assert spec.worktree.main_root == str(repo)

    assert launcher.make_worktree(spec.worktree) is None
    assert _head(Path(path)) == side_head


@pytest.mark.parametrize("kind", ["main", "linked"])
def test_the_read_before_the_press_asks_no_status_of_any_checkout(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path, kind: str
) -> None:
    """
    ``status`` runs programs the repository's ``config`` names, and before the press
    that repository may be a session's -- a linked worktree aimed at a decoy, or a
    main checkout it ``git init``-ed (``tests/test_worktree.py``). So it is not asked.

    Watched at the module's one child runner, so every git the read starts is seen
    whatever asks for it, and at git itself (``GIT_TRACE2_EVENT``), so a program git
    starts -- a filter, a hook, a submodule's own git -- is seen too.
    """
    import json

    if kind == "linked":
        directory = tmp_path.resolve() / "elsewhere" / "linked"
        git(repo, "worktree", "add", "-q", "-b", "side", str(directory))
    else:
        directory = repo
    (directory / "new.txt").write_text("n\n")
    ran: list[tuple[str, ...]] = []
    real_git, real_env = worktree._git, worktree._child_env
    trace = tmp_path / "trace2.json"

    def recorded(where: str, *args: str, **kw: typing.Any) -> typing.Any:
        ran.append(args)
        return real_git(where, *args, **kw)

    monkeypatch.setattr(worktree, "_git", recorded)
    monkeypatch.setattr(
        worktree, "_child_env", lambda: {**real_env(), "GIT_TRACE2_EVENT": str(trace)}
    )

    state = _drafted(directory, "feat-y")
    plan = state.worktree_plan()
    assert isinstance(plan, NewWorktree)
    said = launcher.worktree_notices(plan, state.checkout.answer_for(str(directory)))

    assert ran and not [args for args in ran if "status" in args], ran
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [e for e in events if e["event"] == "start"], "the trace saw the read's git"
    assert [e.get("argv") for e in events if e["event"] == "child_start"] == []
    assert not [e for e in events if e["event"] == "start" and "status" in e["argv"]]
    assert [n.line for n in said if n.tone is launcher.Tone.WARN] == []


# ---------------------------------------------------------------------------
# Uncommitted changes (W1): asked after the press, held once
# ---------------------------------------------------------------------------


def _dirty(repo: Path, change: str) -> None:
    if change == "modified":
        (repo / "tracked.txt").write_text("changed\n")
    elif change == "untracked":
        (repo / "new.txt").write_text("new\n")
    else:
        (repo / "staged.txt").write_text("s\n")
        git(repo, "add", "staged.txt")


def test_a_clean_checkout_is_made_with_no_hold(repo: Path) -> None:
    plan = _plan(repo)
    assert launcher.make_worktree(plan) is None
    assert Path(plan.path).is_dir()


@pytest.mark.parametrize("change", ["modified", "untracked", "staged"])
def test_a_checkout_with_changes_holds_the_press_and_makes_nothing(repo: Path, change: str) -> None:
    _dirty(repo, change)
    plan = _plan(repo)

    said = launcher.make_worktree(plan)

    assert isinstance(said, launcher.UncommittedFound)
    words = said.reason
    assert "uncommitted changes" in words and str(repo) in words and "press launch again" in words
    assert not os.path.exists(plan.path) and not _has_branch(repo, "feat")


def test_a_branch_that_appeared_is_refused_before_the_changes_are_asked(repo: Path) -> None:
    """One press per cause: a refusal the pre-flight makes is not preceded by the hold."""
    _dirty(repo, "untracked")
    plan = _plan(repo)
    git(repo, "branch", "feat")

    said = launcher.make_worktree(plan)

    assert isinstance(said, launcher.WorktreeRefused)
    assert "already exists" in said.reason


def test_a_press_that_accepted_the_changes_asks_no_status(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    _dirty(repo, "untracked")
    plan = dataclasses.replace(_plan(repo), uncommitted_accepted=True)

    def refuses(*a: object, **k: object) -> object:
        raise AssertionError("status was asked of a press that accepted the changes")

    monkeypatch.setattr(launcher.worktree_mod, "uncommitted", refuses)
    assert launcher.make_worktree(plan) is None
    assert Path(plan.path).is_dir()


def test_the_dirty_read_does_not_write_the_index(repo: Path) -> None:
    """``status`` without ``--no-optional-locks`` refreshes the index and writes it back."""
    index = repo / ".git" / "index"
    os.utime(index, (1_000_000, 1_000_000))
    (repo / "tracked.txt").touch()
    assert launcher.make_worktree(_plan(repo)) is None, "stat-dirty only: no change"
    assert index.stat().st_mtime == 1_000_000


def test_a_status_that_cannot_be_read_holds_the_press_once_too(
    monkeypatch: pytest.MonkeyPatch, bridge: Bridge, repo: Path
) -> None:
    """
    Held as changes are, and once: through ``go``, the draft comes back accepting
    it, and its next press makes the worktree and starts the session.
    """
    asked: list[str] = []

    def failing(directory: str, **kw: typing.Any) -> worktree.GitFailed:
        asked.append(directory)
        return worktree.GitFailed("status", 128, "fatal: index file corrupt")

    monkeypatch.setattr(launcher.worktree_mod, "uncommitted", failing)
    said = launcher.make_worktree(_plan(repo))
    assert isinstance(said, launcher.UncommittedFound)
    assert "could not be read" in said.reason and "index file corrupt" in said.reason

    state = _app(bridge)
    state.launcher = _drafted(repo, "feat")
    _press(state, state.launcher)
    _settle(state)
    (failure,) = state.launcher.failures
    assert "index file corrupt" in failure.reason
    assert failure.draft is not None and failure.draft.uncommitted_accepted == str(repo)

    _read(state.launcher)
    _press(state, state.launcher)
    _settle(state)
    assert state.launcher.failures == [] and len(asked) == 2, "the second press asks nothing"
    assert state.pool is not None and len(state.pool.sessions) == 1


def test_a_held_draft_comes_back_accepting_the_changes_and_the_next_press_launches(
    bridge: Bridge, repo: Path
) -> None:
    """
    Through ``go``: the hold arrives as ``LaunchHeldOnChanges``, the draft comes back
    with the changes accepted for its directory, the modal says so, and pressing
    it again makes the worktree and starts the session.
    """
    _dirty(repo, "untracked")
    state = _app(bridge)
    state.launcher = _drafted(repo, "feat")
    draft = _press(state, state.launcher)
    _settle(state)
    assert state.pool is not None and not state.pool.sessions, "no session started"
    (failure,) = state.launcher.failures
    assert "uncommitted changes" in failure.reason
    assert failure.draft == dataclasses.replace(draft, uncommitted_accepted=str(repo))
    assert state.launcher.draft() == failure.draft, "loaded back into the fields"

    _read(state.launcher)
    plan = state.launcher.worktree_plan()
    assert isinstance(plan, NewWorktree) and plan.uncommitted_accepted
    said = launcher.worktree_notices(plan, state.launcher.checkout.answer_for(str(repo)))
    assert any(n.tone is launcher.Tone.WARN and "goes ahead without them" in n.line for n in said)

    _press(state, state.launcher)
    _settle(state)
    assert state.launcher.failures == []
    assert state.pool is not None
    (session,) = state.pool.sessions.values()
    assert session.cwd == plan.path and Path(plan.path).is_dir()
    assert state.launcher.uncommitted_accepted is None


def test_an_acceptance_is_for_the_directory_it_was_given_for(repo: Path, tmp_path: Path) -> None:
    other = tmp_path.resolve() / "src" / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit(other, "o.txt")
    state = LauncherState(task="x", cwd=str(other), branch="feat", uncommitted_accepted=str(repo))
    _read(state)
    plan = state.worktree_plan()
    assert isinstance(plan, NewWorktree) and not plan.uncommitted_accepted


def test_a_launch_and_its_draft_clear_the_acceptance() -> None:
    state = LauncherState(task="x", branch="feat", uncommitted_accepted=".")
    assert launcher.left_by_launch(state.draft()).uncommitted_accepted is None
    state.launched()
    assert state.uncommitted_accepted is None


def _held_on_changes(state: LauncherState) -> None:
    """A press of ``state``'s draft held on its checkout's changes, reported as ``go`` does."""
    draft = state.draft()
    state.launched()
    state.report_failure(draft, "it has changes", accept_uncommitted=True)
    assert state.uncommitted_accepted == state.cwd, "loaded back accepting them"


@pytest.mark.parametrize("how", ["closed", "discarded"])
def test_a_closed_modal_or_a_discarded_draft_takes_the_acceptance_with_it(how: str) -> None:
    """
    The acceptance answered the hold that was on screen. Closed without a launch,
    or with that failure discarded, the next press is asked again.
    """
    state = LauncherState(task="x", cwd="/srv", branch="feat")
    _held_on_changes(state)
    if how == "closed":
        state.closed_without_launch()
        (failure,) = state.failures
        assert failure.draft is not None and failure.draft.uncommitted_accepted is None
        assert state.failure_loaded, "the fields still are that draft"
    else:
        state.discard_failed()
        assert state.failures == []
    assert state.uncommitted_accepted is None


def test_a_cancel_closes_the_modal_and_takes_the_acceptance_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The draw's own close, and not only the method: the cancel button."""
    state = LauncherState(task="x", cwd="/srv", branch="feat")
    _held_on_changes(state)
    _run_draw(monkeypatch, state)
    assert state.uncommitted_accepted == "/srv", "drawing it changes nothing"
    _run_draw(monkeypatch, state, cancel=True)
    assert not state.is_open and state.uncommitted_accepted is None


@pytest.mark.parametrize(
    "rebuild",
    [
        lambda r: f"worktree: {r}",
        lambda r: str(r).strip(),
        lambda r: r + " (again)",
        lambda r: "".join(r),
    ],
    ids=["f-string", "strip", "concatenated", "joined"],
)
def test_a_rebuilt_reason_cannot_change_what_the_launcher_is_told(
    rebuild: typing.Callable[[str], str],
) -> None:
    """
    The hold travels as a variant and the reason as words: whatever is done to the
    words on the way, a held launch is accepted and a refused one is not.
    """
    import concurrent.futures

    from pptmstr import app

    for outcome, accepts in (
        (app.LaunchHeldOnChanges(rebuild("/srv has uncommitted changes")), True),
        (app.LaunchRefused(rebuild("/srv has uncommitted changes")), False),
    ):
        future: concurrent.futures.Future[app.LaunchOutcome] = concurrent.futures.Future()
        future.set_result(outcome)
        state = app.AppState(store=Store(), bridge=None, settings=Settings())  # type: ignore[arg-type]
        state.launcher = LauncherState(task="x", cwd="/srv", branch="feat")
        draft = state.launcher.draft()
        state.launches.append(app._InFlight(future, draft, "launch"))
        state.launcher.launched()

        _reap_launches(state)

        (failure,) = state.launcher.failures
        assert failure.draft is not None
        assert (failure.draft.uncommitted_accepted == "/srv") is accepts, outcome


# ---------------------------------------------------------------------------
# Nothing already there is continued in (W3, as revised on wt-fix-2)
# ---------------------------------------------------------------------------


def _plan(repo: Path, branch: str = "feat") -> NewWorktree:
    """The plan a press makes from a read taken now, as the modal would take it."""
    plan = _drafted(repo, branch).spec().worktree
    assert plan is not None
    return plan


def _held(repo: Path, branch: str = "feat") -> str:
    """Why the modal holds a draft read now, having asserted that a press starts nothing."""
    state = _drafted(repo, branch)
    plan = state.worktree_plan()
    assert isinstance(plan, WorktreeHeld), plan
    assert state.launch_hold() is not None and state.press() is None
    return plan.reason


def _continue_there(path: str) -> str:
    return (
        "it may be incomplete and was not made by this launch; to continue there, set "
        f"the directory to {path} and leave the branch empty"
    )


# How a hold that offers nothing ends.
_NOT_OFFERED = " -- nothing is offered there"


def _lock(repo: Path, path: str, reason: str) -> None:
    """
    What ``worktree add`` leaves while it checks out, and after it is killed there:
    the entry locked ``initializing`` with ``HEAD`` already on the branch.
    """
    (repo / ".git" / "worktrees" / os.path.basename(path) / "locked").write_text(reason)


def _post_checkout(repo: Path, body: str) -> None:
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text(f"#!/bin/sh\n{body}\n")
    hook.chmod(0o755)


@pytest.mark.parametrize(
    ("left", "offered"),
    [
        ("made", True),
        ("made-before-a-restart", True),
        ("locked", False),
        ("locked-no-reason", False),
        ("worktree-left", False),
        ("worktree-left-before-a-restart", True),
    ],
)
def test_a_worktree_at_the_path_on_the_branch_holds_and_offers_only_one_not_known_unfinished(
    monkeypatch: pytest.MonkeyPatch, repo: Path, left: str, offered: bool
) -> None:
    """
    Each way a tree on the branch is left at the path: a finished launch, an add
    still checking out or killed there (the lock, with and without a reason), and a
    ``post-checkout`` that failed. Each holds, and a press from a read taken before
    it was there is refused too. Only a tree git lists unlocked and this process
    has no unfinished add for is offered to continue in, and in words that ask for
    it to be checked; the others name their state and ask for an inspection.

    "Before a restart": the record is this process's, so another one's leftovers
    are an unlocked entry like any other.
    """
    stale = _plan(repo)
    if left.startswith("made"):
        assert launcher.make_worktree(stale) is None
    elif left.startswith("worktree-left"):
        _post_checkout(repo, "exit 3")
        said = _refused(stale)
        assert said is not None and "left registered at" in said
    else:
        git(repo, "worktree", "add", "-q", "-b", "feat", stale.path)
        if left == "locked":
            _lock(repo, stale.path, "initializing")
        else:
            git(repo, "worktree", "lock", stale.path)
    if left.endswith("before-a-restart"):
        monkeypatch.setattr(launcher, "UNFINISHED", launcher.UnfinishedWorktrees())
    refs, listing = _refs(repo), _listing(repo)

    held = _held(repo)

    assert "refs/heads/feat" in held and stale.path in held
    assert held.endswith(_continue_there(stale.path)) is offered, held
    assert ("to continue there" in held) is offered
    if not offered:
        assert held.endswith(_NOT_OFFERED)
    if left.startswith("locked"):
        assert ", locked" in held
    if left == "locked":
        assert "'initializing'" in held
    if left == "worktree-left":
        assert launcher.UNFINISHED_LEFT in held
    said = _refused(stale)
    assert said is not None and stale.path in said
    assert ("to continue there" in said) is offered
    assert (_refs(repo), _listing(repo)) == (refs, listing)


def test_an_add_stopped_in_post_checkout_is_held_while_its_hook_still_writes(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    """
    reviewer-4's case on rv-wt-fix. git drops the lock before ``post-checkout``, and
    stopping git does not stop the hook, so the add's tree is registered, unlocked
    and on the branch while the hook writes into it. Against the code before
    wt-fix-2 the re-read planned reuse and the press returned None -- a session
    started -- with ``hook-done`` absent (run, 2.39.5); against wt-fix-2's, the hold
    offered the operator the empty-branch launch into it (reviewer-6). This process
    remembers its own stopped add, and offers nothing. ``create``'s timeout default
    is bound when it is defined, so the limit is passed through a wrapper.
    """
    _post_checkout(repo, 'sleep 5; touch "$PWD/hook-done"')
    real = worktree.create
    monkeypatch.setattr(launcher.worktree_mod, "create", lambda p, b: real(p, b, timeout=1.0))
    stale = _plan(repo)
    done = Path(stale.path) / "hook-done"
    try:
        said = _refused(stale)
        assert said is not None and "was stopped after 1s" in said
        assert "locked" not in _listing(repo), "the precondition: git marks nothing unfinished"

        held = _held(repo)
        again = _refused(stale)

        assert not done.exists(), "the hook was still writing throughout"
        assert "to continue there" not in held, "no session is invited into it"
        assert launcher.UNFINISHED_STOPPED in held and held.endswith(_NOT_OFFERED)
        assert again is not None and stale.path in again and "to continue there" not in again
    finally:
        deadline = time.monotonic() + TIMEOUT
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(0.05)


def test_a_worktree_on_another_branch_at_the_path_holds_with_how_to_continue_there(
    repo: Path,
) -> None:
    stale = _plan(repo)
    git(repo, "worktree", "add", "-q", "-b", "other", stale.path)

    held = _held(repo)

    assert "refs/heads/other" in held and held.endswith(_continue_there(stale.path))
    said = _refused(stale)
    assert said is not None and stale.path in said
    assert not _has_branch(repo, "feat")
    assert launcher.UNFINISHED.snapshot() == {}, "a refused press leaves no mark"


def test_the_branch_checked_out_elsewhere_holds_naming_where(repo: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path.resolve() / "elsewhere" / "feat"
    stale = _plan(repo)
    git(repo, "worktree", "add", "-q", "-b", "feat", str(elsewhere))

    held = _held(repo)

    assert held.endswith(_continue_there(str(elsewhere)))
    said = _refused(stale)
    assert said is not None and said.endswith(_continue_there(str(elsewhere)))
    assert not os.path.exists(stale.path)


def test_a_registered_worktree_whose_directory_is_gone_holds(repo: Path) -> None:
    """There is nothing to continue in, so the line does not offer it."""
    stale = _plan(repo)
    assert launcher.make_worktree(_plan(repo)) is None
    shutil.rmtree(stale.path)
    assert f"worktree {stale.path}" in _listing(repo)
    git(repo, "branch", "-m", "feat", "moved")

    held = _held(repo)

    assert "directory is gone" in held and "to continue there" not in held
    said = _refused(stale)
    assert said is not None and stale.path in said
    assert not os.path.exists(stale.path)


def test_a_second_press_while_the_first_is_in_post_checkout_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    """
    In the window where git has dropped the lock and the first add's hook still
    runs, a second ``make_worktree`` of the same plan is refused on what git already
    has (the tree is registered), offers nothing -- this process is still making
    it -- and the first still finishes. Once it has, the tree is offered.
    """
    gate = tmp_path / "release"
    entered = tmp_path / "entered"
    _post_checkout(
        repo,
        f'touch "{entered}"; i=0\n'
        f'while [ ! -e "{gate}" ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done',
    )
    plan = _plan(repo)
    first: list[object] = []
    worker = threading.Thread(target=lambda: first.append(launcher.make_worktree(plan)))
    worker.start()
    deadline = time.monotonic() + TIMEOUT
    while not entered.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert entered.exists()

    second = _refused(plan)
    held = _held(repo)

    gate.touch()
    worker.join(TIMEOUT)
    assert second is not None and launcher.UNFINISHED_MAKING in second
    assert "to continue there" not in second and "to continue there" not in held
    assert first == [None]
    assert plan_path_is_one_worktree(repo, plan.path)
    assert _held(repo).endswith(_continue_there(plan.path)), "finished, it is offered"


def plan_path_is_one_worktree(repo: Path, path: str) -> bool:
    return _listing(repo).count(f"worktree {path}\n") == 1


def _has_branch(repo: Path, branch: str) -> bool:
    done = git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    return done.returncode == 0


# ---------------------------------------------------------------------------
# The base is the commit the modal showed
# ---------------------------------------------------------------------------


def test_the_branch_is_made_at_the_commit_read_and_not_at_head_when_made(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    read_at = _head(repo)
    plan = _plan(repo)
    assert plan.base == read_at
    bases: list[str] = []
    real = worktree.create

    def recorded(placement: worktree.Placement, base: str) -> worktree.CreateOutcome:
        bases.append(base)
        # A commit between the HEAD check and the add does not move the base.
        commit(repo, "late.txt")
        return real(placement, base)

    monkeypatch.setattr(launcher.worktree_mod, "create", recorded)

    assert launcher.make_worktree(plan) is None
    assert bases == [read_at]
    assert _head(Path(plan.path)) == read_at != _head(repo)


def test_a_head_that_moved_since_the_read_makes_nothing(repo: Path) -> None:
    plan = _plan(repo)
    moved = commit(repo, "later.txt")

    said = _refused(plan)

    assert said is not None
    assert plan.base[:12] in said and moved[:12] in said and "reads the checkout again" in said
    assert not os.path.exists(plan.path) and not _has_branch(repo, "feat")


def test_a_refused_launch_has_the_modal_read_the_checkout_again(bridge: Bridge, repo: Path) -> None:
    """
    Through ``go``: the stale draft comes back, the read it was planned from is
    dropped, and the next read shows the commit ``HEAD`` names now.
    """
    launcher_state = _drafted(repo, "feat")
    commit(repo, "later.txt")
    state, reason = _refused_through_go(bridge, launcher_state)
    assert "is no longer" in reason
    assert state.launcher.checkout.read is None

    _read(state.launcher)
    plan = state.launcher.worktree_plan()
    assert isinstance(plan, NewWorktree) and plan.base == _head(repo)


def test_a_launch_drops_the_read_it_was_planned_from() -> None:
    state = LauncherState(task="x", cwd="/srv", branch="feat")
    state.checkout.read = CheckoutRead("/srv", worktree.MainCheckout("/srv"))
    state.launched()
    assert state.checkout.read is None


def test_a_link_put_at_the_path_after_the_read_does_not_move_the_sessions_cwd(
    bridge: Bridge, repo: Path, tmp_path: Path
) -> None:
    """
    One resolution: the cwd is the planned path as the read resolved it, not that
    path resolved again at the press, which would follow the link somewhere the
    worktree is not made. The link itself is something at the path, and refused.
    """
    launcher_state = _drafted(repo, "feat")
    plan = launcher_state.worktree_plan()
    assert isinstance(plan, NewWorktree)
    target = tmp_path.resolve() / "target"
    target.mkdir()
    Path(plan.path).parent.mkdir(parents=True)
    Path(plan.path).symlink_to(target)

    spec = launcher_state.spec()
    assert spec.cwd == plan.path

    _, reason = _refused_through_go(bridge, launcher_state)
    assert "already exists" in reason


def test_a_failure_drops_a_read_taken_while_its_launch_was_in_flight() -> None:
    """
    The press dropped the read and the draw asked again while the launch ran, so
    what landed may predate whatever the failed launch left registered.
    """
    state = LauncherState(task="x", cwd="/srv", branch="feat")
    draft = state.draft()
    state.launched()
    state.checkout.read = CheckoutRead("/srv", worktree.MainCheckout("/srv"))
    state.report_failure(draft, "git worktree add failed")
    assert state.checkout.read is None


def test_a_spec_whose_cwd_is_not_its_worktree_is_refused() -> None:
    made = NewWorktree(run_in="/r", main_root="/r", branch="b", path="/h/w/r/b", base="c" * 40)
    LaunchSpec(task="t", model=DEFAULT_MODEL, cwd="/h/w/r/b", worktree=made)
    with pytest.raises(ValueError, match="names /elsewhere"):
        LaunchSpec(task="t", model=DEFAULT_MODEL, cwd="/elsewhere", worktree=made)


def test_a_repository_with_no_commit_holds(tmp_path: Path) -> None:
    empty = tmp_path.resolve() / "empty"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "main")
    hold = _drafted(empty, "feat").launch_hold()
    assert hold is not None and "not a commit" in hold


# ---------------------------------------------------------------------------
# Held in the modal: not a repository, nothing to name, still reading
# ---------------------------------------------------------------------------


def test_a_directory_that_is_not_a_repository_holds_the_launch(tmp_path: Path) -> None:
    plain = tmp_path.resolve() / "plain"
    plain.mkdir()
    state = _drafted(plain, "feat")

    assert state.launch_hold() == launcher._HOLD_NOT_A_REPO
    assert state.press() is None
    with pytest.raises(ValueError):
        state.spec()


def test_a_branch_with_nothing_to_name_a_directory_after_holds(repo: Path) -> None:
    state = _drafted(repo, "///")
    hold = state.launch_hold()
    assert hold is not None and "no letter or digit" in hold
    assert state.press() is None


def test_a_launch_is_held_until_the_read_of_the_field_as_it_stands_lands(
    repo: Path, tmp_path: Path
) -> None:
    state = LauncherState(task="x", cwd=str(repo), branch="feat")
    assert state.launch_hold() == launcher._HOLD_READING
    _read(state)
    assert state.launch_hold() is None

    other = tmp_path.resolve() / "other"
    other.mkdir()
    state.cwd = str(other)
    assert state.launch_hold() == launcher._HOLD_READING, "a read of another field"
    assert state.press() is None


def test_an_open_forgets_the_read() -> None:
    state = LauncherState(task="x", cwd="/srv", branch="feat")
    state.checkout.read = CheckoutRead("/srv", worktree.MainCheckout("/srv"))
    state.begin_open(scanner=lambda cwd: ((), {}))
    assert state.checkout.read is None


def test_a_read_that_lands_after_it_was_forgotten_is_dropped() -> None:
    reading = launcher.CheckoutReading()
    gate = threading.Event()

    def slow(directory: str) -> CheckoutRead:
        gate.wait(TIMEOUT)
        return CheckoutRead(directory, worktree.MainCheckout("/old"))

    worker = reading.want("/srv", reader=slow)
    assert worker is not None
    reading.forget()
    gate.set()
    worker.join(TIMEOUT)
    reading.poll()
    assert reading.read is None


def test_a_reader_that_raises_is_an_answer_and_not_a_hold_for_good() -> None:
    reading = launcher.CheckoutReading()

    def broken(directory: str) -> CheckoutRead:
        raise OSError("boom")

    worker = reading.want("/srv", reader=broken)
    assert worker is not None
    worker.join(TIMEOUT)
    reading.poll()
    assert reading.asked is None
    assert isinstance(reading.read, CheckoutRead)
    assert isinstance(reading.read.checkout, worktree.Unclassified)
    assert isinstance(launcher.plan_worktree(reading.read, "feat"), WorktreeHeld)


# ---------------------------------------------------------------------------
# A resume: the field is disabled and nothing is asked
# ---------------------------------------------------------------------------


def test_with_a_session_picked_the_branch_is_disabled_and_the_resume_runs_where_it_was(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    state = LauncherState(task="carry on", cwd=str(repo), branch="feat")
    state.picker.rows = (_row("s1", cwd=str(repo)),)
    state.picker.pick(state.picker.rows[0], str(repo))

    assert not state.wants_worktree
    assert state.worktree_plan() is None
    spec = state.press()
    assert spec is not None and spec.resume == "s1"
    assert spec.cwd == str(repo) and spec.worktree is None

    calls: list[str] = []
    monkeypatch.setattr(launcher.worktree_mod, "detect", lambda d, **k: calls.append(d))
    fake, _ = _run_draw(monkeypatch, state)
    names = [c[0] for c in fake.method_calls]
    at = next(
        i
        for i, c in enumerate(fake.method_calls)
        if c[0] == "input_text_with_hint" and c.args[0] == "##branch"
    )
    assert "begin_disabled" in names[:at] and names[at + 1] == "end_disabled"
    time.sleep(0.05)
    assert calls == []


def test_a_fork_or_relaunch_of_a_worktree_session_makes_no_worktree() -> None:
    record = AgentRecord(
        node_id=("s1", None),
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task="the task",
        model=DEFAULT_MODEL,
        cwd="/home/x/.pptmstr/worktrees/repo/feat",
    )
    spec = LaunchSpec.from_record(record)
    assert spec.worktree is None
    assert spec.cwd == "/home/x/.pptmstr/worktrees/repo/feat"


# ---------------------------------------------------------------------------
# The draw does not run git
# ---------------------------------------------------------------------------


def test_the_draw_reads_the_checkout_on_a_worker_and_never_on_its_own_thread(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    threads: dict[str, list[threading.Thread]] = {}

    def watched(name: str) -> None:
        real = getattr(worktree, name)

        def recorded(directory: str, **kw: typing.Any) -> object:
            threads.setdefault(name, []).append(threading.current_thread())
            return real(directory, **kw)

        monkeypatch.setattr(launcher.worktree_mod, name, recorded)

    for name in ("detect", "uncommitted", "head_commit", "list_worktrees"):
        watched(name)
    state = LauncherState(task="x", cwd=str(repo), branch="feat")

    _run_draw(monkeypatch, state)
    deadline = time.monotonic() + TIMEOUT
    while not threads and time.monotonic() < deadline:
        time.sleep(0.01)
    while state.checkout.asked is not None and time.monotonic() < deadline:
        time.sleep(0.01)
        state.checkout.poll()

    assert set(threads) == {"detect", "head_commit", "list_worktrees"}, "no status before a press"
    assert all(t is not threading.main_thread() for ts in threads.values() for t in ts)
    assert isinstance(state.worktree_plan(), NewWorktree)


# ---------------------------------------------------------------------------
# Through app._launch: go() makes it, or the launcher gets the draft back
# ---------------------------------------------------------------------------


@pytest.fixture()
def bridge() -> Iterator[Bridge]:
    b = Bridge()
    b.start()
    try:
        yield b
    finally:
        b.stop()


def _app(bridge: Bridge) -> AppState:
    """``cap=0``: the pool announces a session and starts no CLI."""
    state = AppState(store=Store(), bridge=bridge, settings=Settings())
    state.pool = SessionPool(bridge, cap=0)
    return state


def _settle(state: AppState) -> None:
    deadline = time.monotonic() + TIMEOUT
    while not all(e.future.done() for e in state.launches) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert all(e.future.done() for e in state.launches)
    _reap_launches(state)


def _press(state: AppState, launcher_state: LauncherState) -> launcher.Draft:
    spec = launcher_state.press()
    assert spec is not None
    draft = launcher_state.draft()
    _launch(state, spec, draft)
    launcher_state.launched()
    return draft


def test_go_makes_the_worktree_off_both_hot_threads_before_the_session(
    monkeypatch: pytest.MonkeyPatch, bridge: Bridge, repo: Path
) -> None:
    state = _app(bridge)
    calls: list[tuple[NewWorktree, threading.Thread, int]] = []
    real = launcher.make_worktree

    def recorded(
        plan: NewWorktree,
    ) -> launcher.WorktreeRefused | launcher.UncommittedFound | None:
        assert state.pool is not None
        calls.append((plan, threading.current_thread(), len(state.pool.sessions)))
        return real(plan)

    monkeypatch.setattr(launcher, "make_worktree", recorded)
    state.launcher = _drafted(repo, "feat")
    _press(state, state.launcher)
    _settle(state)

    assert state.launcher.failures == []
    plan, thread, sessions_then = calls[0]
    assert len(calls) == 1 and sessions_then == 0
    assert thread is not threading.main_thread()
    assert thread is not bridge._thread  # type: ignore[attr-defined]
    assert state.pool is not None
    (session,) = state.pool.sessions.values()
    assert session.cwd == plan.path and Path(plan.path).is_dir()
    assert session.session_base == tree.session_base(plan.path)


def _refused_through_go(bridge: Bridge, launcher_state: LauncherState) -> tuple[AppState, str]:
    state = _app(bridge)
    state.launcher = launcher_state
    draft = _press(state, launcher_state)
    _settle(state)
    assert state.pool is not None and not state.pool.sessions, "no session started"
    (failure,) = state.launcher.failures
    assert failure.draft == draft
    assert state.launcher.branch == draft.branch and state.launcher.task == draft.task
    return state, failure.reason


def test_an_existing_branch_is_refused_with_the_draft_kept(bridge: Bridge, repo: Path) -> None:
    git(repo, "branch", "feat")
    _, reason = _refused_through_go(bridge, _drafted(repo, "feat"))
    assert "already exists" in reason


def test_something_already_at_the_path_is_refused_with_the_draft_kept(
    bridge: Bridge, repo: Path
) -> None:
    launcher_state = _drafted(repo, "feat")
    plan = launcher_state.worktree_plan()
    assert isinstance(plan, NewWorktree)
    Path(plan.path).mkdir(parents=True)
    _, reason = _refused_through_go(bridge, launcher_state)
    assert "git does not list it as a worktree" in reason
    assert "to continue there" not in reason and reason.endswith(_NOT_OFFERED)
    assert not _has_branch(repo, "feat")


def test_a_name_git_refuses_is_refused_with_the_draft_kept(bridge: Bridge, repo: Path) -> None:
    _, reason = _refused_through_go(bridge, _drafted(repo, "bad..name"))
    assert "will not take" in reason


def test_a_failed_post_checkout_hook_says_the_worktree_was_left(bridge: Bridge, repo: Path) -> None:
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.write_text("#!/bin/sh\nexit 3\n")
    hook.chmod(0o755)
    launcher_state = _drafted(repo, "feat")
    plan = launcher_state.worktree_plan()
    assert isinstance(plan, NewWorktree)

    _, reason = _refused_through_go(bridge, launcher_state)

    assert "left registered at" in reason and plan.path in reason
    assert Path(plan.path).is_dir()


def test_a_refusal_never_falls_back_to_the_checkout_or_the_strict_launch(
    monkeypatch: pytest.MonkeyPatch, bridge: Bridge, repo: Path
) -> None:
    monkeypatch.setattr(
        launcher, "make_worktree", lambda plan: launcher.WorktreeRefused("git said no")
    )
    state, reason = _refused_through_go(bridge, _drafted(repo, "feat"))
    assert reason == "git said no"


def test_under_fake_nothing_is_made(bridge: Bridge, repo: Path) -> None:
    launcher_state = _drafted(repo, "feat")
    state = _app(bridge)
    state.fake = True
    state.launcher = launcher_state
    _press(state, launcher_state)
    _settle(state)
    (failure,) = state.launcher.failures
    assert "--fake" in failure.reason
    assert not _home().exists()


# ---------------------------------------------------------------------------
# Every outcome of create is put in words (W5)
# ---------------------------------------------------------------------------

_P = "/h/.pptmstr/worktrees/r/feat"

_OUTCOMES: list[worktree.CreateOutcome] = [
    worktree.Created(_P, "feat", "c" * 40),
    worktree.InvalidBranch("feat", None),
    worktree.InvalidBranch("feat", "main"),
    worktree.BranchExists("feat"),
    worktree.BaseNotACommit("HEAD"),
    worktree.PathExists(_P),
    worktree.PathRegistered(_P, missing=True),
    worktree.SlugTaken(_P, "/other/feat_"),
    worktree.SlugUnjudged(_P, "/other/long"),
    worktree.GitFailed("rev-parse", 129, "usage"),
    worktree.BranchTimedOut("feat", 10.0),
    worktree.AddFailed(_P, "feat", 128, "could not create"),
    worktree.AddFailedWorktreeLeft(_P, "feat", "c" * 40, 3, "hook said 3"),
    worktree.AddFailedPathHeld(_P, "feat", "refs/heads/x", 128, "taken", kept=None),
    worktree.AddFailedPathHeld(_P, "feat", None, 128, "taken", kept="it moved"),
    worktree.AddFailedBranchLeft(_P, "feat", 128, "failed", kept="it is checked out at /y"),
    worktree.AddTimedOut(_P, "feat", 120.0),
]


def test_every_outcome_create_returns_is_listed_here() -> None:
    assert {type(o) for o in _OUTCOMES} == set(typing.get_args(worktree.CreateOutcome))


@pytest.mark.parametrize("outcome", _OUTCOMES, ids=lambda o: type(o).__name__)
def test_every_failure_is_a_reason_and_only_created_is_none(
    outcome: worktree.CreateOutcome,
) -> None:
    said = launcher.worktree_refusal(outcome, "feat", _P)
    if isinstance(outcome, worktree.Created):
        assert said is None
        return
    assert said
    for value in dataclasses.astuple(outcome):
        if isinstance(value, str) and value not in ("feat", _P) and value != "c" * 40:
            assert value in said, (value, said)


@pytest.mark.parametrize(
    "outcome, words",
    [
        (_OUTCOMES[12], "left registered at"),
        (_OUTCOMES[13], "was removed again"),
        (_OUTCOMES[14], "was left: it moved"),
        (_OUTCOMES[15], "was left: it is checked out at /y"),
        (_OUTCOMES[16], "were left as they are"),
        (_OUTCOMES[10], "may exist now"),
        (_OUTCOMES[11], "nothing was left on disk"),
    ],
    ids=lambda v: type(v).__name__ if not isinstance(v, str) else "",
)
def test_what_a_failure_left_on_disk_is_said(outcome: worktree.CreateOutcome, words: str) -> None:
    said = launcher.worktree_refusal(outcome, "feat", _P)
    assert said is not None and words in said


# ---------------------------------------------------------------------------
# The rows the branch adds are in the scrolling body, not over the buttons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("height", [900.0, 480.0])
def test_with_a_branch_typed_the_buttons_stay_under_the_body_and_on_the_display(
    monkeypatch: pytest.MonkeyPatch, repo: Path, height: float
) -> None:
    """
    Against Dear ImGui itself: the branch field, the placed path and the dirty
    warning are drawn in the scrolling body, so on a short display the body scrolls
    and the launch button is still on screen and live.
    """
    from pptmstr.approval import Policy
    from tests.test_launcher import A_PREMISE, _modal_rect, _RealFrames, _settled

    (repo / "new.txt").write_text("n\n")
    state = LauncherState(task=A_PREMISE, cwd=str(repo), branch="feat", policy=Policy.AUTONOMOUS)
    _read(state)
    _settled(state)
    assert state.checkout.read is not None
    state.request_open()
    with _RealFrames(monkeypatch, state) as real:
        real.io.display_size = real.imgui.ImVec2(1280.0, height)
        # The open forgets the read, and the draw asks for it again on a worker.
        real.frame()
        assert state.checkout.read is None
        deadline = time.monotonic() + TIMEOUT
        while state.checkout.read is None and time.monotonic() < deadline:
            time.sleep(0.01)
            real.frame()
        real.frame(6)
        _, top, _, tall, modal_scroll = _modal_rect(real)
        body = real.body()

        assert state.launch_hold() is None
        assert top >= 0.0 and top + tall <= height
        assert modal_scroll == 0.0
        for label in ("launch", "cancel"):
            low, high, window = real.placed[label]
            assert window == launcher.TITLE
            assert body.pos.y + body.size.y <= low  # type: ignore[attr-defined]
            assert 0.0 <= low and high <= height
        if height < 900.0:
            # The fixture has to be the hazard: there is more than fits.
            assert body.scroll_max.y > 0.0  # type: ignore[attr-defined]


def test_a_path_this_process_left_unfinished_is_not_offered_even_when_git_lists_nothing_there(
    repo: Path,
) -> None:
    """
    The record is consulted at the press before the pre-flight: a directory at a
    path this window's add has not finished with, which git does not list (its
    registration went, or was never made), is not offered as somewhere to continue,
    as the pre-flight's "already exists" line would offer it.
    """
    plan = _plan(repo)
    Path(plan.path).mkdir(parents=True)
    mark, _ = launcher.UNFINISHED.begin(plan.path)
    assert mark is not None

    said = _refused(plan)

    assert said is not None and launcher.UNFINISHED_MAKING in said
    assert "to continue there" not in said and said.endswith(_NOT_OFFERED)
    assert launcher.UNFINISHED.snapshot() == {plan.path: launcher.UNFINISHED_MAKING}
    assert not _has_branch(repo, "feat")


def test_a_press_that_fails_while_another_holds_the_path_leaves_the_others_mark(
    monkeypatch: pytest.MonkeyPatch, repo: Path, tmp_path: Path
) -> None:
    """
    reviewer-8's race on rv-wt-fix-3. Press B of a branch is slow in its status
    read; press A of the same branch comes in that window. Run against wt-fix-3's
    ``make_worktree``, A passed the record (B had not marked the path yet), and
    whichever create lost cleared the winner's in-flight mark, so the modal offered
    the tree while B's ``post-checkout`` still wrote into it. Now A is refused at
    once, making nothing, and B's mark stands until B is done.
    """
    gate = tmp_path / "release"
    entered = tmp_path / "entered"
    _post_checkout(
        repo,
        f'touch "{entered}"; i=0\n'
        f'while [ ! -e "{gate}" ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done',
    )
    plan = _plan(repo)
    b_in_status = threading.Event()
    a_done = threading.Event()
    real_uncommitted = worktree.uncommitted

    def slow_for_b(run_in: str) -> bool | worktree.GitFailed:
        if threading.current_thread().name == "press-b":
            b_in_status.set()
            assert a_done.wait(TIMEOUT)
        return real_uncommitted(run_in)

    monkeypatch.setattr(launcher.worktree_mod, "uncommitted", slow_for_b)
    said: list[object] = []
    press_b = threading.Thread(
        target=lambda: said.append(launcher.make_worktree(plan)), name="press-b"
    )
    try:
        press_b.start()
        assert b_in_status.wait(TIMEOUT)

        refused_a = _refused(plan)
        a_done.set()
        deadline = time.monotonic() + TIMEOUT
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert entered.exists(), "B went on to make the tree"
        held = _held(repo)

        assert refused_a is not None and launcher.UNFINISHED_MAKING in refused_a
        assert press_b.is_alive(), "the precondition: B's hook is still writing"
        assert "to continue there" not in held and launcher.UNFINISHED_MAKING in held
    finally:
        a_done.set()
        gate.touch()
        press_b.join(TIMEOUT)
    assert said == [None]
    assert plan_path_is_one_worktree(repo, plan.path)
    assert _held(repo).endswith(_continue_there(plan.path)), "finished, it is offered"


def _hold_texts() -> list[str]:
    """Every hold and refusal line the launcher builds about a tree, for one path."""
    entry = worktree.WorktreeEntry
    held = [
        launcher._already_there(entry(_P, "c" * 40, "refs/heads/feat"), {}),
        launcher._already_there(entry(_P, "c" * 40, None, detached=True), {}),
        launcher._already_there(entry(_P, "c" * 40, "refs/heads/feat", locked="init"), {}),
        launcher._already_there(entry(_P, "c" * 40, "refs/heads/feat", locked=""), {}),
        launcher._already_there(entry(_P, "c" * 40, "refs/heads/feat", prunable="gone"), {}),
    ]
    states = [
        launcher.UNFINISHED_MAKING,
        launcher.UNFINISHED_STOPPED,
        launcher.UNFINISHED_LEFT,
        launcher.UNFINISHED_BRANCH_STOPPED,
        launcher.UNFINISHED_RAISED,
    ]
    for state in states:
        held.append(launcher._already_there(entry(_P, "c" * 40, "refs/heads/feat"), {_P: state}))
    for outcome in _OUTCOMES:
        said = launcher.worktree_refusal(outcome, "feat", _P)
        if said is not None:
            held.append(said)
    return held


def test_no_hold_names_a_command_to_run_in_the_tree() -> None:
    """
    reviewer-8 on rv-wt-fix-3: a session that wrote the tree can aim its ``.git``
    at a repository it built, and any git run there runs that repository's programs
    as the operator. What a line asks of the operator follows its " -- ", and none
    of it is git; git may be the subject of what a line reports ("git already has a
    worktree ..."), never a thing to run.
    """
    for text in _hold_texts():
        _, _, asked = text.partition(" -- ")
        assert "git " not in asked, text
        assert "git -C" not in text and "status" not in text and "unlock" not in text, text


def test_a_press_refused_by_its_checks_leaves_no_mark(repo: Path) -> None:
    """Nothing was made, so nothing is recorded, and the next press is asked again."""
    plan = _plan(repo)
    _dirty(repo, "untracked")

    said = launcher.make_worktree(plan)

    assert isinstance(said, launcher.UncommittedFound)
    assert launcher.UNFINISHED.snapshot() == {}


def test_an_exception_before_create_leaves_no_mark(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    plan = _plan(repo)

    def boom(run_in: str) -> bool:
        raise RuntimeError("status went away")

    monkeypatch.setattr(launcher.worktree_mod, "uncommitted", boom)
    with pytest.raises(RuntimeError):
        launcher.make_worktree(plan)

    assert launcher.UNFINISHED.snapshot() == {}


def test_an_exception_out_of_create_says_the_state_is_not_known(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    """
    What ``create`` did on disk before it raised is not known, so the path is not
    left "still making it" for good, nor dropped: it says its state is unknown and
    offers nothing.
    """
    plan = _plan(repo)

    def boom(placement: worktree.Placement, base: str) -> worktree.CreateOutcome:
        raise RuntimeError("create went away")

    monkeypatch.setattr(launcher.worktree_mod, "create", boom)
    with pytest.raises(RuntimeError):
        launcher.make_worktree(plan)

    assert launcher.UNFINISHED.snapshot() == {plan.path: launcher.UNFINISHED_RAISED}
    said = _refused(plan)
    assert said is not None and launcher.UNFINISHED_RAISED in said
    assert "to continue there" not in said and said.endswith(_NOT_OFFERED)


def test_a_stopped_git_branch_says_no_worktree_was_added(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    """The path it was for does not exist, and the hold says so rather than naming a tree."""
    plan = _plan(repo)
    monkeypatch.setattr(
        launcher.worktree_mod, "create", lambda p, b: worktree.BranchTimedOut(p.branch, 10.0)
    )
    said = _refused(plan)
    assert said is not None and "may exist now" in said

    again = _refused(plan)

    assert not os.path.exists(plan.path)
    assert again is not None and launcher.UNFINISHED_BRANCH_STOPPED in again
    assert "no worktree was added there" in again and again.endswith(_NOT_OFFERED)


def test_only_the_call_that_took_a_path_can_settle_it() -> None:
    record = launcher.UnfinishedWorktrees()
    mark, before = record.begin(_P)
    second, seen = record.begin(_P)
    assert mark is not None and before == {}
    assert second is None and seen == {_P: launcher.UNFINISHED_MAKING}

    record.abandoned(_P, launcher.Mark())
    record.ended(_P, launcher.Mark(), worktree.BranchExists("feat"))
    record.raised(_P, launcher.Mark())
    assert record.snapshot() == {_P: launcher.UNFINISHED_MAKING}

    record.ended(_P, mark, worktree.Created(_P, "feat", "c" * 40))
    assert record.snapshot() == {}
    record.ended(_P, mark, worktree.AddTimedOut(_P, "feat", 1.0))
    assert record.snapshot() == {}, "a settled mark settles nothing again"
