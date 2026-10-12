"""
Whether the text library can draw, its fonts, and the sentence when it cannot.

The library is ``imscribe`` drawing through imtypeset, a native library built for
one ``imgui-bundle`` wheel and found through two environment variables.
``imscribe.typesetter`` is its gate; this module is pptmstr's side of that gate
and the only place the two meet for anything but a diff model (``diffs``).

**pptmstr is still pptmstr when the library cannot draw.** Four things can be
wrong -- it is not installed, it was refused, its fonts did not load, it stopped
while drawing -- and a fifth is anything raised in here. Each leaves
``can_draw()`` false, so every pane draws with its own renderer
(``roles.enabled``), and each is one sentence the Text menu and the settings
dialog say (``unavailable_reason``). So **no function here raises**: the library
is imported inside the functions that use it, and whatever one of them raises
is caught, kept as the sentence, and costs the typeset path and nothing else.

**Anything a call into the library raises while a pane draws is the library
stopping** (``into_library``). The library sorts its own failures from this side's
and passes the second kind on as they are: an ``AttributeError`` for a name a
newer library dropped, an encoding error for text its native half cannot take.
Left alone that leaves the pane on every frame the text is on screen. So every
such call in ``views`` and ``diffs`` is made inside ``into_library()``, which
logs what was raised once with its traceback, stops the library as a draw that
raised ``TypesetUnavailable`` does (``stopped``), and raises that in its place.
Only the calls into the library are inside it: what pptmstr's own code raises
around them is a defect and leaves as itself.

**A draw that set less than it was handed says so two ways, and neither is an
exception** (``noted``). A view's ``draw`` answers false when a block in view
could not be set as asked, and the library notes an edit it cut --
``typesetter.trouble()`` -- while the draw over it answers true. Measured in
pptmstr on 2026-10-12 (imscribe ``38f5fac``, imtypeset ``c9f8902``): a table row
of more than 1,024 cells loses the rest and draws true; a block over 16 MiB is
cut and draws false while it is in view; no character does either. A lone
surrogate, which ``json.loads`` makes of ``"\\udc80"`` in a tool's arguments,
is set as U+FFFD and noted nowhere (the library the tree pins now, by
``tests/test_typeset_views.py``). ``views`` and ``diffs`` read both.

**Fonts are loaded once the UI font is, at the end of hello_imgui's
``load_additional_fonts``**, and never from ``post_init``: the runner empties the
atlas before that callback, so a face added earlier is gone by the first frame.
The library puts its own copy of each face on the atlas, after the application's
first font, which stays ``Fonts[0]``. Two files are pptmstr's to name before it
does (``imscribe.register_font``): the vendored bold monospace, without which the
library backs bold code with a proportional bold and bold code stops being
monospace; and the upright file of the proportional family in force.

**They are loaded whatever ``Settings.typeset`` says.** Measured in the
application under Xvfb and llvmpipe on 2026-10-11: 21 ms. Switching typeset
text on is then a setting and not a load.

**What a theme costs is paid here and not in a frame.** A theme sizes each face
from its font file, and the library reads a file's kerning with its metrics the
first time a face is asked about: measured in pptmstr on 2026-10-12 (headless,
load average 2.0), 100 ms for the first Roboto face and 47 to 68 ms for each
other, 2.5 ms for Inconsolata, 265 ms for the five a document's theme names.
Left to the first draw that is one frame of a quarter of a second -- the board's,
with every row open, was 291 ms. So where the operator's setting asks for
typeset text the document theme is built at the load, and where it does not the
frame that follows switching it on pays instead. A registration drops what the
library read, so a change of family costs it again: 403 ms measured, in the
frame after the click.

**Whether a diff's columns hold is measured there too** (``columns``), where
typeset text is wanted: the first diff view made in a process is 29 ms and the
measurement 3 to 6 (headless, 2026-10-12, load average 12), and the frame an
approval first came under the cursor would otherwise pay both.

**A change of proportional family loads them again, between frames**
(``pre_frame``): the library reads its files when its context is made, so
another file means another context. Run in a window on 2026-10-11: 11 ms, a view
kept across it draws, and **each load leaves seven more fonts on ImGui's atlas**
that nothing takes off. That is the cost of an operator's click on the family
list and of nothing else. What the fonts left behind had rasterised is let go
of: a load moves the epoch, and ``theme.keep_baked_sizes`` drops every baked
size when it moves.

**The library's context belongs to one ImGui context, and the library is the
one that knows which.** ``imscribe.typesetter.ready()`` is false wherever the
ImGui context that is current is not the one its fonts were loaded in: another
one, none, or that one destroyed, with ``unload()`` called first or not. It
holds the current context to the font it loaded and that font's bytes, because
a context made after one was destroyed is often given its address, and a
comparison of contexts then takes the new one for the old. So ``can_draw()``
asks ``ready()`` and keeps no record of its own, and ``why_not_ready()`` is the
sentence. ``app`` still calls ``unload()`` in ``before_exit``: that is where the
library's context is given back, and nothing here depends on it.

Reached past ``imscribe.__all__``: the module ``imscribe.typesetter`` itself, for
``module``, ``trouble``, ``unload``, ``font`` and ``why_not_ready``, which the
module lists as its own (``typesetter.__all__``) and the facade does not; and
the module ``imscribe.markdown_rules``, for its private ``_parser``, as said
below. A test reads this package's imports and holds them to those two names.
Two reaches are on neither list: the native module's ``face_has_glyph`` with
``typesetter.FACE_ROLES`` (``code_lacks``), and the native module's documents
(``columns``).

**The markdown reader is told to hide nothing, and then asked whether it does**
(``_reader``, ``reader_hides``). A CommonMark reader shows nothing for raw HTML
and for an image, pairs the stars of a glob into emphasis, and draws a link
without where it goes; the panes show what an operator decides on. Measured
through the library as it is, 2026-10-12: ``a <b>x</b> and List<String>`` is set
as ``a x and List``, ``rm **/*.py and t/**/*.md`` as ``rm /*.py and t//*.md``,
and an unclosed ``<!--`` takes every paragraph after it. The library has no
supported place for a host's reader rules (request P7 of
``HANDOFF-advtext.md``, accepted and not built), so the rules are put on its
private parser object, here and nowhere else, and the reader is then read
through the library's public calls: a probe that comes back short stops the
library being offered to draw with, which is the honest answer when the reader
cannot be told to hide nothing.

**Whether monospace keeps its columns is measured, and the measurement has no
promise of any kind** (``columns``). The library has no answer for "is a row
of code drawn on ImGui's grid", so one row is set in a document of the native
module's, as the diff view sets a row, and read back: where it was laid out,
the vertices a draw of it would emit, what a hit test answers on them. Through
``typesetter.module()`` and ``typesetter.base_style``, here and nowhere else.
"""

