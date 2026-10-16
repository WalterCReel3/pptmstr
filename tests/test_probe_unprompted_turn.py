"""
scripts/probe_unprompted_turn.py's parsing: SDK messages to log lines, log lines to
turns, turns to a verdict.

The probe's "no" is only as good as its classifier, so the hazard each test names is
a turn the classifier could fail to flag (or flag wrongly), built from the SDK's own
message classes rather than from the probe's Event, so ``describe`` is in the path.
The last tests drive two arms over a fake client through the same reader the live
run uses, which is the wiring: a classifier nothing feeds would pass every unit
test above them.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import TaskNotificationMessage

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    path = ROOT / "scripts" / "probe_unprompted_turn.py"
    spec = importlib.util.spec_from_file_location("probe_unprompted_turn", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_unprompted_turn"] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def _assistant(*tools: str, parent: str | None = None, stop: str = "end_turn") -> AssistantMessage:
    content: list[Any] = [ToolUseBlock(id=f"tu-{t}", name=t, input={}) for t in tools]
    content = content or [TextBlock(text="x")]
    return AssistantMessage(content=content, model="m", parent_tool_use_id=parent, stop_reason=stop)


def _result(origin: str | None = None, reason: str = "completed") -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        terminal_reason=reason,
        origin=cast(Any, {"kind": origin}) if origin is not None else None,
    )


def _start(parent: str | None = None) -> StreamEvent:
    return StreamEvent(
        uuid="u", session_id="s", event={"type": "message_start"}, parent_tool_use_id=parent
    )


def _notification() -> TaskNotificationMessage:
    return TaskNotificationMessage(
        subtype="task_notification",
        data={},
        task_id="t1",
        status="completed",
        output_file="",
        summary="",
        uuid="u",
        session_id="s",
    )


def _log(*items: object) -> list[Any]:
    """
    A log from a script of "query" strings and SDK messages, one second apart, with
    each message passed through ``describe`` exactly as the reader does.
    """
    events = []
    for t, item in enumerate(items):
        if item == "query":
            events.append(probe.Event(float(t), "query"))
            continue
        event = probe.describe(item, float(t))
        if event is not None:
            events.append(event)
    return events


def test_a_prompted_turn_is_one_prompted_result_and_nothing_flagged() -> None:
    turns = probe.classify(_log("query", _start(), _assistant("Bash"), _assistant(), _result()))
    assert (turns.queries, len(turns.prompted), turns.outstanding) == (1, 1, 0)
    assert not turns.unprompted


def test_a_turn_after_the_last_result_is_flagged_by_count_with_no_origin() -> None:
    turns = probe.classify(
        _log("query", _assistant(), _result(), _notification(), _assistant(), _result())
    )
    assert turns.unprompted_starts == (4.0,)
    assert turns.unpaired == (5.0,)
    assert turns.injected == ()


def test_a_bare_message_start_after_the_last_result_opens_an_unprompted_turn() -> None:
    turns = probe.classify(_log("query", _assistant(), _result(), _start()))
    assert turns.unprompted_starts == (3.0,)
    assert turns.unprompted


def test_an_injected_turn_while_a_prompt_waits_does_not_take_the_prompts_result() -> None:
    """
    By count alone this log is two queries, two results, all paired. The origin is
    what says the first result answered nobody's prompt and the second query is
    still waiting.
    """
    turns = probe.classify(
        _log("query", _assistant(), _result(), "query", _assistant(), _result("task-notification"))
    )
    assert turns.injected == ((5.0, "task-notification"),)
    assert turns.prompted == (2.0,)
    assert turns.outstanding == 1


def test_an_unknown_origin_kind_counts_as_injected() -> None:
    turns = probe.classify(_log("query", _assistant(), _result("some-future-kind")))
    assert turns.injected == ((2.0, "some-future-kind"),)


def test_a_human_origin_is_the_consumers_own_prompt() -> None:
    turns = probe.classify(_log("query", _assistant(), _result("human")))
    assert turns.prompted == (2.0,) and not turns.unprompted


def test_sub_agent_messages_after_the_parents_result_are_not_a_root_turn() -> None:
    turns = probe.classify(
        _log(
            "query",
            _assistant("Agent"),
            _result(),
            _start(parent="tu-Agent"),
            _assistant("Bash", parent="tu-Agent"),
            UserMessage(content="r", parent_tool_use_id="tu-Agent"),
        )
    )
    assert not turns.unprompted


def test_two_queued_prompts_answered_by_one_result_leave_one_outstanding() -> None:
    turns = probe.classify(_log("query", "query", _assistant(), _result()))
    assert (turns.queries, len(turns.prompted), turns.outstanding) == (2, 1, 1)


def test_system_frames_between_turns_are_not_turns() -> None:
    turns = probe.classify(
        _log("query", _assistant(), _result(), SystemMessage(subtype="status", data={}))
    )
    assert not turns.unprompted


def test_token_deltas_are_not_logged_and_message_starts_are() -> None:
    delta = StreamEvent(uuid="u", session_id="s", event={"type": "content_block_delta"})
    assert probe.describe(delta, 0.0) is None
    assert probe.describe(_start(), 0.0) is not None


def test_a_log_reads_back_as_written() -> None:
    events = _log("query", _assistant("Bash", "Read"), _result("task-notification"))
    lines = [e.to_json() for e in events]
    assert [probe.Event.from_json(line) for line in lines] == events


def test_the_table_flags_exactly_what_classify_flags() -> None:
    rows = probe.table(probe.SAMPLE)
    flagged = [r for r in rows if "not asked for" in r]
    assert len(flagged) == 2
    assert "UserMessage" in flagged[0] and "ResultMessage" in flagged[1]


def test_the_built_in_sample_passes_its_self_check() -> None:
    assert probe.self_check() == 0


# ------------------------------------------------------------------------- verdict

_QUIET = probe.Turns(1, (1.0,), (), (), (), 0)
_FOLLOW_UP = probe.Turns(1, (2.0,), (), (), (), 0)


def test_none_seen_needs_every_control() -> None:
    assert (
        probe.verdict(_QUIET, tool_called=True, work_done=True, control=_FOLLOW_UP) == "none-seen"
    )


def test_a_quiet_window_without_the_tool_is_not_a_no() -> None:
    v = probe.verdict(_QUIET, tool_called=False, work_done=True, control=_FOLLOW_UP)
    assert v.startswith("inconclusive")


def test_a_quiet_window_before_the_work_finished_is_not_a_no() -> None:
    v = probe.verdict(_QUIET, tool_called=True, work_done=False, control=_FOLLOW_UP)
    assert v.startswith("inconclusive")


def test_a_quiet_window_with_a_dead_reader_is_not_a_no() -> None:
    dead = probe.Turns(1, (), (), (), (), 1)
    assert probe.verdict(_QUIET, tool_called=True, work_done=True, control=dead).startswith(
        "inconclusive"
    )
    assert probe.verdict(_QUIET, tool_called=True, work_done=True, control=None).startswith(
        "inconclusive"
    )


def test_an_unprompted_turn_is_reported_whatever_the_controls_say() -> None:
    seen = probe.Turns(1, (1.0,), (), (), (3.0,), 0)
    assert probe.verdict(seen, tool_called=False, work_done=False, control=None) == "UNPROMPTED"


# -------------------------------------------------------------------------- wiring


class _FakeClient:
    """
    Answers each query with a scripted turn on the stream ``receive_messages``
    yields, and records PreToolUse the way the probe's hook would. ``inject`` puts a
    task-notification turn on the stream after the first turn's background work.
    """

    def __init__(self, rec: Any, marker: Path | None, inject: bool) -> None:
        self.rec = rec
        self.marker = marker
        self.inject = inject
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.sent = 0

    async def query(self, text: str) -> None:
        self.sent += 1
        if self.sent == 1 and self.marker is not None:
            m: object
            for m in (_start(), _assistant("Bash", stop="tool_use")):
                await self.queue.put(m)
            self.rec.add(probe.Event(self.rec.now(), "hook", "PreToolUse", "Bash"))
            for m in (_assistant(), _result()):
                await self.queue.put(m)
            self.marker.touch()
            if self.inject:
                for m in (_notification(), _start(), _assistant(), _result("task-notification")):
                    await self.queue.put(m)
            return
        for m in (_start(), _assistant(), _result()):
            await self.queue.put(m)

    async def interrupt(self) -> None:
        pass

    async def receive_messages(self) -> Any:
        while True:
            yield await self.queue.get()


async def _drive(arm: Any, tmp_path: Path, marker: str | None, inject: bool) -> Any:
    rec = probe.Recorder()
    client = _FakeClient(rec, tmp_path / marker if marker else None, inject)
    reader = asyncio.ensure_future(probe._read(client, rec))
    try:
        return await arm(client, rec, tmp_path)
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


def _no_waits(monkeypatch: Any) -> None:
    async def instant(seconds: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(probe, "_pause", instant)
    monkeypatch.setattr(probe, "GRACE_S", 0.0)


def test_the_control_arm_records_a_prompted_turn_through_the_reader(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _no_waits(monkeypatch)
    result = asyncio.run(_drive(probe.arm_control, tmp_path, None, False))
    assert result.verdict == "recorded as one prompted turn"


def test_the_bash_arm_reports_an_injected_turn_it_was_handed(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _no_waits(monkeypatch)
    result = asyncio.run(_drive(probe.arm_bg_bash, tmp_path, "bg-bash.done", True))
    assert result.verdict == "UNPROMPTED"


def test_the_bash_arm_says_none_seen_only_with_its_controls_met(
    monkeypatch: Any, tmp_path: Path
) -> None:
    _no_waits(monkeypatch)
    result = asyncio.run(_drive(probe.arm_bg_bash, tmp_path, "bg-bash.done", False))
    assert result.verdict == "none-seen"


# -------------------------------------------------------------- a recorded transcript

# events.jsonl of the bg-bash arm, run by the operator on 2026-10-16 (CLI 2.1.301,
# claude-sonnet-5, cmd-20261016T152917Z-2f51f4b8dee2), with its 15 thinking_tokens
# SystemMessage lines taken out: they are not root activity and change nothing below.
RECORDED_BG_BASH = """\
{"t": 1.133, "kind": "query", "type": "", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": "start"}
{"t": 1.212, "kind": "message", "type": "SystemMessage", "subtype": "init", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 1.216, "kind": "message", "type": "SystemMessage", "subtype": "status", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 1.728, "kind": "message", "type": "RateLimitEvent", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 2.836, "kind": "message", "type": "StreamEvent", "subtype": "message_start", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 4.308, "kind": "message", "type": "AssistantMessage", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": ["Bash"], "label": ""}
{"t": 4.318, "kind": "hook", "type": "PreToolUse", "subtype": "Bash", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 4.45, "kind": "message", "type": "SystemMessage", "subtype": "background_tasks_changed", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 4.451, "kind": "message", "type": "TaskStartedMessage", "subtype": "task_started", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 4.464, "kind": "hook", "type": "PostToolUse", "subtype": "Bash", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 4.471, "kind": "message", "type": "UserMessage", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 4.479, "kind": "message", "type": "SystemMessage", "subtype": "status", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 5.804, "kind": "message", "type": "StreamEvent", "subtype": "message_start", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 5.87, "kind": "message", "type": "AssistantMessage", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 5.895, "kind": "message", "type": "ResultMessage", "subtype": "success", "root": true, "origin": "", "terminal_reason": "completed", "tools": [], "label": ""}
{"t": 19.46, "kind": "message", "type": "TaskUpdatedMessage", "subtype": "task_updated", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": "completed"}
{"t": 19.461, "kind": "mark", "type": "", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": "marker seen"}
{"t": 19.461, "kind": "message", "type": "TaskNotificationMessage", "subtype": "task_notification", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": "completed"}
{"t": 19.461, "kind": "message", "type": "SystemMessage", "subtype": "background_tasks_changed", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 19.512, "kind": "message", "type": "SystemMessage", "subtype": "init", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 19.513, "kind": "message", "type": "SystemMessage", "subtype": "status", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 20.859, "kind": "message", "type": "StreamEvent", "subtype": "message_start", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 25.356, "kind": "message", "type": "AssistantMessage", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 25.358, "kind": "message", "type": "AssistantMessage", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 25.369, "kind": "message", "type": "ResultMessage", "subtype": "success", "root": true, "origin": "task-notification", "terminal_reason": "completed", "tools": [], "label": ""}
{"t": 64.5, "kind": "mark", "type": "", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": "window closed"}
{"t": 64.5, "kind": "query", "type": "", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": "follow-up"}
{"t": 64.52, "kind": "message", "type": "SystemMessage", "subtype": "init", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 64.523, "kind": "message", "type": "SystemMessage", "subtype": "status", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 67.143, "kind": "message", "type": "StreamEvent", "subtype": "message_start", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 67.145, "kind": "message", "type": "AssistantMessage", "subtype": "", "root": true, "origin": "", "terminal_reason": "", "tools": [], "label": ""}
{"t": 67.159, "kind": "message", "type": "ResultMessage", "subtype": "success", "root": true, "origin": "", "terminal_reason": "completed", "tools": [], "label": ""}
"""  # noqa: E501 - recorded lines, kept as the probe wrote them


def test_the_recorded_background_bash_run_classifies_as_one_injected_turn() -> None:
    """
    What the CLI did: the prompted turn ended at 5.9 s, the background Bash finished
    and the CLI ran a turn of its own at 20.9 s whose result carries
    origin=task-notification, and the follow-up query got its own result.
    """
    events = [probe.Event.from_json(line) for line in RECORDED_BG_BASH.splitlines()]
    turns = probe.classify(events)
    assert turns.queries == 2
    assert turns.prompted == (5.895, 67.159)
    assert turns.injected == ((25.369, "task-notification"),)
    assert turns.unprompted_starts == (20.859,)
    assert turns.unpaired == () and turns.outstanding == 0
