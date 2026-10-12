"""
The diff model of a parked call, who draws a diff, and which fences are honest diffs.

The model needs no window and no library context. Who draws a diff is a
measurement of the library that is running (``gate.columns``): it is held
here to be false at every pixel height a role reaches on the library pptmstr
is built against, true at an even em, and to agree both ways with what a diff
view drawn in a window does. ``diff()`` and a fenced diff are tested on both
answers; ``columns_hold`` (``tests/conftest.py``) is the configuration the
second is drawn in. The strict xfail under P21 is the same measurement taken
from a window, and comes off with the request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import advtext
import pytest
from advtext import Diff, RowKind

from pptmstr import approval
from pptmstr.model import NodeId, PendingApproval
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Role, diffs, gate, roles
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
    WINDOW,
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


# -- diff() on the library ----------------------------------------------------------


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
    # The file's label is a line of the chrome's above the view, under no cap.
    label = seam.get_text_line_height_with_spacing()
    assert got["long"] is not None
    assert 5 * row <= got["long"] - label <= 6 * row + 8.0
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
    monkeypatch.setattr(
        diffs, "diff_model", lambda texts, **how: (built.append(texts), real(texts, **how))[1]
    )
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
    monkeypatch.setattr(
        diffs, "approval_diff", lambda p, **how: (asked.append(p.tool_name), real(p, **how))[1]
    )
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
    label = seam.get_text_line_height_with_spacing()
    assert 0 < (cache.height("k") or 0.0) - label <= 60.0 + 8.0


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
    assert said == ["x.py"], "the stop is the seam's own wrapped text, under the file's label"
    typeset.pre_frame()
    assert not typeset.can_draw()
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD), "a load is the way back"


@pytest.mark.xfail(strict=True, reason=P21)
def test_a_diff_row_keeps_its_columns_at_the_advance_imgui_draws_the_face_at(seam: Any) -> None:
    """
    Why no pane calls ``diff()`` on this library. Forty of one character on a row of the diff
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


