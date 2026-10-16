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
change, never per frame. A block that is not coloured, its language unknown
included, is answered without either cache, so a stream's open fence, inked
again at every arrival, keeps nothing (``views.Stream._ink`` says when a
streamed fence is coloured).

What a lex costs is bounded per block here (``BOUND``) and per frame by the
caller: ``needs_lex`` says, without lexing, whether ``block_spans`` would lex,
and ``views`` lexes no more on a frame than its budget allows
(``views._Lexing``).
"""

from __future__ import annotations

import functools
import sys
from array import array
from collections import OrderedDict
from collections.abc import Hashable, Iterable, Iterator
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from pygments.token import Comment, Generic, Keyword, Name, Number, Operator, String

if TYPE_CHECKING:
    from .themes import SyntaxInks

Span = tuple[int, int, int]
"""
A half-open range of a block's text and the packed ink it is drawn in.
"""

_Runs: TypeAlias = "array[int] | None"
"""
What ``_classes`` answers: the coloured runs of a block's code, three numbers a
run (where it begins, where it ends, and its class's index in ``_CLASSES``), or
``None``. Packed and not a tuple a run: a tuple of three is some 120 bytes and
the three numbers here are 12, so ``_LEXED`` holds some seven times the
blocks in the same memory, and a block it holds is never lexed again.
"""

_CLASSES = (
    ("keyword", Keyword),
    ("string", String),
    ("comment", Comment),
    ("number", Number),
    ("function", Name.Function),
    ("operator", Operator),
    ("add", Generic.Inserted),
    ("remove", Generic.Deleted),
    ("heading", Generic.Heading),
    ("heading", Generic.Subheading),
)
"""
The coloured classes, each a field of ``SyntaxInks``, and the Pygments token
tree each one covers. A field may stand for more than one tree: ``heading`` is
both of a diff's header kinds, ``diff --git`` and ``index`` lines
(``Generic.Heading``) and ``@@`` lines (``Generic.Subheading``), which are
sibling trees. Pygments' diff lexer gives ``---`` and ``+++`` lines as
``Generic.Deleted`` and ``Generic.Inserted``, so they take the remove and add
inks. Other lexers emit ``Generic`` tokens too (a markdown heading is
``Generic.Heading``), and take the same inks there.

An entry's place is the class index ``_LEXED`` keeps, so one is only ever
added at the end.
"""

_KEPT = 24 * 2**20
"""
How many bytes each of the two caches keeps (``_Kept``), the least recently
used going first: both together are at most 48 MiB. An entry weighs what it
holds (``_weight``): its text, its key and entry, and its answer, which goes
with the block's tokens and not its characters. Measured 2026-10-16 with
tracemalloc, the caches filled to 4 Mi characters of 3,000-character fences
when each answer was a tuple a token held 58 MB for Python, 92 for JSON and
128 for shell, some 110 bytes a token. Filled past this limit with the same
fences, they hold 42 MiB for each of the three, against 48 weighed;
``tests/test_typeset_syntax.py`` holds the weights to what is measured.

The working set is what one re-ink walks: on a theme switch, every coloured
fence a pane holds. The log holds some 1,750 lines
(``transcript_pane._TYPESET_KEEP`` and a generation) and a fence needs three to
be coloured, so at most some 580 fences; DETAIL some 670 more
(``review.SETTLED_LINES``). A theme switch misses ``_SPANS``, whose answers
carry the theme's inks, and builds them from ``_LEXED``, which lexes nothing it
holds: a 3,000-character fence of shell is some 7 KB there, so some 3,600 of
them fit (5,000 of Python), where it is some 75 KB in ``_SPANS``, which holds
some 330. A working set past ``_LEXED``, a pane of fences each near ``BOUND``,
is lexed again on the switch, within the frame budget, and the switch
completes.
"""

_ENTRY_BYTES = 320
"""
What an entry costs besides its text and its answer: its key, the cache's link
to it, and the pair of weight and answer. Measured with tracemalloc for a fence
of one line, where it is most of what is held.
"""

_SPAN_BYTES = 120
"""
What one span costs in ``_SPANS`` besides its slot in the answer: a tuple of
three, 64 bytes, and its two offsets, 28 bytes each. The ink is a number every
span of that class shares.
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
    only of a fence whose part has no ink of its own (``views._captioned``):
    one that is never coloured is not told it is too long.
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
    if inks is None or not _colours(text, said, ink, inks):
        return tuple(_plain(len(text), said, accent, ink))
    key = (text, said, accent, inks)
    spans = cast("tuple[Span, ...] | None", _SPANS.get(key))
    if spans is None:
        spans = _coloured(text, said, accent, inks)
        _SPANS.put(key, _weight(text, sys.getsizeof(spans) + _SPAN_BYTES * len(spans)), spans)
    return spans


def needs_lex(
    text: str, said: int, ink: int | None, inks: SyntaxInks | None, *, accent: int | None = None
) -> bool:
    """
    Whether ``block_spans`` of this block would lex it: it is coloured and its
    code's runs are not kept, nor, given the caption's ``accent`` it would be
    called with, its spans. Answered without lexing, so a caller can leave a
    block plain for a frame and colour it on a later one.

    **A caller that draws passes ``accent``**: a block inked again and again
    is answered from ``_SPANS`` and never touches ``_LEXED``, which may have
    let it go, and without the accent this answers that it would lex a block
    ``block_spans`` answers from ``_SPANS``.
    """
    if not _colours(text, said, ink, inks):
        return False
    if accent is not None and (text, said, accent, inks) in _SPANS:
        return False
    return (language_of(text[:said]), text, said + 1) not in _LEXED


def _weight(text: str, answer: int) -> int:
    """
    What an entry for ``text`` holds, in bytes, its answer being ``answer``
    bytes: the text counted in each cache that holds it, though the two share
    it, so that the weights are never less than what is held.
    """
    return sys.getsizeof(text) + _ENTRY_BYTES + answer


def _colours(text: str, said: int, ink: int | None, inks: SyntaxInks | None) -> bool:
    """
    Whether a block is coloured by token: every gate of the module docstring,
    its language's lexer the last of them.
    """
    start = said + 1
    return bool(
        said
        and ink is None
        and inks is not None
        and len(text) > start
        and len(text) - start <= BOUND
        and _lexer(language_of(text[:said])) is not None
    )


class _Kept:
    """
    The least recently used entries dropped first once their weights, the
    bytes each one holds (``_weight``), sum past ``_KEPT``. A block over the
    whole of it is answered and not kept.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.held = 0
        self._entries: OrderedDict[Hashable, tuple[int, Any]] = OrderedDict()

    def __contains__(self, key: Hashable) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: Hashable) -> Any:
        found = self._entries.get(key)
        if found is None:
            return None
        self._entries.move_to_end(key)
        return found[1]

    def put(self, key: Hashable, weight: int, value: Any) -> None:
        if key in self._entries:
            self.held -= self._entries.pop(key)[0]
        if weight <= self.limit:
            self._entries[key] = (weight, value)
            self.held += weight
            while self.held > self.limit:
                self.held -= self._entries.popitem(last=False)[1][0]

    def clear(self) -> None:
        self._entries.clear()
        self.held = 0


_SPANS = _Kept(_KEPT)
"""
``block_spans`` of every coloured block, by its text, caption, accent and
token inks: a stream inks every committed fence again at each revision, and
building a 500-line block's spans again is some 130 microseconds.
"""

_LEXED = _Kept(_KEPT)
"""
``_classes`` of every block lexed, by language, text and where its code
starts: a theme carries no part of the answer, so a switch lexes no block
still kept.
"""


def _coloured(text: str, said: int, accent: int, inks: SyntaxInks) -> tuple[Span, ...]:
    """
    ``block_spans`` of a block it colours.
    """
    start = said + 1
    lexed = _classes(language_of(text[:said]), text, start)
    if lexed is None:
        return tuple(_plain(len(text), said, accent, None))
    spans: list[Span] = [(0, said, accent)]
    by_class = [getattr(inks, name) for name, _ in _CLASSES]
    at = start
    for n in range(0, len(lexed), 3):
        begin, end = lexed[n] + start, lexed[n + 1] + start
        if begin > at:
            spans.append((at, begin, inks.default))
        spans.append((begin, end, by_class[lexed[n + 2]]))
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


def _classes(language: str, text: str, start: int) -> _Runs:
    """
    ``_lex_classes``, kept (``_LEXED``).
    """
    key = (language, text, start)
    if key in _LEXED:
        return cast("_Runs", _LEXED.get(key))
    runs = _lex_classes(language, text, start)
    _LEXED.put(key, _weight(text, 0 if runs is None else sys.getsizeof(runs)), runs)
    return runs


def _lex_classes(language: str, text: str, start: int) -> _Runs:
    """
    The coloured runs of ``text[start:]``, the code, in ``language``:
    half-open ranges into the code and the ``SyntaxInks`` field each is drawn
    in (``_Runs``), neighbours of one class joined. ``None`` where the code is
    not coloured at all: no lexer by that name, a plain-text one, or a lexer
    that raised. Empty where it lexed and no token is of a coloured class.

    Kept (``_classes``) by the block's whole text and where its code starts,
    and not by the code cut from it: the two caches then hold the one string
    ``block_spans`` was called with, where a key of the code would be a second
    copy of it.
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
    runs: list[int] = []
    at = 0
    for index, kind, value in tokens:
        if not value or index < at or not code.startswith(value, index):
            # A token whose text is not the code's at its index, or that goes
            # back over one already set, is a lexer's misplaced offset: its
            # colour would land on other characters.
            continue
        end = at = index + len(value)
        found = _class_of(kind)
        if found is None:
            continue
        if runs and runs[-2] == index and runs[-1] == found:
            runs[-2] = end
        else:
            runs += (index, end, found)
    return array("I", runs)


def _lex(lexer: Any, code: str) -> Iterable[tuple[int, Any, str]]:
    """
    Pygments' tokens of ``code``, each with its index into ``code``.

    The library holds a fence's code without its last line end, and a lexer
    whose rules end on one (the diff lexer's rule for an added line) does not read a last
    line as a line without it. So code that does not end on a line end is
    lexed with one, as Pygments' own ``get_tokens`` lexes it, and the tokens
    are cut back to ``code``: none is past its end, and the one that held the
    added line end is shorter by it.
    """
    if code.endswith("\n"):
        return lexer.get_tokens_unprocessed(code)  # type: ignore[no-any-return]
    return _within(lexer.get_tokens_unprocessed(code + "\n"), len(code))


def _within(tokens: Iterable[tuple[int, Any, str]], length: int) -> Iterator[tuple[int, Any, str]]:
    """
    ``tokens`` cut at ``length``: a token that reaches past it is cut there and
    one that starts at or past it is dropped.
    """
    for index, kind, value in tokens:
        if index >= length:
            continue
        yield index, kind, value[: length - index]


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


def _class_of(kind: Any) -> int | None:
    """
    Which ``SyntaxInks`` field a token type is drawn in, as its index in
    ``_CLASSES``, or ``None`` for the code's default ink. A subtype is its
    parent's: ``Keyword.Constant`` is a keyword and ``String.Doc`` a string.
    """
    for found, (_, tree) in enumerate(_CLASSES):
        if kind in tree:
            return found
    return None
