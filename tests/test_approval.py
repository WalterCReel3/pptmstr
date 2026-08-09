"""
Approval: classification, summaries, and diffs.

Classification is the security-relevant part of this program. If it drifts open,
the tool stops being what it claims to be, and nothing else here compensates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pptmstr.approval import (
    Disposition,
    classify,
    diff_line_kind,
    render_diff,
    summarize,
)

# -- classification ------------------------------------------------------------


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "NotebookRead", "TodoWrite"])
def test_reads_are_auto_approved(tool: str) -> None:
    assert classify(tool, {}) is Disposition.AUTO_APPROVE


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "WebFetch"])
def test_mutations_and_network_require_approval(tool: str) -> None:
    assert classify(tool, {}) is Disposition.REQUIRE_APPROVAL


@pytest.mark.parametrize("tool", ["Task", "Agent"])
def test_spawning_a_subagent_requires_approval(tool: str) -> None:
    """
    An orchestrator that gates writes but not the spawning of things that write has
    a hole in it. Design §9 called this "probably yes"; it is yes.
    """
    assert classify(tool, {}) is Disposition.REQUIRE_APPROVAL


@pytest.mark.parametrize("tool", ["SomeFutureTool", "mcp__server__do_thing", ""])
def test_unknown_tools_fail_closed(tool: str) -> None:
    """
    The load-bearing test in this file. An allowlist that defaults open stops being
    an allowlist the first time the SDK or an MCP server adds a tool.
    """
    assert classify(tool, {}) is Disposition.REQUIRE_APPROVAL


def test_nothing_auto_approves_that_can_write() -> None:
    """Belt and braces: no writing tool may ever be added to the auto list."""
    for tool in ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "KillShell"):
        assert classify(tool, {}) is not Disposition.AUTO_APPROVE


# -- summaries -----------------------------------------------------------------


def test_summary_names_the_file_for_writes() -> None:
    assert summarize("Write", {"file_path": "/tmp/a.py", "content": "x"}) == "Write /tmp/a.py"


def test_summary_is_the_command_for_bash() -> None:
    assert summarize("Bash", {"command": "pytest -q"}) == "pytest -q"


def test_summary_names_the_subagent_being_spawned() -> None:
    got = summarize("Task", {"subagent_type": "Explore", "description": "find call sites"})
    assert "Explore" in got and "find call sites" in got


def test_summary_collapses_whitespace_and_clips() -> None:
    """The queue is scanned, so a multi-line command must stay one row tall."""
    got = summarize("Bash", {"command": "line one\n   line two\n" + "x" * 200})
    assert "\n" not in got
    assert len(got) <= 90


def test_summary_of_an_unknown_tool_still_says_something() -> None:
    got = summarize("MysteryTool", {"weird": 3})
    assert "MysteryTool" in got and "weird" in got


def test_summary_of_an_argumentless_tool() -> None:
    assert summarize("MysteryTool", {}) == "MysteryTool"


# -- diffs ---------------------------------------------------------------------


def test_write_to_a_new_file_diffs_against_nothing(tmp_path: Path) -> None:
    target = tmp_path / "new.py"
    diff = render_diff("Write", {"file_path": str(target), "content": "a\nb\n"})
    assert diff is not None
    assert "/dev/null" in diff
    assert "+a" in diff and "+b" in diff


def test_write_over_an_existing_file_shows_the_change(tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("keep\nold\n")
    diff = render_diff("Write", {"file_path": str(target), "content": "keep\nnew\n"})
    assert diff is not None
    assert "-old" in diff and "+new" in diff
    assert "-keep" not in diff


def test_edit_diffs_against_the_file_on_disk(tmp_path: Path) -> None:
    """
    Not against the model's own old_string. The difference between what the model
    believes is in the file and what is actually there is exactly what review is for.
    """
    target = tmp_path / "x.py"
    target.write_text("alpha\nbeta\ngamma\n")
    diff = render_diff(
        "Edit", {"file_path": str(target), "old_string": "beta", "new_string": "BETA"}
    )
    assert diff is not None
    assert "-beta" in diff and "+BETA" in diff
    assert "alpha" in diff


def test_edit_whose_anchor_is_absent_still_renders(tmp_path: Path) -> None:
    """
    "This edit will not apply" is itself something the operator wants to see before
    approving, so a missing anchor must not produce an empty pane.
    """
    target = tmp_path / "x.py"
    target.write_text("nothing matching here\n")
    diff = render_diff(
        "Edit", {"file_path": str(target), "old_string": "absent", "new_string": "replacement"}
    )
    assert diff is not None
    assert "+replacement" in diff


def test_edit_replace_all_replaces_every_occurrence(tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("a\na\na\n")
    diff = render_diff(
        "Edit",
        {"file_path": str(target), "old_string": "a", "new_string": "b", "replace_all": True},
    )
    assert diff is not None
    assert diff.count("+b") == 3


def test_edit_without_replace_all_replaces_one(tmp_path: Path) -> None:
    target = tmp_path / "x.py"
    target.write_text("a\na\na\n")
    diff = render_diff("Edit", {"file_path": str(target), "old_string": "a", "new_string": "b"})
    assert diff is not None
    assert diff.count("+b") == 1


def test_multiedit_renders_each_edit(tmp_path: Path) -> None:
    diff = render_diff(
        "MultiEdit",
        {
            "file_path": str(tmp_path / "x.py"),
            "edits": [
                {"old_string": "one", "new_string": "1"},
                {"old_string": "two", "new_string": "2"},
            ],
        },
    )
    assert diff is not None
    assert "+1" in diff and "+2" in diff


def test_bash_has_no_diff() -> None:
    """
    None is a real answer, not a gap. Inventing a diff for a shell command would be
    worse than showing the command.
    """
    assert render_diff("Bash", {"command": "rm -rf /"}) is None


def test_unreadable_file_does_not_raise(tmp_path: Path) -> None:
    """A directory where a file was expected must not take down the gate."""
    diff = render_diff("Write", {"file_path": str(tmp_path), "content": "x"})
    assert diff is not None


def test_binary_file_does_not_raise(tmp_path: Path) -> None:
    target = tmp_path / "blob"
    target.write_bytes(b"\xff\xfe\x00\x01")
    diff = render_diff("Write", {"file_path": str(target), "content": "text"})
    assert diff is not None


# -- diff styling --------------------------------------------------------------


def test_diff_line_kinds() -> None:
    assert diff_line_kind("+added") == "add"
    assert diff_line_kind("-removed") == "remove"
    assert diff_line_kind(" context") == "context"
    assert diff_line_kind("@@ -1,3 +1,4 @@") == "meta"


def test_file_headers_are_meta_not_add_or_remove() -> None:
    """
    '+++ b/x' starts with '+' but is not an addition; colouring it green would put a
    misleading green line at the top of every diff.
    """
    assert diff_line_kind("+++ b/x.py") == "meta"
    assert diff_line_kind("--- a/x.py") == "meta"


def test_absolute_paths_do_not_get_a_doubled_slash(tmp_path: Path) -> None:
    """
    'b//tmp/x' reads as a typo in the header of every diff the operator sees. The
    a/ b/ prefixes are a git convention for repo-relative paths.
    """
    target = tmp_path / "x.py"
    diff = render_diff("Write", {"file_path": str(target), "content": "a\n"})
    assert diff is not None
    assert "//" not in diff
    assert f"+++ {target}" in diff


def test_relative_paths_keep_the_git_style_prefix() -> None:
    diff = render_diff("Edit", {"file_path": "src/x.py", "old_string": "a", "new_string": "b"})
    assert diff is not None
    assert "b/src/x.py" in diff
