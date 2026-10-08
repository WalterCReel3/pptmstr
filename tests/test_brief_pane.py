"""
BRIEF: the pane's labels and the memo that keeps directory IO off the frame path.

Drawing is exercised with ``imgui`` replaced by a ``MagicMock``, like the other
panes. What needs a real filesystem gets one, because the memo's whole job is to
notice a directory changing.

The typeset path is drawn in real ImGui frames with no window, on the rig
``tests/test_typeset.py`` owns: what a view set is read back from the view.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock

import pytest

from pptmstr import brief
from pptmstr.model import AgentRecord, AgentState, NodeId, Snapshot
from pptmstr.ui import brief_pane, typeset
from pptmstr.ui.focus import FocusState, Scope
from tests import test_typeset as rig

# The rig's fixtures, under the names pytest finds them by.
imgui = rig.imgui
seam = rig.seam

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
    return drawn_with(monkeypatch, snap, pane)[2]


def drawn_with(
    monkeypatch, snap: Snapshot, pane: brief_pane.BriefState, *, seam_on: bool = False
) -> tuple[Any, Any, list[str]]:
    """
    The ``imgui`` double the pane drew into, the seam's double, and the text handed
    to the first.

    The seam is a double either way: the real one draws into the real ImGui, and
    whether it is enabled is whatever the last test that loaded its faces left.
    """
    fake = MagicMock()
    seam_double = MagicMock()
    seam_double.enabled.return_value = seam_on
    monkeypatch.setattr(brief_pane, "typeset", seam_double)
    # imgui.checkbox returns (changed, value); a bare MagicMock does not unpack.
    fake.checkbox.side_effect = lambda *a, **k: (False, False)
    monkeypatch.setattr(brief_pane, "imgui", fake)
    monkeypatch.setattr(brief_pane, "section", MagicMock())
    monkeypatch.setattr(brief_pane, "multiline_input", lambda *a, **k: (False, pane.draft))
    brief_pane.draw(snap, at_root(snap), pane)
    coloured = [str(a) for call in fake.text_colored.call_args_list for a in call.args[1:]]
    disabled = [str(a) for call in fake.text_disabled.call_args_list for a in call.args]
    return fake, seam_double, coloured + disabled


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


# -- how a body is read -------------------------------------------------------------


def test_a_body_is_read_by_the_seams_rule_and_no_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    The wiring. Whether a body is markdown is ``typeset.prose_parts``' to say, so that
    an entry reads here as a concern or a task detail quoting it reads elsewhere.
    """
    seen: list[tuple[str, object]] = []
    answer = (typeset.Part("prose"), typeset.Part("  as written", typeset.Kind.LITERAL))

    def prose_parts(text: str, *, colour: object = None) -> tuple[typeset.Part, ...]:
        seen.append((text, colour))
        return answer

    monkeypatch.setattr(typeset, "prose_parts", prose_parts)
    brief.write_entry(tmp_path, "apples\npears\nplums")
    (item,) = brief.derive(brief.read_entries(tmp_path))

    head, *body = brief_pane.entry_parts(item)

    assert seen == [(item.entry.body, None)]
    assert head.text.lstrip("#") == " 000 premises"
    assert [(part.text, part.kind) for part in body] == [(p.text, p.kind) for p in answer]


def test_an_overturned_entry_asks_the_rule_for_dim_parts(tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "ran: make test\nexit: 1\n\nTraceback:\n  File x\n    y()")
    brief.write_entry(tmp_path, "that was wrong", supersedes=(0,))
    old, _ = brief.derive(brief.read_entries(tmp_path))

    parts = brief_pane.entry_parts(old)

    assert [part.kind for part in parts[1:]] == [typeset.Kind.MARKDOWN, typeset.Kind.LITERAL]
    assert {part.colour for part in parts} == {"text_dim"}


