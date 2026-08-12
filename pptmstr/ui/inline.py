"""
Inline parsing: markdown-it-py over a block's content.

Step 4 of planning/2026-08-10-transcript-markdown.md. Parsed once, at block
finalisation (``pptmstr/ui/blocks.py``), and cached on the frozen ``Block`` --
so a long transcript pays for inline parsing exactly once per block, never once
per frame. Only the inline chain is used (``MarkdownIt.parseInline``); block
structure is ``blocks.py``'s own segmenter, not this library's block parser.

A hand-rolled inline scanner was considered and rejected (planning doc,
"Alternatives considered and rejected"): CommonMark's delimiter-run flanking
rules are what make ``foo_bar_baz``, ``2 * 3 * 4`` and
``path/to/file_name.py::test_thing`` come out right, and implementing those
correctly *is* implementing the reference algorithm.

``markdown_it.token.Token`` has structural equality but is not hashable, and
``Block`` is a frozen (therefore hashable-by-default) dataclass -- storing a raw
``Token`` inside it would make ``hash(block)`` an unpredictable landmine, working
right up until something hashes or set-dedupes a ``Block``. ``InlineToken`` below
is a deliberately thin flattening of the handful of ``Token`` fields the renderer
will need (kind, text content, an href for links), immutable and hashable by
construction. It also keeps step 5 from coupling to markdown-it-py's object model
-- the same reasoning as "hand-rolled rendering, not imgui_md": this codebase
owns its own render-facing types.

Inline tokens from ``parseInline`` are already a flat sequence -- emphasis and
strong are open/close markers around a run of text tokens, not a nested tree --
so flattening is a 1:1 map, no recursion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.rules_inline.emphasis import tokenize as _emphasis_tokenize
from markdown_it.rules_inline.state_inline import StateInline


@dataclass(frozen=True, slots=True)
class InlineToken:
    type: str
    content: str = ""
    # Only link_open carries one today (href). A tuple, not a dict, so InlineToken
    # -- and therefore Block -- stays hashable.
    attrs: tuple[tuple[str, str], ...] = ()


def _asterisk_only_emphasis(state: StateInline, silent: bool) -> bool:
    """
    Suppress underscore emphasis entirely (planning doc, "Underscore emphasis is
    disabled").

    CommonMark's intraword rule already protects ``foo_bar_baz`` -- both
    underscores sit between word characters, so neither can open or close a
    delimiter run. It does not protect ``__init__`` or ``__all__``: each
    underscore run sits at a word *boundary* (start/end of token, next to
    nothing), which is exactly what a delimiter run is allowed to flank.
    Spec-correct, and wrong for a transcript that is mostly Python -- verified
    against the unpatched parser, which does bold both. Overriding the
    tokenize half of the "emphasis" rule (public API: ``ruler.at``) suppresses
    ``_`` before it ever becomes a delimiter; ``*`` is untouched. Cost:
    ``_italic_`` no longer italicises -- deliberate, not an oversight.
    """
    if state.src[state.pos] == "_":
        return False
    return _emphasis_tokenize(state, silent)


def _build_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark")
    md.inline.ruler.at("emphasis", _asterisk_only_emphasis)
    return md


_PARSER = _build_parser()


def _flatten(tok: Any) -> InlineToken:
    attrs = tuple(sorted((k, str(v)) for k, v in tok.attrs.items())) if tok.attrs else ()
    return InlineToken(tok.type, tok.content, attrs)


def parse_inline(text: str) -> tuple[InlineToken, ...]:
    """Parse one block's content into a flat, cache-forever token stream."""
    if not text:
        return ()
    root = _PARSER.parseInline(text, {})
    children = root[0].children or ()
    return tuple(_flatten(tok) for tok in children)
