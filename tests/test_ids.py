"""
Every id pptmstr mints, in the exact form it is stored, shown or read back in.

The forms are spelled out here and not built from ``ids``' table: a test that
read the prefix from the module it pins would pass whatever the prefix became.
A concern's, a decision's, a task's and a command's id are written to board and
command logs and shown to agents, and two of them are parsed back, so a changed
form is a changed file format.

The handler pins drive the real tools on a live bridge, because what is pinned
is the id a tool hands out, not the id a function would return if it were the
one the tool called.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from typing import Any

import pytest

from pptmstr import ids
from pptmstr.bridge import Bridge
from pptmstr.channels.records import is_channel_name
from pptmstr.intents import ChannelLinked
from pptmstr.store import Store
from tests.test_bus import DEV, LEAD, _live_bus, _raised, _team
from tests.test_commandbus import _desk

CONCERN = re.compile(r"c-[0-9a-f]{12}")
DECISION = re.compile(r"dq-[0-9a-f]{12}")
TASK = re.compile(r"t-[0-9a-f]{8}")
COMMAND = re.compile(r"cmd-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
REQUEST = re.compile(r"req-[0-9a-f]{12}")
# ``str(uuid4())`` exactly: a channel's id and a session's. A resume hands the
# session's back to the CLI as the CLI's own.
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
CALL_KEY = re.compile(r"anon-[0-9a-f]{32}")


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Every request id a handler registers with a bridge, in order."""
    seen: list[str] = []
    real = Bridge.ask

    def recording(self: Bridge, request_id: str, on_abandon: Any) -> Any:
        seen.append(request_id)
        return real(self, request_id, on_abandon)

    monkeypatch.setattr(Bridge, "ask", recording)
    yield seen


# -- domain ids, through the tools that mint them ----------------------------------


def test_a_concern_the_bus_files_is_c_and_twelve_hex() -> None:
    store = Store()
    _team(store)
    with _live_bus() as bus:
        for _ in range(3):
            bus.call(store, "post_concern", {"to": "lead", "subject": "s", "body": "b"}, sender=DEV)
        bus.pump(store)

    minted = list(store.snapshot().concerns)
    assert len(set(minted)) == 3
    assert all(CONCERN.fullmatch(i) for i in minted), minted


def test_a_decision_the_bus_files_is_dq_and_twelve_hex() -> None:
    store = Store()
    _team(store)
    with _live_bus() as bus:
        made = {_raised(bus, store) for _ in range(3)}

    assert len(made) == 3
    assert all(DECISION.fullmatch(i) for i in made), made


def test_a_task_declared_without_an_id_is_t_and_eight_hex() -> None:
    store = Store()
    _team(store)
    with _live_bus() as bus:
        for _ in range(3):
            bus.call(store, "declare_task", {"title": "untitled work"}, sender=LEAD)
        bus.call(store, "declare_task", {"task_id": "  mine ", "title": "named"}, sender=LEAD)

    held = set(store.snapshot().tasks)
    assert "mine" in held
    minted = held - {"mine"}
    assert len(minted) == 3
    assert all(TASK.fullmatch(i) for i in minted), minted


def test_a_command_id_is_its_utc_second_and_twelve_hex() -> None:
    with _desk() as desk:
        made = [desk.ask(DEV) for _ in range(3)]
        held = [desk.store.snapshot().commands[i] for i in made]

    assert len(set(made)) == 3
    for command_id, command in zip(made, held, strict=True):
        assert COMMAND.fullmatch(command_id), command_id
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(command.asked_at))
        assert command_id.startswith(f"cmd-{stamp}-")


