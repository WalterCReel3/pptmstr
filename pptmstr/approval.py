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

from .shellscan import is_read_only


class Disposition(enum.Enum):
    AUTO_APPROVE = "auto_approve"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class Policy(enum.Enum):
    """
    How much a session's gate admits without an operator.

    An enum rather than a ``bool research_mode`` because the recorded preference
    (2026-08-11 §"Two corrections", point 2) is to build the general shape -- a
    policy value on the session -- and ship presets over it, so that a second
    preset is a member here rather than a retrofit around a boolean.

    The members are rungs on a ladder of postures (2026-08-22; 2026-09-03 §7),
    not alternatives. A rung names the posture and never the permission, and none
    is named "read-only" or "safe": mutation and egress are independent axes, and
    one reassuring word over both is what lets something mutation-free and
    egress-positive onto an allowlist without a reviewer noticing (2026-08-11
    §"The reframe"). A posture name cannot carry a scope, so each rung states its
    own.

    A parameter of ``classify`` rather than module state: this module's purity is
    the reason the dial is built over the gate at all, and a mode that worked by
    mutating a global would make every test here order-dependent.

    **The two widened rungs rest on different things, and neither subsumes the
    other.** ``PERMISSIVE`` rests on a syntactic claim about the command -- it is
    decidable, fail-closed, and needs no containment, which is why it is the rung
    that survives where a sandbox breaks the work. ``AUTONOMOUS`` abandons the
    claim about the command entirely and rests on a bound around the process, so
    it cannot start without one. That is why the ladder is ordered by how much is
    released and not by how much is trusted.
    """

    # Today's behaviour, and the default: everything off the standing allowlist
    # waits for an operator.
    STRICT = "strict"
    # Adds shellscan-passing `Bash`, and nothing else. Writes, spawns, messages,
    # `WebFetch`, `WebSearch` and unknown tools park as they do under `STRICT`.
    # Pinned row by row by test_the_corpus_under_permissive.
    #
    # Egress stays denied because `WebFetch` pairs with an admitted `cat` of any
    # absolute path into an unattended read-then-send. Context still reaches the
    # API, as it does under `STRICT` (2026-09-03 §8b.8).
    #
    # Deliberately not paired with containment. What the sandbox bounds is writes
    # and network, which this rung already refuses syntactically; what it does not
    # bound is reads, which is the only axis this rung opens -- and `_AUTO` already
    # auto-approves `Read` and `Grep` against any path at every policy, so an
    # admitted `cat` reaches nothing `STRICT` did not already reach (2026-09-03
    # §11 U8). A sandbox here would cost the operator working invocations and buy
    # the gate nothing.
    #
    # Rungs above this one carry their warning in their own name.
    PERMISSIVE = "permissive"
    # Named for the mode rather than for a tool, because what it releases is the
    # whole of ``_REVIEW``: under it the set of calls that would have waited for a
    # human is empty, and the only thing still reaching ``REQUIRE_APPROVAL`` is a
    # tool nobody has heard of.
    #
    # Containment under it is no longer the allowlist's width. ``Bash`` and its
    # children are bounded by the CLI's sandbox; the write tools run inside the CLI
    # process, which that sandbox does not cover, and are bounded instead to the
    # session's directory by the driver's gate. The two halves are one decision --
    # widening here without that write-region check leaves the CLI-process writers
    # unbounded, which is why `driver.AgentSession` refuses to start under this rung
    # with no containment configured. ``WebFetch``/``WebSearch`` are bounded by
    # neither, and are released anyway: what a URL and a prompt can carry out is
    # small beside an unattended agent that cannot read documentation.
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
# approved, and nothing had ever approved it. The ones that remain are the ones the
# premise actually holds for, in one of two shapes: bookkeeping about a task whose
# existence the operator decided at declaration, or -- `read_inbox` -- reading
# messages that were already reviewed at the send.
_BUS_AUTO = frozenset(
    {
        f"mcp__{_BUS_SERVER}__read_inbox",
        f"mcp__{_BUS_SERVER}__read_board",
        f"mcp__{_BUS_SERVER}__claim_task",
        f"mcp__{_BUS_SERVER}__complete_task",
        f"mcp__{_BUS_SERVER}__release_task",
    }
)


