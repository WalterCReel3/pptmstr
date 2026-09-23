"""
Driver translation: SDK messages in, intents and transcript writes out.

Exercised with real SDK message objects but no subprocess. The translation is where
the store's honesty under streaming is decided, and it is worth being able to test
that without spending tokens or waiting on a CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from pptmstr.approval import Policy
from pptmstr.bridge import Bridge
from pptmstr.driver import AgentSession, Translator, _tool_topic
from pptmstr.intents import (
    AgentFinished,
    CompactionObserved,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TopicChanged,
    UsageAccrued,
)
from pptmstr.model import (
    AWAITING_TOPIC,
    INTERRUPTED_TOPIC,
    SUPERVISING_TOPIC,
    AgentState,
    LaunchSpec,
    NodeId,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from pptmstr.pool import SessionPool
from pptmstr.store import Store
from pptmstr.templates import Role, WorkTemplate
from pptmstr.transcript import SegmentKind, Transcript

NODE: NodeId = ("sess-1", None)


def make() -> tuple[Translator, Transcript]:
    transcript = Transcript()
    return Translator(NODE, transcript), transcript


def result(**kwargs: object) -> ResultMessage:
    base: dict[str, object] = {
        "subtype": "success",
        "duration_ms": 10,
        "duration_api_ms": 8,
        "is_error": False,
        "num_turns": 1,
        "session_id": "sess-1",
    }
    base.update(kwargs)
    return ResultMessage(**base)  # type: ignore[arg-type]


# -- topics --------------------------------------------------------------------


def test_topic_names_the_salient_argument() -> None:
    assert _tool_topic("Read", {"file_path": "/tmp/x.py"}) == "read /tmp/x.py"
    assert _tool_topic("Bash", {"command": "pytest -q"}) == "bash pytest -q"


def test_topic_truncates_rather_than_overflowing_the_column() -> None:
    topic = _tool_topic("Bash", {"command": "x" * 200})
    assert len(topic) <= 55
    assert topic.endswith("...")


def test_topic_falls_back_to_the_tool_name() -> None:
    assert _tool_topic("MysteryTool", {}) == "mysterytool"


def test_topic_prefers_the_subject_over_the_longer_description() -> None:
    """TaskCreate carries both; the subject is the one written to be read."""
    topic = _tool_topic(
        "TaskCreate",
        {
            "subject": "Add subtract function to calc.py",
            "description": "Add a subtract(a, b) function matching the style of add.",
        },
    )
    assert topic == "taskcreate Add subtract function to calc.py"


# -- the agent's own task list -------------------------------------------------


def create_task(tr: Translator, tool_use_id: str, task_id: str, subject: str) -> None:
    """
    The two halves of a TaskCreate.

    The call carries the subject and no id; the result carries the id the CLI
    assigned. Neither alone is enough to name a later status change.
    """
    tr.handle(
        AssistantMessage(
            content=[ToolUseBlock(id=tool_use_id, name="TaskCreate", input={"subject": subject})],
            model="m",
        )
    )
    tr.handle(
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=f"Task #{task_id} created successfully: {subject}",
                )
            ]
        )
    )


def update_task(tr: Translator, **args: object) -> str:
    intents = tr.handle(
        AssistantMessage(
            content=[ToolUseBlock(id="u", name="TaskUpdate", input=dict(args))], model="m"
        )
    )
    state = next(i for i in intents if isinstance(i, StateChanged))
    return state.topic or ""


def test_task_update_names_the_work_rather_than_the_mechanism() -> None:
    """
    The whole point: "taskupdate" describes the call, not what the agent is doing.

    A status change is the one moment the agent states its intent outright, so it
    must not be the least informative topic in the stream.
    """
    tr, _ = make()
    create_task(tr, "t1", "1", "Add subtract function to calc.py")
    assert update_task(tr, taskId="1", status="in_progress") == "Add subtract function to calc.py"


def test_a_finished_item_does_not_read_as_work_in_progress() -> None:
    tr, _ = make()
    create_task(tr, "t1", "3", "Write test file for calc.py")
    assert update_task(tr, taskId="3", status="completed") == (
        "completed: Write test file for calc.py"
    )


def test_the_right_subject_is_picked_out_of_several() -> None:
    tr, _ = make()
    create_task(tr, "t1", "1", "Add subtract function")
    create_task(tr, "t2", "2", "Add multiply function")
    create_task(tr, "t3", "3", "Write the tests")
    assert update_task(tr, taskId="2", status="in_progress") == "Add multiply function"


def test_an_update_may_rename_the_item_it_moves() -> None:
    tr, _ = make()
    create_task(tr, "t1", "1", "Write the tests")
    assert update_task(tr, taskId="1", subject="Write the tests and run them") == (
        "Write the tests and run them"
    )
    # The rename sticks: a later status-only update uses the newer subject.
    assert update_task(tr, taskId="1", status="completed") == (
        "completed: Write the tests and run them"
    )


def test_an_id_from_before_we_attached_still_says_something() -> None:
    """A resumed session has tasks this translator never saw created."""
    tr, _ = make()
    assert update_task(tr, taskId="7", status="in_progress") == "task 7"


def test_a_failed_create_binds_nothing() -> None:
    """Binding an id to a task that was never created would misname a later update."""
    tr, _ = make()
    tr.handle(
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="TaskCreate", input={"subject": "Never happened"})],
            model="m",
        )
    )
    tr.handle(
        UserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content="Task #1 failed", is_error=True)]
        )
    )
    assert update_task(tr, taskId="1", status="in_progress") == "task 1"


def test_a_numeric_task_id_joins_the_same_way() -> None:
    """The tool schema is the CLI's to change; an integer id must not break the join."""
    tr, _ = make()
    create_task(tr, "t1", "4", "Run tests to verify")
    assert update_task(tr, taskId=4, status="in_progress") == "Run tests to verify"


def test_a_long_subject_is_clipped_to_the_column() -> None:
    tr, _ = make()
    create_task(tr, "t1", "1", "x" * 200)
    topic = update_task(tr, taskId="1", status="completed")
    assert len(topic) <= 55
    assert topic.endswith("...")


# -- assistant messages --------------------------------------------------------


def test_thinking_and_text_land_in_separate_segments() -> None:
    """Reasoning must be distinguishable from output, or the pane cannot style it."""
    tr, transcript = make()
    tr.handle(
        AssistantMessage(
            content=[ThinkingBlock(thinking="pondering", signature="s"), TextBlock(text="answer")],
            model="claude-opus-5",
        )
    )
    kinds = [s.kind for s in transcript.segments()]
    assert kinds == [SegmentKind.REASONING, SegmentKind.OUTPUT]
    assert transcript.text() == "ponderinganswer"


def test_tool_use_sets_calling_state_and_a_derived_topic() -> None:
    tr, _ = make()
    intents = tr.handle(
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Read", input={"file_path": "/tmp/a"})],
            model="claude-opus-5",
        )
    )
    state = next(i for i in intents if isinstance(i, StateChanged))
    assert state.state is AgentState.CALLING_TOOL
    assert state.topic == "read /tmp/a"


def test_text_only_message_is_thinking() -> None:
    tr, _ = make()
    intents = tr.handle(AssistantMessage(content=[TextBlock(text="hi")], model="claude-opus-5"))
    state = next(i for i in intents if isinstance(i, StateChanged))
    assert state.state is AgentState.THINKING


def test_usage_is_accrued_from_the_message() -> None:
    tr, _ = make()
    intents = tr.handle(
        AssistantMessage(
            content=[TextBlock(text="hi")],
            model="claude-opus-5",
            usage={"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 100},
        )
    )
    usage = next(i for i in intents if isinstance(i, UsageAccrued))
    assert usage.delta.input_tokens == 12
    assert usage.delta.output_tokens == 3
    assert usage.delta.cache_read_input_tokens == 100


def test_missing_usage_fields_do_not_crash() -> None:
    """The usage dict is passed through from the CLI, so its shape is not ours."""
    tr, _ = make()
    intents = tr.handle(
        AssistantMessage(content=[TextBlock(text="hi")], model="m", usage={"input_tokens": None})
    )
    usage = next(i for i in intents if isinstance(i, UsageAccrued))
    assert usage.delta.input_tokens == 0


# -- tool results --------------------------------------------------------------


def test_tool_result_is_attributed_to_its_call() -> None:
    tr, transcript = make()
    tr.handle(
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Grep", input={"pattern": "x"})], model="m"
        )
    )
    tr.handle(UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="3 matches")]))
    assert "Grep -> 3 matches" in transcript.text()


def test_failed_tool_result_is_an_error_segment() -> None:
    tr, transcript = make()
    tr.handle(AssistantMessage(content=[ToolUseBlock(id="t1", name="Read", input={})], model="m"))
    tr.handle(
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="nope", is_error=True)])
    )
    assert SegmentKind.ERROR in {s.kind for s in transcript.segments()}


# -- results -------------------------------------------------------------------


def test_a_turn_ending_settles_no_state() -> None:
    """
    A ResultMessage is a turn boundary, not a session boundary. Naming a state here
    would name it before run() has waited out the sub-agents and the context poll.
    """
    tr, _ = make()
    intents = tr.handle(result(total_cost_usd=0.02, terminal_reason="completed"))
    assert not [i for i in intents if isinstance(i, AgentFinished)]
    assert [i for i in intents if isinstance(i, UsageAccrued)]


def test_an_interrupted_turn_settles_no_state_either() -> None:
    tr, _ = make()
    intents = tr.handle(result(terminal_reason="aborted_streaming"))
    assert not [i for i in intents if isinstance(i, AgentFinished)]


def test_error_result_finishes_failed_with_the_status() -> None:
    tr, _ = make()
    intents = tr.handle(result(is_error=True, errors=["overloaded"], api_error_status=529))
    finished = next(i for i in intents if isinstance(i, AgentFinished))
    assert finished.state is AgentState.FAILED
    assert finished.error is not None
    assert "529" in finished.error and "overloaded" in finished.error


def test_cost_is_emitted_as_a_delta() -> None:
    """
    Whether total_cost_usd is per-turn or cumulative is unconfirmed. Deltaing against
    the last seen value is correct either way, so the ambiguity cannot double-bill.
    """
    tr, _ = make()
    first = tr.handle(result(total_cost_usd=0.10))
    second = tr.handle(result(total_cost_usd=0.25))
    a = next(i for i in first if isinstance(i, UsageAccrued))
    b = next(i for i in second if isinstance(i, UsageAccrued))
    assert a.delta.total_cost_usd == 0.10
    assert round(b.delta.total_cost_usd, 6) == 0.15


def test_cost_never_goes_backwards() -> None:
    tr, _ = make()
    tr.handle(result(total_cost_usd=0.50))
    intents = tr.handle(result(total_cost_usd=0.10))
    assert not [i for i in intents if isinstance(i, UsageAccrued)]


# -- rate limits ---------------------------------------------------------------


def rate(status: str) -> RateLimitEvent:
    return RateLimitEvent(
        rate_limit_info=RateLimitInfo(status=status, rate_limit_type="five_hour"),  # type: ignore[arg-type]
        uuid="u",
        session_id="sess-1",
    )


def test_rejection_becomes_a_state_change() -> None:
    tr, _ = make()
    intents = tr.handle(rate("rejected"))
    assert isinstance(intents[0], StateChanged)
    assert intents[0].state is AgentState.RATE_LIMITED


def test_warning_is_a_topic_not_a_state_change() -> None:
    """
    The agent is still working. Marking it RATE_LIMITED would say it had stopped,
    and the difference between "stuck" and "backing off" is the whole point of
    surfacing this.
    """
    tr, _ = make()
    intents = tr.handle(rate("allowed_warning"))
    assert isinstance(intents[0], TopicChanged)


def test_allowed_is_silent() -> None:
    tr, _ = make()
    assert tr.handle(rate("allowed")) == []


# -- streaming and compaction --------------------------------------------------


def test_thinking_deltas_stream_into_the_reasoning_segment() -> None:
    """Goal #3: reasoning is surfaced as it arrives, not reconstructed afterwards."""
    tr, transcript = make()
    for piece in ("let me ", "think ", "about it"):
        tr.handle(
            StreamEvent(
                uuid="u",
                session_id="sess-1",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": piece},
                },
            )
        )
    assert transcript.text() == "let me think about it"
    assert [s.kind for s in transcript.segments()] == [SegmentKind.REASONING]


def test_text_deltas_stream_into_output() -> None:
    tr, transcript = make()
    tr.handle(
        StreamEvent(
            uuid="u",
            session_id="s",
            event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
        )
    )
    assert [s.kind for s in transcript.segments()] == [SegmentKind.OUTPUT]


def test_unrelated_stream_events_are_ignored() -> None:
    tr, transcript = make()
    tr.handle(StreamEvent(uuid="u", session_id="s", event={"type": "message_start"}))
    assert transcript.text() == ""


def test_compact_boundary_is_observed_and_marked() -> None:
    """
    The transcript gets a marker at the offset where reasoning was discarded, so
    later output can be read as coming from an agent that had already lost it.
    """
    tr, transcript = make()
    intents = tr.handle(SystemMessage(subtype="compact_boundary", data={}))
    assert any(isinstance(i, CompactionObserved) for i in intents)
    assert SegmentKind.COMPACTION in {s.kind for s in transcript.segments()}


def test_unknown_messages_are_ignored() -> None:
    """New SDK message types must not crash the pump."""
    tr, _ = make()
    assert tr.handle(SystemMessage(subtype="something_new", data={})) == []
    assert tr.handle(object()) == []


# -- streaming / complete-message deduplication --------------------------------


def delta(kind: str, **payload: str) -> StreamEvent:
    return StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "content_block_delta", "delta": {"type": kind, **payload}},
    )


def test_streamed_text_is_not_repeated_by_the_complete_message() -> None:
    """
    Regression: with include_partial_messages on, both the deltas and the complete
    AssistantMessage carry the same text. Writing both doubled the transcript -- a
    one-character answer rendered as "99" against a live agent.
    """
    tr, transcript = make()
    tr.handle(delta("text_delta", text="9"))
    tr.handle(AssistantMessage(content=[TextBlock(text="9")], model="m"))
    assert transcript.text() == "9"


def test_streamed_thinking_is_not_repeated_either() -> None:
    tr, transcript = make()
    tr.handle(delta("thinking_delta", thinking="hmm"))
    tr.handle(AssistantMessage(content=[ThinkingBlock(thinking="hmm", signature="s")], model="m"))
    assert transcript.text() == "hmm"


def test_complete_message_is_used_when_nothing_streamed() -> None:
    """
    The flag, not content comparison, is what makes this work when streaming is off
    or unavailable -- as it may be for sub-agents.
    """
    tr, transcript = make()
    tr.handle(AssistantMessage(content=[TextBlock(text="direct")], model="m"))
    assert transcript.text() == "direct"


def test_dedup_flag_resets_between_messages() -> None:
    """A streamed turn must not suppress the text of a later unstreamed one."""
    tr, transcript = make()
    tr.handle(delta("text_delta", text="first"))
    tr.handle(AssistantMessage(content=[TextBlock(text="first")], model="m"))
    tr.handle(AssistantMessage(content=[TextBlock(text="second")], model="m"))
    assert transcript.text() == "firstsecond"


def test_tool_calls_survive_the_dedup() -> None:
    """Only text and thinking are streamed; the formatted tool call is not."""
    tr, transcript = make()
    tr.handle(delta("text_delta", text="thinking out loud"))
    tr.handle(
        AssistantMessage(
            content=[
                TextBlock(text="thinking out loud"),
                ToolUseBlock(id="t1", name="Read", input={"file_path": "/a"}),
            ],
            model="m",
        )
    )
    assert "Read(file_path=/a)" in transcript.text()
    assert transcript.text().count("thinking out loud") == 1


def test_partial_tool_json_is_not_written() -> None:
    """
    Raw JSON fragments would interleave with the formatted call from the complete
    message and read as corruption.
    """
    tr, transcript = make()
    tr.handle(delta("input_json_delta", partial_json='{"file_pa'))
    assert transcript.text() == ""


# -- sub-agent attribution -----------------------------------------------------

SUB: NodeId = ("sess-1", "agent-a")