def test_a_channel_id_is_a_name_a_link_log_can_live_under() -> None:
    """
    A channel's id names its log's directory (``channels.facts.LinkKey``), so the id
    ``link_leads`` mints must pass the plain-name rule the log lister reads
    directories back by.
    """
    from pptmstr import app

    class _State:
        channels_unlisted = None
        board_logs: dict[str, Any] = {}

        def __init__(self) -> None:
            self.bridge = Bridge()

    state = _State()
    made = [
        app.link_leads(state, "brand", frozenset({"a", "b"}), "")  # type: ignore[arg-type]
        for _ in range(3)
    ]

    minted = [m.channel_id for m in made if isinstance(m, ChannelLinked)]
    assert len(set(minted)) == 3
    assert all(is_channel_name(i) for i in minted), minted
    assert all(UUID4.fullmatch(i) for i in minted), minted


# -- the driver's ids, through the session that mints them -------------------------


class _Parked(Exception):
    pass


def _parked_under(*tool_use_ids: str) -> tuple[str, list[str]]:
    """
    A fresh session's id and the pending id its gate parks each call under, in
    order, each stopped at the park so no operator is needed.
    """
    import asyncio

    from pptmstr.driver import AgentSession

    bridge = Bridge()
    session = AgentSession(bridge, task="lead")
    seen: list[str] = []

    def park(pending_id: str) -> Any:
        seen.append(pending_id)
        raise _Parked

    bridge.park = park  # type: ignore[method-assign]
    for tool_use_id in tool_use_ids:
        with pytest.raises(_Parked):
            asyncio.run(
                session._park("Write", {"file_path": "/tmp/x"}, tool_use_id, session.node_id)
            )
    return session.session_id, seen


def test_a_fresh_session_id_is_a_uuid4_string_and_a_resumed_one_is_kept() -> None:
    from pptmstr.driver import AgentSession

    made = [AgentSession(Bridge(), task="lead").session_id for _ in range(3)]
    assert len(set(made)) == 3
    assert all(UUID4.fullmatch(i) for i in made), made

    resumed = AgentSession(Bridge(), task="lead", resume="the-cli-s-own-id")
    assert resumed.session_id == "the-cli-s-own-id"


def test_a_pending_id_is_the_sessions_first_eight_and_the_call_or_eight_hex() -> None:
    """
    A pending id is the key its future waits under in the bridge, so one
    session's calls without a tool_use_id must not share one.
    """
    session_id, (named, *anonymous) = _parked_under("toolu_01ABC", "", "", "")
    assert named == f"{session_id[:8]}-toolu_01ABC"

    assert len(set(anonymous)) == 3
    form = re.compile(rf"{re.escape(session_id[:8])}-[0-9a-f]{{8}}")
    assert all(form.fullmatch(p) for p in anonymous), anonymous


def test_a_subagent_call_without_a_tool_use_id_is_keyed_anon_and_32_hex() -> None:
    """
    The key a sub-agent's call holds its veto under while the gate runs: the
    call's own tool_use_id when it has one, an ``anon-`` key otherwise.
    """
    import asyncio

    from pptmstr.driver import AgentSession
    from tests.test_driver import _fire_gate, _fire_start

    session = AgentSession(Bridge(), task="lead")
    held: list[set[str]] = []

    async def gate(*_args: Any) -> dict[str, Any]:
        held.append(set(session._subagent_in_flight["alpha-id"]))
        return {}

    session._gate_tool_use = gate  # type: ignore[assignment]

    async def drive() -> None:
        await _fire_start(session, "alpha-id")
        await _fire_gate(session, "alpha-id", call_id="tu-7")
        for _ in range(3):
            await _fire_gate(session, "alpha-id", call_id="")

    asyncio.run(drive())

    assert held[0] == {"tu-7"}
    anon = [k for keys in held[1:] for k in keys if k != "tu-7"]
    assert len(set(anon)) == 3
    assert all(CALL_KEY.fullmatch(k) for k in anon), anon


# -- request ids ------------------------------------------------------------------


