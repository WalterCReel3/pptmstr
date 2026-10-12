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
  it stops in the draw or refuses to make the view, and whatever it raised
  (``gate.into_library``).
- *A view that set less than it was handed is not the diff* (``gate.noted``):
  a draw that answered false, or an edit the library cut, is every changed row
  unset for that frame and the pane's own rows from the next.

**And adoption is per diff as well as per library** (``unfit``). The
measurement is of a row of ASCII no longer than itself, and two things a diff
can hold are outside it: a character the code face lacks, which the library
sets from another face at that face's advance, so every column to its right is
off the grid; and a row longer than the measured cell is known to hold for
(``gate.Columns.reach``). A diff with either is its pane's own rows, under one
line saying which. The first is also where the two renderers differ in what
they show: for U+0430 in a path the library draws the Cyrillic letter from the
proportional face, a lookalike of ``a``, and the chrome's font, which has no
Cyrillic, draws its replacement glyph (measured 2026-10-12). Tabs are the
library's question and are measured with the columns: stops every four cells.

**A parked call's model is made once, for every pane that draws it**
(``Parked``), in the frame that first asks, and not at all unasked where that
frame would stall (``work``; the panes hold it to ``review.SETTLED_LINES``).
Measured in pptmstr on 2026-10-12 (headless, no rasteriser, load average 2 to
5, lines of 60 characters). The model is ``difflib``'s cost on what is left
after the common head and tail: 5.7 ms for changes scattered through 1,200
lines, 87 ms through 5,000 and 1.5 s through 20,000, where one block replaced
in 20,000 is 13 ms; the view's first draw is 4 to 97 ms for 1,200 to 20,000
rows; and a model handed to two panes as texts is made by each. At the bound,
both panes drawing the call in the frame that first asks: 13 ms for a new
file of 2,000 lines, 24 to 33 ms for changes scattered through 2,000, 102 ms
for 2,000 lines replaced inside 20,000 and 131 ms with every one of 2,000
lines changed, of which the second pane is 3 to 6 ms. Past it the pane draws
its own rows, which were made when the call parked, and offers the view.

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

_READING = gate.into_library("reading a diff")
_MAKING = gate.into_library("making a diff view")
_HANDING = gate.into_library("handing a diff view its diff")
_DRAWING = gate.into_library("drawing a diff")
"""
Where this module calls into the library while a pane draws (``gate.into_library``).
Around the calls and nothing else.
"""

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
    with _READING:
        files = tuple(model.files)
    for file in files:
        with _READING:
            refusal = str(file.will_not_apply)
            unread = len(file.unread)
            invisible = bool(file.invisible_characters)
            control = bool(file.control_characters)
            label, status, endings = str(file.label), file.status, str(file.line_endings)
            sections = [str(hunk.section) for hunk in file.hunks if hunk.section]
        if refusal:
            out.append((refusal, True))
        if unread:
            out.append(
                (f"{unread} line{'s' if unread != 1 else ''} not read as part of the diff", True)
            )
        if invisible:
            out.append(("contains invisible or bidirectional control characters", True))
        elif control:
            out.append(("contains control characters", True))
        said = (label or "(unnamed)") + state.get(status, "")
        if endings:
            said += f"  (line endings only: {endings})"
        out.append((said, False))
        out.extend((f"@@ {section}", False) for section in sections)
    return tuple(out)


