"""
The real agent driver: one ``ClaudeSDKClient`` per session, translated into intents.

Runs entirely on the Bridge's asyncio thread and reaches the UI through
``bridge.emit`` and its node's ``Transcript`` -- the same two channels the fake
driver used, which is what made building the pane against a fake worth doing.

``ClaudeSDKClient`` rather than ``query()``: the one-shot form cannot reach
``get_context_usage``, ``interrupt`` or ``set_permission_mode``, and this
orchestrator needs all three (design §3.1).

The approval gate lives here (design §5). A tool call that needs a human parks on
an ``asyncio.Future`` held by the Bridge; the await blocks that agent's task and
nothing else, which is I8 satisfied structurally rather than by convention.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookInput,
    HookJSONOutput,
    HookMatcher,
    PreCompactHookInput,
    PreToolUseHookInput,
    RateLimitEvent,
    ResultMessage,
    StreamEvent,
    SubagentStartHookInput,
    SubagentStopHookInput,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import SystemPromptPreset

from .approval import Disposition, classify, render_diff, summarize
from .bridge import Bridge
from .bus import BUS_TOOLS, EDITED_KEY, FROM_KEY, SERVER_NAME, build_server
from .intents import (
    AgentFinished,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    CompactionObserved,
    ContextPolled,
    Intent,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TopicChanged,
    UsageAccrued,
)
from .log import LOG
from .model import (
    AWAITING_TOPIC,
    INTERRUPTED_TOPIC,
    AgentState,
    ContextSnapshot,
    NodeId,
    PendingApproval,
    UsageRollup,
)
from .templates import SOLO, WorkTemplate, lead_briefing, worker_prompt
from .transcript import SegmentKind, Transcript

# Hours, not minutes. HookMatcher.timeout defaults to 60s and the CLI enforces it
# with a per-hook abort, which would kill any review that took longer than a coffee
# break -- see design §5.2.1. This is a backstop against a wedged UI, not a review
# deadline, so it is set far beyond any plausible human latency.
APPROVAL_TIMEOUT_S = 6 * 60 * 60

# Slow on purpose: get_context_usage is a control request, not a push, and the
# number it returns moves on the scale of turns rather than frames.
CONTEXT_POLL_S = 20.0

# How long to keep reading after the stream goes quiet. Only reached when a
# sub-agent is still outstanding; the loop otherwise exits on the parent result.
SUBAGENT_GRACE_S = 120.0

# How many sub-agents one session may have running at once, when the operator has
# not said otherwise.
#
# Not `Settings.concurrency_cap`, and not the same constraint. That one bounds root
# sessions and its ceiling is RAM, because each of those is a Claude Code CLI
# subprocess. Sub-agents are not: they share the parent's session_id and their hooks
# come back over the parent's single control channel, so what N of them cost is N
# concurrent API streams and N transcripts of token burn against one node process,
# plus an operator who has to answer for each spawn at the gate. Four lets both roles
# of the two-role templates run doubled, which is the fan-out the briefing asks for,
# and stops a lead that has read "one worker per independent task" from turning a
# twelve-item board into twelve streams.
DEFAULT_SUBAGENT_CAP = 4

# The names that mean the root session, and so can never be allocated to a
# sub-agent. A worker calls the agent that gave it the job by whichever of these
# comes to mind, and all three have to land on the same node.
ROOT_ADDRESSES = ("lead", "main", "root")


def _split_address(address: str) -> tuple[str, int]:
    """
    An instance address as the role it belongs to and its ordinal.

    ``builder-2`` is the second agent in the ``builder`` role; a bare ``builder`` is
    the first. The inverse of the ``_address_for`` allocation, and the thing that
    lets ``role_status`` tell an ordinal nobody is running from a name nobody has.
    """
    base, _, ordinal = address.rpartition("-")
    if base and ordinal.isdigit() and int(ordinal) >= 2:
        return base, int(ordinal)
    return address, 1


def _spawn_key(subagent_type: str) -> str:
    """
    The name both halves of the spawn join agree to call a role.

    The Agent call writes ``subagent_type`` and ``SubagentStart`` reports
    ``agent_type``; case and surrounding space are the only ways those two can name
    one role differently. A call that named no role at all keys on the same fallback
    ``_subagent_start`` gives an unnamed agent_type, so the pair still meets.
    """
    return subagent_type.strip().lower() or "agent"


def _hook_output(decision: str, reason: str | None = None) -> HookJSONOutput:
    """
    Build a PreToolUse hook result.

    Typed as the HookJSONOutput union because the concrete sync variant is not in
    the SDK's public exports.

    The decision is nested under ``hookSpecificOutput`` and carries its own
    ``hookEventName``. A top-level ``permissionDecision`` -- the shape the design
    sketch originally showed -- is accepted by the type checker and silently is not
    a decision at all.
    """
    specific: dict[str, Any] = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason is not None:
        specific["permissionDecisionReason"] = reason
    return cast(HookJSONOutput, {"hookSpecificOutput": specific})


def _allow_with(edited_args: Mapping[str, Any] | None) -> HookJSONOutput:
    """Allow, optionally replacing the arguments the agent asked for (§5.3)."""
    specific: dict[str, Any] = {"hookEventName": "PreToolUse", "permissionDecision": "allow"}
    if edited_args is not None:
        specific["updatedInput"] = dict(edited_args)
    return cast(HookJSONOutput, {"hookSpecificOutput": specific})


# TaskCreate does not choose its own id: the call carries a subject, and the id the
# CLI assigned appears only in the result text -- "Task #3 created successfully: ...".
_TASK_CREATED = re.compile(r"[Tt]ask #(\d+)")

# The topic column's budget. Enforced on the composed string rather than on the
# argument alone, so a prefix cannot push the result past it.
_TOPIC_LIMIT = 55


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _text_arg(args: Mapping[str, Any], key: str) -> str:
    """
    A tool argument as display text, or "" when it is absent or unusable.

    ``taskId`` has been observed as a string, but the tool schema is the CLI's to
    change and an integer id would read identically -- so accept both rather than
    silently losing the join if it ever arrives as a number.
    """
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, str | int):
        return ""
    return str(value).strip()


def _tool_topic(tool_name: str, tool_input: dict[str, Any]) -> str:
    """
    A thinking topic derived mechanically from the tool call.

    Free and always current, which is the requirement -- this field is visible every
    frame, so it must never be produced by a summarisation call.

    ``subject`` leads because TaskCreate carries both it and a longer ``description``
    of the same item: the subject is the one-line form written to be read.
    """
    for key in ("subject", "file_path", "path", "pattern", "command", "url", "description"):
        value = _text_arg(tool_input, key)
        if value:
            return _clip(f"{tool_name.lower()} {value}", _TOPIC_LIMIT)
    return tool_name.lower()


@dataclass
class Translator:
    """
    Turns SDK messages into intents and transcript writes.

    Split out from the IO so it can be tested without a subprocess: feed it message
    objects, assert on the intents that come back. The transcript is written as a
    side effect because it is append-only and deliberately outside the intent path
    (I7) -- routing a token stream through the queue would put one item per token on
    the UI thread.
    """

    node_id: NodeId
    transcript: Transcript
    # ResultMessage reports a cost per result rather than a running total, but which
    # of those it is has not been confirmed against a multi-turn session. Tracking
    # the last value and emitting the difference is correct either way: if it is
    # cumulative the delta is the increment, and if it is per-turn the previous
    # value has already been banked and reset to 0 does not subtract.
    _last_cost: float = 0.0
    # Tool calls seen this turn, so a ToolResultBlock can name what it belongs to.
    _tool_names: dict[str, str] = field(default_factory=dict)
    # Nodes whose deltas have already written the current message's content. Added
    # by _stream, consumed and discarded by _assistant. Keyed by node because a
    # sub-agent's deltas and the root's complete message interleave on one stream,
    # and a single flag would let one suppress the other's text entirely. A mark is
    # cleared only by a complete message for the *same* node, so a node whose
    # attribution changes between its deltas and its message keeps its mark.
    _streamed_nodes: set[NodeId] = field(default_factory=set)
    # taskId -> subject, so a TaskUpdate carrying nothing but an id and a status can
    # still say which piece of work it is about. This is the agent's own statement of
    # what it is driving towards, which no amount of deriving from file paths reaches.
    _task_subjects: dict[str, str] = field(default_factory=dict)
    # tool_use_id -> subject, for TaskCreate calls whose result has not arrived yet
    # and whose id is therefore not known.
    _pending_task_subjects: dict[str, str] = field(default_factory=dict)
    # tool_use_id of an Agent call -> the sub-agent NodeId it became. Populated by
    # the session once SubagentStart has been joined; used to route Task* progress,
    # which is keyed by tool_use_id rather than agent_id (§2.5.1).
    subagent_by_tool_use: dict[str, NodeId] = field(default_factory=dict)
    # Sub-agent NodeId -> the Transcript that node's record holds. Published by the
    # session, which mints the buffer at spawn and hands the same object to the
    # store on AgentSpawned (intents.py) -- both sides must write and read one
    # object or the UI gets an empty buffer while the driver fills an orphan.
    subagent_transcripts: dict[NodeId, Transcript] = field(default_factory=dict)

    def _node_of(self, message: object) -> NodeId:
        """
        Which node a message describes.

        Sub-agent messages carry parent_tool_use_id -- the id of the Agent call that
        spawned them -- so without this every sub-agent's activity is attributed to
        its parent, and the parent row narrates work it is not doing.
        """
        parent = getattr(message, "parent_tool_use_id", None)
        if parent:
            return self.subagent_by_tool_use.get(parent, self.node_id)
        return self.node_id

    def _transcript_of(self, node: NodeId) -> Transcript:
        """
        The buffer a node's segments belong in.

        An unknown node falls back to the session's rather than being dropped: a
        sub-agent whose spawn join is still missing is already attributed to the
        root by _node_of, and the two must not disagree about where its words go.
        """
        if node == self.node_id:
            return self.transcript
        return self.subagent_transcripts.get(node, self.transcript)

    def handle(self, message: object) -> list[Intent]:
        if isinstance(message, AssistantMessage):
            return self._assistant(message)
        if isinstance(message, UserMessage):
            return self._user(message)
        if isinstance(message, ResultMessage):
            return self._result(message)
        if isinstance(message, RateLimitEvent):
            return self._rate_limit(message)
        if isinstance(message, StreamEvent):
            return self._stream(message)
        if isinstance(message, SystemMessage):
            return self._system(message)
        return []

    # -- complete messages -------------------------------------------------------

    def _assistant(self, msg: AssistantMessage) -> list[Intent]:
        out: list[Intent] = []
        topic: str | None = None
        node = self._node_of(msg)
        transcript = self._transcript_of(node)

        # With include_partial_messages on, every text and thinking block has already
        # arrived delta by delta, and the complete message repeats it in full. Writing
        # both doubles the transcript -- a one-character answer came back as "99".
        # The deltas win because they are what makes reasoning visible as it happens
        # (goal #3); the complete message is still the only source of usage, tool
        # calls and state, so it is not simply ignored.
        already_streamed = node in self._streamed_nodes
        self._streamed_nodes.discard(node)

        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                if not already_streamed:
                    transcript.append(SegmentKind.REASONING, block.thinking)
            elif isinstance(block, TextBlock):
                if not already_streamed:
                    transcript.append(SegmentKind.OUTPUT, block.text)
            elif isinstance(block, ToolUseBlock):
                self._tool_names[block.id] = block.name
                self._note_task(block)
                transcript.append(
                    SegmentKind.TOOL_CALL,
                    f"\n{block.name}({_compact_args(block.input)})\n",
                    meta=(("tool", block.name), ("tool_use_id", block.id)),
                )
                topic = self._topic_for(block)

        if msg.usage:
            out.append(UsageAccrued(node, _usage_from(msg.usage)))
        out.append(
            StateChanged(
                node,
                AgentState.CALLING_TOOL if topic else AgentState.THINKING,
                topic=topic,
            )
        )
        return out

    # -- the agent's own task list -----------------------------------------------

    def _note_task(self, block: ToolUseBlock) -> None:
        """Record what a task-list call says, so a later status change can be named."""
        if block.name == "TaskCreate":
            subject = _text_arg(block.input, "subject")
            if subject:
                self._pending_task_subjects[block.id] = subject
        elif block.name == "TaskUpdate":
            # An update may rename an item as well as move it; when it carries a
            # subject, that is the newer truth and replaces what create recorded.
            task_id = _text_arg(block.input, "taskId")
            subject = _text_arg(block.input, "subject")
            if task_id and subject:
                self._task_subjects[task_id] = subject

    def _bind_task_id(self, block: ToolResultBlock) -> None:
        """
        Join the id the CLI assigned to the subject the call asked for.

        The two halves arrive in different messages -- subject in the call, id in the
        result -- so this is the only point at which they can be put together. The
        subject is taken from the call rather than parsed back out of the result
        text, because the call is what the agent actually wrote.
        """
        subject = self._pending_task_subjects.pop(block.tool_use_id, None)
        if subject is None or block.is_error:
            return
        content = block.content if isinstance(block.content, str) else repr(block.content)
        found = _TASK_CREATED.search(content)
        if found:
            self._task_subjects[found.group(1)] = subject

    def _topic_for(self, block: ToolUseBlock) -> str:
        if block.name == "TaskUpdate":
            return self._task_update_topic(block.input)
        return _tool_topic(block.name, block.input)

    def _task_update_topic(self, args: dict[str, Any]) -> str:
        """
        A status change named by the work it refers to.

        Deliberately not prefixed with the tool name the way every other topic is:
        "taskupdate write the tests" describes the mechanism, and the point of this
        row is the goal. An in-progress item is simply the topic; any other status is
        prefixed with itself, because "the agent is working on X" and "the agent has
        finished X" must not read identically.
        """
        task_id = _text_arg(args, "taskId")
        status = _text_arg(args, "status").replace("_", " ")
        subject = _text_arg(args, "subject") or self._task_subjects.get(task_id, "")
        # An id we never saw created belongs to a session that predates this
        # translator -- a resumed conversation. "task 3" is thin, but it is true, and
        # it is more than the bare tool name.
        label = subject or (f"task {task_id}" if task_id else "task")
        if status and status != "in progress":
            label = f"{status}: {label}"
        return _clip(label, _TOPIC_LIMIT)

    # -- tool results ------------------------------------------------------------

    def _user(self, msg: UserMessage) -> list[Intent]:
        """
        Tool results arrive as user messages -- that is how the protocol carries them
        back to the model, not a sign the operator typed anything.
        """
        node = self._node_of(msg)
        if isinstance(msg.content, str):
            return []
        transcript = self._transcript_of(node)
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                self._bind_task_id(block)
                name = self._tool_names.get(block.tool_use_id, "tool")
                kind = SegmentKind.ERROR if block.is_error else SegmentKind.TOOL_RESULT
                transcript.append(kind, f"{name} -> {_compact_result(block.content)}\n")
        return [StateChanged(node, AgentState.THINKING)]

    def _result(self, msg: ResultMessage) -> list[Intent]:
        out: list[Intent] = []
        cost = msg.total_cost_usd or 0.0
        delta = max(0.0, cost - self._last_cost)
        self._last_cost = max(self._last_cost, cost)
        if delta:
            out.append(UsageAccrued(self.node_id, UsageRollup(total_cost_usd=delta)))

        if msg.is_error:
            detail = "; ".join(msg.errors or []) or msg.subtype
            if msg.api_error_status:
                detail = f"HTTP {msg.api_error_status}: {detail}"
            self.transcript.append(SegmentKind.ERROR, f"\n{detail}\n")
            out.append(
                AgentFinished(self.node_id, AgentState.FAILED, time.monotonic(), error=detail)
            )
            return out

        # No state intent for a turn that merely ended, interrupted or not. A
        # ResultMessage is a turn boundary and only ``run()`` knows what that means
        # for the node -- it is the one place that has waited out the sub-agent
        # grace window and the context poll first. Deciding here means naming a
        # state before the facts that would settle it exist.
        return out

    def _rate_limit(self, msg: RateLimitEvent) -> list[Intent]:
        info = msg.rate_limit_info
        if info.status == "rejected":
            return [
                StateChanged(
                    self.node_id,
                    AgentState.RATE_LIMITED,
                    topic=f"rate limited ({info.rate_limit_type})",
                )
            ]
        if info.status == "allowed_warning":
            # Not a state change: the agent is still working. Surfacing it as a topic
            # keeps "approaching a limit" visible without claiming it has stopped.
            return [TopicChanged(self.node_id, f"nearing rate limit ({info.rate_limit_type})")]
        return []

    def _system(self, msg: SystemMessage) -> list[Intent]:
        if msg.subtype in ("task_started", "task_progress"):
            # The one live signal a background sub-agent gives: a human-readable
            # description of what it is doing. Free and current -- exactly what a
            # thinking topic is supposed to be (§2.6), and the only thing standing
            # in for the stream sub-agents do not produce.
            data = msg.data
            tool_use_id = str(data.get("tool_use_id", ""))
            description = str(data.get("description", "") or "")
            node = self.subagent_by_tool_use.get(tool_use_id)
            if node is not None and description:
                return [SubagentProgress(node, description)]
            return []
        if msg.subtype == "compact_boundary":
            self.transcript.append(
                SegmentKind.COMPACTION, "\n--- context compacted; earlier reasoning discarded ---\n"
            )
            return [CompactionObserved(self.node_id, time.monotonic(), trigger="auto")]
        return []

    # -- partial messages --------------------------------------------------------

    def _stream(self, msg: StreamEvent) -> list[Intent]:
        """
        Token-level deltas, so reasoning is surfaced as it streams rather than
        reconstructed after the fact (goal #3).

        Recording the node in ``_streamed_nodes`` tells the complete AssistantMessage
        that follows not to write the same text again. Doing it with a marker rather
        than by comparing content means it also behaves correctly when streaming is
        unavailable -- the node is absent and the complete message is the source.

        ``input_json_delta`` is deliberately dropped. It carries raw JSON fragments
        of a tool call's arguments, which would interleave with the formatted call
        the complete message produces and read as corruption. Live partial arguments
        are worth having, but they need per-block segments to render sanely, and that
        belongs with the transcript pane in step 7.
        """
        event = msg.event
        if event.get("type") != "content_block_delta":
            return []
        delta = event.get("delta") or {}
        kind = delta.get("type")
        node = self._node_of(msg)
        if kind == "thinking_delta":
            self._transcript_of(node).append(SegmentKind.REASONING, delta.get("thinking", ""))
            self._streamed_nodes.add(node)
        elif kind == "text_delta":
            self._transcript_of(node).append(SegmentKind.OUTPUT, delta.get("text", ""))
            self._streamed_nodes.add(node)
        return []


def _usage_from(usage: Mapping[str, Any]) -> UsageRollup:
    def count(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) else 0

    return UsageRollup(
        input_tokens=count("input_tokens"),
        output_tokens=count("output_tokens"),
        cache_creation_input_tokens=count("cache_creation_input_tokens"),
        cache_read_input_tokens=count("cache_read_input_tokens"),
    )


def _compact_args(args: dict[str, Any], limit: int = 160) -> str:
    parts = []
    for key, value in args.items():
        text = value if isinstance(value, str) else repr(value)
        if len(text) > 60:
            text = text[:57] + "..."
        parts.append(f"{key}={text}")
    joined = ", ".join(parts)
    return joined if len(joined) <= limit else joined[: limit - 3] + "..."


def _compact_result(content: object, limit: int = 300) -> str:
    text = content if isinstance(content, str) else repr(content)
    return text if len(text) <= limit else text[: limit - 3] + "..."


class AgentSession:
    """
    One connected session: one CLI subprocess, one root node in the tree.

    The session ID is minted here rather than learned from the first message, so the
    NodeId -- and therefore every widget key under it (I6) -- is stable from the
    moment the row appears.
    """

    def __init__(
        self,
        bridge: Bridge,
        task: str,
        *,
        model: str | None = None,
        cwd: str | None = None,
        interactive: bool = True,
        template: WorkTemplate | None = None,
        subagent_cap: int = DEFAULT_SUBAGENT_CAP,
    ) -> None:
        self.bridge = bridge
        self.task = task
        # The team shape, or None for a lone agent. Held rather than unpacked at
        # construction because resolve_role needs the role names for a role that has
        # not spawned yet -- "part of this team, not started" and "no such role" are
        # different answers, and only one of them means the sender got it wrong.
        self.template = template or SOLO
        self.session_id = str(uuid.uuid4())
        self.node_id: NodeId = (self.session_id, None)
        self.transcript = Transcript()
        self.model = model or "claude-sonnet-5"
        self.cwd = cwd
        # Whether an operator is attached to answer. False means headless, where a
        # tool needing approval is denied rather than left to hit the timeout.
        self.interactive = interactive
        # agent_id -> the tool_use_id of the Agent call that started it, taken from
        # the ledger below when it starts. Used only to route progress descriptions
        # and output (§2.5.1); approvals never depend on it, because a hook inside a
        # sub-agent reports agent_id directly.
        self._spawn_tool_use: dict[str, str] = {}
        # subagent_type -> the tool_use_ids of Agent calls this session has admitted
        # in that role and not yet matched to a SubagentStart, oldest first.
        #
        # A ledger rather than one slot because the join has to hold whether or not
        # the CLI interleaves the two hooks. The SDK says parallel sub-agents' tool
        # lifecycle hooks share one control channel (claude_agent_sdk/types.py), and
        # the briefing asks the lead to start independent work together, so two
        # PreToolUse hooks may both be answered before either SubagentStart fires. A
        # single slot then binds the second call's id to the first sub-agent and
        # nothing at all to the second, and the unbound one's words fall back to the
        # root's transcript.
        #
        # Keyed on the role because it is the only field both events carry:
        # PreToolUse has the Agent call's `subagent_type` and SubagentStart has
        # `agent_type`. Two agents started in one role are matched FIFO and so may
        # end up swapped relative to their descriptions -- nothing distinguishes
        # twins, since the tool_use_id passed to the SubagentStart callback is an
        # unrelated UUID. A swap costs a label; a miss costs a whole transcript.
        self._pending_spawns: dict[str, list[str]] = {}
        # The most sub-agents allowed to be running or admitted at once.
        self.subagent_cap = subagent_cap
        # Two sets, because "how many are running now" and "have I met this id
        # before" are different questions and the answers legitimately diverge.
        #
        # _live_subagents answers the first: added on SubagentStart, discarded on
        # SubagentStop and on the grace-period expiry, so it is the count the loop
        # waits on. _seen_subagents cannot answer it -- it never shrinks.
        self._live_subagents: set[str] = set()
        # _seen_subagents answers the second, and it is the only thing the resume
        # signal may read: a woken sub-agent starts again under its original id
        # after it has already stopped, so a set that a stop empties would call
        # that wake a spawn.
        self._seen_subagents: set[str] = set()
        # agent_id -> the Transcript handed to the store on AgentSpawned. Kept past
        # SubagentStop so a resumed sub-agent keeps appending to the buffer the UI
        # is already reading (I7), rather than to a replacement the record does not
        # hold.
        self._subagent_transcripts: dict[str, Transcript] = {}
        # address -> agent_id, so the bus can route on a name the model can plausibly
        # write ("qa", "builder-2") instead of an opaque id it has no way to learn.
        # Driver-side rather than a store lookup because the answer is needed on the
        # asyncio thread, where the store cannot be read.
        #
        # Keyed by instance, not by type: a role is a job description and several
        # agents may hold it at once. One address names one agent for the session's
        # life, so a concern cannot retarget mid-run.
        self._roles: dict[str, str] = {}
        self._client: ClaudeSDKClient | None = None
        # Whether the cancellation this session is about to receive was asked for.
        # Set by SessionPool.close and SessionPool.shutdown before they cancel; read
        # by run()'s cancellation arm, which needs exactly this one bit.
        #
        # It exists because cancellation cannot answer the question by itself:
        # Bridge.stop's loop thread cancels every remaining task at shutdown whether
        # or not the pool was asked first, and the SDK's anyio task groups make a
        # transport teardown surfacing as a CancelledError plausible too. Whether a
        # teardown was intended is knowledge only the caller has, so the caller
        # states it rather than run() inferring it from the exception.
        self.teardown_requested = False
        self.transcript_path: str | None = None

    # -- roles, for the bus ------------------------------------------------------

    def resolve_role(self, name: str) -> NodeId | None:
        """
        The node an address names, or None.

        "lead", "main" and "root" all name the root session, because that is what a
        worker naturally calls the agent that gave it the job. Everything else is an
        instance address allocated by ``_address_for``.
        """
        key = name.strip().lower()
        if key in ROOT_ADDRESSES:
            return self.node_id
        agent_id = self._roles.get(key)
        return (self.session_id, agent_id) if agent_id else None

    def role_of(self, node: NodeId) -> str | None:
        """
        The address a node answers to -- the inverse, for rendering a sender.

        The instance address, not the role's type: ``bus.py`` puts this in front of
        a model that will reply to it, so it has to be a name ``resolve_role``
        accepts and routes back to this same node.
        """
        if node == self.node_id:
            return "lead"
        for address, agent_id in self._roles.items():
            if (self.session_id, agent_id) == node:
                return address
        return None

    def known_roles(self) -> tuple[str, ...]:
        return ("lead", *sorted(self._roles))

    def _addresses_in_role(self, base: str) -> tuple[str, ...]:
        """
        Every address currently answering in one role, lowest ordinal first.

        Ordered on the parsed ordinal rather than on the string, which would put
        ``builder-10`` in front of ``builder-2`` in a list the lead reads as a count.
        """
        found = [(_split_address(a), a) for a in self._roles]
        return tuple(a for (parsed_base, _n), a in sorted(found) if parsed_base == base)

    def role_status(self, name: str) -> str:
        """
        Why an address could not be reached, in words the model can act on.

        Three mistakes, three answers, because the fix differs. A role on the team
        with nothing running is *timing* -- start one. An ordinal past the agents
        that are running is *counting* -- the role is reachable, just not that many
        times over. Anything else is *spelling*. The probe's worker retried the same
        wrong name because one message covered two of these.
        """
        key = name.strip().lower()
        base, _ordinal = _split_address(key)
        running = self._addresses_in_role(base)
        if running:
            reachable = ", ".join(running)
            return (
                f"The {base!r} role is running {len(running)} agent(s), reachable as "
                f"{reachable} -- nothing answers to {key!r}. Address one of those, or "
                f"start another agent in the role with the Agent tool "
                f"(subagent_type={base!r})."
            )
        if self.template.role(base) is not None:
            return (
                f"{base!r} is a role on this team but has not been started yet. "
                f"Start it with the Agent tool (subagent_type={base!r}) first."
            )
        known = ", ".join(self.known_roles())
        return f"No agent known as {name!r}. Reachable now: {known}."

    def _address_for(self, agent_id: str, agent_type: str) -> str:
        """
        The address a sub-agent answers to, allocated once and never moved.

        The first agent of a type takes the bare role name and later ones take
        ``builder-2``, ``builder-3`` -- the convention ``templates.lead_briefing``
        hands the lead, so the two must not drift.

        Idempotent per agent_id: ``SubagentStart`` fires again when a sibling wakes a
        finished sub-agent (§2.3), and an address that moved mid-run would send every
        concern already written to it to a sibling instead.

        A candidate another role in the template answers to, or one of the root's own
        names, is skipped rather than taken. A template may define a role literally
        called ``builder-2``, and two agents at one address is the failure this table
        exists to prevent.
        """
        for address, held in self._roles.items():
            if held == agent_id:
                return address
        base = agent_type.strip().lower() or "agent"
        declared = self.template.role_names()
        ordinal = 1
        while True:
            candidate = base if ordinal == 1 else f"{base}-{ordinal}"
            spoken_for = (
                candidate in self._roles
                or candidate in ROOT_ADDRESSES
                or (candidate != base and candidate in declared)
            )
            if not spoken_for:
                return candidate
            ordinal += 1

    # -- hooks -------------------------------------------------------------------

    def _node_for(self, agent_id: str | None) -> NodeId:
        """The node a hook belongs to: the root session, or one of its sub-agents."""
        return (self.session_id, agent_id) if agent_id else self.node_id

    def _model_for_type(self, agent_type: str) -> str:
        """
        The model a sub-agent of this type actually runs on.

        `_team` gives the SDK `role.model or "inherit"`, so a record built from the
        session's model states something false whenever a role overrode it. The join
        is `agent_type` rather than the address `_roles` allocated: `builder-2` is a
        second agent in the builder role, and a role is one model however many agents
        hold it.

        The session's own model is the honest fallback, not a guess -- "inherit" is
        what an unconfigured role is given, and an agent_type outside the template
        is one the CLI resolved from its own definitions, which this hook is not
        told the model for.
        """
        role = self.template.role(agent_type)
        return (role.model if role else None) or self.model

    async def _subagent_start(
        self, hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        data = cast(SubagentStartHookInput, hook_input)
        agent_id = str(data.get("agent_id", ""))
        agent_type = str(data.get("agent_type", "") or "agent")
        if not agent_id:
            return {}

        # The same hook reports two different events. A second SubagentStart for an
        # agent_id we have already seen is a *resume*: a sibling's SendMessage woke
        # a sub-agent that had finished, and the CLI restarts it from its transcript
        # under its original id (§2.3, measured in scripts/verify_wake_path.py).
        # Emitting AgentSpawned again would rebuild the record from nothing --
        # zeroing usage, resetting started_at, and swapping the Transcript the UI is
        # reading (I7). The wake follows the sub-agent's own stop, so only the set
        # that outlives a stop can recognise it.
        resumed = agent_id in self._seen_subagents
        self._seen_subagents.add(agent_id)
        self._live_subagents.add(agent_id)
        self._roles[self._address_for(agent_id, agent_type)] = agent_id

        if resumed:
            self.bridge.emit(AgentResumed(node_id=(self.session_id, agent_id), at=time.monotonic()))
            return {}

        # Below the resume return: a wake is not a spawn and no Agent call was
        # admitted for it, so consuming a ledger entry here would overwrite a live
        # sub-agent's join with an id belonging to some other call -- and take the
        # entry from the pending spawn it really belongs to.
        admitted = self._take_spawn_tool_use(agent_type)
        if admitted:
            self._spawn_tool_use[agent_id] = admitted

        transcript = self._subagent_transcripts.setdefault(agent_id, Transcript())
        self.bridge.emit(
            AgentSpawned(
                node_id=(self.session_id, agent_id),
                parent=self.node_id,
                task=agent_type,
                model=self._model_for_type(agent_type),
                started_at=time.monotonic(),
                agent_type=agent_type,
                topic="starting",
                transcript=transcript,
            )
        )
        return {}

    async def _subagent_stop(
        self, hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        data = cast(SubagentStopHookInput, hook_input)
        agent_id = str(data.get("agent_id", ""))
        if not agent_id:
            return {}
        node = (self.session_id, agent_id)
        # last_assistant_message is the sub-agent's answer, handed over without
        # having to reconstruct it from a stream that never arrives (§2.5.1).
        answer = str(data.get("last_assistant_message", "") or "")
        if answer:
            # Two intents from one string, because the row and the pane want
            # different things from it: the row needs a line that fits a column, and
            # the pane needs the answer the sub-agent was spawned to produce.
            #
            # This hook is the only place the answer arrives *delimited*. A
            # sub-agent's AssistantMessages do reach the stream, carrying
            # parent_tool_use_id, and _assistant routes their text into this node's
            # transcript -- so the words are usually there too, as bytes with nothing
            # marking where the answer begins. Boundaries in the transcript are what
            # 2026-08-13-detail-swaps-to-a-deliverable step 4 is for; until they
            # exist, this string is the only whole answer the store can hold.
            #
            # The message that would carry a boundary cannot arrive: ResultMessage
            # has no parent_tool_use_id field at all (claude_agent_sdk/types.py,
            # unlike AssistantMessage, UserMessage and StreamEvent), so no result is
            # ever attributable to a sub-agent's own node.
            self.bridge.emit(SubagentProgress(node, answer.splitlines()[0][:80]))
            self.bridge.emit(SubagentDelivered(node, answer))
        self.bridge.emit(AgentFinished(node, AgentState.DONE, time.monotonic()))
        self._live_subagents.discard(agent_id)
        return {}

    async def _pre_tool_use(
        self, hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        # The union is discriminated by hook_event_name, and this callback is only
        # ever registered for PreToolUse -- narrowing here rather than branching on a
        # discriminator that cannot vary.
        data = cast(PreToolUseHookInput, hook_input)
        tool_name = str(data.get("tool_name", ""))
        tool_input = data.get("tool_input") or {}
        # Recorded because it is the authoritative session log; ours is a view (§9).
        self.transcript_path = data.get("transcript_path") or self.transcript_path

        # agent_id is present only when the call comes from inside a sub-agent, and
        # it is the only reliable attribution when several run in parallel.
        agent_id = data.get("agent_id")
        node = self._node_for(agent_id)
        # The hook-visible name is "Agent" even though the tool list says "Task"
        # (§2.5.1).
        spawn = tool_name in ("Agent", "Task") and not agent_id
        tool_use_id = str(data.get("tool_use_id", ""))

        # Ahead of classify, because a spawn that cannot be admitted must not reach
        # the operator: parking it would ask a human to approve a call this session
        # has already decided it will not run.
        if spawn and self._outstanding_subagents() >= self.subagent_cap:
            return _hook_output("deny", self._at_cap_reason())

        disposition = classify(tool_name, tool_input)
        if disposition is Disposition.AUTO_APPROVE:
            self._expect_spawn(spawn, tool_use_id, tool_input)
            # Stamped even when nothing is reviewed. The stamp is authentication,
            # not policy: an auto-approved read_inbox still has to know whose inbox
            # it is, and a handler that reached its body unstamped would raise.
            return _allow_with(self._stamp_bus_call(tool_name, tool_input, node))
        if disposition is Disposition.DENY:
            return _hook_output("deny", f"{tool_name} is not permitted by policy")
        if not self.interactive:
            # Headless: nothing can approve, so deny rather than hang until the
            # timeout. A run with no operator must fail closed and say why.
            return _hook_output("deny", f"{tool_name} needs approval and no operator is attached")

        return await self._park(tool_name, tool_input, tool_use_id, node, spawn=spawn)

    def _expect_spawn(self, spawn: bool, tool_use_id: str, tool_input: Mapping[str, Any]) -> None:
        """
        Admit an Agent call to the ledger, for its SubagentStart to claim.

        Written on the allow paths only, never when the hook is entered. The CLI
        runs the tool -- and so fires the SubagentStart this pairs with -- only
        after the hook permits it, so an id recorded any earlier belongs to a call
        that may still be denied, cancelled, or overtaken by another spawn while a
        human sits in the gate. `Agent` is in `_REVIEW`, so that gap is a human's
        latency rather than a scheduling accident.

        Appended rather than assigned: several calls can be admitted before the
        first of them starts, and each one is owed its own join.
        """
        if not spawn or not tool_use_id:
            return
        role = _spawn_key(str(tool_input.get("subagent_type") or ""))
        self._pending_spawns.setdefault(role, []).append(tool_use_id)

    def _take_spawn_tool_use(self, agent_type: str) -> str | None:
        """
        The admitted Agent call a starting sub-agent belongs to, consumed.

        None when nothing is outstanding in that role, which is a real case rather
        than an error: a sub-agent that spawns its own sub-agent makes an Agent call
        carrying an agent_id, which is not admitted here at all. Handing that start
        some other role's entry would misroute two streams instead of one.
        """
        role = _spawn_key(agent_type)
        queue = self._pending_spawns.get(role)
        if not queue:
            return None
        tool_use_id = queue.pop(0)
        if not queue:
            del self._pending_spawns[role]
        return tool_use_id

    def _outstanding_subagents(self) -> int:
        """
        How many sub-agents are running or have been committed to.

        The ledger term is what makes this a bound rather than a suggestion. Every
        PreToolUse in a burst of spawns can be answered before the first
        SubagentStart fires, so a count taken from `_live_subagents` alone reads
        zero for each of them and admits the whole burst.
        """
        return len(self._live_subagents) + sum(len(q) for q in self._pending_spawns.values())

    def _at_cap_reason(self) -> str:
        """
        Why a spawn was refused, in terms that separate it from a policy denial.

        A capacity refusal is about timing, not about the call: the same Agent call
        succeeds later, and the lead needs to be told that rather than left to
        rewrite a request that was never wrong.
        """
        return (
            f"This session already has {self._outstanding_subagents()} sub-agent(s) running "
            f"or starting and its cap is {self.subagent_cap}, so this spawn is refused for "
            f"capacity -- the call itself is fine. Wait for one of them to finish and start "
            f"this one then, or declare the work with declare_task and name what it waits "
            f"on in depends_on, so the board carries the ordering instead of an agent held "
            f"open to express it."
        )

    def _stamp_bus_call(
        self,
        tool_name: str,
        tool_input: Mapping[str, Any],
        node: NodeId,
        *,
        edited: bool = False,
    ) -> Mapping[str, Any] | None:
        """
        Attach the authenticated sender to a bus call, or None for anything else.

        This is what makes §2.7's bus possible at all. An in-process MCP handler is
        handed only the tool name and its arguments -- no session, no agent, no
        tool-use id -- so a sender it read out of its own arguments would be a
        sender the model wrote. ``PreToolUse`` is the only participant that knows,
        and ``updatedInput`` is the only way to tell the handler.

        Applied *after* any operator edit (see ``_park``), so the sender cannot be
        rewritten by edit-then-approve either. Returning None leaves the arguments
        untouched, which is what every non-bus tool wants.

        ``edited`` travels the same way and for the same reason: the gate is also
        the only participant that can know the operator rewrote the call, and a
        handler building a record from the rewritten arguments alone cannot tell
        them from what the sender wrote.
        """
        if tool_name not in BUS_TOOLS:
            return None
        stamped = dict(tool_input)
        # A list, not the NodeId tuple: this crosses to the CLI as JSON and comes
        # back, and JSON has no tuples. bus._sender reconstitutes it.
        stamped[FROM_KEY] = [node[0], node[1]]
        # Written on every stamped call, not only edited ones. `stamped` starts as
        # a copy of the model's own arguments, so writing this only when it is True
        # would let a sender that put `_edited` in its input keep it -- and claim
        # the operator had vetted a message the operator never saw. Overwriting
        # unconditionally is the same rule FROM_KEY follows, and it also makes it
        # moot whether the CLI forwards an out-of-schema argument, which is
        # unmeasured.
        stamped[EDITED_KEY] = bool(edited)
        return stamped

    async def _park(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str,
        node: NodeId,
        *,
        spawn: bool = False,
    ) -> HookJSONOutput:
        """
        Block this agent until the operator decides. I8, structurally.

        The await parks only this task, so other agents keep running and the app
        stays idle-able while it waits. The Bridge holds the future; the store only
        learns that something is pending.
        """
        pending = PendingApproval(
            id=f"{self.session_id[:8]}-{tool_use_id or uuid.uuid4().hex[:8]}",
            node=node,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            raw_args=dict(tool_input),
            summary=summarize(tool_name, tool_input),
            requested_at=time.time(),
            diff=render_diff(tool_name, tool_input),
        )
        future = self.bridge.park(pending.id)
        self.bridge.emit(ApprovalRequested(node, pending))

        try:
            decision = await future
        except asyncio.CancelledError:
            # Reachable two ways: the CLI's per-hook timeout fires (verified to
            # arrive as a cancellation of this coroutine, §5.2.1), or the session is
            # torn down. Either way the store still has the pending row, and leaving
            # it there would show an approval that can never be answered.
            self.bridge.emit(ApprovalResolved(node, pending.id, approved=False, reason="cancelled"))
            raise

        self.bridge.emit(
            ApprovalResolved(
                node,
                pending.id,
                approved=decision.approved,
                reason=decision.reason,
                edited_args=decision.edited_args,
            )
        )
        if decision.approved:
            LOG.info("gate", f"approved {pending.summary}")
            # updatedInput is why edit-then-approve exists: a wrong path or a
            # too-broad command can be corrected and run, rather than rejected and
            # waited on (§5.3).
            #
            # The stamp goes on last, over whatever the operator settled on, so an
            # edited concern still carries the sender the gate authenticated rather
            # than one the edit could have introduced.
            approved_args = decision.edited_args if decision.edited_args is not None else tool_input
            # Admitted from the arguments that will actually run: the operator can
            # retype subagent_type in the editor, and the role the ledger files this
            # call under has to be the one SubagentStart will report.
            self._expect_spawn(spawn, tool_use_id, approved_args)
            # Compared against the *original* input and before the stamp is
            # applied. After it, every approved bus call differs from what the
            # model sent -- the stamp itself is a rewrite -- so the comparison
            # would report every concern as edited. Opening the editor and
            # changing nothing is likewise not an edit.
            edited = decision.edited_args is not None and dict(decision.edited_args) != dict(
                tool_input
            )
            return _allow_with(
                self._stamp_bus_call(tool_name, approved_args, node, edited=edited)
                or decision.edited_args
            )
        reason = decision.reason or "Rejected by operator"
        LOG.warn("gate", f"rejected {pending.summary}")
        return _hook_output("deny", reason)

    async def _pre_compact(
        self, hook_input: HookInput, _tool_use_id: str | None, _context: HookContext
    ) -> HookJSONOutput:
        """
        Compaction is observable, which is the whole basis for treating context as a
        health signal rather than a budget (§2.4).
        """
        trigger = str(cast(PreCompactHookInput, hook_input).get("trigger", "auto"))
        self.bridge.emit(CompactionObserved(self.node_id, time.monotonic(), trigger=trigger))
        LOG.warn("context", f"session compacted ({trigger})")
        return {}

    def _team(self) -> dict[str, AgentDefinition] | None:
        """The template's roles as SDK agent definitions, or None for a lone agent."""
        if not self.template.roles:
            return None
        return {
            role.name: AgentDefinition(
                description=role.description,
                prompt=worker_prompt(role),
                tools=role.tool_list(),
                # "inherit" rather than self.model: a role that does not ask for a
                # model should run on whatever the session was launched with, not on
                # the SDK's default, or the launcher's model choice would silently
                # apply to the lead alone.
                model=role.model or "inherit",
            )
            for role in self.template.roles
        }

    def _system_prompt(self) -> SystemPromptPreset | None:
        """
        The lead's briefing, appended to Claude Code's own system prompt.

        Appended rather than replacing it: the preset carries the tool conventions
        and the environment description this agent still needs, and a bare string
        here would throw all of that away to say four paragraphs about delegation.
        """
        briefing = lead_briefing(self.template)
        if not briefing:
            return None
        return {"type": "preset", "preset": "claude_code", "append": briefing}

    def _options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=self.model,
            cwd=self.cwd,
            session_id=self.session_id,
            agents=self._team(),
            system_prompt=self._system_prompt(),
            # Deny anything not explicitly allowed by the hook. PreToolUse runs on
            # every tool call regardless of mode and its deny is final, which is the
            # property a gate needs.
            permission_mode="dontAsk",
            include_partial_messages=True,
            # display="summarized" is load-bearing, not decoration. Every model in
            # the launcher except Haiku defaults to display="omitted", which still
            # emits thinking blocks and thinking deltas -- with an empty string in
            # them. Reasoning then renders as nothing at all, and the pane's toggle
            # filters an empty set. What arrives here is a summary; the raw chain of
            # thought is never returned by these models at any setting.
            thinking={"type": "adaptive", "display": "summarized"},
            # The bus (§2.7). In-process, so no subprocess and no extra lifecycle to
            # manage -- the CLI reaches these handlers back over the same control
            # channel it uses for hooks.
            mcp_servers={SERVER_NAME: build_server(self)},
            hooks={
                "PreToolUse": [HookMatcher(hooks=[self._pre_tool_use], timeout=APPROVAL_TIMEOUT_S)],
                "PreCompact": [HookMatcher(hooks=[self._pre_compact])],
                "SubagentStart": [HookMatcher(hooks=[self._subagent_start])],
                "SubagentStop": [HookMatcher(hooks=[self._subagent_stop])],
            },
        )

    # -- lifecycle ---------------------------------------------------------------

    def announce(self) -> None:
        """
        Put this session in the tree before it connects.

        Separate from run() so the row exists from the moment the operator asked for
        it -- a session that takes a second to spawn a subprocess should not be
        invisible while it does, or the UI looks like it dropped the request.
        """
        self.bridge.emit(
            AgentSpawned(
                node_id=self.node_id,
                parent=None,
                task=self.task,
                model=self.model,
                started_at=time.monotonic(),
                topic="connecting",
                cwd=self.cwd,
                # Announced here rather than left on the session: this is the only
                # emitter that knows the template, and the UI cannot reach into an
                # AgentSession to ask.
                template=self.template.name,
                transcript=self.transcript,
            )
        )

    async def run(self) -> None:
        """
        Connect, send the opening task, then stay connected for further turns.

        The session does **not** end when a turn does. A turn ending means the agent
        stopped talking, which happens both when it has finished the job and when it
        has asked a question -- and those are not the same thing. Disconnecting at
        the first ResultMessage made them indistinguishable and made replying
        impossible in principle rather than merely unimplemented.

        The loop therefore runs until the session is closed, which cancels this task.
        """
        self.announce()
        translator = Translator(self.node_id, self.transcript)
        # The last failure this session reported and has not recovered from. Held
        # across the whole body, not just the loop, because the cancellation arm has
        # to know about it too.
        failure: AgentFinished | None = None

        try:
            async with ClaudeSDKClient(options=self._options()) as client:
                self._client = client
                await self.send(self.task)
                await self._poll_context()

                last_poll = time.monotonic()
                # receive_messages, not receive_response. A sub-agent launched by
                # the Agent tool is a background task that outlives the parent's
                # ResultMessage (§2.5.1); stopping at the result would drop its
                # completion and leave its row running forever.
                #
                # Read with no timeout. The stream legitimately goes silent for as
                # long as the operator takes to answer an approval or to type the
                # next prompt -- a blanket read deadline here would tear down a
                # session that is correctly waiting, which is I8 broken by the
                # plumbing rather than by the design.
                async for message in client.receive_messages():
                    self._sync_subagent_map(translator)
                    for intent in translator.handle(message):
                        if (
                            isinstance(intent, AgentFinished)
                            and intent.node_id == self.node_id
                            and intent.state is AgentState.FAILED
                        ):
                            # Held, not just noted: re-asserting the failure later
                            # needs the error text, and this is the only object that
                            # has it. Re-emitting the same frozen intent also keeps
                            # ended_at at the moment the session actually died rather
                            # than moving it to whenever the stream happened to close.
                            failure = intent
                        self.bridge.emit(intent)
                    if isinstance(message, ResultMessage) and not message.is_error:
                        # A good turn clears the standing failure. Read per result
                        # rather than latched: a session that errored and then
                        # answered is an ordinary session again.
                        failure = None
                    if isinstance(message, ResultMessage) and failure is None:
                        if self._live_subagents:
                            await self._await_subagents(client, translator)
                        await self._poll_context()
                        # Ready for another prompt rather than finished. Idle, so a
                        # conversation paused on the operator still costs nothing.
                        #
                        # An interrupted turn lands here too, on the same non-terminal
                        # state: interrupt is meant to keep the session usable, so the
                        # confirmation that it landed rides in the topic. The topic is
                        # trustworthy where a summary would not be -- terminal_reason
                        # is the CLI reporting what it did, not an inference about
                        # what the agent said.
                        topic = (
                            INTERRUPTED_TOPIC
                            if message.terminal_reason in ("aborted_streaming", "aborted_tools")
                            else AWAITING_TOPIC
                        )
                        self.bridge.emit(
                            StateChanged(self.node_id, AgentState.AWAITING_INPUT, topic=topic)
                        )
                    if isinstance(message, ResultMessage):
                        # The turn is over, including the drain above for any
                        # sub-agent still reporting, so nothing admitted during it is
                        # still on its way to starting.
                        self._forget_pending_spawns()
                    if time.monotonic() - last_poll > CONTEXT_POLL_S:
                        await self._poll_context()
                        last_poll = time.monotonic()

                # Only reached if the CLI closed the stream on us -- which is one of
                # the things it does after an error result.
                #
                # A standing failure is *re-asserted*, not merely left alone. Between
                # the error and here, any StateChanged for this node moves the record
                # off FAILED: the store's arm guards on `rec.pending` and not on
                # terminality, and `_rate_limit` and `_assistant` both emit one for
                # the root. Emitting nothing here would leave a session whose
                # subprocess is gone reading as rate-limited or thinking -- no
                # obligation, no error text, and `any_active` never settling.
                if failure is not None:
                    self.bridge.emit(failure)
                else:
                    self.bridge.emit(AgentFinished(self.node_id, AgentState.DONE, time.monotonic()))
        except asyncio.CancelledError:
            # Cancellation is not by itself evidence of anything. `SessionPool.close`
            # and `SessionPool.shutdown` both say so first; `Bridge.stop`'s loop
            # thread cancels every remaining task whether or not the pool was asked,
            # and the SDK's anyio task groups make a transport teardown surfacing
            # here plausible as well.
            #
            # An asked-for teardown is DONE, and closing one session and quitting the
            # application are the same statement for a session: the operator ended
            # it. DONE deliberately wins over a standing FAILED there -- ending a
            # session *is* the dismissal, and leaving FAILED would keep a dismissed
            # session in the "needs you" list with nothing left to act on.
            #
            # A cancel nobody asked for ended a live session without being asked to,
            # which is a failure and is reported as one. Reporting it as DONE would
            # be the same silent loss of a failure signal this arm's neighbour exists
            # to prevent; reporting nothing would leave the record wherever its last
            # StateChanged put it, which is that neighbour's own defect.
            if self.teardown_requested:
                self.bridge.emit(AgentFinished(self.node_id, AgentState.DONE, time.monotonic()))
            elif failure is not None:
                self.bridge.emit(failure)
            else:
                self.bridge.emit(
                    AgentFinished(
                        self.node_id,
                        AgentState.FAILED,
                        time.monotonic(),
                        error="the session was cancelled without being closed",
                    )
                )
            raise
        except Exception as exc:
            # The session dying must not take the asyncio thread with it: other
            # agents keep running and the UI has to be told why this one stopped.
            LOG.error("agent", f"{type(exc).__name__}: {exc}")
            self.transcript.append(SegmentKind.ERROR, f"\n{type(exc).__name__}: {exc}\n")
            self.bridge.emit(
                AgentFinished(
                    self.node_id,
                    AgentState.FAILED,
                    time.monotonic(),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        finally:
            self._client = None

    async def send(self, text: str) -> None:
        """
        Send a prompt on the live session. The opening task uses this too.

        Safe to call while the message loop is iterating: query() writes to the
        subprocess's stdin while the loop reads its stdout, so the two do not
        contend. This is the whole point of holding a connected client rather than
        a one-shot query.
        """
        client = self._client
        if client is None:
            LOG.warn("agent", "cannot send: session is not connected")
            return
        self.transcript.append(SegmentKind.SYSTEM, f"\n> {text}\n")
        self.bridge.emit(
            StateChanged(self.node_id, AgentState.THINKING, topic="reading your message")
        )
        await client.query(text)

    def _sync_subagent_map(self, translator: Translator) -> None:
        """
        Republish what the translator needs to attribute a sub-agent's messages: the
        tool_use_id -> node join for progress, and the node -> Transcript join that
        keeps its words out of the root's buffer.

        Both are rebuilt from the session's tables on every message rather than
        pushed from the hook, because the hook runs on the CLI's callback and the
        translator is only ever touched from the message loop.
        """
        translator.subagent_by_tool_use = {
            tool_use_id: (self.session_id, agent_id)
            for agent_id, tool_use_id in self._spawn_tool_use.items()
        }
        translator.subagent_transcripts = {
            (self.session_id, agent_id): transcript
            for agent_id, transcript in self._subagent_transcripts.items()
        }

    async def _await_subagents(self, client: ClaudeSDKClient, translator: Translator) -> None:
        """
        Keep reading after the parent's result while sub-agents are still running.

        Bounded, unlike the main loop. Here the stream really may just go quiet: the
        parent is finished, so nothing will produce another message if a background
        task ends without reporting. The bound therefore only ever cuts short a
        sub-agent that has stopped talking -- never an agent parked on the operator,
        which is why the main loop reads with no deadline at all.
        """
        stream = client.receive_messages()
        while self._live_subagents:
            try:
                message = await asyncio.wait_for(stream.__anext__(), timeout=SUBAGENT_GRACE_S)
            except (TimeoutError, StopAsyncIteration):
                LOG.warn("agent", f"{len(self._live_subagents)} sub-agent(s) stopped reporting")
                self._abandon_live_subagents()
                return
            self._sync_subagent_map(translator)
            for intent in translator.handle(message):
                self.bridge.emit(intent)

    def _abandon_live_subagents(self) -> None:
        """
        Settle every sub-agent still outstanding when the grace period expires.

        FAILED because the honest statement is that it stopped reporting: CANCELLED
        would claim someone cancelled it and DONE would claim an answer arrived,
        and neither is known here. Without this the record never reaches a terminal
        state and the id never leaves the live set, so the card spins for the
        session's life and the count the set answers can only shrink.

        _seen_subagents is deliberately untouched -- a sub-agent that goes quiet can
        still be woken later, and that start is a resume, not a spawn.
        """
        ended_at = time.monotonic()
        for agent_id in self._live_subagents:
            self.bridge.emit(
                AgentFinished(
                    (self.session_id, agent_id),
                    AgentState.FAILED,
                    ended_at,
                    error=f"stopped reporting for {SUBAGENT_GRACE_S:g}s",
                )
            )
        self._live_subagents.clear()

    def _forget_pending_spawns(self) -> None:
        """
        Drop admitted spawns that never started, at the end of the parent's turn.

        A ledger entry is only ever consumed by a SubagentStart, which the CLI fires
        when it runs the tool the gate just allowed. One still here when the turn is
        over is owed to a call that will not start, and because the cap counts these
        it would take a slot away for the session's life -- the same permanently
        shrinking capacity `_abandon_live_subagents` exists to prevent above.

        Clearing costs nothing a spawn wanted: a sub-agent that starts afterwards has
        no admitted call to match anyway, so it joins nothing either way, and the
        alternative is that it joins some *other* call's id.
        """
        self._pending_spawns.clear()

    async def _poll_context(self) -> None:
        """
        Polled, never pushed. Failures are logged and dropped -- a context reading is
        a nicety and must not end a session.
        """
        client = self._client
        if client is None:
            return
        try:
            usage = await client.get_context_usage()
        except Exception as exc:
            LOG.debug("context", f"poll failed: {type(exc).__name__}: {exc}")
            return
        # ContextUsageResponse is a TypedDict, which is a promise about the CLI's
        # output rather than a runtime guarantee -- read it defensively.
        self.bridge.emit(
            ContextPolled(self.node_id, _context_from(cast(Mapping[str, Any], usage), self.model))
        )

    async def interrupt(self) -> None:
        """Stop work without ending the session (design §9: the recoverable lever)."""
        client = self._client
        if client is not None:
            await client.interrupt()


def _context_from(usage: Mapping[str, Any], fallback_model: str) -> ContextSnapshot:
    def count(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) else 0

    max_tokens = count("maxTokens")
    return ContextSnapshot(
        used_tokens=count("totalTokens"),
        max_tokens=max_tokens,
        raw_max_tokens=count("rawMaxTokens") or max_tokens,
        percentage=float(usage.get("percentage") or 0.0),
        auto_compact_enabled=bool(usage.get("isAutoCompactEnabled")),
        auto_compact_threshold=(
            count("autoCompactThreshold") if usage.get("autoCompactThreshold") else None
        ),
        model=str(usage.get("model") or fallback_model),
        polled_at=time.time(),
    )
