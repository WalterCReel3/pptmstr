"""
Whether the text library can draw, its fonts, and the sentence when it cannot.

The library is ``imscribe`` drawing through imtypeset, a native library built for
one ``imgui-bundle`` wheel: an installed distribution that imscribe finds by
itself, or a built pair two environment variables name (``pptmstr.pairing``).
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
That stop holds for the process: ``pre_frame`` makes it the gate's failure, and
only a load of the fonts clears that.

**So pptmstr's own code that runs inside such a call catches what it raises, at
its own edge** (``own_defect``): a callback the library makes from inside its
draw (the link handler, a copy, the captioned stream's blocks) or pptmstr's work
around a call into the library (``formula``). It logs the exception as
pptmstr's defect, at ERROR with its traceback, and gives a lesser answer for its
own text: the library is not stopped for it, and no other pane notices.
``imscribe.TypesetUnavailable`` passes such an edge as it is. **The rule is whose
code raised, not what was being attempted**: each call into the library that
such code makes is wrapped narrowly in ``into_library`` of its own, so what it
raises is still the library stopping.

**A draw that set less than it was handed says so two ways, and neither is an
exception** (``noted``). A view's ``draw`` answers false when a block in view
could not be set as asked, and the library notes an edit it cut --
``typesetter.trouble()`` -- while the draw over it answers true. Measured in
pptmstr on 2026-10-12 (imscribe ``38f5fac``, imtypeset ``c9f8902``): a table row
of more than 1,024 cells loses the rest and draws true; a block over 16 MiB is
cut and draws false while it is in view; no character does either. A lone
surrogate is set as U+FFFD and noted nowhere (the library the tree pins now,
by ``tests/test_typeset_views.py``); session text holds none by the time it
is drawn, since one is written out where that text enters the store
(``pptmstr.drawable``), so this is of text handed to the seam any other way.
``views`` and ``diffs`` read both.

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
below; and ``code_face_lacks`` from the module ``imscribe.ui.typeset_diff_view``,
which lists it and which imscribe names the supported way to ask it (P39). The
session pane's find reaches three more, from ``find``: ``marks_for`` from
``imscribe.typeset_find`` and ``KIND_CODE`` and ``KIND_PARAGRAPH`` from
``imscribe.description``, each on its module's own list; ``find``'s docstring
says why. A test reads this package's imports and holds them to exactly these
(``PAST_THE_FACADE`` and ``BEHIND_THE_FACADE`` in ``tests/test_typeset_gate.py``).
One reach is on no list: the native module's documents (``columns``).

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
The same questions are asked of literal text through the view and the block
it is drawn with (``literal_columns``, ``literal_view``).
"""

from __future__ import annotations

import math
import os
import re
import struct
import traceback
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
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
_BARE_ADDRESSES_RULE = "pptmstr_bare_addresses"
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
    # The display's density as `pre_frame` last read it, or `None` before it has.
    density: tuple[float, float] | None = None


_GATE = _Gate()


def _typesetter() -> Any:
    from imscribe import typesetter

    return typesetter


def _imgui() -> Any:
    from imgui_bundle import imgui

    return imgui


_DEFECTS_SAID: set[str] = set()
_DEFECTS_KEPT = 256


def own_defect(doing: str, failure: BaseException) -> None:
    """
    Log what pptmstr's own code raised while ``doing``, inside or around a call
    into the library: at ERROR with its traceback, once for each message, as
    pptmstr's defect. The caller gives a lesser answer of its own; nothing here
    stops the library. Never raises.
    """
    said = f"pptmstr's own code raised {type(failure).__name__} while {doing}: {failure}"
    if said in _DEFECTS_SAID:
        return
    if len(_DEFECTS_SAID) < _DEFECTS_KEPT:
        _DEFECTS_SAID.add(said)
    try:
        trace = "".join(traceback.format_exception(failure))
    except Exception:
        trace = ""
    LOG.error("typeset", f"{said}\n{trace}".rstrip())


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
        return not_installed(why)
    if kind == REFUSED:
        return f"the typesetting library was refused ({why})"
    return f"the typesetting library is not in use ({kind}: {why})"


