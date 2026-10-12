"""
The diff model of a parked call, and which fences are honest diffs.

The model needs no window and no library context. ``diff()`` is tested on the
library although no pane calls it: the strict xfail under P21 is the measurement
that keeps it uncalled, and comes off with it. A fenced diff is tested as what
it is here, the code of its document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from advtext import Diff, RowKind

from pptmstr import approval
from pptmstr.model import NodeId, PendingApproval
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Role, diffs, roles
from tests.test_typeset_views import (
    _FRAGMENT,
    _GIT,
    _MAIL,
    _ONE_FILE,
    _TWO_FILES,
    BOLD,
    LONG_LABEL,
    P16,
    P21,
    _fences,
    _native_lines,
    frame,
    narrow,
    pending,
    rows,
)


def test_the_unified_string_of_an_edit_without_a_newline_loses_the_added_line(
    tmp_path: Path,
) -> None:
    """
    Why the model is built from texts. ``render_diff`` runs the removed and the added
    line together when the file has no closing newline, and a reader of that string
    sees one removed line and nothing added -- less than the change, in the place an
    operator approves from.
    """
    target = tmp_path / "a.txt"
    target.write_text("foo")
    call = pending("Edit", {"file_path": str(target), "old_string": "foo", "new_string": "bar"})
    assert call.diff is not None
    assert "-foo+bar" in call.diff.splitlines()

    lossy = Diff.from_unified(call.diff)
    assert rows(lossy, RowKind.ADD) == []
    assert rows(lossy, RowKind.REMOVE) == ["foo+bar"]

    faithful = typeset.approval_diff(call)
    assert faithful is not None
    assert rows(faithful, RowKind.REMOVE) == ["foo"]
    assert rows(faithful, RowKind.ADD) == ["bar"]


def test_a_record_with_a_unified_string_and_no_texts_is_still_shown() -> None:
    """
    Lossy, as the test above shows, and still better than telling an approver there
    is no diff for a call that has one.
    """
    old = PendingApproval(
        id="a",
        node=NodeId("n"),
        tool_name="Edit",
        tool_use_id="t",
        raw_args={},
        summary="",
        requested_at=0.0,
        diff="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n",
    )
    built = typeset.approval_diff(old)
    assert built is not None
    assert (rows(built, RowKind.REMOVE), rows(built, RowKind.ADD)) == (["a"], ["b"])
    assert [file.label for file in built.files] == [f"x  {diffs._FROM_UNIFIED}"]
    assert "may be incomplete" in diffs._FROM_UNIFIED


def test_a_text_with_no_before_is_a_new_file() -> None:
    from advtext import FileStatus

    built = typeset.diff_model((approval.DiffText(None, "a\nb\n", "/tmp/new.py"),))
    (file,) = built.files
    assert file.status is FileStatus.NEW
    assert file.label == "/tmp/new.py"
    assert rows(built, RowKind.ADD) == ["a", "b"] and built.removed == 0


def test_a_write_over_a_file_that_could_not_be_read_says_so_instead_of_calling_it_new(
    tmp_path: Path,
) -> None:
    """
    The old content cannot be shown, so everything is shown as added -- and unless
    the label says why, that is indistinguishable from a file that did not exist.
    """
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\xff\xfe\x00\x80binary")
    over = typeset.diff_model(
        approval.diff_texts("Write", {"file_path": str(target), "content": "text\n"}) or ()
    )
    assert "not readable as text" in over.files[0].label
    assert rows(over, RowKind.ADD) == ["text"]

    fresh = typeset.diff_model(
        approval.diff_texts("Write", {"file_path": str(tmp_path / "no"), "content": "t\n"}) or ()
    )
    assert "not readable" not in fresh.files[0].label


def test_an_edit_that_applies_is_numbered_as_the_file_is(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("".join(f"line {n}\n" for n in range(1, 41)))
    texts = approval.diff_texts(
        "Edit", {"file_path": str(target), "old_string": "line 30\n", "new_string": "thirty\n"}
    )
    built = typeset.diff_model(texts or ())
    (file,) = built.files
    assert file.will_not_apply == ""
    added = [row for hunk in file.hunks for row in hunk.rows if row.kind is RowKind.ADD]
    assert [(row.text, row.new_no) for row in added] == [("thirty", 30)]


def test_why_an_edit_may_not_apply_reaches_the_view_worded_as_the_prediction_it_is() -> None:
    """
    The gate's read says the edit has nowhere to land, and which way. That read was
    taken when the call parked and the file may have changed since, so the view's
    header says ``may not apply`` and not the bare reason, which reads as a fact.
    The two strings are still diffed, so the intended change is on screen beside it.
    An unreadable file under an Edit is already named by that reason: the label is
    left alone.
    """
    part = approval.DiffText(
        "old", "new", "/x.py", will_not_apply="file is not readable as text", unreadable=True
    )
    built = typeset.diff_model((part,))
    (file,) = built.files
    assert file.will_not_apply == "may not apply: file is not readable as text"
    assert file.label == "/x.py"
    assert (rows(built, RowKind.REMOVE), rows(built, RowKind.ADD)) == (["old"], ["new"])


def test_several_texts_are_one_file_of_the_diff_each() -> None:
    parts = (
        approval.DiffText("a", "A", "edit 1"),
        approval.DiffText("b\n", "B\n", "edit 3", will_not_apply="anchor not found in file"),
        approval.DiffText("c", "", "edit 4"),
    )
    built = typeset.diff_model(parts)
    assert [file.label for file in built.files] == ["edit 1", "edit 3", "edit 4"]
    assert [file.will_not_apply for file in built.files] == [
        "",
        "may not apply: anchor not found in file",
        "",
    ]
    assert rows(built, RowKind.ADD) == ["A", "B"]
    assert rows(built, RowKind.REMOVE) == ["a", "b", "c"]


def test_a_text_with_no_label_is_still_a_file_with_a_name() -> None:
    built = typeset.diff_model((approval.DiffText(None, "x\n", ""),))
    assert built.files[0].label == "(no path)"


# -- diff() on the library, which no pane calls ------------------------------------


def test_a_diff_rows_pitch_is_its_roles(seam: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The diff view's grid takes its row from the theme it is given, so the row is in
    the same table as every other pitch, and ``diff_row_height`` is what a pane
    sizes a cap in rows with.
    """
    texts = approval.diff_texts("Write", {"file_path": "/x/a.py", "content": "one\ntwo\n"})
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.diff(cache, "d", texts))
    assert cache.view("d").grid.row == 18 == typeset.diff_row_height()
    assert typeset.diff_row_height() != typeset.line_height()

    monkeypatch.setitem(typeset.LEADING, Role.DIFF, 20 / 16)
    roles.moved()
    fresh = typeset.ViewCache()
    frame(seam, lambda: typeset.diff(fresh, "d", texts))
    assert fresh.view("d").grid.row == 20 == typeset.diff_row_height()
    assert typeset.pitch(Role.CODE_BLOCK) == 17.0, "which a fenced block keeps"
    monkeypatch.undo()
    roles.moved()