def test_subagent_messages_attribute_to_the_subagent_node() -> None:
    """
    Without this the parent row narrates work it is not doing -- a live run showed
    a session whose topic was its sub-agent's shell command.
    """
    tr, _ = make()
    tr.subagent_by_tool_use = {"toolu_agent": SUB}
    intents = tr.handle(
        AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Bash", input={"command": "wc -l x"})],
            model="m",
            parent_tool_use_id="toolu_agent",
        )
    )
    state = next(i for i in intents if isinstance(i, StateChanged))
    assert state.node_id == SUB


def test_root_messages_still_attribute_to_the_root() -> None:
    tr, _ = make()
    tr.subagent_by_tool_use = {"toolu_agent": SUB}
    intents = tr.handle(AssistantMessage(content=[TextBlock(text="hi")], model="m"))
    assert next(i for i in intents if isinstance(i, StateChanged)).node_id == NODE


def test_unjoined_parent_falls_back_to_the_root() -> None:
    """
    The tool_use_id -> agent_id join is by adjacency and can miss under parallel
    spawns. Falling back to the root keeps the activity visible rather than routing
    it to a node that does not exist.
    """
    tr, _ = make()
    intents = tr.handle(
        AssistantMessage(content=[TextBlock(text="hi")], model="m", parent_tool_use_id="unknown")
    )
    assert next(i for i in intents if isinstance(i, StateChanged)).node_id == NODE


def test_subagent_usage_is_billed_to_the_subagent() -> None:
    tr, _ = make()
    tr.subagent_by_tool_use = {"toolu_agent": SUB}
    intents = tr.handle(
        AssistantMessage(
            content=[TextBlock(text="hi")],
            model="m",
            parent_tool_use_id="toolu_agent",
            usage={"input_tokens": 5},
        )
    )
    assert next(i for i in intents if isinstance(i, UsageAccrued)).node_id == SUB


# -- per-node transcripts ---------------------------------------------------------


def _routed() -> tuple[Translator, Transcript, Transcript]:
    """A translator with one joined sub-agent, and both buffers to assert on."""
    tr, root = make()
    sub = Transcript()
    tr.subagent_by_tool_use = {"toolu_agent": SUB}
    tr.subagent_transcripts = {SUB: sub}
    return tr, root, sub


def test_subagent_output_lands_in_the_subagents_own_transcript() -> None:
    """
    The root's transcript was an unmarked interleaving of its own words and its
    sub-agents', and selecting a sub-agent showed the empty buffer the store had
    minted for it -- the hazard intents.py:61-65 names.
    """
    tr, root, sub = _routed()
    tr.handle(
        AssistantMessage(
            content=[TextBlock(text="found it"), ToolUseBlock(id="t1", name="Bash", input={})],
            model="m",
            parent_tool_use_id="toolu_agent",
        )
    )
    assert "found it" in sub.text()
    assert "Bash" in sub.text()
    assert root.text() == ""


def test_subagent_tool_results_land_in_the_subagents_own_transcript() -> None:
    tr, root, sub = _routed()
    tr.handle(
        UserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content="42 lines")],
            parent_tool_use_id="toolu_agent",
        )
    )
    assert "42 lines" in sub.text()
    assert root.text() == ""


def test_subagent_deltas_land_in_the_subagents_own_transcript() -> None:
    tr, root, sub = _routed()
    event = delta("text_delta", text="streaming")
    event.parent_tool_use_id = "toolu_agent"
    tr.handle(event)
    assert sub.text() == "streaming"
    assert root.text() == ""


def test_the_roots_own_output_is_unaffected_by_a_joined_subagent() -> None:
    tr, root, sub = _routed()
    tr.handle(AssistantMessage(content=[TextBlock(text="mine")], model="m"))
    assert root.text() == "mine"
    assert sub.text() == ""


def test_a_subagent_delta_does_not_suppress_the_roots_complete_message() -> None:
    """
    The "already streamed" mark is per node. One shared flag let a sub-agent's
    delta consume the root's, and the root's next complete message wrote nothing
    at all -- a drop, not the duplicate the flag exists to prevent.
    """
    tr, root, sub = _routed()
    event = delta("text_delta", text="sub says")
    event.parent_tool_use_id = "toolu_agent"
    tr.handle(event)
    tr.handle(AssistantMessage(content=[TextBlock(text="root says")], model="m"))
    assert root.text() == "root says"
    assert sub.text() == "sub says"


def test_an_unjoined_subagents_words_fall_back_to_the_root() -> None:
    """
    _node_of already attributes an unjoined parent_tool_use_id to the root, so the
    transcript must agree with it. Dropping the text instead would lose it.
    """
    tr, root = make()
    tr.handle(
        AssistantMessage(content=[TextBlock(text="orphan")], model="m", parent_tool_use_id="nope")
    )
    assert root.text() == "orphan"


def test_a_spawned_subagent_is_handed_the_buffer_the_session_writes_into() -> None:
    """
    Store and driver must hold one object. If the store mints its own, the UI reads
    an empty buffer while the driver fills an orphan.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentSpawned

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    session._expect_spawn(True, "toolu_agent", {"subagent_type": "alpha"})
    asyncio.run(_fire_start(session, "agent-a"))

    (spawned,) = bridge.drain()
    assert isinstance(spawned, AgentSpawned)
    assert spawned.transcript is not None

    translator = Translator(session.node_id, session.transcript)
    session._sync_subagent_map(translator)
    translator.handle(
        AssistantMessage(
            content=[TextBlock(text="from the sub")], model="m", parent_tool_use_id="toolu_agent"
        )
    )
    assert spawned.transcript.text() == "from the sub"
    assert session.transcript.text() == ""


# -- the wake path (§2.3) ---------------------------------------------------------


def _start_hook_input(agent_id: str, agent_type: str = "alpha") -> dict[str, object]:
    return {
        "hook_event_name": "SubagentStart",
        "agent_id": agent_id,
        "agent_type": agent_type,
        "session_id": "sess-1",
        "cwd": "/tmp",
        "transcript_path": "/tmp/t.jsonl",
    }


async def _fire_start(session, agent_id: str) -> None:
    await session._subagent_start(_start_hook_input(agent_id), None, None)  # type: ignore[arg-type]


def test_the_first_subagent_start_is_a_spawn() -> None:
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentSpawned

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    asyncio.run(_fire_start(session, "a9b0425f9b92a889e"))

    (intent,) = bridge.drain()
    assert isinstance(intent, AgentSpawned)
    assert intent.node_id == (session.session_id, "a9b0425f9b92a889e")


def test_a_second_start_for_the_same_agent_is_a_resume() -> None:
    """
    Measured behaviour, not a hypothetical: verify_wake_path.py saw SubagentStart
    fire again for agent_id a9b0425f9b92a889e seven seconds after that agent's own
    completion notification, because a sibling had messaged it.

    Emitting AgentSpawned twice rebuilds the record -- zeroing usage, resetting
    started_at, and swapping the Transcript the UI reads against (I7).
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentResumed, AgentSpawned

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def both() -> None:
        await _fire_start(session, "a9b0425f9b92a889e")
        await _fire_start(session, "a9b0425f9b92a889e")

    asyncio.run(both())

    first, second = bridge.drain()
    assert isinstance(first, AgentSpawned)
    assert isinstance(second, AgentResumed)
    assert second.node_id == first.node_id


def test_a_different_agent_still_spawns_after_a_resume() -> None:
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentResumed, AgentSpawned

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def three() -> None:
        await _fire_start(session, "alpha-id")
        await _fire_start(session, "alpha-id")
        await _fire_start(session, "beta-id")

    asyncio.run(three())

    kinds = [type(i).__name__ for i in bridge.drain()]
    assert kinds == [AgentSpawned.__name__, AgentResumed.__name__, AgentSpawned.__name__]


def test_a_resumed_subagent_keeps_the_join_it_was_spawned_with() -> None:
    """
    A wake is not a spawn: no Agent call was admitted for it. Binding on the resume
    path attached whatever id happened to be outstanding to a live sub-agent, whose
    real messages then missed the map and fell back to the root -- and consumed the
    entry the spawn that id belongs to is waiting for.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def spawn_then_wake() -> None:
        session._expect_spawn(True, "tu-real", {"subagent_type": "alpha"})
        await _fire_start(session, "alpha-id")
        session._expect_spawn(True, "tu-someone-elses", {"subagent_type": "alpha"})
        await _fire_start(session, "alpha-id")

    asyncio.run(spawn_then_wake())

    assert session._spawn_tool_use == {"alpha-id": "tu-real"}
    # And the other call's entry is still there for the spawn it belongs to.
    assert session._pending_spawns == {"alpha": ["tu-someone-elses"]}


# -- the spawn ledger, under the concurrency the briefing now asks for ------------


def _admit(session: AgentSession, tool_use_id: str, subagent_type: str) -> None:
    """Admit an approved Agent call the way both of the gate's allow paths do."""
    session._expect_spawn(True, tool_use_id, {"subagent_type": subagent_type})


def test_two_spawns_admitted_together_join_their_own_sub_agents() -> None:
    """
    The case a single slot cannot do at all: both Agent calls are permitted before
    either sub-agent starts, so one slot holds the second call's id and the first
    sub-agent takes it -- leaving the second bound to nothing and its words falling
    back to the root's transcript.

    The starts arrive in the opposite order to the calls, which is what shows the
    role is doing the correlating rather than the order.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def admit_both_then_start_both() -> None:
        _admit(session, "tu-builder", "builder")
        _admit(session, "tu-reviewer", "reviewer")
        await session._subagent_start(_start_hook_input("a-rev", "reviewer"), None, None)
        await session._subagent_start(_start_hook_input("a-bld", "builder"), None, None)

    asyncio.run(admit_both_then_start_both())

    assert session._spawn_tool_use == {"a-rev": "tu-reviewer", "a-bld": "tu-builder"}
    assert session._pending_spawns == {}


def test_two_spawns_of_one_role_bind_one_to_one() -> None:
    """
    Twins are matched FIFO and may end up swapped relative to their descriptions --
    nothing in either event distinguishes two simultaneous builders. What must not
    happen is the single slot's failure: one of them binding an id and the other
    binding none, which is a lost transcript rather than a mislabelled one.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def admit_two_then_start_two() -> None:
        _admit(session, "tu-first", "builder")
        _admit(session, "tu-second", "builder")
        await session._subagent_start(_start_hook_input("a-1", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-2", "builder"), None, None)

    asyncio.run(admit_two_then_start_two())

    assert session._spawn_tool_use == {"a-1": "tu-first", "a-2": "tu-second"}
    assert session._pending_spawns == {}


def test_serial_dispatch_still_never_holds_more_than_one() -> None:
    """
    The path that worked before the ledger has to keep working, and to keep costing
    one entry: a queue that grew under one-at-a-time dispatch would mean an entry is
    not being consumed.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    depths: list[int] = []

    async def one_at_a_time() -> None:
        for tool_use_id, agent_id in (("tu-1", "a-1"), ("tu-2", "a-2")):
            _admit(session, tool_use_id, "builder")
            depths.append(sum(len(q) for q in session._pending_spawns.values()))
            await session._subagent_start(_start_hook_input(agent_id, "builder"), None, None)
            depths.append(sum(len(q) for q in session._pending_spawns.values()))

    asyncio.run(one_at_a_time())

    assert depths == [1, 0, 1, 0]
    assert session._spawn_tool_use == {"a-1": "tu-1", "a-2": "tu-2"}


def test_a_start_in_a_role_nothing_was_admitted_for_takes_no_entry() -> None:
    """
    A sub-agent that spawns its own sub-agent makes an Agent call carrying an
    agent_id, which the gate never admits here. Letting that start take whatever
    entry was outstanding would misroute the admitted spawn's stream as well as its
    own.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def admit_one_then_start_a_stranger() -> None:
        _admit(session, "tu-builder", "builder")
        await session._subagent_start(_start_hook_input("a-nested", "Explore"), None, None)

    asyncio.run(admit_one_then_start_a_stranger())

    assert session._spawn_tool_use == {}
    assert session._pending_spawns == {"builder": ["tu-builder"]}


def test_the_role_join_survives_a_difference_in_case() -> None:
    """
    One side of the join is written by the model and the other is reported by the
    CLI. A key that is not normalised makes ``Builder`` and ``builder`` two roles,
    and the sub-agent that starts binds nothing.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def admit_then_start() -> None:
        _admit(session, "tu-builder", " Builder ")
        await session._subagent_start(_start_hook_input("a-1", "builder"), None, None)

    asyncio.run(admit_then_start())

    assert session._spawn_tool_use == {"a-1": "tu-builder"}


def test_the_launcher_hands_a_session_the_operators_subagent_cap() -> None:
    """
    The setting is worth nothing unless the construction site passes it. A session
    built without it falls back to the module default, which looks identical from
    every other test and ignores whatever the operator configured.
    """
    from pptmstr.app import AppState, _launch
    from pptmstr.settings import Settings

    started: list[AgentSession] = []

    class _RecordingPool:
        def submit(self, session: AgentSession) -> None:
            started.append(session)

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings(subagent_cap=6))
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(state, LaunchSpec(task="do a thing", model="claude-sonnet-5", cwd="/tmp"))
        for _ in range(200):
            if started:
                break
            time.sleep(0.005)
    finally:
        bridge.stop()

    assert [s.subagent_cap for s in started] == [6]


def test_relaunching_a_team_session_relaunches_a_team() -> None:
    """
    ``relaunch`` and ``fork`` exist to re-run work, and both dropped the template
    on the way to ``_launch`` -- so the two verbs whose whole purpose is repeating a
    session silently repeated it as a solo one. Nothing on screen said so: the new
    session runs, it just has no roles.

    The call sites now pass ``AgentRecord.template``, which is None on anything that
    is not a session root. That None is resolved here rather than at each caller, so
    the fallback is one rule instead of two that have to agree.
    """
    from pptmstr.app import AppState, _launch
    from pptmstr.settings import Settings

    started: list[AgentSession] = []

    class _RecordingPool:
        def submit(self, session: AgentSession) -> None:
            started.append(session)

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        # What a relaunch of a team session passes, then what a relaunch of a
        # sub-agent record or a solo session passes, then a name no template has.
        for template in ("feature", None, "no-such-template"):
            _launch(
                state,
                LaunchSpec(
                    task="do a thing", model="claude-sonnet-5", cwd="/tmp", template=template
                ),
            )
        for _ in range(200):
            if len(started) == 3:
                break
            time.sleep(0.005)
    finally:
        bridge.stop()

    assert [s.template.name for s in started] == ["feature", "solo", "solo"]
    assert started[0].template.roles, "a team template with no roles is a solo session"


async def _fire_stop(session, agent_id: str, answer: str = "") -> None:
    await session._subagent_stop(  # type: ignore[arg-type]
        {
            "hook_event_name": "SubagentStop",
            "agent_id": agent_id,
            "last_assistant_message": answer,
            "session_id": "sess-1",
            "cwd": "/tmp",
            "transcript_path": "/tmp/t.jsonl",
        },
        None,
        None,
    )


def test_a_resume_that_follows_a_stop_is_still_a_resume() -> None:
    """
    The order the wake path actually runs in: a sub-agent finishes, a sibling
    messages it, and the CLI starts it again under its original id. A memory of the
    id that a stop erases calls that second start a spawn, and AgentSpawned rebuilds
    the record -- zeroed usage, reset started_at, and a Transcript swapped out from
    under the UI (I7).
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentResumed, AgentSpawned

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def start_stop_start() -> None:
        await _fire_start(session, "alpha-id")
        await _fire_stop(session, "alpha-id", "done with it")
        await _fire_start(session, "alpha-id")

    asyncio.run(start_stop_start())

    kinds = [type(i).__name__ for i in bridge.drain()]
    assert kinds == [
        AgentSpawned.__name__,
        SubagentProgress.__name__,
        SubagentDelivered.__name__,
        AgentFinished.__name__,
        AgentResumed.__name__,
    ]


class _NeverSpeaks:
    """
    A message stream that goes quiet forever, which is what the grace period exists
    to bound.
    """

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()
        raise AssertionError("the stream must not produce a message")


class _SilentClient:
    def receive_messages(self):
        return _NeverSpeaks()


def _hurry(monkeypatch) -> None:
    """
    Collapse both timers so a test runs in milliseconds.

    They are set together because they mean different things and the gap between them
    is what several of these tests are about: the poll tick is how often the loop
    looks, the silence bound is how long an agent may be quiet before it is given up
    on. A poll longer than the bound would make a test pass by never looking.
    """
    from pptmstr import driver as driver_mod

    monkeypatch.setattr(driver_mod, "SUBAGENT_POLL_S", 0.001)
    monkeypatch.setattr(driver_mod, "SUBAGENT_SILENCE_S", 0.05)


async def _fire_gate(
    session, agent_id: str | None, tool_name: str = "Read", call_id: str = "tu-1"
) -> dict:
    """One PreToolUse, as a sub-agent's tool call arrives at the gate."""
    data = _gate_input(tool_name, {"file_path": "/tmp/x"}, agent_id=agent_id)
    data["tool_use_id"] = call_id
    return await session._pre_tool_use(data, call_id, None)  # type: ignore[arg-type,no-any-return]


async def _fire_post(
    session,
    agent_id: str,
    call_id: str = "tu-1",
    tool_name: str = "Read",
    failed: bool = False,
) -> None:
    """
    The closing half of one tool call, the way the CLI sends it.

    Measured in scripts/verify_post_tool_use.py: a call that succeeds fires
    PostToolUse, a call that fails fires PostToolUseFailure and *not* PostToolUse, and
    both carry the agent_id and tool_use_id their PreToolUse carried.
    """
    data: dict = {
        "hook_event_name": "PostToolUseFailure" if failed else "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": "/tmp/x"},
        "tool_use_id": call_id,
        "agent_id": agent_id,
        "session_id": "sess-1",
        "cwd": "/tmp",
        "transcript_path": "/tmp/t.jsonl",
    }
    if failed:
        data["error"] = "File does not exist."
        data["is_interrupt"] = False
    else:
        data["tool_response"] = {"type": "text"}
    await session._post_tool_use(data, call_id, None)  # type: ignore[arg-type]


async def _fire_call(session, agent_id: str, tool_name: str = "Read") -> None:
    """One whole tool call: through the gate, run, and returned."""
    await _fire_gate(session, agent_id, tool_name)
    await _fire_post(session, agent_id, tool_name=tool_name)


def test_a_working_subagent_is_not_settled_for_the_streams_silence(monkeypatch) -> None:
    """
    The defect this whole mechanism was rewritten for.

    The stream carries sub-agent assistant messages; sub-agent *completion* is the
    SubagentStop hook, and a sub-agent's tool calls announce themselves at the gate.
    So a stream that says nothing is not evidence of anything, and the previous
    implementation read it as death: it settled every live sub-agent after one quiet
    interval, while they were working, which released their capacity slots and left
    the operator looking at FAILED cards for agents still writing files.

    Here the stream never speaks at all and alpha keeps calling tools. It must
    survive, and it must survive for longer than the bound -- the loop polls
    throughout, so a single tick that judged it would end the test.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)

    async def start_then_keep_working() -> None:
        await _fire_start(session, "alpha-id")
        bridge.drain()
        waiter = asyncio.ensure_future(
            session._await_subagents(  # type: ignore[arg-type]
                _SilentClient(), Translator(session.node_id, session.transcript)
            )
        )
        # Twenty times the silence bound in elapsed time, with a tool call every
        # fifth of it -- a fixed-timer implementation gives up ten times over.
        for _ in range(20):
            await asyncio.sleep(0.01)
            await _fire_call(session, "alpha-id")
        assert not waiter.done(), "the loop settled a sub-agent that was calling tools"
        await _fire_stop(session, "alpha-id", "finished properly")
        await asyncio.wait_for(waiter, timeout=2.0)

    asyncio.run(start_then_keep_working())

    finished = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert [i.state for i in finished] == [AgentState.DONE]
    assert session._live_subagents == set()


def test_one_subagents_silence_does_not_settle_its_sibling(monkeypatch) -> None:
    """
    Liveness is per agent, because the evidence is. The predecessor kept one timer for
    the whole session and ended every live sub-agent on it, so an agent that had
    reported a moment earlier was failed for a sibling's silence -- and on a board
    where the roles work at different rhythms, the quiet one is usually the one
    thinking hardest.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)

    async def one_works_one_goes_quiet() -> None:
        await _fire_start(session, "alpha-id")
        await _fire_start(session, "beta-id")
        bridge.drain()
        waiter = asyncio.ensure_future(
            session._await_subagents(  # type: ignore[arg-type]
                _SilentClient(), Translator(session.node_id, session.transcript)
            )
        )
        for _ in range(20):
            await asyncio.sleep(0.01)
            await _fire_call(session, "alpha-id")
        assert not waiter.done()
        await _fire_stop(session, "alpha-id", "finished properly")
        await asyncio.wait_for(waiter, timeout=2.0)

    asyncio.run(one_works_one_goes_quiet())

    by_node = {i.node_id: i for i in bridge.drain() if isinstance(i, AgentFinished)}
    assert by_node[(session.session_id, "alpha-id")].state is AgentState.DONE
    beta = by_node[(session.session_id, "beta-id")]
    assert beta.state is AgentState.FAILED
    assert beta.error