def not_installed(why: str) -> str:
    """
    The sentence for a library imscribe calls not installed, worded from what
    pptmstr knows that imscribe does not say. ``why`` is imscribe's reason.

    The one place it is worded: the Text menu, the settings dialog, the
    ``typeset text:`` line of ``pptmstr --version`` and a test's skip reason
    all reach it (``_unaccepted``). imscribe says which of two ways there is
    no library (``typesetter.ABSENT_REASON``, ``SWITCHED_OFF_REASON``; P43),
    in sentences about the variables; pptmstr knows the value of the switch
    and whether this checkout built a pair. So:

    - ``IMSCRIBE_NO_TYPESET`` on: it is switched off, with the value it is on at;
    - no imtypeset installed, no pair built in this checkout's ``.deps`` and
      neither variable naming one: not installed, with nothing more to say;
    - anything else: imscribe's own reason, quoted.

    Never raises.
    """
    try:
        if pairing.asks(os.environ, pairing.DISABLE_VARIABLE):
            value = os.environ[pairing.DISABLE_VARIABLE].strip()
            return f"typeset text is switched off ({pairing.DISABLE_VARIABLE}={value})"
        named = any(
            pairing.named(name) for name in (pairing.LIBRARY_VARIABLE, pairing.PACKAGE_VARIABLE)
        )
        root = pairing.checkout()
        built = root is not None and (root / pairing.DEPS_DIR / pairing.PAIR_DIR).is_dir()
        if not named and not built and pairing.installed() is None:
            return "the typesetting library is not installed"
    except Exception:
        pass
    return f"the typesetting library is not installed ({why})"


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


def pairing_said() -> dict[str, str | None]:
    """
    What the library says of itself, for ``pptmstr --version`` and for the first
    lines of a test run: imscribe's ``typesetter.pairing()``, whose keys are
    its own -- the route the package was found by, the library's path and
    version, the headers it was compiled against, how its gate accepted it,
    and the variables that were set and not read. The first call may be the
    library's import. Empty where imscribe cannot be asked. Never raises.
    """
    try:
        return dict(_typesetter().pairing())
    except Exception:
        return {}


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


_FORMULAS_KEPT = 256
_FORMULAS: OrderedDict[tuple[Any, ...], tuple[Any, Any, str]] = OrderedDict()
"""
What ``formula`` answered, by the body, the theme it was read with and the scale
it was asked at: the last few hundred, because a stream made again reads every
fence again and a settled document is read again when its text changes. A theme
is a dict-holding dataclass and has no hash, so it is held by identity, and kept
in the answer so that its identity is not another's: the seam's themes are made
once an epoch (``themes.demoted``), so a theme of another size is another key.
"""

_REFUSED: OrderedDict[tuple[Any, ...], tuple[Any, Any, str]] = OrderedDict()
"""
What a view refused after ``formula`` said it would set it (``refused``), by the
body and the theme, at every scale, and held apart from ``_FORMULAS``: only
another such refusal evicts one, so reading other formulas cannot make the view
meet it again.
"""

_SIZES: OrderedDict[tuple[Any, ...], tuple[Any, tuple[float, float, float]]] = OrderedDict()
"""
What ``formula_size`` measured, keyed and kept as ``_FORMULAS`` is.
"""

_NOT_ONE_FORMULA = "the fence does not hold one display formula"

OWN_ERROR = "pptmstr error"
"""
The reason a formula is code under when pptmstr's own code raised while asking
about it (``formula``, ``own_defect``).
"""

_READING_FORMULA = into_library("reading a formula")


def formula(body: str, theme: Any, scale: float = 1.0) -> tuple[Any, str]:
    """
    The body of a ```` ```math ```` fence as one display formula, set in
    ``theme`` at ``scale`` times its text's size: ``(block, "")``, or
    ``(None, reason)`` where it will not be set.

    Read alone, as ``$$`` + body + ``$$`` with mathematics on: **nothing else
    is ever read with mathematics on**, so a ``$`` in prose is text. The lines
    of the body are joined into one formula by the reader. A body that reads as
    anything but one closed display formula (empty, or holding a ``$$`` line of
    its own) is refused with ``_NOT_ONE_FORMULA``.

    **The library is asked first whether it will set it**, and a refusal is
    answered here and never handed to a view: a view that meets one draws its
    source and answers false, which takes the whole entry it is in off the
    library (``views._short``). The block's own text and runs are laid out as a
    paragraph in ``typesetter.base_style(theme)``, the style a document of that
    theme is laid out in, so it is asked at the size the document sets it at;
    a theme of another size is another key here and is asked again. The
    library's reason is the answer's.

    **At a scale other than 1 it is asked at that size**: the block is the one
    read at 1 with every run's ``scale`` set (``_scaled``), the size the view
    then draws it at, and that block is what the library is asked about. One
    refused at 1 is refused at every scale without asking again.

    What a view still refuses after this said yes is kept by ``refused``, and
    answered from here from then on, at every scale.

    The read and the pre-check's calls into the native module (``_refusal``)
    are calls into the library (``into_library``): what they raise stops the
    library, as a call into it does anywhere. What pptmstr's own code around
    them raises is pptmstr's defect (``own_defect``): the answer is then
    ``(None, OWN_ERROR)``, not kept, so the fence is code and the library is
    not stopped.
    """
    from imscribe import TypesetUnavailable

    try:
        return _formula(body, theme, scale)
    except TypesetUnavailable:
        raise
    except Exception as failure:
        own_defect("asking whether a formula will be set", failure)
        return None, OWN_ERROR