def test_a_capped_diff_is_no_taller_than_its_cap_and_a_short_one_is_its_rows(seam: Any) -> None:
    row = typeset.diff_row_height()
    short = "\n".join(f"line {n}" for n in range(40))
    long = (approval.DiffText(short, short.replace("line 7", "LINE 7"), "f.py"),)
    one = (approval.DiffText("a\n", "b\n", "f.py"),)
    cache = typeset.ViewCache()
    got: dict[str, float | None] = {}

    def body() -> None:
        got["long"] = typeset.diff(cache, "long", long, height_cap=6 * row)
        got["one"] = typeset.diff(cache, "one", one, height_cap=6 * row)

    frame(seam, body)
    assert cache.view("long").row_count > 6
    assert got["long"] is not None and 5 * row <= got["long"] <= 6 * row + 8.0
    assert got["one"] is not None and got["one"] < got["long"]
    assert cache.height("long") == got["long"]


def test_a_parked_calls_diff_is_built_once_and_never_from_the_disk(
    seam: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The driver read the file when the call parked. The seam is handed that reading
    and must not take another: the file here is gone before the first frame, and
    the diff is still the one the approver was parked in front of.
    """
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\nthree\n")
    call = pending("Edit", {"file_path": str(target), "old_string": "two", "new_string": "2"})
    target.unlink()

    built: list[object] = []
    real = diffs.diff_model
    monkeypatch.setattr(diffs, "diff_model", lambda texts: (built.append(texts), real(texts))[1])
    cache = typeset.ViewCache()
    heights: list[float | None] = []
    for _ in range(3):
        frame(seam, lambda: heights.append(typeset.diff(cache, "k", call)))
    assert len(built) == 1
    assert heights[0] is not None and heights[0] > 0 and len(set(heights)) == 1
    view = cache.view("k")
    assert (view.diff.added, view.diff.removed) == (1, 1)
    assert view.diff.files[0].will_not_apply == ""
    assert cache.unbuilt("k") == 0


def test_new_texts_under_a_key_are_a_new_diff_in_the_same_view_and_equal_ones_are_not(
    seam: Any, tmp_path: Path
) -> None:
    """
    A pane showing arguments the operator is editing hands over ``diff_texts``' answer
    each time they change. Equal texts in a new tuple are the same diff: rebuilding
    would drop the expansions and the selection for nothing. New texts are a new
    model in the same view (the view is what the scroll position belongs to).
    """
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\n")
    args = {"file_path": str(target), "old_string": "two", "new_string": "2"}
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.diff(cache, "k", approval.diff_texts("Edit", args)))
    view = cache.view("k")
    model = view.diff
    frame(seam, lambda: typeset.diff(cache, "k", list(approval.diff_texts("Edit", dict(args)))))
    assert cache.view("k") is view and view.diff is model
    edited = approval.diff_texts("Edit", {**args, "new_string": "II"})
    frame(seam, lambda: typeset.diff(cache, "k", edited))
    assert cache.view("k") is view and view.diff is not model
    assert rows(view.diff, RowKind.ADD) == ["II"]


def test_an_equal_model_that_is_not_the_same_model_is_shown_in_place_of_it(seam: Any) -> None:
    """
    Two models with the same rows compare equal and may still differ in the context
    a click would expand, so a ``Diff`` is the same source only if it is the same one.
    """
    texts = (approval.DiffText("a\n", "b\n", "x.py"),)
    one, other = typeset.diff_model(texts), typeset.diff_model(texts)
    assert one == other and one is not other
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.diff(cache, "k", one))
    view = cache.view("k")
    frame(seam, lambda: typeset.diff(cache, "k", one))
    assert cache.view("k") is view and view.diff is one
    frame(seam, lambda: typeset.diff(cache, "k", other))
    assert cache.view("k").diff is other


def test_a_call_with_no_diff_is_none_and_stays_none_cheaply(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []
    real = diffs.approval_diff
    monkeypatch.setattr(diffs, "approval_diff", lambda p: (asked.append(p.tool_name), real(p))[1])
    call = pending("Bash", {"command": "ls"})
    assert call.texts is None
    cache = typeset.ViewCache()
    got: list[float | None] = []
    for _ in range(3):
        frame(seam, lambda: got.append(typeset.diff(cache, "k", call)))
    assert got == [None, None, None]
    assert asked == ["Bash"]

    def nothing() -> None:
        got.append(typeset.diff(cache, "none", None))
        got.append(typeset.diff(cache, "tuple", ()))
        got.append(typeset.diff(cache, "list", []))
        got.append(typeset.diff(cache, "model", Diff(())))

    frame(seam, nothing)
    assert got[3:] == [None, None, None, None]
    assert [cache.view(key) for key in ("k", "none", "tuple", "list", "model")] == [None] * 5


def test_a_diff_draws_its_gutter_and_both_sides(seam: Any, tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("foo")
    cache = typeset.ViewCache()
    call = pending("Edit", {"file_path": str(target), "old_string": "foo", "new_string": "bar"})
    drawn: list[int] = []

    def body(**given: Any) -> None:
        buffer = seam.get_window_draw_list().vtx_buffer
        before = buffer.size()
        typeset.diff(cache, "k", call, **given)
        drawn.append(buffer.size() - before)

    frame(seam, body)
    view = cache.view("k")
    assert view.row_count >= 4 and view.layout.name == "UNIFIED"
    # The path, the counts, the hunk line, a marker and a word a row: glyphs.
    assert drawn[0] > 4 * 30

    frame(seam, lambda: body(side_by_side=True, height_cap=60.0))
    assert cache.view("k") is view and view.layout.name == "SIDE_BY_SIDE"
    assert 0 < (cache.height("k") or 0.0) <= 60.0 + 8.0


def test_one_model_may_be_drawn_by_two_panes_each_in_a_view_of_its_own(seam: Any) -> None:
    """
    Two panes can show the same parked call in one frame. The model is immutable
    and may be shared; a view holds one scroll position and one origin and may not.
    """
    model = typeset.diff_model((approval.DiffText("a\n", "b\n", "x.py"),))
    inbox, detail = typeset.ViewCache(), typeset.ViewCache()

    def body() -> None:
        typeset.diff(inbox, "a1", model, height_cap=100.0)
        typeset.diff(detail, "a1", model)

    frame(seam, body)
    frame(seam, body)
    assert inbox.view("a1") is not detail.view("a1")
    assert inbox.view("a1").diff is model and detail.view("a1").diff is model


def test_a_diff_is_made_and_drawn_without_opening_a_file_or_asking_the_gate_again(
    seam: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``approval.diff_texts`` is the one place a call's effect on a file is decided, and
    it runs when the call parks. A second reader here would be a second answer -- and
    so would a second call to the first.

    The file the parked call names is changed after it parks, so a seam that read it
    again, by any route, would show different rows. Then every way Python opens a
    file is made to raise for the length of the build and the draw, and so is
    ``diff_texts``.
    """
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\n")
    call = pending("Edit", {"file_path": str(target), "old_string": "two", "new_string": "2"})
    target.write_text("something else entirely\n")
    cache = typeset.ViewCache()
    # Whatever a first diff view reads lazily -- a face's metrics -- is read here.
    frame(seam, lambda: typeset.diff(cache, "warm", (approval.DiffText("a\n", "b\n", "w"),)))

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"a file was opened: {args[:1]}")

    import builtins
    import io
    import os

    for owner, name in ((builtins, "open"), (io, "open"), (os, "open"), (Path, "open")):
        monkeypatch.setattr(owner, name, refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(approval, "diff_texts", refuse)
    built = typeset.approval_diff(call)
    got: list[float | None] = []
    frame(seam, lambda: got.append(typeset.diff(cache, "k", call)))
    monkeypatch.undo()
    assert built is not None
    assert (rows(built, RowKind.REMOVE), rows(built, RowKind.ADD)) == (["two"], ["2"])
    assert got[0] is not None and got[0] > 0
    assert rows(cache.view("k").diff, RowKind.ADD) == ["2"]


def test_a_pane_that_says_the_warning_itself_can_have_the_diff_leave_it_out(seam: Any) -> None:
    texts = (approval.DiffText("a\n", "b\n", LONG_LABEL, will_not_apply="file not found"),)
    cache = typeset.ViewCache()
    narrow(seam, lambda: typeset.diff(cache, "k", texts, warning=False), width=420.0)
    view = cache.view("k")
    assert [file.will_not_apply for file in view.diff.files] == [""]
    # The same texts asked the other way are another model, in the same view.
    narrow(seam, lambda: typeset.diff(cache, "k", texts), width=420.0)
    assert cache.view("k") is view
    assert view.diff.files[0].will_not_apply == "may not apply: file not found"
    # The model a pane is handed is never the stripped one.
    assert typeset.diff_model(texts).files[0].will_not_apply == "may not apply: file not found"


def test_a_diff_the_library_stops_under_says_so_and_reports_its_rows_missing(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import advtext

    texts = (approval.DiffText("a\nb\n", "a\nc\nd\n", "x.py"),)
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.diff(cache, "k", texts))
    assert cache.unbuilt("k") == 0

    def stopped(**given: Any) -> bool:
        raise advtext.TypesetUnavailable("imtypeset stopped: the library said no")

    monkeypatch.setattr(cache.view("k"), "draw", stopped)
    said = _native_lines(seam, monkeypatch)
    tall: list[float | None] = []
    frame(seam, lambda: tall.append(typeset.diff(cache, "k", texts)))
    assert tall[0] is not None and tall[0] > 0.0
    assert cache.unbuilt("k") == 3, "one removed and two added"
    assert not said, "said in the seam's own wrapped text, not as a coloured line"
    typeset.pre_frame()
    assert not typeset.can_draw()
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD), "a load is the way back"


