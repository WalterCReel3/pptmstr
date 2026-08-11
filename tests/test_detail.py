"""
The pure decisions behind the DETAIL pane.

Only the parts checkable without a GL context: how an argument value is turned
into text, that a bound reports what it dropped instead of swallowing it, and that
the clipboard rendering carries the whole call rather than the row's clipped
summary. The drawing itself needs pixels.
"""

from __future__ import annotations

from types import MappingProxyType

from pptmstr.approval import summarize
from pptmstr.model import (
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    NodeId,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from pptmstr.transcript import SegmentKind
from pptmstr.ui import detail

ROOT: NodeId = ("s1", None)


def record(node: NodeId = ROOT, *, task: str = "audit the TLE parser") -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=None,
        depth=0,
        state=AgentState.AWAITING_APPROVAL,
        topic="working",
        task=task,
        model="claude-sonnet-5",
        cwd="/x/orbital",
    )


def snapshot(*records: AgentRecord, needs_you: tuple[object, ...] = ()) -> Snapshot:
    return Snapshot(
        seq=1,
        nodes=MappingProxyType({r.node_id: r for r in records}),
        order=tuple(r.node_id for r in records),
        needs_you=needs_you,  # type: ignore[arg-type]
        any_active=False,
    )


def approval(args: dict[str, object], *, diff: str | None = None) -> ApprovalNeeded:
    tool = "Bash" if "command" in args else "Write"
    return ApprovalNeeded(
        node=ROOT,
        since=1.0,
        summary=summarize(tool, args),
        approval=PendingApproval(
            id="p1",
            node=ROOT,
            tool_name=tool,
            tool_use_id="tu-p1",
            raw_args=args,
            summary=summarize(tool, args),
            requested_at=1.0,
            diff=diff,
        ),
    )


# -- value rendering -----------------------------------------------------------


def test_a_string_argument_keeps_its_newlines() -> None:
    """
    repr would render a 200-line Write as one line of backslash-n, which is the
    loss this pane exists to undo, reintroduced by the formatting.
    """
    assert detail.render_value("a\nb") == "a\nb"


def test_a_non_string_argument_keeps_its_quoting() -> None:
    """A nested dict of edits and the bare word None must not look alike."""
    assert detail.render_value(None) == "None"
    assert detail.render_value(["x"]) == "['x']"


# -- bounds --------------------------------------------------------------------


def test_a_bound_reports_what_it_dropped() -> None:
    text, omitted = detail.clip("abcdef", 4)
    assert text == "abcd"
    assert omitted == 2


def test_a_string_within_the_bound_is_untouched() -> None:
    assert detail.clip("abc", 4) == ("abc", 0)


# -- the clipboard rendering ---------------------------------------------------


def test_the_full_call_survives_a_summary_that_did_not() -> None:
    """
    The reason this pane is not just a wider inbox row: ``summarize`` clips to 90
    characters *before the store sees the string*, so for a Bash call no window
    width recovers the command. It has to be re-read from raw_args.
    """
    command = (
        "for f in $(git ls-files '*.py'); do python -m compileall -q \"$f\"; done  " + "# " * 40
    )
    obligation = approval({"command": command})

    assert obligation.summary.endswith("...")
    assert command not in obligation.summary

    text = detail.plain_text(snapshot(record(), needs_you=(obligation,)), obligation)
    assert command in text


def test_the_clipboard_carries_identity_and_diff() -> None:
    obligation = approval({"file_path": "/tmp/x"}, diff="--- a/x\n+++ b/x\n+hello\n")
    text = detail.plain_text(snapshot(record(), needs_you=(obligation,)), obligation)
    assert "audit the TLE parser" in text
    assert "orbital" in text
    assert "+hello" in text


def test_a_question_renders_the_turn_not_the_row_summary() -> None:
    node = record()
    node.transcript.append(SegmentKind.OUTPUT, "here is what I found, at length")
    obligation = QuestionPending(node=ROOT, since=1.0, summary="ended its turn")
    text = detail.plain_text(snapshot(node, needs_you=(obligation,)), obligation)
    assert "here is what I found, at length" in text


def test_a_failure_renders_its_error() -> None:
    obligation = SessionFailed(node=ROOT, since=1.0, summary="died", error="Traceback: boom")
    text = detail.plain_text(snapshot(record(), needs_you=(obligation,)), obligation)
    assert "Traceback: boom" in text


def test_every_obligation_kind_has_a_label() -> None:
    """
    A pane that goes blank on two of the three kinds is defect 1 in miniature --
    the habit of treating an approval as the only kind of obligation.
    """
    from pptmstr.model import ObligationKind

    assert set(detail._KIND_LABEL) == set(ObligationKind)
