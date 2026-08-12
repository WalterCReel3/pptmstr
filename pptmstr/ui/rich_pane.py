"""
Rich rendering: blocks in, draw-list output.

Step 5 of planning/2026-08-10-transcript-markdown.md (the drawing half; wrapping
lives in span_layout.py). This is the only module in the markdown stack that
touches ImGui -- blocks.py, inline.py and span_layout.py are all plain data and
unit-tested; this one draws, and is exercised against a live frame by
scripts/verify_rich_render.py instead.

Deliberately out of scope, both flagged in the planning doc as unresolved and
sidestepped here rather than guessed at:

- **Clickable links.** The doc calls block hit-testing vs. link clicks
  "unresolved and must be prototyped first in step 5" -- an invisible_button
  covering a block eats the clicks a real link widget underneath it would need.
  Rather than build the rest of the renderer on top of an unvalidated
  interaction model, links render as underlined text and nothing else.
  Copy-block (``Block.lines`` is the verbatim source) is how the operator gets
  the actual URL out today.
- **Syntax highlighting.** Explicitly deferred in the doc to arrive together
  with code selection, on a widget (``TextEditor``) this renderer does not use.
- **Per-cell inline styling in a table.** Step 4 left ``TABLE.inline`` as
  ``None`` for exactly this reason (cell splitting is this step's job, not
  step 4's) -- but a cell draws as plain wrapped text here, no nested emphasis.
- **Alignment from a table's delimiter row** (``:---``, `---:`, `:---:`).
  Parsed by nothing here; every column is left-aligned.

Advance-width equality is what makes single-font-width layout correct:
Inconsolata Medium and Bold measure identically (verified in
scripts/verify_fonts.py -- 8.00px advance either way at 16px, only the ink
differs), so layout only ever measures with one face even though bold runs draw
in a different one.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from imgui_bundle import imgui

from ..theme import Face, P, face
from ..transcript import SegmentKind
from .blocks import Block, BlockKind, with_live_inline
from .inline import InlineToken, parse_inline
from .span_layout import Row, Run, layout_inline

# How many of a single fence's own lines get drawn. Independent of the outer
# block window -- one 3000-line fence inside an otherwise-small window is still
# a per-frame stall without its own cap (planning doc: "a code block also needs
# an inner per-block clamp, or the outer bound is unenforceable").
_FENCE_LINE_CAP = 200

# The outer window is bounded by cumulative *source lines*, not block count, for
# the same reason -- a handful of huge blocks can blow the budget even with few
# blocks in the window (planning doc, same sentence).
_LINE_WINDOW = 400

# A live (still-streaming) block is inline-parsed only up to this many
# characters; longer renders as plain literal text until it finalises.
# Planning doc's Consequences section: ~1.7 ms extrapolated at 4 KB is too much
# at 60 fps for something re-parsed every single frame.
_LIVE_INLINE_LIMIT = 1200

_INDENT = 18.0

# Duplicated from transcript_pane.py's private _KIND_COLOUR rather than
# imported: transcript_pane.py calls into this module (draw()), so importing
# the other way would cycle. Eight entries, SegmentKind grows rarely -- cheaper
# to duplicate than to invent a shared module for one dict.
_KIND_COLOUR = {
    SegmentKind.REASONING: "text_dim",
    SegmentKind.OUTPUT: "text",
    SegmentKind.TOOL_CALL: "accent",
    SegmentKind.TOOL_RESULT: "diff_context",
    SegmentKind.ERROR: "danger",
    SegmentKind.SYSTEM: "text_dim",
    SegmentKind.COMPACTION: "warn",
}

_PROSE_KINDS = (
    BlockKind.PARAGRAPH,
    BlockKind.HEADING,
    BlockKind.BULLET_ITEM,
    BlockKind.ORDERED_ITEM,
    BlockKind.BLOCKQUOTE,
)


@dataclass
class RichState:
    """
    Interaction latch for the rich view, the ``TranscriptState.context_run``
    counterpart for blocks. One field because the pane only ever shows one
    node's transcript at a time -- see that field's own docstring.
    """

    context_block: int | None = None


def windowed(blocks: Sequence[Block]) -> list[Block]:
    """
    A tail-anchored suffix of ``blocks`` whose *rendered* line count -- a fence's
    own contribution capped at ``_FENCE_LINE_CAP``, matching what actually gets
    drawn -- stays under ``_LINE_WINDOW``. Never a block count: a single huge
    fence must not be let through just because it is "one block".

    Always includes at least the newest block, even if it alone is over budget --
    an empty render is a worse failure than a slightly-over-budget frame.
    """
    out: list[Block] = []
    total = 0
    for block in reversed(blocks):
        if block.kind is BlockKind.FENCE:
            # Counted over the body rather than over lines: the delimiters are not
            # drawn, and a language label takes one row when there is one.
            cost = min(len(fence_body(block)), _FENCE_LINE_CAP)
            cost += 1 if dict(block.meta).get("lang") else 0
        else:
            cost = len(block.lines)
        if out and total + cost > _LINE_WINDOW:
            break
        out.append(block)
        total += cost
    out.reverse()
    return out


def block_copy_text(blocks: Sequence[Block]) -> str:
    """Verbatim source of every given block, blank-line separated -- the
    "copy visible" affordance's rich-mode counterpart."""
    return "\n\n".join("\n".join(b.lines) for b in blocks)