from __future__ import annotations

import struct
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from ... import pairing
from ... import theme as ui_theme
from ...log import LOG
from . import roles, themes
from .parts import AS_WRITTEN

_HIDING_RULES = ("html_block", "html_inline", "image", "entity")
"""
The rules of the library's markdown reader that consume text and show nothing
for it, or show other characters than were written: ``&amp;`` is set as ``&``
by the last, and the body's recipient gets the five characters.
"""

_DESTINATIONS_RULE = "pptmstr_link_destinations"
_STARS_RULE = "pptmstr_written_stars"
_AS_WRITTEN_RULE = "pptmstr_as_written"

READER_PROBE = (
    "a <b>x</b> ![alt](u) rm **/*.py and t/**/*.md R&amp;D C:\\_w\\*.log " f"{AS_WRITTEN}*z*\n"
)
READER_PROBE_WORDS = (
    "<b>x</b>",
    "alt",
    "(u)",
    "**/*.py",
    "t/**/*.md",
    "R&amp;D",
    "C:\\_w\\*.log",
    " *z*",
)
"""
A line every word of which a CommonMark reader hides some of, and what must come
back from it: the tags, the image's description and destination, the stars,
the entity as its characters, the backslashes, and the stars the seam marked as
text without its mark.
"""


READY = "ready"
NOT_INSTALLED = "not-installed"
REFUSED = "refused"
FAILED = "failed"
"""
The kinds ``library`` answers with. They are the values of imscribe's
``typesetter.Kind``, so a kind it reports is one of these or another word of
its own, which is said as it is.
"""


@dataclass
class _Gate:
    """
    What pptmstr knows about the library that the library does not answer.

    Module state for the reason ``roles._State`` is: one library context, one
    atlas, one process.
    """

    # Whether the fonts have been asked for since the last `unload`.
    asked: bool = False
    # Whether the library accepted its pairing with the wheel at that load, and
    # whether the load succeeded: what tells "refused" from "its fonts did not
    # load" from "stopped while drawing" afterwards.
    accepted: bool = False
    loaded: bool = False
    # The proportional family whose file the library was given at that load.
    family: str | None = None
    # The vendored bold monospace the application named, for a later load.
    bold: Path | None = None
    # Something raised in this module, as `Type: message`. While it is set the
    # library is not offered to draw with; the next load clears it.
    failure: str | None = None
    # What a view raised when the library stopped inside a draw, kept until the
    # frame it happened in is over (`stopped`, `pre_frame`).
    stopping: str | None = None


_GATE = _Gate()


def _typesetter() -> Any:
    from imscribe import typesetter

    return typesetter


def _imgui() -> Any:
    from imgui_bundle import imgui

    return imgui


def failed(failure: BaseException) -> None:
    """
    Keep what was raised as the reason, and stop offering the library to draw with.

    For this module, and for the one caller that stands where an exception may
    not pass: ``theme``, inside hello_imgui's font callback.
    """
    _GATE.failure = f"{type(failure).__name__}: {failure}"


def stopped(failure: BaseException) -> None:
    """
    Take what a view raised when the library stopped inside a draw. For ``views``.

    **The answer of ``can_draw`` does not change until the frame is over.** A
    pane asks ``enabled`` at several places in one frame, and one that took its
    typeset branch at the first and its own renderer at the second would draw
    half of each into a window laid out for one. So the rest of the frame's
    calls are still made through the seam, each says the reason where its text
    would be, and ``pre_frame`` makes the stop the gate's failure: the next
    frame is every pane's own renderer.
    """
    if _GATE.stopping is None:
        _GATE.stopping = f"{type(failure).__name__}: {failure}"


class into_library:
    """
    A call into the library made while a pane draws: ``with into_library("…"):``
    around the call and nothing else. ``doing`` finishes the sentence "the
    library raised X while …".

    ``TypesetUnavailable`` passes through as it is. Anything else the call
    raises is the library failing (the module docstring says why): logged once
    with its traceback, kept as the stop (``stopped``), and raised again as
    ``TypesetUnavailable`` naming the type, so the handler every draw already
    has says it at the cursor and counts the text as not on screen. The next
    frame is every pane's own renderer.

    A class and not a generator, and stateless, so one is made a module and
    used by every call: a view is several calls into the library on every
    frame it is drawn, and the log draws hundreds of views.
    """

    __slots__ = ("doing",)

    def __init__(self, doing: str) -> None:
        self.doing = doing

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        kind: type[BaseException] | None,
        failure: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if failure is None or not isinstance(failure, Exception):
            return
        from imscribe import TypesetUnavailable

        if isinstance(failure, TypesetUnavailable):
            return
        said = f"the library raised {type(failure).__name__} while {self.doing}: {failure}"
        if _GATE.stopping is None:
            _GATE.stopping = said
            LOG.error("typeset", said)
            LOG.debug("typeset", "".join(traceback.format_exception(failure)))
        raise TypesetUnavailable(said) from failure


def noted() -> object:
    """
    The library's note of the last thing it got over without stopping, as the
    object it is: for telling whether a call into the library made one.

    The library cuts what it cannot take in an edit -- a table's cells past its
    limit, a block's text past 16 MiB -- sets the rest, and says so only here
    (``typesetter.trouble()``); the draw over a cut answers true unless the cut
    block is in view, and for a table not then either. The note is one string
    for the process and the same words twice are the same string to ``==``, so
    a caller keeps what this answered before its calls and compares by
    identity after them: the library makes the string anew for each note.
    ``None`` where the library cannot be asked. Never raises.
    """
    try:
        return _typesetter().trouble()
    except Exception:
        return None


def can_draw() -> bool:
    """
    Whether the library can draw *in the ImGui context that is current*.

    Its fonts are loaded, it has not been switched off since, and the current
    context is the one they were loaded in, which is the library's to know
    (``typesetter.ready``). Never raises. This is the library's half of
    ``roles.enabled``. In the frame the library stopped inside a draw the
    answer is the one the frame began with (``stopped``).
    """
    if not _GATE.loaded or _GATE.failure is not None:
        return False
    if _GATE.stopping is not None:
        return True
    try:
        return bool(_typesetter().ready())
    except Exception as failure:
        failed(failure)
        return False