def test_a_subagent_parked_in_the_approval_gate_is_never_settled(monkeypatch) -> None:
    """
    The case a clock cannot see, and the reason an in-flight call is a veto rather
    than one input among several.

    PreToolUse blocks for as long as the tool takes, and when a human is deciding that
    is up to APPROVAL_TIMEOUT_S -- six hours. Throughout it the sub-agent is working
    and produces no signal whatsoever, by construction. An implementation that only
    compared timestamps would settle exactly the agent that is waiting on the operator
    this application exists to wait on.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    released = asyncio.Event()

    async def park(*_args, **_kwargs):
        await released.wait()
        return {}

    async def start_then_park() -> None:
        monkeypatch.setattr(session, "_park", park)
        await _fire_start(session, "alpha-id")
        bridge.drain()
        gated = asyncio.ensure_future(_fire_gate(session, "alpha-id", tool_name="Write"))
        await asyncio.sleep(0)
        waiter = asyncio.ensure_future(
            session._await_subagents(  # type: ignore[arg-type]
                _SilentClient(), Translator(session.node_id, session.transcript)
            )
        )
        # Far past the bound, with the agent silent the whole way because it is
        # blocked in the gate.
        await asyncio.sleep(0.3)
        assert not waiter.done(), "a sub-agent waiting on the operator was settled"
        assert session._subagent_in_flight.get("alpha-id")
        released.set()
        await gated
        await _fire_stop(session, "alpha-id", "approved and finished")
        await asyncio.wait_for(waiter, timeout=2.0)

    asyncio.run(start_then_park())

    finished = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert [i.state for i in finished] == [AgentState.DONE]
    # The bracket has to come back down however the gate returned, or the agent stays
    # pinned alive for the session's life.
    assert not session._subagent_in_flight


def test_a_tool_call_that_outruns_the_bound_keeps_its_subagent_alive(monkeypatch) -> None:
    """
    The defect the operator watched: a sub-agent doing one WebFetch was settled FAILED
    while it was working normally.

    The gate decision is what the CLI is blocked on, so PreToolUse returns at the moment
    the tool *begins*. A bracket that came down in that hook's `finally` therefore
    covered the approval wait and nothing else, and then restarted the agent's clock as
    the tool started -- leaving a full SUBAGENT_SILENCE_S of unvetoed execution, during
    which the agent is silent by construction.

    Here alpha's call is auto-approved -- so the gate returns immediately, exactly as it
    does in the real defect -- and then the tool runs for many times the bound. It must
    survive, and it must keep its capacity slot. The second half is the other half of
    the requirement: once the call ends the veto is gone, so an agent that then really
    does go quiet is settled. A veto that never lowered would pass the first assertion
    and fail this one.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)

    async def start_then_run_one_long_call() -> None:
        await _fire_start(session, "alpha-id")
        bridge.drain()
        waiter = asyncio.ensure_future(
            session._await_subagents(  # type: ignore[arg-type]
                _SilentClient(), Translator(session.node_id, session.transcript)
            )
        )
        await _fire_gate(session, "alpha-id")
        assert session._subagent_in_flight.get(
            "alpha-id"
        ), "the gate returning is the tool starting, not the tool finishing"
        # Six times the silence bound, with the agent saying nothing because it is
        # inside one long tool call.
        await asyncio.sleep(0.3)
        assert session._live_subagents == {"alpha-id"}, "a working sub-agent was settled"
        assert session._outstanding_subagents() == 1

        await _fire_post(session, "alpha-id")
        assert not session._subagent_in_flight
        await asyncio.wait_for(waiter, timeout=2.0)

    asyncio.run(start_then_run_one_long_call())

    finished = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert [i.state for i in finished] == [AgentState.FAILED]
    assert session._outstanding_subagents() == 0


def test_a_denied_call_leaves_no_veto_behind(monkeypatch) -> None:
    """
    The path that would trade a false FAILED for a permanent false RUNNING.

    A denied tool never runs, so the CLI fires no PostToolUse and no
    PostToolUseFailure for it -- measured in scripts/verify_post_tool_use.py. This
    application denies by default, so a bracket that waited for a closing hook that
    cannot arrive would pin nearly every sub-agent alive for the session's life, still
    holding its capacity slot.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    # Headless, so a tool needing review is denied rather than parked.
    session = AgentSession(bridge, task="lead", interactive=False)

    async def start_then_be_denied() -> None:
        await _fire_start(session, "alpha-id")
        bridge.drain()
        out = await _fire_gate(session, "alpha-id", tool_name="Write")
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert not session._subagent_in_flight, "a denied call left its veto raised"
        # And the agent is then judged on its clock like any other, rather than
        # spinning RUNNING forever.
        waiter = asyncio.ensure_future(
            session._await_subagents(  # type: ignore[arg-type]
                _SilentClient(), Translator(session.node_id, session.transcript)
            )
        )
        await asyncio.wait_for(waiter, timeout=2.0)

    asyncio.run(start_then_be_denied())

    finished = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert [i.state for i in finished] == [AgentState.FAILED]


def test_a_call_that_failed_lowers_its_veto_like_one_that_succeeded(monkeypatch) -> None:
    """
    A tool that errors fires PostToolUseFailure and *not* PostToolUse
    (scripts/verify_post_tool_use.py). Watching only the success event would leave the
    veto raised on every failed call, which is a routine event -- a Read of a path that
    does not exist is enough.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)

    async def start_then_fail_a_call() -> None:
        await _fire_start(session, "alpha-id")
        bridge.drain()
        await _fire_gate(session, "alpha-id")
        assert session._subagent_in_flight.get("alpha-id")
        await _fire_post(session, "alpha-id", failed=True)
        assert not session._subagent_in_flight

    asyncio.run(start_then_fail_a_call())


def test_one_calls_ending_cannot_lower_another_calls_veto(monkeypatch) -> None:
    """
    Why the record is keyed by tool_use_id rather than counted.

    A count cannot distinguish a duplicate close from a second call: closing the same
    call twice would decrement the veto belonging to a *different* tool that is still
    running, and settle the agent in the middle of it. Nothing observed sends a
    duplicate today -- this pins the property rather than a bug, because the failure it
    prevents is a working agent reported dead and the cost of the property is a dict.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)

    async def two_calls_one_of_them_closed_twice() -> None:
        await _fire_start(session, "alpha-id")
        bridge.drain()
        await _fire_gate(session, "alpha-id", call_id="tu-1")
        await _fire_gate(session, "alpha-id", call_id="tu-2")
        await _fire_post(session, "alpha-id", call_id="tu-1")
        await _fire_post(session, "alpha-id", call_id="tu-1")
        assert set(session._subagent_in_flight["alpha-id"]) == {"tu-2"}

        session._subagent_seen_at["alpha-id"] = time.monotonic() - 10.0
        session._settle_silent_subagents()
        assert session._live_subagents == {"alpha-id"}, "tu-2 was still running"

        await _fire_post(session, "alpha-id", call_id="tu-2")
        # The closing hook is itself a signal, so it restarts the silence clock. Age
        # the stamp again to isolate the veto from the liveness it also carries.
        session._subagent_seen_at["alpha-id"] = time.monotonic() - 10.0
        session._settle_silent_subagents()
        assert session._live_subagents == set()

    asyncio.run(two_calls_one_of_them_closed_twice())


def test_a_veto_nothing_ever_lowers_expires(monkeypatch) -> None:
    """
    The bracket spans two hook invocations, so unlike a try/finally it can be left
    raised by a signal that never arrives. Every closing signal the CLI is known to
    emit is handled, but an unbounded veto turns any one that is missed into a
    sub-agent stuck RUNNING for the session's life, holding a capacity slot nothing can
    reclaim -- worse than the false FAILED being fixed, because nothing clears it.
    """
    _hurry(monkeypatch)
    from pptmstr import driver as driver_mod
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    monkeypatch.setattr(driver_mod, "SUBAGENT_CALL_VETO_S", 0.2)

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)

    async def a_call_that_never_returns() -> None:
        await _fire_start(session, "alpha-id")
        bridge.drain()
        await _fire_gate(session, "alpha-id")
        # Past the silence bound but inside the veto: still working, as far as anyone
        # here can tell.
        session._subagent_seen_at["alpha-id"] = time.monotonic() - 10.0
        session._settle_silent_subagents()
        assert session._live_subagents == {"alpha-id"}

        session._subagent_in_flight["alpha-id"]["tu-1"] = time.monotonic() - 1.0
        session._settle_silent_subagents()
        assert session._live_subagents == set(), "a veto with no closer held forever"

    asyncio.run(a_call_that_never_returns())

    finished = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert [i.state for i in finished] == [AgentState.FAILED]
    assert session._outstanding_subagents() == 0


def test_a_live_subagent_with_no_clock_is_settled_rather_than_spared() -> None:
    """
    A missing timestamp used to default to `now`, which reads as "seen this instant"
    and so as "never settle". The invariant that made it unreachable holds -- every
    live agent is stamped by SubagentStart -- but the failure mode if it ever stopped
    holding is silent and permanent, and the honest default for no evidence of life is
    not immortality.
    """
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)
    asyncio.run(_fire_start(session, "alpha-id"))
    bridge.drain()

    session._subagent_seen_at.pop("alpha-id")
    session._settle_silent_subagents()

    assert session._live_subagents == set()
    (finished,) = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert finished.state is AgentState.FAILED
    assert "no liveness record" in (finished.error or "")


def test_a_settled_subagent_gives_its_capacity_slot_back_and_a_live_one_does_not(
    monkeypatch,
) -> None:
    """
    The cap is counted off the live set, so getting liveness wrong quietly moves the
    bound. The predecessor cleared the whole set on one quiet interval while the
    agents were still running: their slots were released, their token burn continued,
    and the session could then admit a fresh set on top of them.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)

    async def one_works_one_goes_quiet() -> None:
        await _fire_start(session, "alpha-id")
        await _fire_start(session, "beta-id")
        assert session._outstanding_subagents() == 2
        waiter = asyncio.ensure_future(
            session._await_subagents(  # type: ignore[arg-type]
                _SilentClient(), Translator(session.node_id, session.transcript)
            )
        )
        for _ in range(20):
            await asyncio.sleep(0.01)
            await _fire_call(session, "alpha-id")
        # Only the one that really stopped reporting has given its slot back.
        assert session._outstanding_subagents() == 1
        await _fire_stop(session, "alpha-id", "finished properly")
        await asyncio.wait_for(waiter, timeout=2.0)
        assert session._outstanding_subagents() == 0

    asyncio.run(one_works_one_goes_quiet())


class _Feed:
    """
    A message stream shaped the way the SDK's really is: ``receive_messages()`` is an
    async *generator*.

    That shape is the whole point of this double. A cancelled ``__anext__`` terminates
    a generator's frame permanently, so every later ``__anext__`` raises
    StopAsyncIteration without waiting. A hand-written ``__anext__`` -- like
    ``_NeverSpeaks`` above -- builds a fresh coroutine per call and survives being
    cancelled, which hides that defect completely.
    """

    def __init__(self, ends_at_once: bool = False) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.ends_at_once = ends_at_once

    async def receive_messages(self):
        if self.ends_at_once:
            return
        while True:
            message = await self.queue.get()
            if message is None:
                return
            yield message