def _plain_text(tokens: Sequence[InlineToken]) -> str:
    """Inline tokens flattened to plain text -- table cells and the
    over-the-limit live-block fallback use this instead of full span layout."""
    parts = []
    for tok in tokens:
        if tok.type in ("text", "code_inline"):
            parts.append(tok.content)
        elif tok.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


@contextmanager
def _body_font(size: float) -> Iterator[imgui.ImFont]:
    """
    Push BODY for the scope of a measurement pass, popped unconditionally.

    Only measurement needs this: ``ImDrawList.add_text`` takes a font directly,
    so drawing never depends on what's ambient. Measurement -- both
    ``calc_word_wrap_position_python`` (a method on a specific font) and
    ``calc_text_size`` (the *ambient* font) -- needs the two to agree, which is
    what pushing BODY here guarantees regardless of what the caller left active.
    """
    body = face(Face.BODY)
    imgui.push_font(body, size)
    try:
        yield body or imgui.get_font()
    finally:
        imgui.pop_font()


def _layout(tokens: Sequence[InlineToken], width: float, size: float) -> tuple[Row, ...]:
    with _body_font(size) as font:
        avail = max(1.0, width)

        def wrap_at(text: str, w: float) -> int:
            return font.calc_word_wrap_position_python(size, text, max(w, 0.0))

        def measure_width(text: str) -> float:
            return imgui.calc_text_size(text).x

        return layout_inline(tokens, avail, wrap_at, measure_width)


def _run_color(run: Run, base: int) -> int:
    if run.code:
        return P.diff_context.u32
    if run.bold:
        return P.text_strong.u32
    if run.italic:
        return P.accent.u32
    return base


def _run_font(run: Run) -> imgui.ImFont | None:
    return face(Face.BOLD) if run.bold else face(Face.BODY)


def _line_advance() -> float:
    """
    Row-to-row advance *inside* a block.

    Not ``get_text_line_height_with_spacing``: that adds ``style.ItemSpacing.y``,
    which is the gap ImGui puts between *widgets*. Between one block and the next
    that is exactly right and already supplied -- ``_draw_one`` ends each block with
    an ``invisible_button``, and ImGui spaces after it. Between two wrapped rows of
    the same paragraph it is a widget gap inserted into a paragraph, which leaded
    every line of rendered markdown and is what made a block read looser than the
    same words drawn by ``imgui.text_wrapped`` beside it.
    """
    return imgui.get_text_line_height()


def _draw_rows(
    dl: imgui.ImDrawList, start: imgui.ImVec2, rows: Sequence[Row], size: float, base: int
) -> float:
    line_h = _line_advance()
    y = start.y
    for row in rows:
        x = start.x
        for run in row.runs:
            font = _run_font(run)
            col = _run_color(run, base)
            dl.add_text(font or imgui.get_font(), size, imgui.ImVec2(x, y), col, run.text)
            width = imgui.calc_text_size(run.text).x
            if run.link:
                # Just inside the bottom of the line box. Previously written as
                # ``line_h - 4.0`` against a height that included ItemSpacing.y,
                # which arrived at the same place only because that spacing happened
                # to be 4px; measured from the tight height it needs no such luck.
                underline_y = y + line_h - 1.0
                dl.add_line(
                    imgui.ImVec2(x, underline_y),
                    imgui.ImVec2(x + width, underline_y),
                    col,
                    1.0,
                )
            x += width
        y += line_h
    return y - start.y