def available() -> bool:
    """
    Whether the operator can have typeset text at all: what the Text menu and
    the settings dialog offer their switches by.

    The library can draw, **and some pane's typeset branch is built on it**
    (``roles.ADOPTED``). Without the second half the switches would be live and
    change nothing on screen, and the dialog would draw its samples through
    calls that are not built. Never raises.
    """
    return bool(roles.ADOPTED) and can_draw()


def obtainable() -> bool:
    """
    Whether typeset text can be had by switching it on.

    The fonts are loaded whatever the setting says, so this is ``available()``:
    there is no load a switch would still have to make.
    """
    return available()


def unavailable_reason() -> str | None:
    """
    Why text cannot be typeset, as one sentence for the menu, or ``None`` when it can.

    The sentence says which of the things that can be wrong is: not installed,
    refused, fonts not loaded, fonts loaded in another ImGui context, stopped
    while drawing, or a failure in the gate itself. The library's own words are
    quoted, not paraphrased.
    """
    if can_draw():
        if roles.ADOPTED:
            return None
        return "no pane sets its text through the typesetting library in this build"
    if _GATE.failure is not None:
        return f"the typesetting library failed and is not in use ({_GATE.failure})"
    if not _GATE.asked:
        return "the typesetting library's fonts have not been loaded"
    try:
        typesetter = _typesetter()
    except Exception as failure:
        return f"the text library is not installed ({type(failure).__name__}: {failure})"
    try:
        why = str(typesetter.reason())
        if typesetter.module() is None:
            if _GATE.loaded:
                noted = str(typesetter.trouble())
                return f"the typesetting library stopped while drawing ({noted or why})"
            if _GATE.accepted:
                return f"the typesetting library's fonts did not load ({why})"
            return _unaccepted(_kind(typesetter), why)
        if _GATE.loaded:
            return (
                f"the typesetting library's fonts are in another window's context "
                f"({typesetter.why_not_ready()})"
            )
        noted = str(typesetter.trouble())
        return f"the typesetting library's fonts did not load ({noted or 'no reason given'})"
    except Exception as failure:
        return f"the typesetting library failed and is not in use ({type(failure).__name__})"


def _kind(typesetter: Any) -> str:
    """
    Which kind of thing is so of the library, as imscribe's own word for it.

    ``typesetter.kind()`` is asked and what it answers is final: it knows of
    routes to a library, and of reasons for there being none, that the two
    variables do not show.
    """
    return str(typesetter.kind().value)


def _unaccepted(kind: str, why: str) -> str:
    """
    The sentence for a library that is not accepted, quoting its own words for why.
    """
    if kind == NOT_INSTALLED:
        return f"the typesetting library is not installed ({why})"
    if kind == REFUSED:
        return f"the typesetting library was refused ({why})"
    return f"the typesetting library is not in use ({kind}: {why})"


def library() -> tuple[str, str]:
    """
    Whether the library is accepted beside the wheel that is running, with no
    window and no fonts: its kind, and the sentence when it is not ``READY``.

    For ``pptmstr --version``. ``unavailable_reason`` answers for a running
    application, where fonts not yet loaded is a reason of its own; this asks
    only what can be known before there is an ImGui context. The first call may
    be the library's import, and with it its check against the wheel. Never
    raises.
    """
    try:
        typesetter = _typesetter()
    except Exception as failure:
        return (
            NOT_INSTALLED,
            f"the text library is not installed ({type(failure).__name__}: {failure})",
        )
    try:
        kind = _kind(typesetter)
        why = str(typesetter.reason())
    except Exception as failure:
        return (
            FAILED,
            f"the typesetting library failed and is not in use "
            f"({type(failure).__name__}: {failure})",
        )
    return kind, "" if kind == READY else _unaccepted(kind, why)


def settle() -> None:
    """
    Ask the library whether it is there, and then take the two variables a
    checkout set for itself back out of the environment (``pairing.release``).

    For the entry point, before the application starts anything. In that
    order: the library remembers the pair it accepted when it was first asked,
    and what is left in the environment after that is only what a session's
    process would inherit. The first asking is the library's import
    and its check against the wheel, which the font load would otherwise have
    been the first to pay. Never raises: an imscribe that is not there has
    nothing to read them with, and ``unavailable_reason`` finds that again.
    """
    try:
        _typesetter().module()
    except Exception:
        pass
    pairing.release()


def read(source: str, theme: Any) -> Any:
    """
    Markdown source as the library's blocks, read with ``theme``: the one call the
    seam reads a settled text with.

    Link reference definitions are off, as they are in the library's stream, so
    a text reads the same finished as it did arriving; and that is the parser
    ``_reader`` put pptmstr's rules on.
    """
    from imscribe import parse_markdown

    return parse_markdown(source, theme, references=False)


def _reader() -> None:
    """
    Make the library's markdown reader show what was written, and check that it does.

    Five changes to the one parser object ``read`` and the library's stream
    read with (reference definitions off, mathematics off), all made on the
    parser and not on the text:

    - The rules in ``_HIDING_RULES`` are switched off. Raw HTML is then text, as
      CommonMark's own ``html: false`` reading has it -- a tag, a comment opener, a
      generic's ``<T>`` -- ``![alt](url)`` is an exclamation mark and a link, and
      a character reference is the characters it was written with.
    - A core rule (``_show_destinations``) writes each link's destination after
      its text.
    - An inline rule (``_written_stars``) reads the stars of a glob, a comment
      opener or a product as the characters they are.
    - The reader's ``escape`` rule is replaced (``_written_escapes``): a
      backslash before punctuation is set, where CommonMark shows only the
      punctuation.
    - An inline rule (``_as_written``) reads ``AS_WRITTEN`` and the character
      after it as that character, which is the seam's own way to say what a
      backslash said.

    On the parser because the alternative, escaping the source before it is
    parsed, is a second markdown reader that has to agree with the first about
    every code span, fence and container, and is wrong wherever it does not.

    The parser is the library's private object and is shared by the process;
    nothing else in this process reads markdown with it. The library could keep
    the name and hand back a parser its views do not read with, and the changes
    would land on nothing: so the reader is then asked (``reader_hides``), and
    this raises when the name is gone or the probe comes back short. Safe to
    call again: a rule already there is not added twice.
    """
    from imscribe import markdown_rules

    parser = markdown_rules._parser(False, False)
    parser.disable(list(_HIDING_RULES))
    if _DESTINATIONS_RULE not in parser.core.ruler.get_all_rules():
        parser.core.ruler.push(_DESTINATIONS_RULE, _show_destinations)
    if _STARS_RULE not in parser.inline.ruler.get_all_rules():
        parser.inline.ruler.before("emphasis", _STARS_RULE, _written_stars)
    parser.inline.ruler.at("escape", _written_escapes)
    if _AS_WRITTEN_RULE not in parser.inline.ruler.get_all_rules():
        # The reader's text rule runs to the next character some rule starts
        # at, and has to be told this is one.
        parser.inline.add_terminator_char(AS_WRITTEN)
        parser.inline.ruler.before("escape", _AS_WRITTEN_RULE, _as_written)
    missing = reader_hides()
    if missing:
        raise RuntimeError(f"the markdown reader hides text: {', '.join(missing)}")


