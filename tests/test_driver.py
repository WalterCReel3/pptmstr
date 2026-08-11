"""
Driver translation: SDK messages in, intents and transcript writes out.

Exercised with real SDK message objects but no subprocess. The translation is where
the store's honesty under streaming is decided, and it is worth being able to test
that without spending tokens or waiting on a CLI.
"""

from __future__ import annotations

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

from pptmstr.driver import Translator, _tool_topic
from pptmstr.intents import (
    AgentFinished,
    CompactionObserved,
    StateChanged,
    TopicChanged,
    UsageAccrued,
)
from pptmstr.model import AgentState, NodeId
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


def test_success_finishes_done() -> None:
    tr, _ = make()
    intents = tr.handle(result(total_cost_usd=0.02, terminal_reason="completed"))
    finished = next(i for i in intents if isinstance(i, AgentFinished))
    assert finished.state is AgentState.DONE


def test_interrupted_turn_is_cancelled_not_done() -> None:
    """
    A cancelled agent is not a completed one, and conflating them would tell the
    operator their interrupt did nothing.
    """
    tr, _ = make()
    intents = tr.handle(result(terminal_reason="aborted_streaming"))
    finished = next(i for i in intents if isinstance(i, AgentFinished))
    assert finished.state is AgentState.CANCELLED


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
