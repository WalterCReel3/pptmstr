"""
Classification and presentation for the approval gate.

Pure functions: no SDK, no UI, no IO except reading the file an edit would change.
Everything here decides *whether* a tool call needs a human and *what the human
should be shown* -- the parking and awaiting live in the driver and the Bridge.

The classification rule is fail-closed. An unrecognised tool requires approval,
because the tool this orchestrator has never heard of is precisely the one that
should not run unreviewed, and an allowlist that defaults open stops being an
allowlist the first time the SDK adds a tool.
"""

from __future__ import annotations

import difflib
import enum
from collections.abc import Mapping
from pathlib import Path
from typing import Any, assert_never


class Disposition(enum.Enum):
    AUTO_APPROVE = "auto_approve"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class Policy(enum.Enum):
    """
    Which of the two allowlists a call is measured against.

    A parameter of ``classify`` rather than module state: this module's purity is
    the reason the dial is built over the gate at all, and a mode that worked by
    mutating a global would make every test here order-dependent.

    ``AUTONOMOUS`` is named for the mode rather than for a tool, because what it
    releases is the whole of ``_REVIEW``: under it the set of calls that would
    have waited for a human is empty, and the only thing still reaching
    ``REQUIRE_APPROVAL`` is a tool nobody has heard of.

    Containment under it is no longer the allowlist's width. ``Bash`` and its
    children are bounded by the CLI's sandbox; the write tools run inside the CLI
    process, which that sandbox does not cover, and are bounded instead to the
    session's directory by the driver's gate. The two halves are one decision --
    widening here without that write-region check leaves the CLI-process writers
    unbounded. ``WebFetch``/``WebSearch`` are bounded by neither, and are released
    anyway: what a URL and a prompt can carry out is small beside an unattended
    agent that cannot read documentation.
    """

    STRICT = "strict"
    AUTONOMOUS = "autonomous"


# The bus server's name, spelled here rather than imported from pptmstr.bus:
# approval.py is deliberately free of SDK imports so the policy can be tested and
# reasoned about on its own, and bus.py pulls in claude_agent_sdk. The pair is
# pinned by a test rather than by an import.
_BUS_SERVER = "pptmstr"
_BUS_POST = f"mcp__{_BUS_SERVER}__post_concern"
_BUS_DECLARE = f"mcp__{_BUS_SERVER}__declare_task"

# Reads, searches and listings. Cheap, reversible, and they do not leave the box.
_AUTO = frozenset(
    {
        "Read",
        "Glob",
        "Grep",
        "NotebookRead",
        "TodoWrite",
        "ListMcpResources",
        "ReadMcpResource",
        # A registry read. ``ToolSearch`` returns the JSON schema of a tool this
        # session already holds; it calls nothing and changes nothing outside the
        # model's context, which is what ``_AUTO``'s first sentence asks for.
        #
        # What it makes callable is judged on its own name and not on this one. The
        # driver registers ``PreToolUse`` with no ``HookMatcher.matcher``, so the
        # gate fires on every tool call, and ``driver._gate_tool_use`` reads the name
        # off the hook payload and consults no record of which schemas were loaded.
        # A schema loaded here therefore reaches ``classify`` exactly as if it had
        # been offered up front -- under ``STRICT`` a loaded ``WebFetch`` still
        # parks, and a loaded tool this build has never named still falls through
        # fail-closed.
        #
        # It is not the ``ReadMcpResource``/``ListMcpResources`` case, which
        # driver.py records as a wart rather than a pattern: those return *data*
        # from a server this process cannot enumerate, so their answer is never
        # classified. This returns a name the gate will judge before it runs.
        #
        # The live consequence, which is a reason to keep ``_AUTO`` narrow rather
        # than a reason to refuse the loader: a deferred tool is unreachable while
        # this is denied, so admitting it makes whatever ``_AUTO`` already holds
        # reachable in fact rather than only on paper.
        "ToolSearch",
    }
)

# Mutating, or reaching the network. Named explicitly so the list reads as a
# decision rather than as whatever happened to be left over. Each reason below is
# a reason to put the call in front of the operator, so each holds exactly while
# there is one: ``AUTONOMOUS`` releases this whole list.
_REVIEW = frozenset(
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
        # A message between agents is reviewed for the same reason a write is: it
        # changes what another agent does next. Gating the *send* rather than the
        # read is what gives rejection somewhere to go -- permissionDecisionReason
        # reaches the agent that wrote the message and could revise it, whereas the
        # recipient could only be told about a message it never saw (§2.7).
        #
        # Nothing new is needed to review one. A parked post_concern is an ordinary
        # PendingApproval: it queues by wait time with everything else, and
        # edit-then-approve rewrites the body through updatedInput (§5.3).
        _BUS_POST,
        # A declaration is where work comes into existence, so it is where the
        # decision belongs. Everything downstream -- which agent claims it, how many
        # run at once, what each writes -- is bookkeeping about work whose existence
        # was never in question, and everything upstream is a lead thinking, which
        # is free.
        #
        # The recorded cost is real and was accepted with the unit: on the baseline
        # run this fires once per declaration, which was six times inside five
        # minutes at a moment when the operator had said one line. The alternatives
        # were a budget set at launch and a per-plan gate, and both were declined.
        #
        # It reuses the same machinery post_concern does, and gains something
        # post_concern does not need: editing a parked declaration through
        # updatedInput rewrites `detail`, `depends_on` and `touches` before the task
        # lands. That is what makes this a scoping moment rather than only a binary
        # one -- the operator sets the size, not just the yes.
        _BUS_DECLARE,
    }
)

