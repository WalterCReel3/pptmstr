"""
What spend each surface draws, and what it says the figure covers.

The findings are in ``notes/2026-10-17-why-a-workers-card-reads-zero-dollars.md``.
No message carries dollars per agent: ``ResultMessage.total_cost_usd`` is the whole
query pipeline's, sub-agents included, and the driver banks it on the root. Tokens
are banked per agent. So a sub-agent's dollar field is a default and never drawn as
a reading, the root's dollars are drawn as the session's, and where tokens sit
beside those dollars they are the session's tokens too.

The first half pins what the SDK and the driver deliver; it describes the data the
display is built on and is unchanged by the display. The second half is the
display.

No CLI is started. The CLI's meaning of ``total_cost_usd`` is a read of the bundled
CLI's schema text and is in the note, not here.
"""

from __future__ import annotations

import dataclasses
from typing import get_type_hints

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk.types import TaskUsage

from pptmstr.driver import Translator
from pptmstr.intents import AgentFinished, ApprovalRequested, SubagentProgress, UsageAccrued
from pptmstr.model import AgentState, NodeId, PendingApproval, UsageRollup
from pptmstr.store import Store
from pptmstr.transcript import Transcript
from pptmstr.ui import health, inbox, rail, session_details
from tests.test_health import _drawn
from tests.test_rail import LEAD, A, B, Frame, opened, team

TOOL_USE = "toolu_agent"


def _result(cost: float) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id=LEAD[0],
        total_cost_usd=cost,
    )


def _worked(store: Store, tr: Translator) -> None:
    """
    A turn in which the worker spends tokens and the root's turn then ends at
    $0.40 -- the shape of every team turn, as far as dollars go.
    """
    store.apply_all(
        tr.handle(
            AssistantMessage(
                content=[TextBlock(text="working")],
                model="m",
                parent_tool_use_id=TOOL_USE,
                usage={"input_tokens": 1200, "output_tokens": 300},
            )
        )
    )
    store.apply_all(tr.handle(_result(0.40)))


def _translator() -> Translator:
    tr = Translator(LEAD, Transcript())
    tr.subagent_by_tool_use = {TOOL_USE: A}
    return tr


def _worked_team() -> Store:
    store = team()
    _worked(store, _translator())
    return store


# -- 1. what the SDK carries ---------------------------------------------------------


def test_the_only_dollar_fields_the_sdk_parses_are_on_the_result() -> None:
    """
    The data gap. Of the message types a sub-agent's activity arrives as, the
    assistant message carries a ``usage`` dict of tokens, the task events carry a
    ``TaskUsage`` of three counts, and only ``ResultMessage`` names dollars -- one
    session-wide figure and a per-*model* breakdown, neither keyed by agent. An SDK
    that grew a per-agent cost field would fail this, and the card's tokens-only
    reading for a sub-agent would be worth revisiting.
    """
    result_fields = {f.name for f in dataclasses.fields(ResultMessage)}
    assert {"total_cost_usd", "model_usage"} <= result_fields
    assert not any("agent" in name for name in result_fields)

    assistant_fields = {f.name for f in dataclasses.fields(AssistantMessage)}
    assert not any("cost" in name for name in assistant_fields)

    assert set(get_type_hints(TaskUsage)) == {"total_tokens", "tool_uses", "duration_ms"}


def test_the_sdk_drops_the_results_subagent_stats() -> None:
    """
    The bundled CLI's result schema has ``subagent_stats`` (spawn and outcome
    counts, no dollars). The SDK's parser builds ``ResultMessage`` field by field
    and does not carry it, so even the counts never reach pptmstr.
    """
    msg = parse_message(
        {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1,
            "duration_api_ms": 1,
            "is_error": False,
            "num_turns": 1,
            "session_id": "s",
            "total_cost_usd": 0.4,
            "subagent_stats": {"spawned": 2, "completed": 2},
        }
    )
    assert isinstance(msg, ResultMessage)
    assert not hasattr(msg, "subagent_stats")


# -- 2. what the driver banks ----------------------------------------------------------


def test_a_task_progress_token_count_is_read_for_its_description_only() -> None:
    """
    ``task_progress`` carries a worker's running ``total_tokens``. The translator
    takes its description for the card's topic and nothing else, so a worker whose
    assistant messages did not reach the stream counts zero tokens on its card.
    """
    tr = _translator()
    out = tr.handle(
        SystemMessage(
            subtype="task_progress",
            data={
                "tool_use_id": TOOL_USE,
                "description": "reading files",
                "usage": {"total_tokens": 9000, "tool_uses": 3, "duration_ms": 40},
            },
        )
    )
    assert out == [SubagentProgress(A, "reading files")]


