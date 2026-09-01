"""
Resuming a session: which id reaches argv, and what happens when the CLI disagrees.

No subprocess anywhere. ``_options()`` is inspected directly, and ``run()`` is driven
against a fake client, because the property under test is an *identity* -- which
session the tree is attached to -- and a live CLI would settle that by accident
rather than by assertion.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from claude_agent_sdk import ResultMessage, SystemMessage

from pptmstr.bridge import Bridge
from pptmstr.driver import AgentSession, SessionIdentityError
from pptmstr.intents import AgentFinished, AgentSpawned
from pptmstr.model import AgentState, LaunchSpec

RESUMED = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


def init_frame(session_id: str | None) -> SystemMessage:
    """
    The handshake frame, shaped as the SDK really delivers it.

    ``SystemMessage`` carries only ``subtype`` and ``data`` -- there is no
    ``session_id`` attribute to set -- so a test that reached for one would be
    asserting against a message shape that never arrives.
    """
    data: dict[str, object] = {"type": "system", "subtype": "init"}
    if session_id is not None:
        data["session_id"] = session_id
    return SystemMessage(subtype="init", data=data)


def result_frame(session_id: str) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id=session_id,
    )


class _FakeClient:
    """Enough of ClaudeSDKClient for ``run()`` to finish with no subprocess."""

    def __init__(self, messages: list[object]) -> None:
        self._messages = messages
        # What the loop actually pulled. A test about a stream that must *stop* has
        # to be able to see that it stopped, which the emitted intents cannot show.
        self.consumed: list[object] = []

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def query(self, prompt: object, session_id: str = "default") -> None:
        return None

    async def get_context_usage(self) -> dict:
        # _poll_context logs and drops failures, which keeps ContextPolled out of the
        # intent stream without every test having to filter it.
        raise RuntimeError("no CLI attached")

    async def receive_messages(self):
        for message in self._messages:
            self.consumed.append(message)
            yield message


def drive(session: AgentSession, messages: list[object], monkeypatch) -> list[object]:
    """Run one session to completion against a fake stream; return what it emitted."""
    monkeypatch.setattr("pptmstr.driver.ClaudeSDKClient", lambda **_: _FakeClient(messages))
    # Tagging is a real filesystem append against a session file no test has; stubbed
    # so these tests are about identity and nothing else.
    monkeypatch.setattr("pptmstr.driver.tag_session", lambda *a, **k: None)
    asyncio.run(session.run())
    return list(session.bridge.drain())


# -- what reaches argv ---------------------------------------------------------


def test_a_resumed_session_passes_resume_and_no_session_id() -> None:
    """
    The whole design in one assertion: one id reaches the CLI, not two.

    ``ClaudeAgentOptions.session_id`` documents itself as unusable with ``resume``
    unless ``fork_session`` is set, and the transport emits both flags from
    independent blocks with no mutual-exclusion check. Omitting one is what stops the
    question of which wins from ever being asked.
    """
    options = AgentSession(Bridge(), task="carry on", resume=RESUMED)._options()

    assert options.resume == RESUMED
    assert options.session_id is None
    # A fork is what makes a resumed session take a new id instead of continuing the
    # previous one, which is precisely what must not happen here.
    assert options.fork_session is False


def test_a_fresh_session_is_unchanged() -> None:
    """The path that already worked keeps working, and keeps working the same way."""
    session = AgentSession(Bridge(), task="start something")
    options = session._options()

    assert options.session_id == session.session_id
    assert options.resume is None
    assert uuid.UUID(session.session_id).version == 4


def test_session_store_is_left_unset_on_both_paths() -> None:
    """
    Setting it would send the SDK down a materialize-into-a-temp-config-dir branch
    that overrides resume on a copy of these options -- so a session with it set is
    not measuring the path production takes.
    """
    assert AgentSession(Bridge(), task="t")._options().session_store is None
    assert AgentSession(Bridge(), task="t", resume=RESUMED)._options().session_store is None


# -- what the NodeId is, and when ----------------------------------------------


def test_the_node_id_is_the_resumed_id_from_construction() -> None:
    """
    Settled before ``run()``, because three things downstream read it before ``run()``
    is scheduled: ``SessionPool.submit`` keys ``sessions`` on it and announces,
    ``_start`` keys ``_running`` on it, and ``app._seed_brief`` derives the brief
    directory from it. An id learned at the handshake would leave all three stale.
    """
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)

    assert session.session_id == RESUMED
    assert session.node_id == (RESUMED, None)


def test_the_announced_row_carries_the_resumed_id(monkeypatch) -> None:
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    emitted = drive(session, [init_frame(RESUMED), result_frame(RESUMED)], monkeypatch)

    (spawned,) = [i for i in emitted if isinstance(i, AgentSpawned)]
    assert spawned.node_id == (RESUMED, None)


# -- the CLI disagreeing -------------------------------------------------------


def test_a_different_id_at_init_fails_the_session(monkeypatch) -> None:
    """
    The load-bearing failure. A CLI that forked to its own id must not be followed:
    every NodeId is derived from the session id, and the pool's maps are keyed on the
    one settled at construction, so continuing would give a live-looking session whose
    send, interrupt and close all silently miss.
    """
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    emitted = drive(session, [init_frame(OTHER)], monkeypatch)

    (finished,) = [i for i in emitted if isinstance(i, AgentFinished)]
    assert finished.state is AgentState.FAILED
    assert finished.node_id == (RESUMED, None)
    assert OTHER in (finished.error or "")


def test_a_different_id_at_the_result_fails_the_session(monkeypatch) -> None:
    """
    ``ResultMessage.session_id`` is checked too, and it is the authoritative reading:
    a required attribute reported after the CLI has settled what it is doing, where
    the init frame is only the early one.
    """
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    emitted = drive(session, [result_frame(OTHER)], monkeypatch)

    (finished,) = [i for i in emitted if isinstance(i, AgentFinished)]
    assert finished.state is AgentState.FAILED
    assert OTHER in (finished.error or "")


def test_the_mismatch_stops_the_stream_rather_than_reporting_it_and_continuing(
    monkeypatch,
) -> None:
    """A message after the bad handshake must never be attributed to this tree."""
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    client = _FakeClient([init_frame(OTHER), result_frame(OTHER)])
    monkeypatch.setattr("pptmstr.driver.ClaudeSDKClient", lambda **_: client)
    monkeypatch.setattr("pptmstr.driver.tag_session", lambda *a, **k: None)
    asyncio.run(session.run())
    emitted = list(session.bridge.drain())

    # The bad init frame and nothing after it. Reporting the mismatch and reading on
    # would be the silent-divergence outcome this whole path exists to prevent.
    assert len(client.consumed) == 1
    # The turn-end arm emits AWAITING_INPUT for every non-error result it handles, so
    # its absence is independent evidence the second frame was never translated.
    assert not [i for i in emitted if getattr(i, "state", None) is AgentState.AWAITING_INPUT]


def test_an_init_frame_with_no_id_fails_the_session(monkeypatch) -> None:
    """
    A handshake that declines to name an id leaves nothing to check against -- we
    passed no ``session_id``, so there is no fallback. Reported rather than assumed
    fine, which is the difference between "checked" and "could not check".
    """
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    emitted = drive(session, [init_frame(None)], monkeypatch)

    (finished,) = [i for i in emitted if isinstance(i, AgentFinished)]
    assert finished.state is AgentState.FAILED
    assert "no session id" in (finished.error or "")


def test_a_stream_that_never_names_an_id_fails_the_session(monkeypatch) -> None:
    """
    "The CLI never said" is not "the CLI agreed". A resumed row the operator will
    trust as continued work has to have been confirmed by something.
    """
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    emitted = drive(session, [], monkeypatch)

    (finished,) = [i for i in emitted if isinstance(i, AgentFinished)]
    assert finished.state is AgentState.FAILED
    assert "never reported a session id" in (finished.error or "")


def test_the_check_raises_rather_than_returning_a_verdict() -> None:
    """
    A distinct type, raised, and checked directly rather than through ``run()``.

    Going through ``run()`` proves the session ends but not *how*: its
    ``except Exception`` arm would render a ``KeyError`` from a typo in the same
    FAILED row with the same shape. Naming the type here is what stops a refactor
    turning the check into something that returns quietly and is ignored.
    """
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)

    with pytest.raises(SessionIdentityError):
        session._check_identity(init_frame(OTHER))

    # A message with no opinion about the id passes through untouched, and the
    # session is not confirmed by it either.
    session._check_identity(SystemMessage(subtype="compact_boundary", data={}))
    assert not session._identity_confirmed

    session._check_identity(result_frame(RESUMED))
    assert session._identity_confirmed


def test_a_matching_id_finishes_normally(monkeypatch) -> None:
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    emitted = drive(session, [init_frame(RESUMED), result_frame(RESUMED)], monkeypatch)

    (finished,) = [i for i in emitted if isinstance(i, AgentFinished)]
    assert finished.state is AgentState.DONE
    assert session._identity_confirmed


def test_a_fresh_session_is_not_identity_checked(monkeypatch) -> None:
    """
    Deliberately asymmetric. Whether the CLI honours ``--session-id`` is a separate
    question with its own measurement; checking it here would add a new way for
    today's launches -- every launch that is not a resume -- to fail.
    """
    session = AgentSession(Bridge(), task="start something")
    emitted = drive(session, [init_frame(OTHER), result_frame(OTHER)], monkeypatch)

    (finished,) = [i for i in emitted if isinstance(i, AgentFinished)]
    assert finished.state is AgentState.DONE


# -- tagging -------------------------------------------------------------------


def test_the_session_is_tagged_once_with_its_own_id(monkeypatch) -> None:
    """
    Not at construction: ``tag_session`` appends without ``O_CREAT`` and raises until
    the CLI has made the file. A completed turn is the first moment it is certainly
    there, which is why the trigger is the result rather than the first message.
    """
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("pptmstr.driver.tag_session", lambda *a: calls.append(a))
    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient(
            [init_frame(RESUMED), result_frame(RESUMED), result_frame(RESUMED)]
        ),
    )
    session = AgentSession(Bridge(), task="carry on", cwd="/tmp/project", resume=RESUMED)
    asyncio.run(session.run())

    # Once, despite two turns, and under this session's own id.
    assert calls == [(RESUMED, "pptmstr", "/tmp/project")]
    assert session._tagged
    # Reaped before run() returned. A detached task outliving its loop is destroyed
    # with a warning and a half-written append.
    assert session._tag_task is None


def test_tagging_does_not_suspend_the_message_loop(monkeypatch) -> None:
    """
    The tag is a detached task, not an await in the loop.

    Not a stylistic preference. That loop is the only thing servicing the approval
    gate and every hook callback for the session, so an inline thread hop costs
    latency on the critical path of message handling and changes the loop's
    observable ordering -- ``tests/test_driver.py`` has cancellation cases that turn
    on nothing suspending there, and awaiting the tag inline breaks one of them.
    """
    order: list[str] = []

    class _Watching(_FakeClient):
        async def receive_messages(self):
            async for message in super().receive_messages():
                order.append("message")
                yield message

    monkeypatch.setattr("pptmstr.driver.tag_session", lambda *a: order.append("tag"))
    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _Watching([result_frame(RESUMED), result_frame(RESUMED)]),
    )
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    asyncio.run(session.run())

    # Both messages were handled before the background write got a turn. Awaited
    # inline, the tag would land between them.
    assert order == ["message", "message", "tag"]


def test_a_failing_tag_never_ends_the_session(monkeypatch) -> None:
    """
    The tag buys a picker the ability to say which sessions it recognises. It is never
    a filter -- an untagged session is still listed and still resumable -- so nothing
    about it is worth ending a session over.
    """

    def always_fails(session_id: str, tag: str, directory: str | None) -> None:
        raise FileNotFoundError("never written")

    monkeypatch.setattr("pptmstr.driver.tag_session", always_fails)
    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient([init_frame(RESUMED), result_frame(RESUMED)]),
    )
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    asyncio.run(session.run())
    emitted = list(session.bridge.drain())

    assert not session._tagged
    (finished,) = [i for i in emitted if isinstance(i, AgentFinished)]
    assert finished.state is AgentState.DONE


def test_a_session_that_finishes_no_turn_is_untagged_and_leaves_no_task(monkeypatch) -> None:
    """
    Accepted, and stated so the next reader does not read it as a defect. It also
    leaves nothing pending: a detached task outliving its loop is destroyed with a
    warning and a half-written append.
    """
    calls: list[object] = []
    monkeypatch.setattr("pptmstr.driver.tag_session", lambda *a: calls.append(a))
    monkeypatch.setattr(
        "pptmstr.driver.ClaudeSDKClient",
        lambda **_: _FakeClient([init_frame(RESUMED)]),
    )
    session = AgentSession(Bridge(), task="carry on", resume=RESUMED)
    asyncio.run(session.run())

    assert calls == []
    assert session._tag_task is None


# -- the spec field ------------------------------------------------------------


def test_the_launch_spec_carries_resume_and_defaults_to_fresh() -> None:
    assert LaunchSpec(task="t", model="m").resume is None
    assert LaunchSpec(task="t", model="m", resume=RESUMED).resume == RESUMED


def test_relaunching_a_record_does_not_resume_it() -> None:
    """
    A relaunch and a fork both want a new conversation from the same premises, which
    is the opposite of continuing one. ``from_record`` must not start setting this.
    """
    from pptmstr.model import AgentRecord

    record = AgentRecord(
        node_id=(RESUMED, None),
        parent=None,
        depth=0,
        state=AgentState.DONE,
        topic="",
        task="the original task",
        model="claude-sonnet-5",
    )
    assert LaunchSpec.from_record(record).resume is None


@pytest.mark.parametrize("resume", [None, RESUMED])
def test_the_session_takes_the_field_the_spec_carries(resume: str | None) -> None:
    """
    The field and the argument are the same name on purpose: a launcher wiring
    ``resume=spec.resume`` is the only wiring that can be written.
    """
    session = AgentSession(Bridge(), task="t", resume=resume)
    assert session.resume == resume