# What the autonomous policy releases is the review list itself. Written as that
# identity rather than as a set that happens to enumerate the same names: the
# property the mode needs is that nothing under it waits for a human, and a copy
# would go quietly false the first time a tool joins ``_REVIEW`` -- which is the
# moment the mode would start parking again with nothing in the repository
# disagreeing. The identity is asserted through ``classify`` in
# tests/test_approval.py, so the branch order in ``classify`` is covered with it.
_AUTONOMOUS_AUTO = _REVIEW

# Coordination that reads or reserves, but does not reach another agent or the
# world. Auto-approving these is what keeps the operator a bottleneck on decisions
# rather than on bookkeeping -- a worker taking the next item off a board the
# operator already approved is not a second decision.
#
# That sentence is the whole of the rule and it is why `declare_task` is no longer
# in this set: it was auto-approved on the premise that the board had already been
# approved, and nothing had ever approved it. The four that remain are the ones the
# premise actually holds for -- each is bookkeeping about a task whose existence is
# now a decision the operator made at declaration.
_BUS_AUTO = frozenset(
    {
        f"mcp__{_BUS_SERVER}__read_inbox",
        f"mcp__{_BUS_SERVER}__read_board",
        f"mcp__{_BUS_SERVER}__claim_task",
        f"mcp__{_BUS_SERVER}__complete_task",
        f"mcp__{_BUS_SERVER}__release_task",
    }
)


def _policy_auto(policy: Policy) -> frozenset[str]:
    """
    The extra allowlist a policy adds beside ``_AUTO``, never in place of it.

    A ``match`` rather than a dict lookup so that adding a ``Policy`` member and
    forgetting to say what it releases is a type error here rather than a
    ``KeyError`` raised on the gate path of a running session.
    """
    match policy:
        case Policy.STRICT:
            return frozenset()
        case Policy.AUTONOMOUS:
            return _AUTONOMOUS_AUTO
    assert_never(policy)


def classify(
    tool_name: str,
    tool_input: Mapping[str, Any],
    policy: Policy = Policy.STRICT,
) -> Disposition:
    """
    Whether a tool call may run unattended.

    Under ``STRICT``, ``Task``/``Agent`` require approval deliberately: spawning a
    sub-agent is a tool call like any other, and an orchestrator that gates writes
    but not the spawning of things that write has a hole in it. Under
    ``AUTONOMOUS`` they auto-approve, because a sub-agent runs in the same CLI
    process under the same sandbox and so has the same bounded reach as the first
    agent; total fan-out is bounded by ``subagent_cap``, whose deny sits ahead of
    this function in ``driver._gate_tool_use`` and which no policy can widen.

    The policy defaults to ``STRICT``, which is what makes adoption free: both
    call sites pass two positional arguments and get exactly today's answers.

    A policy widens the allowlist and nothing else. The final ``REQUIRE_APPROVAL``
    is reached at every policy, because a mode that is dangerous by choice is
    still not a mode that admits tools nobody has seen. Under ``AUTONOMOUS`` it is
    the only way to reach ``REQUIRE_APPROVAL`` at all.
    """
    if tool_name in _AUTO or tool_name in _BUS_AUTO:
        return Disposition.AUTO_APPROVE
    if tool_name in _policy_auto(policy):
        return Disposition.AUTO_APPROVE
    if tool_name in _REVIEW:
        return Disposition.REQUIRE_APPROVAL
    # Unknown, including every MCP tool this build has never seen.
    return Disposition.REQUIRE_APPROVAL


