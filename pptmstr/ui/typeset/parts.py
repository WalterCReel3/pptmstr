"""
What a pane hands the seam to set, and the one rule for whether a body is markdown.

Pure: no library, no ImGui, no state. ``Kind`` is how a piece of text is read,
``Part`` is one piece with its ink and its role, and ``prose_parts(text)`` is
**the** markdown-or-not rule -- every pane showing a concern, a task detail or a
brief entry calls it, so one body reads the same in two panes, typeset or not.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass

from ...theme import Color
from .roles import Role


class Kind(enum.Enum):
    """
    How one part of a ``document`` is read.
    """

    MARKDOWN = "markdown"
    LITERAL = "literal"
    """
    Exactly as written, monospace, never broken: ``plain(wrap=False)``.
    """
    WRAPPED = "wrapped"
    """
    Monospace, broken to the window at spaces, whitespace collapsed: ``plain(wrap=True)``.
    """
    WRAPPED_EXACT = "wrapped-exact"
    """
    Exactly as written, monospace, broken to the window at any column:
    ``plain(wrap=True, exact=True)``. Every space, indent and blank line is kept, and
    a copy is the text without the breaks the window made. For a command or a path.
    """


@dataclass(frozen=True, slots=True)
class Part:
    """
    One piece of a ``document``: its text, how to read it, and its ink.

    ``colour`` draws the whole part in one role's ink -- a segment kind's, or dim for
    something de-emphasised. Left out, markdown takes the palette's role inks and
    literal text the theme's ``text``.

    **Name the role** (``"danger"``, ``"text_dim"``: a field of ``theme.Palette``) rather
    than passing ``P.danger`` when the parts are kept from frame to frame. A name is
    looked up in whichever theme is live when the part is inked; a ``Color`` is the
    theme's that was live when the part was made, and stays that after a switch.

    ``demote`` sets each heading of a markdown part that many levels down, so a
    ``#`` in a body does not outrank the line the pane heads it with: at 1 an h1 is
    set as an h2, and past the third level a heading is body-sized bold.

    ``tight`` leaves out the air a literal or wrapped part has under it: for a
    part that names the one after it -- an argument's name over its value --
    where the gap belongs under the pair. A markdown part has its blocks' own
    spacing and ignores it.

    ``role`` is whose size and leading a literal or wrapped part is set in:
    ``Role.ARGUMENT`` for a call's argument. A markdown part's blocks each have a
    role of their own and it ignores this.
    """

    text: str
    kind: Kind = Kind.MARKDOWN
    colour: Color | str | None = None
    demote: int = 0
    tight: bool = False
    role: Role = Role.LITERAL


_MULTILINE: dict[Role, str] = {
    Role.LITERAL: "literal",
    Role.ARGUMENT: "literal",
}
"""
How a role's text of **several lines** meets the window's edge, by ``Kind`` value:
``"literal"`` is set unbroken and the window scrolls sideways to it,
``"wrapped-exact"`` is broken at the edge with every space kept. Internal, like
``roles.LEADING``. ``multiline_kind()`` answers from it.
"""


def multiline_kind(role: Role) -> Kind:
    """
    The ``Kind`` a role's text of several lines is set as: unbroken, for the window
    to scroll to, or broken at its edge (``_MULTILINE``). A role with no entry is
    broken at the edge, which hides nothing in a window that cannot scroll.
    """
    return Kind(_MULTILINE.get(role, Kind.WRAPPED_EXACT.value))


_WRAP_FLOOR = 60
"""
Nobody wraps prose narrower than this, so lines shorter than it were ended on purpose.
"""

_ENDED_EARLY = 0.8
"""
A line was ended on purpose when it and the next line's first word come to less
than this share of the paragraph's measure. Text wrapped by a person or a model is
wrapped by feel, a few columns either side of the column aimed at, and a rule that
reads every short line as deliberate breaks such prose at random.
"""

_BODY_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_BODY_LIST = re.compile(r" {0,3}(?:[-*+]|\d{1,9}[.)])(?:\s|$)")
_BODY_BLOCK = re.compile(r" {0,3}(?:#{1,6}(?:\s|$)|[-*+](?:\s|$)|\d{1,9}[.)](?:\s|$)|>|\|)")
_BODY_RULE = re.compile(r"\s*([-=*_])(?:\s*\1)*\s*$")
_BODY_FIELD = re.compile(r"[A-Za-z_][\w.-]*(?: [\w.-]+){0,2}:(?:\s+\S|$)")
_BODY_INLINE = re.compile(r"`[^`\n]+`|\*\*\S|\]\(")
AS_WRITTEN = "\ufdd0"
"""
Before a character in markdown source: the character is text, whatever it would
have meant. What ``_shaped`` writes where CommonMark would write a backslash,
because the seam's reader sets a backslash somebody typed as a backslash
(``gate._written_escapes``), so a backslash cannot also be the mark that is not
set. ``gate._as_written`` is the reader's rule for it.

