#!/usr/bin/env python3
"""
Which open fonts installed on this machine would be worth bundling?

pptmstr ships one monospace family in two weights and no italic, and
``planning/archive/2026-08-10-transcript-markdown.md`` records "emphasis is a
colour shift, not a slant" as the decision that forced. This measures what a
replacement or a companion would cost and buy, from the font files themselves,
and renders one fixed document in each so a person can look.

Two modes:

  1. **The table** (default; no display). Enumerates families through
     ``fc-list``, keeps the ones that could set Latin text in a regular and a
     bold, and for each named in ``CANDIDATES`` prints its faces, sizes,
     metrics at a 16 px ImGui pixel height, glyph coverage and licence
     evidence. Every number is read with fontTools directly from the file whose
     path is printed; ``advtext.fontlib.em_size_for_pixel_height`` is called
     once, on the two bundled faces, as a cross-check that the pixel-height
     arithmetic here is the library's.
  2. **The renders** (``--render``; needs a display). One child process per
     family, because ``advtext``'s font registry is process-wide and ImGui's
     atlas is filled once: the candidate is registered under the four prose
     roles for that run only, straight from its installed path. Nothing is
     copied into the tree.

A licence line is evidence only where it names its source: the font's own
``name`` records 13 and 14, and the ``License:`` field of the ``Files: *``
stanza in the owning Debian package's copyright file. Where either is absent
the table says absent; it does not fill the gap.

What a render does and does not show is in ``render_one``.

Usage:  .venv/bin/python scripts/survey_fonts.py
        XVFB_SHOT_SCREEN=1600x1300x24 scripts/xvfb_shot.sh -- \\
            .venv/bin/python scripts/survey_fonts.py --render
        ... --render Hack "Noto Sans"       # only these
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

PIXEL_HEIGHT = 16.0
"""
The ImGui pixel height everything is measured and drawn at: pptmstr's UI font size.
"""

LINE_PX = 370.0
"""
The width a line is counted against: the DETAIL dock at the default layout, as
the task that asked for this states it. Not measured here.
"""

MIN_LATIN_LETTERS = 200
"""
How many Latin letters a regular face must map to count as a Latin text face.

ASCII and Latin-1 are 114 letters and Latin Extended-A is 127 more, so this
asks for most of all three. It is what separates a text family from an Indic or
Thai one that carries ASCII as a courtesy; it does not separate a Latin family
from an Arabic one drawn with a full Latin, which is what ``SET_ASIDE`` is for.
"""

CANDIDATES: tuple[str, ...] = (
    "Hack",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Noto Sans Mono",
    "FreeMono",
    "Nimbus Mono PS",
    "Noto Sans",
    "DejaVu Sans",
    "Liberation Sans",
    "Andika",
    "FreeSans",
    "Nimbus Sans",
    "Noto Serif",
    "DejaVu Serif",
    "Liberation Serif",
    "Yrsa",
)
"""
The families measured and rendered, monospace first.
"""

SET_ASIDE: dict[str, str] = {
    "Amiri": "an Arabic family; its Latin is a companion",
    "Mashq": "an Arabic family; its Latin is a companion",
    "UKIJ": "Uyghur families; their Latin is a companion",
    "Laksaman": "a Thai family; its Latin is a companion",
    "Noto Sans Display": "a display cut of Noto Sans, drawn for large sizes",
    "Noto Serif Display": "a display cut of Noto Serif, drawn for large sizes",
    "Quicksand": "a geometric display face with no italic",
    "Rasa": "Yrsa's Gujarati sibling; no italic",
    "C059": "a Century Schoolbook clone; same package and licence as Nimbus",
    "P052": "a Palatino clone; same package and licence as Nimbus",
    "FreeSerif": "a Times clone; same package and licence as FreeSans",
    "Nimbus Roman": "a Times clone; same package and licence as Nimbus Sans",
    "Nimbus Sans Narrow": "a condensed cut of Nimbus Sans",
    "URW Gothic": "an Avant Garde clone, book and demi only; same package as Nimbus",
    "Liberation Sans Narrow": "a condensed cut, and only in the Liberation 1 package",
}
"""
Families that pass the mechanical filter and are not measured, by name prefix,
with the reason. A passing family named in neither table prints as unclassified.
"""

SAMPLE_PARAGRAPH = (
    "The reducer is pure and total: every arm either produces a new snapshot or "
    "returns the old one unchanged, nothing raises, nothing reads a clock and "
    "nothing performs IO. An intent for something that does not exist is a no-op, "
    "not an error, so a double click or an already-settled approval costs nothing."
)
"""
The fixed text characters-per-line is counted over: prose of the kind a model writes.
"""

MARKDOWN = """\
# Font survey sample

