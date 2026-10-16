"""
Fenced code that names its language, coloured by token: Pygments' tokens as the
spans ``TypesetView.set_colours`` takes.

The library ships no tokeniser and takes ranges with inks from its host
(``imscribe/ui/pages/_syntax.py``); this is pptmstr's. It is the one module that
imports Pygments, and it imports neither the library nor ImGui, nor anything of
pptmstr's at run time (``SyntaxInks`` is an annotation only): it answers spans
of a string, and ``views`` hands them over.

**Colour is added and nothing else.** A span never changes a character or where
it is set, and a block that is not coloured gets exactly the spans it had
without this module: its caption's accent and its part's ink
(``block_spans``). A block is coloured only when all of these hold:

- it has a caption, the fence's info string set as its first line
  (``views._captioned``), and the caption's first word is a name Pygments
  knows a lexer by. A lexer is never guessed from the code;
- that lexer is not one of Pygments' plain-text ones (``text``, ``output``),
  whose every token is plain: such a fence keeps the code role's ink;
- it has no ink of its own. A part or a stream given a colour (dim reasoning,
  a tool's kind) is drawn in that colour, as it was;
- its code is at most ``BOUND`` characters (``too_long``).

Whether a block is coloured is decided by its language and its size, never by
which tokens it holds: every character of a coloured block's code that is in no
coloured token is drawn in one ink, ``SyntaxInks.default``, so ``x`` and
``x = 1`` set the ``x`` alike.

Offsets are into the block's text as the library holds it. The code is the
text after the caption and its line break (``said + 1``), and is lexed with
``get_tokens_unprocessed``, whose indices are into the string as given:
``get_tokens`` first strips leading and trailing newlines, adds one at the end
and, given a ``tabsize``, expands tabs, any of which would move them. A lexer
that hands over another lexer's tokens must shift their indices itself; one
that does not (Pygments' Notmuch lexer, as of 2.21) gives tokens whose text is
not the code's at their index, and those are dropped and drawn in the default
ink (``_classes``).

Lexed once per language and code (``_classes``, kept by value), so a view
inked again for a theme switch or a stream revision that left a block as it
was does not lex it again; a view is inked only when its parts or the theme
change, never per frame. A block that is not coloured is answered without
either cache, so a stream's open fence, inked again at every arrival, keeps
nothing (``views.Stream._ink`` says when a streamed fence is coloured).
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from pygments.token import Comment, Keyword, Name, Number, Operator, String

if TYPE_CHECKING:
    from .themes import SyntaxInks

Span = tuple[int, int, int]
"""
A half-open range of a block's text and the packed ink it is drawn in.
"""

_CLASSES = (
    ("keyword", Keyword),
    ("string", String),
    ("comment", Comment),
    ("number", Number),
    ("function", Name.Function),
    ("operator", Operator),
)
"""
The coloured classes, each a field of ``SyntaxInks``, and the Pygments token
tree each one covers.
"""

_KEPT = 512
"""
How many lexed blocks are kept. A transcript's code blocks, a few hundred
characters to a few thousand each, are well inside it.
"""

BOUND = 32_768
"""
The most characters of code a block is lexed for. A lex runs on the frame that
first inks the block, and its cost is in characters, not lines: one line of
268,894 characters cost as much as 2,000 lines of C++ of 176,000. Measured
2026-10-15 (Pygments 2.21.0, load average 3.3), lexing costs from 0.2 to 3.5
microseconds a character: JSON 0.18, HTML 0.9, YAML 1.4, Bash 1.5, Python 1.7,
Markdown 2.3, one long Python line 2.9, C++ 3.5. At this bound, lexed and
spanned, the median was JSON 14 ms, Python 53 (some 750 typical lines), Bash 61,
Markdown 92 and C++ or one long Python line 131, the worst single run 148: a
stall the frame a block is first inked in, once per block, and never again for
it. Any lex of more than a few hundred lines misses a 60 Hz frame, so the bound
is not a frame budget but a ceiling on one hitch; 200 KB would be some 800 ms
of C++. A block over it costs a third of a microsecond.
"""

TOO_LONG = " (too long to colour)"
"""
What a caption says after the info string when ``too_long``: the block draws
as it would with no colour at all, and the reader is told why.
"""


def language_of(caption: str) -> str:
    """
    The language a fence's info string names: its first word, or ``""``.
    ``python IMPORTANT also delete tests`` is ``python``.
    """
    words = caption.split()
    return words[0] if words else ""


def too_long(caption: str, code: str) -> bool:
    """
    Whether a fence would be coloured but for its size: its language has a
    lexer that colours and its code is more than ``BOUND`` characters. Asked
    of a fence whatever its part's ink, which is not known where its caption
    is made, so an inked part's long fence says so too.
    """
    return len(code) > BOUND and _lexer(language_of(caption)) is not None


def block_spans(
    text: str, said: int, accent: int, ink: int | None, inks: SyntaxInks | None
) -> tuple[Span, ...]:
    """
    Every span one block's text is drawn with: one ``set_colours`` call's
    worth, since a second call would replace the first.

    ``said`` is the caption's length (0 for a block with none), ``accent`` its
    ink, ``ink`` the block's own ink or ``None``, and ``inks`` the token inks,
    or ``None`` where no token is to be coloured. A block that is not coloured
    (the module docstring) gets what it got before tokens were coloured at all,
    and is answered without a cache: those spans cost a tuple of two, and a
    stream's open fence would otherwise keep a copy of itself per arrival.
    """
    start = said + 1
    if (
        not said
        or ink is not None
        or inks is None
        or len(text) <= start
        or len(text) - start > BOUND
    ):
        return tuple(_plain(len(text), said, accent, ink))
    return _coloured(text, said, accent, inks)


@functools.lru_cache(maxsize=_KEPT)
def _coloured(text: str, said: int, accent: int, inks: SyntaxInks) -> tuple[Span, ...]:
    """
    ``block_spans`` of a block it colours if its language lexes. Kept, as
    ``_classes`` is: a stream inks every committed fence again at each
    revision, and building a 500-line block's spans again is some 130
    microseconds.
    """
    start = said + 1
    lexed = _classes(language_of(text[:said]), text, start)
    if lexed is None:
        return tuple(_plain(len(text), said, accent, None))
    spans: list[Span] = [(0, said, accent)]
    at = start
    for begin, end, name in lexed:
        begin += start
        end += start
        if begin > at:
            spans.append((at, begin, inks.default))
        spans.append((begin, end, getattr(inks, name)))
        at = end
    if at < len(text):
        spans.append((at, len(text), inks.default))
    return tuple(spans)


def _plain(length: int, said: int, accent: int, ink: int | None) -> list[Span]:
    """
    A block's spans with no token coloured: the caption's accent, and the
    block's own ink over the rest of it.
    """
    spans: list[Span] = [(0, said, accent)] if said else []
    if ink is not None and length > said:
        spans.append((said, length, ink))
    return spans


@functools.lru_cache(maxsize=_KEPT)
def _classes(language: str, text: str, start: int) -> tuple[tuple[int, int, str], ...] | None:
    """
    The coloured runs of ``text[start:]``, the code, in ``language``:
    half-open ranges into the code and the ``SyntaxInks`` field each is drawn
    in, neighbours of one class joined. ``None`` where the code is not
    coloured at all: no lexer by that name, a plain-text one, or a lexer that
    raised. Empty where it lexed and no token is of a coloured class.

    Kept by value and not by block: a theme carries no part of the answer.
    Keyed by the block's whole text and where its code starts, and not by the
    code cut from it: the two caches then hold the one string ``_coloured``
    was called with, where a key of the code would be a second copy of it.
    """
    lexer = _lexer(language)
    code = text[start:]
    if lexer is None or not code:
        return None
    try:
        tokens = list(_lex(lexer, code))
    except Exception:
        # A lexer's bug, met on whatever an agent wrote, costs the colour and
        # not the frame it would be raised into.
        return None
    runs: list[tuple[int, int, str]] = []
    at = 0
    for index, kind, value in tokens:
        if not value or index < at or not code.startswith(value, index):
            # A token whose text is not the code's at its index, or that goes
            # back over one already set, is a lexer's misplaced offset: its
            # colour would land on other characters.
            continue
        end = at = index + len(value)
        name = _class_of(kind)
        if name is None:
            continue
        if runs and runs[-1][1] == index and runs[-1][2] == name:
            runs[-1] = (runs[-1][0], end, name)
        else:
            runs.append((index, end, name))
    return tuple(runs)


def _lex(lexer: Any, code: str) -> Iterable[tuple[int, Any, str]]:
    """
    Pygments' tokens of ``code``, each with its index into ``code``.
    """
    return lexer.get_tokens_unprocessed(code)  # type: ignore[no-any-return]


@functools.cache
def _lexer(language: str) -> Any:
    """
    The lexer Pygments has by the name ``language``, or ``None``, which it is
    for a plain-text lexer too. Kept, because finding one by name imports its
    module and reads every lexer's aliases.
    """
    if not language:
        return None
    from pygments.lexers import get_lexer_by_name
    from pygments.lexers.special import OutputLexer, TextLexer
    from pygments.util import ClassNotFound

    try:
        lexer = get_lexer_by_name(language)
    except ClassNotFound:
        return None
    except Exception:
        # A broken plugin's entry point is loaded for any name Pygments does
        # not know itself, and raises whatever it raises.
        return None
    return None if isinstance(lexer, (TextLexer, OutputLexer)) else lexer


def _class_of(kind: Any) -> str | None:
    """
    Which ``SyntaxInks`` field a token type is drawn in, or ``None`` for the
    code's default ink. A subtype is its parent's: ``Keyword.Constant`` is a
    keyword and ``String.Doc`` a string.
    """
    for name, tree in _CLASSES:
        if kind in tree:
            return name
    return None