@pytest.mark.xfail(strict=True, reason=P21)
def test_a_diff_row_keeps_its_columns_at_the_advance_imgui_draws_the_face_at(seam: Any) -> None:
    """
    Why no pane calls ``diff()``. Forty of one character on a row of the diff
    view are forty glyphs one step apart, and the step is the advance ImGui has
    baked for the face: measured, they are steps of 7 and 8 px against a baked 8
    (``scripts/measure_typeset_diff.py`` has the rest: 13 px between rows at one
    column, and a press on a glyph answered as its neighbour).
    """
    baked = typeset.face_font("CODE").get_font_baked(16.0).get_char_advance(ord("m"))
    assert baked == 8.0
    texts = (approval.DiffText("", "m" * 40 + "\n", "x"),)
    cache = typeset.ViewCache()
    lefts: list[float] = []

    def body() -> None:
        buffer = seam.get_window_draw_list().vtx_buffer
        before = buffer.size()
        typeset.diff(cache, "k", texts)
        view = cache.view("k")
        row = view.row_count - 1
        top = view.origin[1] + row * view.grid.row
        lefts.extend(
            sorted(
                {
                    buffer[n].pos.x
                    for n in range(before, buffer.size(), 4)
                    if top <= buffer[n].pos.y < top + view.grid.row
                }
            )[-40:]
        )

    frame(seam, body)
    steps = [b - a for a, b in zip(lefts, lefts[1:], strict=False)]
    assert len(steps) == 39
    assert set(steps) == {baked}


