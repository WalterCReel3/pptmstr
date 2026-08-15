"""
Driver translation: SDK messages in, intents and transcript writes out.

Exercised with real SDK message objects but no subprocess. The translation is where
the store's honesty under streaming is decided, and it is worth being able to test
that without spending tokens or waiting on a CLI.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import patch

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
    AgentState,
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
    import time

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
        _launch(state, "do a thing", "claude-sonnet-5", "/tmp")
        for _ in range(200):
            if started:
                break
            time.sleep(0.005)
    finally:
        bridge.stop()

    assert [s.subagent_cap for s in started] == [6]


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


def test_the_grace_period_settles_every_subagent_it_gives_up_on(monkeypatch) -> None:
    """
    Giving up on the read is not giving up on the record. A sub-agent left in the
    live set never reaches a terminal state -- its card spins for the session's life
    and any capacity count read off that set can only ever shrink.
    """
    from pptmstr import driver as driver_mod
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession

    monkeypatch.setattr(driver_mod, "SUBAGENT_GRACE_S", 0.01)

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def start_two_then_wait() -> None:
        await _fire_start(session, "alpha-id")
        await _fire_start(session, "beta-id")
        bridge.drain()
        translator = Translator(session.node_id, session.transcript)
        await session._await_subagents(_SilentClient(), translator)  # type: ignore[arg-type]

    asyncio.run(start_two_then_wait())

    finished = [i for i in bridge.drain() if isinstance(i, AgentFinished)]
    assert {i.node_id for i in finished} == {
        (session.session_id, "alpha-id"),
        (session.session_id, "beta-id"),
    }
    assert all(i.state is AgentState.FAILED for i in finished)
    assert all(i.error for i in finished)
    assert session._live_subagents == set()


def test_a_wake_after_the_grace_period_is_still_not_a_respawn(monkeypatch) -> None:
    """
    Giving up on the read says nothing about whether the sub-agent can be woken. The
    id has to stay known, or the wake rebuilds the record the timeout just settled.
    """
    from pptmstr import driver as driver_mod
    from pptmstr.bridge import Bridge
    from pptmstr.driver import AgentSession
    from pptmstr.intents import AgentResumed

    monkeypatch.setattr(driver_mod, "SUBAGENT_GRACE_S", 0.01)

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")

    async def start_time_out_then_wake() -> None:
        await _fire_start(session, "alpha-id")
        translator = Translator(session.node_id, session.transcript)
        await session._await_subagents(_SilentClient(), translator)  # type: ignore[arg-type]
        assert session._seen_subagents == {"alpha-id"}
        bridge.drain()
        await _fire_start(session, "alpha-id")

    asyncio.run(start_time_out_then_wake())

    (woken,) = bridge.drain()
    assert isinstance(woken, AgentResumed)
    assert woken.node_id == (session.session_id, "alpha-id")


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
