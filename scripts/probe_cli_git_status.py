#!/usr/bin/env python3
"""
Does the Claude CLI run a tree's own git configuration when it is started in that tree?

reviewer-10 (rv-final, finding 1) and reviewer-8 (rv-wt-fix-3, finding 1), from a
claude-code docs issue that cites the 2.1.78 changelog, say the CLI builds a
git-status section of its system prompt by running ``git status`` in its own working
directory, and that the CLI process is not inside pptmstr's Bash-only sandbox. If so,
launching a session in a tree that a prior autonomous session wrote -- the launcher's
"continue there" offer, or resuming a session in its own worktree -- runs that tree's
git configuration as the operator. pptmstr sets neither
``CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS`` nor ``includeGitInstructions`` (grepped by
both reviewers), so nothing suppresses it. Nobody has run the chain end to end; this
measures it.

**The decoy is reviewer-8's recipe.** In a fresh temp tree:

  R         a throwaway repo (one commit).
  P         a linked worktree of R (``git worktree add``), so R still lists P: the
            launcher's listing is "not evidence" (worktree.py docstring), and that is
            the point -- P is session-forgeable while the main repo looks unchanged.
  P/.decoy  a second, independent git directory. P/.git is overwritten to read
            ``gitdir: .../.decoy/.git``, so every git command run with cwd P now uses
            the decoy's config and treats P as its working tree.

Inside the decoy a tracked file is routed through ``filter.x.clean`` and the file is
made stat-dirty (its mtime no longer matches the index), so ``git status`` must re-read
it and run the clean filter to compare -- and ``core.fsmonitor`` names a hook program,
which ``git status`` also invokes. Both the filter and the fsmonitor program do one
thing: ``touch`` a marker under the temp tree. If a marker exists after an arm ran, git
ran the decoy's configured program in that arm. The marker is the result; the model's
narration is never read or printed (STYLE.md 2: a probe captures the result).

**Two trigger channels, two markers.** ``filter.x.clean`` is the channel both reviews
name and the one ``read_checkout`` already refuses to expose. ``core.fsmonitor`` is a
second, independent channel: git runs it as a program on status regardless of the
index's fsmonitor state, so it corroborates the filter rather than standing in for it.
Each is reported separately.

**Four arms, fresh fixture each.** A marker from one arm must never be read as
another's, so every arm builds its own temp tree.

  a-default          the CLI as pptmstr launches it today.
  b-disable-env      ``CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1`` in the environment.
  c-settings-false   ``{"includeGitInstructions": false}`` through ``--settings``.
  d-control-no-cli   the decoy is installed but ``true`` runs instead of the CLI. This
                     arm MUST leave no marker; a marker here means the fixture fires on
                     its own and arms a-c prove nothing.

**The CLI arms cannot run inside pptmstr's sandbox.** ``/home/wreel/.claude/.credentials.json``
is denied to a sandboxed command, so ``claude -p`` cannot authenticate here. Run with
``--fixture-only`` this script therefore runs only what is decidable without the CLI:
the control arm (d), and a self-check that runs plain ``git status`` in P and confirms
the marker appears -- proof the decoy is live. Run with no flag (the operator, outside
the sandbox) it runs all four arms.

Nothing here proposes a fix (memory: defer the fix until the measurement names it).

Usage:  .venv/bin/python scripts/probe_cli_git_status.py [--fixture-only] [--base DIR] [--keep]

    --fixture-only   build the fixture, run the self-check and the control arm, and
                     STOP before the CLI arms. This is what a sandboxed builder runs.
    --base DIR       create the fresh temp tree under DIR (default: $TMPDIR).
    --keep           leave the fixtures on disk for inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pptmstr.cli_version import read_cli_version, resolve_cli_path  # noqa: E402

# A tracked file the clean filter is routed to. It is committed first and made
# stat-dirty afterwards, so the filter runs on `git status`, not at commit time.
TRACKED = "tracked.txt"

# Fixtures are built with no user or system git config, so a hook or filter in the
# operator's own gitconfig cannot shape a result; the decoy's own config is the only
# source under test. The CLI arm inherits the real environment (minus these), because
# the whole question is what the CLI does with its normal configuration -- but the
# decoy's config is repo-local and overrides any global, so the measurement is honest
# either way.
CLEAN_GIT_ENV = {
    **os.environ,
    "LC_ALL": "C",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(
    cwd: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env if env is not None else CLEAN_GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


def must(cwd: Path, *args: str) -> str:
    done = git(cwd, *args)
    if done.returncode != 0:
        raise RuntimeError(f"fixture: git {' '.join(args)} in {cwd}: {done.stderr.strip()}")
    return done.stdout.strip()


@dataclass
class Fixture:
    """
    One arm's throwaway tree. ``probe`` is the worktree P whose ``.git`` points at the
    decoy; ``marker_filter`` and ``marker_fsmonitor`` are the files the decoy's clean
    filter and fsmonitor program touch when git runs them. ``settings`` is the path to
    a ``--settings`` JSON file, written only for the arm that needs one.
    """

    top: Path
    main: Path
    probe: Path
    decoy_git: Path
    marker_filter: Path
    marker_fsmonitor: Path
    settings: Path


def build_fixture(base: Path, name: str) -> Fixture:
    """
    Build R, the worktree P, and the decoy whose config P/.git points at. Returns a
    fixture whose markers do not yet exist: both configured programs touch them, and
    nothing has run git in P since they were configured.
    """
    top = Path(tempfile.mkdtemp(prefix=f"cli-git-probe-{name}-", dir=base))
    main = top / "repo"
    probe = top / "wt"
    marker_filter = top / "MARKER_FILTER"
    marker_fsmonitor = top / "MARKER_FSMONITOR"

    # R: a real repo with one commit, so a worktree can be added from it.
    main.mkdir()
    must(main, "-c", "init.defaultBranch=main", "init")
    must(main, "config", "user.email", "probe@example.invalid")
    must(main, "config", "user.name", "probe")
    (main / "seed.txt").write_text("seed\n")
    must(main, "add", "seed.txt")
    must(main, "commit", "-m", "seed")

    # P: a linked worktree of R. R's admin directory now lists P (the launcher reads
    # that listing), even though P/.git is about to be re-aimed at the decoy.
    must(main, "worktree", "add", str(probe), "-b", "feat")

    # The decoy: an independent git directory. The fsmonitor hook program and the clean
    # filter each touch their marker; both are invoked by `git status`.
    decoy = probe / ".decoy"
    must(probe, "-c", "init.defaultBranch=main", "init", str(decoy))
    decoy_git = decoy / ".git"

    fsmonitor = top / "fsmonitor.sh"
    fsmonitor.write_text(f'#!/bin/sh\ntouch "{marker_fsmonitor}"\nexit 0\n')
    fsmonitor.chmod(fsmonitor.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Re-aim P/.git at the decoy. From here, git run with cwd P uses the decoy's config
    # and treats P as its working tree.
    (probe / ".git").write_text(f"gitdir: {decoy_git}\n")

    must(probe, "config", "user.email", "probe@example.invalid")
    must(probe, "config", "user.name", "probe")

    # Route the tracked file through filter x, then commit it while filter.x.clean is
    # still undefined (an undefined, non-required filter is a no-op). The filter is
    # defined only afterwards, so it fires on the stat-dirty re-read, not at commit.
    (probe / ".gitattributes").write_text(f"{TRACKED} filter=x\n")
    (probe / TRACKED).write_text("hello\n")
    must(probe, "add", TRACKED, ".gitattributes")
    must(probe, "commit", "-m", "tracked")

    must(probe, "config", "filter.x.clean", f'touch "{marker_filter}"; cat')
    must(probe, "config", "core.fsmonitor", str(fsmonitor))

    # Make the tracked file stat-dirty: its mtime no longer matches the index, so
    # `git status` must re-read it and run the clean filter to decide if it changed.
    os.utime(probe / TRACKED, (2_000_000_000, 2_000_000_000))

    # A marker touched during the build (the fsmonitor may have run on `commit`) would
    # make the arm's reading meaningless. Clear both, so the only run that can recreate
    # them is the arm's own.
    marker_filter.unlink(missing_ok=True)
    marker_fsmonitor.unlink(missing_ok=True)

    settings = top / "settings.json"
    return Fixture(top, main, probe, decoy_git, marker_filter, marker_fsmonitor, settings)


@dataclass
class ArmResult:
    name: str
    exit_code: int | None
    marker_filter: bool
    marker_fsmonitor: bool
    note: str = ""


def run_self_check(base: Path) -> ArmResult:
    """
    Run plain `git status` in P, exactly as the CLI is claimed to. If the decoy is live
    the marker appears; this is the proof the fixture works, run by the builder in the
    sandbox because it needs no credentials.
    """
    fx = build_fixture(base, "selfcheck")
    done = git(fx.probe, "status", "--porcelain")
    return ArmResult(
        "self-check (git status, builder-run)",
        done.returncode,
        fx.marker_filter.exists(),
        fx.marker_fsmonitor.exists(),
    )


def run_control(base: Path) -> ArmResult:
    """
    Arm d: the decoy is installed, but `true` runs instead of the CLI. No git runs in
    P, so no marker may appear. One here would mean the fixture fires on its own.
    """
    fx = build_fixture(base, "control")
    done = subprocess.run(["true"], cwd=fx.probe, capture_output=True, text=True, check=False)
    return ArmResult(
        "d-control-no-cli",
        done.returncode,
        fx.marker_filter.exists(),
        fx.marker_fsmonitor.exists(),
    )


def run_cli_arm(
    base: Path, name: str, *, disable_env: bool, settings_false: bool, cli: str
) -> ArmResult:
    """
    Run `claude -p 'say ok' --max-turns 1` with cwd P and read the marker. The CLI's
    stdout is never printed: the marker is the result, the narration is not. Needs
    network and credentials, so it runs only outside pptmstr's sandbox.
    """
    fx = build_fixture(base, name)
    env = dict(os.environ)
    if disable_env:
        env["CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"] = "1"
    cmd = [cli, "-p", "say ok", "--max-turns", "1"]
    if settings_false:
        fx.settings.write_text(json.dumps({"includeGitInstructions": False}) + "\n")
        cmd += ["--settings", str(fx.settings)]
    done = subprocess.run(cmd, cwd=fx.probe, capture_output=True, text=True, check=False, env=env)
    return ArmResult(
        name,
        done.returncode,
        fx.marker_filter.exists(),
        fx.marker_fsmonitor.exists(),
    )


def report(version: str, cli: str | None, results: list[ArmResult]) -> None:
    print(f"CLI version:  {version}")
    print(f"CLI binary:   {cli}")
    print()
    print(f"{'arm':<34}  {'exit':>5}  {'filter':>7}  {'fsmonitor':>10}")
    print("-" * 64)
    for r in results:
        ec = "-" if r.exit_code is None else str(r.exit_code)
        print(
            f"{r.name:<34}  {ec:>5}  "
            f"{('YES' if r.marker_filter else 'no'):>7}  "
            f"{('YES' if r.marker_fsmonitor else 'no'):>10}" + (f"   {r.note}" if r.note else "")
        )
    print()
    print("A YES in filter or fsmonitor means git ran the decoy's configured program in")
    print("that arm. In arm a that is the exposure under test; in arm d it must be 'no'.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure whether the Claude CLI runs a tree's git config."
    )
    parser.add_argument(
        "--fixture-only",
        action="store_true",
        help="run only the control arm and the self-check; stop before the CLI arms",
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=None,
        help="directory to build the fresh temp tree under (default: $TMPDIR)",
    )
    parser.add_argument("--keep", action="store_true", help="leave the fixtures on disk")
    args = parser.parse_args()

    base = args.base if args.base is not None else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)

    cli = resolve_cli_path()
    version = read_cli_version(cli).text or "unreadable"

    results: list[ArmResult] = []
    results.append(run_self_check(base))
    results.append(run_control(base))

    rc = 0
    if args.fixture_only:
        report(version, cli, results)
        print()
        print("--fixture-only: the CLI arms (a, b, c) were NOT run; they need credentials")
        print("and network that pptmstr's sandbox denies. The operator runs this script")
        print("with no flag, outside the sandbox, to measure them.")
    elif cli is None:
        print("No claude CLI resolved; cannot run the CLI arms.", file=sys.stderr)
        report(version, cli, results)
        rc = 1
    else:
        results.append(
            run_cli_arm(base, "a-default", disable_env=False, settings_false=False, cli=cli)
        )
        results.append(
            run_cli_arm(base, "b-disable-env", disable_env=True, settings_false=False, cli=cli)
        )
        results.append(
            run_cli_arm(base, "c-settings-false", disable_env=False, settings_false=True, cli=cli)
        )
        report(version, cli, results)

    if not args.keep:
        for child in base.glob("cli-git-probe-*"):
            shutil.rmtree(child, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
