"""
``scripts/probe_cli_git_status.py``'s arms, held without the CLI: each arm's fixture
plants what its name says, the options it hands the SDK are the driver's, and a
missing marker from a CLI that did not finish is never read as a no.

The CLI arms themselves need credentials and run only outside pptmstr's sandbox; what
is held here is everything about them that is decidable before the CLI starts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from pptmstr.bridge import Bridge
from pptmstr.driver import GIT_STATUS_OFF, AgentSession
from pptmstr.sandbox import containment_settings

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "probe_cli_git_status", ROOT / "scripts" / "probe_cli_git_status.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()

VAR = "CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS"


def _argv(options: Any) -> list[str]:
    """
    The transport's argv for ``options``, with the per-session id masked: every
    ``AgentSession`` draws a fresh one, and it is the only field that may differ
    between two sessions built from the same arguments.
    """
    return [
        re.sub(r"^--session-id=.*$", "--session-id=<id>", arg)
        for arg in probe.sdk_argv(options, "claude")
    ]


@pytest.fixture
def trust_plans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    # Exported as the operator might have it, so a control that fails to drop the
    # variable shows up here.
    monkeypatch.setenv(VAR, "1")
    return {plan.name: plan for plan in probe.plan_trust_arms(tmp_path, "claude")}


@pytest.fixture
def pptmstr_plans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv(VAR, "1")
    return {plan.name: plan for plan in probe.plan_pptmstr_arms(tmp_path, "claude")}


def test_the_trust_arms_are_the_ones_the_task_names(trust_plans: dict[str, Any]) -> None:
    assert set(trust_plans) == {
        "p1-project-env",
        "p1-control",
        "p1-neutral",
        "p2-local-env",
        "p2-control",
        "p2-neutral",
        "k1-sessionstart-uncontained",
        "k1-sessionstart-contained",
        "k1-sessionstart-control",
        "k1-userprompt-uncontained",
        "k1-userprompt-contained",
        "k1-userprompt-control",
    }
    for name, plan in trust_plans.items():
        assert plan.expect == ("YES" if name.endswith("-control") else "no"), name


@pytest.mark.parametrize(
    "names, filename, payload",
    [
        (("p1-project-env", "p1-control"), "settings.json", {"env": {VAR: ""}}),
        (("p2-local-env", "p2-control"), "settings.local.json", {"env": {VAR: ""}}),
        (("p1-neutral",), "settings.json", {"env": {"PPTMSTR_PROBE_NEUTRAL": "1"}}),
        (("p2-neutral",), "settings.local.json", {"env": {"PPTMSTR_PROBE_NEUTRAL": "1"}}),
    ],
)
def test_each_env_arm_plants_its_tier_file_and_only_that(
    trust_plans: dict[str, Any], names: tuple[str, ...], filename: str, payload: dict[str, Any]
) -> None:
    for name in names:
        plan = trust_plans[name]
        target = plan.fx.probe / ".claude" / filename
        assert plan.planted == [target]
        assert json.loads(target.read_text()) == payload
        assert sorted(p.name for p in (plan.fx.probe / ".claude").iterdir()) == [filename]


@pytest.mark.parametrize(
    "event, short", [("SessionStart", "sessionstart"), ("UserPromptSubmit", "userprompt")]
)
def test_each_hook_arm_plants_a_project_hook_that_touches_both_markers(
    trust_plans: dict[str, Any], event: str, short: str
) -> None:
    for suffix in ("uncontained", "contained", "control"):
        plan = trust_plans[f"k1-{short}-{suffix}"]
        target = plan.fx.probe / ".claude" / "settings.json"
        assert plan.planted == [target]
        assert plan.hooks
        hooks = json.loads(target.read_text())["hooks"]
        assert list(hooks) == [event]
        (entry,) = hooks[event]
        (handler,) = entry["hooks"]
        assert handler["type"] == "command"
        inside, outside = probe.hook_markers(plan.fx)
        assert inside.parent == plan.fx.probe
        assert outside.parent == plan.fx.top
        assert plan.fx.probe not in outside.parents
        assert not inside.exists() and not outside.exists()
        # The command is run here, the way the CLI's shell form runs it, to show it
        # touches exactly the two markers the arm reads.
        subprocess.run(["sh", "-c", handler["command"]], check=True)
        assert inside.exists() and outside.exists()


def test_the_planted_fixture_still_fires_on_git_status(trust_plans: dict[str, Any]) -> None:
    # Planting .claude/ into P must not disarm the decoy, or every arm reads no.
    plan = trust_plans["p1-project-env"]
    assert not plan.fx.marker_filter.exists()
    probe.git(plan.fx.probe, "status", "--porcelain")
    assert plan.fx.marker_filter.exists()


def test_the_controls_start_the_cli_directly_without_the_variable(
    trust_plans: dict[str, Any], pptmstr_plans: dict[str, Any]
) -> None:
    controls = [p for n, p in trust_plans.items() if n.endswith("-control")]
    controls.append(pptmstr_plans["h-settings-env-control"])
    for plan in controls:
        assert plan.options is None
        assert plan.cmd[:5] == ["claude", "-p", "say ok", "--max-turns", "1"]
        assert VAR not in plan.env
    # Only h passes its settings by flag; the others rely on the planted file.
    assert pptmstr_plans["h-settings-env-control"].cmd[5:] == [
        "--settings",
        json.dumps({"env": {VAR: ""}}),
    ]
    for plan in controls[:-1]:
        assert plan.cmd[5:] == []


def test_the_pptmstr_arms_carry_the_switch_and_e0_alone_drops_it(
    trust_plans: dict[str, Any], pptmstr_plans: dict[str, Any]
) -> None:
    sdk = [p for p in [*trust_plans.values(), *pptmstr_plans.values()] if p.options is not None]
    assert sdk
    for plan in sdk:
        expected = {} if plan.name == "e0-pptmstr-env-cleared" else GIT_STATUS_OFF
        assert plan.options.env == expected, plan.name
        assert plan.options.cwd == str(plan.fx.probe)


@pytest.mark.parametrize("contained", [False, True])
def test_the_options_builder_matches_the_drivers(tmp_path: Path, contained: bool) -> None:
    fx = probe.build_fixture(tmp_path, "match")
    built = probe.pptmstr_options(fx, contained=contained)
    driver = AgentSession(
        Bridge(),
        task="say ok",
        cwd=str(fx.probe),
        containment=containment_settings() if contained else None,
    )._options()
    assert _argv(built) == _argv(driver)
    assert built.env == driver.env
    assert built.cwd == driver.cwd
    assert built.settings == driver.settings
    assert sorted(built.agents or {}) == sorted(driver.agents or {})


def test_e0_differs_from_pptmstrs_launch_in_env_alone(tmp_path: Path) -> None:
    fx = probe.build_fixture(tmp_path, "e0")
    cleared = probe.pptmstr_options(fx, contained=False, env={})
    default = probe.pptmstr_options(fx, contained=False)
    assert _argv(cleared) == _argv(default)
    assert cleared.env == {} and default.env == GIT_STATUS_OFF


def test_a_contained_hook_arm_flags_an_outside_marker_the_sandbox_could_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = probe.build_fixture(tmp_path, "caveat")
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(tmp_path))
    assert "sandbox may write" in probe.outside_marker_caveat(fx)
    monkeypatch.delenv("CLAUDE_CODE_TMPDIR")
    if Path(f"/tmp/claude-{os.getuid()}") not in tmp_path.resolve().parents:
        assert probe.outside_marker_caveat(fx) == ""


@pytest.mark.parametrize(
    "present, exit_code, reads",
    [
        (True, 0, "YES"),
        (True, None, "YES"),
        (True, 1, "YES"),
        (False, 0, "no"),
        (False, None, "inconclusive"),
        (False, 1, "inconclusive"),
        (None, 0, ""),
    ],
)
def test_a_missing_marker_from_an_unfinished_cli_is_inconclusive(
    present: bool | None, exit_code: int | None, reads: str
) -> None:
    result = probe.ArmResult("x", exit_code, False, False)
    assert probe.verdict(present, result.finished()) == reads


def test_the_report_prints_inconclusive_for_exit_dash(capsys: pytest.CaptureFixture[str]) -> None:
    probe.report(
        "v",
        "cli",
        [probe.ArmResult("e-x", None, False, False, "did not finish: X", expect="no")],
    )
    line = next(row for row in capsys.readouterr().out.splitlines() if row.startswith("e-x"))
    assert line.split()[1:5] == ["no", "-", "inconclusive", "inconclusive"]
