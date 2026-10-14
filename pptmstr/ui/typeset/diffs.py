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

**It is so on the library pptmstr pins, at every size the product sets a diff
at, with nothing asked of the view** (``gate.diff_view``, which makes both the
view a pane draws and the view that is measured, and passes no ``cell``: the
library lays a diff out on the whole-pixel cell ImGui draws the code face at
by itself, from imtypeset 0.4). Measured in pptmstr on 2026-10-13
(``scripts/measure_typeset_diff.py``, a window under Xvfb, imscribe ``0.2.0b1``
and the imtypeset ``0.4.1.192900`` wheel, the chrome font and the vendored
bold as the application loads them, load average 3.5 to 4.2, vertices read
and no pixel), with the diff role at each pixel height from 12 to 24, which is
every text scale and role step there is: adjacent columns are one step apart
on every row, and the step is the advance ImGui has baked for the chrome font
at that height (6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12); rows agree at
every column; characters 10, 20, 40 and 74 of a row are on ImGui's pens; every
glyph is on its cell; and a point a quarter and three quarters into each of
the 64 glyphs of a 74-character row is answered as the positions either side
of it.

**The cell costs nothing in glyph size.** Same run, numbers and nothing about
how it looks, which nobody has judged:

- *The em* drawn is the em asked for at every height (16 px: 15.253), and the
  glyphs come from the raster of the pixel height asked for.
- *A glyph's bitmap* is the chrome font's at that height: ``0`` is 6 x 9,
  7 x 9, 8 x 11 and 9 x 12 px at 14, 16, 18 and 20 px, and ``m`` 7 x 7, 8 x 7,
  9 x 8 and 9 x 9, in the view and in the chrome alike, and so at every height
  from 12 to 24.
- *The pitch* is the role's (``roles.pitch``): 16, 18, 20 and 23 px. A pane's
  own rows are the chrome font's line and the item spacing, 21 px, whatever
  the diff role's size is: they do not follow the role.