def reader_hides() -> tuple[str, ...]:
    """
    Which of the probe's words the reader does not give back: none, when it shows
    what was written.

    Read the two ways text reaches a view -- ``read``, and the library's stream
    -- through the library's public calls and nothing of the parser, so that what
    is checked is the reader in use and not the object ``_reader`` was handed.
    """
    from imscribe import MarkdownStream, Theme

    theme = Theme.build()
    arriving = MarkdownStream(theme)
    arriving.append(READER_PROBE)
    missing: list[str] = []
    for blocks in (read(READER_PROBE, theme).blocks, arriving.blocks):
        said = " ".join(str(getattr(block, "text", "")) for block in blocks)
        missing += [word for word in READER_PROBE_WORDS if word not in said]
    return tuple(dict.fromkeys(missing))


def _written_stars(state: Any, silent: bool) -> bool:
    """
    Read a run of ``*`` as text where it was written as a character and not as
    emphasis: ``**/*.py``, ``src/*``, ``*.md``, ``/* note */``, ``2*3*4``.

    CommonMark pairs such stars up wherever its flanking rules allow --
    ``delete **/*.py and tests/**/*.md`` is ``delete``, a bold ``/*.py and tests/``
    and ``/*.md`` -- and a delimiter is not drawn, so the reader of a body sees a
    different command from the one its recipient gets. A run is text when:

    - a ``/`` is on either side of it (a path, a comment opener or closer);
    - a ``.`` and more follows it (``*.py``, ``**.tar.gz``);
    - a letter or a digit is on both sides (``a*b``, ``foo*bar``), which is how
      CommonMark itself reads ``_``.

    ``**bold**``, ``*emphasis*`` and ``**Ends in a stop.**`` are none of those. What
    still loses its stars is whatever is written exactly as emphasis is: ``rm *tmp
    and old*`` is ``rm`` and an italic ``tmp and old``. In the other direction
    ``**/usr/bin**`` keeps its stars and is not bold. A star in a code span or a
    fence never gets here.
    """
    source: str = state.src
    start: int = state.pos
    if source[start] != "*":
        return False
    stop = start
    while stop < state.posMax and source[stop] == "*":
        stop += 1
    before = source[start - 1] if start else " "
    after = source[stop] if stop < len(source) else " "
    more = source[stop + 1] if stop + 1 < len(source) else " "
    written = (
        before == "/"
        or after == "/"
        or (after == "." and not more.isspace())
        or (before.isalnum() and after.isalnum())
    )
    if not written:
        return False
    if not silent:
        state.pending += source[start:stop]
    state.pos = stop
    return True


_ESCAPABLE = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
"""
The characters a backslash escapes in CommonMark: ASCII punctuation.
"""


def _written_escapes(state: Any, silent: bool) -> bool:
    """
    Read a backslash before punctuation as CommonMark does -- the punctuation
    is text and marks nothing -- and set the backslash as well.

    CommonMark does not draw it: ``C:\\_work\\*.log`` is set as ``C:_work*.log``
    and the pattern ``foo\\.py`` as ``foo.py``, and the reader of a body sees a
    different path and a different pattern from the ones its recipient gets.
    What it costs: a writer who escaped a star on purpose sees their backslash.
    A backslash that ends a line is a line break, as it is in CommonMark, and
    is set before the break. Before anything else a backslash is not this
    rule's and is text already.

    Not reached: a backslash in a table cell before ``|``, which the reader's
    table rule takes when it splits the row, and one in a link's destination.
    """
    source: str = state.src
    at: int = state.pos
    if source[at] != "\\" or at + 1 >= state.posMax:
        return False
    after = source[at + 1]
    if after == "\n":
        if not silent:
            state.pending += "\\"
            state.push("hardbreak", "br", 0)
        at += 2
        # The next line's indentation is not text.
        while at < state.posMax and source[at] in " \t":
            at += 1
        state.pos = at
        return True
    if after not in _ESCAPABLE:
        return False
    if not silent:
        state.pending += source[at : at + 2]
    state.pos = at + 2
    return True


def _as_written(state: Any, silent: bool) -> bool:
    """
    Read ``AS_WRITTEN`` and the character after it as that character, as text.
    """
    source: str = state.src
    at: int = state.pos
    if source[at] != AS_WRITTEN or at + 1 >= state.posMax:
        return False
    if not silent:
        state.pending += source[at + 1]
    state.pos = at + 2
    return True


def _show_destinations(state: Any) -> None:
    """
    Append to each link's text where it goes: ``text (https://...)``.

    A link drawn as its text alone asks its reader to trust a destination they
    cannot see. The destination is written inside the link, so it takes the
    link's ink and its click, and is left out only where the text already is the
    destination to the character (``_names_itself``: most autolinks, a bare
    URL). A title is hidden text too and is written with it.
    """
    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        out: list[Any] = []
        opened: list[tuple[str, str, int]] = []
        for child in token.children:
            if child.type == "link_open":
                attrs = child.attrs or {}
                opened.append((str(attrs.get("href", "")), str(attrs.get("title") or ""), len(out)))
            elif child.type == "link_close" and opened:
                href, title, start = opened.pop()
                said = "".join(
                    str(each.content)
                    for each in out[start + 1 :]
                    if each.type in ("text", "code_inline")
                )
                if title or not _names_itself(said, href):
                    note = type(child)("text", "", 0)
                    note.content = f' ({href} "{title}")' if title else f" ({href})"
                    out.append(note)
            out.append(child)
        token.children = out


