"""
An allow-list SVG sanitiser: model-written SVG in, safe SVG and its natural size out.

`sanitise` is the policy of planning/2026-10-18-vector-displays-beside-typeset-text.md
§3.4, and the natural-size rule of §3.6 "Height". It is pure: stdlib only, no I/O, no
clock, no ImGui. It never raises; every failure is a `Refused` with a one-line reason
the host shows above the fence's source.

It is mandatory and not defence in depth: both renderers measured (resvg, novasvg) read
`<image href="/abs/path">` from disk and draw it, and resvg reads relative hrefs too
(notes/vector-rendering/svg-renderers.md, "Measured"). Nothing here relies on what a
renderer happens to ignore.

Policy, in the order it is applied:

1. **Markup declarations are refused before parsing.** Any `<!` that does not open a
   comment or a CDATA section -- `<!DOCTYPE`, `<!ENTITY`, `<!ELEMENT`, in any case and
   anywhere in the text, including inside comments -- refuses the whole input. With
   no DTD nothing can declare an entity, so billion laughs and XXE never reach the
   parser; expat then refuses an undefined entity reference.
2. **Elements are an allow-list** (`ALLOWED_ELEMENTS`). Anything else is dropped with
   its subtree, which includes `script`, `image`, `a`, `iframe`, `filter`, `feImage`,
   `pattern` and the animation elements. `foreignObject` is rewritten to a centred
   `<text>` at the same place in the tree (see `_rewrite_foreign_object`), because
   Mermaid hangs its labels on it. `filter` stays out: merman's drop shadow is about
   half of resvg's render time for a flowchart (54 ms without it, 112 ms with it;
   notes/vector-rendering/probe-m1-r2.md). `pattern` stays out because resvg
   allocates its tile whole at its size under every transform above it, which no
   check here can bound: a 100-unit tile under `scale(160)` took 1 GB and under
   `scale(1000)` asked for 40 GB (probe R3). The render child's `RLIMIT_AS` catches
   that on Linux, but macOS does not enforce `RLIMIT_AS`, and there it would be
   bounded only by the render timeout. Mermaid and merman emit no patterns; a fill
   that named one takes its fallback or `none` (step 8).
3. **Attributes.** Every `on*` attribute is dropped, compared case-insensitively.
   `href` and `xlink:href` survive only as a same-document `#id`, and come out as
   plain `href` (SVG 2, which resvg reads), so the output needs no xlink namespace.
   Any other namespaced attribute except `xml:space` is dropped. An attribute whose
   value contains a backslash, a `url(` other than `url(#id)`, or a function that
   takes a URL as a string (`image(`, `image-set(`, `src(`) is dropped whole, except
   in `fill` and `stroke`, where a removed paint is black: there the value becomes
   the colour after a leading `url()` (`url(http://x) red` is `red`) or `none`, as in
   step 8, and `Sanitised.notes` says so; the same holds for those declarations in
   CSS (step 4). Such a fallback is kept only if it is a colour in the grammar of
   `_colour_or_none`, since an invalid paint is black too (`fill="foo bar"`,
   measured). The `filter` property is dropped wherever it is set, attribute or
   CSS: with `<filter>` gone it can only name the wrong thing or be a filter
   function, and resvg draws those (`blur(200px)` on 50 full-canvas rects took 44 s
   at 1600x1200; probe R3). `rebeccapurple`, in any case, is written `#663399` in
   every `fill`, `stroke`, `stop-color`, `flood-color` and `color`, attribute or CSS:
   it is the one CSS named colour resvg_py 0.5.0 does not know, and it paints it black
   (measured 2026-10-21; see `_COLOUR_SPELLED`). This is a gap in that version, and
   the rewrite can go when the renderer knows the name.
4. **CSS** in `style=` and in `<style>` is split into declarations, and a declaration
   with a backslash, `@import`, a non-fragment `url(` or a URL-taking function is
   dropped whole, or repainted if it sets `fill` or `stroke` (step 3). In a `<style>`,
   a rule whose selector has one of those is dropped with its block. A `<style>` is
   dropped whole only where its contents cannot be trusted to parse as rules: an
   `@import`, or an at-rule not in `HARMLESS_AT_RULES` (`@font-face` among them).
   Dropping it whole would paint black every shape its classes coloured.
   Nothing is cut out of the middle of a value: a CSS escape makes `u\\72l(` a url
   token, so substring stripping can be evaded, and the backslash rule closes that.
5. **Caps** (`MAX_INPUT_BYTES`, `MAX_INPUT_ELEMENTS`, `MAX_DEPTH`, `MAX_ELEMENTS`,
   `MAX_LAYER_CHAIN`, `MAX_SIDE`, `MAX_NATURAL_PIXELS`). The element cap counts the
   sanitised tree *after* reference expansion: see `_expanded_size`. What these do
   not bound is render time: a full-canvas layer (opacity, mask) costs about 0.1 s
   at 1600x1200 nested or not, and marker repetition and tiny dashes cost seconds
   from a few elements (probe R3). The render process's timeout bounds those.
6. The text is parsed with `xml.etree` after step 1, a new tree is built from what
   passes, and that is serialised with the SVG namespace as the default. Comments
   and processing instructions are not kept (`<?xml-stylesheet?>` among them).
7. **Every font-family names a family the renderer maps.** pptmstr renders with
   `skip_system_fonts` and maps only `serif`, `sans-serif` and `monospace` to its own
   face. `sans-serif` is appended to a list naming none of those, in `font-family=`,
   in the `font` shorthand and in CSS, so the face is chosen by the document and not
   by resvg's fallback for an unmatched family (which probe R1c measured to be
   `serif_family`). `cursive` and `fantasy` are generic families that are not
   mapped, so a list with only those gets `sans-serif` too.
8. **A reference whose target is not in the output is resolved, last.** A `url(#id)`
   or `href="#id"` naming an id the sanitised tree does not have -- a `<filter>` this
   policy dropped, or an id that never existed -- makes resvg skip the referencing
   element (probe R1b: merman's `filter:url(#merman-drop-shadow)` erased every node
   box). So does a `url(#id)` naming an element of the wrong kind for its property
   (`_TARGET_KINDS`: a clip-path naming a `<rect>`), which is treated as dangling.
   In `fill` and `stroke` the value becomes its fallback (`url(#gone) red` is `red`)
   or `none`, which is what SVG 2 makes of a missing paint server; a fallback that is
   not a colour (step 3) is `none` too. Each such repaint is a line in
   `Sanitised.notes`, as in step 3. Anywhere else
   (`clip-path`, `mask`, `marker-*`, `href`, ...) the attribute or the CSS
   declaration is removed, so the element draws without the effect. An `href` needs
   only its target to exist.
9. **CSS custom properties are substituted, right after the parse and before step 2**
   (see `_substitute_custom_properties`). resvg does not implement them: a `var()`
   reaches it as an unknown value and `fill:var(--c)` paints black. Every
   `--name: value` declaration, in a `<style>` rule or a `style=` attribute, goes into
   one document-wide table, a later one replacing an earlier: selectors, inheritance
   and the cascade are not modelled, so a property declared differently under two
   selectors takes the last value everywhere. Each `var(--name)` and
   `var(--name, fallback)` in an attribute, a `style=` or a `<style>` is replaced, and
   the declarations themselves are removed. A `var()` that cannot be resolved -- not
   declared and no fallback, a cycle, nested deeper than `MAX_VAR_DEPTH`, malformed --
   or that resolves to text step 3 or 4 would refuse (an escape, a non-fragment
   `url(`), or to text that spells `var(` again -- fails its attribute or declaration.
   In `fill` and `stroke` that becomes `none`, as in step 8, and in `stop-color`
   `transparent`, because each of those removed is black; anything else is removed.
   A colour-taking property (`_COLOUR_TAKING`) that resolves is held to a colour resvg
   paints, since the substitution is this module's output: it stays only if it is a
   colour (`_colour_or_none`), `inherit`, or in a paint a `url(#id)`, and is otherwise
   failed as above (`--c: foo; fill: var(--c)` would paint black). Each failure is a
   line in `Sanitised.notes`. A `var()` inside a selector empties
   that `<style>`. Text is substituted wherever `var(` appears, inside a CSS string
   too, so none survives. The work and the output are bounded: see `_Vars` and
   `MAX_VAR_VALUES`. A document whose substitution would pass `MAX_INPUT_BYTES` is
   refused as soon as it would, each use of a value counted, before the text is built.

The root's `width` and `height` are rewritten to the natural size, so a renderer that
sizes its canvas from them gets the size the host laid out for.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
XML_NS = "http://www.w3.org/XML/1998/namespace"

# 1 MiB. The probe's stress document (1,200 rects and 1,200 labels) is about 130 KB, and
# Mermaid's output for a large flowchart is a few hundred KB, most of it <style>. A
# megabyte is past anything a reply should carry inline and parses in milliseconds.
MAX_INPUT_BYTES = 1 << 20

# 5,000 elements after reference expansion. The probe's 2,400-element document took
# about 450 ms in resvg (text-dominated, ~0.3 ms a label), so this bounds one render to
# about a second on the worker. A typical Mermaid diagram is a few hundred.
MAX_ELEMENTS = 5_000

# Elements in the parsed input, before anything is dropped. The walks that clean the tree
# are Python and cost about 2 us an element, so this is what bounds them: a megabyte of
# `<g/>` is 262,000 elements and took 1.4 s on the frame thread before this cap. Four
# times MAX_ELEMENTS leaves room for what the cleaning drops (Mermaid's HTML labels are a
# few XHTML elements inside each foreignObject; merman's output keeps all but four).
MAX_INPUT_ELEMENTS = 4 * MAX_ELEMENTS

# How many masks and clip paths may be live inside one another, through
# nesting or through `url(#id)`. resvg holds a canvas-sized pixmap for each while it
# draws what is inside: a mask chain 68 deep (the deepest MAX_ELEMENTS admits) took
# 655 MB and 11.8 s at 1600x1200 (probe R3, 2026-10-20). Mermaid uses none deeper than
# one. At the largest raster (4096x4096) a mask chain of four peaked at 692 MB of
# address space and eight at 1.28 GB, over the render child's limit
# (`render.MEMORY_LIMIT_BYTES`). References made from `<style>` rules are not followed
# (see `_expanded_size`); that limit is what bounds those.
MAX_LAYER_CHAIN = 4
_LAYERS = frozenset({"mask", "clipPath"})

# Nesting depth of the parsed input. Mermaid nests about a dozen deep. The bound is what
# lets every walk here recurse without meeting Python's recursion limit.
MAX_DEPTH = 64

# Natural size. 4096 x 4096 is 64 MiB of RGBA at 1x. The raster is made at the drawn
# width, never above the natural width, and the renderer seam holds each of its sides to
# `render.MAX_TEXTURE_SIDE`, shrinking a longer figure to fit.
MAX_SIDE = 16_384.0
MAX_NATURAL_PIXELS = 4096 * 4096

# How many `var()` may be open inside one another, through a declared value or a
# fallback, while one value is resolved. A design-token sheet nests two or three deep (a
# role colour naming a palette colour). The bound also caps how many times a fallback's
# text is searched again for `var(`, once per enclosing level.
MAX_VAR_DEPTH = 8

# Distinct values holding a `var()` that one document may have resolved. Each costs
# about 10 us of Python, so at the byte cap (50,000 of them) step 9 alone took about a
# second on the frame thread. A chart's values repeat a palette of a few dozen, and a
# repeat is a lookup (`_Vars.resolved`).
MAX_VAR_VALUES = MAX_ELEMENTS

# Lines of `Sanitised.notes` step 9 writes before it says only that there are more:
# the host shows them, and a document can fail a var() in every declaration.
MAX_NOTES = 8

ALLOWED_ELEMENTS: frozenset[str] = frozenset(
    {
        # structure
        "svg",
        "g",
        "defs",
        "symbol",
        "use",
        # shapes
        "path",
        "rect",
        "circle",
        "ellipse",
        "line",
        "polyline",
        "polygon",
        # text
        "text",
        "tspan",
        "textPath",
        # paint servers and masking
        "linearGradient",
        "radialGradient",
        "stop",
        "clipPath",
        "mask",
        # the rest
        "marker",
        "style",
        "title",
        "desc",
    }
)

# Elements whose content is text only; child elements inside them are discarded.
_TEXT_ONLY = frozenset({"style", "title", "desc"})

# The generic families the renderer seam maps to pptmstr's own face (§3.4, "Fonts").
MAPPED_GENERICS: frozenset[str] = frozenset({"serif", "sans-serif", "monospace"})
FALLBACK_FAMILY = "sans-serif"

_CSS_WIDE = frozenset({"inherit", "initial", "unset", "revert", "revert-layer"})
_SYSTEM_FONTS = frozenset({"caption", "icon", "menu", "message-box", "small-caption", "status-bar"})

# XHTML elements whose boundaries are line breaks in a foreignObject label.
_BLOCK = frozenset(
    {"br", "div", "p", "li", "ul", "ol", "tr", "table", "pre", "blockquote", "section"}
    | {f"h{n}" for n in range(1, 7)}
)
_LINE_HEIGHT_EM = 1.2

_ID = r"#[\w.:-]+"
_FRAGMENT = re.compile(_ID)
_LOCAL_URL = re.compile(rf"url\(\s*(?:{_ID}|\"{_ID}\"|'{_ID}')\s*\)", re.IGNORECASE)
_LOCAL_URL_ID = re.compile(rf"url\(\s*[\"']?({_ID})[\"']?\s*\)", re.IGNORECASE)
_ANY_URL = re.compile(r"url\s*\(", re.IGNORECASE)
_URL_STRING_FUNC = re.compile(r"(?:image-set|image|src)\s*\(", re.IGNORECASE)
_IMPORT = re.compile(r"@import", re.IGNORECASE)
_AT_KEYWORD = re.compile(r"@([\w-]*)")

# The at-rules a <style> may hold, lowercase. Each holds rules or declarations in the
# grammar step 4 already cuts, and loads nothing. Any other drops the element: an at-rule
# one does not know has a grammar of its own, so which of its parts is a declaration
# cannot be told, and several load or name a URL (`@font-face` src, `@namespace`,
# `@counter-style` symbols, `@document`).
HARMLESS_AT_RULES: frozenset[str] = frozenset(
    {"media", "supports", "keyframes", "-webkit-keyframes", "layer"}
)
# Every pattern here runs on model-written text on the frame thread, so none may
# backtrack super-linearly: no two unbounded quantifiers over overlapping classes may
# be adjacent or nested. Where whitespace has to be split off around a token, that is
# done with str methods (`_split_important`, `_number`), not with `\s*` either side of
# a lazy or optional group: three such patterns took 8-26 s on a 100,000-space run.
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_UNIT_PX = {"px": 1.0, "pt": 4 / 3, "pc": 16.0, "in": 96.0, "cm": 96 / 2.54, "mm": 96 / 25.4}
_FONT_PROPS = ("font-family", "font")


@dataclass(frozen=True, slots=True)
class Sanitised:
    """
    SVG that passed the policy, and the natural size the host lays it out at.

    `width` and `height` are in CSS px and are also what the root's attributes now
    say. The host's height for a drawn width `w` is `min(w, width) * height / width`.
    `notes` says what the policy changed that the author may not expect: one line per
    distinct `var()` that could not be resolved or resolved to a paint that is not one
    (step 9), then one per distinct unsafe or dangling `fill` or `stroke` that was
    repainted (steps 3 and 8), each list capped at `MAX_NOTES`.
    Nothing displays them yet: their consumer is the figure-preview tool scoped in the
    board task fence-lessons, which would hand them to the session that wrote the
    figure.
    """

    svg: str
    width: float
    height: float
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Refused:
    """
    Input the policy will not pass, with the one-line reason the host shows.
    """

    reason: str


Outcome = Sanitised | Refused


class _Refusal(Exception):
    """
    Raised inside this module only, and turned into `Refused` by `sanitise`.
    """


def sanitise(svg: str) -> Outcome:
    """
    Apply the policy to `svg`. Total: any input, of any type, gives an `Outcome`.
    """
    if not isinstance(svg, str):
        return Refused(f"not text: {type(svg).__name__}")
    try:
        return _sanitise(svg)
    except _Refusal as refusal:
        return Refused(str(refusal))
    except Exception as exc:  # the never-raises property; tests hold this arm unreached
        return Refused(f"internal error: {type(exc).__name__}")


def _sanitise(svg: str) -> Sanitised:
    if len(svg) > MAX_INPUT_BYTES or len(svg.encode("utf-8", "surrogatepass")) > MAX_INPUT_BYTES:
        raise _Refusal(f"larger than {MAX_INPUT_BYTES} bytes")
    _reject_declarations(svg)
    try:
        root = ET.fromstring(svg)
    except (ET.ParseError, ValueError) as exc:
        raise _Refusal(f"not well-formed XML: {exc}") from None
    if _local(root.tag) != "svg" or _ns(root.tag) not in ("", SVG_NS):
        raise _Refusal("the root element is not <svg>")
    _check_shape(root)
    notes = _substitute_custom_properties(root, len(svg))
    repainted: dict[str, None] = {}
    out = _clean(root, repainted)
    if out is None:
        raise _Refusal("the root element is not <svg>")
    _resolve_references(out, repainted)
    notes += _capped(repainted, "and more paint that was repainted")
    width, height = _natural_size(out)
    out.set("width", _fmt(width))
    out.set("height", _fmt(height))
    count = _expanded_size(out)
    if count > MAX_ELEMENTS:
        raise _Refusal(f"{count} elements after <use> expansion, over {MAX_ELEMENTS}")
    # The tree is built with unqualified names and the namespace declared once here:
    # ElementTree's default_namespace option refuses unqualified attribute names.
    out.set("xmlns", SVG_NS)
    text = ET.tostring(out, encoding="unicode")
    return Sanitised(svg=text, width=width, height=height, notes=notes)


# Step 1 -------------------------------------------------------------------------------


def _reject_declarations(text: str) -> None:
    for match in re.finditer("<!", text):
        rest = text[match.end() : match.end() + 7]
        if rest.startswith("--") or rest.startswith("[CDATA["):
            continue
        word = rest.upper()
        if word.startswith("DOCTYPE"):
            raise _Refusal("a DOCTYPE is not accepted")
        if word.startswith("ENTITY"):
            raise _Refusal("an ENTITY declaration is not accepted")
        raise _Refusal("a markup declaration (<!...) is not accepted")


# Steps 2 and 3 ------------------------------------------------------------------------


def _ns(tag: object) -> str:
    if isinstance(tag, str) and tag.startswith("{"):
        return tag[1:].partition("}")[0]
    return ""


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rpartition("}")[2] if tag.startswith("{") else tag


def _is_svg(tag: object) -> bool:
    return _ns(tag) in ("", SVG_NS)


def _check_shape(root: ET.Element) -> None:
    stack: list[tuple[ET.Element, int]] = [(root, 1)]
    count = 0
    while stack:
        element, depth = stack.pop()
        count += 1
        if count > MAX_INPUT_ELEMENTS:
            raise _Refusal(f"more than {MAX_INPUT_ELEMENTS} elements")
        if depth > MAX_DEPTH:
            raise _Refusal(f"nested deeper than {MAX_DEPTH}")
        stack.extend((child, depth + 1) for child in element)


def _clean(src: ET.Element, notes: dict[str, None]) -> ET.Element | None:
    """
    `src` and its subtree with steps 2 to 4 applied, or None if it is dropped. A line
    for each repainted fill or stroke goes into `notes`.
    """
    local = _local(src.tag)
    if not _is_svg(src.tag):
        return None
    if local == "foreignObject":
        return _rewrite_foreign_object(src, notes)
    if local not in ALLOWED_ELEMENTS:
        return None
    out = ET.Element(local, _clean_attributes(src.attrib, notes))
    if local in _TEXT_ONLY:
        text = "".join(src.itertext())
        if local == "style":
            cleaned = _clean_stylesheet(text, notes)
            if cleaned is None:
                return None
            text = cleaned
        out.text = text or None
        return out
    # A dropped element's tail is still its parent's content (text runs), so it joins
    # the text before it. Collected and joined once: a megabyte of `<x/>a` is
    # 200,000 tails, and appending each to a string is quadratic.
    last: ET.Element | None = None
    runs: list[str] = [src.text] if src.text else []
    for child in src:
        kept = _clean(child, notes)
        if kept is None:
            if child.tail:
                runs.append(child.tail)
            continue
        _set_run(out, last, runs)
        runs = [child.tail] if child.tail else []
        out.append(kept)
        last = kept
    _set_run(out, last, runs)
    return out


def _set_run(parent: ET.Element, last: ET.Element | None, runs: list[str]) -> None:
    text = "".join(runs) or None
    if last is None:
        parent.text = text
    else:
        last.tail = text


def _clean_attributes(attrib: dict[str, str], notes: dict[str, None]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in attrib.items():
        if name.startswith("{"):
            ns, _, local = name[1:].partition("}")
            if ns == XLINK_NS and local == "href":
                # A plain href that is kept wins over this one in either order, as
                # in SVG 2. One that is dropped (not a `#id`) does not: this one is
                # then kept, where SVG 2 would use the plain one and draw nothing.
                # That differs in fidelity only, since both are held to `#id`.
                if "href" in out:
                    continue
                key = "href"
            elif ns == XML_NS and local == "space":
                key = name
            else:
                continue
        else:
            key = name
        lowered = key.lower()
        if lowered.startswith("on"):
            continue
        if lowered == "style":
            # Declaration by declaration, so one bad declaration does not cost the rest.
            value = _clean_declarations(value, notes)
            if not value:
                continue
        elif lowered == "href":
            fragment = value.strip()
            if key != "href" or not _FRAGMENT.fullmatch(fragment):
                continue
            value = fragment
        elif lowered == "filter":
            continue
        elif _css_unsafe(value):
            if lowered not in _PAINT:
                continue
            value = _repaint(key, value, notes)
        elif lowered in ("font-family", "font"):
            value = _fix_font_value(lowered, value)
        elif lowered in _COLOUR_PROPS:
            value = _spell_colours(value)
        out[key] = value
    return out


def _rewrite_foreign_object(fo: ET.Element, notes: dict[str, None]) -> ET.Element | None:
    """
    A foreignObject becomes centred text where it stood: its XHTML's text, one line per
    block element or `<br>`, at the box's centre. Everything else inside it is gone.
    """
    lines = _foreign_lines(fo)
    if not lines:
        return None
    x = _number(fo.get("x")) or 0.0
    y = _number(fo.get("y")) or 0.0
    w = _number(fo.get("width")) or 0.0
    h = _number(fo.get("height")) or 0.0
    cx, cy = _fmt(x + w / 2), _fmt(y + h / 2)
    kept = {k: v for k, v in fo.attrib.items() if k in ("class", "style", "transform")}
    attrs = _clean_attributes(kept, notes)
    attrs.update({"x": cx, "y": cy, "text-anchor": "middle", "dominant-baseline": "central"})
    text = ET.Element("text", attrs)
    if len(lines) == 1:
        text.text = lines[0]
        return text
    first = -(len(lines) - 1) * _LINE_HEIGHT_EM / 2
    for index, line in enumerate(lines):
        dy = first if index == 0 else _LINE_HEIGHT_EM
        span = ET.SubElement(text, "tspan", {"x": cx, "dy": f"{_fmt(dy)}em"})
        span.text = line
    return text


def _foreign_lines(fo: ET.Element) -> list[str]:
    lines: list[list[str]] = [[]]

    def visit(element: ET.Element) -> None:
        for child in element:
            block = _local(child.tag) in _BLOCK
            if block:
                lines.append([])
            if child.text:
                lines[-1].append(child.text)
            visit(child)
            if block:
                lines.append([])
            if child.tail:
                lines[-1].append(child.tail)

    if fo.text:
        lines[0].append(fo.text)
    visit(fo)
    joined = (" ".join("".join(parts).split()) for parts in lines)
    return [line for line in joined if line]


# Step 9 -------------------------------------------------------------------------------

_VAR = re.compile(r"var\(", re.IGNORECASE)
_VAR_ARGS = re.compile(r"[(),]")
_CUSTOM_NAME = re.compile(r"--[\w-]+")


class _Unresolved(Exception):
    """
    A `var()` that cannot be resolved; it fails the attribute or declaration holding it.
    """


@dataclass(slots=True)
class _Vars:
    """
    The custom-property table and what substitution may still spend.

    `budget` is the characters of declared values substitution may still scan; each
    expansion of a name charges its declared value's length. Every character one
    resolution produces comes from text it scanned, so this bounds the work and the
    length of each distinct resolved value, however the declarations refer to one
    another: ten names each naming the previous one ten times would be 10^10
    characters, and is refused at the budget instead. `resolved` holds each distinct
    value's outcome, since a stylesheet repeats one `var()` in every rule; a repeat is
    a lookup and is not charged to `budget`. A fallback is not charged either, because
    it is part of text already scanned or charged, and `_Parens` locates every `var()`
    in a text by lookup, so a fallback is searched again only once per enclosing level,
    which `MAX_VAR_DEPTH` bounds.

    `size` is the document's length as substituted so far, and bounds the output. A
    repeat scans nothing but is copied into the output each time it is used, so every
    substitution, repeats included, is charged to `size` (`grow`) before the text
    holding it is built, and the document is refused the moment `size` would pass
    `MAX_INPUT_BYTES`: a 100 KB value used 60,000 times in one `<style>` is refused
    after about ten uses rather than copied 60,000 times. `coloured` memoises what a
    resolved value becomes in a colour property, so a repeat is not copied to check it.
    """

    declared: dict[str, str]
    budget: int
    size: int
    notes: dict[str, None]
    parsed: dict[str, _Parens]
    resolved: dict[str, str | _Unresolved]
    coloured: dict[tuple[str, str], str | None]
    splits: dict[tuple[str, str], list[tuple[str, str]]]

    def note(self, line: str) -> None:
        # Unbounded here, since MAX_VAR_VALUES bounds how many lines there can be.
        self.notes[line] = None

    def grow(self, by: int) -> None:
        self.size += by
        if self.size > MAX_INPUT_BYTES:
            raise _Refusal(f"var() substitution makes it larger than {MAX_INPUT_BYTES} bytes")

    def lines(self) -> tuple[str, ...]:
        return _capped(self.notes, "and more var() that could not be resolved")


def _capped(notes: dict[str, None], more: str) -> tuple[str, ...]:
    """
    The first `MAX_NOTES` of `notes`, and `more` if there were others: the host shows
    them, and a document can fail in every declaration.
    """
    if len(notes) <= MAX_NOTES:
        return tuple(notes)
    return (*list(notes)[:MAX_NOTES], more)


def _substitute_custom_properties(root: ET.Element, size: int) -> tuple[str, ...]:
    """
    Step 9 on the parsed input, in place. `size` is the input's length: the
    substituted document may not be longer than `MAX_INPUT_BYTES`. Returns the notes.
    """
    env = _Vars({}, MAX_INPUT_BYTES, size, {}, {}, {}, {}, {})
    _custom_properties(root, env)
    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if name.lower() == "style":
                if not _has_custom(value):
                    continue
                new: str | None = _substitute_css(value, env, stylesheet=False)
            elif "var(" in value.lower():
                new = _substitute_value(name, value, env)
                env.grow(len(new or "") - len(value))
            else:
                continue
            if new is None:
                del element.attrib[name]
            else:
                element.set(name, new)
        if _local(element.tag) == "style" and _is_svg(element.tag):
            text = "".join(element.itertext())
            if _has_custom(text):
                for child in list(element):
                    element.remove(child)
                element.text = _substitute_css(text, env, stylesheet=True)
    return env.lines()


def _has_custom(css: str) -> bool:
    return "--" in css or "var(" in css.lower()


def _custom_properties(root: ET.Element, env: _Vars) -> None:
    """
    Every `--name: value` declared in the document into `env.declared`, the last
    declaration of a name winning, with `!important` removed. Values are kept raw:
    they are checked once substituted, where step 3 and 4's rules apply to the whole of
    the result. Each text split is kept in `env.splits` for the substitution pass.
    """
    declared = env.declared

    def collect(css: str, delimiters: str) -> None:
        for piece, delimiter in _split(css, delimiters, env):
            if delimiter == "{":
                continue
            name, colon, value = piece.partition(":")
            name = name.strip()
            if colon and name.startswith("--"):
                declared[name] = _split_important(value)[0].strip()

    for element in root.iter():
        for name, value in element.attrib.items():
            if name.lower() == "style" and "--" in value:
                collect(value, ";")
        if _local(element.tag) == "style" and _is_svg(element.tag):
            text = "".join(element.itertext())
            if "--" in text:
                collect(text, ";{}")


def _split(css: str, delimiters: str, env: _Vars) -> list[tuple[str, str]]:
    """
    `_css_split` once per text: step 9 reads each text twice, to collect and to
    substitute, and a stylesheet is up to a megabyte.
    """
    pieces = env.splits.get((css, delimiters))
    if pieces is None:
        pieces = env.splits[css, delimiters] = list(_css_split(css, delimiters))
    return pieces


def _substitute_css(text: str, env: _Vars, *, stylesheet: bool) -> str | None:
    """
    A `style=` value or a stylesheet with its custom-property declarations removed and
    each `var()` substituted. None for a `style=` with nothing left. A stylesheet with a
    `var()` in a selector is emptied whole, since a rule cannot lose its selector.
    Each piece's change in length is charged to `env.size` before the piece is built,
    so an output past the cap is refused and never assembled.
    """
    pieces: list[str] = []
    grown = 0

    def grow(by: int) -> None:
        nonlocal grown
        env.grow(by)
        grown += by

    for piece, delimiter in _split(text, ";{}" if stylesheet else ";", env):
        if "var(" in piece.lower() and delimiter == "{":
            env.note(f"a var() in the selector {piece.strip()[:40]!r}; the <style> is emptied")
            env.grow(-grown - len(text))
            return ""
        prop, colon, value = piece.partition(":")
        if colon and prop.strip().startswith("--"):
            grow(-len(piece))
            piece = ""
        elif "var(" in piece.lower():
            if not colon:
                env.note(f"a var() outside a declaration: {piece.strip()[:40]!r}; dropped")
                grow(-len(piece))
                piece = ""
            else:
                resolved = _substitute_value(prop, value, env)
                if resolved is None:
                    grow(-len(piece))
                    piece = ""
                else:
                    grow(len(resolved) - len(value))
                    piece = f"{prop}:{resolved}"
        if piece or delimiter != ";":
            pieces.append(piece + delimiter)
        else:
            grow(-len(delimiter))
    css = "".join(pieces)
    if stylesheet or css.strip(" \t\r\n;"):
        return css
    env.grow(-len(css))
    return None


def _substitute_value(prop: str, value: str, env: _Vars) -> str | None:
    """
    `value` with every `var()` substituted. A failed one is what `_IN_PLACE_OF` says
    for the property: `none` in a paint, `transparent` in `stop-color`, and None
    (remove it) in any other. A resolved text that still spells `var(` has failed: one
    substitution can join the text around it into a new `var()` (`var(--x, va)r(--c)`
    with `--x` undeclared is `var(--c)`), and substitution does not rescan its output.
    """
    outcome = env.resolved.get(value)
    if outcome is None:
        if len(env.resolved) >= MAX_VAR_VALUES:
            raise _Refusal(f"more than {MAX_VAR_VALUES} distinct values that use var()")
        try:
            resolved = _substitute(_parens(value), 0, len(value), env, (), 0)
            if not resolved.strip():
                raise _Unresolved("resolves to nothing")
            if _VAR.search(resolved):
                raise _Unresolved("resolves to text that is itself a var()")
            if _css_unsafe(resolved):
                raise _Unresolved("resolves to an escape, a URL or an @import")
            outcome = env.resolved[value] = resolved
        except _Unresolved as why:
            outcome = env.resolved[value] = why
    name = prop.strip().lower()
    if isinstance(outcome, str):
        if name in _COLOUR_TAKING:
            return _substituted_colour(prop, outcome, env)
        return outcome
    instead = _IN_PLACE_OF.get(name)
    env.note(f"{prop.strip()}: {outcome}; {_put_instead(name, instead)}")
    return instead


def _put_instead(name: str, instead: str | None) -> str:
    if instead is None:
        return "removed"
    return f"painted {instead}" if name in _PAINT else f"set to {instead}"


# The properties step 9 holds to a colour, and what a failed one becomes. A removed
# fill, stroke or stop-color is black (its initial value), so those take a value that
# paints nothing; `stop-color: none` is black too, and `transparent` is not (measured
# on resvg_py 0.5.0, 2026-10-21). A removed `color` inherits its parent's, which is
# what CSS does with an invalid one. `flood-color` and `lighting-color` take effect
# only in a filter, and step 2 drops every filter element.
_IN_PLACE_OF: dict[str, str | None] = {
    "fill": "none",
    "stroke": "none",
    "stop-color": "transparent",
}
_COLOUR_TAKING = frozenset(
    {"fill", "stroke", "stop-color", "flood-color", "lighting-color", "color"}
)


def _substituted_colour(prop: str, value: str, env: _Vars) -> str | None:
    """
    A resolved value of a `_COLOUR_TAKING` property held to what resvg paints, since
    the substitution is this module's output and a non-colour paints black: a colour
    (`_colour_or_none`), `inherit` (measured to inherit), or, in `fill` and `stroke`, a
    `url(#id)` paint (step 8 resolves it). Anything else is `_IN_PLACE_OF` for the
    property, with a note. Memoised in `env.coloured`, because a stylesheet repeats a
    value and checking it copies it.
    """
    name = prop.strip().lower()
    key = (name, value)
    if key in env.coloured:
        return env.coloured[key]
    body, important = _split_important(value)
    stripped = body.strip()
    out: str | None = value
    paint_url = name in _PAINT and _LOCAL_URL.match(stripped)
    if not (paint_url or stripped.lower() == "inherit"):
        colour = _colour_or_none(stripped)
        lead = body[: len(body) - len(body.lstrip())]
        if colour == "none" and (stripped != "none" or name not in _PAINT):
            instead = _IN_PLACE_OF.get(name)
            env.note(
                f"{prop.strip()}: var() resolves to {stripped[:40]!r}, not a colour;"
                f" {_put_instead(name, instead)}"
            )
            out = None if instead is None else f"{lead}{instead}{important}"
        elif colour != stripped:
            out = f"{lead}{colour}{important}"
    env.coloured[key] = out
    return out


@dataclass(frozen=True, slots=True)
class _Parens:
    """
    A text and, for each `(` in it, the index of the `)` that closes it and of the
    first comma at its own level. Found in one pass, so a `var()` and its fallback
    are located by lookup and the text is never rescanned for them.
    """

    text: str
    close: dict[int, int]
    comma: dict[int, int]


def _parens(text: str) -> _Parens:
    close: dict[int, int] = {}
    comma: dict[int, int] = {}
    if text.count("(") == 1:
        # The common case, `fill: var(--c)`: str methods, without a Python loop.
        at = text.find("(")
        end = text.find(")", at)
        if end >= 0:
            close[at] = end
            first = text.find(",", at, end)
            if first >= 0:
                comma[at] = first
        return _Parens(text, close, comma)
    open_: list[int] = []
    for token in _VAR_ARGS.finditer(text):
        at = token.start()
        c = text[at]
        if c == "(":
            open_.append(at)
        elif not open_:
            continue
        elif c == ")":
            close[open_.pop()] = at
        elif open_[-1] not in comma:
            comma[open_[-1]] = at
    return _Parens(text, close, comma)


def _substitute(
    src: _Parens, lo: int, hi: int, env: _Vars, open_: tuple[str, ...], depth: int
) -> str:
    """
    `src.text[lo:hi]` with every `var()` in it replaced, recursively. `open_` holds the
    names being expanded around this text, to find a cycle.
    """
    text = src.text
    out: list[str] = []
    i = lo
    while (match := _VAR.search(text, i, hi)) is not None:
        paren = match.end() - 1
        close = src.close.get(paren)
        if close is None or close >= hi:
            raise _Unresolved("a var() is not closed")
        comma = src.comma.get(paren, close)
        name = text[match.end() : comma].strip()
        fallback = (comma + 1, close) if comma < close else None
        out.append(text[i : match.start()])
        out.append(_expand(name, src, fallback, env, open_, depth + 1))
        i = close + 1
    out.append(text[i:hi])
    return "".join(out)


def _expand(
    name: str,
    src: _Parens,
    fallback: tuple[int, int] | None,
    env: _Vars,
    open_: tuple[str, ...],
    depth: int,
) -> str:
    if depth > MAX_VAR_DEPTH:
        raise _Unresolved(f"var() nested deeper than {MAX_VAR_DEPTH}")
    if not _CUSTOM_NAME.fullmatch(name):
        raise _Unresolved(f"var({name[:40]}) does not name a custom property")
    if name in open_:
        raise _Unresolved(f"var({name}) is part of a cycle")
    value = env.declared.get(name)
    if value is None:
        if fallback is None:
            raise _Unresolved(f"var({name}) is not declared")
        return _substitute(src, *fallback, env, open_, depth).strip()
    env.budget -= len(value)
    if env.budget < 0:
        raise _Refusal(f"var() substitution scans more than {MAX_INPUT_BYTES} characters")
    parsed = env.parsed.get(name)
    if parsed is None:
        parsed = env.parsed[name] = _parens(value)
    return _substitute(parsed, 0, len(value), env, (*open_, name), depth)


# Step 8 -------------------------------------------------------------------------------

_PAINT = frozenset({"fill", "stroke"})
_PAINT_SERVERS = frozenset({"linearGradient", "radialGradient"})
_MARKERS = frozenset({"marker"})

# What a `url(#id)` in each property may name. A property absent here needs only an
# element with the id. Measured on resvg_py 0.5.0 (2026-10-20): a filter, clip-path or
# mask naming a <rect> erases the shape, as a dangling one does; fill or stroke naming
# one paints nothing, ignoring a fallback; a marker naming one draws no marker.
# `filter` is not here because the property never survives step 3.
_TARGET_KINDS: dict[str, frozenset[str]] = {
    "fill": _PAINT_SERVERS,
    "stroke": _PAINT_SERVERS,
    "clip-path": frozenset({"clipPath"}),
    "mask": frozenset({"mask"}),
    "marker": _MARKERS,
    "marker-start": _MARKERS,
    "marker-mid": _MARKERS,
    "marker-end": _MARKERS,
}

# The kinds of element that carry each id: one id may be on several elements.
_Kinds = dict[str, set[str]]


def _resolve_references(root: ET.Element, notes: dict[str, None]) -> None:
    """
    Step 8 on the cleaned tree, in place. A line for each repainted fill or stroke goes
    into `notes`.
    """
    kinds: _Kinds = {}
    for element in root.iter():
        ident = element.get("id")
        if ident is not None:
            kinds.setdefault(ident, set()).add(_local(element.tag))
    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if name == "href":
                resolved: str | None = value if value[1:] in kinds else None
            elif name == "style":
                resolved = _resolve_declarations(value, kinds, "; ", notes) or None
            else:
                resolved = _resolve_value(name, value, kinds, notes)
            if resolved is None:
                del element.attrib[name]
            else:
                element.set(name, resolved)
        if element.tag == "style" and element.text:
            element.text = _resolve_declarations(element.text, kinds, None, notes)


def _resolves(prop: str, target: str, kinds: _Kinds) -> bool:
    """
    Whether `url(#target)` in property `prop` names an element the property can use.
    Where the id is on several elements, every one of them must be such an element.
    """
    have = kinds.get(target)
    if have is None:
        return False
    want = _TARGET_KINDS.get(prop)
    return want is None or have <= want


def _resolve_value(prop: str, value: str, kinds: _Kinds, notes: dict[str, None]) -> str | None:
    """
    `value` with dangling fragment references resolved for property `prop`, or None
    when the property should be removed. A reference is dangling when its target is
    missing or is not a kind of element `prop` can use (`_TARGET_KINDS`). A repainted
    fill or stroke puts a line in `notes`.
    """
    name = prop.strip()
    prop = name.lower()
    targets = [m.group(1)[1:] for m in _LOCAL_URL_ID.finditer(value)]
    dangling = next((t for t in targets if not _resolves(prop, t, kinds)), None)
    if dangling is None:
        return value
    if prop not in _PAINT:
        return None
    body, important = _split_important(value)
    colour = _colour_or_none(_LOCAL_URL.sub("", body))
    why = "names nothing in the output" if dangling not in kinds else "is not a paint server"
    notes[f"{name}: url(#{dangling[:40]}) {why}; painted {colour[:40]}"] = None
    return f"{colour}{important}"


# A paint's fallback colour ----------------------------------------------------------

# The CSS named colours. Matched case-insensitively, as CSS and resvg both do.
NAMED_COLOURS: frozenset[str] = frozenset("""
    aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
    blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk
    crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki
    darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
    darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue
    dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
    ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo
    ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
    lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen
    lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen
    linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple
    mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred
    midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
    orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip
    peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
    saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
    slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
    wheat white whitesmoke yellow yellowgreen
    """.split())

# Names written out as their value because resvg_py 0.5.0 does not know them and paints
# them black (measured 2026-10-21; the other 147 paint their colour). Spelled in every
# value of `_COLOUR_PROPS` (`_spell_colours`), not only in a fallback this module keeps.
_COLOUR_SPELLED = {"rebeccapurple": "#663399"}
_SPELLED_NAME = re.compile(
    r"(?<![\w-])(" + "|".join(_COLOUR_SPELLED) + r")(?![\w-])", re.IGNORECASE
)

# The properties whose value can hold a colour. `color` is here because it is what
# `currentColor` paints: `color="rebeccapurple"` under `fill="currentColor"` paints black
# too (measured 2026-10-21). `flood-color` has no effect while `filter` is dropped.
_COLOUR_PROPS = frozenset({"fill", "stroke", "stop-color", "flood-color", "color"})


def _spell_colours(value: str) -> str:
    return _SPELLED_NAME.sub(lambda m: _COLOUR_SPELLED[m.group(1).lower()], value)


def _fix_colour_declaration(declaration: str) -> str:
    """
    A declaration of one of `_COLOUR_PROPS` with its names spelled; any other as it is.
    """
    if _SPELLED_NAME.search(declaration) is None:
        return declaration
    name, colon, value = declaration.partition(":")
    if not colon or name.strip().lower() not in _COLOUR_PROPS:
        return declaration
    return f"{name}{colon}{_spell_colours(value)}"


_HEX_COLOUR = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")
_COLOUR_FUNCTION = re.compile(r"(rgba?|hsla?)\(", re.IGNORECASE)


def _colour_or_none(text: str) -> str:
    """
    `text` stripped if it is a colour resvg paints as written, else `none`: an invalid
    paint is black. Accepted: a named colour, `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`,
    `currentColor`, `none`, and `rgb()`/`rgba()` of three numbers or three percentages
    and `hsl()`/`hsla()` of a number and two percentages, each with an optional numeric
    alpha, separated by commas or by whitespace. This is narrower than CSS where resvg
    is: it paints `NONE`, `hsl(120deg, ...)`, a percentage alpha and `rgb(1 2 3 / 0.5)`
    black, so those are `none` here. A number may be signed or have an exponent:
    resvg paints `rgb(1e2,0,0)` as (100,0,0) and `rgb(+5,-1,300)` clamped to (5,0,255)
    (measured 2026-10-21). `currentColor` is kept although it paints black where no
    `color` is set, since that is CSS's own initial value and not a value this module
    wrote.
    """
    colour = text.strip()
    lowered = colour.lower()
    if lowered in NAMED_COLOURS:
        return _COLOUR_SPELLED.get(lowered, colour)
    if colour == "none" or lowered == "currentcolor" or _HEX_COLOUR.fullmatch(colour):
        return colour
    function = _COLOUR_FUNCTION.match(colour)
    if function is None or not colour.endswith(")"):
        return "none"
    inner = colour[function.end() : -1]
    args = [a.strip() for a in inner.split(",")] if "," in inner else inner.split()
    if len(args) not in (3, 4):
        return "none"
    alpha_ok = len(args) == 3 or _is_number(args[3])
    if function.group(1).lower().startswith("rgb"):
        channels_ok = all(map(_is_number, args[:3])) or all(map(_is_percentage, args[:3]))
    else:
        channels_ok = _is_number(args[0]) and _is_percentage(args[1]) and _is_percentage(args[2])
    return colour if alpha_ok and channels_ok else "none"


def _is_number(text: str) -> bool:
    return _NUMBER.fullmatch(text) is not None


def _is_percentage(text: str) -> bool:
    return text.endswith("%") and _is_number(text[:-1])


def _resolve_declarations(
    text: str, kinds: _Kinds, joiner: str | None, notes: dict[str, None]
) -> str:
    """
    CSS with each declaration's dangling references resolved. With a `joiner`, `text`
    is a declaration list (`style=`) and the kept declarations are joined by it;
    without one it is a stylesheet and its delimiters are kept in place.
    """
    pieces: list[str] = []
    for piece, delimiter in _css_split(text, ";" if joiner else ";{}"):
        prop, colon, value = piece.partition(":")
        if colon and _LOCAL_URL.search(value):
            resolved = _resolve_value(prop, value, kinds, notes)
            if resolved is None:
                piece = ""
            elif resolved is not value:
                piece = f"{prop}{colon}{resolved if resolved[:1].isspace() else ' ' + resolved}"
        if joiner is None:
            pieces.append(piece + delimiter)
        elif piece.strip():
            pieces.append(piece.strip())
    return (joiner or "").join(pieces)


# Step 4 -------------------------------------------------------------------------------


def _css_unsafe(text: str) -> bool:
    """
    True if `text` holds anything that can make a CSS or presentation-attribute parser
    reach outside the document: an escape, `@import`, a non-fragment `url(`, or a
    function that takes a URL as a string.
    """
    if "\\" in text:
        return True
    if "(" not in text and "@" not in text:
        return False  # each pattern below needs one; step 4 asks this of every piece
    if _IMPORT.search(text) or _URL_STRING_FUNC.search(text):
        return True
    return _ANY_URL.search(_LOCAL_URL.sub("", text)) is not None


_CSS_SPECIAL: dict[str, re.Pattern[str]] = {}


def _css_split(text: str, delimiters: str) -> Iterator[tuple[str, str]]:
    """
    Split CSS at top-level `delimiters`, outside strings, with comments removed (a
    comment separates tokens, so it becomes a space). Yields `(piece, delimiter)`;
    the last delimiter is "". A backslash is not interpreted: a piece holding one is
    dropped by the caller, so where an escaped quote ends a string does not matter.
    """
    # It jumps from one character that matters to the next, rather than stepping a
    # character at a time: this runs over up to a megabyte on the frame thread.
    special = _CSS_SPECIAL.get(delimiters)
    if special is None:
        special = re.compile("[" + re.escape("\"'/" + delimiters) + "]")
        _CSS_SPECIAL[delimiters] = special
    piece: list[str] = []
    i, n = 0, len(text)
    while (match := special.search(text, i)) is not None:
        at = match.start()
        c = text[at]
        if c in "\"'":
            end = text.find(c, at + 1)
            if end < 0:
                break  # an unclosed string runs to the end of the text
            piece.append(text[i : end + 1])
            i = end + 1
        elif c == "/":
            if not text.startswith("/*", at):
                piece.append(text[i : at + 1])
                i = at + 1
                continue
            piece.append(text[i:at] + " ")
            end = text.find("*/", at + 2)
            i = n if end < 0 else end + 2
        else:
            piece.append(text[i:at])
            yield "".join(piece), c
            piece = []
            i = at + 1
    piece.append(text[i:])
    yield "".join(piece), ""


def _clean_declarations(text: str, notes: dict[str, None]) -> str:
    kept = []
    for declaration, _ in _css_split(text, ";"):
        if _is_filter(declaration) or not declaration.strip():
            continue
        if _css_unsafe(declaration):
            prop, colon, value = declaration.partition(":")
            prop = prop.strip()
            if not colon or prop.lower() not in _PAINT or _css_unsafe(prop):
                continue
            declaration = f"{prop}:{_repaint(prop, value, notes)}"
        kept.append(_fix_colour_declaration(_fix_font_declaration(declaration)).strip())
    return "; ".join(kept)


_LEADING_URL = re.compile(r"\s*url\s*\(\s*", re.IGNORECASE)


def _repaint(prop: str, value: str, notes: dict[str, None]) -> str:
    """
    What an unsafe `fill` or `stroke` value becomes in place of being removed, which
    would paint it black: the colour after a leading `url()` when there is one and it
    is a colour (`_colour_or_none`), else `none`. A trailing `!important` is kept. A
    line goes into `notes`.
    """
    body, important = _split_important(value)
    rest = _after_leading_url(body)
    fallback = _colour_or_none(rest) if rest is not None and "\\" not in body else "none"
    notes[f"{prop}: {_unsafe_reason(body)}; painted {fallback[:40]}"] = None
    return f"{fallback}{important}"


def _after_leading_url(text: str) -> str | None:
    """
    `text` after a `url(...)` that opens it, or None if it does not open with one that
    closes. A quoted URL ends at its closing quote; an unquoted one at the first `)`.
    """
    match = _LEADING_URL.match(text)
    if match is None:
        return None
    at = match.end()
    if text[at : at + 1] in ("'", '"'):
        end = text.find(text[at], at + 1)
        if end < 0:
            return None
        close = text.find(")", end + 1)
        if close < 0 or text[end + 1 : close].strip():
            return None
    else:
        close = text.find(")", at)
        if close < 0:
            return None
    return text[close + 1 :]


def _unsafe_reason(text: str) -> str:
    """
    Why `_css_unsafe(text)` holds, in the words of a note.
    """
    if "\\" in text:
        return "a backslash escape"
    if _IMPORT.search(text):
        return "an @import"
    if _URL_STRING_FUNC.search(text):
        return "a function that loads a URL"
    return "a url() outside the document"


def _clean_stylesheet(text: str, notes: dict[str, None]) -> str | None:
    """
    A stylesheet with each unsafe declaration dropped or repainted as in `style=`, and
    each rule whose selector is unsafe dropped with its block; None to drop the
    element. Every at-rule must be one of `HARMLESS_AT_RULES`, found textually, so an
    `@` in a string or an escaped at-keyword (`@\\69mport`) drops it too.
    """
    if _IMPORT.search(text):
        return None
    for match in _AT_KEYWORD.finditer(text):
        if match.group(1).lower() not in HARMLESS_AT_RULES:
            return None
    pieces: list[str] = []
    skipping = 0  # open braces inside a rule being dropped
    for piece, delimiter in _css_split(text, ";{}"):
        if skipping:
            skipping += (delimiter == "{") - (delimiter == "}")
            continue
        if delimiter == "{":
            if _css_unsafe(piece):
                skipping = 1
                continue
        elif _css_unsafe(piece) or _is_filter(piece):
            piece = _clean_declarations(piece, notes)
        else:
            piece = _fix_colour_declaration(_fix_font_declaration(piece))
        pieces.append(piece + delimiter)
    css = "".join(pieces)
    # Each kept piece passed `_css_unsafe` and the delimiters between them cannot
    # complete a token, so this holds by construction; it is checked because the
    # consequence of being wrong is a fetch.
    return None if _css_unsafe(css) else css


def _is_filter(declaration: str) -> bool:
    name, colon, _ = declaration.partition(":")
    return bool(colon) and name.strip().lower() == "filter"


# Step 7 -------------------------------------------------------------------------------


def _fix_font_declaration(declaration: str) -> str:
    """
    A `font-family` or `font` declaration with its family list fixed (step 7); any
    other declaration as it is.
    """
    lead = declaration[: len(declaration) - len(declaration.lstrip())]
    name, colon, value = declaration[len(lead) :].partition(":")
    prop = name.rstrip()
    if not colon or prop.lower() not in _FONT_PROPS:
        return declaration
    body, important = _split_important(value)
    fixed = _fix_font_value(prop.lower(), body)
    return f"{lead}{name}{colon}{fixed}{important}"


def _split_important(value: str) -> tuple[str, str]:
    """
    `value` split before a trailing `!important` and the whitespace around it, as
    `(body, "!important" part)`; the second is "" when there is none.
    """
    rest = value.rstrip()
    if rest[-9:].lower() != "important":
        return value, ""
    bang = rest[:-9].rstrip()
    if not bang.endswith("!"):
        return value, ""
    end = len(bang[:-1].rstrip())
    return value[:end], value[end:]


def _fix_font_value(prop: str, value: str) -> str:
    stripped = value.strip()
    if stripped.lower() in _CSS_WIDE:
        return value
    if prop == "font":
        if stripped.lower() in _SYSTEM_FONTS:
            return value
        words = (w for family, _ in _css_split(stripped, ",") for w in family.split())
    else:
        words = (family.strip() for family, _ in _css_split(stripped, ","))
    if any(word.lower() in MAPPED_GENERICS for word in words):
        return value
    if not stripped:
        return FALLBACK_FAMILY
    lead = value[: len(value) - len(value.lstrip())]
    return f"{lead}{stripped}, {FALLBACK_FAMILY}"


# Step 5: sizes ------------------------------------------------------------------------


def _number(text: str | None) -> float | None:
    """
    A length in px: a number, optionally followed by an absolute unit, with
    whitespace allowed around either. Anything else, `%` and `em` among it, is None.
    """
    if text is None:
        return None
    body, factor = text.strip(), 1.0
    for unit, px in _UNIT_PX.items():
        if body.endswith(unit):
            body, factor = body[: -len(unit)].rstrip(), px
            break
    if not _NUMBER.fullmatch(body):
        return None
    value = float(body) * factor
    return value if math.isfinite(value) else None


def _positive(text: str | None) -> float | None:
    value = _number(text)
    return value if value is not None and value > 0 else None


def _view_box(text: str | None) -> tuple[float, float] | None:
    if text is None:
        return None
    parts = re.split(r"[\s,]+", text.strip())
    if len(parts) != 4 or not all(_NUMBER.fullmatch(p) for p in parts):
        return None
    w, h = float(parts[2]), float(parts[3])
    if not (math.isfinite(w) and math.isfinite(h) and w > 0 and h > 0):
        return None
    return w, h


def _max_width(style: str | None) -> float | None:
    if style is None:
        return None
    for declaration, _ in _css_split(style, ";"):
        name, colon, value = declaration.partition(":")
        if colon and name.strip().lower() == "max-width":
            return _positive(value)
    return None


def _natural_size(root: ET.Element) -> tuple[float, float]:
    """
    The size the host lays the figure out at, from the root's attributes:

    - `width` and `height` both absolute: those;
    - one of them absolute, with a `viewBox`: that one, the other from the aspect;
    - otherwise (`width="100%"`, or a `viewBox` alone) with a `viewBox`: the width is
      `style="max-width: ...px"` if present, else the viewBox's in user units as px,
      and the height follows from the aspect;
    - anything else is refused, because the aspect cannot be known.
    """
    width = _positive(root.get("width"))
    height = _positive(root.get("height"))
    box = _view_box(root.get("viewBox"))
    if width is not None and height is not None:
        size = (width, height)
    elif box is not None:
        bw, bh = box
        if width is not None:
            size = (width, width * bh / bw)
        elif height is not None:
            size = (height * bw / bh, height)
        else:
            natural = _max_width(root.get("style")) or bw
            size = (natural, natural * bh / bw)
    else:
        raise _Refusal("no natural size: needs width and height, or a viewBox")
    w, h = size
    if not (math.isfinite(w) and math.isfinite(h) and w > 0 and h > 0):
        raise _Refusal("no natural size: it is zero or not finite")
    if w > MAX_SIDE or h > MAX_SIDE or w * h > MAX_NATURAL_PIXELS:
        raise _Refusal(
            f"natural size {w:.0f}x{h:.0f} is over the cap"
            f" ({MAX_SIDE:.0f} a side, {MAX_NATURAL_PIXELS} pixels)"
        )
    return w, h


def _fmt(value: float) -> str:
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


# Step 5: the element count after expansion ---------------------------------------------


def _references(element: ET.Element) -> list[str]:
    found = [m.group(1)[1:] for v in element.attrib.values() for m in _LOCAL_URL_ID.finditer(v)]
    href = element.get("href")
    if href is not None:
        found.append(href[1:])
    return found


def _expanded_size(root: ET.Element) -> int:
    """
    The number of elements a renderer instantiates: each element, plus its children,
    plus for every same-document reference it makes (`href`, and `url(#id)` in any
    attribute) the expanded size of what it names, once per referencing element.

    Expansion is summed rather than nested `<use>` refused: icon sets and Mermaid's
    markers legitimately reference definitions that reference others, and a sum over
    a memoised walk is exact and linear in the tree, where the alternative bounds the
    count by forbidding a shape. A reference cycle is refused. Where an id is defined
    twice, the larger target is counted. References made from `<style>` rules, and a
    marker's repetition per path vertex, are not counted.

    The same walk refuses a chain of more than `MAX_LAYER_CHAIN` masks and clip
    paths inside one another, by nesting or by reference.
    """
    by_id: dict[str, list[ET.Element]] = {}
    for element in root.iter():
        ident = element.get("id")
        if ident is not None:
            by_id.setdefault(ident, []).append(element)

    def dependencies(element: ET.Element) -> list[ET.Element]:
        targets = [t for ref in _references(element) for t in by_id.get(ref, ())]
        return [*element, *targets]

    size: dict[int, int] = {}
    layers: dict[int, int] = {}
    on_path: set[int] = set()
    stack: list[tuple[ET.Element, bool]] = [(root, False)]
    while stack:
        element, finished = stack.pop()
        key = id(element)
        if finished:
            on_path.discard(key)
            total = 1 + sum(size[id(c)] for c in element)
            for ref in _references(element):
                total += max((size[id(t)] for t in by_id.get(ref, ())), default=0)
            if total > MAX_ELEMENTS:
                return total
            size[key] = total
            inner = max((layers[id(d)] for d in dependencies(element)), default=0)
            layers[key] = inner + (_local(element.tag) in _LAYERS)
            if layers[key] > MAX_LAYER_CHAIN:
                raise _Refusal(f"masks or clip paths {layers[key]} deep, over {MAX_LAYER_CHAIN}")
            continue
        if key in size:
            continue
        if key in on_path:
            raise _Refusal("a reference cycle (an element refers to its own ancestor)")
        on_path.add(key)
        stack.append((element, True))
        stack.extend((d, False) for d in dependencies(element) if id(d) not in size)
    return size[id(root)]