def test_every_request_id_a_tool_parks_under_is_distinct_and_one_form(asked: list[str]) -> None:
    """
    A request id is a key in ``Bridge._requests`` and nothing else: a second
    outstanding request under the same key would take the first one's answer.
    """
    store = Store()
    _team(store)
    with _live_bus() as bus:
        bus.call(store, "declare_task", {"task_id": "t1", "title": "do t1"}, sender=LEAD)
        bus.call(store, "claim_task", {"task_id": "t1"}, sender=DEV)
        bus.call(store, "read_board", {}, sender=DEV)
        bus.call(store, "read_inbox", {}, sender=DEV)
        bus.call(store, "read_inbox", {}, sender=DEV)
        _raised(bus, store)
        bus.call(store, "read_decisions", {}, sender=DEV)
    with _desk() as desk:
        made = desk.ask(DEV)
        desk.read(DEV)
        desk.withdraw(made, DEV)

    assert len(asked) >= 10
    assert len(set(asked)) == len(asked), asked
    assert all(REQUEST.fullmatch(i) for i in asked), asked


# -- the module, directly ---------------------------------------------------------

_DRAWS = 500


@pytest.mark.parametrize(
    ("mint", "form"),
    [
        (ids.request, REQUEST),
        (ids.concern, CONCERN),
        (ids.decision, DECISION),
        (ids.task, TASK),
        (lambda: ids.command(1_790_000_000.0), COMMAND),
        (ids.channel, UUID4),
        (ids.session, UUID4),
        (lambda: ids.pending("0123abcd-rest", ""), re.compile(r"0123abcd-[0-9a-f]{8}")),
        (lambda: ids.call_key(""), CALL_KEY),
    ],
    ids=[
        "request",
        "concern",
        "decision",
        "task",
        "command",
        "channel",
        "session",
        "pending",
        "call_key",
    ],
)
def test_each_mint_is_its_pinned_form_and_does_not_repeat(mint: Any, form: re.Pattern[str]) -> None:
    drawn = [mint() for _ in range(_DRAWS)]
    assert all(form.fullmatch(i) for i in drawn), drawn[:3]
    assert len(set(drawn)) == _DRAWS


def test_a_command_id_is_stamped_with_the_time_it_is_handed_in_utc() -> None:
    assert ids.command(0.0).startswith("cmd-19700101T000000Z-")
    assert ids.command(1_790_000_000.9).startswith("cmd-20260921T141320Z-")


def test_each_kind_is_read_back_as_itself_and_no_other() -> None:
    """
    A concern id is told from every other minted kind by its shape alone, and
    so is a command id: ``re`` and a command's directory name are read by these.
    """
    minted = {
        "request": ids.request(),
        "concern": ids.concern(),
        "decision": ids.decision(),
        "task": ids.task(),
        "command": ids.command(1_790_000_000.0),
    }
    assert [k for k, v in minted.items() if ids.is_concern_id(v)] == ["concern"]
    assert [k for k, v in minted.items() if ids.is_command_id(v)] == ["command"]


@pytest.mark.parametrize(
    "text",
    [
        "c-0123456789a",
        "c-0123456789abc",
        "c-0123456789AB",
        "c-0123456789a\n",
        "c-0123456789a\u0661",
        "d-0123456789ab",
        "",
    ],
)
def test_what_is_not_a_concern_id(text: str) -> None:
    assert not ids.is_concern_id(text)


def test_a_concern_id_is_exactly_c_and_twelve_lower_hex() -> None:
    assert ids.is_concern_id("c-0123456789ab")


@pytest.mark.parametrize(
    "value",
    [
        "cmd-20261013T101500Z-0123456789a",
        "cmd-20261013T101500Z-0123456789ab\n",
        "cmd-20261013T101500Z-0123456789AB",
        "cmd-2026101T3101500Z-0123456789ab",
        "cmd-20261013T101500Z-../123456789",
        None,
        12,
    ],
)
def test_what_is_not_a_command_id(value: object) -> None:
    assert not ids.is_command_id(value)