def test_a_diff_the_library_refuses_a_view_for_reports_every_changed_row_missing(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The library stopped under another pane earlier in the frame, and this key
    is new: the diff view's constructor raises before the seam holds anything
    for it. What is on screen is the sentence, so every changed row is reported
    as not set and a caller that offers a decision on the diff is not told
    "nothing missing".
    """

    def refused(self: Any, *given: Any, **named: Any) -> None:
        raise advtext.TypesetUnavailable("imtypeset is not in use: the library said no")

    monkeypatch.setattr(advtext.TypesetDiffView, "__init__", refused)
    texts = (approval.DiffText("a\nb\n", "a\nc\nd\n", "x.py"),)
    cache = typeset.ViewCache()
    tall: list[float | None] = []
    frame(seam, lambda: tall.append(typeset.diff(cache, "k", texts)))
    assert tall[0] is not None and tall[0] > 0.0
    assert cache.view("k") is None
    assert cache.unbuilt("k") == 3
    typeset.pre_frame()
    assert not typeset.can_draw()
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD), "a load is the way back"


# -- what a header row says is said above the view ------------------------------------


def _said_with_sizes(seam: Any, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float, float]]:
    """
    Every string handed to ``imgui.text_colored`` from here on, with the width
    and the height of the item it made.
    """
    lines: list[tuple[str, float, float]] = []
    real = seam.text_colored

    def recording(colour: Any, text: str) -> None:
        real(colour, text)
        size = seam.get_item_rect_size()
        lines.append((text, float(size.x), float(size.y)))

    monkeypatch.setattr(seam, "text_colored", recording)
    return lines


def test_a_diff_header_too_long_for_its_view_is_said_above_it_warning_first(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The view's file header is one line cut at the view's edge, and the view does
    not scroll its headers (the strict xfail under P16 above): under a long path
    the end of the path and ``may not apply`` are where nothing reaches them.
    The library pptmstr is built against cannot say whether a header fit, so
    the seam says the header's words above the view always, the warning before
    the path, wrapped to the view's width and so all in sight.
    """
    texts = (approval.DiffText("a\n", "b\n", LONG_LABEL, will_not_apply="anchor not found"),)
    cache = typeset.ViewCache()
    said = _said_with_sizes(seam, monkeypatch)
    narrow(seam, lambda: typeset.diff(cache, "k", texts), width=420.0)
    assert [text for text, _, _ in said] == ["may not apply: anchor not found", LONG_LABEL]
    one_line = seam.get_text_line_height()
    for text, wide, _ in said:
        assert wide <= 420.0, text
    assert said[1][2] >= 2 * one_line, "the path is broken at the view's edge, not cut there"

    # A pane that says the prediction itself is not told it twice.
    said.clear()
    narrow(seam, lambda: typeset.diff(cache, "k", texts, warning=False), width=420.0)
    assert [text for text, _, _ in said] == [LONG_LABEL]


def test_what_is_wrong_with_a_file_is_said_above_its_diff_before_its_name(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The header row's other warnings are the library's reading of the rows: a
    character it draws a placeholder for, and one that reads differently from
    what it is. Cut off at the view's edge they are a diff that looks ordinary.
    So is a file shown as new because what is there could not be read.
    """
    texts = (
        approval.DiffText("x = 1\n", "x = 1 \u202e# reversed\n", "bidi.py"),
        approval.DiffText("a\n", "a\x07\n", "bell.py"),
        approval.DiffText(None, "new\n", "blob.bin", unreadable=True),
        approval.DiffText("a\r\nb\r\n", "a\nb\n", "crlf.txt"),
    )
    assert diffs.above(typeset.diff_model(texts)) == (
        ("contains invisible or bidirectional control characters", True),
        ("bidi.py", False),
        ("contains control characters", True),
        ("bell.py", False),
        ("blob.bin  (existing file is not readable as text; shown as new)  (new file)", False),
        ("crlf.txt  (line endings only: CRLF -> LF)", False),
    )
    cache = typeset.ViewCache()
    said = _native_lines(seam, monkeypatch)
    frame(seam, lambda: typeset.diff(cache, "k", texts))
    assert said == [line for line, _ in diffs.above(cache.view("k").diff)]


def test_a_model_made_from_texts_has_nothing_in_a_header_row_but_what_is_said_above(
    tmp_path: Path,
) -> None:
    """
    What ``above`` has to cover is what the view puts in rows it does not
    scroll. A model made from a call's texts has a label, a state and the
    warnings above, and no hunk section, no line read as nothing and no file
    shown as written: those come only of reading a unified string back, which
    is why a parked call with no texts is its pane's own rows (``draws``).
    """
    target = tmp_path / "a.py"
    target.write_text("def f():\n    one\n" + "    pad\n" * 40 + "def g():\n    two\n")
    edit = pending("Edit", {"file_path": str(target), "old_string": "two", "new_string": "2"})
    for call in (
        edit,
        pending("Write", {"file_path": str(tmp_path / "new.py"), "content": "a\n"}),
        pending("MultiEdit", {"edits": [{"old_string": "a", "new_string": "b"}]}),
    ):
        assert diffs.draws(call)
        model = typeset.approval_diff(call)
        assert model is not None
        for file in model.files:
            assert not file.dialect and not file.raw and not file.unread and not file.header
            assert [hunk.section for hunk in file.hunks if hunk.section] == []
    old = PendingApproval(
        id="a",
        node=NodeId("n"),
        tool_name="Edit",
        tool_use_id="t",
        raw_args={},
        summary="",
        requested_at=0.0,
        diff="said over the header\n--- a/x\n+++ b/x\n@@ -1 +1 @@ def f():\n-a\n+b\n",
    )
    assert not diffs.draws(old)
    read = typeset.approval_diff(old)
    assert read is not None and read.files[0].header[0] == "said over the header"
    assert ("@@ def f():", False) in diffs.above(read)


def test_a_row_wider_than_its_view_is_not_broken_and_its_end_is_in_reach(seam: Any) -> None:
    """
    A diff's row is one line whatever its length. The view says how wide its
    widest is, is taller by its own bar when that is wider than the view, and
    scrolls to the row's last character.
    """
    row = "x = '" + "a" * 293 + "Z'"
    texts = (approval.DiffText("", f"short\n{row}\n", "x.py"),)
    plain = (approval.DiffText("", "short\nalso short\n", "x.py"),)
    cache = typeset.ViewCache()

    def body() -> None:
        typeset.diff(cache, "wide", texts)
        typeset.diff(cache, "plain", plain)

    frame(seam, body)
    wide, fits = cache.view("wide"), cache.view("plain")
    assert wide.row_count == fits.row_count, "the long row is one row"
    measure = WINDOW[0]
    cell = wide.grid.cell
    assert wide.content_width >= len(row) * cell > 2.0 * measure
    assert wide.height_for(measure) > fits.height_for(measure), "taller by the bar"
    assert (cache.height("wide") or 0.0) > (cache.height("plain") or 0.0)

    wide.scroll_x = 10**6
    frame(seam, body)
    assert wide.scroll_x > 0, "the view scrolls sideways"
    # Scrolled as far as it goes, the row's last cell is left of the view's right edge.
    assert wide.content_width - wide.scroll_x <= measure


# -- who draws a diff: a measurement of the library that is running --------------------

PIXEL_HEIGHTS = (14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0)
"""
Every pixel height a diff's rows are set at with the UI font at 16 and the
operator's scale and step anywhere they go: ``roles.STEPS`` is -2 to +4.
"""


def _at(tall: float, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Tell the seam the UI font is ``tall`` pixels high, as ``pre_frame`` reads it.
    """
    monkeypatch.setattr(gate, "_live_font_size", lambda: tall)
    roles.follow(tall)


def _em(em: float) -> float:
    """
    The pixel height at which Inconsolata's em is ``em`` pixels.
    """
    return em / advtext.em_size_for_pixel_height(advtext.Face.CODE, 1.0)


def test_the_measurement_is_what_decides_and_nothing_has_overridden_it() -> None:
    assert diffs.ADOPT == "measured"


def test_every_step_a_role_can_take_is_a_pixel_height_the_measurement_was_taken_at() -> None:
    assert {typeset.BODY_PX + step for step in typeset.STEPS} <= set(PIXEL_HEIGHTS)


@pytest.mark.parametrize("tall", PIXEL_HEIGHTS)
def test_on_the_library_as_built_against_a_diffs_columns_hold_at_no_pixel_height(
    seam: Any, monkeypatch: pytest.MonkeyPatch, tall: float
) -> None:
    """
    Requests P21 and P22 as the seam measures them, at every size the product
    sets a diff at. The library lays a column out from the font file, where
    ImGui draws the rasteriser's whole pixel: so the cell is not the baked
    advance, glyphs of a long row are off their column, and a press on a drawn
    glyph lands on a neighbour. While this holds no pane draws a diff with the
    library. **It comes off with P21**, as the strict xfail below does: on a
    library that keeps its columns this test fails and the panes have already
    changed renderer.
    """
    _at(tall, monkeypatch)
    assert typeset.px(Role.DIFF) == tall
    found = diffs.columns()
    assert not found.holds
    assert found.size == tall, "the row was read from the bake it was drawn from"
    assert found.baked == float(int(found.baked)) and abs(found.cell - found.baked) > 0.009
    assert found.glyphs == 156 and found.off > 70
    assert not diffs.adopted() and not typeset.diff_adopted()
    assert "px column where ImGui draws" in found.why


@pytest.mark.parametrize("em", [12.0, 14.0, 16.0, 18.0, 20.0, 22.0])
def test_at_an_even_em_a_diffs_columns_hold_by_the_same_measurement(
    seam: Any, monkeypatch: pytest.MonkeyPatch, em: float
) -> None:
    """
    The configuration in which the library as it is keeps a column: Inconsolata's
    advance is half an em, so at an even em the cell laid out is the whole pixel
    ImGui bakes. Every glyph of the row is then on its column and every press
    lands on the glyph it was made on. This is where the other branch of every
    pane is drawn by a run (``columns_hold``).
    """
    _at(_em(em), monkeypatch)
    found = diffs.columns()
    assert found.holds, found
    assert found.baked == em / 2.0 and abs(found.cell - found.baked) < gate.COLUMN_EPSILON_PX
    assert (found.glyphs, found.off, found.mishit) == (156, 0, 0)
    assert diffs.adopted()


@pytest.mark.parametrize("em", [13.0, 15.0, 17.0])
def test_at_an_odd_em_they_do_not(seam: Any, monkeypatch: pytest.MonkeyPatch, em: float) -> None:
    _at(_em(em), monkeypatch)
    found = diffs.columns()
    assert not found.holds and found.off and found.mishit
    assert abs(found.cell - em / 2.0) < gate.COLUMN_EPSILON_PX, "half a pixel off the grid"


def test_a_row_too_short_to_show_it_is_not_enough_if_the_cell_is_not_the_baked_advance(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The row measured is 186 columns. A cell a thousandth of a pixel off the baked
    advance moves no glyph of it and misplaces a press on none, and moves one
    500 columns along a row an agent wrote: so the cell is compared on its own,
    to a bound far under what the row could show.
    """
    import dataclasses

    real = advtext.TypesetDiffView.grid.fget
    assert real is not None

    def a_thousandth_wide(self: Any) -> Any:
        grid = real(self)
        return dataclasses.replace(grid, cell=grid.cell + 0.001)

    monkeypatch.setattr(advtext.TypesetDiffView, "grid", property(a_thousandth_wide))
    assert typeset.load_fonts(BOLD), "so that nothing measured earlier is held"
    _at(_em(16.0), monkeypatch)
    found = diffs.columns()
    assert (found.off, found.mishit) == (0, 0)
    assert 0.0009 < found.cell - found.baked < 0.0011 and not found.holds
    assert found.why == "lays out a 8.001 px column where ImGui draws 8 px"
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD)


def test_a_cell_that_is_the_baked_advance_is_not_enough_if_one_glyph_is_drawn_off_it(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The three things asked are asked separately. Here the library is the one
    that keeps its columns, at an even em, and what its draw emitted is read
    with one glyph a pixel right of where it was drawn: the cell is still the
    baked advance, and the columns do not hold.
    """
    real = gate._glyph_quads

    def nudged(native: Any, vertices: bytes) -> list[tuple[float, float, float, float]]:
        quads = real(native, vertices)
        if len(quads) == 156:
            # The row of code, and not the row of tabs measured after it.
            left, right, u, v = quads[90]
            quads[90] = (left + 1.0, right + 1.0, u, v)
        return quads

    monkeypatch.setattr(gate, "_glyph_quads", nudged)
    assert typeset.load_fonts(BOLD), "so that nothing measured earlier is held"
    _at(_em(16.0), monkeypatch)
    found = diffs.columns()
    assert abs(found.cell - found.baked) < gate.COLUMN_EPSILON_PX
    assert (found.off, found.holds) == (1, False)
    assert found.why == "draws 1 of 156 glyphs of a row off their column"
    assert not diffs.adopted()
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD)


def test_a_row_drawn_on_its_columns_is_not_enough_if_a_press_lands_on_a_neighbour(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    And the third: the cell is right and every glyph is on it, and the library's
    hit test answers from somewhere else -- here, three pixels to the left, as
    it would if it answered from a layout the draw had moved away from.
    """
    native = advtext.typesetter.module()
    real = native.Document.hit_test

    def shifted(self: Any, at: tuple[float, float]) -> Any:
        return real(self, (at[0] + 3.0, at[1]))

    monkeypatch.setattr(native.Document, "hit_test", shifted)
    assert typeset.load_fonts(BOLD), "so that nothing measured earlier is held"
    _at(_em(16.0), monkeypatch)
    found = diffs.columns()
    assert abs(found.cell - found.baked) < gate.COLUMN_EPSILON_PX and found.off == 0
    assert found.mishit == 156 and not found.holds
    assert found.why == "answers a press on 156 of 156 glyphs as a neighbour"
    assert not diffs.adopted()
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD)


def _drawn_row(seam: Any, text: str) -> tuple[Any, list[float]]:
    """
    A one-row diff drawn by the diff view in a window: the view, and the left
    edge of each of the row's glyphs, read from the window's draw list.
    """
    texts = (approval.DiffText("", text + "\n", "x"),)
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
            )[-len(text) :]
        )

    frame(seam, body)
    return cache.view("k"), lefts


@pytest.mark.parametrize("held", [False, True], ids=["pixel height 16", "em 16"])
def test_what_one_measured_row_says_is_what_a_diff_view_drawn_in_a_window_does(
    seam: Any, monkeypatch: pytest.MonkeyPatch, held: bool
) -> None:
    """
    The measurement sets a row in a document of its own and never opens a
    window. What it answers is held here to the view a pane would draw: forty
    of one character on a row of a diff view, in a frame, read from the draw
    list. Where the measurement holds they are one baked advance apart, and a
    point a quarter and three quarters into each glyph is answered by the
    view's own document as the positions either side of that character; where
    it does not, neither is so.
    """
    _at(_em(16.0) if held else 16.0, monkeypatch)
    found = diffs.columns()
    assert found.holds is held
    view, lefts = _drawn_row(seam, "m" * 40)
    assert len(lefts) == 40
    assert abs(view.grid.cell - found.cell) < 1e-9, "the row measured is the view's row"
    steps = {b - a for a, b in zip(lefts, lefts[1:], strict=False)}
    assert (steps == {found.baked}) is held, steps

    code = view.native_documents[1]
    middle = (view.row_count - 0.5) * view.grid.row
    base = int(code.hit_test((0.5, middle)).position[1])
    landed = 0
    for index, left in enumerate(lefts):
        pen = left - lefts[0]
        before = int(code.hit_test((pen + 0.25 * found.baked, middle)).position[1]) - base
        after = int(code.hit_test((pen + 0.75 * found.baked, middle)).position[1]) - base
        landed += (before, after) == (index, index + 1)
    assert (landed == 40) is held, landed


def test_a_measurement_is_taken_once_for_a_size_and_again_after_a_load(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A pane asks in every frame it draws a diff, and the menu's sentence asks
    again. The row is set once for a code style; a load of the fonts is another
    atlas, and the answer for it is measured.
    """
    taken: list[object] = []
    real = gate._measured_columns
    monkeypatch.setattr(gate, "_measured_columns", lambda theme: (taken.append(1), real(theme))[1])
    roles.moved()
    for _ in range(3):
        assert not diffs.adopted()
        assert typeset.diff_renderer()
    assert len(taken) == 0, "the size was measured by an earlier test and is still held"
    assert typeset.load_fonts(BOLD)
    for _ in range(3):
        assert not diffs.adopted()
    assert len(taken) == 1
    _at(_em(16.0), monkeypatch)
    for _ in range(3):
        assert diffs.adopted()
    assert len(taken) == 2


def test_a_row_that_cannot_be_measured_does_not_hold_and_stops_nothing(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The measurement reaches past what the library promises. Whatever that
    raises is an answer of "no" with the reason, and is not the library
    stopping: every pane keeps its typeset branch and draws its own diff.
    """

    def broken(theme: Any) -> Any:
        raise AttributeError("module 'imtypeset' has no attribute 'Document'")

    monkeypatch.setattr(gate, "_measured_columns", broken)
    assert typeset.load_fonts(BOLD), "so that nothing measured earlier is held"
    _at(_em(16.0), monkeypatch)
    found = diffs.columns()
    assert not found.holds and "could not be measured (AttributeError" in found.why
    assert not diffs.adopted()
    assert typeset.can_draw() and typeset.enabled(typeset.Pane.DETAIL)
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD)


@pytest.mark.parametrize("held", [False, True], ids=["pixel height 16", "em 16"])
def test_one_constant_overrides_the_measurement_either_way(
    seam: Any, monkeypatch: pytest.MonkeyPatch, held: bool
) -> None:
    _at(_em(16.0) if held else 16.0, monkeypatch)
    assert diffs.adopted() is held
    monkeypatch.setattr(diffs, "ADOPT", "always")
    assert diffs.adopted()
    assert (typeset.diff_renderer() or "").startswith("diffs.ADOPT is 'always': the library ")
    monkeypatch.setattr(diffs, "ADOPT", "never")
    assert not diffs.adopted()
    assert typeset.diff_renderer() == "diffs.ADOPT is 'never': diffs are pptmstr's own rows"


def test_no_constant_draws_a_diff_with_a_library_that_cannot_draw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Needs no library, and is the answer where there is none: ``always`` is about
    what was measured, not about whether there is anything to draw with.
    """
    monkeypatch.setattr(gate, "can_draw", lambda: False)
    monkeypatch.setattr(diffs, "ADOPT", "always")
    assert not diffs.adopted()
    assert typeset.diff_renderer() is None


def test_the_sentence_says_who_draws_a_diff_and_by_what_measurement(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    One line, for the Text menu and under the settings dialog's master switch:
    an operator cannot tell from a diff which renderer drew it, or why.
    """
    assert typeset.diff_renderer() == (
        "diffs are pptmstr's own rows: the library "
        "lays out a 7.626 px column where ImGui draws 8 px"
    )
    typeset.configure(typeset=False)
    assert typeset.diff_renderer() is None, "the switch is its own sentence"
    typeset.configure(typeset=True)
    _at(_em(16.0), monkeypatch)
    assert typeset.diff_renderer() == (
        "diffs are the library's: it keeps a 8 px column: laid out, drawn and hit over a row"
    )


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
    does not. On the library pptmstr is built against no fence is handed to the
    diff view (its rows do not keep their columns: ``diffs.adopted``), so
    whatever a fence holds is the code of its document, every line of it,
    under the word it was opened with. The test after this one draws the same
    fences where the columns hold.

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


HOSTED = {
    *("one", "two", "fragment", "no newline", "section", "wordy hunk"),
    # A sentence where the reader takes it for a row: unmarked context inside a
    # hunk or a fragment, and it is drawn as one.
    *(f"one+prose@{at}" for at in (1, 3, 4, 5)),
    *(f"two+prose@{at}" for at in (1, 3, 4, 5, 9, 10)),
    *(f"fragment+prose@{at}" for at in range(5)),
    "fragment+rule-below",
    # A tab and a stamp after the last row are that row's text.
    *("one+stamped", "two+stamped", "fragment+stamped"),
}
"""
The fences of ``_fences()`` a diff view draws every line of, by name: the ones
``fence_diff`` must answer for and no other. Not among them: a sentence over a
file's header or under a hunk that has had its count, a mail, a rule over a
diff, what ``git diff`` prints, a path with a stamp, a rename, and a fence
that is not a diff at all (shown as written, in rows the view does not scroll).
"""


def _drawn_lines(hosted: Any) -> list[str]:
    """
    Every form in which a written line can be a row of a hosted diff: behind
    its marker, and bare for context nobody marked.
    """
    forms: list[str] = []
    for file in hosted.view.diff.files:
        for hunk in file.hunks:
            for row in hunk.rows:
                forms += [row.kind.value + row.text, row.text]
    return forms


@pytest.mark.parametrize(("name", "lines"), _fences(), ids=[name for name, _ in _fences()])
def test_where_columns_hold_an_honest_diff_fence_is_a_diff_view_and_every_line_is_still_set(
    columns_hold: Any, name: str, lines: list[str]
) -> None:
    """
    The same fences on the other branch. One a diff view draws every line of is
    hosted by the library's diff fences in its document's view, at the diff
    role's size, and the lines the view puts in header rows, which it cuts at
    its edge, are a code block above it. One that holds a line no view would
    draw is the code of its document under the word it was opened with, as on
    the library that does not keep its columns. A stream hosts none.
    """
    seam = columns_hold
    # As the library's reader has a fence: a tab is the spaces to its stop.
    text = "\n".join(lines).expandtabs(4)
    honest = diffs.fence_diff("diff", text) is not None
    assert honest is (name in HOSTED)
    body = "".join(line + "\n" for line in lines)
    source = f"Before.\n\n```diff\n{body}```\n\nAfter.\n"
    cache = typeset.ViewCache()

    def body_() -> None:
        typeset.markdown(cache, "markdown", source)
        typeset.document(cache, "parts", (typeset.Part(source),))

    frame(seam, body_)
    frame(seam, body_)
    for key in ("markdown", "parts"):
        view = cache.view(key)
        texts = cache.texts(key)
        assert cache.unbuilt(key) == 0
        if not honest:
            assert not view.hosts, key
            assert texts[1] == "\n".join(["diff", text]), key
            continue
        headers = diffs.fence_headers(text)
        at = 2 if headers else 1
        assert list(view.hosts) == [at], key
        if headers:
            assert texts[1] == "\n".join(headers), "what a header row would cut, above it"
        hosted = view.hosted(at)
        assert hosted.text == text + "\n", "the view read the text the rule was asked of"
        assert diffs.accounted(hosted.text, hosted.view.diff)
        # Not by the rule's own counting: each written line is a row of the
        # view's model or one of the lines set above it.
        rows_ = _drawn_lines(hosted)
        lost = [
            line
            for line in text.split("\n")
            if line.strip()
            and line not in headers
            and not line.startswith("@@")
            and line not in rows_
            and "\\" + line[1:].lstrip() not in rows_
        ]
        assert lost == [], key
        assert hosted.view.grid.cell == pytest.approx(8.0, abs=gate.COLUMN_EPSILON_PX)
        assert hosted.view.grid.row == typeset.diff_row_height()
        assert hosted.view.row_count > 0 and hosted.view.origin is not None, "and drew it"
        assert texts[at + 1] == "After."

    handle = typeset.stream(cache, "stream")
    handle.append(source)
    handle.close()
    frame(seam, lambda: handle.draw())
    assert not handle.view.hosts
    assert cache.texts("stream")[1] == "\n".join(["diff", text])


def test_a_fence_whose_info_string_says_more_than_diff_is_never_a_diff_view(
    columns_hold: Any,
) -> None:
    source = "```diff IMPORTANT also delete tests\n-old1\n+new1\n```\n"
    cache = typeset.ViewCache()
    frame(columns_hold, lambda: typeset.markdown(cache, "k", source))
    assert not cache.view("k").hosts
    assert cache.texts("k") == ("diff IMPORTANT also delete tests\n-old1\n+new1",)


def test_a_fence_the_library_stops_under_while_hosting_reports_its_text_missing(
    columns_hold: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The diff view of a hosted fence is made while its document is, so a library
    that refuses it is caught where a refused document is: the sentence is on
    screen and every line handed over is reported as not set.
    """

    def refused(self: Any, *given: Any, **named: Any) -> None:
        raise advtext.TypesetUnavailable("imtypeset is not in use: the library said no")

    monkeypatch.setattr(advtext.TypesetDiffView, "__init__", refused)
    source = "Before.\n\n```diff\n-old1\n+new1\n```\n"
    cache = typeset.ViewCache()
    frame(columns_hold, lambda: typeset.markdown(cache, "k", source))
    assert cache.unbuilt("k") == source.count("\n") + 1
    typeset.pre_frame()
    assert not typeset.can_draw()
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD)


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


# -- a view that set less than it was handed, and a library that raises ----------------


def _said_by_the_seam(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    said: list[str] = []
    real = diffs.views._said
    monkeypatch.setattr(
        diffs.views, "_said", lambda im, text, ink=None: (said.append(text), real(im, text, ink))[1]
    )
    return said


_THREE = (approval.DiffText("one\ntwo\nthree\n", "one\n2\nthree\nfour\n", "x.py"),)
"""
A change of three rows: one removed, two added.
"""


def _draw_answers_false(patched: pytest.MonkeyPatch) -> None:
    real = advtext.TypesetDiffView.draw
    patched.setattr(
        advtext.TypesetDiffView, "draw", lambda self, **given: bool(real(self, **given)) and False
    )


def _draw_cuts_an_edit(patched: pytest.MonkeyPatch) -> None:
    real = advtext.TypesetDiffView.draw

    def cut(self: Any, **given: Any) -> bool:
        advtext.typesetter.note("a diff edit was cut: block 2: the text is longer than 16 MiB")
        return bool(real(self, **given))

    patched.setattr(advtext.TypesetDiffView, "draw", cut)


@pytest.mark.parametrize(
    ("short", "words"),
    [
        (_draw_answers_false, "could not set something in view as it was asked to"),
        (_draw_cuts_an_edit, "said: a diff edit was cut: block 2: the text is longer than 16 MiB"),
    ],
    ids=["draw answers false", "an edit was cut and draw answers true"],
)
def test_a_diff_the_view_set_less_of_than_it_was_handed_is_unset_and_then_the_panes_own(
    seam: Any, monkeypatch: pytest.MonkeyPatch, short: Any, words: str
) -> None:
    """
    ``TypesetDiffView.draw`` answers false when a row in view could not be set as
    asked, and the library cuts a block past 16 MiB and notes it while the draw
    answers true: either way rows of the change are on no screen. Both are
    made here by their signal, since a real one needs a 16 MiB line. In that
    frame every changed row is reported unset under a sentence, so nothing is
    decided over it; from the next ``diff()`` answers ``None`` under a line
    saying why, which is a pane's cue to draw its own rows, and holds no view.
    The library is not stopped for one diff.
    """
    said = _said_by_the_seam(monkeypatch)
    cache = typeset.ViewCache()
    got: list[float | None] = []
    with monkeypatch.context() as patched:
        short(patched)
        frame(seam, lambda: got.append(typeset.diff(cache, "k", _THREE)))
    assert got[0] is not None and got[0] > 0
    assert cache.unbuilt("k") == 3
    assert said == [f"typeset: the library {words}"]

    frame(seam, lambda: got.append(typeset.diff(cache, "k", _THREE)))
    assert got[1] is None and cache.view("k") is None
    assert said[-1] == f"this diff is the pane's own rows: the library {words}"
    typeset.pre_frame()
    assert typeset.can_draw()

    again = (approval.DiffText("a\n", "b\n", "y.py"),)
    frame(seam, lambda: got.append(typeset.diff(cache, "k", again)))
    assert got[2] is not None and cache.unbuilt("k") == 0, "another diff is asked of the library"


def test_a_diff_with_characters_no_face_has_is_set_whole(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    What a false answer does not track: a row with an emoji, CJK, Greek and a
    control character draws true. (Where the columns hold such a row is the
    pane's own for another reason: ``unfit``.)
    """
    said = _said_by_the_seam(monkeypatch)
    texts = (approval.DiffText("a\n", "x \U0001f600 y 漢 π \x01 z\n", "x.py"),)
    cache = typeset.ViewCache()
    assert not diffs.adopted()
    for _ in range(3):
        frame(seam, lambda: typeset.diff(cache, "k", texts))
        assert cache.unbuilt("k") == 0 and cache.view("k") is not None
    assert said == []


def _raises_attribute_error(*given: Any, **named: Any) -> Any:
    raise AttributeError("'FileDiff' object has no attribute 'renamed'")


def _fault_in_draw(patched: pytest.MonkeyPatch) -> None:
    patched.setattr(advtext.TypesetDiffView, "draw", _raises_attribute_error)


def _fault_in_set_diff(patched: pytest.MonkeyPatch) -> None:
    patched.setattr(advtext.TypesetDiffView, "set_diff", _raises_attribute_error)


def _fault_in_what_above_reads(patched: pytest.MonkeyPatch) -> None:
    patched.setattr(advtext.FileDiff, "invisible_characters", property(_raises_attribute_error))


DIFF_FAULTS: dict[str, tuple[Any, str]] = {
    "draw": (_fault_in_draw, "drawing a diff"),
    "set_diff": (_fault_in_set_diff, "handing a diff view its diff"),
    "an attribute above reads": (_fault_in_what_above_reads, "reading a diff"),
}


@pytest.mark.parametrize("where", list(DIFF_FAULTS))
def test_anything_else_the_library_raises_under_a_diff_is_the_library_stopping(
    seam: Any, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """
    The diff view, and the model ``above`` reads, can raise what is not the
    library's own stop: a library that renamed a field of ``FileDiff`` is an
    ``AttributeError`` on every frame an approval is queued. ``diff()`` does
    not let it out: it is said at the cursor with its type, every changed row
    is unset for that frame, ``diff()`` still answers a height so that the
    pane does not draw half of each renderer, and the next frame the library
    is off. The view is held from an earlier frame, so that handing it a new
    model is reached.
    """
    said = _said_by_the_seam(monkeypatch)
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.diff(cache, "k", (approval.DiffText("a\n", "b\n", "x.py"),)))
    fault, doing = DIFF_FAULTS[where]
    got: list[float | None] = []
    with monkeypatch.context() as patched:
        fault(patched)
        frame(seam, lambda: got.append(typeset.diff(cache, "k", _THREE)))
        assert got[0] is not None
        assert said == [
            f"typeset text stopped: the library raised AttributeError while {doing}: "
            "'FileDiff' object has no attribute 'renamed'"
        ]
        assert cache.unbuilt("k") == 3
        assert typeset.can_draw(), "until the frame is over"
        typeset.pre_frame()
        assert not typeset.can_draw()
        assert "AttributeError" in (typeset.unavailable_reason() or "")
    assert typeset.load_fonts(BOLD)


def test_a_lone_surrogate_in_a_diff_row_stops_the_library_and_does_not_leave_the_seam(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    said = _said_by_the_seam(monkeypatch)
    cache = typeset.ViewCache()
    texts = (approval.DiffText("a\n", "b \udc80 c\n", "x.py"),)
    frame(seam, lambda: typeset.diff(cache, "k", texts))
    assert len(said) == 1 and "the library raised UnicodeEncodeError" in said[0]
    assert cache.unbuilt("k") == 2
    typeset.pre_frame()
    assert not typeset.can_draw()
    assert typeset.load_fonts(BOLD)


def test_what_the_seams_own_code_raises_under_a_diff_is_a_defect_and_is_not_caught(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = typeset.ViewCache()

    def broken() -> Any:
        raise ValueError("a slip in the seam")

    with monkeypatch.context() as patched:
        patched.setattr(diffs.themes, "diff_theme", broken)
        with pytest.raises(ValueError, match="a slip in the seam"):
            frame(seam, lambda: typeset.diff(cache, "k", _THREE))
    typeset.pre_frame()
    assert typeset.can_draw()


# -- adoption is per diff as well as per library --------------------------------------


def _one_row(text: str) -> tuple[approval.DiffText, ...]:
    return (approval.DiffText("", text + "\n", "x.py"),)


def test_a_row_with_a_character_the_code_face_lacks_is_off_its_columns_where_they_hold(
    columns_hold: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Why the rule below exists, on the library as it is. The measurement is of
    ASCII in the code face and holds at this em. A row with U+03C0 and U+0416
    in it, drawn by the diff view in a window: the ``x`` after the first is
    2 px right of its column and the one after the second a whole column
    right of its own, because the library set each letter from the
    proportional face at that face's advance. A tab does land on the grid.
    """
    monkeypatch.setattr(diffs, "unfit", lambda model: None)
    assert diffs.columns().holds
    _, plain = _drawn_row(columns_hold, "xxxxxxxxxx")
    assert {b - a for a, b in zip(plain, plain[1:], strict=False)} == {8.0}

    # The helper answers a left edge a character of the text, tabs counted:
    # the row's five glyphs are the last five.
    _, tabbed = _drawn_row(columns_hold, "x\tx\txx\tx")
    tabbed = tabbed[-5:]
    assert {(left - tabbed[0]) % 8.0 for left in tabbed} == {0.0}

    _, lefts = _drawn_row(columns_hold, "xπxЖx")
    # The third and fifth characters, two and four columns in: 16 and 32 px.
    assert [lefts[2] - lefts[0], lefts[4] - lefts[0]] == [18.0, 40.0], lefts


def test_where_columns_hold_a_diff_with_such_a_row_is_not_given_to_the_view_and_says_why(
    columns_hold: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Adoption is per diff as well as per library. A model with a row holding a
    character the code face has no glyph for is answered ``None``, which is a
    pane's cue for its own rows, under one line naming the characters; it
    holds no view and reports nothing unset, since the pane's rows are the
    diff. ASCII, an accent the face has, and a tab are given to the view.
    """
    said = _said_by_the_seam(monkeypatch)
    cache = typeset.ViewCache()
    got: dict[str, float | None] = {}

    def body() -> None:
        got["greek"] = typeset.diff(cache, "greek", _one_row("area = π * r ** 2  # Ж"))
        got["path"] = typeset.diff(cache, "path", _one_row("open('src/pаth.py')"))
        got["ascii"] = typeset.diff(cache, "ascii", _one_row("area = 3 * r ** 2"))
        got["accent"] = typeset.diff(cache, "accent", _one_row("café = '→'\tcolumn"))

    for _ in range(2):
        said.clear()
        frame(columns_hold, body)
        assert got["greek"] is None and got["path"] is None
        assert got["ascii"] is not None and got["accent"] is not None
        assert cache.view("greek") is None and cache.view("path") is None
        assert cache.view("ascii") is not None and cache.view("accent") is not None
        assert said == [
            "this diff is the pane's own rows: a row holds U+03C0, U+0416, which the code face "
            "has no glyph for, and the library would set what follows off its column",
            "this diff is the pane's own rows: a row holds U+0430, which the code face "
            "has no glyph for, and the library would set what follows off its column",
        ]
    assert cache.unbuilt("greek") == 0 and cache.unbuilt("ascii") == 0
    assert typeset.diff_renderer() == f"diffs are the library's: it {diffs.columns().why}"


def test_context_a_click_brings_in_is_asked_about_as_the_rows_before_it_were(
    columns_hold: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The rows of a model are what is asked about, and a click on collapsed
    context makes another model with more rows. A line of the file that was
    not a row, and holds a character the code face lacks, is one once it is
    expanded into view: the next frame the diff is the pane's own rows.
    """
    said = _said_by_the_seam(monkeypatch)
    before = ["# π is defined here"] + [f"line_{n} = {n}" for n in range(1, 40)]
    after = list(before)
    after[30] = "line_30 = 'changed'"
    texts = (approval.DiffText("\n".join(before) + "\n", "\n".join(after) + "\n", "x.py"),)
    cache = typeset.ViewCache()
    got: list[float | None] = []
    frame(columns_hold, lambda: got.append(typeset.diff(cache, "k", texts)))
    view = cache.view("k")
    assert got == [got[0]] and got[0] is not None and view is not None and said == []
    view.expand(0, 0, 100)
    assert "# π is defined here" in rows(view.diff, RowKind.CONTEXT)
    frame(columns_hold, lambda: got.append(typeset.diff(cache, "k", texts)))
    assert got[1] is None and cache.view("k") is None
    assert len(said) == 1 and "U+03C0" in said[0]


def test_where_columns_hold_a_row_longer_than_they_were_measured_for_is_the_panes_own(
    columns_hold: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The cell is held to the baked advance within a bound, and what that
    answers for ends at ``Columns.reach`` columns. A minified file is one row:
    one column past the reach it is the pane's own, and at the reach the view's.
    """
    said = _said_by_the_seam(monkeypatch)
    reach = diffs.columns().reach
    assert 40_000 < reach < 50_000
    cache = typeset.ViewCache()
    got: dict[str, float | None] = {}

    def body() -> None:
        got["at"] = typeset.diff(cache, "at", _one_row("m" * reach))
        got["past"] = typeset.diff(cache, "past", _one_row("m" * (reach + 1)))

    frame(columns_hold, body)
    assert got["at"] is not None and got["past"] is None
    assert said == [
        f"this diff is the pane's own rows: a row is {reach + 1:,} columns and the library's "
        f"columns were measured to hold for {reach:,}"
    ]


def test_where_columns_hold_a_fenced_diff_with_such_a_row_is_the_code_of_its_document(
    columns_hold: Any,
) -> None:
    honest = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-area = 3\n+area = 4\n"
    assert diffs.fence_diff("diff", honest) is not None
    assert diffs.fence_diff("diff", honest.replace("4", "π")) is None


# -- a parked call's model is made once, for every pane that draws it ------------------


def test_the_lines_a_diff_has_to_match_are_what_its_texts_do_not_share() -> None:
    """
    What a model costs follows the lines left once the common head and tail
    are set aside, and not the length of the file.
    """
    file = [f"line {n}" for n in range(1000)]

    def text(lines: list[str]) -> str:
        return "\n".join(lines) + "\n"

    one = list(file)
    one[500] = "changed"
    scattered = [line + (" x" if n % 37 == 0 else "") for n, line in enumerate(file)]
    grown = file[:400] + ["new"] * 30 + file[400:]
    assert diffs.work((approval.DiffText(text(file), text(one), "x"),)) == 1
    assert diffs.work((approval.DiffText(text(file), text(scattered), "x"),)) == 1000
    assert diffs.work((approval.DiffText(text(file), text(grown), "x"),)) == 30
    assert diffs.work((approval.DiffText(None, text(file), "x"),)) == 1000
    assert diffs.work((approval.DiffText(text(file), text(file), "x"),)) == 0
    assert diffs.work((approval.DiffText("a", "b", "1"), approval.DiffText("c\nd", "e", "2"))) == 3
    assert diffs.work(()) == 0


def test_two_panes_drawing_one_parked_call_make_its_model_once(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    DETAIL follows the inbox's cursor, so one call is drawn by two panes in
    every frame, each in a view of its own. Handed the call's texts, each made
    the model -- ``difflib`` over the change, twice. Handed the one ``Parked``
    the panes share, the model is made in the frame that first asks and is the
    same object in both views; nothing is made before a pane asks.
    """
    built: list[object] = []
    real = diffs.diff_model
    monkeypatch.setattr(
        diffs, "diff_model", lambda texts, **how: (built.append(texts), real(texts, **how))[1]
    )
    held = typeset.ParkedDiff(_THREE)
    assert held.work == 3 and built == []
    detail, inbox = typeset.ViewCache(), typeset.ViewCache()

    def body() -> None:
        typeset.diff(detail, "d", held, warning=False)
        typeset.diff(inbox, "i", held, warning=False, height_cap=200.0)

    for _ in range(3):
        frame(seam, body)
    assert len(built) == 1
    assert detail.view("d") is not inbox.view("i")
    assert detail.view("d").diff is inbox.view("i").diff is held.model(False)
    assert detail.unbuilt("d") == 0 == inbox.unbuilt("i")

    built.clear()
    frame(seam, lambda: (typeset.diff(detail, "d", _THREE), typeset.diff(inbox, "i", _THREE)))
    assert len(built) == 2, "what handing each pane the texts costs"


def test_a_pane_that_says_a_files_warnings_itself_has_them_left_out_of_the_model(
    tmp_path: Path,
) -> None:
    """
    A pane says the gate's prediction and "this file could not be read" above
    the view, from the record. Left in the model as well, the second was in
    the file's label, which the seam also sets above the view: said twice.
    """
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\xff\xfe\x00\x80binary")
    over = approval.diff_texts("Write", {"file_path": str(target), "content": "text\n"}) or ()
    assert "not readable as text" in typeset.diff_model(over).files[0].label
    assert typeset.diff_model(over, warned=False).files[0].label == str(target)

    edit = (approval.DiffText("a", "b", "x.py", will_not_apply="anchor not found in file"),)
    assert typeset.diff_model(edit).files[0].will_not_apply.startswith("may not apply")
    assert typeset.diff_model(edit, warned=False).files[0].will_not_apply == ""
