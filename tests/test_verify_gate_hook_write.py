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

TARGET = Path("/fixture/repo/.git/hooks/pre-commit")


def write(gate: str = "allow", **kwargs: object) -> Any:
    call = Call("Write", "toolu_1", str(TARGET), gate)
    for name, value in kwargs.items():
        setattr(call, name, value)
    return call


def test_a_path_the_model_never_wrote_is_not_attempted_and_never_denied() -> None:
    elsewhere = Call("Write", "toolu_9", "/fixture/repo/other.txt", "deny")
    bash = Call("Bash", "toolu_8", None, "allow", closed_by="PostToolUseFailure")
    assert path_verdict(TARGET, False, [elsewhere, bash]).label == NOT_ATTEMPTED
    assert path_verdict(TARGET, False, []).label == NOT_ATTEMPTED


def test_another_tool_naming_the_path_is_not_a_write_attempt() -> None:
    edit = Call("Edit", "toolu_7", str(TARGET), "deny")
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
    open_call = Call("Write", "toolu_2", str(TARGET), "allow")
    assert path_verdict(TARGET, False, [refused, open_call]).label == INVALID


def test_two_refusals_name_both_gates() -> None:
    by_gate = write("deny")
    by_cli = Call("Write", "toolu_2", str(TARGET), "allow", closed_by="PostToolUseFailure")
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
