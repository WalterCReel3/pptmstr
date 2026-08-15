"""
The splash art, and a measured brightness ranking of the glyphs it is made of.

The NEEDS YOU pane shows this when there are no sessions at all. Its cells cycle among
characters of equal ink, so the picture holds its shape while the texture moves
-- which only works if "equal ink" is something that was measured rather than
eyeballed. ``scripts/rank_glyphs.py`` does the measuring; this module carries
the result and the art itself, and imports nothing that is not already a runtime
dependency.

Three names are the contract, and the renderer in ``splash.py`` is written
against them:

``ART``
    The rows, in order, exactly as authored. Rows are ragged -- trailing spaces
    were never in the file and are not added here -- so a renderer must not
    assume a rectangle.
``RANKS``
    Buckets of interchangeable glyphs, ordered dim to bright.
``RANK_OF``
    Which bucket a character is in.

**U+0020 is in none of them, deliberately.** Space is the background, not the
dimmest glyph: substituting for it would fill the negative space in and dissolve
the silhouette the animation exists to preserve. A renderer that looks up a cell
in ``RANK_OF`` and finds nothing is looking at background and must leave it
alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

# Beside the module rather than in ``pptmstr/assets``: the wheel ships
# ``packages = ["pptmstr"]``, which carries every non-Python file under the
# package, so either location ships -- but the art is the data half of this
# module and nothing else reads it.
_ART_PATH = Path(__file__).resolve().parent / "splash_art.txt"


def _rows(path: Path) -> tuple[str, ...]:
    """
    The art's lines, splitting on U+000A only.

    ``str.splitlines`` is the obvious call and the wrong one: it also breaks on
    U+000B, U+000C, U+001C-U+001E and U+2028, any of which could appear in text
    art as a *glyph* and would silently split one row into two.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return tuple(lines)


ART: tuple[str, ...] = _rows(_ART_PATH)


# Ink buckets from `scripts/rank_glyphs.py` -- see that script for what the two
# metrics measure and where they are only approximations. Regenerate with it rather
# than editing by hand; tests/test_splash_art.py recomputes the whole table from the
# font and fails if the literal and the script drift apart.
#
# Twelve ink bands with a floor of three members is the knee of the measured curve:
# eleven admit a 22.7% within-band ink spread, twelve admit 17.2%, and thirteen only
# reaches 16.6% while adding another three-member band. So no swap changes a cell's
# ink by more than about a sixth, and every cell has at least two other glyphs it can
# become.
#
# Two of those bands are then split in half by ink *height*, giving fourteen buckets.
# Equal ink area is not equal ink position: U+201E and U+201C are all but the same
# shape 0.5315 em apart, with areas agreeing to within 0.088%, so a cell can swap
# between them, hold its brightness and hop most of the cell. Some of that is wanted
# -- the panel is meant to read as glitchy -- so the split *bounds* the travel rather
# than removing it, and the bound is loose on purpose.
#
# What it costs is that the buckets are no longer totally ordered dim to bright: two
# buckets sharing one ink band interleave, so what holds instead is that no bucket is
# more than one band's ink ratio brighter than any later one.
#
# Escaped rather than literal because the inventory contains U+00AD, which is
# invisible in an editor, and CP1252-range punctuation whose ASCII lookalikes are
# one keystroke away.
_BUCKETS: tuple[str, ...] = (
    "`\xb4\xb7",
    "\xaf\u02c6\xa8",
    "\xb8\xad\u201a",
    "\u02dc\u2019\u2018",
    ":\u2022~\u2013\u2039\u203a\xb9",
    "\xac\u2026\u2014^;",
    "\xf7\xb3\u201e",
    "\xb0\xb2\u201d\u201c",
    "\xab\xbb!\xa1+\xa6=\xd7r/1|<>",
    "\u2122*\xa4\xbf?vi\xec\xedLc7)(",
    "Tx\xefJ\xba\xeeYltI{}\xb1fs\u2020zF\xaanu[CjV3y",
    (
        "eo\xa32\xe7\xdd\xbd\xcd\xbc\xcc\xf9\xfa\u0192a4\xfdSZw\u0178\xcf\xb5\xae5\xa9\xce\xe8\xe9"
        "\xf3\xf2h\u0161\xbe\u017e\xfck\u2021\xde\xf1P\xff%\xfb\xc7U"
    ),
    "\xa5G\xe0\xe196\xeb\xf6\xf5m\xf4\u2030\xa2d$\xe4D\xe3Ob\xda\xd9\xe58\xa7@&\xdc",
    "\xf00\xd3\xd2\xd6\xb6",
)

RANKS: tuple[tuple[str, ...], ...] = tuple(tuple(bucket) for bucket in _BUCKETS)

# Derived, not written out a second time. An index that had to be kept in step
# with RANKS by hand is a stored duplicate of a derivable fact, and the one thing
# derivation does *not* catch is a character listed in two buckets -- the dict
# would take whichever came last and say nothing. tests/test_splash_art.py checks
# for that directly.
RANK_OF: Mapping[str, int] = {char: index for index, bucket in enumerate(RANKS) for char in bucket}
