"""
The real agent driver: one ``ClaudeSDKClient`` per session, translated into intents.

Runs entirely on the Bridge's asyncio thread and reaches the UI through
``bridge.emit`` and its node's ``Transcript`` -- the same two channels the fake
driver used, which is what made building the pane against a fake worth doing.

``ClaudeSDKClient`` rather than ``query()``: the one-shot form cannot reach
``get_context_usage``, ``interrupt`` or ``set_permission_mode``, and this
orchestrator needs all three (design §3.1).

**Scope note.** This is build step 3, so the gate here classifies and *denies*
rather than parking. Step 4 replaces ``_decide`` with an await on the operator.
Denying unrecognised tools rather than allowing them means the step-3 driver is
safe to point at a real repository: nothing it does can write.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from claude_agent_sdk import (
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
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from .bridge import Bridge
from .intents import (
    AgentFinished,
    AgentSpawned,
    CompactionObserved,
    ContextPolled,
    Intent,
    StateChanged,
    TopicChanged,
    UsageAccrued,
)
from .log import LOG
from .model import AgentState, ContextSnapshot, NodeId, UsageRollup
from .transcript import SegmentKind, Transcript

# Hours, not minutes. HookMatcher.timeout defaults to 60s and the CLI enforces it
# with a per-hook abort, which would kill any review that took longer than a coffee
# break -- see design §5.2.1. This is a backstop against a wedged UI, not a review
# deadline, so it is set far beyond any plausible human latency.
APPROVAL_TIMEOUT_S = 6 * 60 * 60

# Slow on purpose: get_context_usage is a control request, not a push, and the
# number it returns moves on the scale of turns rather than frames.
CONTEXT_POLL_S = 20.0

# Read-only tools. Everything else is denied until step 4 puts a human behind it.
_READ_ONLY = frozenset(
    {"Read", "Glob", "Grep", "NotebookRead", "TodoWrite", "WebFetch", "WebSearch", "Task"}
)


def classify_read_only(tool_name: str, _tool_input: dict[str, Any]) -> bool:
    """
    Whether a tool may run without a human.

    Fails closed: an unrecognised tool is denied. A tool this orchestrator has never
    heard of is exactly the one that should not run unreviewed, and an allowlist that
    defaults open stops being an allowlist the first time the SDK adds a tool.
    """
    return tool_name in _READ_ONLY


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


def _tool_topic(tool_name: str, tool_input: dict[str, Any]) -> str:
    """
    A thinking topic derived mechanically from the tool call.

    Free and always current, which is the requirement -- this field is visible every
    frame, so it must never be produced by a summarisation call.
    """
    for key in ("file_path", "path", "pattern", "command", "url", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            trimmed = value if len(value) <= 48 else value[:45] + "..."
            return f"{tool_name.lower()} {trimmed}"
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
    # Whether deltas have already written the current message's content. Set by
    # _stream, consumed and cleared by _assistant.
    _streamed_content: bool = False

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

        # With include_partial_messages on, every text and thinking block has already
        # arrived delta by delta, and the complete message repeats it in full. Writing
        # both doubles the transcript -- a one-character answer came back as "99".
        # The deltas win because they are what makes reasoning visible as it happens
        # (goal #3); the complete message is still the only source of usage, tool
        # calls and state, so it is not simply ignored.
        already_streamed = self._streamed_content
        self._streamed_content = False

        for block in msg.content:
            if isinstance(block, ThinkingBlock):
                if not already_streamed:
                    self.transcript.append(SegmentKind.REASONING, block.thinking)
            elif isinstance(block, TextBlock):
                if not already_streamed:
                    self.transcript.append(SegmentKind.OUTPUT, block.text)
            elif isinstance(block, ToolUseBlock):
                self._tool_names[block.id] = block.name
                self.transcript.append(
                    SegmentKind.TOOL_CALL,
                    f"\n{block.name}({_compact_args(block.input)})\n",
                    meta=(("tool", block.name), ("tool_use_id", block.id)),
                )
                topic = _tool_topic(block.name, block.input)

        if msg.usage:
            out.append(UsageAccrued(self.node_id, _usage_from(msg.usage)))
        out.append(
            StateChanged(
                self.node_id,
                AgentState.CALLING_TOOL if topic else AgentState.THINKING,
                topic=topic,
            )
        )
        return out

    def _user(self, msg: UserMessage) -> list[Intent]:
        """
        Tool results arrive as user messages -- that is how the protocol carries them
        back to the model, not a sign the operator typed anything.
        """
        if isinstance(msg.content, str):
            return []
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                name = self._tool_names.get(block.tool_use_id, "tool")
                kind = SegmentKind.ERROR if block.is_error else SegmentKind.TOOL_RESULT
                self.transcript.append(kind, f"{name} -> {_compact_result(block.content)}\n")
        return [StateChanged(self.node_id, AgentState.THINKING)]

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

        # terminal_reason distinguishes "finished" from "you stopped it", which the
        # UI must not conflate -- a cancelled agent is not a completed one.
        cancelled = msg.terminal_reason in ("aborted_streaming", "aborted_tools")
        state = AgentState.CANCELLED if cancelled else AgentState.DONE
        out.append(AgentFinished(self.node_id, state, time.monotonic()))
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

        Setting ``_streamed_content`` tells the complete AssistantMessage that follows
        not to write the same text again. Doing it with a flag rather than by
        comparing content means it also behaves correctly when streaming is
        unavailable -- the flag stays false and the complete message is the source.

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
        if kind == "thinking_delta":
            self.transcript.append(SegmentKind.REASONING, delta.get("thinking", ""))
            self._streamed_content = True
        elif kind == "text_delta":
            self.transcript.append(SegmentKind.OUTPUT, delta.get("text", ""))
            self._streamed_content = True
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
        decide: Callable[[str, dict[str, Any], NodeId], Awaitable[bool]] | None = None,
    ) -> None:
        self.bridge = bridge
        self.task = task
        self.session_id = str(uuid.uuid4())
        self.node_id: NodeId = (self.session_id, None)
        self.transcript = Transcript()
        self.model = model or "claude-sonnet-5"
        self.cwd = cwd
        self._decide = decide
        self._client: ClaudeSDKClient | None = None
        self.transcript_path: str | None = None

    # -- hooks -------------------------------------------------------------------

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

        if classify_read_only(tool_name, tool_input):
            return _hook_output("allow")
        if self._decide is None:
            reason = (
                f"{tool_name} requires operator approval, which is not wired up yet "
                "(build step 4). Denied rather than allowed."
            )
            LOG.warn("gate", f"denied {tool_name}")
            return _hook_output("deny", reason)
        approved = await self._decide(tool_name, tool_input, self.node_id)
        return _hook_output("allow") if approved else _hook_output("deny", "rejected by operator")

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

    def _options(self) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            model=self.model,
            cwd=self.cwd,
            session_id=self.session_id,
            # Deny anything not explicitly allowed by the hook. PreToolUse runs on
            # every tool call regardless of mode and its deny is final, which is the
            # property a gate needs.
            permission_mode="dontAsk",
            include_partial_messages=True,
            hooks={
                "PreToolUse": [HookMatcher(hooks=[self._pre_tool_use], timeout=APPROVAL_TIMEOUT_S)],
                "PreCompact": [HookMatcher(hooks=[self._pre_compact])],
            },
        )

    # -- lifecycle ---------------------------------------------------------------

    async def run(self) -> None:
        """Connect, send the task, pump messages until the turn ends."""
        self.bridge.emit(
            AgentSpawned(
                node_id=self.node_id,
                parent=None,
                task=self.task,
                model=self.model,
                started_at=time.monotonic(),
                topic="connecting",
                transcript=self.transcript,
            )
        )
        translator = Translator(self.node_id, self.transcript)

        try:
            async with ClaudeSDKClient(options=self._options()) as client:
                self._client = client
                self.bridge.emit(StateChanged(self.node_id, AgentState.THINKING, topic="starting"))
                await client.query(self.task)
                await self._poll_context()

                last_poll = time.monotonic()
                async for message in client.receive_response():
                    for intent in translator.handle(message):
                        self.bridge.emit(intent)
                    if time.monotonic() - last_poll > CONTEXT_POLL_S:
                        await self._poll_context()
                        last_poll = time.monotonic()

                await self._poll_context()
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
