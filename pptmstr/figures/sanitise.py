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
   its subtree, which includes `script`, `image`, `a`, `iframe`, `filter`, `feImage`
   and the animation elements. `foreignObject` is rewritten to a centred `<text>` at
   the same place in the tree (see `_rewrite_foreign_object`), because Mermaid hangs
   its labels on it. `filter` stays out: its cost is unmeasured beyond merman's drop
   shadow, which is about half of resvg's render time for a flowchart (54 ms without
   it, 112 ms with it; notes/vector-rendering/probe-m1-r2.md).
3. **Attributes.** Every `on*` attribute is dropped, compared case-insensitively.
   `href` and `xlink:href` survive only as a same-document `#id`, and come out as
   plain `href` (SVG 2, which resvg reads), so the output needs no xlink namespace.
   Any other namespaced attribute except `xml:space` is dropped. An attribute whose
   value contains a backslash, a `url(` other than `url(#id)`, or a function that
   takes a URL as a string (`image(`, `image-set(`, `src(`) is dropped whole.
4. **CSS** in `style=` is split into declarations, and a declaration with a
   backslash, `@import`, a non-fragment `url(` or a URL-taking function is dropped
   whole. A `<style>` element with any of those, or `@font-face`, is dropped whole.
   Nothing is cut out of the middle of a value: a CSS escape makes `u\\72l(` a url
   token, so substring stripping can be evaded, and the backslash rule closes that.
5. **Caps** (`MAX_INPUT_BYTES`, `MAX_DEPTH`, `MAX_ELEMENTS`, `MAX_SIDE`,
   `MAX_NATURAL_PIXELS`). The element cap counts the sanitised tree *after*
   reference expansion: see `_expanded_size`.
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
   box). In `fill` and `stroke` the value becomes its fallback (`url(#gone) red` is
   `red`) or `none`, which is what SVG 2 makes of a missing paint server. Anywhere
   else (`filter`, `clip-path`, `mask`, `marker-*`, `href`, ...) the attribute or the
   CSS declaration is removed, so the element draws without the effect.

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

# Nesting depth of the parsed input. Mermaid nests about a dozen deep. The bound is what
# lets every walk here recurse without meeting Python's recursion limit.
MAX_DEPTH = 64

# Natural size. 16384 px is the common GL_MAX_TEXTURE_SIZE, and 4096 x 4096 is 64 MiB of
# RGBA at 1x. The raster is made at the drawn width, never above the natural width, so
# these bound the texture at 1x; the renderer seam applies its own cap for DPI scale.
MAX_SIDE = 16_384.0
MAX_NATURAL_PIXELS = 4096 * 4096

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
        "pattern",
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
_STYLE_AT_RULE = re.compile(r"@(?:import|font-face)", re.IGNORECASE)
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_LENGTH = re.compile(rf"\s*({_NUMBER.pattern})\s*(px|pt|pc|in|cm|mm)?\s*")
_UNIT_PX = {"": 1.0, "px": 1.0, "pt": 4 / 3, "pc": 16.0, "in": 96.0, "cm": 96 / 2.54}
_UNIT_PX["mm"] = 96 / 25.4
_FONT_DECL = re.compile(r"(\s*)(font-family|font)(\s*:)(.*?)(\s*!\s*important\s*)?", re.I | re.S)


@dataclass(frozen=True, slots=True)
class Sanitised:
    """
    SVG that passed the policy, and the natural size the host lays it out at.

    `width` and `height` are in CSS px and are also what the root's attributes now
    say. The host's height for a drawn width `w` is `min(w, width) * height / width`.
    """

    svg: str
    width: float
    height: float


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
    _check_depth(root)
    out = _clean(root)
    if out is None:
        raise _Refusal("the root element is not <svg>")
    _resolve_references(out)
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
    return Sanitised(svg=text, width=width, height=height)


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


def _check_depth(root: ET.Element) -> None:
    stack: list[tuple[ET.Element, int]] = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise _Refusal(f"nested deeper than {MAX_DEPTH}")
        stack.extend((child, depth + 1) for child in element)


def _clean(src: ET.Element) -> ET.Element | None:
    local = _local(src.tag)
    if not _is_svg(src.tag):
        return None
    if local == "foreignObject":
        return _rewrite_foreign_object(src)
    if local not in ALLOWED_ELEMENTS:
        return None
    out = ET.Element(local, _clean_attributes(src.attrib))
    if local in _TEXT_ONLY:
        text = "".join(src.itertext())
        if local == "style":
            cleaned = _clean_stylesheet(text)
            if cleaned is None:
                return None
            text = cleaned
        out.text = text or None
        return out
    out.text = src.text
    last: ET.Element | None = None
    for child in src:
        kept = _clean(child)
        if kept is None:
            # The dropped element's tail is still its parent's content (text runs).
            if child.tail:
                if last is None:
                    out.text = (out.text or "") + child.tail
                else:
                    last.tail = (last.tail or "") + child.tail
            continue
        kept.tail = child.tail
        out.append(kept)
        last = kept
    return out