# The whole of ``PERMISSIVE``'s widening: one tool, decided per command
# (2026-08-11 §1). There is no by-name admission set for this rung, so admitting a
# tool outright takes a visible branch here rather than a name appended to a
# frozenset.
#
# `Task`/`Agent` are absent deliberately, and what used to be a wait is now a live
# distinction between the rungs. Inheriting a relaxed gate through a spawn would let
# one approval relax an unbounded number of downstream calls (2026-08-11 §4).
# 2026-09-03 §8 reverses that, on the premise that a sub-agent shares the parent's
# sandbox and so has the same bounded reach. That premise is built now -- but it is
# containment, and this rung deliberately has none, so the reversal does not reach it
# (2026-09-17 §4). ``inherits_to_subagents`` draws the same boundary once, for the
# driver.
def _permissive_admits(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """
    Whether ``PERMISSIVE`` admits a call that ``STRICT`` would park.

    Answers only about the widening. Everything ``_AUTO`` and ``_BUS_AUTO``
    already admit is decided before this is reached, and everything this
    returns False for falls through to the unchanged classification.
    """
    if tool_name != "Bash":
        return False
    command = tool_input.get("command")
    # A `Bash` call whose command is absent or is not a string is a call the
    # table has not read, so there is nothing to admit. Coercing it with `str()`
    # would classify the repr rather than the command that runs.
    return isinstance(command, str) and is_read_only(command)


def _policy_admits(policy: Policy, tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """
    Whether ``policy`` widens the gate to admit a call ``STRICT`` would park.

    A predicate rather than the frozenset of tool names this returned while there
    was one widened rung, because the two rungs no longer answer the same kind of
    question. ``AUTONOMOUS`` releases a set of tool *names*; ``PERMISSIVE``
    releases one name conditional on its *argument*, and a set of names cannot
    carry that condition. Asking "does this policy admit this call" is the one
    question both can answer, so it is the one each rung answers here.

    Still a ``match`` closed by ``assert_never``, which is the property worth
    keeping from the set-returning version: adding a ``Policy`` member and
    forgetting to say what it releases is a type error at this line rather than a
    ``KeyError`` raised on the gate path of a running session.
    """
    match policy:
        case Policy.STRICT:
            return False
        case Policy.PERMISSIVE:
            return _permissive_admits(tool_name, tool_input)
        case Policy.AUTONOMOUS:
            return tool_name in _AUTONOMOUS_AUTO
    assert_never(policy)


def requires_containment(policy: Policy) -> bool:
    """
    Whether a session may not start under this rung without a containment settings blob.

    The ladder's cap, expressed as a property of the rung rather than as a name
    checked at the one place that enforces it. A rung releases writes and spawns
    only by taking this on: the allowlist stops being what bounds the session, so
    something else has to be, and the sandbox is the only other thing there is.

    ``test_a_rung_that_releases_writes_pays_for_it_with_containment`` is what makes
    that a rule rather than a description -- it reads both answers off this module,
    so a fourth rung that released writes and returned False here would fail before
    it could put a reassuring line on the launcher.

    ``PERMISSIVE`` returns False and is not an oversight. What the sandbox bounds is
    writes and network, which that rung already refuses syntactically; what it does
    not bound is reads, which is the only axis that rung opens. Requiring one would
    cost the operator working invocations -- an X server connection, a venv
    interpreter -- and buy the gate nothing (2026-09-03 §11 U8).
    """
    match policy:
        case Policy.STRICT:
            return False
        case Policy.PERMISSIVE:
            return False
        case Policy.AUTONOMOUS:
            return True
    assert_never(policy)


def inherits_to_subagents(policy: Policy) -> bool:
    """
    Whether a sub-agent is gated by its session's policy or falls back to ``STRICT``.

    A property of the rung, so it lives beside the rung rather than in the driver:
    it is decided by what the rung rests on, and ``assert_never`` makes a third
    widened rung say which answer it takes.

    ``AUTONOMOUS`` inherits. 2026-09-03 §8 reverses 2026-08-11 §4 for it, because
    sandbox configuration is per CLI process and a sub-agent shares its parent's --
    so each additional agent has the same bounded reach as the first, and what
    fan-out multiplies is volume, which ``subagent_cap`` bounds ahead of
    ``classify``.

    ``PERMISSIVE`` does not, and §4 stands for it unchanged. Its safety is a claim
    about a command string, not a bound around a process, and nothing about the
    parent's gate follows a spawn into a child. Inheriting it would be §4's original
    hazard exactly: one approval relaxing an unbounded number of downstream calls.
    """
    match policy:
        case Policy.STRICT:
            return False
        case Policy.PERMISSIVE:
            return False
        case Policy.AUTONOMOUS:
            return True
    assert_never(policy)


def classify(
    tool_name: str,
    tool_input: Mapping[str, Any],
    policy: Policy = Policy.STRICT,
) -> Disposition:
    """
    Whether a tool call may run unattended, under the caller's policy.

    Under ``STRICT`` and ``PERMISSIVE``, ``Task``/``Agent`` require approval
    deliberately: spawning a sub-agent is a tool call like any other, and an
    orchestrator that gates writes but not the spawning of things that write has a
    hole in it. Under ``AUTONOMOUS`` they auto-approve, because a sub-agent runs in
    the same CLI process under the same sandbox and so has the same bounded reach as
    the first agent; total fan-out is bounded by ``subagent_cap``, whose deny sits
    ahead of this function in ``driver._gate_tool_use`` and which no policy can
    widen.

    ``policy`` is a parameter and not module state, so that a session running
    relaxed cannot change what a concurrent session is gated by, and so that no
    test in this module's suite becomes order-dependent (2026-08-11 §1).

    The policy defaults to ``STRICT``, which is what makes adoption free: a call
    site passing two positional arguments gets exactly today's answers.

    A policy widens the allowlist and nothing else. It may only add *above* the
    ``_REVIEW`` check and may not touch the final ``REQUIRE_APPROVAL``, which is
    reached at every policy: the tool this build has never heard of is the one that
    must not run unreviewed, whatever the operator asked for (2026-09-03 §6.1).
    Under ``AUTONOMOUS`` that final arm is the only way to reach
    ``REQUIRE_APPROVAL`` at all.
    """
    if tool_name in _AUTO or tool_name in _BUS_AUTO:
        return Disposition.AUTO_APPROVE
    if _policy_admits(policy, tool_name, tool_input):
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