def summarize(tool_name: str, tool_input: Mapping[str, Any], width: int = 90) -> str:
    """
    One line naming what the call would do. This is the queue row.

    Reads as an action rather than as a serialised argument dict, because the queue
    is scanned rather than read -- the operator is deciding which item to look at,
    not deciding the item.

    **The path comes from the key the tool declares**, which is the same rule
    ``model.written_path`` applies and has to be the same rule. This is the row a
    human approves from and that one is what the ledger measures and what the gate
    bounds, so a call carrying both keys must not be able to show one path here and
    act on the other -- a decoy that fools the reviewer is worse than one that only
    fools the measurement, because the reviewer is what the measurement is for.

    ``"?"`` when the declared key is absent, and that is the honest row rather than
    a gap: the other key being filled in does not make it the target, and a call
    whose destination cannot be read is one the operator should open.
    """

    def clip(text: str, limit: int = width) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 3] + "..."

    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        declared = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
        path = str(tool_input.get(declared) or "?")
        return clip(f"{tool_name} {path}")
    if tool_name == "Bash":
        return clip(str(tool_input.get("command") or ""))
    if tool_name in ("WebFetch", "WebSearch"):
        return clip(f"{tool_name} {tool_input.get('url') or tool_input.get('query') or ''}")
    if tool_name in ("Task", "Agent"):
        subtype = tool_input.get("subagent_type") or "agent"
        return clip(f"spawn {subtype}: {tool_input.get('description') or ''}")
    if tool_name == _BUS_POST:
        # The row an operator scans to decide whether to open it, so it leads with
        # who is being told what. The body is the diff-equivalent and belongs in the
        # detail pane, not here.
        to = tool_input.get("to") or "?"
        subject = str(tool_input.get("subject") or "").strip()
        return clip(f"message {to}: {subject or tool_input.get('body') or ''}")
    if tool_name == _BUS_DECLARE:
        # The question at this row is "should this work exist", so it leads with the
        # claim. The specification is the diff-equivalent and belongs in the detail
        # pane; what rides along here is the *size* -- how many files it will write
        # and whether it is sequenced behind anything -- because sign-off is a
        # scoping moment and those two are what the operator would otherwise open
        # the row to find.
        title = str(tool_input.get("title") or "").strip()
        parts = [f"declare {title or '(untitled)'}"]
        touches = tool_input.get("touches") or ()
        if touches:
            parts.append(f"writes {', '.join(str(p) for p in touches)}")
        depends = tool_input.get("depends_on") or ()
        if depends:
            parts.append(f"after {', '.join(str(d) for d in depends)}")
        return clip(" · ".join(parts))
    if not tool_input:
        return tool_name
    key = next(iter(tool_input))
    return clip(f"{tool_name} {key}={tool_input[key]!r}")


def render_diff(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    """
    A unified diff of what the call would change, or None when there is nothing
    diff-shaped to show.

    Returning None is meaningful rather than a failure: a Bash command has no diff,
    and inventing one would be worse than the summary the caller already has. The
    detail pane branches on it.

    Reads the current file from disk so the diff is against what is actually there
    -- an Edit's own ``old_string`` is what the model *believes* is there, and the
    difference between those two is exactly what review is for.
    """
    if tool_name == "Write":
        path = str(tool_input.get("file_path") or "")
        new = str(tool_input.get("content") or "")
        return _unified(_read(path), new, path)

    if tool_name == "Edit":
        path = str(tool_input.get("file_path") or "")
        old_string = str(tool_input.get("old_string") or "")
        new_string = str(tool_input.get("new_string") or "")
        current = _read(path)
        if current is not None and old_string and old_string in current:
            replaced = current.replace(
                old_string, new_string, -1 if tool_input.get("replace_all") else 1
            )
            return _unified(current, replaced, path)
        # The file is unreadable, or the anchor is not in it. Show the intended
        # replacement rather than nothing -- "this edit will not apply" is itself
        # something the operator wants to see before approving.
        return _unified(old_string, new_string, path or "(anchor not found in file)")

    if tool_name == "MultiEdit":
        edits = tool_input.get("edits")
        if not isinstance(edits, list):
            return None
        parts = []
        for i, edit in enumerate(edits, 1):
            if not isinstance(edit, dict):
                continue
            parts.append(
                _unified(
                    str(edit.get("old_string") or ""),
                    str(edit.get("new_string") or ""),
                    f"edit {i}",
                )
            )
        return "\n".join(p for p in parts if p) or None

    return None


def _read(path: str) -> str | None:
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Missing file is the common case for Write, and it is not an error --
        # the diff is then simply "everything is new".
        return None


def _unified(before: str | None, after: str, label: str) -> str:
    # The a/ b/ prefixes are a git convention for repo-relative paths; on an
    # absolute path they produce "b//tmp/x", which reads as a typo in the header of
    # every diff the operator sees.
    prefix_a, prefix_b = ("", "") if label.startswith("/") else ("a/", "b/")
    lines = difflib.unified_diff(
        (before or "").splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{prefix_a}{label}" if before is not None else "/dev/null",
        tofile=f"{prefix_b}{label}",
        n=3,
    )
    return "".join(lines)


def diff_line_kind(line: str) -> str:
    """
    Classify a diff line for styling: 'add', 'remove', 'meta' or 'context'.

    The caller keeps the leading +/- in the rendered text regardless of colour --
    that gutter is the non-hue channel, and it is what keeps a diff readable in
    high contrast and to a colour-deficient operator (design §6.1).
    """
    if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
        return "meta"
    if line.startswith("+"):
        return "add"
    if line.startswith("-"):
        return "remove"
    return "context"
