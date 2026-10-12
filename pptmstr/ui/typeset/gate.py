"""
Whether the text library can draw, its fonts, and the sentence when it cannot.

The library is ``advtext`` drawing through imtypeset, a native library built for
one ``imgui-bundle`` wheel and found through two environment variables.
``advtext.typesetter`` is its gate; this module is pptmstr's side of that gate
and the only place the two meet for anything but a diff model (``diffs``).

**pptmstr is still pptmstr when the library cannot draw.** Four things can be
wrong -- it is not installed, it was refused, its fonts did not load, it stopped
while drawing -- and a fifth is anything raised in here. Each leaves
``can_draw()`` false, so every pane draws with its own renderer
(``roles.enabled``), and each is one sentence the Text menu and the settings
dialog say (``unavailable_reason``). So **no function here raises**: the library
is imported inside the functions that use it, and whatever one of them raises
is caught, kept as the sentence, and costs the typeset path and nothing else.

**Fonts are loaded once the UI font is, at the end of hello_imgui's
``load_additional_fonts``**, and never from ``post_init``: the runner empties the
atlas before that callback, so a face added earlier is gone by the first frame.
The library puts its own copy of each face on the atlas, after the application's
first font, which stays ``Fonts[0]``. Two files are pptmstr's to name before it
does (``advtext.register_font``): the vendored bold monospace, without which the
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

**A change of proportional family loads them again, between frames**
(``pre_frame``): the library reads its files when its context is made, so
another file means another context. Run in a window on 2026-10-11: 11 ms, a view
kept across it draws, and **each load leaves seven more fonts on ImGui's atlas**
that nothing takes off. That is the cost of an operator's click on the family
list and of nothing else.

**The library's context belongs to one ImGui context** (``_Gate.home``).
``advtext.typesetter.ready()`` stays true after the ImGui context its fonts are
on is destroyed, and a view then draws without raising, into an atlas that is
gone. So ``can_draw()`` also asks whether the current ImGui context is the one
the fonts were loaded in, and whoever destroys that context calls ``unload()``
first: ``app`` in ``before_exit``, the tests in ``tests/conftest.py``.

Reached past ``advtext.__all__``: the module ``advtext.typesetter`` itself, for
``module``, ``trouble``, ``unload`` and ``LIBRARY_VARIABLE``, which the library
lists as reached through their module with no promise attached, and for ``kind``
where the library has one (``_kind``); and the module
``advtext.markdown_rules``, for its private ``_parser``, as said below. A test
reads this package's imports and holds them to those two names.

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

**One reach has no promise of any kind** (``face_font``): the ``ImFont`` the
library draws a face with, for a native ImGui line in that face. It is found the
way the library's own demonstration finds it, through the native module's
``font_atlas_index``; a supported name is request P14 of ``HANDOFF-advtext.md``.
It is here once, it answers ``None`` when anything about it fails, and a test
fails when the library stops answering it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ... import pairing
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
The kinds ``library`` answers with. They are the values of advtext's
``typesetter.Kind`` where it has one, so a kind it reports is one of these or a
word of its own, which is said as it is.
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
    # The ImGui context they were asked for in: where the library's context is,
    # if it made one. Kept whether or not the load succeeded, so that whoever
    # destroys that context knows there may be something to unload first.
    home: Any = None
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
    from advtext import typesetter

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


def can_draw() -> bool:
    """
    Whether the library can draw *in the ImGui context that is current*.

    Its fonts are loaded, it has not been switched off since, and the current
    context is the one they were loaded in. Never raises. This is the library's
    half of ``roles.enabled``. In the frame the library stopped inside a draw
    the answer is the one the frame began with (``stopped``).
    """
    if not _GATE.loaded or _GATE.failure is not None:
        return False
    if _GATE.stopping is not None:
        return True
    try:
        return bool(_typesetter().ready()) and _imgui().get_current_context() == _GATE.home
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
    refused, fonts not loaded, stopped while drawing, or a failure in the gate
    itself. The library's own words are quoted, not paraphrased.
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
        if not typesetter.ready():
            noted = str(typesetter.trouble())
            return f"the typesetting library's fonts did not load ({noted or 'no reason given'})"
    except Exception as failure:
        return f"the typesetting library failed and is not in use ({type(failure).__name__})"
    return "the typesetting library's fonts are in another window's context"


def _kind(typesetter: Any) -> str:
    """
    Which kind of thing is so of the library, as one of advtext's own words for it.

    **The one place pptmstr asks whether the library can say this itself.** An
    advtext that has ``typesetter.kind()`` is asked, and what it answers is
    final: it knows of routes to a library, and of reasons for there being
    none, that the two variables do not show. One that has not is read by what
    it does answer: accepted is ``READY``, a library that was named and is not
    in use was ``REFUSED``, and no library named is ``NOT_INSTALLED``.
    """
    asked = getattr(typesetter, "kind", None)
    if callable(asked):
        found = asked()
        return str(getattr(found, "value", found))
    if typesetter.module() is not None:
        return READY
    return REFUSED if pairing.named(typesetter.LIBRARY_VARIABLE) else NOT_INSTALLED


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
    order: the library reads the variables the first time it is asked and not
    again, and what is left in the environment after that is only what a
    session's process would inherit. The first asking is the library's import
    and its check against the wheel, which the font load would otherwise have
    been the first to pay. Never raises: an advtext that is not there has
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
    from advtext import parse_markdown

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
    from advtext import markdown_rules

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
    from advtext import MarkdownStream, Theme

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
    _GATE.home = None
    _GATE.family = None
    _GATE.bold = bold
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
            _GATE.home = _imgui().get_current_context()
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
    return can_draw()


def _register(bold: Path | None, prose: Path | None) -> None:
    """
    Name pptmstr's two files in the library's registry, which is the process's.

    Only where the registry does not already say so: a registration drops every
    metric the library has read from a font file.
    """
    from advtext import Face, register_font, registered_fonts, unregister_font

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
    and when the reach itself fails; ``imgui.push_font`` reads ``None`` as "keep
    the current font", so a caller that pushes the answer falls back to the UI
    font. Asked every time: the library's fonts move on the atlas when it loads
    them again. Never raises.

    The module docstring says what kind of reach this is. Measured on ASCII in
    Inconsolata (``scripts/measure_typeset_advances.py``, the control): text
    ImGui draws from this entry lands where the chrome's own font puts it.
    """
    if not can_draw():
        return None
    try:
        typesetter = _typesetter()
        index = typesetter.module().font_atlas_index(typesetter.FACE_ROLES[face])
        fonts = _imgui().get_io().fonts.fonts
        if index is None or not 0 <= index < len(fonts):
            return None
        return fonts[index]
    except Exception:
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
    _GATE.home = None
    _GATE.family = None
    _GATE.accepted = _GATE.loaded = False
    _GATE.failure = _GATE.stopping = None
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