@pytest.mark.xfail(strict=True, reason=P16)
def test_a_diff_header_too_long_for_its_view_can_be_brought_on_screen(seam: Any) -> None:
    """
    The view's file header is one line, cut at the view's edge and outside what
    the view scrolls to: under a long path, ``may not apply`` and the end of the
    path are where nobody can reach them, and the view does not say that the
    header was cut. Asked of the view as it is: the width it scrolls across is
    less than its own header's. A pane that adopts ``diff()`` says the warning
    itself (``warning=False``) and would have to say the path.
    """
    texts = (approval.DiffText("a\n", "b\n", LONG_LABEL, will_not_apply="anchor not found"),)
    cache = typeset.ViewCache()
    narrow(seam, lambda: typeset.diff(cache, "k", texts), width=420.0)
    view = cache.view("k")
    header = f"{LONG_LABEL}  +1 -1  ! may not apply: anchor not found"
    assert max(420.0, view.content_width) >= len(header) * view.grid.cell


# -- a fenced diff is the code of its document --------------------------------------


@pytest.mark.parametrize(("name", "lines"), _fences(), ids=[name for name, _ in _fences()])
def test_every_line_of_a_diff_fence_is_set_settled_and_arriving(
    seam: Any, name: str, lines: list[str]
) -> None:
    """
    The library's diff reader keeps every line and its view draws only some: what
    it files as a file's header lines -- a sentence over ``---``, a mail's message,
    a line under a hunk that had its count -- is on the model and not on screen.
    In a concern body that is a sentence the recipient reads and the approver
    does not. No fence is handed to the library's diff view (its rows do not
    keep their columns), so whatever a fence holds is the code of its document,
    every line of it, under the word it was opened with.

    Arriving, the fence is looked at after each whole line, still open: at no
    point is a line that has arrived missing.
    """
    body = "".join(line + "\n" for line in lines)
    source = f"Before.\n\n```diff\n{body}```\n\nAfter.\n"
    cache = typeset.ViewCache()

    def code(upto: int) -> str:
        # As the library's reader has a fence: a tab is the spaces to its stop.
        return "\n".join(["diff", *lines[:upto]]).expandtabs(4)

    def body_() -> None:
        typeset.markdown(cache, "markdown", source)
        typeset.document(cache, "parts", (typeset.Part(source),))

    frame(seam, body_)
    for key in ("markdown", "parts"):
        assert not cache.view(key).hosts, key
        assert cache.texts(key)[1] == code(len(lines)), key
    handle = typeset.stream(cache, "stream")
    handle.append("Before.\n\n```diff\n")
    for at, line in enumerate(lines):
        handle.append(line + "\n")
        frame(seam, lambda: handle.draw())
        assert cache.texts("stream")[1].rstrip("\n") == code(at + 1).rstrip("\n"), at
    handle.append("```\n\nAfter.\n")
    handle.close()
    frame(seam, lambda: handle.draw())
    assert cache.texts("stream") == cache.texts("markdown")
    assert not handle.view.hosts