def unfit(model: Diff) -> str | None:
    """
    Why this diff is not one the diff view is given, as the words after "this
    diff is the pane's own rows:", or ``None`` for one it is.

    The columns were measured over a row of ASCII (``gate.columns``), and that
    answers for a diff only as far as its rows are like that one:

    - *A row holding a character the code face lacks.* The library sets it
      from another face at that face's advance, and everything right of it on
      that row is off the grid: measured at an em of 16, ``c`` after ``ab π``
      is 2 px right of its column, and what follows a ``Ж`` 8 px. Asked of
      the library the way its own diff view asks (``gate.code_lacks``).
    - *A row longer than the measured cell holds for* (``gate.Columns.reach``).

    Of the rows the model holds: what a click on collapsed context brings in
    is another model, and ``diff()`` asks again of that one. One pass over the
    rows, and one call into the library for each distinct character above
    ASCII. Raises what the library raises.

    ``None`` wherever the columns do not hold at all: the view is then drawn
    only because ``ADOPT`` says so, to be looked at, and no row of it is on a
    grid for these to be off.
    """
    found = columns()
    if not found.holds:
        return None
    reach = found.reach
    longest = 0
    above_ascii: set[str] = set()
    with _READING:
        for file in model.files:
            for hunk in file.hunks:
                for row in hunk.rows:
                    text = row.text
                    longest = max(longest, len(text))
                    if not text.isascii():
                        above_ascii.update(text)
        lacking = gate.code_lacks("".join(sorted(above_ascii)))
    if lacking:
        named = ", ".join(f"U+{ord(char):04X}" for char in lacking[:4])
        more = f" and {len(lacking) - 4} more" if len(lacking) > 4 else ""
        return (
            f"a row holds {named}{more}, which the code face has no glyph for, "
            "and the library would set what follows off its column"
        )
    if longest > reach:
        return (
            f"a row is {longest:,} columns and the library's columns were measured "
            f"to hold for {reach:,}"
        )
    return None


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
    the view cuts at its edge and does not scroll. And, where the library is
    drawing, when it is a diff the view is given at all (``unfit``). Asked of
    a block's own text both where the block is dressed and where the view
    asks who draws it, so the two cannot disagree.
    """
    if not is_diff_fence(info) or not text.strip():
        return None
    from advtext import Diff

    with _READING:
        model = Diff.from_unified(text if text.endswith("\n") else text + "\n")
    if not accounted(text, model) or any(file.dialect or file.raw for file in model.files):
        return None
    if gate.can_draw() and unfit(model) is not None:
        # Code of its document, as it is wherever a diff is not the library's.
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


def diff_model(texts: Sequence[DiffText], *, warned: bool = True) -> Diff:
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

    ``warned=False`` leaves both of those out, for a pane that says them itself
    above the view (``inbox.refusals``, ``inbox.shown_as_new``): the label is
    set above the view as well, and would be the pane's sentence over again.

    Pure. Cost is ``difflib``'s on what is left after the common head and tail are
    set aside (``work``): one contiguous edit is cheap in any file, and scattered
    changes are not -- the module docstring has the times.
    Immutable, so one model may be drawn by two panes -- each in a view of its own.
    """
    from advtext import Diff

    files: list[Any] = []
    for part in texts:
        label = part.label or "(no path)"
        if warned and part.unreadable and part.will_not_apply is None:
            label = f"{label}  (existing file is not readable as text; shown as new)"
        hedged = f"may not apply: {part.will_not_apply}" if warned and part.will_not_apply else ""
        with _READING:
            built = Diff.from_texts(part.before, part.after, label, will_not_apply=hedged)
            files.extend(built.files)
    with _READING:
        return Diff(tuple(files))


def work(texts: Sequence[DiffText]) -> int:
    """
    How many lines of some texts a diff of them has to match: for each part,
    what is left of the longer of its two texts once the lines they share at
    the start and at the end are set aside. What ``diff_model`` costs follows
    this and not the length of the file: one line changed in 20,000 is 1.

    No library and no ``difflib``: one pass over the lines from each end.
    """
    total = 0
    for part in texts:
        before = (part.before or "").splitlines()
        after = (part.after or "").splitlines()
        short = min(len(before), len(after))
        head = 0
        while head < short and before[head] == after[head]:
            head += 1
        tail = 0
        while tail < short - head and before[-1 - tail] == after[-1 - tail]:
            tail += 1
        total += max(len(before), len(after)) - head - tail
    return total


class Parked:
    """
    One parked call's diff as the seam holds it: made once, on first asking,
    for every pane that draws the call. What ``diff()`` is handed for one.

    A pane keeps it for as long as the call is parked, where both panes reach
    it (``review.ReviewState.diffs``), and hands it to ``diff()``; this
    package keeps no table of calls. ``work`` is known when it is made and
    costs no ``difflib``: what the model would cost, for a pane to ask before
    it asks for the model (``work``; ``review.SETTLED_LINES`` is the bound
    the panes hold it to).

    The model is made inside ``diff()``, where what the library raises is
    caught, and not here.
    """

    __slots__ = ("_models", "texts", "work")

    def __init__(self, texts: Sequence[DiffText]) -> None:
        self.texts: tuple[DiffText, ...] = tuple(texts)
        self.work: int = work(self.texts)
        self._models: dict[bool, Diff] = {}

    def model(self, warned: bool = True) -> Diff:
        """
        The library's model of the texts (``diff_model``), the same object at
        every asking. Raises what the library raises.
        """
        made = self._models.get(warned)
        if made is None:
            made = self._models[warned] = diff_model(self.texts, warned=warned)
        return made


_FROM_UNIFIED = "(read back from the unified text: may be incomplete)"