def test_a_heading_in_a_body_is_set_under_the_entrys_own(seam: Any, tmp_path: Path) -> None:
    """
    A body that opens with ``# Title`` is not the start of the entry. Its title is a
    size under the line that says which entry this is, and nothing in it is over.
    """
    brief.write_entry(tmp_path, "# Title\n\n## Section\n\nwords\n")
    pane = brief_pane.BriefState()

    typeset_frame(seam, tmp_path, pane)

    view = pane.views.view(str(tmp_path / "000-premises.md"))
    texts = [view.block_text(n) for n in range(len(view.document.blocks))]
    assert texts == ["000 premises", "Title", "Section", "words"]
    entry, title, section, words = (block.style.font.size for block in view.document.blocks)
    assert entry > title > section == words


# -- the typeset path -------------------------------------------------------------


def typeset_frame(im: Any, directory: Path, pane: brief_pane.BriefState) -> None:
    snap = snapshot(brief_path=str(directory))
    rig.frame(im, lambda: brief_pane.draw(snap, at_root(snap), pane))


def shown(pane: brief_pane.BriefState, directory: Path, name: str) -> list[str]:
    view = pane.views.view(str(directory / name))
    return [view.block_text(n) for n in range(len(view.document.blocks))]


def test_with_the_seam_off_the_seam_is_not_drawn_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brief.write_entry(tmp_path, "the board is per-session")
    pane = brief_pane.BriefState()

    _, seam_double, lines = drawn_with(monkeypatch, snapshot(brief_path=str(tmp_path)), pane)

    assert "000 premises" in lines and "the board is per-session" in lines
    seam_double.document.assert_not_called()


def test_an_entry_is_set_as_its_heading_and_its_markdown_body(seam: Any, tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "# Title\n\nA **bold** claim.\n\n- one\n- two")
    pane = brief_pane.BriefState()

    typeset_frame(seam, tmp_path, pane)

    assert shown(pane, tmp_path, "000-premises.md") == [
        "000 premises",
        "Title",
        "A bold claim.",
        "• one",
        "• two",
    ]
    glyphs, _ = pane.views.drawn(str(tmp_path / "000-premises.md")) or (0, 0)
    assert glyphs >= len("000premisesTitleAboldclaim.onetwo")


def test_a_body_of_one_item_a_line_is_drawn_on_as_many_lines(seam: Any, tmp_path: Path) -> None:
    """
    The lines are in what the view set, not only in the parts the rule returned.
    """
    brief.write_entry(tmp_path, "apples\npears\nplums")
    pane = brief_pane.BriefState()

    typeset_frame(seam, tmp_path, pane)

    assert shown(pane, tmp_path, "000-premises.md")[1] == "apples\npears\nplums"


def test_an_overturned_entry_is_said_to_be_in_words_and_dimmed(seam: Any, tmp_path: Path) -> None:
    """
    Obligation 1 under the seam. The words are the channel; a dim ink with no words
    is a difference in contrast only, which not every reader or display carries.
    """
    brief.write_entry(tmp_path, "the board is per-session")
    brief.write_entry(tmp_path, "that was wrong", supersedes=(0,))
    pane = brief_pane.BriefState()

    typeset_frame(seam, tmp_path, pane)

    assert shown(pane, tmp_path, "000-premises.md") == [
        "000 premises -- superseded by 001",
        "the board is per-session",
    ]
    old, new = pane.entries(tmp_path)
    assert [part.colour for part in pane.parts(old)] == ["text_dim", "text_dim"]
    assert [part.colour for part in pane.parts(new)] == ["accent", None]


def test_the_parts_are_the_same_object_from_frame_to_frame(seam: Any, tmp_path: Path) -> None:
    """
    The seam compares nothing for a sequence it was handed last frame, and compares
    every part's text for one it was not.
    """
    brief.write_entry(tmp_path, "a")
    pane = brief_pane.BriefState()
    typeset_frame(seam, tmp_path, pane)
    (item,) = pane.entries(tmp_path)
    held = pane.parts(item)
    view = pane.views.view(str(item.entry.path))
    layout = view.layout

    typeset_frame(seam, tmp_path, pane)

    assert pane.parts(pane.entries(tmp_path)[0]) is held
    assert pane.views.view(str(item.entry.path)) is view and view.layout is layout


