"""
A role's face, size and leading as the library's ``Theme``, and pptmstr's inks as its ``Palette``.

``roles`` says what the operator chose; this module says it to the library. One
theme sets a markdown document: each block kind in its role's face, pixel
height and line pitch, on a heading scale and with the block spacing of a pane
and not of a page. ``demoted(n)`` is that theme with every heading set as the
level ``n`` below it. The palette is pptmstr's theme, role for role, and follows
a theme switch with nobody telling it.

**Which text the library sets at all is decided here** (``native``). Text of
a role that is monospace by nature -- its columns are content -- and that
stands on its own, a literal part or ``plain()``, is drawn by ImGui in the
chrome's font unless its caller asks for the library and the library is
measured to keep its columns there (``views.literal_unfit``,
``gate.literal_columns``); the library sets markdown. A fenced block inside a
markdown part goes with its document. By pptmstr's measurement on 2026-10-13
(``scripts/measure_typeset_advances.py --pptmstr``, a window under Xvfb,
imscribe ``0.2.0b1`` on the imtypeset ``0.4.1.192900`` wheel, load average 1.6,
vertices read; ``HANDOFF-advtext.md``, A2 and request P21), with pptmstr's
fonts and role themes at every body size the settings offer and every step,
so at every pixel height from 12 to 24: a fence in a Roboto document, a fence
and prose in an all-monospace one, and a host's own code block in the
literal role's style are drawn in steps of ImGui's baked advance and no
other, rows agree at every column, characters 1 to 74 are on the pens of the
same text through ``ImDrawList.add_text`` in the chrome's font, every glyph
is on its rectangle, and a point a quarter and three quarters into a glyph is
answered as the character drawn there (but at a line's last character, whose
three-quarter point answers the start of the next line). **What moved on the
strength of it**: the log's tool calls, results and errors, and DETAIL's
error (``literal_style``). An approval's arguments did not.

**What the library does not take from a role**, each a request in
``HANDOFF-advtext.md``: an inline code span has no size of its own (P11), and a
line holding a taller glyph is taller than its neighbours, so a pitch here is
what the library is asked for and not a promise about every line (P13).

Themes and palettes are kept until ``roles`` says a built document would be
wrong (its epoch) or pptmstr's theme changes. The library is imported inside
the functions that use it, so this module imports where there is none.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ... import theme as ui_theme
from ...theme import Color
from . import roles
from .roles import Role, Slot

HEADINGS: tuple[tuple[int, float, float, float], ...] = (
    (1, 22 / 16, 0.70, 0.30),
    (2, 19 / 16, 0.70, 0.30),
    (3, 17 / 16, 0.60, 0.25),
    (4, 1.00, 0.50, 0.20),
    (5, 1.00, 0.50, 0.20),
    (6, 1.00, 0.50, 0.20),
)
"""
Each heading level: its size as a multiple of the heading role's pixel height,
and the air above and below it in ems of its own size.