def approval_diff(pending: PendingApproval, *, warned: bool = True) -> Diff | None:
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
        return diff_model(pending.texts, warned=warned)
    if pending.diff:
        from advtext import Diff

        with _READING:
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
    source: PendingApproval | Parked | Diff | Sequence[DiffText] | None,
    *,
    side_by_side: bool = False,
    height_cap: float | None = None,
    width: float | None = None,
    warning: bool = True,
) -> float | None:
    """
    A diff at the cursor, set by the library, or ``None`` when the library's
    view is not what draws it. The height claimed.

    **A pane calls this where ``adopted()`` and, for a parked call, ``draws``
    say so**, and draws its own rows otherwise and when this answers ``None``:
    there is nothing diff-shaped to show, or the diff is one the view is not
    given (``unfit``), or the view set less of it than it was handed on the
    frame before. In the last two a line at the cursor says which, over the
    rows the pane then draws.

    **What the view's header rows say is set above it first, wrapped to the
    view's width** (``above``), always: a header row is cut at the view's edge.

    ``source`` is a parked call as its panes hold it (``Parked``: one model,
    made here on first asking, for both of them), a parked call itself, the
    texts of one (``approval.diff_texts``' answer, for arguments the operator
    is editing), or a ``Diff`` already made. Anything but a ``Parked`` or a
    ``Diff`` is made into a model when the source is another object than the
    one held, in this call: the module docstring says what that costs.

    ``height_cap`` puts a diff taller than it in a child of the view's own,
    which scrolls; a long row is scrolled to sideways by the view's own bar.
    ``warning=False`` leaves out of each file's header what a pane says above
    the view itself: the gate's prediction, and that a file could not be
    read. ``side_by_side`` is two columns.

    **What is reported unset** (``ViewCache.unbuilt``): every changed row when
    the library stopped under the diff, whatever it raised and wherever, and
    when the view's draw set less than it was handed; none otherwise.
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
    top = im.get_cursor_pos().y
    try:
        noted = gate.noted()
        held = entry.token
        # A model is the same source only when it is the same one: two that
        # compare equal may differ in the context a click would expand. So is
        # a parked call as its panes hold it. Texts and a parked call itself
        # are what they hold.
        same = (
            isinstance(held, tuple)
            and len(held) == 2
            and held[1] == warning
            and (held[0] is given or (not isinstance(given, (Diff, Parked)) and held[0] == given))
        )
        if not same:
            model = _model(given, warning)
            entry.token = (given, warning)
            entry.model = model
            entry.unset = entry.scanned = None
            # The view is kept: its scroll position is the reader's place.
            if entry.view is not None and model is not None:
                with _HANDING:
                    if model.files:
                        entry.view.set_diff(model)
        model = entry.model
        with _READING:
            empty = model is None or not model.files
        if not empty and entry.unset is None:
            # What the view shows: the model handed over, or what a click on
            # collapsed context made of it since.
            shown = model
            if entry.view is not None:
                with _READING:
                    shown = entry.view.diff
            if entry.scanned is not shown:
                entry.scanned = shown
                entry.unset = unfit(shown)
        if empty or entry.unset is not None:
            if entry.view is not None:
                with _HANDING:
                    entry.view.close()
                entry.segments = []
            if not empty:
                views._said(im, f"this diff is the pane's own rows: {entry.unset}")
            return None
        entry.frame = frame
        layout = DiffLayout.SIDE_BY_SIDE if side_by_side else DiffLayout.UNIFIED
        wide = views._width(im, width)
        said = above(model)
        im.push_text_wrap_pos(im.get_cursor_pos_x() + wide)
        for line, warns in said:
            im.text_colored((ui_theme.P.warn if warns else ui_theme.P.accent).vec4, line)
        im.pop_text_wrap_pos()
        view = entry.view
        theme, palette = themes.diff_theme(), themes.palette()
        if view is None:
            with _MAKING:
                view = TypesetDiffView(model, theme, layout=layout)
            entry.segments = [views._Segment((), view)]
        with _DRAWING:
            if view.layout is not layout:
                view.set_layout(layout)
            view.height_cap = height_cap
            whole = bool(view.draw(palette=palette, width_px=wide))
        entry.missing = 0
        now = gate.noted()
        short = (
            f"said: {now}" if now is not noted and now else None if whole else views._NOT_ALL_SET
        )
        if short is not None:
            # Less is on screen than was handed over: no row is answered for
            # in this frame, and the next is the pane's own rows.
            entry.missing = _changed(model)
            entry.unset = f"the library {short}"
            views._said(im, f"typeset: the library {short}", ui_theme.P.warn)
    except TypesetUnavailable as stopped:
        gate.stopped(stopped)
        entry.frame = frame
        entry.missing = _changed(entry.model)
        views._said(im, f"typeset text stopped: {stopped}", ui_theme.P.warn)
    entry.height = float(im.get_cursor_pos().y - top)
    return entry.height


def _changed(model: Diff | None) -> int:
    """
    How many rows a diff changes, and never none: what is reported unset when
    the diff is not on screen. Of a model that cannot be read, one.
    """
    try:
        return sum(file.added + file.removed for file in model.files) if model else 1
    except Exception:
        return 1


def _model(
    source: PendingApproval | Parked | Diff | tuple[DiffText, ...] | None, warning: bool
) -> Diff | None:
    """
    The library's model of whatever ``diff()`` was handed, with or without
    what a pane says above the view itself.
    """
    from advtext import Diff

    if source is None:
        return None
    if isinstance(source, Parked):
        return source.model(warning)
    if isinstance(source, PendingApproval):
        return approval_diff(source, warned=warning)
    if isinstance(source, tuple):
        return diff_model(source, warned=warning)
    if warning:
        return source
    with _READING:
        return Diff(tuple(replace(file, will_not_apply="") for file in source.files))


def diff_row_height() -> float:
    """
    The height of one row of a ``diff()``, in pixels: to size a cap.
    """
    return roles.pitch(Role.DIFF)
