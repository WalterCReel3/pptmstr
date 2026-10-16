#!/usr/bin/env python3
"""
Does the Claude CLI run a tree's own git configuration when it is started in that tree?

reviewer-10 (rv-final, finding 1) and reviewer-8 (rv-wt-fix-3, finding 1), from a
claude-code docs issue that cites the 2.1.78 changelog, say the CLI builds a
git-status section of its system prompt by running ``git status`` in its own working
directory, and that the CLI process is not inside pptmstr's Bash-only sandbox. If so,
launching a session in a tree that a prior autonomous session wrote -- the launcher's
"continue there" offer, or resuming a session in its own worktree -- runs that tree's
git configuration as the operator. Arms a-d measure the bare CLI; pptmstr's own
launches now set ``CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS`` (``driver.GIT_STATUS_OFF``),
and arms e-h measure that.

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

With ``--pptmstr``, four more arms measure the switch pptmstr now sets
(``driver.GIT_STATUS_OFF``) through its own option building rather than a direct call:

  e-pptmstr-uncontained       the CLI started by the SDK from ``AgentSession._options()``.
  f-pptmstr-contained         the same, with ``sandbox.containment_settings()``.
  g-pptmstr-vs-settings-env   e, with a ``--settings`` whose ``env`` block sets the
                              variable to the empty string: can an operator's settings
                              undo it?
  h-settings-env-control      the CLI directly, no variable, g's ``--settings``. Must
                              answer YES for g to be read.
  e0-pptmstr-env-cleared      e with ``env={}``: pptmstr's launch shape without the
                              switch. Must answer YES for e and f to be read: it shows
                              that pptmstr's own option shape runs git status at all.

With ``--trust``, the arms that ask what a forged tree's *own* settings can make the
CLI do through pptmstr's launch. pptmstr leaves ``setting_sources`` unset, so the CLI
loads the tree's ``.claude/settings.json`` (project tier) and
``.claude/settings.local.json`` (local tier):

  p1-project-env              the switch on, and P/.claude/settings.json sets the
                              variable to "" in its ``env`` block.
  p1-control                  the same file, the CLI started directly, no variable.
  p1-neutral                  the switch on, P/.claude/settings.json with an unrelated
                              ``env`` entry. Expected no: a YES in p1 is then the
                              variable's doing, not some other git run that loading a
                              project settings file costs.
  p2-local-env, p2-control,   the same three in P/.claude/settings.local.json.
  p2-neutral
  k1-<event>-uncontained      P/.claude/settings.json defines one hook (SessionStart or
  k1-<event>-contained        UserPromptSubmit) whose command touches a marker inside P
                              and a second outside P, in the fixture's top directory.
                              Launched from pptmstr's options, without and with
                              ``sandbox.containment_settings()``. The outside marker in
                              the contained arm asks whether the hook ran inside the
                              sandbox, which would refuse that write.
  k1-<event>-control          the same hook, the CLI started directly.

With ``--user-only``, the candidate fix: the same forged-tree arms with pptmstr's
options plus ``setting_sources=["user"]`` (argv ``--setting-sources=user``). Each "no"
here is paired with its default-sources arm from ``--trust``, which ``--user-only``
therefore also runs, and which answered YES when the operator ran it:

  u0-user-only-env-cleared    user-only, ``env={}``, nothing planted. Expected YES: the
                              user-only launch shape still runs git status, so a "no"
                              in u-p1/u-p2 is the project or local tier not loading,
                              not git never running. (Control for u-p1, u-p2.)
  u-p1-project-env            p1-project-env under user-only. Control: p1-project-env.
  u-p2-local-env              p2-local-env under user-only. Control: p2-local-env.
  u-k1-<event>-uncontained    k1-<event>-uncontained under user-only.
  u-k1-<event>-contained      k1-<event>-contained under user-only.
  u-flag-hook-contained       user-only and contained, with a SessionStart hook added to
                              the ``--settings`` JSON (the flag tier, which carries the
                              containment), nothing planted in P. Expected YES: the flag
                              tier still loads under user-only, so the containment still
                              reaches the CLI, and hooks still fire on this shape, so a
                              "no" in u-k1 is the project tier not loading.
  u-user-tier-hook            user-only, ``CLAUDE_CONFIG_DIR`` pointed at a throwaway
                              directory in the fixture whose ``settings.json`` defines a
                              SessionStart hook. Expected YES: the user tier still
                              applies. The operator's real ``~/.claude`` is not touched,
                              and for that reason the CLI also finds no credentials there:
                              unless the operator authenticates through the environment,
                              it may stop before its first request, and the arm then
                              reads YES only if SessionStart fired first, inconclusive
                              otherwise.

**A "no" from an arm whose CLI did not finish is not a no.** Exit ``-`` (the SDK
session raised before a result) or a non-zero exit from a direct CLI call means the
CLI may have stopped before it built its prompt or ran its hooks, so a missing marker
is printed as ``inconclusive``. A marker that is present is a YES whatever the exit.

**The CLI arms cannot run inside pptmstr's sandbox.** ``/home/wreel/.claude/.credentials.json``
is denied to a sandboxed command, so ``claude -p`` cannot authenticate here. Run with
``--fixture-only`` this script therefore runs only what is decidable without the CLI:
the control arm (d), and a self-check that runs plain ``git status`` in P and confirms
the marker appears -- proof the decoy is live. Run with no flag (the operator, outside
the sandbox) it runs all four arms.

Usage:  .venv/bin/python scripts/probe_cli_git_status.py [--fixture-only] [--pptmstr]
            [--trust] [--user-only] [--dry-run] [--base DIR] [--keep]

    --fixture-only   build the fixture, run the self-check and the control arm, and
                     STOP before the CLI arms. This is what a sandboxed builder runs.
    --pptmstr        add arms e0 and e-h.
    --trust          add arms p1, p2 and k1 with their controls.
    --user-only      add the u- arms (``setting_sources=["user"]``), and the --trust
                     arms that are their controls.
    --dry-run        build every selected arm's fixture and print what each would
                     start -- argv, environment override, planted files -- and start
                     no CLI.
    --base DIR       create the fresh temp tree under DIR (default: $TMPDIR).
    --keep           leave the fixtures on disk for inspection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    """
    One arm's reading. ``hook_in`` and ``hook_out`` are None for an arm that plants no
    hook; ``expect`` is what the arm must answer for the reading to be the one its
    design assumes, and is empty for arms a-d, whose meaning the note gives.
    """

    name: str
    exit_code: int | None
    marker_filter: bool
    marker_fsmonitor: bool
    note: str = ""
    expect: str = ""
    hook_in: bool | None = None
    hook_out: bool | None = None

    def finished(self) -> bool:
        return self.exit_code == 0


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


async def _drive(options: Any) -> tuple[int | None, str]:
    """
    One turn of ``say ok`` through the SDK client, on the options pptmstr built. The
    exit code is 0 for a turn that reached its result and None otherwise; the note is
    the failure's type and nothing of its text, because a CLI's error can echo its
    environment.
    """
    from claude_agent_sdk import ClaudeSDKClient, ResultMessage

    try:
        async with ClaudeSDKClient(options=options) as client:
            await client.query("say ok")
            stream = client.receive_messages()
            while True:
                message = await asyncio.wait_for(stream.__anext__(), timeout=180.0)
                if isinstance(message, ResultMessage):
                    return 0, ""
    except Exception as exc:  # noqa: BLE001 -- the failure is the observation
        return None, f"did not finish: {type(exc).__name__}"
    # Reached only if the client's context swallowed the loop's exception.
    return None, "did not finish: no result"


GIT_VAR = "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"

# The variable set to the empty string. It re-enables the section under every reading
# of the variable the 2.1.301 bundle allows (``!""`` is true, and a parsed empty is
# undefined), and the direct-CLI controls (h, p1-control, p2-control) show it does.
ENV_EMPTY: dict[str, Any] = {"env": {GIT_VAR: ""}}
_SETTINGS_ENV_EMPTY = json.dumps(ENV_EMPTY)

# An env entry nothing reads, for the neutral arms: a settings file is planted and
# loaded, and nothing in it touches the git section.
ENV_NEUTRAL: dict[str, Any] = {"env": {"PPTMSTR_PROBE_NEUTRAL": "1"}}

# Where a forged tree's own settings live, by tier.
TIER_FILES = {"project": "settings.json", "local": "settings.local.json"}

HOOK_EVENTS = ("SessionStart", "UserPromptSubmit")


def pptmstr_options(fx: Fixture, *, contained: bool, env: dict[str, str] | None = None) -> Any:
    """
    The options ``AgentSession.run()`` hands ``ClaudeSDKClient`` for a session in P,
    built by ``AgentSession._options()`` itself and not reproduced here: run() adds
    nothing to them between the refusal checks and the client. ``contained`` gives
    the session ``sandbox.containment_settings()``, the other shape ``_options``
    builds. ``env``, when given, replaces the options' ``env`` field and nothing else;
    ``{}`` is pptmstr's launch shape without the switch.
    """
    import dataclasses

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.sandbox import containment_settings

    session = AgentSession(
        Bridge(),
        task="say ok",
        cwd=str(fx.probe),
        containment=containment_settings() if contained else None,
    )
    options = session._options()
    if env is not None:
        options = dataclasses.replace(options, env=dict(env))
    return options


def plant_settings(fx: Fixture, tier: str, payload: dict[str, Any]) -> Path:
    """
    Write ``payload`` as the forged tree's own settings file for ``tier``
    ("project" or "local") under P/.claude, and return its path.
    """
    target = fx.probe / ".claude" / TIER_FILES[tier]
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(payload) + "\n")
    return target


def hook_markers(fx: Fixture) -> tuple[Path, Path]:
    """
    The hook's two markers: one inside P, which a sandboxed command may write, and
    one in the fixture's top directory, beside P and outside it.
    """
    return fx.probe / "HOOK_MARKER_IN", fx.top / "HOOK_MARKER_OUT"


def hook_settings(fx: Fixture, event: str) -> dict[str, Any]:
    """
    A settings payload defining one command hook on ``event`` that touches both
    markers. Two separate ``touch`` commands, so a refused outside write cannot keep
    the inside marker from appearing.
    """
    inside, outside = hook_markers(fx)
    command = f"touch {shlex.quote(str(inside))}; touch {shlex.quote(str(outside))}"
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": command}]}]}}


def outside_marker_caveat(fx: Fixture) -> str:
    """
    Empty when the outside marker lies where the contained sandbox would refuse a
    write, otherwise why it may not. The sandbox lets a command write its working
    directory and a per-user temp directory (code.claude.com/docs/en/sandboxing,
    "Default write behavior"); a fixture built under either reads YES outside
    whether or not the hook was sandboxed.
    """
    top = fx.top.resolve()
    writable = [fx.probe.resolve(), Path(f"/tmp/claude-{os.getuid()}")]
    override = os.environ.get("CLAUDE_CODE_TMPDIR")
    if override:
        writable.append(Path(override).resolve())
    for root in writable:
        if top == root or root in top.parents:
            return f"outside marker is under {root}, which the sandbox may write"
    return ""


@dataclass
class Plan:
    """
    One arm, built and not yet run. ``options`` is set for an arm the SDK starts from
    pptmstr's options; ``cmd`` and ``env`` for one this script starts directly.
    ``planted`` names the settings files the arm wrote into P.
    """

    name: str
    expect: str
    fx: Fixture
    options: Any = None
    cmd: list[str] | None = None
    env: dict[str, str] | None = None
    planted: list[Path] = field(default_factory=list)
    hooks: bool = False
    caveat: str = ""
    control: str = ""


def _direct(cli: str, settings: str | None = None) -> list[str]:
    cmd = [cli, "-p", "say ok", "--max-turns", "1"]
    if settings is not None:
        cmd += ["--settings", settings]
    return cmd


def _env_without_switch() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != GIT_VAR}


def plan_pptmstr_arms(base: Path, cli: str) -> list[Plan]:
    """
    Arms e0 and e-h. e0 is pptmstr's launch shape with the switch removed, the
    positive control on pptmstr's own path. g replaces the options' settings with a
    ``--settings`` env block (the flag tier only: it says nothing about the user,
    project or local tiers), and h is its direct-CLI control.
    """
    import dataclasses

    plans = []
    fx = build_fixture(base, "e0")
    plans.append(
        Plan("e0-pptmstr-env-cleared", "YES", fx, pptmstr_options(fx, contained=False, env={}))
    )
    fx = build_fixture(base, "e")
    plans.append(Plan("e-pptmstr-uncontained", "no", fx, pptmstr_options(fx, contained=False)))
    fx = build_fixture(base, "f")
    plans.append(Plan("f-pptmstr-contained", "no", fx, pptmstr_options(fx, contained=True)))
    fx = build_fixture(base, "g")
    options = dataclasses.replace(
        pptmstr_options(fx, contained=False), settings=_SETTINGS_ENV_EMPTY
    )
    plans.append(Plan("g-pptmstr-vs-settings-env", "no", fx, options))
    fx = build_fixture(base, "h")
    plans.append(
        Plan(
            "h-settings-env-control",
            "YES",
            fx,
            cmd=_direct(cli, _SETTINGS_ENV_EMPTY),
            env=_env_without_switch(),
        )
    )
    return plans


def plan_trust_arms(base: Path, cli: str) -> list[Plan]:
    """
    Arms p1, p2 and k1: what the forged tree's own settings files make the CLI do
    through pptmstr's launch. Each "no" is paired with a direct-CLI control that must
    answer YES; p1 and p2 also have a neutral arm, so that a YES there is the
    variable's doing and not a git run that loading the file costs.
    """
    plans = []
    for tier, short in (("project", "p1"), ("local", "p2")):
        label = f"{short}-{tier}-env"
        fx = build_fixture(base, label)
        planted = [plant_settings(fx, tier, ENV_EMPTY)]
        plans.append(Plan(label, "no", fx, pptmstr_options(fx, contained=False), planted=planted))
        fx = build_fixture(base, f"{short}-control")
        planted = [plant_settings(fx, tier, ENV_EMPTY)]
        plans.append(
            Plan(
                f"{short}-control",
                "YES",
                fx,
                cmd=_direct(cli),
                env=_env_without_switch(),
                planted=planted,
            )
        )
        fx = build_fixture(base, f"{short}-neutral")
        planted = [plant_settings(fx, tier, ENV_NEUTRAL)]
        plans.append(
            Plan(
                f"{short}-neutral",
                "no",
                fx,
                pptmstr_options(fx, contained=False),
                planted=planted,
            )
        )
    for event in HOOK_EVENTS:
        short = "sessionstart" if event == "SessionStart" else "userprompt"
        for contained in (False, True):
            label = f"k1-{short}-{'contained' if contained else 'uncontained'}"
            fx = build_fixture(base, label)
            planted = [plant_settings(fx, "project", hook_settings(fx, event))]
            plans.append(
                Plan(
                    label,
                    "no",
                    fx,
                    pptmstr_options(fx, contained=contained),
                    planted=planted,
                    hooks=True,
                    caveat=outside_marker_caveat(fx) if contained else "",
                )
            )
        label = f"k1-{short}-control"
        fx = build_fixture(base, label)
        planted = [plant_settings(fx, "project", hook_settings(fx, event))]
        plans.append(
            Plan(
                label,
                "YES",
                fx,
                cmd=_direct(cli),
                env=_env_without_switch(),
                planted=planted,
                hooks=True,
            )
        )
    return plans


# The candidate fix under measurement: the CLI loads the user tier and neither of the
# tree's own tiers. Managed and flag settings are not sources this list names; the
# 2.1.301 bundle adds "flagSettings" and "policySettings" to whatever it is given,
# which u-flag-hook-contained measures.
USER_ONLY = ["user"]


def user_only(options: Any) -> Any:
    """
    ``options`` with ``setting_sources=["user"]`` and nothing else changed.
    """
    import dataclasses

    return dataclasses.replace(options, setting_sources=list(USER_ONLY))


def with_flag_hook(settings: str | None, fx: Fixture, event: str) -> str:
    """
    ``settings`` (the ``--settings`` JSON, or None) with ``hook_settings``'s hook
    added as a top-level ``hooks`` key and every other key kept as it was.
    """
    merged: dict[str, Any] = json.loads(settings) if settings else {}
    if "hooks" in merged:
        raise ValueError("the flag settings already define hooks; refusing to overwrite them")
    merged["hooks"] = hook_settings(fx, event)["hooks"]
    return json.dumps(merged)


def config_home(fx: Fixture) -> Path:
    """
    The throwaway directory ``CLAUDE_CONFIG_DIR`` names for u-user-tier-hook: the
    user tier's ``settings.json`` lives there in place of the operator's ``~/.claude``.
    It sits in the fixture's top directory, beside P and not inside it.
    """
    return fx.top / "config-home"


def plan_user_only_arms(base: Path, cli: str) -> list[Plan]:
    """
    The u- arms: the forged-tree arms of ``plan_trust_arms`` with pptmstr's options
    plus ``setting_sources=["user"]``. Each "no" names its control, the --trust arm
    with the same fixture and the default sources. u0 and u-flag-hook-contained are
    positive controls on the user-only shape itself, and u-user-tier-hook shows what
    the shape keeps.
    """
    import dataclasses

    del cli  # every u- arm is launched through the SDK from pptmstr's options
    plans = []
    fx = build_fixture(base, "u0")
    plans.append(
        Plan(
            "u0-user-only-env-cleared",
            "YES",
            fx,
            user_only(pptmstr_options(fx, contained=False, env={})),
            control="e0-pptmstr-env-cleared",
        )
    )
    for tier, short in (("project", "p1"), ("local", "p2")):
        label = f"u-{short}-{tier}-env"
        fx = build_fixture(base, label)
        planted = [plant_settings(fx, tier, ENV_EMPTY)]
        plans.append(
            Plan(
                label,
                "no",
                fx,
                user_only(pptmstr_options(fx, contained=False)),
                planted=planted,
                control=f"{short}-{tier}-env",
            )
        )
    for event in HOOK_EVENTS:
        short = "sessionstart" if event == "SessionStart" else "userprompt"
        for contained in (False, True):
            suffix = "contained" if contained else "uncontained"
            label = f"u-k1-{short}-{suffix}"
            fx = build_fixture(base, label)
            planted = [plant_settings(fx, "project", hook_settings(fx, event))]
            plans.append(
                Plan(
                    label,
                    "no",
                    fx,
                    user_only(pptmstr_options(fx, contained=contained)),
                    planted=planted,
                    hooks=True,
                    caveat=outside_marker_caveat(fx) if contained else "",
                    control=f"k1-{short}-{suffix}",
                )
            )
    fx = build_fixture(base, "u-flag-hook")
    contained_options = user_only(pptmstr_options(fx, contained=True))
    options = dataclasses.replace(
        contained_options,
        settings=with_flag_hook(contained_options.settings, fx, "SessionStart"),
    )
    plans.append(
        Plan(
            "u-flag-hook-contained",
            "YES",
            fx,
            options,
            hooks=True,
            caveat=outside_marker_caveat(fx),
        )
    )
    fx = build_fixture(base, "u-user-tier")
    home = config_home(fx)
    home.mkdir()
    user_settings = home / "settings.json"
    user_settings.write_text(json.dumps(hook_settings(fx, "SessionStart")) + "\n")
    base_options = user_only(pptmstr_options(fx, contained=False))
    options = dataclasses.replace(
        base_options, env={**base_options.env, "CLAUDE_CONFIG_DIR": str(home)}
    )
    plans.append(
        Plan(
            "u-user-tier-hook",
            "YES",
            fx,
            options,
            planted=[user_settings],
            hooks=True,
        )
    )
    return plans


def run_plan(plan: Plan) -> ArmResult:
    """
    Start the arm's CLI, then read its markers. A direct CLI's output is captured and
    never printed; an SDK arm's messages are read only for the result's arrival.
    """
    note = ""
    if plan.options is not None:
        exit_code, note = asyncio.run(_drive(plan.options))
    else:
        assert plan.cmd is not None
        done = subprocess.run(
            plan.cmd,
            cwd=plan.fx.probe,
            capture_output=True,
            text=True,
            check=False,
            env=plan.env,
        )
        exit_code = done.returncode
    if plan.caveat:
        note = f"{note}; {plan.caveat}" if note else plan.caveat
    inside, outside = hook_markers(plan.fx)
    return ArmResult(
        plan.name,
        exit_code,
        plan.fx.marker_filter.exists(),
        plan.fx.marker_fsmonitor.exists(),
        note,
        expect=plan.expect,
        hook_in=inside.exists() if plan.hooks else None,
        hook_out=outside.exists() if plan.hooks else None,
    )


def sdk_argv(options: Any, cli: str | None) -> list[str]:
    """
    The argv the SDK transport builds from ``options``, by the transport's own
    ``_build_command``. ``cli_path`` is filled in for the build only, because the
    transport resolves it at connect, which this does not reach.
    """
    import dataclasses

    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    shown = dataclasses.replace(options, cli_path=cli or "claude")
    return SubprocessCLITransport(prompt="", options=shown)._build_command()


def describe_plan(plan: Plan, cli: str | None) -> list[str]:
    """
    What the arm would start, for ``--dry-run``: the argv, the environment it
    overrides, and the files it planted. No value of the operator's environment is
    printed, only the names this arm adds or removes.
    """
    lines = [f"[{plan.name}]  expect {plan.expect}  cwd={plan.fx.probe}"]
    if plan.options is not None:
        lines.append("  via SDK from AgentSession._options()")
        lines.append(f"  argv: {shlex.join(sdk_argv(plan.options, cli))}")
        lines.append(f"  options.env: {plan.options.env!r}")
        lines.append(f"  options.setting_sources: {plan.options.setting_sources!r}")
    else:
        assert plan.cmd is not None and plan.env is not None
        lines.append("  direct CLI")
        lines.append(f"  argv: {shlex.join(plan.cmd)}")
        removed = sorted(set(os.environ) - set(plan.env))
        lines.append(f"  env: inherited, minus {removed}")
    for path in plan.planted:
        lines.append(f"  planted {path}: {path.read_text().strip()}")
    if plan.hooks:
        inside, outside = hook_markers(plan.fx)
        lines.append(f"  hook markers: in={inside} out={outside}")
    if plan.caveat:
        lines.append(f"  caveat: {plan.caveat}")
    if plan.control:
        lines.append(f"  control: {plan.control}")
    return lines


def verdict(present: bool | None, finished: bool) -> str:
    """
    How one marker reads: a present marker is YES whatever the exit; a missing one is
    no only from a CLI that finished, and inconclusive otherwise.
    """
    if present is None:
        return ""
    if present:
        return "YES"
    return "no" if finished else "inconclusive"


def report(version: str, cli: str | None, results: list[ArmResult]) -> None:
    print(f"CLI version:  {version}")
    print(f"CLI binary:   {cli}")
    print()
    header = (
        f"{'arm':<36}  {'expect':>6}  {'exit':>4}  {'filter':>12}  {'fsmonitor':>12}"
        f"  {'hook-in':>12}  {'hook-out':>12}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        ec = "-" if r.exit_code is None else str(r.exit_code)
        ok = r.finished()
        print(
            f"{r.name:<36}  {r.expect:>6}  {ec:>4}  "
            f"{verdict(r.marker_filter, ok):>12}  {verdict(r.marker_fsmonitor, ok):>12}  "
            f"{verdict(r.hook_in, ok):>12}  {verdict(r.hook_out, ok):>12}"
            + (f"   {r.note}" if r.note else "")
        )
    print()
    print("A YES in filter or fsmonitor means git ran the decoy's configured program in")
    print("that arm. In arm a that is the exposure under test; in arm d it must be 'no'.")
    print("A YES in hook-in or hook-out means the tree's own hook ran. 'inconclusive' is")
    print("a missing marker from a CLI that did not finish (exit '-' or non-zero): the")
    print("CLI may have stopped before it built its prompt or ran its hooks.")


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
    parser.add_argument(
        "--pptmstr",
        action="store_true",
        help="also run arms e0 and e-h: the CLI started from AgentSession._options()",
    )
    parser.add_argument(
        "--trust",
        action="store_true",
        help="also run arms p1, p2 and k1: the forged tree's own settings and hooks",
    )
    parser.add_argument(
        "--user-only",
        action="store_true",
        help="also run the u- arms (setting_sources=['user']) and the --trust arms "
        "that are their controls",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build the selected arms and print what each would start; start no CLI",
    )
    args = parser.parse_args()

    base = args.base if args.base is not None else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)

    cli = resolve_cli_path()
    version = read_cli_version(cli).text or "unreadable"

    results: list[ArmResult] = []
    results.append(run_self_check(base))
    results.append(run_control(base))

    rc = 0
    if args.dry_run:
        report(version, cli, results)
        plans: list[Plan] = []
        if args.pptmstr:
            plans += plan_pptmstr_arms(base, cli or "claude")
        if args.trust or args.user_only:
            plans += plan_trust_arms(base, cli or "claude")
        if args.user_only:
            plans += plan_user_only_arms(base, cli or "claude")
        print()
        print("--dry-run: no CLI was started. Each arm would start:")
        for plan in plans:
            print()
            print("\n".join(describe_plan(plan, cli)))
    elif args.fixture_only:
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
        plans = []
        if args.pptmstr:
            plans += plan_pptmstr_arms(base, cli)
        if args.trust or args.user_only:
            plans += plan_trust_arms(base, cli)
        if args.user_only:
            plans += plan_user_only_arms(base, cli)
        # Every arm has its own fixture and reads only that fixture's markers, so one
        # arm's hook or git run cannot be read as another's.
        for plan in plans:
            results.append(run_plan(plan))
        report(version, cli, results)

    if not args.keep:
        for child in base.glob("cli-git-probe-*"):
            shutil.rmtree(child, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