def _names_itself(said: str, href: str) -> bool:
    """
    Whether a link's text is its own destination, character for character.

    Only then is the destination left unsaid. The two may differ in a scheme the
    reader supplied (``mailto:``, ``http://``) and in a closing ``/``, and in nothing
    else: the reader's own normalising is not asked, because it is what turns
    ``https://аpple.com`` with a Cyrillic ``а`` into the ``xn--`` host it goes to,
    and a text that only *normalises* to its destination is a lookalike of it.
    Such a link, an autolink included, is set with where it goes after it.
    """
    said = said.strip().removesuffix("/")
    goes = href.removesuffix("/")
    return bool(said) and goes in (said, f"mailto:{said}", f"http://{said}", f"https://{said}")


def load_fonts(bold: Path | None = None) -> bool:
    """
    Give the library its fonts, in the current ImGui context. Whether it can draw
    (``can_draw``).

    Call it at the end of hello_imgui's ``load_additional_fonts``, after the
    application's own first font. ``bold`` is the vendored bold monospace. Every
    call makes the library's context again, which closes every document a view
    made before it, so the epoch moves.

    The markdown reader is made to show what was written first (``_reader``); a
    reader that cannot be is a failure like any other here, and no font is loaded.

    Never raises: a library that is absent or refused answers false, and so does
    anything raised on the way; ``unavailable_reason()`` has the sentence.
    """
    _GATE.asked = True
    _GATE.family = None
    _GATE.bold = bold
    _COLUMNS.clear()
    _GATE.accepted = _GATE.loaded = False
    _GATE.failure = _GATE.stopping = None
    try:
        typesetter = _typesetter()
    except Exception:
        # Not a failure of the gate: `unavailable_reason` finds it missing again
        # and says so, with what the import raised.
        return False
    try:
        _GATE.accepted = typesetter.module() is not None
        if _GATE.accepted:
            _reader()
            family = roles.proportional()
            _register(bold, roles.prose_path(family))
            if typesetter.load_fonts():
                _GATE.family = family.name
                _GATE.loaded = True
    except Exception as failure:
        failed(failure)
    roles.moved()
    if roles.wanted() and can_draw():
        # Here and not in the first frame that sets text: "What a theme costs"
        # in the module docstring.
        try:
            themes.document_theme()
        except Exception as failure:
            failed(failure)
    if roles.wanted() and can_draw():
        # And whether a diff's columns hold, for the same reason: the first
        # diff view made in a process is 29 ms, measured, and a pane would
        # otherwise pay it in the frame an approval first came under the
        # cursor. Kept only if it could be taken: what cannot be measured here
        # is asked again, and then kept, by the frame that wants to know.
        try:
            theme = themes.diff_theme()
            _COLUMNS[_columns_key(theme)] = _measured_columns(theme)
        except Exception:
            pass
    return can_draw()


def _register(bold: Path | None, prose: Path | None) -> None:
    """
    Name pptmstr's two files in the library's registry, which is the process's.

    Only where the registry does not already say so: a registration drops every
    metric the library has read from a font file.
    """
    from imscribe import Face, register_font, registered_fonts, unregister_font

    held = registered_fonts()
    if bold is not None and bold.is_file() and held.get(Face.CODE_BOLD) != bold.resolve():
        register_font(Face.CODE_BOLD, bold.resolve())
    if held.get(Face.PROSE) != prose:
        if prose is None:
            unregister_font(Face.PROSE)
        else:
            register_font(Face.PROSE, prose)


def face_font(face: str) -> Any:
    """
    The ``ImFont`` the library draws a face with, by the name of its ``Face``
    member (``"PROSE_BOLD"``), or ``None``.

    ``None`` when the library cannot draw, when it loaded no file for the face,
    and when the library cannot say (``typesetter.font``); ``imgui.push_font``
    reads ``None`` as "keep the current font", so a caller that pushes the
    answer falls back to the UI font. Asked every time: the library's fonts
    move on the atlas when it loads them again. Never raises.

    Measured on ASCII in Inconsolata (``scripts/measure_typeset_advances.py``,
    the control): text ImGui draws from this entry lands where the chrome's own
    font puts it.
    """
    if not can_draw():
        return None
    try:
        from imscribe import Face

        return _typesetter().font(Face[face])
    except Exception:
        return None


DIFF_CELL = "pixel"
"""
The cell the library's diff view is asked to lay its columns out at: the whole
pixel ImGui has baked for the code face at the role's pixel height, and not
the advance in the font file (the library's default, ``"font"``), which is a
fraction of a pixel at every pixel height a role reaches and is where a column
is lost (``columns``; ``tests/test_typeset_diffs.py`` keeps the default's
answer as the reason this is passed). ``diffs`` says what it costs in glyph
size.
"""


def diff_view(model: Any, theme: Any, **given: Any) -> Any:
    """
    A diff view of the library's as pptmstr makes one: on ``DIFF_CELL``.

    **The one place a diff view is made**: the view a pane draws
    (``diffs.diff``) and the view whose grid ``columns`` measures are both
    made here, so what was measured is the configuration that is drawn.
    Raises what the library raises.
    """
    from imscribe import TypesetDiffView

    return TypesetDiffView(model, theme, cell=DIFF_CELL, **given)


def diff_fences() -> Any:
    """
    The library's hook for a fenced diff in a settled document, on the same
    cell as ``diff_view``. Raises what the library raises.
    """
    from imscribe import TypesetDiffFences

    return TypesetDiffFences(cell=DIFF_CELL)


def code_lacks(text: str) -> tuple[str, ...]:
    """
    The characters of ``text`` above ASCII that the library's code face has no
    glyph for, each once, in the order met.

    The library sets such a character from another face at that face's
    advance, so a row of monospace holding one is off its columns to the right
    of it. Asked as the library's diff view asks for its own
    ``code_face_lacks``, which is not a name the library promises: the native
    module's ``face_has_glyph`` for the code face's role, one call a distinct
    character. Stricter than the view's own answer in one way: a control or
    an invisible character the view would show as a placeholder is asked
    about as itself. Raises what the library raises; for ``diffs.unfit``.
    """
    if text.isascii():
        return ()
    typesetter = _typesetter()
    native = typesetter.module()
    face = typesetter.FACE_ROLES["CODE"]
    distinct = dict.fromkeys(char for char in text if char > "~")
    return tuple(char for char in distinct if not native.face_has_glyph(face, ord(char)))