- *What crosses its row.* No printable ASCII glyph, at any height from 12 to
  24. U+2502, which the code face has, reaches 1 to 3 px above its row and 0
  to 2 below (1 and 2 at 16; 3 and 2 at 24).

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
sets in whole cells borrowed from another face, two for a Greek or a Cyrillic
letter, while its view counts one cell a character, so the end of such a row
is past what the view scrolls to (imscribe's A7, open on its side); and a row
longer than the measured cell is known to hold for
(``gate.Columns.reach``). A diff with either is its pane's own rows, under one
line saying which. The first is also where the two renderers differ in what
they show: for U+0430 in a path the library draws the Cyrillic letter from the
proportional face, a lookalike of ``a``, and the chrome's font, which has no
Cyrillic, draws its replacement glyph (measured 2026-10-12). Tabs are the
library's question and are measured with the columns: stops every four cells.

**A character that draws as nothing, or reorders what is beside it, is named
by its code point over the diff, whoever draws the diff** (``hidden``,
``hidden_warning``): a C0 or C1 control, a format character (the
bidirectional controls, a zero-width space, a byte-order mark), a line or
paragraph separator. Neither renderer shows one as what it is. Measured in
pptmstr on 2026-10-12 (imscribe ``bb240c3``, imtypeset ``2fd705e``, the chrome
font at 16 px, headless, quads read and no pixel): the library's view sets
each as U+FFFD, which the code face has, in one cell on the grid, so such a
character is no reason to refuse a diff to the view; ImGui draws U+202E,
U+FEFF, U+0007 and U+0085 with the quad it draws U+0430 and U+4E2D with, its
fallback glyph, and U+200B as an advance with no ink, so on a pane's own rows
a bidirectional override is one more letter the font lacks and a zero-width
space is a space. The sentence is the same one on either renderer and is what
a pane says before a file's name: ``above`` has it for the view, and ``diff()``
says it over the line that hands a diff back to its pane. ``own_rows_warning``
is the same sentence of a call's unified string, for a pane that draws its own
rows without having asked ``diff()``: with typeset text off, where no diff is
the library's, and past the bound on what one frame is asked to match
(``inbox.hidden_in_rows``, which the inbox row and DETAIL both say it from).

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
from collections.abc import Hashable, Iterable, Iterator, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Literal

from ... import theme as ui_theme
from ...approval import DiffText
from ...model import PendingApproval
from . import gate, roles, themes, views
from .roles import Role
from .views import ViewCache

if TYPE_CHECKING:
    from imscribe import Diff

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
  it. It differs from ``"measured"`` only where the measurement says no, and
  **what it costs there** is what the measurement found. On the library's
  default cell (the module docstring has the run) that is, at a pixel height
  of 16, rows that disagree at one column by up to 13 px, a row of 40
  characters ending 15 px left of the same text in ImGui's, and a press on
  54 of the 64 glyphs of a 74-character row selecting from a neighbouring
  character: an approver reading a diff whose columns drift and copying text
  other than what the highlight covers. Nor is a diff asked about
  (``unfit``), so a row with a character the code face lacks is drawn off
  its columns.
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
    follows it can be where nothing reaches them. **These are said always, and
    not only where a header was cut.** The library can say whether a file's
    header fit (``TypesetDiffView.headers``, request P16), and that answer is
    not used: it covers a file's row and not a hunk's section; under a height
    cap it does not know the width before the draw, since the child's
    scrollbar narrows it; and a warning that is above the view at one width of
    the pane and inside a row at another is one an approver has to find twice.
    For each file, warnings first, and first of them the characters the view
    draws as a placeholder, by code point (``hidden_warning``), with any such
    character in the file's label, which the library does not ask about; then the
    gate's prediction where the model still carries it
    (``diff(warning=True)``) and lines read as nothing, in the library's own
    words for them. Then the file's label with its state. A hunk's section is a header row too and
    follows its file. Counts are not repeated: they are short and lead the row.
    """
    from imscribe import FileStatus

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
            # The library's own answer, which it keeps, says whether there is
            # anything to name: the rows are read here only where there is.
            lines = tuple(_lines(file)) if file.control_characters else ()
            label, status, endings = str(file.label), file.status, str(file.line_endings)
            sections = [str(hunk.section) for hunk in file.hunks if hunk.section]
        marked = hidden_warning((label, *lines))
        if marked is not None:
            out.append((marked, True))
        if refusal:
            out.append((refusal, True))
        if unread:
            out.append(
                (f"{unread} line{'s' if unread != 1 else ''} not read as part of the diff", True)
            )
        said = (label or "(unnamed)") + state.get(status, "")
        if endings:
            said += f"  (line endings only: {endings})"
        out.append((said, False))
        out.extend((f"@@ {section}", False) for section in sections)
    return tuple(out)


_C0 = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_INVISIBLE = frozenset(
    "\u00ad\u061c\u06dd\u070f\u0890\u0891\u08e2\u180e\ufeff\U000110bd\U000110cd\U000e0001"
).union(
    *(
        map(chr, range(first, last + 1))
        for first, last in (
            (0x0600, 0x0605),
            (0x200B, 0x200F),
            (0x2028, 0x202E),
            (0x2060, 0x2064),
            (0x2066, 0x206F),
            (0xFFF9, 0xFFFB),
            (0x13430, 0x1343F),
            (0x1BCA0, 0x1BCA3),
            (0x1D173, 0x1D17A),
            (0xE0020, 0xE007F),
        )
    )
)
"""
Unicode's format characters (Cf) and its line and paragraph separators (Zl,
Zp), written out as the library writes them out (``imscribe.diff``): a pane's
own rows are answered for where there is no library to ask, and Python's own
table is whichever Unicode the interpreter was built with -- 3.11's has no
U+13439, which the library's list has. ``tests/test_typeset_diffs.py`` holds
this list and the library's equal at every code point.
"""

_BIDIRECTIONAL = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
"""
The characters that reorder the letters beside them: the marks, the embeddings
and overrides, the isolates and what ends them. Named first in a warning, so
no count of other characters stands between an approver and one of these.
"""

_NAMED = 12
"""
How many code points of a class one warning spells out before it counts the rest.
"""

PLACEHOLDER = "\ufffd"
"""
What the library's diff view sets in place of a character ``hidden`` answers,
and of a lone surrogate (``imscribe.ui.typeset_diff_view.CONTROL_PLACEHOLDER``;
``tests/test_typeset_diffs.py`` holds the two equal).
"""


def _hides(char: str) -> str | None:
    """
    ``"control"`` for a C0 control other than a tab or a newline, DEL, or a C1
    control; ``"invisible"`` for a format character (Unicode's Cf) or a line or
    paragraph separator (Zl, Zp); ``None`` for any other character.

    The library's classes (``imscribe.diff.has_control``, ``has_invisible``;
    ``_INVISIBLE`` has why they are written again here).
    """
    if char < "\x7f":
        return "control" if char < " " and char not in "\t\n" else None
    if char <= "\x9f":
        return "control"
    return "invisible" if char in _INVISIBLE else None


def hidden(lines: Iterable[str]) -> tuple[str, ...]:
    """
    The characters of some lines of a diff that draw as nothing or as a
    placeholder and are not what the text beside them looks like, each once:
    the bidirectional controls first, then the rest in code-point order.

    A tab is not one, nor a newline, nor a lone surrogate, which is neither
    renderer's to draw (``unfit``). Pure, and no library: one pass over the
    lines.
    """
    seen: set[str] = set()
    for line in lines:
        if not line.isascii():
            seen.update(line)
        elif not line.isprintable():
            seen.update(_C0.findall(line))
    found = [char for char in seen if _hides(char) is not None]
    return tuple(sorted(found, key=lambda char: (char not in _BIDIRECTIONAL, char)))


def hidden_warning(lines: Iterable[str]) -> str | None:
    """
    The one sentence that says some lines of a diff hold characters an
    approver cannot see for what they are, and which: ``None`` where they
    hold none (``hidden``).

    **The same sentence whoever draws the diff**, since what it warns of is in
    the text and not in the renderer: said above the library's view for each
    file (``above``), over a diff handed back to its pane (``diff()``), and by
    a pane over rows of its own (``own_rows_warning``). The words are the
    library's for its header row, with the code points after them, as many as
    ``_NAMED`` of each class and the bidirectional ones first.
    """
    found = hidden(lines)
    if not found:
        return None
    unseen = [char for char in found if _hides(char) == "invisible"]
    controls = [char for char in found if _hides(char) == "control"]
    said = []
    for words, chars in (
        ("invisible or bidirectional control characters", unseen),
        ("control characters", controls),
    ):
        if not chars:
            continue
        named = ", ".join(f"U+{ord(char):04X}" for char in chars[:_NAMED])
        if len(chars) > _NAMED:
            named += f" and {len(chars) - _NAMED} more"
        said.append(f"{words}: {named}")
    return "contains " + "; and ".join(said)


def own_rows_warning(pending: PendingApproval) -> str | None:
    """
    ``hidden_warning`` of a parked call's unified diff, which is what a pane's
    own rows are: for a pane to say over them where it draws them without
    having asked ``diff()``, which says it itself over a diff it hands back.

    Of the string cut at its newlines only, each line less the carriage return
    of a CRLF end: ``str.splitlines``, which is how a pane makes its rows,
    breaks a line at a form feed, at U+0085 and at U+2028, and what it broke
    at is in no row to be found.
    """
    if not pending.diff:
        return None
    return hidden_warning(line.removesuffix("\r") for line in pending.diff.split("\n"))


def _lines(*files: Any) -> Iterator[str]:
    """
    Every line of some files of a model that the diff view sets as code: each
    hunk's rows, and the lines of a file shown as written. Reads the library's
    model; raises what that raises.
    """
    for file in files:
        for hunk in file.hunks:
            for row in hunk.rows:
                yield row.text
        yield from file.raw


def _hidden_in(model: Diff) -> str | None:
    """
    ``hidden_warning`` of a whole model, its files' labels with their rows:
    what ``diff()`` says over a diff it hands back to its pane, whose rows are
    every file's. Raises what the library raises.
    """
    with _READING:
        labels = tuple(str(file.label) for file in model.files)
        lines = tuple(_lines(*(file for file in model.files if file.control_characters)))
    return hidden_warning((*labels, *lines))


def unfit(model: Diff) -> str | None:
    """
    Why this diff is not one the diff view is given, as the words after "this
    diff is the pane's own rows:", or ``None`` for one it is.

    The columns were measured over a row of ASCII (``gate.columns``), and that
    answers for a diff only as far as its rows are like that one:

    - *A row holding a character the code face lacks.* The library sets it
      in whole cells, and not always one: measured at a pixel height of 16
      (cell 8 px) in ``xπxЖx``, U+03C0 and U+0416, borrowed from the prose
      face, take two cells each, so the ``x`` after the first is at cell 3
      and the last at cell 6, while the view counts one cell a character for
      its width and its scroll; U+4E2D and U+1F600, which no bundled face has,
      take one. So such a row is on the grid and reaches further right than
      the view allows for. Asked of the library (``gate.code_lacks``) about
      the row as the view is handed it: **a control or an invisible character
      is asked about as the placeholder the view sets for it** (``hidden``,
      ``PLACEHOLDER``), which the code face has and which is one cell on the
      grid, so a diff is not refused for one. It is named above the view
      instead (``above``).
    - *A row holding a lone surrogate.* Refused on its own account, below.
    - *A row longer than the measured cell holds for* (``gate.Columns.reach``).

    Of the rows the model holds: what a click on collapsed context brings in
    is another model, and ``diff()`` asks again of that one. One pass over the
    rows, and one call into the library for each distinct character above
    ASCII. Raises what the library raises.

    ``None`` wherever the columns do not hold at all: the view is then drawn
    only because ``ADOPT`` says so, to be looked at, and no row of it is on a
    grid for these to be off.

    **A lone surrogate is the pane's own rows, and for what is true of it**:
    the library would set it as U+FFFD, on its column, and say nothing of it
    in any header, so the diff is handed back under a line naming it
    (``tests/test_typeset_diffs.py``). A parked call's texts hold none: what
    ``json.loads`` makes of ``"\\udc80"`` in a tool's arguments is written
    out where session text enters the store (``pptmstr.drawable``), and is
    ASCII by the time it is a row. This answers for texts that reach the seam
    any other way.
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
    lone = sorted(char for char in above_ascii if "\ud800" <= char <= "\udfff")
    if lone:
        named = ", ".join(f"U+{ord(char):04X}" for char in lone[:4])
        more = f" and {len(lone) - 4} more" if len(lone) > 4 else ""
        return (
            f"a row holds {named}{more}, a lone surrogate, which the library "
            f"would set as U+{ord(PLACEHOLDER):04X} and not name"
        )
    asked = {PLACEHOLDER if _hides(char) else char for char in above_ascii}
    with _READING:
        lacking = gate.code_lacks("".join(sorted(asked)))
    if lacking:
        named = ", ".join(f"U+{ord(char):04X}" for char in lacking[:4])
        more = f" and {len(lacking) - 4} more" if len(lacking) > 4 else ""
        return (
            f"a row holds {named}{more}, which the code face has no glyph for, "
            "and the library's view would count one cell for a character it may set two wide"
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
    from imscribe import Diff

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

    **Over them, where the fence holds what cannot be seen, the sentence that
    says so** (``hidden_warning``): such a fence is one the view draws, a
    placeholder in each such character's cell, and the view's own word for it
    is in a header row.
    """
    out = []
    marked = hidden_warning(line.removesuffix("\r") for line in text.split("\n"))
    if marked is not None:
        out.append(marked)
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
    from imscribe import Diff

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
        from imscribe import Diff

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
    **And a diff handed back to its pane is first said to hold what it holds
    that cannot be seen** (``hidden_warning``), over the line saying why it
    is handed back: the pane's rows show such a character as a letter its
    font lacks, or as nothing.

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
    from imscribe import Diff, DiffLayout, TypesetUnavailable

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
                entry.said = above(shown)
                entry.hidden = _hidden_in(shown)
                entry.unset = unfit(shown)
        if empty or entry.unset is not None:
            if entry.view is not None:
                with _HANDING:
                    entry.view.close()
                entry.segments = []
            if not empty:
                if entry.hidden is not None:
                    views._said(im, entry.hidden, ui_theme.P.warn)
                views._said(im, f"this diff is the pane's own rows: {entry.unset}")
            return None
        entry.frame = frame
        layout = DiffLayout.SIDE_BY_SIDE if side_by_side else DiffLayout.UNIFIED
        wide = views._width(im, width)
        said = entry.said
        im.push_text_wrap_pos(im.get_cursor_pos_x() + wide)
        for line, warns in said:
            im.text_colored((ui_theme.P.warn if warns else ui_theme.P.accent).vec4, line)
        im.pop_text_wrap_pos()
        view = entry.view
        theme, palette = themes.diff_theme(), themes.palette()
        if view is None:
            with _MAKING:
                view = gate.diff_view(model, theme, layout=layout)
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
    from imscribe import Diff

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
