"""
Approval: classification, summaries, and diffs.

Classification is the security-relevant part of this program. If it drifts open,
the tool stops being what it claims to be, and nothing else here compensates.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from pptmstr import approval
from pptmstr.approval import (
    Disposition,
    Policy,
    classify,
    diff_line_kind,
    render_diff,
    summarize,
)
from pptmstr.model import WRITING_TOOLS, written_path

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


# -- the policy parameter ------------------------------------------------------

# Near-misses of names the autonomous policy releases, alongside tools nobody has
# seen. Membership is exact, so a lookup that ever became fuzzy would admit "bash"
# or a name with a stray space.
_UNKNOWN_TOOLS = ("SomeFutureTool", "mcp__server__do_thing", "", "bash", "Bash ")


def _known_tools() -> list[str]:
    """
    Every tool name ``approval`` classifies by name.

    Read off the module's own sets rather than written out here, so a tool added
    to ``approval.py`` is swept by the policy tests below without anyone
    remembering to add it. Those tests claim "and nothing else", which a
    hand-maintained list cannot support.
    """
    return sorted(approval._AUTO | approval._REVIEW | approval._BUS_AUTO)


def test_the_default_path_is_bit_identical_to_strict() -> None:
    """
    The whole of the OFF-path evidence. Every other classification test in this
    file, and both call sites in the tree, pass two positional arguments; if the
    default answer ever diverges from the STRICT answer, those tests are measuring
    a path nothing in production takes.
    """
    for tool in [*_known_tools(), *_UNKNOWN_TOOLS]:
        assert classify(tool, {}) is classify(tool, {}, Policy.STRICT), tool


def test_adding_a_tool_to_the_review_list_releases_it_under_the_autonomous_policy() -> None:
    """
    The literal the suite holds, and the mistake it stands in front of is the
    silent one.

    ``_AUTONOMOUS_AUTO = _REVIEW`` is an identity, so the released set cannot drift
    from the reviewed one and no list here could catch it drifting. What the
    identity leaves unguarded is the other direction: a name added to ``_REVIEW``
    meaning "a human should see this" is released unattended in the same keystroke,
    and nothing else in the repository disagrees. Pinning the membership is what
    makes that a decision rather than a side effect -- a tool added here is a
    decision about the autonomous mode whether or not its author was thinking about
    one, and this is where they find that out.

    The opposite mistake does not need pinning and is why this test is on this side
    rather than on the released set: a name in ``_REVIEW`` that the mode failed to
    release is met by ``driver._no_reviewer_reason``, which denies it with a reason
    the agent reads and reports. It costs a visible refusal on the first call
    instead of a park nobody answers, so it surfaces on its own.

    ``planning/2026-09-03`` §6.2 is the precedent one layer in:
    ``test_nothing_auto_approves_that_can_write`` exists because relaxing it "would
    remove the only thing standing between a future contributor and moving those
    tools into ``_AUTO`` outright".

    The MCP names are spelled rather than built from ``approval._BUS_POST``, because
    a pin that reads its expected value out of the module under test pins nothing.
    ``test_bus.py::test_the_gate_and_the_bus_agree_on_tool_names`` is what holds
    those spellings against the server's own.
    """
    assert approval._REVIEW == frozenset(
        {
            "Write",
            "Edit",
            "MultiEdit",
            "NotebookEdit",
            "Bash",
            "BashOutput",
            "KillShell",
            "WebFetch",
            "WebSearch",
            "Task",
            "Agent",
            "mcp__pptmstr__post_concern",
            "mcp__pptmstr__declare_task",
        }
    )


def test_the_autonomous_policy_releases_the_review_list_and_nothing_else() -> None:
    """
    What AUTONOMOUS adds is exactly the set STRICT would have put in front of an
    operator -- an identity, not a list that happens to enumerate the same names.

    Computed from behaviour rather than read off the constant, so moving a tool
    into ``_AUTO`` outright is caught by the same assertion. Together with the
    membership pin above this holds behaviour against a literal, which is the
    exactness the mode needs; neither test makes that claim on its own.
    """
    released = {
        tool
        for tool in [*_known_tools(), *_UNKNOWN_TOOLS]
        if classify(tool, {}, Policy.AUTONOMOUS) is not classify(tool, {}, Policy.STRICT)
    }
    assert released == set(approval._REVIEW)


def test_the_review_list_is_empty_under_the_autonomous_policy() -> None:
    """
    The same fact from the other side, and it is the one the mode needs: under
    AUTONOMOUS no name this build knows classifies as REQUIRE_APPROVAL, so the
    only route to one is the unknown-tool fallthrough. A park under this mode has
    no audience, which is why "which tools are released" matters less than "is
    anything left that waits".

    Beside ``test_nothing_auto_approves_that_can_write`` rather than instead of
    it: that one keeps asserting exactly what it asserts today for STRICT.
    """
    parked = [
        tool
        for tool in _known_tools()
        if classify(tool, {}, Policy.AUTONOMOUS) is Disposition.REQUIRE_APPROVAL
    ]
    assert parked == []
    for tool in _UNKNOWN_TOOLS:
        assert classify(tool, {}, Policy.AUTONOMOUS) is Disposition.REQUIRE_APPROVAL, tool


def test_the_two_uncontained_network_tools_are_released_not_denied() -> None:
    """
    ``WebFetch`` and ``WebSearch`` run inside the CLI process, so the sandbox's
    ``network.strictAllowlist`` does not bound them and they are the mode's one
    unmetered egress. They are released regardless: a ``WebFetch`` carries a URL
    and a prompt, with no request body, so what it can carry out is bounded by a
    URL -- against which an unattended agent that cannot read documentation is the
    larger cost. Named separately from the exactness test above because the
    decision went the other way first and a bare set membership does not say so.
    """
    for tool in ("WebFetch", "WebSearch"):
        assert classify(tool, {}, Policy.AUTONOMOUS) is Disposition.AUTO_APPROVE
        assert classify(tool, {}, Policy.STRICT) is Disposition.REQUIRE_APPROVAL


@pytest.mark.parametrize("policy", list(Policy))
def test_no_policy_denies_outright(policy: Policy) -> None:
    """
    ``classify`` answers with two of the three dispositions and a policy does not
    change that. DENY is the driver's word -- it is what the gate says when there
    is no operator to park for, or when a write leaves the session's directory --
    and a policy-scoped deny set here would put the same refusal in two places
    with only one of them holding the reason.
    """
    for tool in [*_known_tools(), *_UNKNOWN_TOOLS]:
        assert classify(tool, {}, policy) is not Disposition.DENY, tool


@pytest.mark.parametrize("policy", list(Policy))
def test_unknown_tools_fail_closed_at_every_policy(policy: Policy) -> None:
    """
    The fallthrough is reached at every policy. A policy adds a narrower allowlist
    beside the existing one and never touches the default, because a mode that is
    dangerous by choice is still not a mode that admits tools nobody has seen.

    Parametrised over ``list(Policy)``, so a member added without an arm in
    ``_policy_auto`` fails here as well as under mypy.
    """
    for tool in _UNKNOWN_TOOLS:
        assert classify(tool, {}, policy) is Disposition.REQUIRE_APPROVAL, tool


@pytest.mark.parametrize("tool", ["Task", "Agent"])
def test_a_spawn_parks_under_strict_and_runs_under_the_autonomous_policy(tool: str) -> None:
    """
    This reverses 08-22 D2, which kept spawns gated at every preset, and the
    reversal is the one the 09-03 record's "Remaining open" argued for: a
    sub-agent runs in the same CLI process under the same sandbox, so it has the
    same bounded reach as the first agent rather than multiplying it, and reach is
    what D2's argument was about. Total fan-out stays bounded by ``subagent_cap``,
    whose deny sits ahead of ``classify`` in ``driver._gate_tool_use`` -- pinned
    there by ``test_the_cap_refuses_a_spawn_before_any_policy_is_consulted``,
    because it is the one bound a policy cannot widen.

    A mode that cannot spawn never forms a team, so under AUTONOMOUS this is not a
    widening at the margin: it is the difference between the mode working and the
    mode stopping at one agent.
    """
    assert classify(tool, {}, Policy.STRICT) is Disposition.REQUIRE_APPROVAL
    assert classify(tool, {}, Policy.AUTONOMOUS) is Disposition.AUTO_APPROVE


def test_the_schema_loader_is_auto_approved_at_every_policy() -> None:
    """
    ``ToolSearch`` is a registry read, so it is auto-approved for the same reason
    ``Read`` is, and under ``STRICT`` as much as under ``AUTONOMOUS``.

    Named separately from the ``_AUTO`` sweep because what carries it is a claim
    about a *different* tool: admitting the loader does not admit what it loads.
    The gate fires on every tool call -- ``driver`` registers ``PreToolUse`` with
    no ``HookMatcher.matcher`` -- and reads the name off the hook payload, so a
    schema loaded here reaches ``classify`` exactly as an up-front one would. The
    second half of this test is that claim: a ``WebFetch`` the loader made callable
    still parks under ``STRICT``.

    The narrower alternative was ``_REVIEW``, which ``AUTONOMOUS`` would release
    and ``STRICT`` would park. It was declined because parking it puts an operator
    in front of a call with no decision in it.
    """
    for policy in Policy:
        assert classify("ToolSearch", {}, policy) is Disposition.AUTO_APPROVE, policy
    assert classify("WebFetch", {}, Policy.STRICT) is Disposition.REQUIRE_APPROVAL


# Tool names this harness offers, written out rather than read off ``approval``.
#
# The point is the independence. ``_known_tools()`` above is ``_AUTO | _REVIEW |
# _BUS_AUTO``, so the suite is closed over the module under test and a tool the
# harness offers but the module has never named is invisible to every assertion
# built on it. This list is the outside measurement, and it is what makes
# "nothing waits under AUTONOMOUS" a claim about the harness rather than about
# ``approval.py``'s own spelling.
#
# Where the names come from, strongest evidence first:
#
# - Observed arriving at ``PreToolUse`` in a pptmstr session:
#   ``notes/2026-08-17-what-a-worker-is-given.md`` run ``04e4db`` records ``Bash``,
#   ``ToolSearch`` and ``ScheduleWakeup`` refused at the gate, and question 4 of
#   that probe watched for ``Skill`` there.
# - Granted by this application: ``templates.READ_ONLY_TOOLS`` passes ``Read``,
#   ``Glob``, ``Grep``, ``NotebookRead``, ``WebFetch`` and ``WebSearch`` to the SDK
#   as a tool list.
# - Present as a tool name in the CLI binary the SDK launches, 2.1.251, which is
#   the version ``cli_version.py`` reads and above that module's floor.
#
# ``SlashCommand`` and ``AskUserQuestion`` are deliberately absent: neither is
# confirmed offered to a session this application launches, and the instruction
# for this list is to drop what cannot be confirmed rather than to guess wide.
# Host-supplied tools outside the CLI's own set are out of scope here and are
# what the ``deferred-tool-audit`` task is enumerating.
_HARNESS_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "NotebookRead",
    "TodoWrite",
    "ListMcpResources",
    "ReadMcpResource",
    "ToolSearch",
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Bash",
    "BashOutput",
    "KillShell",
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
    # Classified by no set, so each reaches the fail-closed fallthrough and is
    # denied under AUTONOMOUS with a message telling the agent to use a tool this
    # build recognises. Whether that denial is intended is open per name and is
    # not this test's decision -- see ``deferred-tool-audit``. ``strict=True`` so
    # that classifying one of them turns this red and forces the marker out
    # rather than letting it outlive the question it names.
    *(
        pytest.param(
            name,
            marks=pytest.mark.xfail(
                strict=True,
                reason=f"{name} is unclassified; whether AUTONOMOUS should deny it is open",
            ),
        )
        for name in ("ScheduleWakeup", "ExitPlanMode", "Skill")
    ),
)


@pytest.mark.parametrize("tool", _HARNESS_TOOLS)
def test_no_tool_this_harness_offers_waits_for_an_operator_under_autonomy(tool: str) -> None:
    """
    Under ``AUTONOMOUS`` a ``REQUIRE_APPROVAL`` is not a park, it is a denial:
    ``driver._no_reviewer_reason`` turns it into one whose text says the build does
    not recognise the tool and to do the work with one it does. So for a tool the
    harness really offers, that answer is both a refusal and a false instruction --
    there is no other tool, and retrying cannot help.

    That makes the measurement here different from
    ``test_the_review_list_is_empty_under_the_autonomous_policy``, which asserts
    the same property over ``_known_tools()`` and therefore cannot fail for a name
    ``approval.py`` has never heard of. This one can, and that is its whole value.
    """
    assert classify(tool, {}, Policy.AUTONOMOUS) is not Disposition.REQUIRE_APPROVAL


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


# -- diff line caching ---------------------------------------------------------


def test_a_whole_file_write_produces_a_large_diff(tmp_path: Path) -> None:
    """
    Motivates the per-item line cache and the clipper in the review pane: the diffs
    an operator spends longest reading are exactly the ones that would cost most to
    re-split and re-emit every frame.
    """
    target = tmp_path / "big.py"
    target.write_text("".join(f"old {i}\n" for i in range(2000)))
    diff = render_diff("Write", {"file_path": str(target), "content": "new\n"})
    assert diff is not None
    assert len(diff.splitlines()) > 1000


def test_the_reducers_writing_tool_set_matches_summarizes_own_branch() -> None:
    """
    ``model.WRITING_TOOLS`` is a deliberate copy of the tuple ``summarize`` branches
    on: the reducer's module graph stops at ``model`` and ``approval`` reads the
    disk, so the set cannot be imported from here. Deliberate duplication is only
    safe while something fails when the two drift, and this is that something.

    A tool added to ``summarize`` and not to ``WRITING_TOOLS`` writes a file that is
    measured as no write at all -- silently, and in the direction that makes an
    agent look compliant.
    """
    tree = ast.parse(Path(approval.__file__).read_text(), filename=approval.__file__)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "summarize")
    branches = [
        {e.value for e in cmp.comparators[0].elts if isinstance(e, ast.Constant)}
        for cmp in ast.walk(fn)
        if isinstance(cmp, ast.Compare)
        and isinstance(cmp.ops[0], ast.In)
        and isinstance(cmp.left, ast.Name)
        and cmp.left.id == "tool_name"
        and isinstance(cmp.comparators[0], ast.Tuple)
    ]
    writing = [b for b in branches if "Write" in b]
    assert len(writing) == 1, "summarize's path branch is no longer a single tuple"
    assert writing[0] == set(WRITING_TOOLS)


def test_every_writing_tool_summarizes_to_a_path_the_reducer_can_also_read() -> None:
    """
    The behavioural half of the pin above: the row an operator reads and the path
    the reducer measures come from the same argument, so a tool whose path lives
    under a third key cannot summarize fine and measure to nothing.

    Swept over both keys rather than only the declared one, which is the whole
    claim: the row names a path exactly when the ledger reads one. Asking each tool
    only about the key it declares would pass against two functions that had
    stopped agreeing everywhere else, and they did stop -- ``written_path`` moved to
    per-tool selection to close the decoy and this one had to move with it.
    """
    for tool in WRITING_TOOLS:
        for key in ("file_path", "notebook_path"):
            args = {key: "pptmstr/store.py"}
            measured = written_path(tool, args)
            row = summarize(tool, args)
            if measured is None:
                assert row == f"{tool} ?", (tool, key)
            else:
                assert row == f"{tool} {measured}", (tool, key)


def test_the_queue_row_names_the_file_the_tool_will_act_on_not_the_decoy() -> None:
    """
    The adversarial half, and the one that says why the sweep above matters. A
    ``NotebookEdit`` carrying a harmless ``file_path`` and a real ``notebook_path``
    used to be summarized on the harmless one, so the operator approved a row
    describing a file the call would not touch.

    Under STRICT these four tools park, so this is the ordinary reviewed path with
    a human attached rather than anything about the unattended mode.
    """
    decoyed = {"file_path": "/tmp/harmless.txt", "notebook_path": "/etc/evil.ipynb"}

    assert summarize("NotebookEdit", decoyed) == "NotebookEdit /etc/evil.ipynb"
    assert summarize("Write", decoyed) == "Write /tmp/harmless.txt"
    assert written_path("NotebookEdit", decoyed) == "/etc/evil.ipynb"
    assert written_path("Write", decoyed) == "/tmp/harmless.txt"
