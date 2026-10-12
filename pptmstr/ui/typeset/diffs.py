"""
A diff of what a parked call would do: the model, and which fences are honest diffs.

**A diff is built from the texts a parked call carries, and nothing here reads
a file.** ``approval.diff_texts`` is the one place that decides what a call does
to a file; the driver runs it at park time and ``PendingApproval.texts`` holds
the answer. ``diff_model`` turns texts into the library's model, which is
immutable, so one model may be drawn by two panes.

**A fenced diff is a diff only when a diff view draws every line of it**
(``is_diff_fence``, ``accounted``). The library's reader keeps every line and its
view does not draw them all: a sentence above ``--- a/x``, a mail's message, a
line under a hunk that has had its count, are on the model and nowhere on
screen. On an approval surface that is text the recipient reads and the approver
does not. The library's own test for a fence is looser -- any info string that
begins with the word -- so the rule is pptmstr's and is asked first.

**``diff()`` sets a diff through the library's diff view, and a pane calls it
where the library that is running keeps a column** (``adopted``). A diff's
columns are content and its selection is what an operator copies, so the
question is put to the library as a measurement (``gate.columns``, requests
P21 and P22 of ``HANDOFF-advtext.md``) and not to a version number: the
advance a row is laid out at is the one ImGui has baked for the face, every
glyph of a long row is drawn on it, and a point on a drawn glyph is answered
as that glyph's character. Where that is so DETAIL and the inbox row draw an
approval's diff with ``diff()`` and a fenced ``diff`` block of a settled
document is hosted by the library's diff fences (``fence_diff``); where it is
not, each pane draws its own rows and a fence is the code of its document.

**It is not so on the library pptmstr is built against.** Measured in pptmstr
on 2026-10-12 (``scripts/measure_typeset_diff.py``, a window under Xvfb,
advtext ``38f5fac``, imtypeset ``c9f8902``): at a pixel height of 16 the view
lays a column out at 7.626 px against a baked 8, draws rows in steps of 7 and
8 px mixed, rows disagree at one column by up to 13 px, the 40th character of
a row is 15 px left of where ImGui puts it, and a point on a drawn glyph lands
on another character for 54 of the 64 glyphs of a 74-character row. The same
at 14, 18 and 20. At an even **em** (14, 16, 18, 20 px: Inconsolata's advance
is half an em) every one of those is exact, which is the configuration the
tests draw the adopted branch in; pptmstr's roles are sized by pixel height,
so no setting of the product reaches it.

**What an adopted diff view is held to**, since an approval is decided over it:

- *Nothing in a header row is only there.* The view's file header is one line
  cut at the view's edge and outside what the view scrolls (request P16), so
  what it says is said above the view, wrapped, always (``above``): a file's
  label and state, and what is wrong with it.
- *Only a model made from texts is drawn* (``draws``). One read back from a
  unified string keeps lines the view does not draw (``accounted``) and may
  hold a file shown as written, whose rows do not scroll; a parked call that
  carries no texts is its pane's own rows, which are that string line for line.
- *A row is never broken*, and one wider than the view is scrolled to by the
  view's own bar.
- *The library stopping is said and counted* (``ViewCache.unbuilt``), whether
  it stops in the draw or refuses to make the view.

The library is imported where a model is made and not when this module is, so
the package imports without it.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from ... import theme as ui_theme
from ...approval import DiffText
from ...model import PendingApproval
from . import gate, roles, themes, views
from .roles import Role
from .views import ViewCache

if TYPE_CHECKING:
    from advtext import Diff

ADOPT: Literal["measured", "always", "never"] = "measured"
"""
Who draws a diff where the library can draw at all. THE place to change it;
there is no setting.

- ``"measured"``: the library's diff view where its columns hold by
  measurement (``gate.columns``), each pane's own renderer where they do not.
- ``"never"``: each pane's own renderer.
- ``"always"``: the library's diff view whatever was measured, for looking at
  it. **What that costs on the library as it is** (advtext ``38f5fac``; the
  module docstring has the run): at a pixel height of 16 a column is laid out
  at 7.626 px and drawn in steps of 7 and 8, so rows disagree at one column by
  up to 13 px, a row of 40 characters ends 15 px left of the same text in
  ImGui's, and a press on 54 of the 64 glyphs of a 74-character row selects
  from a neighbouring character. An approver would be reading a diff whose
  columns drift and copying text other than what the highlight covers.
