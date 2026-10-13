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

from .commandtext import visible
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
    # Deliberately not paired with containment, and the reason is a cost
    # judgement rather than a claim that containment buys nothing. What the
    # sandbox bounds is writes and network, which this rung already refuses
    # syntactically. On the read axis it adds exactly one thing: a deny at
    # `sandbox.DENIED_CREDENTIAL_FILES`' four prefixes, since upstream's sandbox
    # read policy is the rest of the computer. Four paths against a rung whose
    # read reach is the filesystem, and whether that key is honoured at all is
    # unmeasured -- 2026-09-03 §11 U1's residue, with
    # `scripts/verify_read_bound.py`'s K arms as the instrument and no recorded
    # run. Against that sits a real operator cost: an X server connection and a
    # venv interpreter both break under the sandbox, which is what this rung
    # exists to survive.
    #
    # The two routes do not share a downstream enforcer, so no parity argument
    # is available here. Measured 2026-09-23: the CLI's `Read` refuses
    # `/proc/self/environ` outright while an admitted `cat` returns it, so this
    # rung reaches bytes `STRICT` cannot (2026-09-03 §12 U11). That is a reach
    # containment would not close either -- the sandbox's read policy covers
    # `/proc` -- which is why it is recorded rather than answered by flipping
    # `requires_containment`.
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
_BUS_AMEND = f"mcp__{_BUS_SERVER}__amend_task"

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
        # An amendment is a declaration made a second time: it sets what an agent
        # is held to, so it meets whatever a declaration meets on the same rung,
        # by being in the same set. Parked, it is edited through updatedInput like
        # one, and the handler drops the amender's basis from an edited call.
        #
        # Under ``AUTONOMOUS`` that is no review at all. This whole set is
        # released there, so an agent's amendment lands unseen, as its declaration
        # did. What stands on that rung is the reducer's rule and not this gate:
        # only the agent that declared a task and never held it may amend it, it
        # may not then claim it, and the text it replaced is kept as a version
        # (``store._amender_refusal``, ``store._claim_refusal``).
        _BUS_AMEND,
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
#
# **"Decided at declaration" and "reviewed at the send" are true of the rungs that
# park those calls, and only of them.** `declare_task`, `amend_task` and
# `post_concern` are in ``_REVIEW``, which ``STRICT`` and ``PERMISSIVE`` hold and
# ``AUTONOMOUS`` releases whole. A specification an agent amended was approved at
# the amendment as it was at the declaration, and "at declaration" below covers
# both. Under ``AUTONOMOUS`` no operator saw the specification or the concern, and
# every read below hands on text nobody reviewed. That is the rung's stated cost and
# not a hole in this set: the reads carry exactly as much review as the writes were
# given, and wherever "approved" or "reviewed" is said below it means on a rung that
# asks.
#
# `read_task` is `read_board` for one row, with the texts a board read cuts, and
# is here on `read_board`'s grounds exactly: it returns a specification approved
# at its declaration and notes reviewed at their send, scoped to the caller's own
# board.
#
# **`release_task` is bookkeeping on the caller's own claim, with one exception
# that rides the same auto-approved name on every rung, ``STRICT`` included.** The
# root of a session may release a claim that is not its own when the holder is an
# agent of an earlier run of that session (`store._may_reclaim`): it puts another
# agent's task back on the board, and no operator is asked. It is here because
# what it undoes is a claim nothing running will ever finish or give back, it
# gives the root nothing but the task's return to PENDING -- the root cannot
# complete it in the holder's place -- and parking it would leave the row stuck
# until somebody answered. Whether the root should have that release at all is
# question 6 of
# `planning/2026-10-12-session-control-and-the-inter-team-record-in-one-sequence.md`,
# which the operator has not answered; it is built as the lead recommended there,
# and taking the two lines out of the reducer's `TaskReleased` arm is the whole
# of the other answer.
#
# `read_digest` is here on those grounds too, and by leaving one thing out. Three of
# its slots are the caller's own board again: tasks approved at declaration,
# results as `read_board` already shows them, concerns reviewed at their send. The
# fourth, what is waiting on the operator, is drawn from the approval queue --
# calls nobody has approved yet -- so the tool reports who is waiting and for what
# kind of thing and never the queue row (`bus._digest_text`). With the row in it
# this would be a read that hands one agent another's unreviewed message.
#
# **`complete_task` is where the premise is thinnest on every rung, and it is stated
# rather than hidden.** A completion may carry two pieces of prose, a `result` and a
# `not_run` list, and the board then shows both to every agent on it. Neither is
# parked under any policy, so they are the prose on the board that no operator saw
# before it landed even where every declaration and every concern was reviewed. It
# stays here because what the gate would be deciding is whether work already done
# may be recorded as done, and a parked or refused completion strands a claim and
# everything waiting on it. Both are rendered as the claimer's own account for that
# reason, and quoted line by line so neither can be read as a line the board wrote
# (`bus._board_line`, `bus._quoted`).
#
# **The three decision tools are here because a decision is addressed to the
# operator, and one of them is not private to the operator.** `raise_decision` puts
# its text in the operator's own queue, and parking it would put a message for the
# operator in front of the operator to ask whether the operator may see it: the
# queue row is the review. `withdraw_decision` removes the asker's own row.
#
# The text has a second reader, and it is an agent. The root of a session reads
# every decision raised in it (`model.Snapshot.decisions_readable_by`), and
# `read_decisions` hands it the question, what happens if it goes unanswered, the
# evidence and the recommendation whole -- up to four texts of
# `model.MAX_DECISION_TEXT_CHARS` that a sub-agent wrote and no operator approved
# before the lead could read them. So on the rungs that park `post_concern`, a
# sub-agent's `raise_decision` is words to its lead that are not parked. It is the
# second such text on the board beside `complete_task`'s result, and it is held
# the same way: quoted line by line behind the asker's name, never as a line the
# tool wrote (`bus._decision_whole`), with the operator looking at the same words
# in the queue. No board or digest read shows a question (`bus._digest_text`), so
# the lead is the only agent it reaches. What bounds the three is the store: an
# agent may have `model.MAX_OPEN_DECISIONS_PER_ASKER` open, a session's sub-agents
# `model.MAX_OPEN_SUBAGENT_DECISIONS` between them, and only the operator's own
# intent ends one with an answer.
#
# **Both halves are decisions taken without the operator.** That a raise runs
# without sign-off is question 8, and that the session root reads its workers'
# decisions is question 4, of
# `planning/2026-10-12-session-control-and-the-inter-team-record-in-one-sequence.md`;
# the operator has answered neither, and each was built as recommended there. Put
# together they open the unparked channel above, which neither question asked
# about on its own. Moving `raise_decision` to ``_REVIEW`` closes it on the rungs
# that review a concern, and is the whole of the other answer to question 8;
# narrowing `decisions_readable_by` to the asker is the other answer to question 4.
#
# **The three command tools are here on the decision tools' grounds, and on every
# rung.** `request_command` asks the operator to run a command outside the
# sandbox. Its only reader before anything happens is the operator, in a row of
# its own kind, so parking it would put a request for the operator in front of
# the operator to ask whether the operator may see it; and the asker was told to
# carry on, which a parked call does not let it do. The row is the review:
# nothing runs on this call, and nothing runs at all until the operator presses
# twice on the exact string, which only an intent naming no node can start
# (`store._approved`). `withdraw_command` removes the asker's own row.
#
# `read_commands` hands text to a second reader, as `read_decisions` does: a
# session's root reads every command asked in it
# (`model.Snapshot.commands_readable_by`), so a sub-agent's command, reason and
# expectation reach its lead unparked, and so does the end of what the command
# printed -- bytes of a program the operator chose to run, which may be the
# contents of a file an agent wrote. Each is quoted behind a mark of its own
# and never as a line the tool wrote (`commandbus`), and what bounds them is the
# store: `command.MAX_OPEN_PER_ASKER` open for an agent and
# `command.MAX_OPEN_SUBAGENTS` across a session's sub-agents.
#
# Named here for every session and offered to a contained one alone: the server
# of a session with no containment does not have the three (`bus.build_server`),
# so on it these names admit a call that reaches no tool.
_BUS_AUTO = frozenset(
    {
        f"mcp__{_BUS_SERVER}__read_inbox",
        f"mcp__{_BUS_SERVER}__read_board",
        f"mcp__{_BUS_SERVER}__read_task",
        f"mcp__{_BUS_SERVER}__read_digest",
        f"mcp__{_BUS_SERVER}__claim_task",
        f"mcp__{_BUS_SERVER}__complete_task",
        f"mcp__{_BUS_SERVER}__release_task",
        f"mcp__{_BUS_SERVER}__raise_decision",
        f"mcp__{_BUS_SERVER}__read_decisions",
        f"mcp__{_BUS_SERVER}__withdraw_decision",
        f"mcp__{_BUS_SERVER}__request_command",
        f"mcp__{_BUS_SERVER}__read_commands",
        f"mcp__{_BUS_SERVER}__withdraw_command",
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

    ``PERMISSIVE`` returns False and is not an oversight, but it rests on a cost
    judgement rather than on containment being free of value. What the sandbox bounds
    is writes and network, which that rung already refuses syntactically. Reads are
    the axis it opens, and containment bounds those only at
    ``sandbox.DENIED_CREDENTIAL_FILES``' four prefixes -- narrow but not nothing, and
    itself unmeasured (2026-09-03 §11 U1). Set against an X server connection and a
    venv interpreter that stop working under the sandbox, the rung declines the trade
    and states its cost rather than claiming there is none.

    **The reach this leaves open is real and is recorded at §12 U11**: an admitted
    ``cat`` returns ``/proc/self/environ``, which the CLI's ``Read`` refuses. Flipping
    this to True would not close it, because the sandbox's read policy is the whole
    computer minus those four prefixes and ``/proc`` is not among them. So U11 is a
    reason to know what the rung reaches, not a reason to change this answer.
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

    Both widened rungs inherit, and they get there by different routes because they
    rest on different things.

    ``AUTONOMOUS`` inherits because sandbox configuration is per CLI process and a
    sub-agent shares its parent's, so each additional agent has the same bounded
    reach as the first (2026-09-03 §8, reversing 2026-08-11 §4 for that rung).

    ``PERMISSIVE`` inherits because its claim is per *command*: ``shellscan`` is
    re-run, fail-closed, against every call a sub-agent makes, exactly as it is for
    the root. Nothing about that verdict weakens with fan-out. 2026-08-11 §4 argued
    the other way on the grounds that one approval would relax the gate for an
    unbounded number of downstream calls, and that is no longer the shape of the
    tree: ``Task``/``Agent`` stay in ``_REVIEW`` under this rung, so every agent
    costs its own approval, and ``subagent_cap``'s deny runs ahead of ``classify``
    where no policy can widen it (2026-09-24, amending 2026-09-17 §4).

    What fan-out multiplies under either rung is volume, and the volume that matters
    here is calls that auto-approve and so construct no ``PendingApproval``: they are
    in the transcript but are not reviewable as decisions. ``subagent_cap`` is the
    only thing bounding that, since the automatic revoke was removed on 2026-09-21.

    A property of the rung, so it lives beside the rung rather than in the driver:
    it is decided by what the rung rests on, and ``assert_never`` makes a fourth rung
    say which answer it takes rather than inheriting one by position.
    """
    match policy:
        case Policy.STRICT:
            return False
        case Policy.PERMISSIVE:
            return True
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


def command_shown(tool_name: str, key: str, value: object) -> str | None:
    """
    A Bash call's command as every approval surface draws it, or None when
    ``key`` of ``tool_name`` is not one.

    ``commandtext.visible`` and nothing else: a newline is the two characters
    backslash-n followed by the break itself, so a line break in the command
    is never drawn as a space, and never as a break the pane could have made
    by wrapping. `ls # '` + newline + `touch pwned #'` is two commands to bash
    (``shellscan._scan``), and flattened to `ls # ' touch pwned #'` the second
    is comment text to the eye. Since a command carrying a comment parks, that
    row is the one the operator approves from.

    One function so that the queue row, the expanded row and the detail pane
    cannot each have a rule: ``summarize`` flattens this to one line, where
    the marker survives and the break does not, and the two panes draw it
    line for line.

    What it costs an ordinary command is ``visible``'s whole scheme rather
    than its newline rule alone. A backslash is drawn doubled, so
    `grep 'a\\.b' f` reads `grep 'a\\\\.b' f`; a tab is `\\x09`; a character
    outside ASCII is `\\u{...}`. A command with none of those is drawn as
    typed. Taking only the newline rule would leave a carriage return and a
    bidirectional override drawn as nothing, and a literal backslash-n
    indistinguishable from a marked break.
    """
    if tool_name != "Bash" or key != "command" or not isinstance(value, str):
        return None
    return visible(value)


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

    **A message, a declaration and an amendment say what their author said about
    them**, in the words the board will use once they land (``board.basis_label``
    and its neighbours, and ``model``'s reading of the raw arguments). This is the
    row the operator signs the text off from, so a standing the board shows
    afterwards and this row did not would be a claim approved unseen. The standing and the
    incident marker ride ahead of the subject or title because the row is clipped
    from the right and they are the short part. A declaration's property goes
    last for the opposite reason: it is a whole sentence that will rarely fit, and
    at the end a cut shows as the ellipsis it is, where in the middle it would
    push the size off the row and still not be whole. The detail pane lists every
    argument uncut.
    """
    # Imported here because ``model`` imports ``Policy`` from this module, so a
    # top-level import closes a cycle. Neither pulls in the SDK.
    from .board import basis_label, incident_marker, property_label
    from .model import Basis, normalised_property, stated_flag, stated_incident

    def standing(raw: object) -> str:
        try:
            return basis_label(Basis.stated(raw))
        except ValueError:
            # The handler refuses this call. Shown as written, so the operator
            # approving it knows the approval will not put anything on the board.
            return f"basis {str(raw)!r} is not one the bus accepts"

    def clip(text: str, limit: int = width) -> str:
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 3] + "..."

    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        declared = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
        path = str(tool_input.get(declared) or "?")
        return clip(f"{tool_name} {path}")
    if tool_name == "Bash":
        # The one row that is not a description of the call but the call, so it
        # is the one row where the flattening below could hide something: see
        # `command_shown`. The count leads because the row is clipped from the
        # right, and a second line past the cut must not read as a long first.
        shown = command_shown(tool_name, "command", str(tool_input.get("command") or "")) or ""
        lines = shown.count("\n") + 1
        return clip(shown if lines == 1 else f"{lines} lines · {shown}")
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
        marker = incident_marker(stated_incident(tool_input.get("incident")))
        return clip(
            f"message {to} ({standing(tool_input.get('basis'))}): "
            f"{marker}{subject or tool_input.get('body') or ''}"
        )
    if tool_name == _BUS_DECLARE:
        # The question at this row is "should this work exist", so it leads with the
        # claim. The specification is the diff-equivalent and belongs in the detail
        # pane; what rides along here is the *size* -- how many files it will write
        # and whether it is sequenced behind anything -- because sign-off is a
        # scoping moment and those two are what the operator would otherwise open
        # the row to find.
        title = str(tool_input.get("title") or "").strip()
        parts = [f"declare ({standing(tool_input.get('basis'))}) {title or '(untitled)'}"]
        touches = tool_input.get("touches") or ()
        if touches:
            parts.append(f"writes {', '.join(str(p) for p in touches)}")
        depends = tool_input.get("depends_on") or ()
        if depends:
            parts.append(f"after {', '.join(str(d) for d in depends)}")
        held_to = property_label(normalised_property(tool_input.get("property")))
        if held_to:
            parts.append(held_to)
        return clip(" · ".join(parts))
    if tool_name == _BUS_AMEND:
        # The question at this row is "may this replace what the task is held to".
        # Which task and how the amender stands behind the new text lead; what it
        # does to the property follows, because that sentence is the bar and an
        # amendment that moves or removes it is the one an operator most needs to
        # open. The text itself is the diff-equivalent, as a declaration's is.
        #
        # A basis is shown only beside a text it can end up beside. With no
        # ``detail``, or a blank one, the handler leaves the text standing and
        # the reducer keeps the basis its author gave it, so the amender's is
        # discarded: printed here, it would be a standing the operator signed
        # off and the board never shows. One the bus will not accept is still
        # shown, since that call is refused whatever else it carries.
        task = str(tool_input.get("task_id") or "?").strip()
        sent, raw = tool_input.get("detail"), tool_input.get("basis")
        said = standing(raw)
        if sent is None or not str(sent).strip():
            try:
                given = Basis.stated(raw)
            except ValueError:
                pass
            else:
                said = "its text stands" + (
                    "" if given is None else "; the basis given is not kept"
                )
        parts = [f"amend {task} ({said})"]
        held_to = property_label(normalised_property(tool_input.get("property")))
        if stated_flag(tool_input.get("withdraw_property")):
            parts.append("WITHDRAWS its property")
        elif held_to:
            parts.append(f"replaces its property -- {held_to}")
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