def _held(answers: OrderedDict[Any, Any], key: tuple[Any, ...], theme: Any) -> Any:
    held = answers.get(key)
    if held is None or held[0] is not theme:
        return None
    answers.move_to_end(key)
    return held


def _formula(body: str, theme: Any, scale: float) -> tuple[Any, str]:
    from imscribe import MarkdownText, parse_markdown

    for held in (
        _held(_REFUSED, (body, id(theme)), theme),
        _held(_FORMULAS, (body, id(theme), scale), theme),
    ):
        if held is not None:
            return held[1], held[2]
    answer: tuple[Any, str] = (None, _NOT_ONE_FORMULA)
    if scale != 1.0:
        block, why = _formula(body, theme, 1.0)
        answer = (None, why)
        if block is not None:
            block = _scaled(block, scale)
            why_not = _refusal(block, theme)
            answer = (block, "") if why_not is None else (None, why_not)
    else:
        with _READING_FORMULA:
            read = parse_markdown(f"$$\n{body}\n$$", theme, references=False, math=True)
        blocks = read.blocks
        if len(blocks) == 1 and isinstance(blocks[0], MarkdownText):
            block = blocks[0]
            found = block.formulas
            if len(found) == 1 and found[0].display and found[0].state == "set":
                why_not = _refusal(block, theme)
                answer = (block, "") if why_not is None else (None, why_not)
    _keep(_FORMULAS, (body, id(theme), scale), theme, answer)
    return answer


def _scaled(block: Any, scale: float) -> Any:
    """
    A formula block read at 1 with every run set at ``scale`` times its size.
    """
    from dataclasses import replace

    return replace(block, runs=tuple(run._replace(scale=scale) for run in block.runs))


def refused(body: str, theme: Any, reason: str) -> None:
    """
    Keep that a view refused the formula of ``body`` in ``theme``, which
    ``formula`` had said it would set: ``formula`` answers the refusal from now
    on, at every scale. Whatever size it was refused at, the fence is then code:
    no formula has been found that the library refuses at one size and sets at
    another (``tests/test_math_fence.py``), so a smaller size is not tried.
    """
    _keep(_REFUSED, (body, id(theme)), theme, (None, reason))


def _keep(answers: OrderedDict[Any, Any], key: tuple[Any, ...], theme: Any, answer: Any) -> None:
    answers[key] = (theme, *answer)
    answers.move_to_end(key)
    while len(answers) > _FORMULAS_KEPT:
        answers.popitem(last=False)


_MEASURING_FORMULA = into_library("measuring a formula")


def formula_size(block: Any, body: str, theme: Any) -> tuple[float, float, float]:
    """
    How wide and how tall a formula block read at 1 (``formula``) is set in
    ``theme``, and the height of a line of that theme's paragraphs, in pixels:
    what ``views.formula_scale`` sizes it by. Every call into the native module
    is the library's (``into_library``).

    Measured once at 1, because the library sets a formula at a run's scale
    linearly: ``\\sum_{i=1}^{n} i = \\frac{n(n+1)}{2}`` is 95.43 x 37.66 px at
    1 and 190.86 x 75.31 at 2 (2026-10-22, the default theme), so its size at
    any scale is this one times the scale.
    """
    from imscribe import BlockKind, px_from_sp

    held = _held(_SIZES, (body, id(theme)), theme)
    if held is not None:
        size: tuple[float, float, float] = held[1]
        return size
    typesetter = _typesetter()
    with _MEASURING_FORMULA:
        ts = typesetter.module()
        if ts is None:
            return 0.0, 0.0, 0.0
        runs = [ts.Run(*run) for run in block.runs]
        with ts.Paragraph() as paragraph:
            paragraph.set_text(block.text, runs)
            paragraph.layout(_ASKED_WIDTH_PX, typesetter.base_style(theme))
            lines = paragraph.lines()
        # The pitch of the theme's paragraph lines, which follows the size the
        # operator set for the text.
        line = float(px_from_sp(theme.style(BlockKind.PARAGRAPH).leading, theme.px_per_inch))
    left = min((float(line_.min[0]) for line_ in lines), default=0.0)
    right = max((float(line_.max[0]) for line_ in lines), default=0.0)
    top = min((float(line_.min[1]) for line_ in lines), default=0.0)
    foot = max((float(line_.max[1]) for line_ in lines), default=0.0)
    size = (right - left, foot - top, line)
    _keep(_SIZES, (body, id(theme)), theme, (size,))
    return size


