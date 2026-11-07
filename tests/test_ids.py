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
from pptmstr.intents import ChannelLinked
from pptmstr.model import is_channel_name
from pptmstr.store import Store
from tests.test_bus import DEV, LEAD, _live_bus, _raised, _team
from tests.test_commandbus import _desk

CONCERN = re.compile(r"c-[0-9a-f]{12}")
DECISION = re.compile(r"dq-[0-9a-f]{12}")
TASK = re.compile(r"t-[0-9a-f]{8}")
COMMAND = re.compile(r"cmd-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}")
REQUEST = re.compile(r"req-[0-9a-f]{12}")


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
    A channel's id names its log's directory (``channels.LinkKey``), so the id
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

    ids = [m.channel_id for m in made if isinstance(m, ChannelLinked)]
    assert len(set(ids)) == 3
    assert all(is_channel_name(i) for i in ids), ids


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
    ],
    ids=["request", "concern", "decision", "task", "command"],
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