"""


def adopted() -> bool:
    """
    Whether a diff is drawn by the library's diff view in this frame: ``ADOPT``
    asked of the library that is running. A pane asks ``enabled(pane)`` as well,
    and ``draws`` of the call it holds. Never raises.

    Measured once for the diff role's size until the fonts are loaded again, so
    the answer does not change inside a frame; the frame a pane first asks in
    pays the measurement (``gate.columns`` has the cost).
    """
    if ADOPT == "never" or not gate.can_draw():
        return False
    return ADOPT == "always" or columns().holds


def columns() -> gate.Columns:
    """
    What was measured of a row set as a diff's rows are, at the diff role's size
    (``gate.columns``). Never raises.
    """
    try:
        return gate.columns(themes.diff_theme())
    except Exception as failure:
        return gate.Columns(False, f"gave no theme for the diff role ({failure})")


def renderer() -> str | None:
    """
    Who draws a diff and why, as one sentence for the Text menu and the settings
    dialog. ``None`` where the answer is already on screen as another
    sentence: the library cannot draw (``unavailable_reason``), or the operator
    has typeset text switched off.
    """
    if not gate.can_draw() or not roles.wanted():
        return None
    if ADOPT == "never":
        return "diffs.ADOPT is 'never': diffs are pptmstr's own rows"
    found = columns()
    if ADOPT == "always":
        return f"diffs.ADOPT is 'always': the library {found.why}"
    if found.holds:
        return f"diffs are the library's: it {found.why}"
    return f"diffs are pptmstr's own rows: the library {found.why}"


def draws(pending: PendingApproval) -> bool:
    """
    Whether a parked call's diff is one the diff view shows all of: it carries
    the texts the diff is made from.

    A record with a unified string and no texts is read back from the string
    (``approval_diff``), and that model can hold what the view draws nowhere
    or cannot scroll to: lines over a file's header, a hunk's section in a
    header row, a file shown as written. The string itself is every line, so
    such a call is its pane's own rows.
    """
    return pending.texts is not None


def above(model: Diff) -> tuple[tuple[str, bool], ...]:
    """
    What the diff view's header rows say that a pane must say above the view,
    a line each: the text, and whether it is a warning.

    A header row is one line, cut at the view's edge, and the view scrolls its
    code and not its headers, so the end of a long path and whatever warning
    follows it can be where nothing reaches them. The library as built against
    does not say whether a header fit (request P16), so these are said always.
    For each file, warnings first: the gate's prediction where the model still
    carries it (``diff(warning=True)``), lines read as nothing, and characters
    drawn as a placeholder, in the library's own words for them. Then the
    file's label with its state. A hunk's section is a header row too and
    follows its file. Counts are not repeated: they are short and lead the row.
    """
    from advtext import FileStatus

    state = {
        FileStatus.NEW: "  (new file)",
        FileStatus.DELETED: "  (deleted file)",
        FileStatus.BINARY: "  (binary file)",
        FileStatus.UNCHANGED: "  (no changes)",
    }
    out: list[tuple[str, bool]] = []
    for file in model.files:
        if file.will_not_apply:
            out.append((file.will_not_apply, True))
        if file.unread:
            count = len(file.unread)
            out.append(
                (f"{count} line{'s' if count != 1 else ''} not read as part of the diff", True)
            )
        if file.invisible_characters:
            out.append(("contains invisible or bidirectional control characters", True))
        elif file.control_characters:
            out.append(("contains control characters", True))
        said = (file.label or "(unnamed)") + state.get(file.status, "")
        if file.line_endings:
            said += f"  (line endings only: {file.line_endings})"
        out.append((said, False))
        out.extend((f"@@ {hunk.section}", False) for hunk in file.hunks if hunk.section)
    return tuple(out)


def is_diff_fence(info: str) -> bool:
    """
    Whether a fence's info string asks for a diff: ``diff`` or ``patch`` and
    nothing more.

    Narrower than the library's rule, which reads the first word and drops the
    rest. A fence opened ``diff IMPORTANT also delete tests`` is code under its
    whole info string, and not a diff under none of it.

    Necessary and not enough: the fence must also be one a diff view draws every
    line of (``accounted``).
    """
    return info.split() in (["diff"], ["patch"])


_HUNK_LINE = re.compile(r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$")


def accounted(text: str, model: Diff) -> bool:
    """
    Whether the diff view draws every line of ``text``, the unified text ``model``
    was read from.

    The library's reader drops no line and its view does not draw all it keeps: a
    sentence above a file header, a mail's message, whatever is over a bare
    ``---``, a line under a hunk that had had its count -- each is kept on the
    model (``FileDiff.header``) and is nowhere on screen. On an approval surface
    that sentence is one the recipient reads and the approver does not.

    Decided from the model, by counting, and not from a list of those shapes:
    what the view draws of a model is known -- a file's label, each hunk's ``@@``
    line with its section, each row behind its marker, each line of a file shown
    as written -- and every line of the text with ink in it must be one of those,
    each drawn line answering for one written line. A line is drawn when it is

    - a row's: its marker and its text, or the text alone for context a writer
      did not mark;
    - a hunk's ``@@`` line whose section is the hunk's. The numbers are not
      compared: the view writes the counts it counted;
    - ``--- path`` or ``+++ path`` where the path is the file's label (under ``a/``
      or ``b/`` or not) or ``/dev/null``;
    - a line of a file shown as written (``FileDiff.raw``).

    Anything else is not. That takes in what ``git diff`` prints -- its ``diff
    --git`` line, and an ``index`` line whose hashes and mode are on no row -- so
    a fence holding one is set as code, and so is a path with a timestamp after
    it, and a rename written as two different paths. Order is not compared.
    """
    shown: dict[str, int] = {}

    def draws(line: str) -> None:
        shown[line] = shown.get(line, 0) + 1

    for file in model.files:
        for line in file.raw:
            draws(line.rstrip("\r"))
        if file.dialect:
            continue
        label = file.label
        for mark, path in (("--- ", file.old_path), ("+++ ", file.new_path)):
            bare = path[2:] if path[:2] in ("a/", "b/") else path
            if path and (path == "/dev/null" or label in (path, bare)):
                draws(mark + path)
        for hunk in file.hunks:
            draws("@@ " + hunk.section)
            for row in hunk.rows:
                draws(row.kind.value + row.text)
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        forms = [line, " " + line]
        if line.startswith("@@"):
            found = _HUNK_LINE.match(line)
            forms.append("@@ " + (found[1] if found else line.strip("@ ")))
        elif line.startswith("\\"):
            forms.append("\\" + line[1:].lstrip())
        for form in forms:
            if shown.get(form, 0) > 0:
                shown[form] -= 1
                break
        else:
            return False
    return True


def fence_diff(info: str, text: str) -> Diff | None:
    """
    The model of a fenced block the diff view is to draw, or ``None`` for a
    fence that is the code of its document: pptmstr's rule for a fence, whole.

    A diff view draws it when the info string is the one word
    (``is_diff_fence``), the view draws every line of the text (``accounted``),
    and no file of it is shown as written: those lines are header rows, which
    the view cuts at its edge and does not scroll. Pure, and asked of a
    block's own text both where the block is dressed and where the view asks
    who draws it, so the two cannot disagree.
    """
    if not is_diff_fence(info) or not text.strip():
        return None
    from advtext import Diff

    model = Diff.from_unified(text if text.endswith("\n") else text + "\n")
    if not accounted(text, model) or any(file.dialect or file.raw for file in model.files):
        return None
    return model


def fence_headers(text: str) -> tuple[str, ...]:
    """
    The lines of an honest fenced diff that the view draws as header rows, as
    written: a file's ``---`` and ``+++`` and a hunk's ``@@`` line that names a
    section. They are set above the hosted view as well (``above`` has why).
    """
    out = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line.startswith(("--- ", "+++ ")):
            out.append(line)
        elif line.startswith("@@"):
            found = _HUNK_LINE.match(line)
            if (found[1] if found else line.strip("@ ")).strip():
                out.append(line)
    return tuple(out)


def diff_model(texts: Sequence[DiffText]) -> Diff:
    """
    The diff of some before/after texts: one file of it per part.

    From the texts and not from a unified string. ``difflib.unified_diff`` over lines
    kept with their ends writes a removed and an added line as one physical line,
    ``-foo+bar``, whenever a text has no closing newline -- the normal case for an
    Edit's strings -- and no reader can take that apart: the view would show one
    removed line and no added one (pinned in ``tests/test_typeset_diffs.py``).

    What a part says about itself is carried onto its file. ``will_not_apply`` is
    the gate's reading of the file at the moment the call parked, a prediction, and
    reaches the view's header as one: ``may not apply: <reason>``. ``unreadable`` on
    a Write -- a file that exists and could not be shown, drawn as all-new -- goes
    in the label, because the model has no field for it and without the words it is
    indistinguishable from a file being created.

    Pure. Cost is ``difflib``'s on what is left after the common head and tail are
    set aside: one contiguous edit is cheap in any file; scattered changes were
    200 ms across 5,000 lines and 960 ms across 20,000 on the library's model as
    it was on 2026-10-08, and have not been timed since.
    Immutable, so one model may be drawn by two panes -- each in a view of its own.
    """
    from advtext import Diff

    files: list[Any] = []
    for part in texts:
        label = part.label or "(no path)"
        if part.unreadable and part.will_not_apply is None:
            label = f"{label}  (existing file is not readable as text; shown as new)"
        hedged = f"may not apply: {part.will_not_apply}" if part.will_not_apply else ""
        built = Diff.from_texts(part.before, part.after, label, will_not_apply=hedged)
        files.extend(built.files)
    return Diff(tuple(files))


_FROM_UNIFIED = "(read back from the unified text: may be incomplete)"


def approval_diff(pending: PendingApproval) -> Diff | None:
    """
    The diff a parked call would make, from the texts it carries. No file is read.

    ``None`` when the call has nothing diff-shaped. A record built without texts but
    with a unified string -- a construction site that predates ``texts`` -- is read
    from that string rather than shown as having no diff: lossy in the way
    ``diff_model`` describes, and better than hiding a change from its approver.
    Each file read that way says so in its header (``_FROM_UNIFIED``), because a
    line the reader could not take apart is not in it.
    """
    if pending.texts is not None:
        return diff_model(pending.texts)
    if pending.diff:
        from advtext import Diff

        read = Diff.from_unified(pending.diff)
        return Diff(
            tuple(
                replace(file, label=f"{file.label or '(no path)'}  {_FROM_UNIFIED}")
                for file in read.files
            )
        )
    return None


def diff(
    cache: ViewCache,
    key: Hashable,
    source: PendingApproval | Diff | Sequence[DiffText] | None,
    *,
    side_by_side: bool = False,
    height_cap: float | None = None,
    width: float | None = None,
    warning: bool = True,
) -> float | None:
    """
    A diff at the cursor, set by the library, or ``None`` when there is nothing
    diff-shaped to show. The height claimed.

    **A pane calls this where ``adopted()`` and, for a parked call, ``draws``
    say so**, and draws its own rows otherwise and when this answers ``None``.

    **What the view's header rows say is set above it first, wrapped to the
    view's width** (``above``), always: a header row is cut at the view's edge.

    ``source`` is a parked call, the texts of one (``approval.diff_texts``' answer,
    for arguments the operator is editing), or a ``Diff`` already made. The
    model is made when the source is another object than the one held, in this
    call: ``diff_model`` says what that costs for a large change.

    ``height_cap`` puts a diff taller than it in a child of the view's own,
    which scrolls; a long row is scrolled to sideways by the view's own bar.
    ``warning=False`` leaves the gate's prediction out of each file's header,
    for a pane that says it above the view. ``side_by_side`` is two columns.
    """
    from advtext import Diff, DiffLayout, TypesetDiffView, TypesetUnavailable

    im = views._frame()
    entry = cache._held(key, "diff")
    frame = im.get_frame_count()
    if entry.frame == frame:
        return views._said(im, f"typeset: {key!r} drawn twice in one frame", ui_theme.P.danger)
    given: Any = source
    if isinstance(source, Sequence):
        # Never the caller's own list, which is theirs to change.
        given = tuple(source)
    held = entry.token
    # A model is the same source only when it is the same one: two that compare
    # equal may differ in the context a click would expand. Texts and a parked
    # call are what they hold.
    if not (
        isinstance(held, tuple)
        and len(held) == 2
        and held[1] == warning
        and (held[0] is given or (not isinstance(given, Diff) and held[0] == given))
    ):
        model = _model(given)
        if model is not None and not warning:
            model = Diff(tuple(replace(file, will_not_apply="") for file in model.files))
        entry.token = (given, warning)
        entry.model = model
        # The view is kept: its scroll position is the reader's place.
        if entry.view is not None and model is not None and model.files:
            entry.view.set_diff(model)
    model = entry.model
    if model is None or not model.files:
        if entry.view is not None:
            entry.view.close()
            entry.segments = []
        return None
    entry.frame = frame
    top = im.get_cursor_pos().y
    layout = DiffLayout.SIDE_BY_SIDE if side_by_side else DiffLayout.UNIFIED
    wide = views._width(im, width)
    im.push_text_wrap_pos(im.get_cursor_pos_x() + wide)
    for said, warns in above(model):
        im.text_colored((ui_theme.P.warn if warns else ui_theme.P.accent).vec4, said)
    im.pop_text_wrap_pos()
    try:
        view = entry.view
        if view is None:
            view = TypesetDiffView(model, themes.diff_theme(), layout=layout)
            entry.segments = [views._Segment((), view)]
        if view.layout is not layout:
            view.set_layout(layout)
        view.height_cap = height_cap
        view.draw(palette=themes.palette(), width_px=wide)
        entry.missing = 0
    except TypesetUnavailable as stopped:
        gate.stopped(stopped)
        entry.missing = sum(file.added + file.removed for file in model.files) or 1
        views._said(im, f"typeset text stopped: {stopped}", ui_theme.P.warn)
    entry.height = float(im.get_cursor_pos().y - top)
    return entry.height


def _model(source: PendingApproval | Diff | tuple[DiffText, ...] | None) -> Diff | None:
    """
    The library's model of whatever ``diff()`` was handed.
    """
    if source is None:
        return None
    if isinstance(source, PendingApproval):
        return approval_diff(source)
    if isinstance(source, tuple):
        return diff_model(source)
    return source


def diff_row_height() -> float:
    """
    The height of one row of a ``diff()``, in pixels: to size a cap.
    """
    return roles.pitch(Role.DIFF)