A pane's scale and not a page's: the library's own runs 1.25 a level, to 31 px
over a 16 px body, which is a title page in a pane 400 px wide. The sizes are
whole pixel heights at a 16 px body -- 22, 19, 17 -- because ImGui bakes at a
whole pixel. Below the third level the size is the body's and the levels are
told apart by face.
"""

_HEADING_FACES = {5: "PROSE_BOLD_ITALIC", 6: "PROSE_ITALIC"}
"""
The proportional face of a heading level that is not plain bold, by the name of
the library's ``Face`` member.
"""

_PARAGRAPH_GAP_EM = 0.45
_CODE_GAP_EM = 0.40

_FILL_ALPHA = 0.10
_EMPHASIS_ALPHA = 0.20
_HEADER_FILL_ALPHA = 0.10
_SELECTION_ALPHA = 0.35


@dataclass
class _Kept:
    """
    The themes and palettes built so far, and what they were built under.
    """

    epoch: int = -1
    themes: dict[int, Any] = field(default_factory=dict)
    diff: Any = None
    literal: dict[Role, Any] = field(default_factory=dict)
    palette_of: object = None
    palettes: dict[int | None, Any] = field(default_factory=dict)


_KEPT = _Kept()


def native(role: Role) -> bool:
    """
    Whether text of a role that stands on its own is drawn by ImGui unless a
    caller asks for the library: every role that is monospace by nature
    (``roles.SLOTS``).

    The module docstring has the measurement this rests on. A role the operator
    merely *chose* monospace for -- prose, a heading, a table -- is not one: its
    columns are not content.
    """
    return roles.SLOTS[role] == (Slot.MONOSPACE,)


def line_height(role: Role) -> float:
    """
    The pitch of one line of a role's text as it is drawn, in pixels.

    ImGui's line for a role it draws (``native``): the font's pixel height,
    which is the line of such a role's text where the library sets it too but
    at three heights (``literal_line``). The role's own pitch (``roles.pitch``)
    for one the library sets as markdown.
    """
    return roles.px(role) if native(role) else roles.pitch(role)


def _bold_monospace() -> bool:
    """
    Whether the library was given a bold monospace file of its own. Without one it
    backs the face with a proportional bold, and the columns go with it.
    """
    from imscribe import Face, registered_fonts

    return Face.CODE_BOLD in registered_fonts()


def _faces(slot: Slot) -> Any:
    """
    The face table of a theme whose running text is in ``slot``. Without a bold
    monospace the weight is given up and the columns kept.
    """
    from imscribe import Face, Faces

    bold = Face.CODE_BOLD if _bold_monospace() else Face.CODE
    return Faces.mono(bold=bold) if slot is Slot.MONOSPACE else Faces(code_bold=bold)


def _sized(style: Any, face: Any, tall: float, pitch: float) -> Any:
    """
    A block style in ``face``, ``tall`` pixels high as ImGui means a font size,
    its baselines ``pitch`` pixels apart.
    """
    from imscribe import em_size_for_pixel_height, sp_from_px

    size = sp_from_px(em_size_for_pixel_height(face, tall))
    return replace(style, font=replace(style.font, face=face, size=size), leading=sp_from_px(pitch))


def document_theme() -> Any:
    """
    The theme of a markdown document, for the roles as they are now.

    The library builds a theme from one body size and one face table, and reads
    a block's family off the block's own face. So each block kind is given the
    face of its role's slot, which is what lets a monospace heading stand over
    proportional prose in one document.
    """
    return demoted(0)


def diff_theme() -> Any:
    """
    The theme a diff view is given: the document's, with code set at the diff
    role's pixel height and pitch. The view reads its row from the code style.
    """
    base = demoted(0)
    hit = _KEPT.diff
    if hit is None:
        from imscribe import BlockKind, Face, px_from_sp

        pitch = roles.pitch(Role.DIFF)
        code = _sized(base.blocks[BlockKind.CODE], Face.CODE, roles.px(Role.DIFF), pitch)
        # The view's row is its leading rounded UP to a pixel, and a pitch
        # converted to the library's unit may land a fraction over the pixel
        # it came from: the leading is brought back under it.
        leading = code.leading
        while px_from_sp(leading) > pitch:
            leading -= 1
        blocks = dict(base.blocks)
        blocks[BlockKind.CODE] = replace(code, leading=leading)
        hit = _KEPT.diff = replace(base, blocks=blocks)
    return hit


def literal_line(role: Role) -> float:
    """
    How far apart the lines of a role's literal text are where the library sets
    it (``literal_style``): the role's pixel height, ImGui's own line for the
    same text, **and one pixel more where that height is two more than a
    multiple of four** (14, 18 and 22 px).

    Under ``fixed_line_height`` the library puts a line's baseline three
    quarters of the line down and rounds it to draw, and a line of 4k + 2
    pixels puts every baseline on a half pixel: which way each row is rounded
    then depends on where the block is on the screen, through the float the
    position is held in. Measured in pptmstr on 2026-10-13, eight rows drawn
    at 60 heights down a window from y 47 to 460, at every pixel height from
    12 to 24 and lines of that height and one and two more: 49 of the 60
    placements put a row a pixel off its line at every line of 4k + 2 px, and
    none at any other.
    """
    tall = roles.px(role)
    return tall + 1.0 if tall % 4 == 2 else tall


def literal_style(role: Role) -> Any:
    """
    The block style literal text of ``role`` is set in when the library sets it
    (``views``): the code face at the role's pixel height, nothing above or
    below it and no indent, and one line every ``literal_line(role)``, which is
    ImGui's own line for the same text at every height but three. So the block
    stands where ImGui's lines of it would.

    The leading is the line in the library's unit, which is within 1e-5 px of
    it: half a pixel after some 50,000 lines.
    """
    demoted(0)
    hit = _KEPT.literal.get(role)
    if hit is None:
        from imscribe import BlockKind, Face

        line = literal_line(role)
        code = _sized(demoted(0).blocks[BlockKind.CODE], Face.CODE, roles.px(role), line)
        hit = _KEPT.literal[role] = replace(code, space_above=0, space_below=0, left_indent=0)
    return hit


def demoted(levels: int) -> Any:
    """
    The document theme with each heading set as the one ``levels`` below it; past
    the sixth, as the sixth.
    """
    if _KEPT.epoch != roles._STATE.epoch:
        _KEPT.epoch = roles._STATE.epoch
        _KEPT.themes.clear()
        _KEPT.diff = None
        _KEPT.literal.clear()
    levels = min(max(int(levels), 0), 6)
    hit = _KEPT.themes.get(levels)
    if hit is None:
        base = _KEPT.themes.get(0)
        if base is None:
            base = _KEPT.themes[0] = _build()
        hit = base
        if levels:
            from imscribe import BlockKind

            blocks = dict(base.blocks)
            for level in range(1, 7):
                lower = BlockKind[f"HEADING_{min(6, level + levels)}"]
                blocks[BlockKind[f"HEADING_{level}"]] = base.blocks[lower]
            hit = _KEPT.themes[levels] = replace(base, blocks=blocks)
    return hit


def _build() -> Any:
    from imscribe import Align, BlockKind, Face, Spacing, Theme

    prose = roles.style(Role.PROSE)
    base = Theme.at_pixel_height(
        roles.px(Role.PROSE), faces=_faces(prose.slot), align=Align.LEFT, spacing=Spacing.FRENCH
    )
    blocks = dict(base.blocks)
    em = blocks[BlockKind.PARAGRAPH].font.size
    for kind in (
        BlockKind.PARAGRAPH,
        BlockKind.BULLET_ITEM,
        BlockKind.ORDERED_ITEM,
        BlockKind.QUOTE,
        BlockKind.RULE,
    ):
        blocks[kind] = _sized(
            blocks[kind], blocks[kind].font.face, roles.px(Role.PROSE), roles.pitch(Role.PROSE)
        )
    blocks[BlockKind.PARAGRAPH] = replace(
        blocks[BlockKind.PARAGRAPH], space_below=round(em * _PARAGRAPH_GAP_EM)
    )

    heading = roles.style(Role.HEADING)
    for level, scale, above, below in HEADINGS:
        kind = BlockKind[f"HEADING_{level}"]
        tall = max(1.0, roles._half_up(roles.px(Role.HEADING) * scale))
        if heading.slot is Slot.PROPORTIONAL:
            face = Face[_HEADING_FACES.get(level, "PROSE_BOLD")]
        else:
            face = Face.CODE_BOLD if level < 6 and _bold_monospace() else Face.CODE
        sized = _sized(blocks[kind], face, tall, roles.pitch(Role.HEADING, tall))
        size = sized.font.size
        blocks[kind] = replace(
            sized, space_above=round(size * above), space_below=round(size * below)
        )

    # Fenced code at its own role's pixel height: sized in ems of proportional
    # prose it would come out shorter than the chrome beside it.
    blocks[BlockKind.CODE] = replace(
        _sized(
            blocks[BlockKind.CODE],
            Face.CODE,
            roles.px(Role.CODE_BLOCK),
            roles.pitch(Role.CODE_BLOCK),
        ),
        space_above=round(em * _CODE_GAP_EM),
        space_below=round(em * _CODE_GAP_EM),
    )

    cell = Face.CODE if roles.style(Role.TABLE).slot is Slot.MONOSPACE else Face.PROSE
    blocks[BlockKind.TABLE] = replace(
        _sized(blocks[BlockKind.TABLE], cell, roles.px(Role.TABLE), roles.pitch(Role.TABLE)),
        space_above=round(em * _CODE_GAP_EM),
        space_below=round(em * _PARAGRAPH_GAP_EM),
    )
    # The face table is the proportional one whatever prose is set in: a block's
    # family is read off its own face, and a code span in a proportional block
    # is then the monospace.
    return replace(base, blocks=blocks, faces=_faces(Slot.PROPORTIONAL))


def _alpha(colour: Color, alpha: float) -> int:
    """
    The colour's packed form with its alpha replaced.
    """
    return (colour.u32 & 0x00FFFFFF) | (max(0, min(255, round(alpha * 255))) << 24)


def build_palette(p: ui_theme.Palette) -> Any:
    """
    The library's palette for one pptmstr palette, role for role.

    The role meanings are the ones pptmstr's own renderers use: a heading is
    ``accent``, a quote ``text_dim``, code ``diff_context`` (``rich_pane``), a rule
    ``border``; in a diff, ``diff_add`` / ``diff_remove`` / ``diff_context`` and
    ``accent`` for the file and hunk headers. The bands behind added and removed
    lines are the same two colours, translucent, so they tint the pane's own
    ground whichever theme is live. The ``+`` and ``-`` in the gutter are one ink
    for both and are what tells the two apart without hue, so that ink is the
    theme's ``text`` and not a dim one.
    """
    from imscribe import BlockKind, DiffPalette, Palette

    roles_ink = {BlockKind[f"HEADING_{level}"]: p.accent.u32 for level, _, _, _ in HEADINGS}
    roles_ink[BlockKind.QUOTE] = p.text_dim.u32
    roles_ink[BlockKind.CODE] = p.diff_context.u32
    base = Palette(
        page=p.panel.u32,
        ink=p.text.u32,
        rule=p.border.u32,
        highlight=_alpha(p.focus, _SELECTION_ALPHA),
        link=p.accent.u32,
        diff=DiffPalette(
            add=p.diff_add.u32,
            remove=p.diff_remove.u32,
            context=p.diff_context.u32,
            meta=p.text.u32,
            add_fill=_alpha(p.diff_add, _FILL_ALPHA),
            remove_fill=_alpha(p.diff_remove, _FILL_ALPHA),
            add_emphasis=_alpha(p.diff_add, _EMPHASIS_ALPHA),
            remove_emphasis=_alpha(p.diff_remove, _EMPHASIS_ALPHA),
            gutter=p.text.u32,
            header=p.accent.u32,
            header_fill=_alpha(p.accent, _HEADER_FILL_ALPHA),
            warning=p.warn.u32,
            current=p.accent.u32,
        ),
    )
    return base.with_roles(roles_ink)


def palette(ink: Color | None = None) -> Any:
    """
    The palette for the theme that is live right now.

    With ``ink``, every block is drawn in that one colour: a segment kind's, or
    dim text for something de-emphasised. Links, rules and the diff colours stay.
    """
    current = ui_theme.current()
    if _KEPT.palette_of is not current:
        _KEPT.palette_of = current
        _KEPT.palettes.clear()
    key = None if ink is None else ink.u32
    hit = _KEPT.palettes.get(key)
    if hit is None:
        hit = _KEPT.palettes.get(None)
        if hit is None:
            hit = _KEPT.palettes[None] = build_palette(current)
        if ink is not None:
            hit = _KEPT.palettes[key] = replace(hit, ink=ink.u32, roles=())
    return hit
