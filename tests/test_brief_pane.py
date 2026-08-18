"""
BRIEF: the pane's labels and the memo that keeps directory IO off the frame path.

Drawing is exercised with ``imgui`` replaced by a ``MagicMock``, like the other
panes. What needs a real filesystem gets one, because the memo's whole job is to
notice a directory changing.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from unittest.mock import MagicMock

from pptmstr import brief
from pptmstr.model import AgentRecord, AgentState, NodeId, Snapshot
from pptmstr.ui import brief_pane
from pptmstr.ui.focus import FocusState, Scope

ROOT: NodeId = ("s1", None)


def record(*, brief_path: str | None) -> AgentRecord:
    return AgentRecord(
        node_id=ROOT,
        parent=None,
        depth=0,
        state=AgentState.AWAITING_APPROVAL,
        topic="working",
        task="the operator's whole prompt",
        model="claude-sonnet-5",
        cwd="/x/orbital",
        template="feature",
        brief=brief_path,
    )


def snapshot(*, brief_path: str | None) -> Snapshot:
    return Snapshot(
        seq=1,
        nodes=MappingProxyType({ROOT: record(brief_path=brief_path)}),
        order=(ROOT,),
        needs_you=(),
        any_active=False,
    )


def at_root(snap: Snapshot) -> FocusState:
    focus = FocusState()
    focus.to_node(snap, ROOT, scope=Scope.SESSION)
    return focus


def drawn(monkeypatch, snap: Snapshot, pane: brief_pane.BriefState) -> list[str]:
    fake = MagicMock()
    # imgui.checkbox returns (changed, value); a bare MagicMock does not unpack.
    fake.checkbox.side_effect = lambda *a, **k: (False, False)
    monkeypatch.setattr(brief_pane, "imgui", fake)
    monkeypatch.setattr(brief_pane, "section", MagicMock())
    monkeypatch.setattr(brief_pane, "multiline_input", lambda *a, **k: (False, pane.draft))
    brief_pane.draw(snap, at_root(snap), pane)
    coloured = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]
    disabled = [str(a) for call in fake.text_disabled.call_args_list for a in call.args]
    return coloured + disabled


def derived(tmp_path: Path) -> tuple[brief.DerivedEntry, ...]:
    return brief.derive(brief.read_entries(tmp_path))


# -- absence ----------------------------------------------------------------------


def test_a_session_without_a_brief_says_so(monkeypatch) -> None:
    lines = drawn(monkeypatch, snapshot(brief_path=None), brief_pane.BriefState())
    assert any("launched without a brief" in line for line in lines)


def test_an_empty_brief_directory_invites_the_first_entry(monkeypatch, tmp_path: Path) -> None:
    """
    A brief that exists and is empty is a different state from no brief at all: one
    is an operator who has not written yet, the other is a session that has nowhere
    to write to.
    """
    tmp_path.mkdir(exist_ok=True)
    lines = drawn(monkeypatch, snapshot(brief_path=str(tmp_path)), brief_pane.BriefState())
    assert any("no premises written yet" in line for line in lines)


def test_the_pane_names_the_directory_a_worker_is_told_to_read(monkeypatch, tmp_path: Path) -> None:
    """The operator has to be able to check the two are the same place."""
    lines = drawn(monkeypatch, snapshot(brief_path=str(tmp_path)), brief_pane.BriefState())
    assert any(str(tmp_path) in line for line in lines)


# -- the summary ------------------------------------------------------------------


def test_an_empty_brief_summarises_as_empty() -> None:
    assert brief_pane.summary_label(()) == "no entries"


def test_a_brief_with_nothing_overturned_counts_only_entries(tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "a")
    brief.write_entry(tmp_path, "b")

    assert brief_pane.summary_label(derived(tmp_path)) == "2 entr(ies)"


def test_a_brief_with_supersessions_says_how_much_still_stands(tmp_path: Path) -> None:
    """
    Two different questions an operator arrives with: how much has been said, and
    how much of it is still true.
    """
    brief.write_entry(tmp_path, "a")
    brief.write_entry(tmp_path, "b", supersedes=(0,))

    label = brief_pane.summary_label(derived(tmp_path))
    assert label == "2 entr(ies) · 1 standing · 1 superseded"


# -- entry headings ---------------------------------------------------------------


def test_a_standing_entry_reads_as_itself(tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "the parser is fixed-width")
    (item,) = derived(tmp_path)

    assert brief_pane.entry_label(item) == "000 premises"


def test_an_overturned_entry_says_what_overturned_it(tmp_path: Path) -> None:
    """
    Obligation 1, in the one place a reader looks. Dropping the entry would make the
    change invisible in the log that exists to record it.
    """
    brief.write_entry(tmp_path, "the board is per-session")
    brief.write_entry(tmp_path, "that was wrong", supersedes=(0,))
    first, second = derived(tmp_path)

    assert brief_pane.entry_label(first) == "000 premises -- superseded by 001"
    assert brief_pane.entry_label(second) == "001 amendment · overturns 000"


def test_the_overturned_entry_is_still_rendered(monkeypatch, tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "the board is per-session")
    brief.write_entry(tmp_path, "that was wrong", supersedes=(0,))

    lines = drawn(monkeypatch, snapshot(brief_path=str(tmp_path)), brief_pane.BriefState())

    assert any("the board is per-session" in line for line in lines)
    assert any("that was wrong" in line for line in lines)


# -- the composer -----------------------------------------------------------------


def test_the_hint_says_nothing_is_overturned_by_default() -> None:
    assert "Nothing is overturned" in brief_pane.compose_hint(set())


def test_the_hint_names_the_ordinals_rather_than_counting_them() -> None:
    """
    "supersedes 2 entries" is a sentence the operator cannot check against the boxes
    they ticked.
    """
    assert brief_pane.compose_hint({0, 2}) == "Ctrl+Enter appends, overturning 000, 002."


def test_appending_writes_an_entry_and_clears_the_draft(tmp_path: Path) -> None:
    pane = brief_pane.BriefState(draft="the parser is fixed-width")
    brief_pane._append(pane, tmp_path)

    (entry,) = brief.read_entries(tmp_path)
    assert entry.body == "the parser is fixed-width"
    assert (pane.draft, pane.supersedes, pane.error) == ("", set(), None)


def test_appending_carries_the_ticked_supersessions(tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "first")
    pane = brief_pane.BriefState(draft="second", supersedes={0})

    brief_pane._append(pane, tmp_path)

    assert brief.read_entries(tmp_path)[1].supersedes == (0,)


def test_a_failed_append_keeps_the_draft_and_says_so(tmp_path: Path) -> None:
    """
    A premise the operator believes landed and did not is worse than no premise: a
    worker acts on its absence while the operator acts on its presence.
    """
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory", encoding="utf-8")
    pane = brief_pane.BriefState(draft="keep me")

    brief_pane._append(pane, blocked)

    assert pane.draft == "keep me"
    assert pane.error is not None


# -- the memo ---------------------------------------------------------------------


def test_the_directory_is_not_re_read_when_nothing_changed(monkeypatch, tmp_path: Path) -> None:
    """
    The frame loop's premise is that building a frame touches no IO. Re-reading per
    frame would put an iterdir and N read_text calls on the frame path at 60fps.
    """
    brief.write_entry(tmp_path, "a")
    pane = brief_pane.BriefState()
    pane.entries(tmp_path)

    calls: list[Path] = []
    monkeypatch.setattr(brief, "read_entries", lambda d: calls.append(d) or ())
    pane.entries(tmp_path)
    pane.entries(tmp_path)

    assert calls == []


def test_a_new_entry_is_picked_up(tmp_path: Path) -> None:
    pane = brief_pane.BriefState()
    brief.write_entry(tmp_path, "a")
    assert len(pane.entries(tmp_path)) == 1

    pane.draft = "b"
    brief_pane._append(pane, tmp_path)

    assert len(pane.entries(tmp_path)) == 2


def test_a_directory_that_disappears_reads_as_empty(tmp_path: Path) -> None:
    pane = brief_pane.BriefState()
    assert pane.entries(tmp_path / "gone") == ()


def test_an_entry_written_by_anything_else_is_picked_up(tmp_path: Path) -> None:
    """
    The memo is keyed on the directory's mtime rather than on a flag this pane sets,
    so a write it did not make still invalidates it. A key only this pane could
    clear would go stale the moment anything else appended.
    """
    pane = brief_pane.BriefState()
    brief.write_entry(tmp_path, "a")
    assert len(pane.entries(tmp_path)) == 1

    brief.write_entry(tmp_path, "b")

    assert len(pane.entries(tmp_path)) == 2
