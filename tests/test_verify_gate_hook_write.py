"""
The verdict logic of `scripts/verify_gate_hook_write.py`, with no session.

The probe's whole value is that DENIED is not a word it reaches by default: an absent
file has four causes and only one of them is a refusal. These construct each.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load() -> ModuleType:
    """
    The script as a module. Loaded by path because `scripts/` is not a package.
    """
    source = Path(__file__).resolve().parent.parent / "scripts" / "verify_gate_hook_write.py"
    spec = importlib.util.spec_from_file_location("pptmstr_test_gate_hook_write", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered before execution: the dataclasses in it resolve their own module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()
CLI, GATE = probe.CLI, probe.GATE
DENIED, INVALID, LANDED, NOT_ATTEMPTED = (
    probe.DENIED,
    probe.INVALID,
    probe.LANDED,
    probe.NOT_ATTEMPTED,
)
Call, Verdict = probe.Call, probe.Verdict
held_to_control, path_verdict = probe.held_to_control, probe.path_verdict
prompt_for, targets_for, tool_results = probe.prompt_for, probe.targets_for, probe.tool_results

TARGET_PATH = Path("/fixture/repo/.git/hooks/pre-commit")
TARGET = probe.Target("hooks/pre-commit", TARGET_PATH, "HW-hook-abc", "# HW-hook-abc")


def write(gate: str = "allow", **kwargs: object) -> Any:
    call = Call("Write", "toolu_1", str(TARGET_PATH), gate)
    for name, value in kwargs.items():
        setattr(call, name, value)
    return call


def test_a_path_the_model_never_wrote_is_not_attempted_and_never_denied() -> None:
    elsewhere = Call("Write", "toolu_9", "/fixture/repo/other.txt", "deny")
    bash = Call("Bash", "toolu_8", None, "allow", closed_by="PostToolUseFailure")
    assert path_verdict(TARGET, False, [elsewhere, bash]).label == NOT_ATTEMPTED
    assert path_verdict(TARGET, False, []).label == NOT_ATTEMPTED


def test_another_tool_naming_the_path_is_not_a_write_attempt() -> None:
    edit = Call("Edit", "toolu_7", str(TARGET_PATH), "deny")
    assert path_verdict(TARGET, False, [edit]).label == NOT_ATTEMPTED


def test_the_literal_on_the_host_is_landed_whatever_the_hooks_closed_with() -> None:
    failed = write(closed_by="PostToolUseFailure", result="nope", result_is_error=True)
    assert path_verdict(TARGET, True, [failed]).label == LANDED


def test_a_literal_on_the_host_with_no_allowed_write_is_invalid_not_landed() -> None:
    assert path_verdict(TARGET, True, []).label == INVALID
    assert path_verdict(TARGET, True, [write("deny")]).label == INVALID


def test_a_gate_deny_is_denied_by_the_gate() -> None:
    verdict = path_verdict(TARGET, False, [write("deny", gate_reason="outside the region")])
    assert (verdict.label, verdict.gated_by) == (DENIED, GATE)


def test_an_allowed_write_that_failed_is_denied_by_the_cli() -> None:
    by_hook = path_verdict(TARGET, False, [write(closed_by="PostToolUseFailure")])
    by_result = path_verdict(TARGET, False, [write(result="refused", result_is_error=True)])
    assert (by_hook.label, by_hook.gated_by) == (DENIED, CLI)
    assert (by_result.label, by_result.gated_by) == (DENIED, CLI)


def test_an_allowed_write_reporting_success_with_no_literal_is_invalid() -> None:
    ok = write(closed_by="PostToolUse", result="File created", result_is_error=False)
    assert path_verdict(TARGET, False, [ok]).label == INVALID


def test_an_allowed_write_nothing_closed_is_invalid_not_denied() -> None:
    assert path_verdict(TARGET, False, [write()]).label == INVALID
    unreadable_gate = write("<<none>>")
    assert path_verdict(TARGET, False, [unreadable_gate]).label == INVALID


def test_one_unaccounted_attempt_outweighs_a_refused_one() -> None:
    refused = write("deny")
    open_call = Call("Write", "toolu_2", str(TARGET_PATH), "allow")
    assert path_verdict(TARGET, False, [refused, open_call]).label == INVALID


def test_two_refusals_name_both_gates() -> None:
    by_gate = write("deny")
    by_cli = Call("Write", "toolu_2", str(TARGET_PATH), "allow", closed_by="PostToolUseFailure")
    verdict = path_verdict(TARGET, False, [by_gate, by_cli])
    assert verdict.label == DENIED
    assert verdict.gated_by == f"{GATE}; {CLI}"


def test_without_the_control_only_a_landed_reading_stands() -> None:
    denied = Verdict(DENIED, GATE, ("x",))
    assert held_to_control(denied, control_landed=False).label == INVALID
    assert held_to_control(Verdict(NOT_ATTEMPTED), control_landed=False).label == INVALID
    assert held_to_control(Verdict(LANDED), control_landed=False).label == LANDED
    assert held_to_control(denied, control_landed=True) is denied


def test_the_four_paths_sit_between_two_controls_with_distinct_literals() -> None:
    targets = targets_for(Path("/fixture/repo"), "abc")
    assert [t.control for t in targets] == [True, False, False, False, False, True]
    assert {str(t.path.relative_to("/fixture/repo")) for t in targets if not t.control} == {
        ".git/hooks/pre-commit",
        ".git/config",
        ".claude/settings.local.json",
        ".mcp.json",
    }
    literals = [t.literal for t in targets]
    assert len(set(literals)) == len(literals)
    assert all(t.literal in t.content for t in targets)
    # No literal is a prefix of another, so one file cannot answer for a second.
    assert not any(a != b and a in b for a in literals for b in literals)
    prompt = prompt_for(targets)
    assert all(str(t.path) in prompt and t.content in prompt for t in targets)


def test_tool_results_are_read_by_call_id_with_their_error_flag() -> None:
    def user(*blocks: dict[str, object]) -> str:
        return json.dumps({"type": "user", "message": {"role": "user", "content": list(blocks)}})

    transcript = "\n".join(
        [
            "not json",
            json.dumps({"type": "attachment"}),
            json.dumps({"type": "user", "message": {"content": "plain"}}),
            user({"type": "tool_result", "tool_use_id": "a", "content": "made", "is_error": False}),
            user(
                {
                    "type": "tool_result",
                    "tool_use_id": "b",
                    "content": [{"type": "text", "text": "no "}, {"type": "text", "text": "way"}],
                    "is_error": True,
                },
                {"type": "text", "text": "ignored"},
            ),
        ]
    )
    assert tool_results(transcript) == {"a": ("made", False), "b": ("no way", True)}


# ------------------------------------------------------------- the protected-path arms

REPO = Path("/fixture/repo")


def test_every_arm_is_bracketed_by_controls_with_distinct_literals() -> None:
    every: list[str] = []
    for arm in probe.ARMS:
        targets = arm.targets(REPO, "abc")
        assert targets[0].control and targets[-1].control, arm.key
        assert any(not t.control for t in targets), arm.key
        assert len({t.key for t in targets}) == len(targets), arm.key
        literals = [t.literal for t in targets if t.key not in ("control-first", "control-last")]
        every.extend(literals)
        assert all(t.literal in t.content for t in targets), arm.key
        prompt = prompt_for(targets)
        assert all(t.content in prompt for t in targets), arm.key
        assert f"all {len(targets)} have been attempted" in prompt
        assert "dangerouslyDisableSandbox" in prompt
    # Across arms too, so a literal never answers for another arm's target.
    assert len(set(every)) == len(every)
    assert not any(a != b and a in b for a in every for b in every)


def test_the_arms_are_the_ones_the_task_names_under_the_policy_it_names() -> None:
    by_key = {arm.key: arm for arm in probe.ARMS}
    assert by_key["a6-strict-approved"].policy is probe.Policy.STRICT
    assert {arm.policy for key, arm in by_key.items() if key != "a6-strict-approved"} == {
        probe.Policy.AUTONOMOUS
    }

    def middle(key: str) -> list[Any]:
        return [t for t in by_key[key].targets(REPO, "abc") if not t.control]

    assert [(t.tool, t.path) for t in middle("a1-write-settings")] == [
        ("Write", REPO / ".claude/settings.json")
    ]
    assert [(t.tool, t.path) for t in middle("a2-write-hook")] == [
        ("Write", REPO / ".claude/hooks/h.sh")
    ]
    edit = middle("a3-edit-settings")
    assert [(t.tool, t.path, t.read_first) for t in edit] == [
        ("Edit", REPO / ".claude/settings.json", True)
    ]
    assert [(t.path, t.seen_at) for t in middle("a4-symlink")] == [
        (REPO / "lnk/settings.json", REPO / ".claude/settings.json"),
        (REPO / "lnk/hooks/h.sh", REPO / ".claude/hooks/h.sh"),
    ]
    bash = middle("a5-bash")
    assert {t.tool for t in by_key["a5-bash"].targets(REPO, "abc")} == {"Bash"}
    assert [t.path for t in bash] == [
        REPO / ".claude/settings.json",
        REPO / ".claude/settings.local.json",
        REPO / ".claude/hooks/h.sh",
        REPO / "sub/.claude/settings.json",
        REPO / ".claude",
    ]
    # The mv is last: a success would move every other target out from under them.
    assert bash[-1].content.startswith("mv ") and bash[-1].exists_only
    assert [t.path for t in middle("a6-strict-approved")] == [REPO / ".claude/settings.json"]


def test_a_bash_call_is_attributed_by_its_literal_and_a_file_call_by_tool_and_path() -> None:
    bash_target = probe.Target(
        "b", REPO / ".claude/settings.json", "HW-b-x", "echo HW-b-x > f", tool="Bash"
    )
    edit_target = probe.Target("e", REPO / "f.json", "HW-e-x", "HW-e-x", tool="Edit", old="B")
    calls = [
        Call("Bash", "t1", None, "allow", command="  echo HW-b-x > f  "),
        Call("Bash", "t2", None, "allow", command="echo other"),
        Call("Write", "t3", str(REPO / "f.json"), "allow"),
        Call("Edit", "t4", str(REPO / "f.json"), "allow"),
        Call("Read", "t5", str(REPO / "f.json"), "allow"),
    ]
    assert [c.tool_use_id for c in probe.attempts(bash_target, calls)] == ["t1"]
    assert [c.tool_use_id for c in probe.attempts(edit_target, calls)] == ["t4"]


def test_a_refused_bash_write_is_denied_by_the_cli_and_a_silent_one_is_invalid() -> None:
    target = probe.Target(
        "b", REPO / ".claude/settings.json", "HW-b-x", "echo HW-b-x > f", tool="Bash"
    )
    refused = Call(
        "Bash",
        "t1",
        None,
        "allow",
        command=target.content,
        result="Read-only",
        result_is_error=True,
    )
    verdict = path_verdict(target, False, [refused])
    assert (verdict.label, verdict.gated_by) == (DENIED, CLI)
    quiet = Call("Bash", "t1", None, "allow", command=target.content, closed_by="PostToolUse")
    assert path_verdict(target, False, [quiet]).label == INVALID
    assert path_verdict(target, True, [quiet]).label == LANDED


def test_a_target_is_held_to_the_first_control_of_its_own_tool() -> None:
    targets = probe.ARMS[3].targets(REPO, "abc")
    assert probe.ARMS[3].key == "a3-edit-settings"
    edit = next(t for t in targets if not t.control)
    assert probe.opening_control(edit, targets).key == "edit-control"
    write_target = targets_for(REPO, "abc")[1]
    assert probe.opening_control(write_target, targets_for(REPO, "abc")).key == "control-first"


def test_strict_approves_only_its_own_file_targets() -> None:
    targets = probe.targets_strict(REPO, "abc")
    settings = str(REPO / ".claude/settings.json")
    assert probe.approvable("Write", {"file_path": settings}, REPO, targets)
    assert probe.approvable("Write", {"file_path": ".claude/settings.json"}, REPO, targets)
    assert probe.approvable("Write", {"file_path": str(REPO / "control-first.txt")}, REPO, targets)
    assert not probe.approvable("Edit", {"file_path": settings}, REPO, targets)
    assert not probe.approvable("Write", {"file_path": str(REPO / "other")}, REPO, targets)
    assert not probe.approvable("Bash", {"command": f"echo x > {settings}"}, REPO, targets)


def test_every_prepared_fixture_is_sound_and_the_host_shows_nothing_yet(tmp_path: Path) -> None:
    for arm in probe.ARMS:
        repo = tmp_path / arm.key
        repo.mkdir()
        arm.prepare(repo, "abc")
        targets = arm.targets(repo, "abc")
        assert probe.fixture_check(arm, repo, targets) == [], arm.key


def test_a_broken_fixture_is_reported(tmp_path: Path) -> None:
    arm = next(a for a in probe.ARMS if a.key == "a3-edit-settings")
    arm.prepare(tmp_path, "abc")
    (tmp_path / ".claude/settings.json").write_text("{}", encoding="utf-8")
    assert probe.fixture_check(arm, tmp_path, arm.targets(tmp_path, "abc"))
    link_arm = next(a for a in probe.ARMS if a.key == "a4-symlink")
    other = tmp_path / "linked"
    other.mkdir()
    (other / ".claude").mkdir()
    (other / "lnk").mkdir()
    assert probe.fixture_check(link_arm, other, link_arm.targets(other, "abc"))


def test_the_mv_is_read_from_the_destination_existing(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    mv = probe.targets_bash(tmp_path, "abc")[-2]
    assert mv.exists_only
    assert probe.on_host(mv)[0] is False
    (tmp_path / ".claude").rename(mv.seen_at)
    assert probe.on_host(mv)[0] is True


def test_the_offline_gate_allows_every_autonomous_target_and_parks_strict(tmp_path: Path) -> None:
    for arm in probe.ARMS:
        repo = tmp_path / arm.key
        repo.mkdir()
        arm.prepare(repo, "abc")
        answers = {probe.offline_gate(t, repo, arm.policy) for t in arm.targets(repo, "abc")}
        if arm.policy is probe.Policy.AUTONOMOUS:
            assert answers == {"allow"}, (arm.key, answers)
        else:
            assert "allow" not in answers and "deny" not in answers, (arm.key, answers)