def _draw_prose(
    dl: imgui.ImDrawList, start: imgui.ImVec2, width: float, block: Block, size: float
) -> float:
    indented = block.kind in (BlockKind.BULLET_ITEM, BlockKind.ORDERED_ITEM, BlockKind.BLOCKQUOTE)
    indent = _INDENT if indented else 0.0
    text_x = start.x + indent
    tokens = block.inline or ()
    rows = _layout(tokens, width - indent, size)

    base = P.text.u32
    if block.kind is BlockKind.HEADING:
        base = P.accent.u32
    elif block.kind is BlockKind.BLOCKQUOTE:
        base = P.text_dim.u32

    height = _draw_rows(dl, imgui.ImVec2(text_x, start.y), rows, size, base)

    if block.kind is BlockKind.BULLET_ITEM:
        dl.add_text(imgui.ImVec2(start.x, start.y), P.text_dim.u32, "•")
    elif block.kind is BlockKind.ORDERED_ITEM:
        n = dict(block.meta).get("start", "1")
        dl.add_text(imgui.ImVec2(start.x, start.y), P.text_dim.u32, f"{n}.")
    elif block.kind is BlockKind.BLOCKQUOTE:
        dl.add_line(
            imgui.ImVec2(start.x + 3.0, start.y),
            imgui.ImVec2(start.x + 3.0, start.y + height),
            P.border.u32,
            2.0,
        )
    return height


def fence_body(block: Block) -> tuple[str, ...]:
    """
    The code inside a fence, without its delimiters.

    ``Block.lines`` deliberately stays verbatim -- copy-as-markdown depends on
    getting back exactly what the model emitted, fences included -- so the ``` rows
    are dropped here, where the drawing happens, rather than at parse time. An
    unclosed fence has an opening delimiter and no closing one, which is why the
    tail is only trimmed when the block actually closed.
    """
    body = block.lines[1:]  # the opening delimiter is always the first line
    if block.closed and body:
        body = body[:-1]
    return body


def _draw_fence(dl: imgui.ImDrawList, start: imgui.ImVec2, block: Block) -> float:
    line_h = _line_advance()
    lines = fence_body(block)
    shown = lines[:_FENCE_LINE_CAP]
    y = start.y
    lang = dict(block.meta).get("lang")
    if lang:
        # The info string as a label, not as the ```lang row it came from. Costs the
        # same single line the delimiter used to, and says something.
        dl.add_text(imgui.ImVec2(start.x, y), P.text_dim.u32, lang)
        y += line_h
    for line in shown:
        dl.add_text(imgui.ImVec2(start.x, y), P.diff_context.u32, line or " ")
        y += line_h
    if len(lines) > len(shown):
        dl.add_text(
            imgui.ImVec2(start.x, y),
            P.text_dim.u32,
            f"... {len(lines) - len(shown)} more line(s), copy to see all",
        )
        y += line_h
    if not block.closed:
        dl.add_text(imgui.ImVec2(start.x, y), P.warn.u32, "(fence not closed)")
        y += line_h
    return y - start.y


def _draw_literal(dl: imgui.ImDrawList, start: imgui.ImVec2, block: Block) -> float:
    line_h = _line_advance()
    color = getattr(P, _KIND_COLOUR.get(block.segment_kind, "text")).u32
    y = start.y
    for line in block.lines:
        dl.add_text(imgui.ImVec2(start.x, y), color, line or " ")
        y += line_h
    return y - start.y


def _draw_thematic_break(dl: imgui.ImDrawList, start: imgui.ImVec2, width: float) -> float:
    line_h = _line_advance()
    y = start.y + line_h * 0.5
    dl.add_line(imgui.ImVec2(start.x, y), imgui.ImVec2(start.x + width, y), P.border.u32, 1.0)
    return line_h