def test_the_roots_tokens_are_its_own_and_its_dollars_are_the_results() -> None:
    """
    In the store, the root's record holds the session's dollars beside only its own
    tokens; the worker's tokens are on the worker's record and its dollars are a
    zero nothing reported. The surfaces below are built on exactly this.
    """
    snap = _worked_team().snapshot()
    assert snap.nodes[LEAD].usage.total_cost_usd == pytest.approx(0.40)
    assert snap.nodes[LEAD].usage.input_tokens == 0
    assert snap.nodes[LEAD].usage.output_tokens == 0
    assert snap.nodes[A].usage.output_tokens == 300
    assert snap.nodes[A].usage.total_cost_usd == 0.0


def test_a_fresh_translator_puts_the_whole_reported_total_on_the_root() -> None:
    """
    One ``Translator`` per ``run()``, starting from ``_last_cost = 0``. The CLI's
    schema says a resumed session's first result already carries the turns before
    it, so a resumed root's figure includes spend from runs this process did not
    see. Pinned on the translator half only; the fix waits on a live measurement.
    """
    tr = Translator(LEAD, Transcript())
    out = tr.handle(_result(1.25))
    assert out == [UsageAccrued(LEAD, UsageRollup(total_cost_usd=1.25))]


def test_a_running_total_that_falls_is_not_counted_until_it_passes_its_old_high() -> None:
    """
    ``_result`` keeps the highest total seen. The CLI resets its running total on
    a mid-session ``/clear``; spend after that is not banked until the new total
    exceeds the old one. pptmstr sends no ``/clear``, so this is a latent
    undercount, not a live one.
    """
    tr = Translator(LEAD, Transcript())
    banked = [
        sum(i.delta.total_cost_usd for i in tr.handle(_result(c)) if isinstance(i, UsageAccrued))
        for c in (0.40, 0.10, 0.45)
    ]
    assert banked == [pytest.approx(0.40), 0.0, pytest.approx(0.05)]


# -- 3. where it is drawn ------------------------------------------------------------


def _owes(store: Store, node: NodeId) -> None:
    store.apply(
        ApprovalRequested(
            node,
            PendingApproval(
                id=f"p-{node[1]}",
                node=node,
                tool_name="Write",
                tool_use_id="tu",
                raw_args={},
                summary="Write x",
                requested_at=6.0,
            ),
        )
    )