def test_a_poll_tick_does_not_end_the_read(monkeypatch) -> None:
    """
    The defect the operator watched: a sub-agent reported FAILED that then carried on
    working.

    The loop timed each read out to poll, and a timeout around ``__anext__`` cancels
    it. That terminated the generator ``receive_messages()`` returns, so the next tick
    got StopAsyncIteration immediately -- from a transport that was wide open -- and
    the loop read it as a closed session and settled everyone. With the poll tick at a
    second, one quiet second was enough.

    Here alpha works steadily across many ticks and the stream stays open throughout.
    It must not be judged, it must keep its capacity slot, and the read must still be
    able to deliver a message afterwards.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)
    feed = _Feed()

    async def work_through_many_ticks() -> None:
        await _fire_start(session, "alpha-id")
        bridge.drain()
        waiter = asyncio.ensure_future(
            session._await_subagents(  # type: ignore[arg-type]
                feed, Translator(session.node_id, session.transcript)
            )
        )
        for _ in range(20):
            await asyncio.sleep(0.01)
            await _fire_call(session, "alpha-id")
        assert not waiter.done(), "a poll tick was read as a closed stream"
        assert session._live_subagents == {"alpha-id"}
        assert session._outstanding_subagents() == 1
        # The read survived every one of those ticks, so it can still carry a message.
        # Drained by polling rather than by one long sleep: a sleep here is silence
        # like any other, and past the bound it would settle alpha for real.
        feed.queue.put_nowait(AssistantMessage(content=[TextBlock(text="still here")], model="m"))
        for _ in range(20):
            await asyncio.sleep(0.001)
            if feed.queue.empty():
                break
        assert feed.queue.empty(), "the read was destroyed by a tick and took no message"
        await _fire_stop(session, "alpha-id", "finished properly")
        await asyncio.wait_for(waiter, timeout=2.0)

    asyncio.run(work_through_many_ticks())

    finished = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert [i.state for i in finished] == [AgentState.DONE]


def test_a_closed_stream_settles_everything_still_outstanding(monkeypatch) -> None:
    """
    The one collective verdict, and the only case that deserves one: a stream that has
    ended can never deliver another message for anybody, where a quiet one says nothing
    about anyone. It holds across both channels, not only this one -- the SDK reads the
    transport in a single task that routes control frames to the hook callbacks, and the
    end of this stream is that task's own teardown, so no SubagentStop can follow it.
    Without this a record never reaches a terminal state, its card spins for the
    session's life, and the capacity count can only shrink.

    Built on ``_Feed`` rather than a hand-written iterator on purpose: the verdict is
    only sound if the stream really ended, and only a generator can tell the difference
    between that and a read this loop destroyed itself.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def start_two_then_read_a_closed_stream() -> None:
        await _fire_start(session, "alpha-id")
        await _fire_start(session, "beta-id")
        bridge.drain()
        await asyncio.wait_for(
            session._await_subagents(  # type: ignore[arg-type]
                _Feed(ends_at_once=True), Translator(session.node_id, session.transcript)
            ),
            timeout=2.0,
        )

    asyncio.run(start_two_then_read_a_closed_stream())

    finished = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert {i.node_id for i in finished} == {
        (session.session_id, "alpha-id"),
        (session.session_id, "beta-id"),
    }
    assert all(i.state is AgentState.FAILED for i in finished)
    assert all("closed" in (i.error or "") for i in finished)
    assert session._live_subagents == set()


def test_no_read_is_left_in_flight_when_the_wait_returns(monkeypatch) -> None:
    """
    Both this loop and the session's main read pull from one single-consumer stream,
    where a message goes to exactly one receiver. The main loop resumes the instant
    this returns, so a read left pending here would take the message meant for it.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", interactive=False)
    feed = _Feed()

    async def wait_then_check_nobody_is_still_reading() -> None:
        await _fire_start(session, "alpha-id")
        waiter = asyncio.ensure_future(
            session._await_subagents(  # type: ignore[arg-type]
                feed, Translator(session.node_id, session.transcript)
            )
        )
        await asyncio.sleep(0.01)
        await _fire_stop(session, "alpha-id", "finished properly")
        await asyncio.wait_for(waiter, timeout=2.0)
        feed.queue.put_nowait(
            AssistantMessage(content=[TextBlock(text="for the main loop")], model="m")
        )
        await asyncio.sleep(0.05)
        assert feed.queue.qsize() == 1, "the abandoned read swallowed the next message"

    asyncio.run(wait_then_check_nobody_is_still_reading())


def test_a_wake_after_a_settlement_is_still_not_a_respawn(monkeypatch) -> None:
    """
    Giving up on a sub-agent says nothing about whether it can be woken. The id has to
    stay known, or the wake rebuilds the record the settlement just ended.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentResumed

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def start_settle_then_wake() -> None:
        await _fire_start(session, "alpha-id")
        await asyncio.sleep(0.1)
        session._settle_silent_subagents()
        assert session._seen_subagents == {"alpha-id"}
        bridge.drain()
        await _fire_start(session, "alpha-id")

    asyncio.run(start_settle_then_wake())

    (woken,) = bridge.drain()
    assert isinstance(woken, AgentResumed)
    assert woken.node_id == (session.session_id, "alpha-id")


def test_a_resumed_subagent_does_not_inherit_its_own_stale_clock(monkeypatch) -> None:
    """
    A wake reuses the id, so the bookkeeping has to be cleared by the stop rather than
    overwritten by the next start. A last-seen left behind from before the agent
    stopped makes the resumed agent instantly overdue -- it would be settled on the
    first poll tick, having just started.
    """
    _hurry(monkeypatch)
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def start_stop_wait_then_wake() -> None:
        await _fire_start(session, "alpha-id")
        await _fire_stop(session, "alpha-id", "done")
        assert "alpha-id" not in session._subagent_seen_at
        await asyncio.sleep(0.1)  # longer than the silence bound
        await _fire_start(session, "alpha-id")
        session._settle_silent_subagents()

    asyncio.run(start_stop_wait_then_wake())

    assert session._live_subagents == {"alpha-id"}
    failed = [
        i for i in bridge.drain() if isinstance(i, AgentFinished) and i.state is AgentState.FAILED
    ]
    assert not failed, "a just-resumed sub-agent was settled on a clock from its previous life"


# -- the bus stamp (§2.7) ---------------------------------------------------------
#
# An in-process MCP handler is handed only the tool name and its arguments. The
# gate is the only participant that knows who called, so these pin the one
# mechanism that gives a concern a sender at all.


def _gate_input(tool_name: str, tool_input: dict, agent_id: str | None = None) -> dict:
    data: dict = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_use_id": "tu-1",
        "session_id": "sess-1",
        "cwd": "/tmp",
        "transcript_path": "/tmp/t.jsonl",
    }
    if agent_id is not None:
        data["agent_id"] = agent_id
    return data


def _decision(out) -> dict:
    return out["hookSpecificOutput"]


def test_an_auto_approved_bus_call_still_gets_a_sender() -> None:
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.bus import FROM_KEY, qualified
    from pptmstr.driver import AgentSession

    session = AgentSession(Bridge(), task="lead")
    out = asyncio.run(
        session._pre_tool_use(_gate_input(qualified("read_inbox"), {}, "agent-qa"), None, None)
    )

    spec = _decision(out)
    assert spec["permissionDecision"] == "allow"
    # The stamp is authentication, not policy. read_inbox is never reviewed, and it
    # still cannot run without knowing whose inbox it is.
    assert spec["updatedInput"][FROM_KEY] == [session.session_id, "agent-qa"]


def test_a_root_call_is_stamped_with_no_agent_id() -> None:
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.bus import FROM_KEY, qualified
    from pptmstr.driver import AgentSession

    session = AgentSession(Bridge(), task="lead")
    out = asyncio.run(
        session._pre_tool_use(_gate_input(qualified("claim_task"), {}, None), None, None)
    )

    # NodeId's shape exactly: root sessions have agent_id None (I6).
    assert _decision(out)["updatedInput"][FROM_KEY] == [session.session_id, None]


def test_a_non_bus_tool_is_not_rewritten() -> None:
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    session = AgentSession(Bridge(), task="lead")
    out = asyncio.run(
        session._pre_tool_use(_gate_input("Read", {"file_path": "/tmp/x"}), None, None)
    )

    # Stamping everything would put a private key into the arguments of every tool
    # in the CLI, several of which validate their input strictly.
    assert "updatedInput" not in _decision(out)


def test_the_operators_edit_cannot_change_who_sent_it() -> None:
    import asyncio

    from pptmstr.bridge import Bridge, Decision
    from pptmstr.bus import FROM_KEY, qualified
    from pptmstr.driver import AgentSession

    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(bridge, task="lead")

        async def drive() -> dict:
            call = asyncio.create_task(
                session._pre_tool_use(
                    _gate_input(
                        qualified("post_concern"),
                        {"to": "dev", "subject": "s", "body": "original"},
                        "agent-qa",
                    ),
                    None,
                    None,
                )
            )
            # Let the gate park and register its future before answering it.
            for _ in range(200):
                await asyncio.sleep(0.005)
                if bridge.parked_count:
                    break
            (pending,) = [i for i in bridge.drain() if hasattr(i, "pending")]
            bridge.resolve(
                pending.pending.id,
                Decision(
                    approved=True,
                    edited_args={
                        "to": "dev",
                        "subject": "s",
                        "body": "narrowed",
                        # An edit that tries to reattribute the message.
                        FROM_KEY: ["sess-1", "agent-lead"],
                    },
                ),
            )
            return await call

        out = asyncio.run_coroutine_threadsafe(drive(), bridge.loop).result(timeout=10)
    finally:
        bridge.stop()

    spec = _decision(out)
    assert spec["permissionDecision"] == "allow"
    # The operator's rewrite of the body is honoured; their rewrite of the sender
    # is not, because the stamp is applied last.
    assert spec["updatedInput"]["body"] == "narrowed"
    assert spec["updatedInput"][FROM_KEY] == [session.session_id, "agent-qa"]


# -- work templates ---------------------------------------------------------------


def test_a_lone_agent_configures_no_team() -> None:
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    options = AgentSession(Bridge(), task="t")._options()

    # None, not an empty dict. Solo must produce exactly the options the SDK saw
    # before templates existed, or every existing session changes shape at once.
    assert options.agents is None
    assert options.system_prompt is None


def test_a_template_becomes_agent_definitions() -> None:
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    options = AgentSession(Bridge(), task="t", template=FEATURE)._options()

    assert options.agents is not None
    assert set(options.agents) == set(FEATURE.role_names())
    builder = options.agents["builder"]
    assert "implement" in builder.prompt.lower()
    # The worker half is appended, or a role has no idea it is on a team.
    assert "read_inbox()" in builder.prompt


def test_a_role_without_a_model_inherits_the_sessions() -> None:
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    options = AgentSession(Bridge(), task="t", model="claude-opus-4-5", template=FEATURE)._options()

    assert options.agents is not None
    # "inherit", not the session's model string: the launcher's choice applies to
    # the whole team, and hard-coding it here would silently pin roles to whatever
    # the lead happened to be launched with even after a set_model.
    assert options.agents["builder"].model == "inherit"


def _template_with_model(model: str | None) -> WorkTemplate:
    return WorkTemplate(
        name="two-model",
        description="d",
        lead_prompt="p",
        roles=(Role(name="builder", description="d", prompt="p", model=model),),
    )


def test_a_spawned_subagent_records_its_roles_own_model() -> None:
    """
    The role's model is what the SDK is given (`role.model or "inherit"`), so a
    record built from the session's states a model the sub-agent is not running on.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentSpawned

    bridge = Bridge()
    session = AgentSession(
        bridge,
        task="t",
        model="claude-sonnet-5",
        template=_template_with_model("claude-opus-4-5"),
    )
    asyncio.run(session._subagent_start(_start_hook_input("a-1", "builder"), None, None))  # type: ignore[arg-type]

    (spawned,) = bridge.drain()
    assert isinstance(spawned, AgentSpawned)
    assert spawned.model == "claude-opus-4-5"


def test_a_role_with_no_model_of_its_own_is_recorded_as_the_sessions() -> None:
    """
    "inherit" reaches the SDK; the record has to name what that resolved to, and
    the session's model is the only thing that does.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentSpawned

    bridge = Bridge()
    session = AgentSession(
        bridge,
        task="t",
        model="claude-sonnet-5",
        template=_template_with_model(None),
    )
    asyncio.run(session._subagent_start(_start_hook_input("a-1", "builder"), None, None))  # type: ignore[arg-type]

    (spawned,) = bridge.drain()
    assert isinstance(spawned, AgentSpawned)
    assert spawned.model == "claude-sonnet-5"


def test_an_agent_type_outside_the_template_falls_back_to_the_session_model() -> None:
    """
    The CLI resolves its own agent definitions and the hook is not told their
    model. Naming the session's is the only answer available.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentSpawned

    bridge = Bridge()
    session = AgentSession(
        bridge,
        task="t",
        model="claude-sonnet-5",
        template=_template_with_model("claude-opus-4-5"),
    )
    asyncio.run(session._subagent_start(_start_hook_input("a-1", "Explore"), None, None))  # type: ignore[arg-type]

    (spawned,) = bridge.drain()
    assert isinstance(spawned, AgentSpawned)
    assert spawned.model == "claude-sonnet-5"


def test_a_restricted_role_reaches_the_sdk_with_the_bus_attached() -> None:
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import BUS_TOOL_NAMES, FEATURE

    options = AgentSession(Bridge(), task="t", template=FEATURE)._options()

    assert options.agents is not None
    reviewer = options.agents["reviewer"].tools
    assert reviewer is not None
    assert set(BUS_TOOL_NAMES) <= set(reviewer)
    assert "Edit" not in reviewer


def test_the_briefing_is_appended_not_substituted() -> None:
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    options = AgentSession(Bridge(), task="t", template=FEATURE)._options()

    # A bare string would discard Claude Code's own system prompt -- the tool
    # conventions and environment description this agent still needs -- to say a few
    # paragraphs about delegation.
    assert isinstance(options.system_prompt, dict)
    assert options.system_prompt["type"] == "preset"
    assert options.system_prompt["preset"] == "claude_code"
    assert "**builder**" in options.system_prompt["append"]


def test_an_unstarted_role_is_a_different_error_from_an_unknown_one() -> None:
    """
    The wake-path probe's worker retried the same wrong name because the refusal
    did not say which mistake it had made. A role that exists but has not spawned is
    a timing problem the lead can fix; a misspelling is not.
    """
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    session = AgentSession(Bridge(), task="t", template=FEATURE)

    unstarted = session.role_status("builder")
    assert "has not been started" in unstarted
    assert "subagent_type='builder'" in unstarted

    unknown = session.role_status("qa")
    assert "No agent known as" in unknown
    assert "lead" in unknown


def test_a_role_becomes_reachable_once_it_spawns() -> None:
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    session = AgentSession(Bridge(), task="t", template=FEATURE)
    assert session.resolve_role("builder") is None

    asyncio.run(session._subagent_start(_start_hook_input("a-1", "builder"), None, None))

    assert session.resolve_role("builder") == (session.session_id, "a-1")
    assert session.role_of((session.session_id, "a-1")) == "builder"
    # "lead" always resolves, because a worker's first instinct is to report upward.
    assert session.resolve_role("lead") == session.node_id


def test_a_second_agent_of_one_role_gets_an_address_of_its_own() -> None:
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    session = AgentSession(Bridge(), task="t", template=FEATURE)

    async def two() -> None:
        await session._subagent_start(_start_hook_input("a-1", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-2", "builder"), None, None)

    asyncio.run(two())

    # Both properties, and neither alone is the design. The first keeps the bare
    # name, so nothing a lead already addressed retargets to whichever twin spawned
    # last; the second is reachable at an ordinal, so it is not write-only -- able to
    # claim and to post, and never able to be answered. That is the convention the
    # lead briefing hands the model.
    assert session.resolve_role("builder") == (session.session_id, "a-1")
    assert session.resolve_role("builder-2") == (session.session_id, "a-2")
    assert session.known_roles() == ("lead", "builder", "builder-2")


def test_every_address_a_sender_is_shown_can_be_replied_to() -> None:
    """
    ``bus.py`` renders ``role_of(sender)`` in front of a model that will plausibly
    write it straight back into ``post_concern(to=...)``. A name that does not round
    trip is worse than no name: it reads as an invitation to answer an agent nothing
    routes to.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    session = AgentSession(Bridge(), task="t", template=FEATURE)

    async def three() -> None:
        await session._subagent_start(_start_hook_input("a-1", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-2", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-3", "reviewer"), None, None)

    asyncio.run(three())

    for node in [session.node_id] + [(session.session_id, a) for a in ("a-1", "a-2", "a-3")]:
        address = session.role_of(node)
        assert address is not None
        assert session.resolve_role(address) == node