U+FDD0 is one of the code points Unicode sets aside for a program's own use and
no font draws, so a body that already holds one loses nothing a reader could
have seen; ``_shaped`` writes it twice where it finds it.
"""

_BODY_PUNCTUATION = re.compile(rf"([!-/:-@\[-`{{-~{AS_WRITTEN}])")
# What a writer wrapping by hand carries to the next line whole: a link, a code
# span, or failing those a word.
_BODY_UNIT = re.compile(r"\[[^\]\n]*\]\([^)\n]*\)\S*|`[^`\n]+`\S*|\S+")


def prose_parts(text: str, *, colour: Color | str | None = None) -> tuple[Part, ...]:
    r"""
    A body somebody wrote -- a concern, a task detail, a brief entry -- as the parts
    of one ``document``. **The one rule for whether such a body is markdown**; every
    pane that shows one calls this, so a body reads the same wherever it appears.

    Nothing promises a body is markdown. Agents mostly write it, and a worker
    without a moment to format writes ``key: value`` lines and pastes a traceback.
    Read as markdown those lines fold into one paragraph and the traceback loses
    the indentation that says which line belongs to which frame. So a body is read
    **a paragraph at a time**, and each paragraph is judged on its own:

    - **Markdown already** -- a paragraph with a fence, a list item, a heading, a
      quote or a table row in it, or one continuing a list -- is left exactly as
      written. An indented line under a list item is the item's continuation.
    - **Indented lines** that no list accounts for are a traceback or a snippet
      nobody fenced: the paragraph is set as written, monospace (``Kind.LITERAL``).
    - **Lines ended on purpose** keep their line breaks, as markdown hard breaks.
      Two tests. Every line of the paragraph is a field -- ``key: value``, the key
      one to three words -- or, line by line, the line stopped well short of the
      paragraph's measure (``_ENDED_EARLY``) for the next line's first word to
      have fitted -- or its first link or code span, which a writer carries over
      whole. The measure is the paragraph's second-longest line, so one line that
      could not be wrapped (a path, a command) does not make the rest look short;
      and it is never under ``_WRAP_FLOOR``.
    - Anything else is prose wrapped at a column, which is what markdown is for.

    A paragraph that keeps its breaks and shows no sign its writer meant markdown --
    no code span, no ``**``, no link -- has its punctuation escaped, so a ``*`` or a
    ``#`` in ``exit: 1`` lines stays what was typed.

    A body in which no paragraph needed anything is one markdown part holding the
    text untouched, which is the ordinary case and costs one pass over its lines.

    The failure in one direction is a line break more or fewer than was meant; in
    the other, prose in monospace. In neither is a character lost.

    ``colour`` is given to every part (``Part.colour``).
    """
    if not text.strip():
        return ()
    paragraphs = _paragraphs(text)
    shapes: list[tuple[str, list[str]]] = []
    in_list = False
    for lines, fenced in paragraphs:
        listed = bool(_BODY_LIST.match(lines[0]))
        in_list = listed or (in_list and lines[0].startswith(("  ", "\t")))
        shapes.append(("markdown", lines) if fenced or in_list else _shaped(lines))
    if all(shape == "markdown" for shape, _ in shapes):
        return (Part(text, colour=colour),)
    parts: list[Part] = []
    prose: list[str] = []
    for shape, lines in shapes:
        if shape == "exact":
            if prose:
                parts.append(Part("\n\n".join(prose), colour=colour))
                prose = []
            parts.append(Part("\n".join(lines), Kind.LITERAL, colour))
        else:
            prose.append("\n".join(lines))
    if prose:
        parts.append(Part("\n\n".join(prose), colour=colour))
    return tuple(parts)


PROMPT_MARK = "> "
"""
What a log writes in front of a prompt (``AgentSession.send``): the mark that
says the operator wrote this and the agent did not.
"""


def prompt_parts(text: str, *, colour: Color | str | None = None) -> tuple[Part, ...]:
    """
    A prompt as a log holds it -- ``PROMPT_MARK`` and what was sent -- as the parts
    of one ``document``: what was sent is a body somebody wrote, read by
    ``prose_parts``, and the mark is set as the characters it is.

    Read as markdown, the mark would open a block quote and be drawn as a rule
    beside the first paragraph only. So it is put in front of the first part as
    text (``AS_WRITTEN``), where that part is a paragraph of markdown, and in front
    of a literal part as it is. Where the prompt begins with a block of its own --
    a heading, a list, a quote, a table, a fence, an indent -- text in front of
    its first line would make that block a paragraph, and the mark is a part of
    its own above it.
    """
    if not text.startswith(PROMPT_MARK):
        return prose_parts(text, colour=colour)
    body = text[len(PROMPT_MARK) :]
    mark = Part(AS_WRITTEN + PROMPT_MARK.rstrip(), colour=colour)
    parts = prose_parts(body, colour=colour)
    if not parts:
        return (mark,)
    first = parts[0]
    line = body.split("\n", 1)[0]
    if first.kind is Kind.LITERAL:
        return (Part(PROMPT_MARK + first.text, Kind.LITERAL, colour), *parts[1:])
    opens_a_block = (
        line[:1].isspace()
        or _BODY_BLOCK.match(line)
        or _BODY_FENCE.match(line)
        or _BODY_RULE.match(line)
    )
    if opens_a_block:
        return (mark, *parts)
    return (Part(AS_WRITTEN + PROMPT_MARK + first.text, colour=colour), *parts[1:])


def _paragraphs(text: str) -> list[tuple[list[str], bool]]:
    """
    The paragraphs of a body: runs of lines between blank ones, and whether each
    holds a fence. A fence keeps its blank lines and whatever touches it.
    """
    out: list[tuple[list[str], bool]] = []
    current: list[str] = []
    fenced = False
    closer = ""
    for line in text.split("\n"):
        fence = _BODY_FENCE.match(line)
        if closer:
            current.append(line)
            if fence and fence[1].startswith(closer) and not line.strip(" " + closer[0]):
                closer = ""
        elif fence:
            closer = fence[1]
            fenced = True
            current.append(line)
        elif line.strip():
            current.append(line)
        elif current:
            out.append((current, fenced))
            current, fenced = [], False
    if current:
        out.append((current, fenced))
    return out


def _shaped(lines: list[str]) -> tuple[str, list[str]]:
    """
    How a paragraph with no fence in it, outside any list, is to be set, and its
    lines as they should then be written: ``markdown``, ``exact`` or ``lines``.
    """
    if len(lines) < 2:
        return "markdown", lines
    indented = [n for n, line in enumerate(lines) if line.startswith(("  ", "\t"))]
    marked = [n for n, line in enumerate(lines) if _BODY_BLOCK.match(line)]
    if marked:
        # A list item, a heading, a quote or a table row: markdown's to read, and
        # an indented line after a list item is that item's continuation.
        listed = [n for n in marked if _BODY_LIST.match(lines[n])]
        if not indented or (listed and listed[0] < indented[0]):
            return "markdown", lines
    if indented:
        return "exact", lines
    if marked or any(_BODY_RULE.match(line) for line in lines):
        return "markdown", lines
    if all(_BODY_FIELD.match(line) for line in lines):
        breaks = set(range(len(lines) - 1))
    else:
        lengths = sorted(len(line.rstrip()) for line in lines)
        measure = max(_WRAP_FLOOR, lengths[-2] if len(lengths) > 2 else lengths[-1])
        if len(lengths) == 2:
            # Two lines give one line to judge and no second-longest to judge it
            # against: the first line's own length is the only evidence of a column.
            measure = max(_WRAP_FLOOR, len(lines[0].rstrip()))
        breaks = {
            n
            for n in range(len(lines) - 1)
            if len(lines[n].rstrip()) + 1 + _first_unit(lines[n + 1]) <= measure * _ENDED_EARLY
        }
    if not breaks:
        return "markdown", lines
    meant = bool(_BODY_INLINE.search("\n".join(lines)))
    out: list[str] = []
    for n, line in enumerate(lines):
        kept = line.rstrip() if meant else _BODY_PUNCTUATION.sub(AS_WRITTEN + r"\1", line.strip())
        # Two trailing spaces are markdown's hard line break.
        out.append(kept + "  " if n in breaks else kept)
    return "lines", out


def _first_unit(line: str) -> int:
    """
    The length of what a line starts with that could not have been split to fit the
    line above: a whole link or code span where it starts with one, else a word.
    """
    found = _BODY_UNIT.match(line.lstrip())
    return len(found[0]) if found else 0


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------