@dataclass(frozen=True, slots=True)
class Columns:
    """
    What was measured of one row of monospace set by the library (``columns``).

    ``cell`` is the advance the library lays a column out at and ``baked`` the
    advance ImGui has baked for the face at ``size``, the pixel height the row's
    glyphs were drawn from; both 0 where the row could not be read. ``cell``
    is the farther from ``baked`` of two readings: what the view says its cell
    is (``TypesetDiffView.grid``), and the measured row's own width over its
    characters. On a whole-pixel cell the first is the pixel the view aimed at
    and the second is what the layout came to, which is what a long row adds
    up (``COLUMN_EPSILON_PX``). Of the
    ``glyphs`` with ink, ``off`` are not drawn at their index times ``baked``
    from the row's first, and ``mishit`` are ones a point on the drawn glyph is
    answered for as another character's. ``tabs`` is whether every character
    after a tab was drawn a whole number of columns in. ``why`` is what the
    library does, as the words that follow "the library" in a sentence about
    the answer, and short: the settings dialog has one line for that sentence.
    """

    holds: bool
    why: str
    cell: float = 0.0
    baked: float = 0.0
    size: float = 0.0
    glyphs: int = 0
    off: int = 0
    mishit: int = 0
    tabs: bool = False

    @property
    def reach(self) -> int:
        """
        How many columns long a row may be and still be answered for: the row
        measured is ``len(_COLUMN_ROW)`` characters, and a cell that is out by
        ``e`` against the baked advance has a pen half a pixel out after
        ``0.5 / e`` columns, which is where a pen rounded to a pixel would be
        the next one (``COLUMN_EPSILON_PX``). 0 where nothing holds.
        """
        if not self.holds:
            return 0
        out = abs(self.cell - self.baked)
        return _FARTHEST_COLUMN if out * _FARTHEST_COLUMN < 0.5 else int(0.5 / out)


COLUMN_EPSILON_PX = 1e-4
"""
How far the laid-out cell may be from the baked advance and still be it.

The library's lengths are whole scaled points, 1/65536 pt, so a row laid out
on a cell that is meant to be 8 px is 7.999989 px a character (measured at a
pixel height of 16 on ``DIFF_CELL``; between 1.3e-6 and 2.4e-5 px out at the
heights from 12 to 24). The view itself answers the whole pixel it aimed at
wherever the layout came within a thousandth of it, so the row's own width is
read as well and the farther of the two is what is held to this bound
(``Columns.cell``). The row's pens are compared too (``Columns.off``), so the
bound decides only what the probe row is too short to show, and **what it
decides is bounded by a row's length** (``Columns.reach``): an error of e a
column is half a pixel once the row is 0.5 / e columns long -- 5,000 columns
at this bound, 44,815 at a pixel height of 16, 20,590 at 20, 1.3 at the
0.374 px the library's default cell is out by at 16. **And it does add up**:
measured in pptmstr on 2026-10-12 in a diff view drawn with no window at a
pixel height of 16, the glyphs at the end of a row of 100,000 are 1 px left
of the grid the row began on and those at the end of 1,000,000 are 11. A
diff with a row longer than the reach is its pane's own rows
(``diffs.unfit``); a minified file is one line.
"""

_FARTHEST_COLUMN = 1_000_000
"""
The reach of a cell that is the baked advance exactly: no row is asked about
past it.
"""

_TAB_ROW = "\ta\tbc\tdefg\th"
"""
A row of tabs, each followed by ink: ``columns`` holds every glyph of it to a
whole number of columns from the row's start, wherever the library puts a tab
stop. Measured on 2026-10-12: stops every four columns.
"""

_COLUMN_ROW = (
    "total = float(baked.get_char_advance(code)) + kern_px  # 40th is in 'kern'  "
    "iiii|llll|mmmm|WWWW|0000|1111|8888|....|  name       size  kind  @@ -1,7 +1,9 @@  "
    '{[<(~^`_-=+*/\\%$#!?;:,"&)>]}'
)
"""
The row ``columns`` sets: 186 characters, 156 of them with ink. Code as a diff
holds it, with runs of spaces, the narrowest and the widest glyphs of the face
in runs of their own, and punctuation; on the library as it was on 2026-10-12
a row of 40 was already 15 px short of ImGui's.
"""

_COLUMNS: dict[object, Columns] = {}
"""
What ``columns`` measured, by the code style measured. Emptied when the fonts
are loaded or taken down: the measurement is of one atlas.
"""