def test_a_resumed_subagent_keeps_the_address_it_was_given() -> None:
    """
    A sibling's SendMessage wakes a finished sub-agent and SubagentStart fires again
    under its original id. Allocating afresh would hand it a second address and leave
    every concern already written to the first one pointing at a sibling.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    session = AgentSession(Bridge(), task="t", template=FEATURE)

    async def spawn_wake() -> None:
        await session._subagent_start(_start_hook_input("a-1", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-2", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-1", "builder"), None, None)

    asyncio.run(spawn_wake())

    assert session.resolve_role("builder") == (session.session_id, "a-1")
    assert session.resolve_role("builder-2") == (session.session_id, "a-2")
    assert session.resolve_role("builder-3") is None


def test_an_ordinal_nobody_is_running_is_a_count_not_a_spelling_mistake() -> None:
    """
    "builder-3" with two builders up is neither a typo nor a role waiting to start.
    The lead can only act on it -- address one of the two, or start a third -- if the
    refusal says how many are actually reachable.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import FEATURE

    session = AgentSession(Bridge(), task="t", template=FEATURE)

    async def two() -> None:
        await session._subagent_start(_start_hook_input("a-1", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-2", "builder"), None, None)

    asyncio.run(two())

    counting = session.role_status("builder-3")
    assert session.resolve_role("builder-3") is None
    assert "2 agent(s)" in counting
    assert "builder, builder-2" in counting
    # Not the other two arms: nothing here is misspelled, and the role is running.
    assert "No agent known as" not in counting
    assert "has not been started" not in counting

    # The role with none of it running is still the timing mistake, addressed by
    # ordinal or not: the fix is to start one, not to pick a different name.
    session._roles.clear()
    assert "has not been started" in session.role_status("builder-2")


def test_a_role_the_template_names_is_never_allocated_to_another_role() -> None:
    """
    A template may define a role literally called "builder-2". If the second builder
    took that address, a concern to the role would reach a builder and the role
    itself would be unaddressable -- two agents at one name, which is the failure the
    address table exists to prevent.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.templates import Role, WorkTemplate

    template = WorkTemplate(
        name="collide",
        description="d",
        lead_prompt="p",
        roles=(
            Role(name="builder", description="d", prompt="p"),
            Role(name="builder-2", description="d", prompt="p"),
        ),
    )
    session = AgentSession(Bridge(), task="t", template=template)

    async def three() -> None:
        await session._subagent_start(_start_hook_input("a-1", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-2", "builder"), None, None)
        await session._subagent_start(_start_hook_input("a-3", "builder-2"), None, None)

    asyncio.run(three())

    assert session.resolve_role("builder") == (session.session_id, "a-1")
    # Skipped, not shared: the second builder steps over the name the template owns.
    assert session.resolve_role("builder-3") == (session.session_id, "a-2")
    assert session.resolve_role("builder-2") == (session.session_id, "a-3")


def test_the_roots_own_names_are_never_handed_to_a_sub_agent() -> None:
    """
    "lead", "main" and "root" are how a worker addresses the agent that gave it the
    job. A sub-agent of type "lead" -- which the CLI will resolve from its own
    definitions whether or not a template declares it -- taking that address would
    silently divert every upward report.
    """
    import asyncio

    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    session = AgentSession(Bridge(), task="t")
    asyncio.run(session._subagent_start(_start_hook_input("a-1", "lead"), None, None))

    assert session.resolve_role("lead") == session.node_id
    assert session.resolve_role("main") == session.node_id
    assert session.role_of((session.session_id, "a-1")) == "lead-2"


def test_announce_carries_the_template_into_the_store() -> None:
    """
    The wiring, not the record. `AgentRecord.template` and the reducer arm that
    fills it both pass while `announce` emits no template at all, and the pane
    then treats every team as solo and draws no board.
    """
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentSpawned
    from pptmstr.store import Store
    from pptmstr.templates import FEATURE

    bridge = Bridge()
    session = AgentSession(bridge, task="lead", template=FEATURE)
    session.announce()

    (intent,) = bridge.drain()
    assert isinstance(intent, AgentSpawned)
    assert intent.template == "feature"

    store = Store()
    store.apply(intent)
    assert store.snapshot().nodes[session.node_id].template == "feature"


def test_a_session_launched_with_no_template_announces_solo() -> None:
    """
    AgentSession defaults to SOLO rather than None, so the record says which
    shape it is rather than leaving the UI to guess from an absence.
    """
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentSpawned

    bridge = Bridge()
    AgentSession(bridge, task="lead").announce()

    (intent,) = bridge.drain()
    assert isinstance(intent, AgentSpawned)
    assert intent.template == "solo"


# -- what a turn ending does to the session (2026-08-11-turn-end-still-marks-done) --


class _FakeClient:
    """
    Enough of ClaudeSDKClient for ``run()`` to drive a session with no subprocess.

    ``on_frame`` runs once each message has been fully handled, standing in for the
    UI thread draining the bridge on its own cadence. The hazard these tests cover
    is a state that is only right once a *later* intent lands, so a test that
    inspects the world solely at the end of the stream cannot see it.
    """

    def __init__(
        self,
        messages: list[object],
        on_frame: object = None,
        *,
        stay_open: bool = False,
    ) -> None:
        self._messages = messages
        self._on_frame = on_frame
        self._stay_open = stay_open

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def query(self, prompt: object, session_id: str = "default") -> None:
        return None

    async def get_context_usage(self) -> dict:
        # _poll_context logs and drops failures, so this keeps ContextPolled out of
        # the intent stream without the test having to filter it.
        raise RuntimeError("no CLI attached")

    async def receive_messages(self):
        for message in self._messages:
            yield message
            if self._on_frame is not None:
                self._on_frame()
        if self._stay_open:
            await asyncio.Event().wait()


def _fake_client(messages: list[object], **kwargs):
    """
    Patch the SDK client for the duration of a block.

    A context manager rather than the ``monkeypatch`` fixture the tests above use,
    because the cancellation cases need the patch to span an ``asyncio.run`` from
    inside the coroutine that drives the pool.
    """
    return patch("pptmstr.driver.ClaudeSDKClient", lambda **_: _FakeClient(messages, **kwargs))


async def _settle(turns: int = 3) -> None:
    """
    Let a freshly created session task reach the point where it parks.

    Nothing in _FakeClient suspends before the stay_open wait, so one turn is
    enough; the rest are slack against that stopping being true.
    """
    for _ in range(turns):
        await asyncio.sleep(0)


def _drive(messages: list[object], monkeypatch, **kwargs) -> tuple[Store, list[Snapshot]]:
    """
    Run one session against a fake client, applying every intent to a real Store.

    Asserting on the drained intents instead would pass while a later
    AgentFinished(DONE) overwrote the state under test: only the record says
    whether a decision stood.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="do a thing")
    store = Store()
    frames: list[Snapshot] = []

    def frame() -> None:
        store.apply_all(bridge.drain())
        frames.append(store.snapshot())

    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient(messages, frame, **kwargs),
    )
    asyncio.run(session.run())
    frame()
    return store, frames


def _record(store: Store):
    (node,) = store.snapshot().order
    return store.snapshot().nodes[node]


def test_a_failed_turn_stays_failed(monkeypatch) -> None:
    """
    The failure signal survives the rest of the loop.

    Both ways of losing it are live: the post-result block's own
    StateChanged(AWAITING_INPUT), and the AgentFinished(DONE) emitted when the CLI
    closes the stream behind the error.
    """
    store, _ = _drive(
        [result(is_error=True, errors=["overloaded"], api_error_status=529)], monkeypatch
    )

    rec = _record(store)
    assert rec.state is AgentState.FAILED
    assert rec.error is not None and "529" in rec.error
    (obligation,) = store.snapshot().needs_you
    assert isinstance(obligation, SessionFailed)


def test_a_recovered_session_can_still_be_closed_normally(monkeypatch) -> None:
    """
    The standing failure is per result, not latched. A session that errored and then
    took another turn successfully is an ordinary session again, so the stream
    closing behind it means DONE.
    """
    store, _ = _drive(
        [result(is_error=True, errors=["overloaded"]), result(terminal_reason="completed")],
        monkeypatch,
    )

    assert _record(store).state is AgentState.DONE


def test_an_ordinary_turn_end_leaves_the_session_messageable(monkeypatch) -> None:
    store, frames = _drive([result(terminal_reason="completed")], monkeypatch)

    at_turn_end = frames[0].nodes[frames[0].order[0]]
    assert at_turn_end.state is AgentState.AWAITING_INPUT
    assert at_turn_end.topic == AWAITING_TOPIC
    assert not at_turn_end.state.is_terminal


def test_a_turn_ending_forgets_a_spawn_that_never_started(monkeypatch) -> None:
    """
    Driven through run() rather than by calling the method, because the leak is only
    closed if the turn boundary actually calls it. An entry nothing removes sits in
    a table the cap counts, so a session that admitted a spawn which never started
    loses that capacity for its whole life -- the shape `_await_subagents` had.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="do a thing")
    _admit(session, "tu-never", "builder")

    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient([result(terminal_reason="completed")]),
    )
    asyncio.run(session.run())

    assert session._pending_spawns == {}
    assert session._outstanding_subagents() == 0


def test_a_turn_ending_does_not_disturb_a_join_already_made(monkeypatch) -> None:
    """
    Forgetting the ledger is not forgetting the joins it produced: the map the
    translator reads from outlives every turn boundary, because a sub-agent keeps
    streaming after the parent's result.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="do a thing")
    _admit(session, "tu-real", "builder")
    asyncio.run(session._subagent_start(_start_hook_input("a-1", "builder"), None, None))
    bridge.drain()

    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient([result(terminal_reason="completed")]),
    )
    asyncio.run(session.run())

    assert session._spawn_tool_use == {"a-1": "tu-real"}


def test_an_interrupted_turn_says_so_where_the_operator_reads(monkeypatch) -> None:
    """
    An interrupt is the recoverable lever, so it must not land on a terminal state
    -- but conflating it with an ordinary turn end would tell the operator their
    interrupt did nothing. The topic carries it, and the inbox row must carry it
    too: that is the list the operator actually works from.
    """
    store, frames = _drive([result(terminal_reason="aborted_streaming")], monkeypatch)

    snap = frames[0]
    rec = snap.nodes[snap.order[0]]
    assert rec.state is AgentState.AWAITING_INPUT
    assert rec.topic == INTERRUPTED_TOPIC

    (obligation,) = snap.needs_you
    assert isinstance(obligation, QuestionPending)
    assert "interrupted" in obligation.summary


def test_an_uninterrupted_turn_does_not_claim_to_be_interrupted(monkeypatch) -> None:
    _, frames = _drive([result(terminal_reason="completed")], monkeypatch)

    (obligation,) = frames[0].needs_you
    assert isinstance(obligation, QuestionPending)
    assert "interrupted" not in obligation.summary


def test_a_failure_survives_a_later_state_change(monkeypatch) -> None:
    """
    The failure is re-asserted at stream close, not merely left standing.

    Between the error and the close, anything emitting a StateChanged for this node
    moves the record off FAILED -- the store's arm guards on `rec.pending` and not
    on terminality. A rate-limit rejection is the case that arrives with a 529
    storm, which is exactly when the error result happened too. Without the
    re-assert the session ends RATE_LIMITED: non-terminal, no obligation, and a
    reply box enabled on a subprocess that is gone.
    """
    store, _ = _drive(
        [result(is_error=True, errors=["overloaded"], api_error_status=529), rate("rejected")],
        monkeypatch,
    )

    rec = _record(store)
    assert rec.state is AgentState.FAILED
    assert rec.error is not None and "529" in rec.error
    (obligation,) = store.snapshot().needs_you
    assert isinstance(obligation, SessionFailed)


def test_the_re_assert_keeps_the_moment_the_session_died(monkeypatch) -> None:
    """
    The obligation's wait is measured from ended_at, so re-stamping it at close
    would restart the clock on a failure the operator has been ignoring for a
    while -- and the length of that wait is what orders the inbox.
    """
    store, frames = _drive(
        [result(is_error=True, errors=["overloaded"]), rate("rejected")], monkeypatch
    )

    at_failure = frames[0].nodes[frames[0].order[0]]
    assert at_failure.ended_at is not None
    assert _record(store).ended_at == at_failure.ended_at


def test_an_operator_close_reads_as_closed() -> None:
    """
    The wiring, not the flag: `SessionPool.close` states that the teardown was asked
    for, and it is the only participant that knows. DONE deliberately wins over a
    standing FAILED here -- closing *is* the dismissal, and leaving FAILED would keep
    a dismissed session in the "needs you" list with nothing left to do.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="do a thing")

    async def operator_closes() -> None:
        with _fake_client([result(is_error=True, errors=["overloaded"])], stay_open=True):
            pool = SessionPool(bridge)
            pool.submit(session)
            await _settle()
            task = pool._running[session.node_id]
            await pool.close(session.node_id)
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(operator_closes())

    store = Store()
    store.apply_all(bridge.drain())
    assert _record(store).state is AgentState.DONE


def test_quitting_the_application_reads_as_closed() -> None:
    """
    Shutdown cancels every session without going through `close`, so the flag has to
    be set there too. Quitting closes every session -- the contract `AgentState.DONE`
    states -- and it is the one cancellation where who asked is known exactly, so
    reporting it as a failure would be a false failure signal.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="do a thing")

    async def quit_the_app() -> None:
        with _fake_client([result(is_error=True, errors=["overloaded"])], stay_open=True):
            pool = SessionPool(bridge)
            pool.submit(session)
            await _settle()
            await pool.shutdown()

    asyncio.run(quit_the_app())

    store = Store()
    store.apply_all(bridge.drain())
    assert _record(store).state is AgentState.DONE


def test_a_cancel_nobody_asked_for_does_not_read_as_closed() -> None:
    """
    Cancellation is not by itself an operator close. `Bridge.stop`'s loop thread
    cancels every remaining task at shutdown, and the SDK's anyio task groups make
    a transport teardown surfacing here plausible as well -- reporting either as
    DONE is the silent loss of a failure signal this whole change exists to fix.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="do a thing")

    async def something_else_cancels() -> None:
        with _fake_client([result(is_error=True, errors=["overloaded"])], stay_open=True):
            task = asyncio.create_task(session.run())
            await _settle()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(something_else_cancels())

    store = Store()
    store.apply_all(bridge.drain())
    rec = _record(store)
    assert rec.state is AgentState.FAILED
    assert rec.error is not None and "overloaded" in rec.error


def test_a_cancelled_healthy_session_does_not_read_as_closed_either() -> None:
    """
    A live session torn down without being asked to has ended, and the operator has
    to be able to tell that from one they closed. The record says which.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="do a thing")

    async def something_else_cancels() -> None:
        with _fake_client([result(terminal_reason="completed")], stay_open=True):
            task = asyncio.create_task(session.run())
            await _settle()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(something_else_cancels())

    store = Store()
    store.apply_all(bridge.drain())
    assert _record(store).state is AgentState.FAILED


# -- a sub-agent's deliverable (2026-08-13-detail-swaps-to-a-deliverable, step 3) --


