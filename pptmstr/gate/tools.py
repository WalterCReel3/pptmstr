"""
The tool names the gate knows, and the tables that sort them.

``AUTO``, ``BUS_AUTO``, ``REVIEW`` and ``AUTONOMOUS_AUTO`` say which calls run
unattended on which rung (``gate.classify.classify``); ``FILE_READS`` and
``TREE_READS`` are the reading tools whose paths ``gate.reach.records_read``
measures. The predicates here read a tool's name and its arguments and nothing
else.

Imports nothing from pptmstr, and no SDK. ``model`` and the store read
``peer_channel``, so an edge into the package would close a cycle through them,
and the bus server's name is spelled here rather than imported from ``bus``
(``_BUS_SERVER``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# The bus server's name, spelled here rather than imported from pptmstr.bus:
# the gate is deliberately free of SDK imports so the policy can be tested and
# reasoned about on its own, and bus.py pulls in claude_agent_sdk. The pair is
# pinned by a test rather than by an import.
_BUS_SERVER = "pptmstr"
_BUS_POST = f"mcp__{_BUS_SERVER}__post_concern"
_BUS_DECLARE = f"mcp__{_BUS_SERVER}__declare_task"
_BUS_AMEND = f"mcp__{_BUS_SERVER}__amend_task"
_BUS_PEER = f"mcp__{_BUS_SERVER}__send_to_peer"
_BUS_DECIDE = f"mcp__{_BUS_SERVER}__raise_decision"
_BUS_WITHDRAW = f"mcp__{_BUS_SERVER}__withdraw_task"
_BUS_WITHDRAW_PEER = f"mcp__{_BUS_SERVER}__withdraw_peer_message"

# The two calls across a link that name their channel (``peer_channel``), by
# the names the gate is handed, for a reader that says which a parked call is.
# The third, a withdrawal, names none: ``records.WITHDRAW_PEER_TOOL``.
PEER_MESSAGE_TOOL = _BUS_PEER
PEER_DECISION_TOOL = _BUS_DECIDE

# Reads, searches and listings. Cheap, reversible, and they do not leave the box.
# The four that name a path are admitted by name and then measured by
# ``records_read``, which is where one of them stops being admitted.
AUTO = frozenset(
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
        # model's context, which is what ``AUTO``'s first sentence asks for.
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
        # The live consequence, which is a reason to keep ``AUTO`` narrow rather
        # than a reason to refuse the loader: a deferred tool is unreachable while
        # this is denied, so admitting it makes whatever ``AUTO`` already holds
        # reachable in fact rather than only on paper.
        "ToolSearch",
    }
)

# Mutating, or reaching the network. Named explicitly so the list reads as a
# decision rather than as whatever happened to be left over. Each reason below is
# a reason to put the call in front of the operator, so each holds exactly while
# there is one: ``AUTONOMOUS`` releases this whole list.
REVIEW = frozenset(
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
        # (``store.tasks._amender_refusal``, ``store.tasks._claim_refusal``).
        _BUS_AMEND,
        # A message to another team's lead is a message between agents, reviewed
        # for ``post_concern``'s reason, and gated by the *sender's* rung: each
        # side's own rung governs (the 2026-10-17 plan, Decided 1), so a lead on
        # a reviewed rung parks its send here whatever rung the other lead is on,
        # and work it asks for parks again at the other board's ``declare_task``.
        _BUS_PEER,
        # A withdrawal undoes a declaration: it ends work the operator approved
        # when it was declared, lets go of every task waiting on it, and turns
        # away the holder's result. So it meets what a declaration meets, and
        # parked it is one press for the operator where the alternative before it
        # existed was the operator finding the row and closing it by hand. What
        # stands under ``AUTONOMOUS`` is the reducer's rule: only while it is
        # unfinished, and only by the task's declarer, or by the root of the
        # declarer's session once the declarer has stopped -- a sub-agent whose
        # record is terminal, or an agent of an earlier run -- and no agent it
        # could not take the claim back from holds it
        # (``store.tasks._withdrawer_refusal``, ``store.tasks._has_stopped``,
        # ``store.tasks._may_take_claim``).
        _BUS_WITHDRAW,
    }
)

# What the autonomous policy releases is the review list itself. Written as that
# identity rather than as a set that happens to enumerate the same names: the
# property the mode needs is that nothing under it waits for a human, and a copy
# would go quietly false the first time a tool joins ``REVIEW`` -- which is the
# moment the mode would start parking again with nothing in the repository
# disagreeing. The identity is asserted through ``classify`` in
# tests/test_approval.py, so the branch order in ``classify`` is covered with it.
AUTONOMOUS_AUTO = REVIEW

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
# `post_concern` are in ``REVIEW``, which ``STRICT`` and ``PERMISSIVE`` hold and
# ``AUTONOMOUS`` releases whole. A specification an agent amended was approved at
# the amendment as it was at the declaration, and "at declaration" below covers
# both. Under ``AUTONOMOUS`` no operator saw the specification or the concern, and
# every read below hands on text nobody reviewed. That is the rung's stated cost and
# not a hole in this set: the reads carry exactly as much review as the writes were
# given, and wherever "approved" or "reviewed" is said below it means on a rung that
# asks.
#
# `read_peer_messages` is `read_inbox` for the messages another team's lead sent:
# reviewed at the send, on the sender's rung, which may not be the reader's. A
# lead on a reviewed rung reading a peer on ``AUTONOMOUS`` is handed text no
# operator saw. That is Decided 1's rule -- each side's rung governs its own
# acts -- and the text is quoted as the peer's and never as an instruction
# (`channels.text._peer_messages_text`), so it moves nothing onto the reader's board but
# through a `declare_task` the reader's own rung parks.
#
# `read_task` is `read_board` for one row, with the texts a board read cuts, and
# is here on `read_board`'s grounds exactly: it returns a specification approved
# at its declaration and notes reviewed at their send, scoped to the caller's own
# board.
#
# **`release_task` is bookkeeping on the caller's own claim, with one exception
# that rides the same auto-approved name on every rung, ``STRICT`` included.** The
# root of a session may release a claim that is not its own when the holder is an
# agent of an earlier run of that session (`store.tasks._may_reclaim`): it puts another
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
# kind of thing and never the queue row (`board.digesttext._digest_text`). With the row in it
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
# (`board.text._board_line`, `replies._quoted`).
#
# **The three decision tools are here because a decision is addressed to the
# operator, and one of them is not private to the operator.** `raise_decision` puts
# its text in the operator's own queue, and parking it would put a message for the
# operator in front of the operator to ask whether the operator may see it: the
# queue row is the review. `withdraw_decision` removes the asker's own row.
#
# **Except a decision raised on a channel, which this set does not admit.** With a
# `channel` argument the lead of the session at the other end of that link reads
# the decision whole as soon as the store files it (`model.Snapshot
# .channel_decision_readable`), before the operator has looked at the queue row,
# so the row reviews nothing that reader sees. That call is text from one team's
# lead to another's, and it is gated as `send_to_peer` is: parked on the rungs
# that park `_BUS_PEER`, released where it is released (`_reviewed_by_arguments`).
# Without a channel it is the call this paragraph describes, on every rung.
#
# The text has a second reader inside the session too. The root of a session reads
# every decision raised in it (`model.Snapshot.decisions_readable_by`), and
# `read_decisions` hands it the question, what happens if it goes unanswered, the
# evidence and the recommendation whole -- up to four texts of
# `model.MAX_DECISION_TEXT_CHARS` that a sub-agent wrote and no operator approved
# before the lead could read them. So on the rungs that park `post_concern`, a
# sub-agent's `raise_decision` is words to its lead that are not parked. It is the
# second such text on the board beside `complete_task`'s result, and it is held
# the same way: quoted line by line behind the asker's name, never as a line the
# tool wrote (`decisiontext._decision_whole`), with the operator looking at the same words
# in the queue. No board or digest read shows a question (`board.digesttext._digest_text`), so
# without a channel the lead is the only agent it reaches; with one, the other
# link's lead is a second, and that call parks as above. What bounds the three is
# the store: an agent may have `model.MAX_OPEN_DECISIONS_PER_ASKER` open, a session's sub-agents
# `model.MAX_OPEN_SUBAGENT_DECISIONS` between them, and only the operator's own
# intent ends one with an answer.
#
# **Both halves are decisions taken without the operator.** That a raise runs
# without sign-off is question 8, and that the session root reads its workers'
# decisions is question 4, of
# `planning/2026-10-12-session-control-and-the-inter-team-record-in-one-sequence.md`;
# the operator has answered neither, and each was built as recommended there. Put
# together they open the unparked channel above, which neither question asked
# about on its own. Moving `raise_decision` to ``REVIEW`` closes it on the rungs
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
# (`store.commands._approved`). `withdraw_command` removes the asker's own row.
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
#
# **`withdraw_peer_message` is here on `withdraw_decision`'s grounds, without a
# reason.** It takes back a message the caller's own lead sent, which only that
# sender may (`store.mail._message_withdrawal_refusal`), so like `withdraw_decision`
# it removes the asker's own act. Parking it would leave a question the sender
# has disowned open for the other lead while the operator gets to the row,
# which is the case-study failure the tool exists to end.
#
# **Except a withdrawal that gives a reason, which this set does not admit.**
# The reason is quoted to the recipient when it had read the message
# (`channels.text._withdrawn_message_lines`): it is text from one team's lead to
# another's, and so a send, gated by the sender's rung as `send_to_peer` is
# (Decided 1) -- parked on the rungs that park `_BUS_PEER`, released where it
# is released (`_reviewed_by_arguments`). It is not moved to ``REVIEW``,
# which would park the bare withdrawal too: that one carries no words, so it
# stays auto on every rung and ends the question at once.
#
# `why_blocked` and `ready` are `read_board` asked a narrower question, and change
# nothing. About the caller's own board they hand back ids, titles, states and
# holders a board read already shows. **They also say one thing a board read does
# not, and that is accepted rather than overlooked.** Asked about any id,
# `why_blocked` says whether a task of another session in this process holds it
# (`board.graphtext._why_blocked_text`, `_hop_state`, `_root_needs`), so an agent can
# test ids against other sessions' boards with no review. The reason it stays:
# ids within one local pptmstr process are not secret -- task ids are one
# namespace for every session in it, a declaration already learns that an id is
# taken, and a board row already says a dependency is "held elsewhere"
# (`board.foreign_wait_label`) -- and nothing but the id's existence is said: no
# title, specification, holder or state of that task.

BUS_AUTO = frozenset(
    {
        f"mcp__{_BUS_SERVER}__read_inbox",
        f"mcp__{_BUS_SERVER}__read_peer_messages",
        _BUS_WITHDRAW_PEER,
        f"mcp__{_BUS_SERVER}__read_board",
        f"mcp__{_BUS_SERVER}__read_task",
        f"mcp__{_BUS_SERVER}__read_digest",
        f"mcp__{_BUS_SERVER}__why_blocked",
        f"mcp__{_BUS_SERVER}__ready",
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


# The tools that read one file, by the argument that names it.
FILE_READS: Mapping[str, str] = {"Read": "file_path", "NotebookRead": "notebook_path"}

# The tools that read a tree: everything their ``path`` holds may come back, so a
# ``path`` that contains the records is a read of them.
TREE_READS = frozenset({"Glob", "Grep"})


def _reviewed_by_arguments(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """
    Whether a call whose name is in ``BUS_AUTO`` is reviewed anyway, for what its
    arguments make it: a ``raise_decision`` that names a channel, or a
    ``withdraw_peer_message`` that gives a reason, either of which hands text to
    another team's lead (see ``BUS_AUTO``'s comment). Such a call is classified
    as ``_BUS_PEER`` is, on every rung.

    "Names a channel" and "gives a reason" are the handlers' own readings of
    the argument (``bus.raise_decision``, ``bus.withdraw_peer_message``):
    anything but absent, None or blank. A value the handler then refuses parks
    too, which costs a review of a call that does nothing; reading it any
    narrower here would let a value the two read differently through unparked.
    """
    if tool_name == _BUS_DECIDE:
        return bool(str(tool_input.get("channel") or "").strip())
    if tool_name == _BUS_WITHDRAW_PEER:
        return bool(str(tool_input.get("reason") or "").strip())
    return False


def peer_channel(tool_name: str, tool_input: Mapping[str, Any]) -> str | None:
    """
    The channel a call names to reach another team's lead on, or None for a
    call that names none: ``send_to_peer``'s, and a ``raise_decision`` that
    names one (``_reviewed_by_arguments``). Read as the handler reads it,
    stripped. The store asks it of a parked call to name, on its row, the
    session the call would reach (``summarize``'s ``reaches``).

    None for a ``withdraw_peer_message``, which reaches another team's lead
    too but names no channel: it acts on the link of the message it names,
    which only the store holds (``model.Snapshot.parked_link``).
    """
    if tool_name == _BUS_PEER or (
        tool_name == _BUS_DECIDE and _reviewed_by_arguments(tool_name, tool_input)
    ):
        return str(tool_input.get("channel") or "?").strip() or "?"
    return None


def _dry_run(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    """
    Whether a call is a ``declare_task`` dry run, which declares nothing.

    ``declare_task`` is in ``REVIEW`` because a declaration is where work comes
    into existence. A dry run brings nothing into existence -- the handler reads
    the board and answers, and emits no write (``bus.declare_task``) -- so it is a
    lead thinking, which that comment calls free, and parking it would put an
    operator press in front of every plan a lead checks.

    Only a JSON ``true``. The handler also reads ``"true"`` and ``"yes"`` as a dry
    run (``model.stated_flag``), but admitting by a second reading of the same
    argument is how the two would come to disagree, and the cost of the narrower
    one is only that a spelled-out flag parks: a call admitted here that the
    handler took as a real declaration would put work on the board unreviewed. A
    spelling that parks is shown on its row as the dry run it is (``summarize``).

    **What a dry run tells its caller is more than a board read, and that is
    accepted.** For a duplicate id it says whether the id is a task of another
    session in this process, and names that session when the operator has taken
    it off the fleet (``TaskWriteSettled.held_off_fleet``). A real declaration
    says the same, but parks first under ``STRICT`` and ``PERMISSIVE``; this one
    does not. It stays auto-approved on ``BUS_AUTO``'s grounds for
    ``why_blocked``: ids within one local process are not secret, and nothing of
    the other task but its id and its session's is said.
    """
    return tool_name == _BUS_DECLARE and tool_input.get("dry_run") is True


def states_provenance(tool_name: str) -> bool:
    """
    Whether a call of ``tool_name`` can state a provenance: a declaration, and
    nothing else. Asked before anything reads the arguments, so a pane can skip
    serialising a call that has none -- a whole-file Write, every frame.
    """
    return tool_name == _BUS_DECLARE