## What the reducer promises

The reducer is **pure and total**: every arm *either* produces a new snapshot
or returns the old one unchanged. `_apply(snap, intent, now)` reads no clock,
and ***nothing raises***. An *emphasised phrase long enough to judge the italic
by* sits beside `inline_code()` and 0O1lI| {}[] -> != <=.

- A first item, with **bold** and *italic* in it.
- A second, long enough to wrap onto another line at this width.

```python
def is_claimable(task, done):
    # blocked-ness is derived, not stored
    return all(d in done for d in task.depends_on)
```

| state | meaning | count |
|---|---|---|
| `CLAIMED` | *held* by an agent | 12 |
| `DONE` | **finished** | 340 |

Symbols: → ← ⇒ ✓ ✗ … — ≤ ≥ ≠ αβγ Δ ┌─┬─┐ │ └─┴─┘
"""
"""
The one document every family is rendered in.
"""

COVERAGE: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("arrows", (0x2192, 0x2190, 0x21D2)),
    ("ticks", (0x2713, 0x2717)),
    ("punct", (0x2026, 0x2014)),
    ("box", tuple(range(0x2500, 0x2580))),
    ("greek", (*(c for c in range(0x391, 0x3AA) if c != 0x3A2), *range(0x3B1, 0x3CA))),
    ("rel", (0x2264, 0x2265, 0x2260)),
)
"""
What model output uses that the shipped monospace may lack: → ← ⇒, ✓ ✗, … —,
the box-drawing block, the Greek alphabet in both cases (final sigma included),
and ≤ ≥ ≠.
"""

_FC_FORMAT = "%{family[0]}\t%{style[0]}\t%{file}\t%{weight}\t%{slant}\t%{width}\t%{fontformat}\n"
_REGULAR, _NORMAL_WIDTH = 80, 100
# fontconfig's demibold and bold: FreeSans names its bold "Bold" and weighs it 180.
_BOLD = (180, 200)


@dataclass(frozen=True, slots=True)
class Family:
    """
    One family's four text faces, as files. A face the family lacks is None.
    """

    name: str
    regular: Path
    bold: Path
    italic: Path | None
    bold_italic: Path | None
    italic_style: str
    source: str

    @property
    def files(self) -> tuple[Path, ...]:
        """
        The faces that exist, regular first.
        """
        faces = (self.regular, self.bold, self.italic, self.bold_italic)
        return tuple(face for face in faces if face is not None)

    @property
    def slug(self) -> str:
        """
        The family's name as it appears in a file name.
        """
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


@cache
def _font(path: Path) -> Any:
    from fontTools.ttLib import TTFont

    return TTFont(str(path), lazy=True)


def _revision(path: Path) -> float:
    return float(_font(path)["head"].fontRevision)


def installed() -> tuple[int, dict[str, Family]]:
    """
    Every installed family with an upright regular and bold at normal width.

    Returns the count of all families fontconfig lists alongside, so the table
    can say how much was filtered. Only single-font sfnt files are considered:
    a Type 1 file is nothing ImGui can load, and a ``.ttc`` here is always CJK.
    Where two packages install the same family (Liberation 1 and 2 do), the
    file with the higher ``head.fontRevision`` is the one kept.
    """
    listing = subprocess.run(
        ["fc-list", "--format", _FC_FORMAT], capture_output=True, text=True, check=True
    ).stdout
    everything: set[str] = set()
    slots: dict[str, dict[tuple[bool, bool], tuple[Path, str, int]]] = {}
    for line in listing.splitlines():
        name, style, file, weight, slant, width, fmt = line.split("\t")
        everything.add(name)
        path = Path(file)
        if fmt not in ("TrueType", "CFF") or path.suffix.lower() not in (".ttf", ".otf"):
            continue
        if not weight.isdigit() or int(weight) not in (_REGULAR, *_BOLD):
            continue
        if width and int(width) != _NORMAL_WIDTH:
            continue
        key = (int(weight) in _BOLD, slant not in ("", "0"))
        # A family with both a demibold and a bold (Noto) offers the bold; among
        # files of one weight the later revision wins, read only on a tie.
        held = slots.setdefault(name, {}).get(key)
        if (
            held is None
            or int(weight) > held[2]
            or (int(weight) == held[2] and _revision(path) > _revision(held[0]))
        ):
            slots[name][key] = (path, style, int(weight))
    families: dict[str, Family] = {}
    for name, faces in slots.items():
        if (False, False) not in faces or (True, False) not in faces:
            continue
        italic = faces.get((False, True))
        bold_italic = faces.get((True, True))
        families[name] = Family(
            name=name,
            regular=faces[(False, False)][0],
            bold=faces[(True, False)][0],
            italic=None if italic is None else italic[0],
            bold_italic=None if bold_italic is None else bold_italic[0],
            italic_style="none" if italic is None else italic[1],
            source="installed",
        )
    return len(everything), families


def shipped() -> tuple[Family, Family]:
    """
    The two families pptmstr can already draw: Roboto, and Inconsolata.

    Roboto's four faces and Inconsolata's medium weight are imgui-bundle's;
    Inconsolata's bold is the one file pptmstr vendors itself.
    """
    import imgui_bundle

    fonts = Path(imgui_bundle.__file__).parent / "assets" / "fonts"
    roboto = Family(
        name="Roboto",
        regular=fonts / "Roboto" / "Roboto-Regular.ttf",
        bold=fonts / "Roboto" / "Roboto-Bold.ttf",
        italic=fonts / "Roboto" / "Roboto-RegularItalic.ttf",
        bold_italic=fonts / "Roboto" / "Roboto-BoldItalic.ttf",
        italic_style="Italic",
        source="imgui-bundle",
    )
    inconsolata = Family(
        name="Inconsolata",
        regular=fonts / "Inconsolata-Medium.ttf",
        bold=ROOT / "pptmstr" / "assets" / "fonts" / "Inconsolata-Bold.ttf",
        italic=None,
        bold_italic=None,
        italic_style="none",
        source="imgui-bundle + pptmstr",
    )
    return roboto, inconsolata


def latin_letters(path: Path) -> int:
    """
    How many letters named LATIN the face maps.
    """
    cmap = _font(path).getBestCmap() or {}
    return sum(
        1
        for codepoint in cmap
        if unicodedata.category(chr(codepoint)).startswith("L")
        and unicodedata.name(chr(codepoint), "").startswith("LATIN")
    )


def _glyph_top(path: Path, char: str) -> float:
    """
    The top of one glyph's outline in font units: 0 if the face has no such glyph.
    """
    from fontTools.pens.boundsPen import BoundsPen

    font = _font(path)
    name = (font.getBestCmap() or {}).get(ord(char))
    if name is None:
        return 0.0
    glyphs = font.getGlyphSet()
    pen = BoundsPen(glyphs)
    glyphs[name].draw(pen)
    return 0.0 if pen.bounds is None else float(pen.bounds[3])


def _advances(path: Path, text: str) -> list[float]:
    """
    Each character's advance in font units, unkerned; ``.notdef``'s for an unmapped one.
    """
    font = _font(path)
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"]
    return [float(hmtx[cmap.get(ord(char), ".notdef")][0]) for char in text]


def is_monospace(path: Path) -> bool:
    """
    Whether every printable ASCII character has one advance.

    Asked of the metrics and not of ``post.isFixedPitch`` or fontconfig's
    ``spacing``, which Noto Sans Mono leaves unset.
    """
    return len(set(_advances(path, "".join(chr(c) for c in range(0x20, 0x7F))))) == 1


def wrapped_lines(advances: list[float], text: str, measure: float) -> int:
    """
    How many lines a greedy word wrap sets ``text`` in, breaking at spaces only.
    """
    lines, used, word = 1, 0.0, 0.0
    space = advances[text.index(" ")]
    for char, advance in zip(text + " ", [*advances, space], strict=True):
        if char != " ":
            word += advance
            continue
        if used and used + space + word > measure:
            lines, used = lines + 1, word
        else:
            used += (space if used else 0.0) + word
        word = 0.0
    return lines


@dataclass(frozen=True, slots=True)
class Measured:
    """
    One regular face at ``PIXEL_HEIGHT``: every length in pixels.
    """

    em: float
    advance: float
    x_height: float
    cap_height: float
    chars_per_line: float
    lines: int


def measure(path: Path) -> Measured:
    """
    A face's metrics at the pixel height, by ImGui's meaning of height.

    ImGui sizes a font so that ``hhea`` ascender minus descender is the pixel
    height asked for, so the scale is the face's own and two faces "at 16" have
    different ems. That is ``advtext.fontlib``'s statement of it, which
    ``cross_check`` holds this to; the font loader's source was not read.

    x-height and cap-height are the outline tops of ``x`` and ``H``, not the
    ``OS/2`` fields, which older tables omit. The advance is the mean over
    ``SAMPLE_PARAGRAPH``, unkerned -- for a monospace that is its cell.
    """
    font = _font(path)
    hhea = font["hhea"]
    scale = PIXEL_HEIGHT / float(hhea.ascender - hhea.descender)
    advances = [advance * scale for advance in _advances(path, SAMPLE_PARAGRAPH)]
    mean = sum(advances) / len(advances)
    return Measured(
        em=float(font["head"].unitsPerEm) * scale,
        advance=mean,
        x_height=_glyph_top(path, "x") * scale,
        cap_height=_glyph_top(path, "H") * scale,
        chars_per_line=LINE_PX / mean,
        lines=wrapped_lines(advances, SAMPLE_PARAGRAPH, LINE_PX),
    )


def coverage(path: Path) -> dict[str, tuple[int, int]]:
    """
    For each group in ``COVERAGE``, how many of its codepoints the face maps, of how many.
    """
    cmap = _font(path).getBestCmap() or {}
    return {
        name: (sum(1 for codepoint in codepoints if codepoint in cmap), len(codepoints))
        for name, codepoints in COVERAGE
    }


@dataclass(frozen=True, slots=True)
class Licence:
    """
    What two sources say about one file's licence. A field with no source is None.
    """

    name_13: str | None
    name_14: str | None
    copyright_0: str | None
    package: str | None
    copyright_file: Path | None
    debian_licence: str | None
    reserved: tuple[str, ...]


def _package(path: Path) -> str | None:
    found = subprocess.run(["dpkg-query", "-S", str(path)], capture_output=True, text=True)
    return found.stdout.split(":", 1)[0].strip() if found.returncode == 0 else None


def licence(path: Path) -> Licence:
    """
    The licence evidence for one font file: its name table, and its package's copyright file.

    The Debian field read is the ``License:`` of the ``Files: *`` stanza, which
    is the one that covers the font files in every package met here except
    fonts-freefont-ttf, whose fonts have a stanza of their own -- so that one
    is searched for first. Lines naming a reserved font name are collected from
    both sources, because that is the condition an OFL redistribution turns on.
    """
    names = _font(path)["name"]
    name_0 = names.getDebugName(0)
    name_13 = names.getDebugName(13)
    package = _package(path)
    copyright_file = None if package is None else Path("/usr/share/doc") / package / "copyright"
    debian = None
    text = ""
    if copyright_file is not None and copyright_file.is_file():
        text = copyright_file.read_text(encoding="utf-8", errors="replace")
        stanzas = re.findall(r"^Files:(.*?)\n(?:.*?\n)*?License:[ \t]*(.*)$", text, re.MULTILINE)
        by_glob = {glob.strip(): name.strip() for glob, name in stanzas}
        debian = next(
            (name for glob, name in by_glob.items() if ".ttf" in glob), by_glob.get("*")
        ) or (None if not stanzas else "(a License: field with no short name)")
    reserved = sorted(
        {
            " ".join(line.split())
            for source in (text, name_0 or "", name_13 or "")
            for line in source.splitlines()
            if re.search(r"reserved font name", line, re.IGNORECASE)
            and not re.search(r'"Reserved Font Name" refers|the Reserved Font Name\(s\)', line)
            and re.search(r"copyright|with reserved", line, re.IGNORECASE)
        }
    )
    return Licence(
        name_13=name_13,
        name_14=names.getDebugName(14),
        copyright_0=name_0,
        package=package,
        copyright_file=copyright_file,
        debian_licence=debian,
        reserved=tuple(reserved),
    )


def _clip(text: str | None, width: int = 110) -> str:
    if text is None:
        return "<absent>"
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 3] + "..."


def _kb(size: int) -> str:
    return f"{size / 1024:.0f}"


def print_filter(total: int, families: dict[str, Family]) -> dict[str, Family]:
    """
    Print what the mechanical filter kept, and return the candidates among it.
    """
    passing = {
        name: family
        for name, family in sorted(families.items())
        if latin_letters(family.regular) >= MIN_LATIN_LETTERS
    }
    print(f"fontconfig lists {total} families.")
    print(
        f"{len(families)} have an upright regular and bold at normal width in a .ttf/.otf; "
        f"{len(passing)} of those map at least {MIN_LATIN_LETTERS} Latin letters."
    )
    print()
    for name in passing:
        if name in CANDIDATES:
            verdict = "candidate"
        else:
            reason = next((why for prefix, why in SET_ASIDE.items() if name.startswith(prefix)), "")
            verdict = f"set aside: {reason}" if reason else "UNCLASSIFIED"
        print(f"  {name:24} {verdict}")
    missing = [name for name in CANDIDATES if name not in passing]
    if missing:
        print(f"\n  named in CANDIDATES and not installed or not passing: {', '.join(missing)}")
    return {name: passing[name] for name in CANDIDATES if name in passing}


def print_table(rows: list[Family]) -> None:
    """
    The metrics, coverage and size tables, one row per family, baselines first.
    """
    print(f"\n## Metrics at a {PIXEL_HEIGHT:g} px pixel height (fontTools, regular face)\n")
    print(
        f"{'family':20} {'mono':4} {'em px':>6} {'adv px':>6} {'x-h px':>6} {'cap px':>6} "
        f"{'ch/' + format(LINE_PX, 'g'):>6} {'lines':>5}  faces (italic style)"
    )
    for family in rows:
        m = measure(family.regular)
        faces = "".join(
            letter if face is not None else "-"
            for letter, face in zip(
                ("R", "B", "I", "X"),
                (family.regular, family.bold, family.italic, family.bold_italic),
                strict=True,
            )
        )
        print(
            f"{family.name:20} {'yes' if is_monospace(family.regular) else 'no':4} "
            f"{m.em:6.2f} {m.advance:6.2f} {m.x_height:6.2f} {m.cap_height:6.2f} "
            f"{m.chars_per_line:6.1f} {m.lines:5d}  {faces} ({family.italic_style})"
        )
    print(
        "\n  adv = mean unkerned advance over SAMPLE_PARAGRAPH; ch/370 = 370 / adv; lines ="
        "\n  that paragraph greedily wrapped at 370 px. faces: R regular, B bold, I italic,"
        "\n  X bold italic."
    )

    print("\n## Coverage (regular face): mapped / asked\n")
    header = " ".join(f"{name:>7}" for name, _ in COVERAGE)
    print(f"{'family':20} {header}")
    for family in rows:
        found = coverage(family.regular)
        cells = " ".join(f"{f'{have}/{of}':>7}" for have, of in found.values())
        cmap = _font(family.regular).getBestCmap() or {}
        lacks = "".join(
            chr(codepoint)
            for _, codepoints in COVERAGE
            if len(codepoints) <= 3
            for codepoint in codepoints
            if codepoint not in cmap
        )
        print(f"{family.name:20} {cells}   {'lacks ' + ' '.join(lacks) if lacks else ''}".rstrip())

    print("\n## Size on disk, kB\n")
    print(f"{'family':20} {'R':>6} {'B':>6} {'I':>6} {'X':>6} {'total':>7}  directory")
    for family in rows:
        sizes = [
            None if face is None else face.stat().st_size
            for face in (family.regular, family.bold, family.italic, family.bold_italic)
        ]
        cells = " ".join(f"{'-' if size is None else _kb(size):>6}" for size in sizes)
        total = sum(size for size in sizes if size is not None)
        print(f"{family.name:20} {cells} {_kb(total):>7}  {family.regular.parent}")


def print_licences(rows: list[Family]) -> None:
    """
    The licence evidence, one block per family, each line naming where it was read.
    """
    print("\n## Licence evidence (regular face)\n")
    for family in rows:
        found = licence(family.regular)
        version = _clip(_font(family.regular)["name"].getDebugName(5), 60)
        print(f"{family.name}  [{family.source}]  {version}")
        print(f"  file          {family.regular}")
        print(f"  name[0]       {_clip(found.copyright_0)}")
        print(f"  name[13]      {_clip(found.name_13)}")
        print(f"  name[14]      {_clip(found.name_14)}")
        if found.package is None:
            print("  package       <no Debian package owns this file>")
        else:
            print(f"  package       {found.package}: {found.copyright_file}")
            print(f"  License:      {_clip(found.debian_licence)}")
        for line in found.reserved:
            print(f"  reserved name {_clip(line)}")
        print()


def cross_check() -> None:
    """
    Compare this script's pixel-height arithmetic with advtext's, on the bundled faces.
    """
    from advtext import Face, em_size_for_pixel_height

    roboto, inconsolata = shipped()
    print("\n## Cross-check against advtext.em_size_for_pixel_height\n")
    for label, face, family in (
        ("Roboto", Face.PROSE, roboto),
        ("Inconsolata", Face.CODE, inconsolata),
    ):
        theirs = em_size_for_pixel_height(face, PIXEL_HEIGHT)
        ours = measure(family.regular).em
        agree = "agree" if abs(theirs - ours) < 1e-6 else "DISAGREE"
        print(f"  {label:12} advtext {theirs:.3f} px   here {ours:.3f} px   {agree}")


def _colour(rgb: int, alpha: int = 255) -> int:
    """
    0xRRGGBB as ImGui packs a colour: alpha high, then blue, green, red.
    """
    red, green, blue = (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
    return (alpha << 24) | (blue << 16) | (green << 8) | red


def render_one(name: str, out: Path) -> int:
    """
    Render ``MARKDOWN`` in one family beside today's all-Inconsolata, and write two PNGs.

    The left pane is the candidate and the right pane is the family pptmstr
    draws everything in now, set by ``advtext``'s all-mono theme rather than by
    pptmstr's own panes; both are ``LINE_PX`` wide at ``PIXEL_HEIGHT``. A proportional candidate
    takes the four prose roles and leaves code to Inconsolata, so the picture
    shows how the two sit together. A monospace candidate takes every role,
    code included: ``advtext`` has no monospace-italic role, so its italic is
    registered under the prose italic and the face table is declared
    proportional, which is the only way ``Theme.face_for`` will reach it.

    The second PNG is the top of the first magnified three times with no
    filtering, for judging the raster rather than the design.

    What this shows: the glyphs ImGui's font loader rasterises from these files
    at this size, on whatever GL the display has -- under ``xvfb_shot.sh`` that
    is Mesa's llvmpipe, a software rasteriser. The atlas is baked on the CPU
    and drawn 1:1, so glyph shapes should not depend on the GPU, but nobody
    has compared; the loader's and renderer's names are printed so the claim
    is tied to what ran. The loader matters more than the GPU: a build of
    imgui-bundle whose loader is FreeType applies a face's hinting, and one on
    stb_truetype applies none.

    A character the candidate's regular face does not map is drawn as ImGui's
    fallback glyph and is **not** counted by the view's ``DrawStats.missing``,
    which counts something else. So the unmapped characters of ``MARKDOWN`` are
    worked out here from the ``cmap`` and printed beside the draw statistics.

    What it does not show: any subpixel or LCD rendering,
    a high-DPI display, or pptmstr's own panes.
    """
    from advtext import (
        Align,
        BareView,
        BlockKind,
        Face,
        Faces,
        Palette,
        Theme,
        build_document,
        char_measure,
        fontlib_metrics,
        load_faces,
        register_font,
    )
    from imgui_bundle import hello_imgui, imgui, immapp
    from OpenGL import GL
    from screenshot import write_png

    roboto, inconsolata = shipped()
    if name == roboto.name:
        family = roboto
    else:
        family = installed()[1][name]
        italic = family.italic or family.regular
        register_font(Face.PROSE, family.regular)
        register_font(Face.PROSE_BOLD, family.bold)
        register_font(Face.PROSE_ITALIC, italic)
        register_font(Face.PROSE_BOLD_ITALIC, family.bold_italic or family.bold)
    register_font(Face.CODE_BOLD, inconsolata.bold)

    mono = is_monospace(family.regular)
    candidate_faces = (
        Faces(code=Face.PROSE, code_bold=Face.PROSE_BOLD, monospace=frozenset())
        if mono
        else Faces()
    )
    themes = (
        Theme.at_pixel_height(PIXEL_HEIGHT, faces=candidate_faces, align=Align.LEFT),
        Theme.at_pixel_height(PIXEL_HEIGHT, faces=Faces.mono(), align=Align.LEFT),
    )
    captions = (
        f"{family.name}" + ("" if mono else " + Inconsolata code") + f"  {PIXEL_HEIGHT:g} px",
        f"Inconsolata, every role (advtext's all-mono theme)  {PIXEL_HEIGHT:g} px",
    )
    # pptmstr's dark palette: text, text_strong for headings, text_dim for a
    # quote, border for rules, accent for links, on the panel colour.
    palette = Palette(
        ink=_colour(0xC9CEDC),
        rule=_colour(0x323848),
        highlight=_colour(0x2B3550, 0xB0),
        link=_colour(0x4FC1E9),
    ).with_roles(
        {
            BlockKind.HEADING_1: _colour(0xF0F3FA),
            BlockKind.HEADING_2: _colour(0xF0F3FA),
            BlockKind.QUOTE: _colour(0x7C8397),
        }
    )
    ground, panel = _colour(0x14161C), _colour(0x1A1D26)
    views = [
        BareView(
            document=build_document(MARKDOWN, theme, fontlib_metrics),
            theme=theme,
            measure=char_measure(fontlib_metrics),
            px_per_inch=theme.px_per_inch,
        )
        for theme in themes
    ]
    seen: dict[str, Any] = {"frame": 0, "fonts": None, "stats": [], "done": False}

    def load_fonts() -> None:
        hello_imgui.imgui_default_settings.load_default_font_with_font_awesome_icons()
        seen["fonts"] = load_faces()

    def gui() -> None:
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(viewport.work_pos)
        imgui.set_next_window_size(viewport.work_size)
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_move.value
        imgui.push_style_color(imgui.Col_.window_bg, ground)
        imgui.push_style_color(imgui.Col_.child_bg, panel)
        imgui.begin("survey_fonts", None, flags)
        stats, heights = [], []
        for number, (view, caption) in enumerate(zip(views, captions, strict=True)):
            if number:
                imgui.same_line()
            imgui.begin_group()
            imgui.text_unformatted(caption)
            imgui.begin_child(
                f"##pane-{number}", imgui.ImVec2(LINE_PX, 0.0), imgui.ChildFlags_.borders.value
            )
            top = imgui.get_cursor_pos_y()
            stats.append(view.draw(fonts=seen["fonts"], palette=palette))
            heights.append(imgui.get_cursor_pos_y() - top)
            imgui.end_child()
            imgui.end_group()
        seen["stats"], seen["heights"] = stats, heights
        imgui.end()
        imgui.pop_style_color(2)

    def before_swap() -> None:
        if seen["frame"] != 8:
            return
        io = imgui.get_io()
        width = int(io.display_size.x * io.display_framebuffer_scale.x)
        height = int(io.display_size.y * io.display_framebuffer_scale.y)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        GL.glReadBuffer(GL.GL_BACK)
        data = bytes(GL.glReadPixels(0, 0, width, height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE))
        stride = width * 3
        rows = [data[y * stride : (y + 1) * stride] for y in range(height - 1, -1, -1)]
        out.parent.mkdir(parents=True, exist_ok=True)
        write_png(out, width, height, rows)
        zoom, crop = 3, min(height, 330)
        zoomed = [
            b"".join(row[x : x + 3] * zoom for x in range(0, stride, 3))
            for row in rows[:crop]
            for _ in range(zoom)
        ]
        write_png(out.with_name(out.stem + "-zoom.png"), width * zoom, crop * zoom, zoomed)
        seen["renderer"] = GL.glGetString(GL.GL_RENDERER).decode()
        seen["loader"] = io.fonts.font_loader_name
        seen["done"] = True

    def after_swap() -> None:
        seen["frame"] += 1
        if seen["frame"] > 9:
            hello_imgui.get_runner_params().app_shall_exit = True

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "survey_fonts"
    params.app_window_params.window_geometry.size = (int(LINE_PX) * 2 + 30, 1240)
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.fps_idling.enable_idling = False
    params.callbacks.load_additional_fonts = load_fonts
    params.callbacks.show_gui = gui
    params.callbacks.before_swap = before_swap
    params.callbacks.after_swap = after_swap
    immapp.run(params)

    if not seen["done"]:
        print(f"FAIL {name}: the window closed before a frame was grabbed")
        return 1
    drawn = " ".join(f"glyphs={s.glyphs}" for s in seen["stats"])
    cmap = _font(family.regular).getBestCmap() or {}
    unmapped = "".join(sorted({c for c in MARKDOWN if not c.isspace() and ord(c) not in cmap}))
    heights = " ".join(f"{height:.0f}" for height in seen["heights"])
    print(
        f"wrote {out.relative_to(ROOT)} (+ -zoom)  {name}: candidate|inconsolata {drawn}  "
        f"unmapped in the sample [{' '.join(unmapped)}]  "
        f"heights px {heights}  loader={seen['loader']}  renderer={seen['renderer']}"
    )
    return 0


def render(names: list[str], out: Path) -> int:
    """
    Render each named family in a child process of its own, into ``out``.
    """
    roboto, _ = shipped()
    known = {roboto.name: roboto, **installed()[1]}
    wanted = names or [roboto.name, *(name for name in CANDIDATES if name in known)]
    status = 0
    for name in wanted:
        if name not in known:
            print(f"FAIL {name}: not installed")
            status = 1
            continue
        target = out / f"fonts-{known[name].slug}.png"
        child = subprocess.run(
            [sys.executable, __file__, "--render-one", name, "--out", str(target)], check=False
        )
        status = status or child.returncode
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--render", nargs="*", metavar="FAMILY", help="render these, or all")
    parser.add_argument("--render-one", metavar="FAMILY", help=argparse.SUPPRESS)
    parser.add_argument("--out", type=Path, default=ROOT / "shots")
    args = parser.parse_args()

    if args.render_one is not None:
        return render_one(args.render_one, args.out)
    if args.render is not None:
        return render(args.render, args.out)

    total, families = installed()
    candidates = print_filter(total, families)
    rows = [*shipped(), *candidates.values()]
    print_table(rows)
    cross_check()
    print_licences(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