_ANSWER = (
    "## What I found\n"
    "\n"
    "The checksum is computed and never compared, in sixty places.\n"
    "\n"
    "- `tle/parse.py:41` reads it and drops it\n"
    "- every caller downstream assumes it was checked\n"
)


def _stop_hook_input(agent_id: str, last_message: str) -> dict[str, object]:
    return {
        "hook_event_name": "SubagentStop",
        "agent_id": agent_id,
        "session_id": "sess-1",
        "cwd": "/tmp",
        "transcript_path": "/tmp/t.jsonl",
        "last_assistant_message": last_message,
    }


async def _fire_stop(session, agent_id: str, last_message: str) -> None:
    await session._subagent_stop(  # type: ignore[arg-type]
        _stop_hook_input(agent_id, last_message), None, None
    )


def test_a_subagents_whole_answer_reaches_the_store() -> None:
    """
    The row's topic is a column's worth; the answer is what the sub-agent was
    spawned to produce. The stream carries those words too -- a sub-agent's
    AssistantMessages arrive with parent_tool_use_id and are routed into its
    transcript -- but nothing there delimits the answer, and no ResultMessage can
    be attributed to a sub-agent's node at all. So this string is the only whole
    answer the store can hold, and clipping it here destroys the only copy.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    asyncio.run(_fire_stop(session, "agent-a", _ANSWER))

    delivered = next(i for i in bridge.drain() if isinstance(i, SubagentDelivered))
    assert delivered.node_id == (session.session_id, "agent-a")
    assert delivered.text == _ANSWER


def test_the_row_still_gets_its_one_line_topic() -> None:
    """Two registers from one string. The deliverable must not cost the rail its
    summary, or the card goes blank at the moment the sub-agent finishes."""
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    asyncio.run(_fire_stop(session, "agent-a", _ANSWER))

    progress = next(i for i in bridge.drain() if isinstance(i, SubagentProgress))
    assert progress.description == "## What I found"


def test_a_long_first_line_is_clipped_in_the_topic_but_not_the_deliverable() -> None:
    answer = "x" * 400 + "\nand the rest"
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    asyncio.run(_fire_stop(session, "agent-a", answer))

    intents = bridge.drain()
    progress = next(i for i in intents if isinstance(i, SubagentProgress))
    delivered = next(i for i in intents if isinstance(i, SubagentDelivered))
    assert len(progress.description) == 80
    assert delivered.text == answer


def test_a_subagent_that_said_nothing_delivers_nothing() -> None:
    """An empty deliverable is not an empty answer -- it is no answer. Emitting one
    would put an empty 'what it delivered' section over a sub-agent whose words, if
    it had any, are in its transcript."""
    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    asyncio.run(_fire_stop(session, "agent-a", ""))

    assert not [i for i in bridge.drain() if isinstance(i, SubagentDelivered)]


# -- the brief travels with the launch (row 5, step 1) -----------------------------


def _recording_launch(spec: LaunchSpec) -> list[AgentSession]:
    """Run `_launch` against a pool that only records, and hand back what it built."""
    from pptmstr.app import AppState, _launch
    from pptmstr.settings import Settings

    started: list[AgentSession] = []

    class _RecordingPool:
        def submit(self, session: AgentSession) -> None:
            started.append(session)

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(state, spec)
        for _ in range(200):
            if started:
                break
            time.sleep(0.005)
    finally:
        bridge.stop()
    return started


def test_a_brief_reaches_the_session_it_was_launched_with() -> None:
    """
    The wiring, not the value (STYLE.md §2). `LaunchSpec` can carry it and `_launch`
    can still drop it on the floor, which leaves the premises exactly as unreachable
    as they were.
    """
    started = _recording_launch(
        LaunchSpec(task="t", model="claude-sonnet-5", cwd="/tmp", brief="/briefs/s1")
    )
    assert [s.brief for s in started] == ["/briefs/s1"]


def test_a_session_launched_without_a_brief_has_none() -> None:
    started = _recording_launch(LaunchSpec(task="t", model="claude-sonnet-5", cwd="/tmp"))
    assert [s.brief for s in started] == [None]


def test_a_relaunch_keeps_the_brief_the_session_was_launched_with() -> None:
    """
    The reason the path is stored rather than derived from cwd and the session id.
    A relaunch is a *new* session id, so a derived path would point at an empty
    directory and lose the premises at the moment the record exists to keep them.

    This is row 9's defect in a new field: `relaunch` and `fork` build a launch from
    an `AgentRecord`, and the field that is not carried is gone with nothing on
    screen saying so.
    """
    from pptmstr.model import AgentRecord

    record = AgentRecord(
        node_id=("s1", None),
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task="audit the parser",
        model="claude-sonnet-5",
        cwd="/srv/repo",
        template="feature",
        brief="/briefs/s1",
    )

    spec = LaunchSpec.from_record(record)

    assert (spec.brief, spec.template) == ("/briefs/s1", "feature")
    assert (spec.task, spec.model, spec.cwd) == ("audit the parser", "claude-sonnet-5", "/srv/repo")


def test_a_record_with_no_cwd_relaunches_in_this_directory() -> None:
    """A blank cwd is the FLEET rail's grouping key, so None must not become ""."""
    from pptmstr.model import AgentRecord

    record = AgentRecord(
        node_id=("s1", None),
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task="t",
        model="m",
    )
    assert LaunchSpec.from_record(record).cwd == "."


def test_the_announce_puts_the_brief_on_the_record() -> None:
    """
    End of the chain: the store is the only place the UI can read it from, so a
    brief that reaches `AgentSession` and stops there is still unaddressable.
    """
    from pptmstr.intents import AgentSpawned

    store = Store()
    store.apply(
        AgentSpawned(
            node_id=("s1", None),
            parent=None,
            task="t",
            model="m",
            started_at=0.0,
            template="feature",
            brief="/briefs/s1",
        )
    )
    assert store.snapshot().nodes[("s1", None)].brief == "/briefs/s1"


def test_the_announce_carries_the_brief_the_session_holds() -> None:
    """
    The link a hand-built `AgentSpawned` cannot test. `AgentSession` can hold the
    brief and `announce` can still omit it from the intent, and the store would then
    be building a correct record out of an incomplete announce -- "plumbed through"
    and "works end to end" are different claims (STYLE.md §2).
    """
    from pptmstr.intents import AgentSpawned

    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(bridge, "t", model="m", cwd="/tmp", brief="/briefs/s1")
        session.announce()
        for _ in range(200):
            spawns = [i for i in bridge.drain() if isinstance(i, AgentSpawned)]
            if spawns:
                break
            time.sleep(0.005)
    finally:
        bridge.stop()

    assert [s.brief for s in spawns] == ["/briefs/s1"]


def test_a_worker_is_told_where_the_sessions_premises_are() -> None:
    """
    The wiring, not the prose (STYLE.md §2). `worker_prompt` can render the path and
    `AgentSession` can hold it while `_team()` never joins the two -- which leaves
    every worker launched with a brief it is never told about, and the run looks
    exactly like one with no brief at all.
    """
    from pptmstr.templates import FEATURE

    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(
            bridge, "t", model="m", cwd="/tmp", brief="/briefs/s1", template=FEATURE
        )
        team = session._team()
    finally:
        bridge.stop()

    assert team is not None
    for name, definition in team.items():
        assert "/briefs/s1" in definition.prompt, name


def test_a_worker_launched_without_a_brief_is_told_about_none() -> None:
    from pptmstr.templates import FEATURE

    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(bridge, "t", model="m", cwd="/tmp", template=FEATURE)
        team = session._team()
    finally:
        bridge.stop()

    assert team is not None
    for name, definition in team.items():
        assert "premises this session" not in definition.prompt, name


# -- the premises reach the workers, end to end -----------------------------------
#
# Every piece of row 5 passed on its own and the feature had no population path: the
# operator had to hand-type a directory for a session id that did not exist yet.
# These drive launcher -> _launch -> AgentSession -> worker prompt, which is the
# claim the per-piece tests could not make.


def _launched(spec: LaunchSpec, monkeypatch, root) -> AgentSession:
    """Run `_launch` against a recording pool, with briefs rooted in a temp dir."""
    from pptmstr import brief as brief_mod
    from pptmstr.app import AppState, _launch
    from pptmstr.settings import Settings

    monkeypatch.setattr(brief_mod, "default_root", lambda: root)

    started: list[AgentSession] = []

    class _RecordingPool:
        def submit(self, session: AgentSession) -> None:
            started.append(session)

    bridge = Bridge()
    bridge.start()
    try:
        state = AppState(store=Store(), bridge=bridge, settings=Settings())
        state.pool = _RecordingPool()  # type: ignore[assignment]
        _launch(state, spec)
        for _ in range(200):
            if started:
                break
            time.sleep(0.005)
    finally:
        bridge.stop()
    assert started, "the pool was never handed a session"
    return started[0]


def test_launching_a_team_writes_the_task_as_its_first_premise(monkeypatch, tmp_path) -> None:
    """
    The premises the operator means are in the launch text box -- that is the
    record's own diagnosis. This is what makes them addressable, and it costs the
    operator no new habit.
    """
    from pptmstr import brief as brief_mod

    session = _launched(
        LaunchSpec(
            task="the TLE parser is fixed-width; every field is positional",
            model="m",
            cwd="/tmp",
            template="feature",
        ),
        monkeypatch,
        tmp_path,
    )

    assert session.brief is not None
    (entry,) = brief_mod.read_entries(Path(session.brief))
    assert entry.body == "the TLE parser is fixed-width; every field is positional"
    assert entry.path.name == "000-premises.md"


def test_every_worker_on_that_team_is_told_where_to_read_it(monkeypatch, tmp_path) -> None:
    """
    The end of the chain, and the claim no per-piece test could make: a brief that
    is written and never reaches a worker prompt is a brief nobody reads.
    """
    session = _launched(
        LaunchSpec(task="premises", model="m", cwd="/tmp", template="feature"),
        monkeypatch,
        tmp_path,
    )

    team = session._team()
    assert team is not None
    for name, definition in team.items():
        assert session.brief in definition.prompt, name


def test_a_solo_session_seeds_nothing(monkeypatch, tmp_path) -> None:
    """No workers to seed, and a directory per solo launch is litter."""
    session = _launched(LaunchSpec(task="do a thing", model="m", cwd="/tmp"), monkeypatch, tmp_path)

    assert session.brief is None
    assert list(tmp_path.iterdir()) == []


def test_a_session_pointed_at_an_existing_brief_is_not_seeded_over(monkeypatch, tmp_path) -> None:
    """
    A fork continuing its parent's work. Seeding here would bury the premises it was
    launched to continue under a copy of its own task line.
    """
    from pptmstr import brief as brief_mod

    parent = tmp_path / "parent"
    brief_mod.write_entry(parent, "the original premises")

    session = _launched(
        LaunchSpec(
            task="continue the work", model="m", cwd="/tmp", template="feature", brief=str(parent)
        ),
        monkeypatch,
        tmp_path,
    )

    assert session.brief == str(parent)
    (entry,) = brief_mod.read_entries(parent)
    assert entry.body == "the original premises"


def test_two_team_sessions_do_not_share_a_brief(monkeypatch, tmp_path) -> None:
    spec = LaunchSpec(task="premises", model="m", cwd="/tmp", template="feature")
    first = _launched(spec, monkeypatch, tmp_path)
    second = _launched(spec, monkeypatch, tmp_path)

    assert first.brief != second.brief


def test_a_brief_that_cannot_be_written_does_not_stop_the_launch(monkeypatch, tmp_path) -> None:
    """
    The work the operator asked for is worth more than the seeding of it. The log
    says which happened rather than leaving a team quietly unbriefed.
    """
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf-8")

    session = _launched(
        LaunchSpec(task="premises", model="m", cwd="/tmp", template="feature"),
        monkeypatch,
        blocked,
    )

    assert session.brief is None
    assert session.task == "premises"


# -- a lead that is only waiting on its workers ----------------------------------
#
# Measured, not supposed: `scripts/verify_lead_turn_via_agent_session.py` ran the
# full stack and reported ANSWERED-IN-WAIT-LOOP -- a prompt sent while sub-agents
# were live came back as its own turn, ahead of the fan-out. Every surface said
# otherwise, and the reason is here: `_result` emits no state intent, so the lead
# keeps whatever its last assistant message set. That is THINKING, which is also
# what a lead mid-turn wears, so one state stood for two situations the operator
# has to act on differently.


def _states_of(session: AgentSession, intents: list[object]) -> list[AgentState]:
    return [
        i.state for i in intents if isinstance(i, StateChanged) and i.node_id == session.node_id
    ]


def test_a_lead_waiting_on_its_workers_is_not_left_reading_as_thinking(monkeypatch) -> None:
    """
    Entering `_await_subagents` is the one moment the lead's own state is knowable,
    and it is emitted there rather than inferred later.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="fan out")
    # A worker admitted before the lead's turn ended, which is what puts run() into
    # the wait at all.
    session._live_subagents.add("worker-1")
    seen: list[object] = []

    def frame() -> None:
        seen.extend(bridge.drain())

    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient([result()], frame),
    )
    asyncio.run(session.run())
    frame()

    states = _states_of(session, seen)
    assert AgentState.SUPERVISING in states, "the lead never said it was supervising"
    # Before the turn-over state, not instead of it: once the workers are done the
    # lead is genuinely waiting on the operator and must still say so.
    assert states.index(AgentState.SUPERVISING) < states.index(AgentState.AWAITING_INPUT)


def test_the_supervising_topic_says_the_lead_can_be_reached(monkeypatch) -> None:
    """
    The topic column is the row's one line of prose and is on screen every frame,
    so it carries the part the operator can act on. A state with no topic would say
    "supervising" and leave the capability as something to be found out.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="fan out")
    session._live_subagents.add("worker-1")
    seen: list[object] = []

    def frame() -> None:
        seen.extend(bridge.drain())

    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient([result()], frame),
    )
    asyncio.run(session.run())
    frame()

    supervising = [
        i for i in seen if isinstance(i, StateChanged) and i.state is AgentState.SUPERVISING
    ]
    assert supervising
    assert supervising[0].topic == SUPERVISING_TOPIC
    assert "send" in SUPERVISING_TOPIC


def test_a_lead_with_no_workers_never_claims_to_be_supervising(monkeypatch) -> None:
    """
    The state is about the wait, not about being a lead. A solo session whose turn
    ends goes straight to AWAITING_INPUT, and a spurious SUPERVISING there would
    tell the operator to expect an answer that has nothing to arrive from.
    """
    bridge = Bridge()
    session = AgentSession(bridge, task="alone")
    seen: list[object] = []

    def frame() -> None:
        seen.extend(bridge.drain())

    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient([result()], frame),
    )
    asyncio.run(session.run())
    frame()

    states = _states_of(session, seen)
    assert AgentState.SUPERVISING not in states
    assert AgentState.AWAITING_INPUT in states


# -- containment, and a sandbox that fails to start --------------------------------


def test_an_uncontained_launch_is_handed_no_settings_at_all() -> None:
    """
    None has to reach the transport as an absent flag, not as a value. It does:
    ``_build_settings_value`` branches on ``settings is not None`` and returns before
    it can serialise anything, so a launch that asks for no containment builds the
    argv it built before the field existed.
    """
    options = AgentSession(Bridge(), task="t")._options()

    assert options.settings is None
    assert options.sandbox is None


def test_the_containment_configuration_reaches_the_settings_field_verbatim() -> None:
    from pptmstr.sandbox import containment_settings

    raw = containment_settings()
    options = AgentSession(Bridge(), task="t", containment=raw)._options()

    # Verbatim, not re-serialised: the string is the only form three of §8's keys have,
    # since the SDK's types cannot express them and a round trip through them would
    # drop exactly those.
    assert options.settings == raw