def _split_table_row(line: str) -> list[str]:
    """
    GFM cell split on unescaped ``|``. A leading/trailing pipe (the common GFM
    style) is trimmed; ``\\|`` is an escaped literal pipe, not a separator.
    """
    trimmed = line.strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|") and not trimmed.endswith("\\|"):
        trimmed = trimmed[:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(trimmed):
        ch = trimmed[i]
        if ch == "\\" and i + 1 < len(trimmed) and trimmed[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    cells.append("".join(current).strip())
    return cells


def _draw_table(index: int, block: Block) -> None:
    if len(block.lines) < 2:
        return
    header = _split_table_row(block.lines[0])
    body = [_split_table_row(line) for line in block.lines[2:]]
    columns = max(1, len(header))
    flags = (
        imgui.TableFlags_.resizable
        | imgui.TableFlags_.borders
        | imgui.TableFlags_.sizing_stretch_prop
    )
    if not imgui.begin_table(f"##rich_table_{index}", columns, flags):
        return
    for cell in header:
        imgui.table_setup_column(cell or " ")
    imgui.table_headers_row()
    for row in body:
        imgui.table_next_row()
        for col in range(columns):
            imgui.table_next_column()
            raw = row[col] if col < len(row) else ""
            plain = _plain_text(parse_inline(raw)) if raw else ""
            imgui.text_wrapped(plain or " ")
    imgui.end_table()

    if imgui.begin_popup_context_item(f"##rich_table_ctx_{index}"):
        if imgui.menu_item_simple(f"copy table ({len(body)} row(s))"):
            imgui.set_clipboard_text("\n".join(block.lines))
        imgui.end_popup()


def _draw_one(index: int, rich: RichState, block: Block, width: float, size: float) -> None:
    imgui.push_id(index)

    if block.kind is BlockKind.TABLE:
        _draw_table(index, block)
        imgui.pop_id()
        return

    start = imgui.get_cursor_screen_pos()
    dl = imgui.get_window_draw_list()

    if block.kind is BlockKind.THEMATIC_BREAK:
        height = _draw_thematic_break(dl, start, width)
    elif block.kind is BlockKind.FENCE:
        height = _draw_fence(dl, start, block)
    elif block.kind is BlockKind.LITERAL:
        height = _draw_literal(dl, start, block)
    elif block.kind in _PROSE_KINDS:
        height = _draw_prose(dl, start, width, block, size)
    else:  # pragma: no cover -- exhaustive over BlockKind; a future kind lands here
        height = imgui.get_text_line_height_with_spacing()

    imgui.invisible_button("##hit", imgui.ImVec2(width, max(height, 1.0)))
    if imgui.is_item_hovered():
        rich.context_block = index

    if imgui.begin_popup_context_item("##rich_block_ctx"):
        label = block.kind.value.replace("_", " ")
        if imgui.menu_item_simple(f"copy {label}"):
            imgui.set_clipboard_text("\n".join(block.lines))
        imgui.end_popup()

    imgui.pop_id()


def draw(rich: RichState, blocks: Sequence[Block], live: Block | None) -> None:
    """
    Draw a node's blocks in rich mode.

    ``blocks`` is the node's full committed history; windowing happens here, not
    in the caller, so the window-size policy stays one place. ``live`` is
    whatever ``BlockCursor.live_block()`` returned this frame, or None.
    """
    width = imgui.get_content_region_avail().x
    size = imgui.get_font_size()

    window = windowed(blocks)
    dropped = len(blocks) - len(window)
    if dropped > 0:
        imgui.text_disabled(f"... {dropped} earlier block(s) not shown (history window is bounded)")

    if not imgui.is_popup_open("##rich_block_ctx"):
        rich.context_block = None

    for i, block in enumerate(window):
        _draw_one(i, rich, block, width, size)

    if live is not None:
        live = with_live_inline(live, _LIVE_INLINE_LIMIT)
        if live.inline is None and live.kind in _PROSE_KINDS:
            # Over the limit (or the parse was never attempted for this kind) --
            # render the raw source plainly rather than block on inline parsing
            # every frame. Styling appears once the block finalises.
            start = imgui.get_cursor_screen_pos()
            dl = imgui.get_window_draw_list()
            height = _draw_literal(dl, start, live)
            imgui.invisible_button("##live", imgui.ImVec2(width, max(height, 1.0)))
        else:
            _draw_one(len(window), rich, live, width, size)