@pytest.mark.parametrize(
    "shape",
    ["active", "worker-ended", "all-ended", "worker-blocked"],
)
def test_no_card_draws_dollars_but_the_roots_and_it_says_they_are_the_sessions(
    monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    """
    Every density a card has draws a spend, so every one is reached. The worker
    that spent 1,500 tokens draws them; the two that spent none draw a count of
    zero, not a dollar figure; the lead draws the turn's whole figure as the
    session's. The only "$" anywhere on the rail is the lead's.
    """
    store = _worked_team()
    if shape in ("worker-ended", "all-ended"):
        store.apply(AgentFinished(A, AgentState.DONE, ended_at=5.0))
    if shape == "all-ended":
        store.apply(AgentFinished(B, AgentState.DONE, ended_at=5.0))
        store.apply(AgentFinished(LEAD, AgentState.DONE, ended_at=6.0))
    if shape == "worker-blocked":
        _owes(store, A)
    snap = store.snapshot()
    densities = {rail._density(snap.nodes[A], list(snap.needs_you))}
    assert (
        densities
        == {
            "active": {"active"},
            "worker-ended": {"ended"},
            "all-ended": {"ended"},
            "worker-blocked": {"blocked"},
        }[shape]
    )

    drawn = Frame(monkeypatch, snap, state=opened()).text

    assert [s for s in drawn if "$" in s] == ["session $0.40"]
    assert drawn.count("1.5k tok") == 1
    assert drawn.count("0 tok") == 2


def test_a_cards_spend_reads_the_record_it_is_given() -> None:
    """
    The slot's text, unit by unit: a sub-agent's input and output tokens and never
    its cache reads, which would swamp them; a root's dollars, named as the
    session's.
    """
    usage = UsageRollup(
        input_tokens=2_000_000,
        output_tokens=500_000,
        cache_read_input_tokens=9_000_000,
        total_cost_usd=7.5,
    )
    snap = _worked_team().snapshot()
    worker = dataclasses.replace(snap.nodes[A], usage=usage)
    root = dataclasses.replace(snap.nodes[LEAD], usage=usage)
    assert rail.card_spend(worker) == "2.5M tok"
    assert rail.card_spend(root) == "session $7.50"
    assert rail.card_spend(dataclasses.replace(worker, usage=UsageRollup())) == "0 tok"


def test_details_says_a_worker_has_no_dollars_of_its_own() -> None:
    """
    A worker's Details holds no cost: the CLI reports none per agent, and a stated
    "$0.00 estimated by the SDK" claims an estimate the SDK never made. Its tokens
    beside the unknown are real, and say they are its own.
    """
    snap = _worked_team().snapshot()
    shown = session_details.view(
        snap, A, held=None, looked=None, logging=None, now=10.0, wall_now=10.0
    )
    assert shown is not None
    cost = shown.row("cost")
    tokens = shown.row("tokens")
    assert cost is not None and tokens is not None
    assert cost.known is False
    assert cost.value == session_details.NOT_KNOWN
    assert session_details.NOT_PER_AGENT in cost.note
    assert "$" not in cost.value + cost.note
    assert tokens.value.startswith("1,200 in, 300 out")
    assert "its own" in tokens.note


def test_details_gives_a_session_its_dollars_and_its_tokens_on_one_footing() -> None:
    """
    The root's Details says the dollars are the whole session's, and the tokens
    row beside them sums every agent's, so the two count the same calls. The
    root's own share is in that row's note.
    """
    snap = _worked_team().snapshot()
    shown = session_details.view(
        snap, LEAD, held=None, looked=None, logging=None, now=10.0, wall_now=10.0
    )
    assert shown is not None
    cost, tokens = shown.row("cost"), shown.row("tokens")
    assert cost is not None and tokens is not None
    assert (cost.value, cost.note) == ("$0.40", session_details.SESSION_COST)
    assert tokens.value.startswith("1,200 in, 300 out")
    assert "3 sub-agents included" in tokens.note
    assert tokens.note.endswith("its own 0 in, 0 out")


def test_a_session_without_sub_agents_has_one_tokens_row() -> None:
    """The control: with nothing to sum, the session's tokens are the root's."""
    store = Store()
    from tests.test_rail import launch

    store.apply(launch(LEAD))
    store.apply(UsageAccrued(LEAD, UsageRollup(input_tokens=10, total_cost_usd=0.1)))
    shown = session_details.view(
        store.snapshot(), LEAD, held=None, looked=None, logging=None, now=10.0, wall_now=10.0
    )
    assert shown is not None
    tokens = shown.row("tokens")
    assert tokens is not None and tokens.value.startswith("10 in") and tokens.note == ""


def test_session_usage_takes_dollars_from_the_root_alone() -> None:
    """
    The sum adds tokens across the session and takes the dollars only from the
    root. A sub-agent record carrying dollars, which the driver never writes,
    would otherwise be counted on top of a figure that already includes it.
    """
    snap = _worked_team().snapshot()
    nodes = dict(snap.nodes)
    nodes[A] = dataclasses.replace(
        nodes[A], usage=nodes[A].usage.plus(UsageRollup(total_cost_usd=9.0))
    )
    snap = dataclasses.replace(snap, nodes=nodes)
    usage = session_details.session_usage(snap, snap.nodes[LEAD])
    assert usage.total_cost_usd == pytest.approx(0.40)
    assert (usage.input_tokens, usage.output_tokens) == (1200, 300)


@pytest.mark.parametrize("node", [LEAD, A], ids=["the lead", "a worker"])
def test_health_sets_session_dollars_beside_session_tokens(
    monkeypatch: pytest.MonkeyPatch, node: NodeId
) -> None:
    """
    HEALTH is the session's whichever agent is selected. Its dollar line says the
    figure is the session's, and the token line under it is the session's too: the
    worker's 1,200 in and 300 out, and not the lead's zero.
    """
    said, _ = _drawn(monkeypatch, _worked_team().snapshot(), node)
    assert "$0.40" in said.strings
    assert health.SESSION_SPEND in said.strings
    assert "1k in · 300 out · 0 cached" in said.strings
    assert health.SESSION_TOKENS in said.strings


def test_run_total_is_the_roots_figure_because_workers_add_nothing() -> None:
    """
    ``inbox.run_total`` sums every record's dollars. That is the sum of the roots'
    because a worker's are a zero nothing reported; anything that put a worker's
    share on its record while the root kept the session-wide total would make this
    line count the worker twice.
    """
    assert inbox.run_total(_worked_team().snapshot()).endswith("· $0.40")