def test_the_typed_sandbox_field_is_left_unset_so_the_containment_survives() -> None:
    """
    The load-bearing assertion is not ``sandbox is None`` -- that is its default and a
    test of it passes whatever this session does. It is that the settings value the
    transport actually builds from these options still carries §8's four keys.

    ``_build_settings_value`` assigns rather than merges, and
    ``containment_settings()`` emits exactly one top-level key, so a session that set
    the typed field as well would arrive here having silently deleted all four.
    """
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from pptmstr.sandbox import containment_settings

    options = AgentSession(Bridge(), task="t", containment=containment_settings())._options()
    assert options.sandbox is None

    built = SubprocessCLITransport("", options)._build_settings_value()
    assert built is not None
    sandbox = json.loads(built)["sandbox"]
    assert sandbox["failIfUnavailable"] is True
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["autoAllowBashIfSandboxed"] is False
    assert sandbox["network"]["strictAllowlist"] is True


def test_the_clis_error_stream_is_piped_into_the_transcript() -> None:
    """
    Driven through the callback the options actually carry, not through the method by
    name: the transport pipes stderr only when ``options.stderr`` is set, so a handler
    nothing is wired to would leave the warning on the terminal exactly as before.
    """
    session = AgentSession(Bridge(), task="t")
    callback = session._options().stderr
    assert callback is not None

    callback("bwrap: command not found; continuing without sandbox")

    assert "bwrap: command not found" in session.transcript.text()
    kinds = {segment.kind for segment in session.transcript.segments()}
    assert kinds == {SegmentKind.ERROR}


def test_the_error_stream_is_piped_even_when_nothing_is_contained() -> None:
    """
    Deliberately unconditional. A diagnostic the operator cannot see is a property of
    this application rather than of the containment configuration, and the launch most
    likely to produce one is the launch that asked for containment and did not get it
    -- which from here is indistinguishable from a launch that asked for none.
    """
    assert AgentSession(Bridge(), task="t")._options().stderr is not None


def test_consecutive_diagnostics_stay_on_their_own_lines() -> None:
    """
    The transport hands over rstripped lines and drops the empty ones, so nothing
    upstream restores the separator. ERROR segments coalesce, so a missing newline
    would run a whole run of diagnostics into one unreadable line.
    """
    session = AgentSession(Bridge(), task="t")
    callback = session._options().stderr
    assert callback is not None

    callback("first")
    callback("second")

    assert session.transcript.text() == "first\nsecond\n"


def test_the_transport_delivers_whole_lines_to_the_callback() -> None:
    """
    ``_stderr_line``'s docstring asserts three facts about the SDK: the stream is
    framed into lines, each is rstripped, and empty ones are dropped. It restores the
    newline on that basis, so the assertion is load-bearing rather than descriptive.

    Driven through the transport's own ``_handle_stderr`` against a stand-in stream,
    because no part of those three is visible from the callback's own arguments -- a
    test that called the callback directly would pass just as well against a transport
    that handed over raw 64KiB chunks.
    """
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    session = AgentSession(Bridge(), task="t")
    transport = SubprocessCLITransport("", session._options())

    class _Chunks:
        """Arbitrary chunk boundaries, which is what anyio's stream actually yields."""

        async def __aiter__(self):
            for chunk in ("bwrap: command ", "not found\n\nrunning unsandboxed\n"):
                yield chunk

    transport._stderr_stream = _Chunks()  # type: ignore[assignment]
    asyncio.run(transport._handle_stderr())

    assert session.transcript.text() == "bwrap: command not found\nrunning unsandboxed\n"


# -- the gate's policy (2026-09-03 §5, §7, §8) -------------------------------------
#
# tests/test_approval.py already establishes what each policy classifies. These are
# about the wire: that a policy set on a session is the one the gate measures with,
# for the root agent and for its sub-agents, and that the sub-agent cap stays ahead
# of it.


def _spy_on_classify(monkeypatch) -> list[tuple[str, Policy]]:
    """
    Record (tool_name, policy) for every classification the gate makes, answering
    exactly as the real classifier does.

    Delegating rather than returning a canned Disposition is what lets these tests
    assert the decision as well as the argument: a gate that passed the policy and
    then ignored the answer would satisfy the argument assertion on its own.
    """
    from pptmstr import driver as driver_mod

    seen: list[tuple[str, Policy]] = []
    real = driver_mod.classify

    def spy(tool_name, tool_input, policy=Policy.STRICT):
        seen.append((tool_name, policy))
        return real(tool_name, tool_input, policy)

    monkeypatch.setattr(driver_mod, "classify", spy)
    return seen


def _headless_bash(session, agent_id: str | None = None) -> dict:
    """
    One Bash call through the real gate path, with no operator attached.

    Headless is what makes the decision readable without a bridge and an approver:
    a Bash call the policy does not release is denied for want of an operator rather
    than parked, so allow-versus-deny is exactly the policy's answer.
    """
    return _decision(
        asyncio.run(
            session._pre_tool_use(_gate_input("Bash", {"command": "ls"}, agent_id), None, None)
        )
    )


def test_a_session_that_names_no_policy_is_measured_against_strict(monkeypatch) -> None:
    """
    The OFF path, and §5 calls it the whole of the OFF-path evidence: a session built
    the way every caller builds one today reaches the classifier with STRICT.
    """
    seen = _spy_on_classify(monkeypatch)
    session = AgentSession(Bridge(), task="lead", interactive=False)

    assert _headless_bash(session)["permissionDecision"] == "deny"
    assert seen == [("Bash", Policy.STRICT)]


def test_the_policy_a_session_was_built_with_reaches_the_classifier(monkeypatch) -> None:
    """
    The thing nothing pinned before: every classify call site passed two positional
    arguments, so a policy could be correct and still be measured against nothing.
    """
    seen = _spy_on_classify(monkeypatch)
    session = AgentSession(Bridge(), task="lead", policy=Policy.AUTONOMOUS, interactive=False)

    assert _headless_bash(session)["permissionDecision"] == "allow"
    assert seen == [("Bash", Policy.AUTONOMOUS)]


def test_a_sub_agent_is_under_its_sessions_policy_deliberately(monkeypatch) -> None:
    """
    §8 inverts 2026-08-11 §4's "sub-agents do not inherit it" for this dial, on the
    reasoning that containment is per CLI process and sub-agents share the parent's.

    Named here because one AgentSession serves every sub-agent's PreToolUse, so the
    behaviour would be the same if it were an accident of where the field is stored.
    `_policy_for` takes the agent_id and declines to branch on it; this is the test
    that says so out loud.
    """
    seen = _spy_on_classify(monkeypatch)
    session = AgentSession(Bridge(), task="lead", policy=Policy.AUTONOMOUS, interactive=False)

    assert _headless_bash(session, agent_id="agent-qa")["permissionDecision"] == "allow"
    assert seen == [("Bash", Policy.AUTONOMOUS)]


def test_a_sub_agent_of_a_strict_session_is_strict_too(monkeypatch) -> None:
    """
    Inheritance is the session's policy whichever one it is, not a release valve that
    only ever widens. Without this the test above passes against a gate that hands
    sub-agents AUTONOMOUS unconditionally.
    """
    seen = _spy_on_classify(monkeypatch)
    session = AgentSession(Bridge(), task="lead", interactive=False)

    assert _headless_bash(session, agent_id="agent-qa")["permissionDecision"] == "deny"
    assert seen == [("Bash", Policy.STRICT)]


def test_the_cap_refuses_a_spawn_before_any_policy_is_consulted(monkeypatch) -> None:
    """
    §7's ordering invariant, pinned against the policy rather than against parking:
    the cap is the one bound a policy cannot widen, and it is only that because the
    deny sits ahead of classify rather than inside it.

    The existing cap tests would all pass with the two swapped -- the call is denied
    either way -- so the assertion that carries this one is that the classifier was
    never asked.
    """
    seen = _spy_on_classify(monkeypatch)
    session = AgentSession(
        Bridge(), task="lead", policy=Policy.AUTONOMOUS, subagent_cap=1, interactive=False
    )
    asyncio.run(_fire_start(session, "a-1"))

    spec = _decision(
        asyncio.run(
            session._pre_tool_use(_gate_input("Agent", {"subagent_type": "builder"}), None, None)
        )
    )

    assert spec["permissionDecision"] == "deny"
    assert "cap is 1" in spec["permissionDecisionReason"]
    assert seen == []


def _drain_spawns(bridge: Bridge) -> list:
    from pptmstr.intents import AgentSpawned

    for _ in range(200):
        spawns = [i for i in bridge.drain() if isinstance(i, AgentSpawned)]
        if spawns:
            return spawns
        time.sleep(0.005)
    return []


def test_the_announce_carries_the_policy_the_gate_will_apply() -> None:
    """
    The record and the gate read the same attribute, asserted together on one session
    so they cannot be right separately and disagree. An operator supervising several
    sessions has only the record to tell which are under-gated; a record saying STRICT
    beside a gate auto-approving Bash is the failure §6.3 exists to prevent, and it is
    invisible from either half alone.

    `AgentSession` can hold the policy and `announce` can still omit it -- "plumbed
    through" and "works end to end" are different claims (STYLE.md §2).
    """
    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(bridge, "t", policy=Policy.AUTONOMOUS, interactive=False)
        session.announce()
        spawns = _drain_spawns(bridge)
    finally:
        bridge.stop()

    store = Store()
    for intent in spawns:
        store.apply(intent)

    assert store.snapshot().nodes[session.node_id].policy is Policy.AUTONOMOUS
    assert _headless_bash(session)["permissionDecision"] == "allow"


def test_a_session_that_names_no_policy_announces_strict() -> None:
    """
    The default path, which the AUTONOMOUS case alone cannot distinguish from an
    announce that hard-codes the wider value.
    """
    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(bridge, "t", interactive=False)
        session.announce()
        spawns = _drain_spawns(bridge)
    finally:
        bridge.stop()

    assert [s.policy for s in spawns] == [Policy.STRICT]


# -- the autonomous policy: nothing parks, and writes stay in the region -------


def _autonomous(tmp_path: Path, **kwargs) -> AgentSession:
    """
    A session under the autonomous policy with an operator attached.

    ``interactive=True`` is the whole point of the helper and not a default taken
    by accident: headless already denies everything that needs approval, so a
    no-park claim measured on a headless session would pass against a gate that
    had no autonomous branch at all.
    """
    kwargs.setdefault("cwd", str(tmp_path))
    return AgentSession(Bridge(), task="lead", policy=Policy.AUTONOMOUS, interactive=True, **kwargs)


def _gate(session: AgentSession, tool_name: str, tool_input: dict, agent_id=None) -> dict:
    return _decision(
        asyncio.run(session._pre_tool_use(_gate_input(tool_name, tool_input, agent_id), None, None))
    )


def _forbid_parking(monkeypatch) -> None:
    async def explode(*args, **kwargs):
        raise AssertionError(f"the gate parked: {args!r}")

    monkeypatch.setattr(AgentSession, "_park", explode)


def test_nothing_parks_under_the_autonomous_policy(tmp_path: Path, monkeypatch) -> None:
    """
    The invariant rather than an example. Every name this build classifies, plus
    names it does not: a future SDK tool, an MCP tool from a server it has never
    seen, a bus name this build does not have, and the empty string.

    A park here is not a degraded mode. `APPROVAL_TIMEOUT_S` is six hours and the
    CLI is blocked on the hook's answer for all of it, so one reachable path makes
    the session hang rather than run unattended -- which is why this enumerates the
    sets instead of sampling them, and reads them off `approval` so a tool added
    there is swept in without anyone remembering to add it here.
    """
    from pptmstr import approval

    _forbid_parking(monkeypatch)
    session = _autonomous(tmp_path)
    names = [
        *sorted(approval._AUTO | approval._REVIEW | approval._BUS_AUTO),
        "SomeFutureTool",
        "mcp__other_server__do_thing",
        "mcp__pptmstr__invent_a_task",
        "",
    ]

    for name in names:
        # An inside-the-region path so the write tools are answered by the policy
        # rather than by the confinement; either answer is a decision, and this way
        # the allow half of the sweep is exercised too.
        args = {"file_path": str(tmp_path / "f.py"), "notebook_path": str(tmp_path / "n.ipynb")}
        assert _gate(session, name, args)["permissionDecision"] in ("allow", "deny"), name


def test_an_unrecognised_tool_is_denied_for_the_mode_not_for_a_missing_operator(
    tmp_path: Path, monkeypatch
) -> None:
    """
    The two ways to have no reviewer say different things on purpose. "No operator
    is attached" tells an agent the deployment was wrong and the same call would
    have run elsewhere; under this mode the operator is attached and has said in
    advance that they will not be asked, so the agent should stop looking for a
    human and pick another tool.
    """
    _forbid_parking(monkeypatch)
    spec = _gate(_autonomous(tmp_path), "SomeFutureTool", {})

    assert spec["permissionDecision"] == "deny"
    reason = spec["permissionDecisionReason"]
    assert "unattended by choice" in reason
    assert "retrying will not change that" in reason
    assert "no operator is attached" not in reason


def test_a_strict_session_with_an_operator_still_parks(tmp_path: Path, monkeypatch) -> None:
    """
    The ordering the new branch could have broken by sitting one line too early:
    STRICT with an operator attached is still a park, and the autonomous deny is
    reached only under the autonomous policy.
    """
    parked: list[str] = []

    async def record(self, tool_name, *args, **kwargs):
        parked.append(tool_name)
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}

    monkeypatch.setattr(AgentSession, "_park", record)
    session = AgentSession(Bridge(), task="lead", cwd=str(tmp_path), interactive=True)

    _gate(session, "Write", {"file_path": str(tmp_path / "a.py"), "content": "x"})

    assert parked == ["Write"]