def columns(theme: Any) -> Columns:
    """
    Whether a row of code set by the library with ``theme`` keeps its columns
    and is hit where it is drawn, by measurement. Never raises: a row that
    cannot be measured does not hold, and ``why`` says what stopped it.

    **What is asked** (requests P21 and P22 of ``HANDOFF-advtext.md``, as one
    answer):

    - the cell the diff view lays a column out at -- what its grid says
      (``diff_view(...).grid``) and what the measured row's width comes to a
      character -- is the advance ImGui has baked for the code face at the
      pixel height the row is drawn from, within ``COLUMN_EPSILON_PX``;
    - every glyph of ``_COLUMN_ROW`` is drawn with its pen at its index times
      that advance from the row's first, so N characters are N advances on
      this row and on any other, and the row lines up with the same text
      through ``ImDrawList.add_text``;
    - a point a quarter of an advance into a drawn glyph is answered by the
      library's hit test as the position before that character, and a point
      three quarters in as the position after it. The hit test answers the
      nearest position *between* characters, so "the character drawn there" is
      those two answers, and a press, a drag and a copy land on what is seen;
    - every glyph after a tab (``_TAB_ROW``) is drawn a whole number of
      advances in from the row's start.

    **What is not asked, because it is a fact about a diff and not about the
    library**: the row is ASCII in the code face. A character that face lacks
    is set from another at that face's advance, and a row longer than
    ``Columns.reach`` is past what a row this short can answer for; both are
    asked of each diff (``diffs.unfit``).

    **How, with no frame.** The row is one Code block of a document of the
    native module's, styled as the diff view styles its own (the view's grid
    gives the scale and the pitch), laid out, and drawn to buffers and not to a
    window (``Document.draw_to_buffers``): the vertices are the ones a window
    would get. Drawn twice and the second read, so every glyph is on the atlas
    before a texture coordinate is compared. The pixel height the glyphs came
    from is the one whose baked glyph has the first quad's texture
    coordinates; a pen is a quad's left edge less that glyph's bearing.
    ``tests/test_typeset_diffs.py`` holds the answer to what a diff view
    drawn in a window does, where it holds and where it does not.

    **Nothing is left on ImGui's atlas** (``_measured_columns``): a draw to
    buffers rasterises the row at the size it is set at, and a size nothing
    then draws from is one ``theme.keep_baked_sizes`` would keep until the
    epoch moves. The sizes the measurement made are discarded before it
    answers, and the ones it found there are looked up without being made or
    marked used.

    **What it costs**: one diff view made and never drawn, two documents of one
    line laid out, four draws to buffers, 312 hit tests: 3 to 6 ms measured in
    pptmstr on 2026-10-12 (headless, load average 2.6 and 12), once for a
    code style until the fonts are loaded again, and 29 ms more the first time
    a diff view is made in a process. ``load_fonts`` takes it for the diff
    role's size where typeset text is wanted, so a frame pays only after the
    size of the diff role has changed under it.
    """
    try:
        key = _columns_key(theme)
    except Exception as failure:
        return Columns(False, f"was given a theme with no code style ({type(failure).__name__})")
    held = _COLUMNS.get(key)
    if held is None:
        if not can_draw():
            return Columns(False, "is not drawing")
        try:
            held = _measured_columns(theme)
        except Exception as failure:
            held = Columns(False, f"could not be measured ({type(failure).__name__}: {failure})")
        _COLUMNS[key] = held
    return held


def _columns_key(theme: Any) -> object:
    """
    What a measurement is of: the cell the view is made on, the code style the
    row was set in, and the display's density.

    ImGui rasterises a size again for another density, and the advance it
    bakes is a whole pixel of *that* raster: a window dragged from a display
    at one density to one at another, at the same size in points, is another
    measurement. Read where ``theme.keep_baked_sizes`` reads it. **Nobody has
    run this on a display whose density is not 1** (requests H19 and P5 are
    open); the key is what keeps an answer taken at one density from being
    given at another, and says nothing about what the answer there is.
    """
    from imscribe import BlockKind

    return (DIFF_CELL, theme.style(BlockKind.CODE).font, theme.px_per_inch, _density())


def _density() -> tuple[float, float]:
    """
    The display's framebuffer scale as ImGui has it, or (0, 0) with no context.
    """
    im = _imgui()
    if im.get_current_context() is None:
        return (0.0, 0.0)
    scale = im.get_io().display_framebuffer_scale
    return (float(scale.x), float(scale.y))


def _measured_columns(theme: Any) -> Columns:
    """
    ``columns``, uncached, raising whatever the library raises.

    Every baked size the measurement makes on ImGui's atlas is discarded
    before this returns (``_discard_new``), whatever it answers or raises.
    """
    atlas = _imgui().get_io().fonts
    before = [(font, ui_theme.baked_sizes(atlas, font)) for font in atlas.fonts]
    try:
        return _measured(theme)
    finally:
        _discard_new(atlas, before)


def _discard_new(atlas: Any, before: list[tuple[Any, list[tuple[float, float]]]]) -> None:
    """
    Discard every baked size on ``atlas`` that is not in ``before``: the sizes
    made since it was read.

    **Why this is safe where discarding a size is not** (``theme.
    keep_baked_sizes`` has the hazard): ImGui adds a size at the end of its
    pool and closes the pool's gaps only at the next frame, by moving down
    whatever is behind one. The sizes made since ``before`` are the end of the
    pool, so nothing is behind them and nothing moves. A size that was there
    before is not touched: something else draws from it.
    """
    internal = _imgui().internal
    for font, had in before:
        held = {size for size, _ in had}
        for size, _ in ui_theme.baked_sizes(atlas, font):
            if size not in held:
                made = _held_bake(atlas, font, size)
                if made is not None:
                    internal.im_font_atlas_baked_discard(atlas, font, made)


def _held_bake(atlas: Any, font: Any, size: float) -> Any:
    """
    The baked size ``font`` has at ``size`` px, or ``None``: none made, none
    marked used. ``ImFont.get_font_baked`` does both.
    """
    closest = _imgui().internal.im_font_atlas_baked_get_closest_match
    baked = closest(atlas, font, size + 0.01, 0.0)
    return baked if baked is not None and abs(float(baked.size) - size) < 0.005 else None