_ASKED_WIDTH_PX = 100_000.0
"""
The measure a formula is asked at. A display formula wider than its measure is
set and runs past it; it is not refused for its width.
"""


_ASKING_FORMULA = into_library("asking whether a formula will be set")


def _refusal(block: Any, theme: Any) -> str | None:
    """
    Why the library would not set the formula of a display-formula block in
    ``theme``, or ``None`` when it would.

    The calls into imscribe's typesetter and the native module (``_laid_formula``)
    are the library's and are made inside ``into_library``: what they raise stops
    the library. The reason made of their answer is pptmstr's (``_refusal_said``).
    """
    typesetter = _typesetter()
    with _ASKING_FORMULA:
        ts = typesetter.module()
        if ts is None:
            return f"the library is not in use: {typesetter.reason()}"
        first, error = _laid_formula(ts, typesetter, block, theme)
    return _refusal_said(first, error)


def _laid_formula(
    ts: Any, typesetter: Any, block: Any, theme: Any
) -> tuple[tuple[bool, str] | None, str]:
    """
    The block laid out as a paragraph by the native module: whether its first
    formula was refused and why (``None`` where none was found), and the
    paragraph's error. Every call here is the library's.
    """
    runs = [ts.Run(*run) for run in block.runs]
    with ts.Paragraph() as paragraph:
        paragraph.set_text(block.text, runs)
        paragraph.layout(_ASKED_WIDTH_PX, typesetter.base_style(theme))
        found = paragraph.formulas()
        error = paragraph.error
        said = str(error.message or error.code)
    first = (bool(found[0].refused), str(found[0].reason)) if found else None
    return first, said


def _refusal_said(first: tuple[bool, str] | None, error: str) -> str | None:
    """
    The reason ``_refusal`` answers, from what the library said of the formula.
    """
    if first is None:
        return f"the library found no formula in it ({error})"
    refused, reason = first
    if refused:
        return reason or "the library would not set it"
    return None