def test_an_auto_approved_write_inside_the_region_runs(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    spec = _gate(
        _autonomous(tmp_path), "Write", {"file_path": str(tmp_path / "pkg" / "a.py"), "content": ""}
    )

    assert spec["permissionDecision"] == "allow"


def test_a_relative_write_is_measured_from_the_sessions_directory(tmp_path: Path) -> None:
    """
    A relative ``file_path`` means the agent's directory, not pptmstr's. Resolving
    it against this process's cwd would measure the write against the wrong tree
    and answer no for an ordinary in-region write -- or yes for an escape, if the
    two directories happened to nest the other way.
    """
    session = _autonomous(tmp_path)

    assert _gate(session, "Write", {"file_path": "a.py"})["permissionDecision"] == "allow"
    assert _gate(session, "Write", {"file_path": "../a.py"})["permissionDecision"] == "deny"


def test_a_write_outside_the_region_is_denied_and_told_where_the_region_is(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / "elsewhere.py"
    spec = _gate(_autonomous(tmp_path / "work"), "Write", {"file_path": str(outside)})

    assert spec["permissionDecision"] == "deny"
    # The path it may not write and the region it may are both named: a refusal
    # that says only "denied" is one the agent answers by trying a variation.
    assert str(outside) in spec["permissionDecisionReason"]
    assert str(tmp_path / "work") in spec["permissionDecisionReason"]


def test_a_symlink_out_of_the_region_is_followed(tmp_path: Path) -> None:
    """
    The escape that a string comparison misses. The link is inside the region by
    every spelling test; what it names is not, and the write lands where it points.
    """
    region = tmp_path / "work"
    region.mkdir()
    (tmp_path / "outside").mkdir()
    (region / "door").symlink_to(tmp_path / "outside")

    spec = _gate(_autonomous(region), "Write", {"file_path": str(region / "door" / "a.py")})

    assert spec["permissionDecision"] == "deny"


def test_a_sibling_named_after_the_region_is_not_inside_it(tmp_path: Path) -> None:
    """
    ``/src/proj-scratch`` has ``/src/proj`` as a string prefix and is a different
    tree. ``lies_inside_checkout`` compares components, and this is the test that
    says the gate uses it rather than a prefix of its own.
    """
    region = tmp_path / "proj"
    region.mkdir()
    (tmp_path / "proj-scratch").mkdir()

    spec = _gate(_autonomous(region), "Write", {"file_path": str(tmp_path / "proj-scratch" / "a")})

    assert spec["permissionDecision"] == "deny"


def test_a_home_relative_write_is_expanded_before_it_is_measured(tmp_path: Path) -> None:
    """
    ``~/x`` is not an absolute path, so joining it to the region unexamined would
    place it in a directory literally named ``~`` inside the region and allow it.
    Whether the write tool expands it is the CLI's business; the gate cannot afford
    to be the one that assumed it does not.
    """
    spec = _gate(_autonomous(tmp_path), "Write", {"file_path": "~/escaped.py"})

    assert spec["permissionDecision"] == "deny"


def test_a_path_the_filesystem_cannot_place_is_refused(tmp_path: Path) -> None:
    """
    A symlink loop leaves the filesystem unable to say where the path is. This is
    the one case where ``lies_inside_checkout`` answers *inside* -- fail-closed for
    the question of whether pptmstr's own source is exposed, fail-open for this one
    -- so the gate resolves first and refuses a path with no location, rather than
    taking an answer pointing the wrong way.
    """
    region = tmp_path / "work"
    region.mkdir()
    (region / "a").symlink_to(region / "b")
    (region / "b").symlink_to(region / "a")

    spec = _gate(_autonomous(region), "Write", {"file_path": str(region / "a")})

    assert spec["permissionDecision"] == "deny"


def test_a_write_tool_whose_target_cannot_be_read_is_refused(tmp_path: Path) -> None:
    """
    Fail closed on the case the confinement exists for: a released write tool whose
    destination this build cannot extract cannot be shown to write inside the
    region, and "cannot be shown" has to mean no.
    """
    spec = _gate(_autonomous(tmp_path), "Write", {"content": "x"})

    assert spec["permissionDecision"] == "deny"
    assert "no readable target path" in spec["permissionDecisionReason"]


def test_notebook_edit_is_measured_on_its_own_argument_name(tmp_path: Path) -> None:
    """
    ``NotebookEdit`` names its target ``notebook_path`` where the other three use
    ``file_path``. A confinement that read one key would let this one through, and
    it is in the released set with the others.
    """
    session = _autonomous(tmp_path)
    inside = {"notebook_path": str(tmp_path / "n.ipynb")}
    outside = {"notebook_path": str(tmp_path.parent / "n.ipynb")}

    assert _gate(session, "NotebookEdit", inside)["permissionDecision"] == "allow"
    assert _gate(session, "NotebookEdit", outside)["permissionDecision"] == "deny"


def test_a_sub_agents_write_is_measured_against_the_sessions_region(tmp_path: Path) -> None:
    """
    One `AgentSession` serves every sub-agent's PreToolUse, so the region is the
    session's for every node it gates. A sub-agent inherits `cwd` in the store too,
    but the gate does not read the record -- inheritance there and confinement here
    are separate mechanisms and this pins the one that refuses the write.
    """
    session = _autonomous(tmp_path)
    outside = {"file_path": str(tmp_path.parent / "a.py")}

    assert _gate(session, "Write", outside, "agent-qa")["permissionDecision"] == "deny"


def test_the_region_is_the_sessions_cwd_and_not_its_reporting_base(tmp_path: Path) -> None:
    """
    `cwd` and `session_base` are the same directory for a session launched today,
    so only a session built with them apart can say which one the gate uses. It
    must be `cwd`: that is where the CLI runs, and so the region the sandbox bounds
    `Bash` to. `session_base` is the units a write is *reported* in, and a
    book-keeping choice may not decide what may be written.
    """
    work = tmp_path / "work"
    work.mkdir()
    reporting = tmp_path / "reported"
    reporting.mkdir()
    session = _autonomous(work, session_base=str(reporting))

    assert _gate(session, "Write", {"file_path": str(work / "a")})["permissionDecision"] == "allow"
    assert (
        _gate(session, "Write", {"file_path": str(reporting / "a")})["permissionDecision"] == "deny"
    )


def test_bash_is_left_to_the_sandbox(tmp_path: Path) -> None:
    """
    The confinement covers the four tools that run inside the CLI process, and
    stops there. `Bash` is bounded by the CLI's own sandbox, whose writable region
    is the same directory -- and classifying the contents of a command was closed
    by `planning/2026-09-03` §3 on the ground that the parser becomes the security
    property. A gate that half-read commands would be that parser.
    """
    spec = _gate(_autonomous(tmp_path), "Bash", {"command": f"touch {tmp_path.parent}/x"})

    assert spec["permissionDecision"] == "allow"


def test_a_strict_session_is_not_measured_against_a_region(tmp_path: Path) -> None:
    """
    The OFF path stays what it was. A `Write` outside the directory under STRICT is
    refused for the reason it has always been refused -- there is nobody to approve
    it -- and not by the new confinement, which never runs because no write tool
    reaches the auto-approve branch under STRICT.
    """
    session = AgentSession(Bridge(), task="lead", cwd=str(tmp_path), interactive=False)

    spec = _gate(session, "Write", {"file_path": str(tmp_path.parent / "a.py")})

    assert spec["permissionDecision"] == "deny"
    assert "no operator is attached" in spec["permissionDecisionReason"]


def _briefing_spy(monkeypatch) -> dict[str, list]:
    """
    Record the policy each prompt builder is called with, answering as the real one
    does. A spy rather than a comparison of the two texts: `lead_briefing` is free
    to make the same text for both policies -- it does exactly that for a template
    with no roles -- and a test comparing outputs would then pass against a session
    that passed the wrong policy, or none.
    """
    from pptmstr import driver as driver_mod

    seen: dict[str, list] = {"lead": [], "worker": []}
    real_lead, real_worker = driver_mod.lead_briefing, driver_mod.worker_prompt

    def lead(template, policy=Policy.STRICT):
        seen["lead"].append(policy)
        return real_lead(template, policy)

    def worker(role, brief=None, policy=Policy.STRICT):
        seen["worker"].append(policy)
        return real_worker(role, brief, policy)

    monkeypatch.setattr(driver_mod, "lead_briefing", lead)
    monkeypatch.setattr(driver_mod, "worker_prompt", worker)
    return seen


def _two_role_team() -> WorkTemplate:
    return WorkTemplate(
        name="t",
        description="d",
        lead_prompt="p",
        roles=(
            Role(name="builder", description="d", prompt="p"),
            Role(name="reviewer", description="d", prompt="p"),
        ),
    )


def test_the_session_prompts_are_built_under_the_policy_the_gate_applies(monkeypatch) -> None:
    """
    The prompt and the gate have to agree about who is watching. Under AUTONOMOUS
    nothing parks and no message reaches a person, and an agent told otherwise
    writes for an audience that does not exist -- or waits for a reply a human was
    supposed to prompt. Both halves of the team are covered because a worker never
    reads the lead's briefing: `worker_prompt` is what a builder is told, and it is
    builders that hit the write-region denial.
    """
    seen = _briefing_spy(monkeypatch)
    session = AgentSession(
        Bridge(), task="lead", template=_two_role_team(), policy=Policy.AUTONOMOUS
    )

    session._system_prompt()
    session._team()

    assert seen["lead"] == [Policy.AUTONOMOUS]
    assert seen["worker"] == [Policy.AUTONOMOUS, Policy.AUTONOMOUS]


def test_a_session_that_names_no_policy_is_briefed_as_strict(monkeypatch) -> None:
    """
    The default path, which the AUTONOMOUS case alone cannot distinguish from a
    wiring that hands both prompt builders whatever value is to hand.
    """
    seen = _briefing_spy(monkeypatch)
    session = AgentSession(Bridge(), task="lead", template=_two_role_team())

    session._system_prompt()
    session._team()

    assert seen["lead"] == [Policy.STRICT]
    assert seen["worker"] == [Policy.STRICT, Policy.STRICT]


def test_a_path_the_os_will_not_accept_is_refused_rather_than_raised(tmp_path: Path) -> None:
    """
    An embedded NUL raises `ValueError` from `Path.resolve`, and every path here
    was written by a model. An exception out of the gate leaves `PreToolUse`
    raising into the CLI's hook machinery, and what that does with a hook that
    raised is unmeasured -- so the string that cannot be a path is refused here,
    where the answer is this gate's to choose.
    """
    spec = _gate(_autonomous(tmp_path), "Write", {"file_path": str(tmp_path / "a\0b")})

    assert spec["permissionDecision"] == "deny"


# -- the policy and the sandbox start together or not at all ------------------


def test_the_pair_is_checked_for_absence_at_every_combination() -> None:
    """
    Three combinations and only one of them refuses. The two that pass are what
    makes this a check on the pair rather than a check on the policy: an
    autonomous session with containment starts, and so does an ordinary one
    without it, which is every session this application has ever run.
    """
    contained = "{}"
    assert AgentSession(Bridge(), "t", policy=Policy.AUTONOMOUS)._uncontained_autonomy()
    assert (
        AgentSession(
            Bridge(), "t", policy=Policy.AUTONOMOUS, containment=contained
        )._uncontained_autonomy()
        is None
    )
    assert AgentSession(Bridge(), "t")._uncontained_autonomy() is None
    assert AgentSession(Bridge(), "t", containment=contained)._uncontained_autonomy() is None


def test_an_uncontained_autonomous_session_refuses_to_start(monkeypatch) -> None:
    """
    The wiring, and the half that matters: the predicate is worth nothing if the
    session starts anyway. `ClaudeSDKClient` is replaced with something that raises
    on construction, so "no CLI was spawned" is asserted rather than inferred from
    the absence of a subprocess in a test that never had one.

    The refusal is reported the way every other fatal here is -- a FAILED record
    carrying the reason, and the reason on the transcript -- because an operator
    who ticked the box is owed the refusal in the place they are looking.
    """

    def explode(**_):
        raise AssertionError("an uncontained autonomous session spawned a CLI")

    monkeypatch.setattr("pptmstr.driver.ClaudeSDKClient", explode)
    bridge = Bridge()
    session = AgentSession(bridge, "t", policy=Policy.AUTONOMOUS)

    asyncio.run(session.run())

    store = Store()
    store.apply_all(bridge.drain())
    record = store.snapshot().nodes[session.node_id]
    assert record.state is AgentState.FAILED
    assert "no containment" in (record.error or "")
    assert "no containment" in session.transcript.text()


def test_a_contained_autonomous_session_is_not_refused(monkeypatch) -> None:
    """
    The other direction, which the refusal alone cannot distinguish from a guard
    that refuses the policy outright. This one reaches the client.
    """
    reached: list[bool] = []

    def explode(**_):
        reached.append(True)
        raise RuntimeError("stop here")

    monkeypatch.setattr("pptmstr.driver.ClaudeSDKClient", explode)
    session = AgentSession(Bridge(), "t", policy=Policy.AUTONOMOUS, containment="{}")

    asyncio.run(session.run())

    assert reached == [True]


def test_a_padded_path_cannot_smuggle_an_absolute_target_into_the_region(
    tmp_path: Path,
) -> None:
    """
    Escape A. `Path(" /etc/passwd")` is *relative* -- its first component is a
    space -- so an absolute-looking target with a leading space joined the region
    and resolved inside it.

    Refused rather than stripped, and the difference is the whole point: stripping
    here names one file and the CLI names whichever its own reading produces, and
    nothing has measured that the two agree. A spelling two readers resolve
    differently is a spelling containment cannot be shown for, which is the rule
    this function already applies to everything else it cannot place.
    """
    session = _autonomous(tmp_path)
    padded = f" {tmp_path.parent}/escaped.txt"

    spec = _gate(session, "Write", {"file_path": padded})

    assert spec["permissionDecision"] == "deny"
    assert "whitespace" in spec["permissionDecisionReason"]


@pytest.mark.parametrize("pad", [" ", "\t", "\n", "\xa0", "  "])
def test_no_padding_character_gets_a_different_answer(pad: str, tmp_path: Path) -> None:
    """
    The class rather than the character. The reviewer found the space; the rule is
    that a target whose spelling is not its own stripped form is refused, and these
    are the paddings `str.strip` removes -- including the non-breaking space, which
    a reader would not see at all.
    """
    session = _autonomous(tmp_path)

    inside = _gate(session, "Write", {"file_path": f"{pad}{tmp_path}/a.txt"})
    trailing = _gate(session, "Write", {"file_path": f"{tmp_path}/a.txt{pad}"})

    assert inside["permissionDecision"] == "deny", pad
    assert trailing["permissionDecision"] == "deny", pad


def test_a_decoy_argument_cannot_answer_for_the_key_the_tool_acts_on(tmp_path: Path) -> None:
    """
    Escape B, at the gate. A `NotebookEdit` naming a harmless `file_path` inside
    the region and a `notebook_path` outside it was measured on the harmless one
    and allowed.
    """
    session = _autonomous(tmp_path)
    decoyed = {
        "file_path": str(tmp_path / "harmless.txt"),
        "notebook_path": str(tmp_path.parent / "escaped.ipynb"),
    }

    spec = _gate(session, "NotebookEdit", decoyed)

    assert spec["permissionDecision"] == "deny"
    assert "escaped.ipynb" in spec["permissionDecisionReason"]


def test_the_policy_a_denial_names_comes_through_the_one_function_that_decides_it(
    tmp_path: Path, monkeypatch
) -> None:
    """
    `_policy_for` is the single place a call's policy is decided, and its docstring
    says per-node scoping would be a change there and nowhere else. That was false
    while `_no_reviewer_reason` read the attribute directly: a per-node policy
    would have classified one way and refused the other.

    Scoping the spy to a node rather than asserting the deny text, because the two
    readers agree today and only a disagreement between them would show up in the
    text.
    """
    seen: list[str | None] = []
    real = AgentSession._policy_for

    def spy(self, agent_id):
        seen.append(agent_id)
        return real(self, agent_id)

    monkeypatch.setattr(AgentSession, "_policy_for", spy)
    session = _autonomous(tmp_path)

    spec = _gate(session, "SomeFutureTool", {}, "agent-qa")

    assert spec["permissionDecision"] == "deny"
    # Once for classify's policy and once for the refusal, both naming the node.
    assert seen == ["agent-qa", "agent-qa"]


# -- the CLI loads the bus and no other MCP server --------------------------------


def test_the_session_restricts_the_cli_to_the_servers_it_passes() -> None:
    """
    §8a item 4, which shipped unbuilt. Without this the CLI also loads whatever the
    operator's project `.mcp.json`, user settings and plugins name -- a set this
    process cannot enumerate or even see.

    Not policy-scoped, and that is the decision rather than an oversight:
    `ReadMcpResource` and `ListMcpResources` are in `approval._AUTO` and
    auto-approve at every policy, so an unenumerable server's resources are
    readable with no human asked under STRICT as much as under AUTONOMOUS.
    """
    session = AgentSession(Bridge(), task="lead")

    assert session._options().strict_mcp_config is True


def test_the_bus_is_passed_explicitly_so_the_restriction_keeps_it() -> None:
    """
    The pairing, asserted on one options object so the two cannot be right
    separately and disagree. `strict_mcp_config` keeps only what `mcp_servers`
    carries, so the flag without the bus is a session whose agents cannot reach the
    board -- which would present as a silent team rather than as an error.
    """
    from pptmstr.bus import SERVER_NAME

    options = AgentSession(Bridge(), task="lead")._options()

    assert options.strict_mcp_config is True
    assert list(options.mcp_servers) == [SERVER_NAME]


def test_the_bus_reaches_the_argv_that_the_restriction_narrows_to() -> None:
    """
    The mechanism the comment in `_options` rests on, pinned against the installed
    SDK rather than quoted from its docstring: an in-process server is passed to the
    CLI through `--mcp-config` like any other, as `{"type": "sdk", ...}` with the
    instance stripped, and `--strict-mcp-config` keeps exactly what `--mcp-config`
    carried.

    Reaching into `_internal` is deliberate. This is the one claim in the pair that
    is about the SDK's behaviour rather than ours, and an SDK that stopped routing
    in-process servers through that flag would take the board silent with nothing
    else in this repository disagreeing. `cli_version.resolve_cli_path` reaches the
    same way and for the same reason.
    """
    import json

    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from pptmstr import cli_version
    from pptmstr.bus import SERVER_NAME

    options = AgentSession(Bridge(), task="lead")._options()
    transport = SubprocessCLITransport(prompt="x", options=options)
    transport._cli_path = cli_version.resolve_cli_path()

    argv = transport._build_command()

    assert "--strict-mcp-config" in argv
    carried = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
    assert list(carried) == [SERVER_NAME]
    # The live handler cannot cross a process boundary, so the SDK strips it and the
    # CLI reaches back over the control channel. A build that passed it would be
    # serialising a Python object into argv.
    assert "instance" not in carried[SERVER_NAME]