def _measured(theme: Any) -> Columns:
    """
    The measurement itself: ``_measured_columns`` cleans up after it.
    """
    from imscribe import Diff

    typesetter = _typesetter()
    native = typesetter.module()
    grid = diff_view(Diff(), theme).grid
    said = cell = float(grid.cell)
    base = typesetter.base_style(theme, 0)
    pitched = native.BlockStyle(Scale=grid.scale, LineHeight=grid.row / (grid.em * grid.scale))
    row = _COLUMN_ROW
    inked = [index for index, char in enumerate(row) if not char.isspace()]
    wide = float(len(row) + 8) * max(cell, 1.0) * 2.0
    clip = (-16.0, -16.0, wide, 4.0 * grid.row)
    with native.Document.from_blocks(
        [native.Block(native.BlockKind.Code, row)], [pitched]
    ) as document:
        document.layout(wide, base)
        laid = float(document.caret_rect((0, len(row)))[0]) / len(row)
        document.draw_to_buffers((0.0, 0.0), clip)
        quads = _glyph_quads(native, document.draw_to_buffers((0.0, 0.0), clip)[0])
        if len(quads) != len(inked):
            return Columns(
                False,
                f"draws a row of {len(inked)} characters as {len(quads)} glyphs",
                cell=cell,
                glyphs=len(inked),
            )
        code = face_font("CODE")
        if code is None:
            return Columns(False, "has no code face on ImGui's atlas", cell=cell)
        baked = _bake_of(code, row[inked[0]], quads[0])
        if baked is None:
            return Columns(
                False,
                "draws its glyphs from a pixel height that could not be found",
                cell=cell,
                glyphs=len(inked),
            )
        advance = float(baked.get_char_advance(ord("0")))
        if abs(laid - advance) > abs(said - advance):
            cell = laid
        middle = grid.row / 2.0
        off = mishit = 0
        first: float | None = None
        for index, quad in zip(inked, quads, strict=True):
            pen = quad[0] - float(baked.find_glyph(ord(row[index])).x0)
            if first is None:
                first = pen - index * advance
            if abs(pen - first - index * advance) > 0.01:
                off += 1
            before = document.hit_test((pen + 0.25 * advance, middle)).position[1]
            after = document.hit_test((pen + 0.75 * advance, middle)).position[1]
            if (int(before), int(after)) != (index, index + 1):
                mishit += 1
    with native.Document.from_blocks(
        [native.Block(native.BlockKind.Code, _TAB_ROW)], [pitched]
    ) as document:
        document.layout(wide, base)
        document.draw_to_buffers((0.0, 0.0), clip)
        quads = _glyph_quads(native, document.draw_to_buffers((0.0, 0.0), clip)[0])
        after_tabs = [char for char in _TAB_ROW if not char.isspace()]
        tabs = len(quads) == len(after_tabs) and first is not None and advance > 0.0
        for char, quad in zip(after_tabs, quads, strict=False):
            columns = (quad[0] - float(baked.find_glyph(ord(char)).x0) - (first or 0.0)) / (
                advance or 1.0
            )
            tabs = tabs and abs(columns - round(columns)) * advance <= 0.01
    size = float(baked.size)
    if abs(cell - advance) > COLUMN_EPSILON_PX:
        why = f"lays out a {cell:.3f} px column where ImGui draws {advance:g} px"
    elif off:
        why = f"draws {off} of {len(inked)} glyphs of a row off their column"
    elif mishit:
        why = f"answers a press on {mishit} of {len(inked)} glyphs as a neighbour"
    elif not tabs:
        why = "draws what follows a tab off its column"
    else:
        why = f"keeps a {advance:g} px column: laid out, drawn and hit over a row"
    holds = abs(cell - advance) <= COLUMN_EPSILON_PX and not off and not mishit and tabs
    return Columns(holds, why, cell, advance, size, len(inked), off, mishit, tabs)


def _glyph_quads(native: Any, vertices: bytes) -> list[tuple[float, float, float, float]]:
    """
    The quads among ``Document.draw_to_buffers``' vertices, in the order drawn,
    each as (left, right, u of its left edge, v of its top edge).
    """
    built = native.build_info()
    step = int(built.sizeof_imdrawvert)
    at_pos, at_uv = int(built.offsetof_imdrawvert_pos), int(built.offsetof_imdrawvert_uv)
    out = []
    for start in range(0, len(vertices) - 4 * step + 1, 4 * step):
        corners = [
            (
                *struct.unpack_from("<ff", vertices, start + n * step + at_pos),
                *struct.unpack_from("<ff", vertices, start + n * step + at_uv),
            )
            for n in range(4)
        ]
        xs, _, us, vs = zip(*corners, strict=True)
        out.append((min(xs), max(xs), min(us), min(vs)))
    return out


def _bake_of(font: Any, char: str, quad: tuple[float, float, float, float]) -> Any:
    """
    The ``ImFontBaked`` of ``font`` a quad of ``char`` was drawn from: the one
    whose glyph is on the atlas where the quad's texture coordinates are.
    ``None`` when none is.

    Asked of the sizes the atlas holds for the font and of no other
    (``theme.baked_sizes``): ``ImFont.get_font_baked`` would make each size it
    was asked for, empty, and the atlas would keep it.
    """
    atlas = _imgui().get_io().fonts
    for size, _ in ui_theme.baked_sizes(atlas, font):
        baked = _held_bake(atlas, font, size)
        if baked is None or not baked.is_glyph_loaded(ord(char)):
            continue
        glyph = baked.find_glyph(ord(char))
        if abs(glyph.u0 - quad[2]) < 1e-7 and abs(glyph.v0 - quad[3]) < 1e-7:
            return baked
    return None


def unload() -> None:
    """
    Take the library's context down. For whoever destroys the ImGui context, first.

    ``can_draw()`` is false afterwards and ``load_fonts()`` makes it true again.
    Does nothing when the fonts were never asked for. Never raises.
    """
    if not _GATE.asked:
        return
    _GATE.asked = False
    _GATE.family = None
    _GATE.accepted = _GATE.loaded = False
    _GATE.failure = _GATE.stopping = None
    _COLUMNS.clear()
    try:
        _typesetter().unload()
    except Exception as failure:
        failed(failure)


def pre_frame() -> None:
    """
    The seam's between-frames work. Call before each ``new_frame``. Never raises.

    Both cheap when there is nothing to do, which is nearly always, and neither
    is done while the library cannot draw.

    **A library that stopped inside a draw of the frame before is off from
    here** (``stopped``).

    **A family the library was not given is given to it.** The operator chose
    another proportional family; the fonts are loaded again, here and not in the
    click, because a load remakes the library's context under whatever is
    drawing.

    **The size of running text follows the UI font.** The library draws at the
    size its theme names and not at ImGui's current font size, and
    ``hello_imgui`` scales the UI font for the display, so the size is read from
    what ImGui reports. A change moves the epoch.
    """
    if _GATE.stopping is not None:
        _GATE.failure, _GATE.stopping = _GATE.stopping, None
    if not can_draw():
        return
    try:
        if roles.proportional().name != _GATE.family:
            load_fonts(_GATE.bold)
        size = _live_font_size()
        if size is not None:
            roles.follow(size)
    except Exception as failure:
        failed(failure)


def epoch() -> int:
    """
    What moves whenever text is about to be drawn in other faces or at other
    sizes than before: the seam's epoch (``roles.moved``).

    For ``theme.keep_baked_sizes``, which keeps the sizes on ImGui's atlas for
    as long as this stands still and lets go of them all when it moves. Read
    whether or not the library can draw: the roles size the panes' own text too.
    """
    return roles._STATE.epoch


def _live_font_size() -> float | None:
    """
    The UI font's size as ImGui has it between frames, or ``None`` before the first.

    Between frames the font stack is empty, so this is the default font's size with
    the display scale applied and never a size some pane had pushed.
    """
    im = _imgui()
    if im.get_current_context() is None or im.get_frame_count() < 1:
        return None
    size = float(im.get_font_size())
    return size if size >= 1.0 else None


roles.bind(can_draw)