def _reader() -> None:
    """
    Make the library's markdown reader show what was written, and check that it does.

    Six changes to the one parser object ``read`` and the library's stream
    read with (reference definitions off, mathematics off), all made on the
    parser and not on the text:

    - The rules in ``_HIDING_RULES`` are switched off. Raw HTML is then text, as
      CommonMark's own ``html: false`` reading has it -- a tag, a comment opener, a
      generic's ``<T>`` -- ``![alt](url)`` is an exclamation mark and a link, and
      a character reference is the characters it was written with.
    - A core rule (``_bare_addresses``) makes a bare ``http(s)://`` address in
      text a link, before the next one runs.
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
    if _BARE_ADDRESSES_RULE not in parser.core.ruler.get_all_rules():
        # Before the destinations are written, so that a bare address is a link
        # whose text is its destination and is not said twice.
        parser.core.ruler.before(_DESTINATIONS_RULE, _BARE_ADDRESSES_RULE, _bare_addresses)
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


_BARE_SCHEME = re.compile(r"https?://", re.IGNORECASE)
# White space, <, >, ", and CJK and full-width punctuation: the CJK Symbols and
# Punctuation block, and the full-width forms of ASCII punctuation with the
# half-width CJK marks after them.
_BARE_ENDS = re.compile('[\\s<>"\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65]')
_BARE_ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")
_BARE_NOT_AFTER = frozenset("+-./:@")
_BARE_TRAILING = frozenset("?!.,:*_~'")


def _bare_addresses(state: Any) -> None:
    """
    Make each bare ``http://`` or ``https://`` address in text a link whose text
    is the address as written.

    Modelled on GFM's extended URL autolink
    (https://github.github.com/gfm/#autolinks-extension-), read 2026-10-20, and
    narrower than it on purpose: model output is read for what it says, so a
    link the writer did not mean costs more than an address left as text.

    - Only the two schemes, in any case. No ``www.``-only address and no bare
      email, which GFM also links: ``see www.x.org`` or ``a@b.c`` in prose is
      as often a name as a destination.
    - It starts where nothing that could be part of a scheme (an ASCII letter or
      digit, ``+``, ``-``, ``.``: RFC 3986 section 3.1) and no ``/``, ``:`` or
      ``@`` is before it, so ``xhttps://``, ``git+https://`` and an ``http://`` inside
      another address are not one. GFM's list is narrower (line start, space,
      ``*_~(``); this one also lets an address start after a quote or a bracket.
      Only the address's own text token is looked at. The token before one that
      starts a text token is a code span, a link or emphasis, which ends in the
      source in a backtick, ``>``, ``)``, ``*``, ``_`` or ``~``; none of those
      stops an address here, so looking back across tokens would decide nothing
      differently. ``` `x`https://a.org ``` is linked.
    - At least one letter or digit follows the ``//``. GFM asks for a domain with
      a dot in it; ``http://localhost:8000`` is an address agents write.
    - It ends at white space, ``<``, ``>`` or ``"``. GFM ends at the first two;
      the other two are not URI characters (RFC 3986), and here raw HTML is
      text, so ``href="https://x.org/">`` is an address and its quote and bracket.
      It also ends at CJK and full-width punctuation (``、。，！？）「」``), which
      GFM takes into the address: prose in those scripts puts no space after one.
    - An address whose authority holds an ``@`` is not linked. A user before the
      host is how ``https://docs.python.org@evil.example/`` reads as one site and
      goes to another, and ``links.followable`` refuses it anyway.
    - Then, until none applies, GFM's trims as cmark-gfm makes them: a trailing
      ``?!.,:*_~`` (and ``'``) goes; a trailing ``)`` goes while the address has
      more ``)`` than ``(``, so ``(see https://x.org/a)`` and
      ``https://en.wikipedia.org/wiki/Foo_(bar)`` are both right; a trailing
      ``;`` goes, and the whole of an entity-like ``&hl;`` with it. A trailing
      ``]`` stays, as it does in GFM.

    Its destination is the parser's own ``normalizeLink`` of it read as
    CommonMark reads it -- a backslash before ASCII punctuation is an escape, so
    ``Foo\\_(bar)`` goes to ``Foo_(bar)`` -- with the scheme lower-cased first (the
    reader punycodes a host only for a lower-case scheme), and an address
    ``validateLink`` refuses stays text, as markdown-it does for ``<...>``. The
    text keeps the backslash (``_written_escapes``), so it differs from the
    destination and ``_show_destinations`` says where the click goes; so it does
    for a lookalike host and its ``xn--`` destination.

    Only ``text`` tokens are read, and none inside a link: a code span, a fence, a
    code block and mathematics are never ``text``, and an autolink or a
    ``[t](u)`` is inside a link. The text is cut, never rewritten, so what is set
    is the characters that were written. An address an emphasis rule broke into
    pieces (``https://x.org/*a*``) is linked up to the break, as markdown-it's
    own linkify would.

    Not ``linkify-it-py``, which markdown-it's ``linkify`` option needs: one more
    dependency for this grammar, and its fuzzy matching links a bare
    ``example.com`` and an email, which is the recall this rule declines.
    """
    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        out: list[Any] = []
        inside = 0
        for child in token.children:
            if child.type == "link_open":
                inside += 1
            elif child.type == "link_close":
                inside = max(0, inside - 1)
            elif child.type == "text" and not inside:
                out += _linked(state.md, child)
                continue
            out.append(child)
        token.children = out


def _linked(md: Any, text: Any) -> list[Any]:
    """
    One text token as itself, or cut into text and links around each bare address.
    """
    source = str(text.content)
    Token = type(text)
    pieces: list[Any] = []
    done = 0

    def said(content: str) -> None:
        if content:
            piece = Token("text", "", 0)
            piece.content = content
            piece.level = text.level
            pieces.append(piece)

    for found in _BARE_SCHEME.finditer(source):
        start = found.start()
        if start < done:
            continue
        before = source[start - 1] if start else ""
        if (before.isascii() and before.isalnum()) or before in _BARE_NOT_AFTER:
            continue
        host = found.end()
        if host >= len(source) or not source[host].isalnum():
            continue
        ending = _BARE_ENDS.search(source, host)
        end = _trimmed(source, start, ending.start() if ending else len(source))
        after_scheme = _BARE_ESCAPED.sub(r"\1", source[host:end])
        if "@" in re.split(r"[/?#]", after_scheme, maxsplit=1)[0]:
            continue
        href = md.normalizeLink(found.group().lower() + after_scheme)
        if not md.validateLink(href):
            continue
        said(source[done:start])
        opened = Token("link_open", "a", 1)
        opened.attrs = {"href": href}
        opened.markup = "linkify"
        opened.info = "auto"
        opened.level = text.level
        pieces.append(opened)
        said(source[start:end])
        closed = Token("link_close", "a", -1)
        closed.markup = "linkify"
        closed.info = "auto"
        closed.level = text.level
        pieces.append(closed)
        done = end
    if not done:
        return [text]
    said(source[done:])
    return pieces


def _trimmed(source: str, start: int, end: int) -> int:
    """
    Where an address read from ``start`` to ``end`` ends once GFM's trailing
    punctuation, unbalanced ``)`` and entity-like ``;`` are off it.
    """
    while end > start:
        last = source[end - 1]
        if last in _BARE_TRAILING:
            end -= 1
        elif last == ")" and source.count(")", start, end) > source.count("(", start, end):
            end -= 1
        elif last == ";":
            at = end - 2
            while at > start and source[at].isalnum():
                at -= 1
            end = at if at < end - 2 and source[at] == "&" else end - 1
        else:
            break
    return end


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
            # And literal text's, which the log asks of its first tool result.
            literal_columns(themes.document_theme(), themes.literal_style(roles.Role.LITERAL))
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


def diff_view(model: Any, theme: Any, **given: Any) -> Any:
    """
    A diff view of the library's as pptmstr makes one: with no ``cell``.

    The library lays a diff out on the whole-pixel cell ImGui draws the code
    face at when it is given nothing, and its ``cell=`` keyword is deprecated:
    accepted, ignored, and a ``DeprecationWarning`` at the caller's line. So
    nothing is passed, and ``columns`` measures whether what that gives keeps
    a column.

    **The one place a diff view is made**: the view a pane draws
    (``diffs.diff``) and the view whose grid ``columns`` measures are both
    made here, so what was measured is the configuration that is drawn.
    Raises what the library raises.
    """
    from imscribe import TypesetDiffView

    return TypesetDiffView(model, theme, **given)


def diff_fences(held: Any = None) -> Any:
    """
    The library's hook for a fenced diff in a settled document, made as
    ``diff_view`` makes a view: with no ``cell``. Raises what the library
    raises.

    **Asked for each fence, with what the asking before answered** (``held``):
    the hook is one object for as long as the process runs, because what it
    knows of a fence it has read is found through it.
    """
    from imscribe import TypesetDiffFences

    return TypesetDiffFences() if held is None else held


def code_lacks(text: str) -> tuple[str, ...]:
    """
    The characters of ``text`` above ASCII that the library's code face has no
    glyph for, each once, in the order met.

    The library sets such a character in whole cells from another face where
    one has it, two for some, and its diff view counts the cells it draws
    (``diffs.unfit`` has the measurement). The library's own answer,
    ``code_face_lacks`` of its diff view's module, which it names as the
    supported way to ask (pptmstr's P39): one call into the library a distinct
    character. Of the text as handed to the view, where a control or an
    invisible character is a placeholder, which ``diffs.unfit`` puts there
    before it asks. Raises what the library raises: ``TypesetUnavailable``
    where it cannot be asked, before its fonts are loaded among them.
    """
    if text.isascii():
        return ()
    from imscribe.ui.typeset_diff_view import code_face_lacks

    return tuple(code_face_lacks(text))


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

The library's own answer for a cell is a float a little off the whole pixel
(8.000008583 for 8 at a pixel height of 16), and the view answers the whole
pixel wherever that came within a thousandth of it. So the measured row's own
width is read as well -- where the caret after its last character is, over its
characters -- and the farther of the two is what is held to this bound
(``Columns.cell``): 8.000009 at 16, and between 1e-6 and 1.6e-5 px out at the
heights from 12 to 24. The row's pens are compared too (``Columns.off``), so
the bound decides only what the probe row is too short to show, and **what it
decides is bounded by a row's length** (``Columns.reach``): an error of e a
column is half a pixel once the row is 0.5 / e columns long -- 5,000 columns
at this bound, 54,418 at a pixel height of 16, 33,124 at 14 and 30,474 at 20,
which is the least from 12 to 24. Measured in pptmstr on 2026-10-13 (imscribe
``0.2.0b1``, the imtypeset ``0.4.1.192900`` wheel, a row of 1,000 set in a
document with no window at 16 px): **the glyphs are drawn on the whole pixel,
the last on its cell to the float, and it is the caret that is out**, 0.0088
px right after 1,000 characters, which is what a press and a selection go by.
A diff with a row longer than the reach is its pane's own rows
(``diffs.unfit``); a minified file is one line. Not measured at this pin: a
row as long as the reach in a diff view, which was 1 px out at 100,000
columns on imtypeset 0.3.
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
    What a measurement is of: the code style the row was set in, and the
    display's density.

    ImGui rasterises a size again for another density, and the advance it
    bakes is a whole pixel of *that* raster: a window dragged from a display
    at one density to one at another, at the same size in points, is another
    measurement. Read where ``theme.keep_baked_sizes`` reads it. **Nobody has
    run this on a display whose density is not 1** (requests H19 and P5 are
    open); the key is what keeps an answer taken at one density from being
    given at another, and says nothing about what the answer there is.
    """
    from imscribe import BlockKind

    return (theme.style(BlockKind.CODE).font, theme.px_per_inch, _density())


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
    return _without_new_sizes(lambda: _measured(theme))


def _without_new_sizes(measure: Callable[[], Columns]) -> Columns:
    """
    What ``measure`` answers, with every baked size it made on ImGui's atlas
    discarded before this returns (``_discard_new``), whatever it answers or
    raises.
    """
    atlas = _imgui().get_io().fonts
    before = [(font, ui_theme.baked_sizes(atlas, font)) for font in atlas.fonts]
    try:
        return measure()
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
    The measurement of a diff view's row: ``_measured_columns`` cleans up after it.

    The row is one Code block of a document of the native module's, styled as the
    diff view styles its own (its grid gives the scale and the pitch).
    """
    from imscribe import Diff

    typesetter = _typesetter()
    native = typesetter.module()
    grid = diff_view(Diff(), theme).grid
    said = float(grid.cell)
    base = typesetter.base_style(theme, 0)
    pitched = native.BlockStyle(Scale=grid.scale, LineHeight=grid.row / (grid.em * grid.scale))
    wide = float(len(_COLUMN_ROW) + 8) * max(said, 1.0) * 2.0

    @contextmanager
    def laid(row: str) -> Iterator[tuple[Any, float]]:
        with native.Document.from_blocks(
            [native.Block(native.BlockKind.Code, row)], [pitched]
        ) as document:
            document.layout(wide, base)
            yield document, grid.row / 2.0

    return _row_columns(laid, said, (-16.0, -16.0, wide, 4.0 * grid.row))


def _row_columns(
    laid: Callable[[str], AbstractContextManager[tuple[Any, float]]],
    said: float | None,
    clip: tuple[float, float, float, float],
) -> Columns:
    """
    ``columns``' questions of a row of code, wherever it was set: ``laid(row)``
    gives a native document holding the row as its one block, laid out, with
    the y of the row's middle. ``said`` is the cell the setter says it lays a
    column out at, or ``None`` where it says none.
    """
    native = _typesetter().module()
    row = _COLUMN_ROW
    inked = [index for index, char in enumerate(row) if not char.isspace()]
    with laid(row) as (document, middle):
        laid_cell = float(document.caret_rect((0, len(row)))[0]) / len(row)
        cell = laid_cell if said is None else said
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
        if abs(laid_cell - advance) > abs(cell - advance):
            cell = laid_cell
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
    with laid(_TAB_ROW) as (document, _):
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


def literal_view(theme: Any) -> Any:
    """
    A view of the library's that literal text is set in, as pptmstr makes one:
    a code line runs on past the measure and the view claims its width
    (``code_overflow="grow"``), and every line is one pitch under the last
    (``fixed_line_height``).

    **The one place such a view is made**: the views a pane draws literal text
    with (``views``) and the view ``literal_columns`` measures are both made
    here, so what was measured is the configuration that is drawn. Raises what
    the library raises.
    """
    from imscribe import TypesetView

    return TypesetView(theme=theme, code_overflow="grow", fixed_line_height=True)


def literal_columns(theme: Any, style: Any) -> Columns:
    """
    ``columns`` for literal text: whether a row set as a host's own code block
    in ``style``, in a ``literal_view`` of ``theme``, keeps its columns and is
    hit where it is drawn. The same questions of the same row, through the
    view and the block literal text is drawn with, and no diff view. Never
    raises; kept as ``columns`` keeps its answers.

    Measured in pptmstr on 2026-10-13 (``scripts/measure_typeset_advances.py
    --pptmstr``): at every pixel height from 12 to 24 the row's steps are
    ImGui's baked advance, rows agree at every column, pens are on the
    chrome's, and every glyph is on its rectangle.
    """
    try:
        key = ("literal", style.font, style.leading, theme.px_per_inch, _density())
    except Exception as failure:
        return Columns(False, f"was given a style it cannot read ({type(failure).__name__})")
    held = _COLUMNS.get(key)
    if held is None:
        if not can_draw():
            return Columns(False, "is not drawing")
        try:
            held = _without_new_sizes(lambda: _measured_literal(theme, style))
        except Exception as failure:
            held = Columns(False, f"could not be measured ({type(failure).__name__}: {failure})")
        _COLUMNS[key] = held
    return held


def _measured_literal(theme: Any, style: Any) -> Columns:
    from imscribe import MarkdownCode, px_from_sp

    tall = float(px_from_sp(style.font.size))
    wide = float(len(_COLUMN_ROW) + 8) * max(tall, 1.0)

    @contextmanager
    def laid(row: str) -> Iterator[tuple[Any, float]]:
        view = literal_view(theme)
        try:
            view.set_document([MarkdownCode(style, row)])
            view.layout_for(wide)
            (line,) = view.set_lines(0, 1)
            yield view.native_document, (line.top + line.bottom) / 2.0
        finally:
            view.close()

    return _row_columns(laid, None, (-16.0, -16.0, wide, 4.0 * tall))


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
    _GATE.density = None
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
    ``hello_imgui`` scales the UI font for the display, so the size is the one
    ImGui will draw the chrome at inside the coming frame, its two style scales
    applied (``_live_font_size``). A change moves the epoch.

    **So does a change of the display's density.** A view keeps the grid it
    worked out, and a diff view's whole-pixel cell is a pixel of the raster
    ImGui had for the density it was made at; ``columns`` is asked again for
    another density (``_columns_key``), so a view held from before would be
    drawn on a cell the fresh answer is not about. ``theme.keep_baked_sizes``
    lets go of every baked size on the same change. **Nobody has run this on
    a display whose density is not 1**: ``tests/test_typeset_gate.py`` changes
    the number ImGui reports, under a context with no backend.
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
            # Read where the size is: once a frame has been, so the density is
            # the one a backend reported and not the one a context starts with.
            density = _density()
            if density != _GATE.density:
                if _GATE.density is not None:
                    roles.moved()
                _GATE.density = density
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
    The size the chrome's text is drawn at inside the next frame, or ``None``
    before the first frame.

    **Computed from the style, as ImGui computes it, and not read.** Between
    frames ``get_font_size()`` is the base size with neither of the style's
    scales applied -- measured headless on imgui 1.92.9, 2026-10-14: 16 px
    between frames and 18 px inside one at ``font_scale_dpi`` 1.1 -- and this
    runs before ``new_frame``, so a reading taken inside a frame would be one
    frame late on every change. ``font_scale_dpi`` is what hello_imgui sets
    from the display's DPI on Windows and Linux (on macOS it is 1 and the
    framebuffer's 2x is a rasteriser density, ``_density``).
    ``tests/test_typeset_gate.py`` holds this to the in-frame size over a sweep
    of bases and both scales. The base is filled in by the first ``new_frame``,
    so there is no answer before it.
    """
    im = _imgui()
    if im.get_current_context() is None or im.get_frame_count() < 1:
        return None
    style = im.get_style()
    return drawn_size(style.font_size_base, style.font_scale_main, style.font_scale_dpi)


def drawn_size(base: float, main: float, dpi: float) -> float | None:
    """
    ImGui's ``UpdateCurrentFontSize`` for a top-level window, which every pane
    is: ``base × main × dpi`` in single precision, ``IM_ROUND``-ed, clamped to
    ImGui's 1 to ``IMGUI_FONT_SIZE_MAX`` (512). ``None`` for a base under 1,
    which ImGui has not filled in yet, and for a product that is not a number:
    a hand-set NaN scale is not a reason to stop the library.

    The wheel is built without ImGui's obsolete functions (it has no
    ``io.font_global_scale`` and no ``ImFont.scale``), so the two legacy
    factors ImGui would also multiply by are not there to read.
    """
    if not base >= 1.0:
        return None
    size = _single(_single(_single(base) * _single(main)) * _single(dpi))
    if not math.isfinite(size):
        return None
    return min(max(1.0, float(math.floor(_single(size + 0.5)))), 512.0)


def pushed(px: float) -> float:
    """
    The size to hand ``imgui.push_font`` for text ImGui is to draw ``px`` pixels high.

    **The seam's sizes and ``push_font``'s are not in one unit.** A role's size
    is the pixel height text is drawn at, which is what the library's themes
    name (``_live_font_size``); ``push_font`` takes a base that ImGui then
    multiplies by both style scales (``drawn_size``), so a role's size handed
    to it as it is would be scaled twice. ``px`` divided by the two scales
    comes back to ``px`` through ImGui's rounding for every whole ``px``,
    which every role's size is. ``px`` as it is with no context, or with a
    scale that is not a positive number.
    """
    im = _imgui()
    if im.get_current_context() is None:
        return px
    style = im.get_style()
    scale = _single(style.font_scale_main) * _single(style.font_scale_dpi)
    if not (math.isfinite(scale) and scale > 0.0):
        return px
    return px / scale


def _single(value: float) -> float:
    """
    ``value`` as the nearest single-precision float, which is what ImGui holds it as.
    """
    return float(struct.unpack("<f", struct.pack("<f", value))[0])


roles.bind(can_draw)
