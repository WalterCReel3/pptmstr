"""
The open matches in ``store/waking`` are pinned by classification.

``_peer_waking`` and ``_moved`` each match on the intent and end in an open
``case _``: their docstrings argue that most intents deliberately neither wake a
lead nor move a link, so naming every intent in the source would carry no
information. Instead, each function's intents are classified here as HANDLED
(a case names it) or IGNORED (it falls to ``case _``), and the two sets must
partition ``get_args(Intent)`` exactly. A new intent fails until it is put in
one of them, and a case added or removed fails until HANDLED says so
(``planning/2026-11-09-decisions-owed-after-round-4.md``, decision 2).

The cases are read from the source with ``ast``, never by calling the function,
so what is held is what the match names, not what one input happens to reach.
"""

from __future__ import annotations

import ast
import typing
from pathlib import Path

import pytest

from pptmstr.intents import Intent

_WAKING = Path(__file__).resolve().parents[1] / "pptmstr" / "store" / "waking.py"

_INTENTS = frozenset(member.__name__ for member in typing.get_args(Intent))

PEER_WAKING_HANDLED = frozenset(
    {
        "BoardRestored",
        "DecisionRaised",
        "DecisionWakeSent",
        "PeerInboxRead",
        "PeerSent",
        "PeerWakeSent",
        "SessionSettled",
    }
)

# Wakes come only from mail, a decision, a settle or a restore, and end only at a send or a read.
PEER_WAKING_IGNORED = frozenset(
    {
        "AgentFinished",
        "AgentRemoved",
        "AgentResumed",
        "AgentSpawned",
        "ApprovalRequested",
        "ApprovalResolved",
        "BoardRead",
        "ChannelClosed",
        "ChannelLinked",
        "ChannelsRestored",
        "CommandApproved",
        "CommandEnded",
        "CommandRefused",
        "CommandRequested",
        "CommandStopAsked",
        "CommandWithdrawn",
        "CommandsRead",
        "CommandsRestored",
        "CompactionObserved",
        "ConcernEdited",
        "ConcernPosted",
        "ConcernWithdrawn",
        "ContextPolled",
        "DecisionAnswered",
        "DecisionDismissed",
        "DecisionWithdrawn",
        "DecisionsRead",
        "DigestRead",
        "FailedSessionClosed",
        "FailureAcknowledged",
        "HeldMailDismissed",
        "InboxRead",
        "PeerQuestionAged",
        "PeerQuestionDismissed",
        "SessionDetached",
        "SessionRecalled",
        "StateChanged",
        "SubagentDelivered",
        "SubagentProgress",
        "TaskAmended",
        "TaskClaimRequested",
        "TaskClosed",
        "TaskCompleted",
        "TaskDeclared",
        "TaskReleased",
        "TaskVersionHanded",
        "TaskWithdrawn",
        "TopicChanged",
        "UsageAccrued",
        "WakeChainReleased",
    }
)

MOVED_HANDLED = frozenset(
    {
        "ApprovalResolved",
        "ConcernEdited",
        "ConcernWithdrawn",
        "DecisionAnswered",
        "DecisionDismissed",
        "WakeChainReleased",
    }
)

# "Only the operator acting on that link moves it"; "Nothing an agent does moves it".
MOVED_IGNORED = frozenset(
    {
        "AgentFinished",
        "AgentRemoved",
        "AgentResumed",
        "AgentSpawned",
        "ApprovalRequested",
        "BoardRead",
        "BoardRestored",
        "ChannelClosed",
        "ChannelLinked",
        "ChannelsRestored",
        "CommandApproved",
        "CommandEnded",
        "CommandRefused",
        "CommandRequested",
        "CommandStopAsked",
        "CommandWithdrawn",
        "CommandsRead",
        "CommandsRestored",
        "CompactionObserved",
        "ConcernPosted",
        "ContextPolled",
        "DecisionRaised",
        "DecisionWakeSent",
        "DecisionWithdrawn",
        "DecisionsRead",
        "DigestRead",
        "FailedSessionClosed",
        "FailureAcknowledged",
        "HeldMailDismissed",
        "InboxRead",
        "PeerInboxRead",
        "PeerQuestionAged",
        "PeerQuestionDismissed",
        "PeerSent",
        "PeerWakeSent",
        "SessionDetached",
        "SessionRecalled",
        "SessionSettled",
        "StateChanged",
        "SubagentDelivered",
        "SubagentProgress",
        "TaskAmended",
        "TaskClaimRequested",
        "TaskClosed",
        "TaskCompleted",
        "TaskDeclared",
        "TaskReleased",
        "TaskVersionHanded",
        "TaskWithdrawn",
        "TopicChanged",
        "UsageAccrued",
    }
)

_PINS = {
    "_peer_waking": (PEER_WAKING_HANDLED, PEER_WAKING_IGNORED),
    "_moved": (MOVED_HANDLED, MOVED_IGNORED),
}


def _intent_match(function: str) -> ast.Match:
    """
    The one ``match intent:`` in ``function``'s body in ``store/waking.py``.
    """
    tree = ast.parse(_WAKING.read_text(encoding="utf-8"), filename=str(_WAKING))
    defs = [
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function
    ]
    assert len(defs) == 1, f"{function} is not defined once at the top of {_WAKING}"
    matches = [
        node
        for node in ast.walk(defs[0])
        if isinstance(node, ast.Match)
        and isinstance(node.subject, ast.Name)
        and node.subject.id == "intent"
    ]
    assert len(matches) == 1, f"{function} has {len(matches)} `match intent:`, not one"
    return matches[0]


def _class_names(pattern: ast.pattern, function: str) -> list[str]:
    """
    The classes a case's pattern names, an OR pattern's alternatives included.
    """
    match pattern:
        case ast.MatchOr(patterns=alternatives):
            return [name for alt in alternatives for name in _class_names(alt, function)]
        case ast.MatchClass(cls=ast.Name(id=name)) | ast.MatchClass(cls=ast.Attribute(attr=name)):
            return [name]
        case _:
            raise AssertionError(
                f"{function} has a case that is not a class pattern: {ast.unparse(pattern)}"
            )


def _is_default(case: ast.match_case) -> bool:
    return (
        isinstance(case.pattern, ast.MatchAs)
        and case.pattern.pattern is None
        and case.pattern.name is None
        and case.guard is None
    )


def _handled(function: str) -> list[str]:
    """
    The intents ``function``'s match names, in source order, with the open
    default it ends in checked and left out.
    """
    cases = _intent_match(function).cases
    assert cases and _is_default(cases[-1]), f"{function}'s match no longer ends in `case _`"
    return [name for case in cases[:-1] for name in _class_names(case.pattern, function)]


@pytest.mark.parametrize("function", sorted(_PINS))
def test_cases_are_the_handled_set(function: str) -> None:
    handled, _ = _PINS[function]
    named = _handled(function)
    assert len(named) == len(set(named)), f"{function} names an intent twice: {named}"
    assert set(named) == handled, (
        f"{function}'s cases changed: added {sorted(set(named) - handled)}, "
        f"removed {sorted(handled - set(named))}; update its HANDLED and IGNORED sets"
    )


@pytest.mark.parametrize("function", sorted(_PINS))
def test_every_intent_is_classified_once(function: str) -> None:
    handled, ignored = _PINS[function]
    assert not handled & ignored, f"{function} classifies {sorted(handled & ignored)} twice"
    assert handled | ignored == _INTENTS, (
        f"{function}: unclassified {sorted(_INTENTS - (handled | ignored))}, "
        f"not an intent {sorted((handled | ignored) - _INTENTS)}"
    )