# -- which fences are honest diffs -------------------------------------------------


def _read(lines: list[str]) -> tuple[str, Diff]:
    text = "\n".join(lines)
    return text, Diff.from_unified(text + "\n")


def test_a_fence_asks_for_a_diff_only_when_its_info_string_is_the_one_word() -> None:
    """
    The library takes any info string that begins with the word and drops the
    rest; the rest is text the recipient gets and the approver would not see.
    """
    for info in ("diff", "patch", "  diff  "):
        assert diffs.is_diff_fence(info), info
    for info in ("diff IMPORTANT also delete tests", "difference", "patch -p1", "python", ""):
        assert not diffs.is_diff_fence(info), info


@pytest.mark.parametrize("lines", [_ONE_FILE, _TWO_FILES, _FRAGMENT], ids=["one", "two", "bare"])
def test_every_line_of_an_honest_diff_is_one_a_diff_view_draws(lines: list[str]) -> None:
    assert diffs.accounted(*_read(lines))


@pytest.mark.parametrize(
    "lines",
    [
        ["Please also delete the tests.", *_ONE_FILE],
        [*_ONE_FILE, "A sentence between two files.", *_TWO_FILES[6:]],
        [*_ONE_FILE, "a line after the last hunk"],
        [_MAIL, *_ONE_FILE],
        ["over the rule", "---", *_ONE_FILE],
        _GIT,
    ],
    ids=["above", "between files", "after the last hunk", "a mail", "a bare rule", "git's index"],
)
def test_a_diff_with_a_line_no_view_would_draw_is_not_accounted_for(lines: list[str]) -> None:
    """
    The reader keeps every line and the view does not draw them all. A fence with
    one such line is code, where all of it is on screen.
    """
    assert not diffs.accounted(*_read(lines))