def test_an_entry_overturned_later_keeps_its_view(seam: Any, tmp_path: Path) -> None:
    """
    Being overturned changes an entry's heading and its ink, and not which view it
    is: the operator's scroll and selection survive the append they just made.
    """
    brief.write_entry(tmp_path, "the board is per-session")
    pane = brief_pane.BriefState()
    typeset_frame(seam, tmp_path, pane)
    view = pane.views.view(str(tmp_path / "000-premises.md"))

    pane.draft, pane.supersedes = "that was wrong", {0}
    brief_pane._append(pane, tmp_path)
    typeset_frame(seam, tmp_path, pane)

    assert pane.views.view(str(tmp_path / "000-premises.md")) is view
    assert shown(pane, tmp_path, "000-premises.md")[0] == "000 premises -- superseded by 001"
    assert len(pane.views) == 2


def test_views_of_another_directory_are_let_go(seam: Any, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    brief.write_entry(first, "a")
    brief.write_entry(first, "b")
    brief.write_entry(second, "c")
    pane = brief_pane.BriefState()
    typeset_frame(seam, first, pane)
    assert len(pane.views) == 2

    typeset_frame(seam, second, pane)

    assert pane.views.keys() == (str(second / "000-premises.md"),)


def test_views_are_let_go_when_the_directory_disappears(seam: Any, tmp_path: Path) -> None:
    brief.write_entry(tmp_path / "one", "a")
    pane = brief_pane.BriefState()
    typeset_frame(seam, tmp_path / "one", pane)

    pane.entries(tmp_path / "gone")

    assert len(pane.views) == 0


def test_two_files_claiming_one_ordinal_are_two_views(seam: Any, tmp_path: Path) -> None:
    """
    ``write_entry`` never makes this and a hand can. Keyed by ordinal the second
    would be the same key drawn twice in a frame, which the seam refuses.
    """
    (tmp_path / "000-premises.md").write_text("mine\n", encoding="utf-8")
    (tmp_path / "000-also.md").write_text("theirs\n", encoding="utf-8")
    pane = brief_pane.BriefState()

    typeset_frame(seam, tmp_path, pane)

    assert shown(pane, tmp_path, "000-premises.md") == ["000 premises", "mine"]
    assert shown(pane, tmp_path, "000-also.md") == ["000 also", "theirs"]


def test_a_clipped_body_is_clipped_in_the_view_too(seam: Any, tmp_path: Path) -> None:
    brief.write_entry(tmp_path, "x" * (brief_pane._MAX_BODY_CHARS + 7))
    pane = brief_pane.BriefState()

    typeset_frame(seam, tmp_path, pane)

    assert shown(pane, tmp_path, "000-premises.md")[1] == "x" * brief_pane._MAX_BODY_CHARS


def test_the_child_scrolls_sideways_only_under_the_seam(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    Under the seam a fenced line is never broken, so one longer than the pane is
    reachable only if the child scrolls to it; native text in that child is then
    wrapped at a stated position, not at "the window's end", which has moved. With
    the seam off both are what they always were.
    """
    brief.write_entry(tmp_path, "a")
    snap = snapshot(brief_path=str(tmp_path))

    off, _, _ = drawn_with(monkeypatch, snap, brief_pane.BriefState())
    assert off.begin_child.call_args.kwargs == {"window_flags": 0}
    off.push_text_wrap_pos.assert_called_with(0.0)

    on, _, _ = drawn_with(monkeypatch, snap, brief_pane.BriefState(), seam_on=True)
    assert on.begin_child.call_args.kwargs == {
        "window_flags": on.WindowFlags_.horizontal_scrollbar.value
    }
    assert on.push_text_wrap_pos.call_args.args != (0.0,)


def test_the_bound_is_announced_on_the_typeset_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    brief.write_entry(tmp_path, "x" * (brief_pane._MAX_BODY_CHARS + 7))
    pane = brief_pane.BriefState()

    _, seam_double, lines = drawn_with(
        monkeypatch, snapshot(brief_path=str(tmp_path)), pane, seam_on=True
    )

    assert "... 7 more character(s) not shown" in lines
    assert "x" * brief_pane._MAX_BODY_CHARS not in lines
    seam_double.document.assert_called_once()