def _clean_attributes(attrib: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in attrib.items():
        if name.startswith("{"):
            ns, _, local = name[1:].partition("}")
            if ns == XLINK_NS and local == "href":
                if "href" in out:
                    continue  # a plain href beside it wins, as in SVG 2
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
            value = _clean_declarations(value)
            if not value:
                continue
        elif lowered == "href":
            fragment = value.strip()
            if key != "href" or not _FRAGMENT.fullmatch(fragment):
                continue
            value = fragment
        elif _css_unsafe(value):
            continue
        elif lowered in ("font-family", "font"):
            value = _fix_font_value(lowered, value)
        out[key] = value
    return out


def _rewrite_foreign_object(fo: ET.Element) -> ET.Element | None:
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
    attrs = _clean_attributes(kept)
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


# Step 8 -------------------------------------------------------------------------------

_PAINT = frozenset({"fill", "stroke"})
_IMPORTANT = re.compile(r"(.*?)(\s*!\s*important\s*)?", re.IGNORECASE | re.DOTALL)


def _resolve_references(root: ET.Element) -> None:
    ids = {ident for element in root.iter() if (ident := element.get("id")) is not None}
    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if name == "href":
                resolved: str | None = value if value[1:] in ids else None
            elif name == "style":
                resolved = _resolve_declarations(value, ids, "; ") or None
            else:
                resolved = _resolve_value(name, value, ids)
            if resolved is None:
                del element.attrib[name]
            else:
                element.set(name, resolved)
        if element.tag == "style" and element.text:
            element.text = _resolve_declarations(element.text, ids, None)


def _resolve_value(prop: str, value: str, ids: set[str]) -> str | None:
    """
    `value` with dangling fragment references resolved for property `prop`, or None
    when the property should be removed.
    """
    targets = [m.group(1)[1:] for m in _LOCAL_URL_ID.finditer(value)]
    if all(target in ids for target in targets):
        return value
    if prop.strip().lower() not in _PAINT:
        return None
    match = _IMPORTANT.fullmatch(value)
    assert match is not None  # the pattern matches any text
    body, important = match.groups()
    fallback = _LOCAL_URL.sub("", body).strip()
    return f"{fallback or 'none'}{important or ''}"


def _resolve_declarations(text: str, ids: set[str], joiner: str | None) -> str:
    """
    CSS with each declaration's dangling references resolved. With a `joiner`, `text`
    is a declaration list (`style=`) and the kept declarations are joined by it;
    without one it is a stylesheet and its delimiters are kept in place.
    """
    pieces: list[str] = []
    for piece, delimiter in _css_split(text, ";" if joiner else ";{}"):
        prop, colon, value = piece.partition(":")
        if colon and _LOCAL_URL.search(value):
            resolved = _resolve_value(prop, value, ids)
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
    if "\\" in text or _IMPORT.search(text) or _URL_STRING_FUNC.search(text):
        return True
    return _ANY_URL.search(_LOCAL_URL.sub("", text)) is not None


def _css_split(text: str, delimiters: str) -> Iterator[tuple[str, str]]:
    """
    Split CSS at top-level `delimiters`, outside strings, with comments removed (a
    comment separates tokens, so it becomes a space). Yields `(piece, delimiter)`;
    the last delimiter is "". A backslash is not interpreted: a piece holding one is
    dropped by the caller, so where an escaped quote ends a string does not matter.
    """
    piece: list[str] = []
    quote = ""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            piece.append(c)
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
            piece.append(c)
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            piece.append(" ")
            continue
        elif c in delimiters:
            yield "".join(piece), c
            piece = []
        else:
            piece.append(c)
        i += 1
    yield "".join(piece), ""


def _clean_declarations(text: str) -> str:
    kept = []
    for declaration, _ in _css_split(text, ";"):
        if _css_unsafe(declaration) or not declaration.strip():
            continue
        kept.append(_fix_font_declaration(declaration).strip())
    return "; ".join(kept)


def _clean_stylesheet(text: str) -> str | None:
    if _css_unsafe(text) or _STYLE_AT_RULE.search(text):
        return None
    return "".join(
        _fix_font_declaration(piece) + delimiter for piece, delimiter in _css_split(text, ";{}")
    )


# Step 7 -------------------------------------------------------------------------------


def _fix_font_declaration(declaration: str) -> str:
    match = _FONT_DECL.fullmatch(declaration)
    if match is None:
        return declaration
    lead, prop, colon, value, important = match.groups()
    fixed = _fix_font_value(prop.lower(), value)
    return f"{lead}{prop}{colon}{fixed}{important or ''}"


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
    if text is None:
        return None
    match = _LENGTH.fullmatch(text)
    if match is None:
        return None
    value = float(match.group(1)) * _UNIT_PX[(match.group(2) or "").lower()]
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
            continue
        if key in size:
            continue
        if key in on_path:
            raise _Refusal("a reference cycle (an element refers to its own ancestor)")
        on_path.add(key)
        stack.append((element, True))
        stack.extend((d, False) for d in dependencies(element) if id(d) not in size)
    return size[id(root)]
