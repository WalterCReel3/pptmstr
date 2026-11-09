"""
Whether a tool call may run unattended, under the caller's rung (``classify``).

The rule is fail-closed. An unrecognised tool requires approval, because the tool
this orchestrator has never heard of is precisely the one that should not run
unreviewed, and an allowlist that defaults open stops being an allowlist the first
time the SDK adds a tool.

No SDK and no UI. The disk it touches is ``gate.reach``'s: every call it would
admit by a reading or writing tool's name is first measured against the records
there.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, assert_never

from ..plugins import SKILLS as _SHIPPED_SKILLS
from ..shellscan import is_read_only
from .policy import Disposition, Policy
from .reach import records_read, records_written
from .tools import (
    AUTO,
    AUTONOMOUS_AUTO,
    BUS_AUTO,
    FILE_READS,
    REVIEW,
    TREE_READS,
    _dry_run,
    _reviewed_by_arguments,
)


def _shipped_skill(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """
    Whether this is a ``Skill`` call loading a skill pptmstr ships
    (``plugins.SKILLS``), which every rung admits (the operator's decision,
    2026-10-21; planning record §10.5).

    The skill is pptmstr's own text, from its own package, and loading it runs
    nothing. Any other skill keeps falling through: a user's or a project's skill
    may carry `` !`command` `` lines the CLI runs as it loads, and whether the
    ``PreToolUse`` hook sees those is unmeasured.

    The name sits at ``tool_input["skill"]`` (``Skill {"skill":
    "pptmstr:pptmstr-figures", "args": ...}``, recorded by the skill probe's live
    runs). It is compared whole against the shipped names rather than by its
    prefix, so ``pptmstr:../x``, ``pptmstr: x`` and a name with the prefix and no
    such skill all park as any other skill does.
    """
    if tool_name != "Skill":
        return False
    name = tool_input.get("skill")
    return isinstance(name, str) and name in _SHIPPED_SKILLS


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

    Answers only about the widening. Everything ``AUTO`` and ``BUS_AUTO``
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
            return tool_name in AUTONOMOUS_AUTO or _reviewed_by_arguments(tool_name, tool_input)
    assert_never(policy)


def classify(
    tool_name: str,
    tool_input: Mapping[str, Any],
    policy: Policy = Policy.STRICT,
    *,
    session_id: str | None = None,
    cwd: str | None = None,
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
    ``REVIEW`` check and may not touch the final ``REQUIRE_APPROVAL``, which is
    reached at every policy: the tool this build has never heard of is the one that
    must not run unreviewed, whatever the operator asked for (2026-09-03 §6.1).
    Under ``AUTONOMOUS`` that final arm, a read of another session's records and
    a write into any session's are the only ways to reach ``REQUIRE_APPROVAL``.

    **No rung admits a read of another session's records.** ``records_read`` is
    asked of the reading tools ahead of the allowlist, and of a ``Bash`` command
    under ``PERMISSIVE``, the one rung that admits a command on what it says. A
    call it names is not admitted: it takes whatever a call that needs approval
    takes on its rung, which is the operator where one is asked and a refusal
    where none is. ``session_id`` and ``cwd`` are passed through to it and mean
    what they mean there; left out, no directory under the records is the
    caller's own.

    ``Bash`` under ``AUTONOMOUS`` is not asked, because that rung does not read
    the command: what bounds it there is the sandbox, which denies the records
    directory whole (``gate.sandbox.denied_records``).

    **No rung admits a write into any session's records, the caller's own
    included.** ``records_written`` is asked of the four write tools ahead of
    everything else, and ``cwd`` is what a relative target is measured from. The
    driver's write bound cannot stand in for it: a session whose directory
    contains the configuration directory has the records inside its region.
    """
    if records_written(tool_name, tool_input, cwd=cwd) is not None:
        return Disposition.REQUIRE_APPROVAL
    reads = tool_name in FILE_READS or tool_name in TREE_READS
    if reads or (tool_name == "Bash" and policy is Policy.PERMISSIVE):
        if records_read(tool_name, tool_input, session_id=session_id, cwd=cwd) is not None:
            return Disposition.REQUIRE_APPROVAL
    reviewed_anyway = _reviewed_by_arguments(tool_name, tool_input)
    if (
        tool_name in AUTO
        or (tool_name in BUS_AUTO and not reviewed_anyway)
        or _dry_run(tool_name, tool_input)
    ):
        return Disposition.AUTO_APPROVE
    if _shipped_skill(tool_name, tool_input):
        return Disposition.AUTO_APPROVE
    if _policy_admits(policy, tool_name, tool_input):
        return Disposition.AUTO_APPROVE
    if tool_name in REVIEW or reviewed_anyway:
        return Disposition.REQUIRE_APPROVAL
    # Unknown, including every MCP tool this build has never seen.
    return Disposition.REQUIRE_APPROVAL
