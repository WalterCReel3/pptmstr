"""
The SVG sanitiser: each hostile case by name, the natural-size rule, and totality.
"""

from __future__ import annotations

import io
import random
import re
import signal
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import TypeVar

import pytest

from pptmstr.figures.sanitise import (
    ALLOWED_ELEMENTS,
    HARMLESS_AT_RULES,
    MAX_DEPTH,
    MAX_ELEMENTS,
    MAX_INPUT_BYTES,
    MAX_INPUT_ELEMENTS,
    MAX_LAYER_CHAIN,
    MAX_NOTES,
    MAX_VAR_DEPTH,
    MAX_VAR_VALUES,
    NAMED_COLOURS,
    SVG_NS,
    XML_NS,
    Refused,
    Sanitised,
    sanitise,
)

HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"'
    ' width="200" height="100">'
)
T = TypeVar("T")
LOCAL_URL = re.compile(r"url\(\s*([\"']?)#[\w.:-]+\1\s*\)", re.IGNORECASE)


def doc(body: str, head: str = HEAD) -> str:
    return f"{head}{body}</svg>"


def passed(svg: str) -> Sanitised:
    out = sanitise(svg)
    assert isinstance(out, Sanitised), out
    assert_safe(out.svg)
    return out


def refused(svg: str) -> str:
    out = sanitise(svg)
    assert isinstance(out, Refused), out
    return out.reason


def quoted(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;")


def render_pixel(svg: str, at: tuple[int, int] = (100, 50)) -> tuple[int, ...]:
    """
    The RGBA resvg paints at `at` with the canvas 200 px wide; skips without resvg.
    """
    resvg_py = pytest.importorskip("resvg_py")
    image = pytest.importorskip("PIL.Image")
    png = resvg_py.svg_to_bytes(svg_string=svg, width=200)
    with image.open(io.BytesIO(bytes(png))) as pixels:
        return tuple(pixels.convert("RGBA").getpixel(at))


def tree(out: Sanitised) -> ET.Element:
    return ET.fromstring(out.svg)


def local(tag: str) -> str:
    return tag.rpartition("}")[2]


def find_all(out: Sanitised, name: str) -> list[ET.Element]:
    return list(tree(out).iter(f"{{{SVG_NS}}}{name}"))


def assert_safe(svg: str) -> None:
    """
    The properties every passed output must have, checked on the output alone.
    """
    assert "<!" not in svg
    root = ET.fromstring(svg)
    assert root.tag == f"{{{SVG_NS}}}svg"
    for element in root.iter():
        assert element.tag.startswith(f"{{{SVG_NS}}}"), element.tag
        assert local(element.tag) in ALLOWED_ELEMENTS, element.tag
        for name, value in element.attrib.items():
            assert not name.lower().startswith("on"), name
            assert not name.startswith("{") or name == f"{{{XML_NS}}}space", name
            assert "\\" not in value, (name, value)
            if name.lower() == "href":
                assert re.fullmatch(r"#[\w.:-]+", value), value
            assert not re.search(r"url\s*\(", LOCAL_URL.sub("", value), re.I), (name, value)
        if local(element.tag) == "style":
            css = element.text or ""
            assert "\\" not in css and "@import" not in css.lower()
            assert not re.search(r"url\s*\(", LOCAL_URL.sub("", css), re.I), css


# Step 1: declarations -----------------------------------------------------------------

DOCTYPES = {
    "plain": '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "x.dtd">' + doc(""),
    "lowercase": "<!doctype svg>" + doc(""),
    "mixed case": "<!DoCtYpE svg>" + doc(""),
    "after xml declaration": '<?xml version="1.0"?>\n<!DOCTYPE svg>' + doc(""),
    "after a comment": "<!-- hello -->\n<!DOCTYPE svg>" + doc(""),
    "after declaration and comments": '<?xml version="1.0"?><!--a--><!--b--><!DOCTYPE svg>'
    + doc(""),
    "internal subset": '<!DOCTYPE svg [<!ATTLIST svg x CDATA "1">]>' + doc(""),
    "external entity (XXE)": '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    + doc("<text>&x;</text>"),
}


@pytest.mark.parametrize("svg", DOCTYPES.values(), ids=DOCTYPES.keys())
def test_doctype_is_refused_in_every_variant(svg: str) -> None:
    assert "DOCTYPE" in refused(svg)


def test_doctype_hidden_inside_a_comment_is_still_refused() -> None:
    refused("<!-- <!DOCTYPE svg> -->" + doc(""))


def test_entity_declaration_is_refused() -> None:
    assert "ENTITY" in refused('<!ENTITY x "y">' + doc("<text>&x;</text>"))


def test_billion_laughs_is_refused_without_expansion() -> None:
    laughs = (
        '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a0 "aaaaaaaaaa">'
        + "".join(f'<!ENTITY a{i} "{f"&a{i - 1};" * 10}">' for i in range(1, 10))
        + "]>"
        + doc("<text>&a9;</text>")
    )
    reason = within_bound(lambda: refused(laughs), 0.5)
    assert "DOCTYPE" in reason


def test_undefined_entity_reference_is_refused() -> None:
    assert "XML" in refused(doc("<text>&nbsp;</text>"))


def test_comments_and_cdata_are_not_declarations() -> None:
    out = passed("<!-- a comment -->" + doc("<style><![CDATA[rect{fill:red}]]></style>"))
    assert find_all(out, "style")[0].text == "rect{fill:red}"


# Step 2: elements ---------------------------------------------------------------------


def test_absolute_image_is_dropped() -> None:
    out = passed(doc('<image width="20" height="20" href="/etc/secret.png"/>'))
    assert "image" not in out.svg and "secret" not in out.svg


def test_relative_image_is_dropped() -> None:
    out = passed(doc('<image width="20" height="20" href="secret.png"/>'))
    assert "image" not in out.svg and "secret" not in out.svg


def test_file_url_href_is_dropped() -> None:
    out = passed(doc('<use xlink:href="file:///etc/passwd#x"/><use href="file:///x.svg#a"/>'))
    assert [u.attrib for u in find_all(out, "use")] == [{}, {}]


def test_http_href_is_dropped() -> None:
    out = passed(doc('<use href="http://127.0.0.1:9/x.svg#a"/><textPath xlink:href="https://e/"/>'))
    assert "127.0.0.1" not in out.svg and "https://e/" not in out.svg


def test_non_fragment_href_is_dropped_even_when_an_id_matches_its_tail() -> None:
    # The dangling-reference pass compares href[1:] with ids; the fragment rule must
    # not lean on it.
    out = passed(doc('<rect id="ttp://e/x.svg"/><use href="http://e/x.svg"/>'))
    assert find_all(out, "use")[0].attrib == {}


def test_javascript_href_is_dropped() -> None:
    out = passed(doc('<use xlink:href="javascript:alert(1)"/>'))
    assert "javascript" not in out.svg


def test_fragment_href_is_kept_and_comes_out_as_plain_href() -> None:
    out = passed(doc('<defs><rect id="r"/></defs><use xlink:href=" #r "/>'))
    assert find_all(out, "use")[0].attrib == {"href": "#r"}


def test_script_is_dropped() -> None:
    out = passed(doc("<script>document.write(1)</script><rect/>"))
    assert "script" not in out.svg and "document" not in out.svg
    assert len(find_all(out, "rect")) == 1


@pytest.mark.parametrize(
    "name", ["a", "iframe", "image", "filter", "feImage", "animate", "set", "switch", "video"]
)
def test_elements_outside_the_allow_list_are_dropped_with_their_subtree(name: str) -> None:
    out = passed(doc(f'<{name} href="#r"><rect id="inside"/></{name}><circle r="1"/>'))
    assert name not in out.svg and "inside" not in out.svg
    assert len(find_all(out, "circle")) == 1


def test_elements_in_another_namespace_are_dropped() -> None:
    out = passed(doc('<x:rect xmlns:x="urn:other" width="5"/><rect/>'))
    assert len(find_all(out, "rect")) == 1 and "urn:other" not in out.svg


def test_a_dropped_elements_tail_text_stays_in_the_text_run() -> None:
    out = passed(doc("<text>a<script>x</script>b<tspan>c</tspan>d<a>e</a>f</text>"))
    assert "".join(find_all(out, "text")[0].itertext()) == "abcdf"


def test_processing_instructions_are_not_kept() -> None:
    out = passed('<?xml-stylesheet href="http://e/x.css"?>' + doc(""))
    assert "stylesheet" not in out.svg


def test_svg_without_a_namespace_is_accepted_and_namespaced() -> None:
    out = passed('<svg width="10" height="10"><rect/></svg>')
    assert tree(out).tag == f"{{{SVG_NS}}}svg"


@pytest.mark.parametrize(
    "svg",
    [
        '<html width="1" height="1"/>',
        '<g xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
        '<svg xmlns="urn:not-svg" width="1" height="1"/>',
        "not xml at all",
        "",
    ],
)
def test_a_root_that_is_not_svg_is_refused(svg: str) -> None:
    refused(svg)


def test_text_that_is_not_a_string_is_refused() -> None:
    assert "not text" in sanitise(b"<svg/>").reason  # type: ignore[arg-type, union-attr]


# Step 2: foreignObject ----------------------------------------------------------------


def test_foreign_object_becomes_centred_text_where_it_stood() -> None:
    out = passed(
        doc(
            '<g class="node" transform="translate(40,30)">'
            '<g class="label" transform="translate(-25,-12)">'
            '<foreignObject x="2" y="4" width="50" height="24" class="lbl" onclick="x()">'
            '<div xmlns="http://www.w3.org/1999/xhtml" style="color:red">'
            '<span class="nodeLabel"><p>Start  here</p></span></div>'
            "</foreignObject></g></g>"
        )
    )
    assert "foreignObject" not in out.svg and "xhtml" not in out.svg
    label = tree(out).find(f".//{{{SVG_NS}}}g[@class='label']")
    assert label is not None and label.get("transform") == "translate(-25,-12)"
    (text,) = list(label)
    assert text.tag == f"{{{SVG_NS}}}text"
    assert text.attrib == {
        "class": "lbl",
        "x": "27",
        "y": "16",
        "text-anchor": "middle",
        "dominant-baseline": "central",
    }
    assert text.text == "Start here"


def test_foreign_object_lines_become_tspans_centred_on_the_box() -> None:
    out = passed(
        doc(
            '<foreignObject width="100" height="40">'
            '<div xmlns="http://www.w3.org/1999/xhtml">one<br/>two<p>three</p></div>'
            "</foreignObject>"
        )
    )
    spans = find_all(out, "tspan")
    assert [s.text for s in spans] == ["one", "two", "three"]
    assert [s.get("dy") for s in spans] == ["-1.2em", "1.2em", "1.2em"]
    assert {s.get("x") for s in spans} == {"50"}


def test_foreign_object_drops_its_xhtml_markup_and_keeps_only_text() -> None:
    out = passed(
        doc(
            '<foreignObject width="10" height="10"><div xmlns="http://www.w3.org/1999/xhtml">'
            '<img src="/etc/x.png"/><a href="http://e/">link</a><script>s()</script>'
            "</div></foreignObject>"
        )
    )
    assert "<img" not in out.svg and "x.png" not in out.svg and "http://e/" not in out.svg
    assert find_all(out, "text")[0].text == "links()"


def test_foreign_object_attributes_carried_to_the_text_are_sanitised() -> None:
    out = passed(
        doc(
            '<foreignObject width="10" height="10" class="c" transform="u\\72l(x)"'
            ' style="fill:url(http://e/x); color:red">a</foreignObject>'
        )
    )
    text = find_all(out, "text")[0]
    assert (text.get("class"), text.get("transform"), text.get("style")) == (
        "c",
        None,
        "fill:none; color:red",
    )


def test_foreign_object_without_text_is_dropped() -> None:
    out = passed(doc('<foreignObject width="10" height="10"><div/></foreignObject><rect/>'))
    assert find_all(out, "text") == [] and len(find_all(out, "rect")) == 1


def test_foreign_object_with_unparseable_geometry_centres_on_zero() -> None:
    out = passed(doc('<foreignObject x="inf" width="1e999" height="50%">a</foreignObject>'))
    text = find_all(out, "text")[0]
    assert (text.get("x"), text.get("y")) == ("0", "0")


# Step 3: attributes -------------------------------------------------------------------


def test_on_attributes_are_dropped() -> None:
    out = passed(doc('<rect onload="a()" ONCLICK="b()" onMouseOver="c()" width="5"/>', HEAD + ""))
    assert find_all(out, "rect")[0].attrib == {"width": "5"}


def test_on_attribute_on_the_root_is_dropped() -> None:
    out = passed('<svg xmlns="http://www.w3.org/2000/svg" width="5" height="5" onload="x()"/>')
    assert "onload" not in out.svg


URL_ATTRIBUTES = [
    "clip-path",
    "mask",
    "marker-start",
    "marker-mid",
    "marker-end",
    "filter",
    "cursor",
]
FOREIGN_URLS = [
    "url(http://127.0.0.1:9/x.svg#a)",
    "url(file:///etc/passwd)",
    "URL( 'x.svg#a' )",
    'url("relative.svg#a")',
    "url(#a) url(http://e/b)",
    "url(#a",
]


@pytest.mark.parametrize("value", FOREIGN_URLS)
@pytest.mark.parametrize("attribute", URL_ATTRIBUTES)
def test_url_in_a_presentation_attribute_is_dropped(attribute: str, value: str) -> None:
    out = passed(doc(f'<path d="M0 0" {attribute}="{quoted(value)}"/>'))
    assert find_all(out, "path")[0].attrib == {"d": "M0 0"}


# A removed fill paints black, so an unsafe paint is repainted instead (step 3).
@pytest.mark.parametrize("value", FOREIGN_URLS)
@pytest.mark.parametrize("attribute", ["fill", "stroke"])
def test_url_in_a_paint_attribute_becomes_none(attribute: str, value: str) -> None:
    out = passed(doc(f'<path d="M0 0" {attribute}="{quoted(value)}"/>'))
    assert find_all(out, "path")[0].attrib == {"d": "M0 0", attribute: "none"}


@pytest.mark.parametrize("value", ["url(#a)", "url('#a')", 'url( "#a" )', "URL(#a) none"])
def test_fragment_url_in_a_presentation_attribute_is_kept(value: str) -> None:
    out = passed(doc(f'<linearGradient id="a"/><path fill="{quoted(value)}"/>'))
    assert find_all(out, "path")[0].get("fill") == value


def test_url_taking_functions_are_dropped() -> None:
    out = passed(doc("<rect mask=\"image-set('x.png' 1x)\" stroke=\"image('y.png') red\"/>"))
    assert find_all(out, "rect")[0].attrib == {"stroke": "none"}


def test_backslash_in_an_attribute_drops_it() -> None:
    out = passed(doc(r'<rect cursor="u\72l(http://e/)" fill="u\72l(http://e/) red" width="1"/>'))
    assert find_all(out, "rect")[0].attrib == {"fill": "none", "width": "1"}


def test_other_namespaced_attributes_are_dropped_and_xml_space_kept() -> None:
    out = passed(
        doc('<text xml:space="preserve" xml:base="http://e/" xlink:title="t" x="1">a</text>')
    )
    assert find_all(out, "text")[0].attrib == {f"{{{XML_NS}}}space": "preserve", "x": "1"}


# Step 4: CSS --------------------------------------------------------------------------


def style_text(out: Sanitised) -> str | None:
    styles = find_all(out, "style")
    return styles[0].text if styles else None


def test_css_url_in_a_style_element_repaints_only_that_declaration() -> None:
    out = passed(doc("<style>rect{fill:url(http://e/x.svg#p) red;stroke:blue}</style><rect/>"))
    assert style_text(out) == "rect{fill:red;stroke:blue}"
    assert out.notes == ("fill: a url() outside the document; painted red",)


def test_css_escaped_url_in_a_style_element_repaints_only_that_declaration() -> None:
    out = passed(doc(r"<style>rect{fill:u\72l(http://e/x) red;stroke:blue}</style><rect/>"))
    assert style_text(out) == "rect{fill:none;stroke:blue}"


def test_an_unsafe_declaration_in_a_style_element_is_dropped_and_its_rule_kept() -> None:
    out = passed(doc("<style>.a{fill:#e8590c} .b{mask:url(http://x/y);opacity:.5}</style>"))
    assert style_text(out) == ".a{fill:#e8590c} .b{;opacity:.5}"


def test_an_unsafe_selector_drops_its_rule_and_every_block_inside_it() -> None:
    out = passed(
        doc(
            "<style>.a{fill:red} .b[x='url(http://x)']{fill:blue}"
            " @media url(http://x){.c{fill:green} .e{fill:gold}} .d{fill:#123}</style>"
        )
    )
    assert style_text(out) == ".a{fill:red} .d{fill:#123}"


@pytest.mark.parametrize(
    "css",
    [
        '@IMPORT "http://e/x.css"; rect{fill:red}',
        '@font-face{font-family:x;src:local("y")} rect{fill:red}',
        "@namespace svg 'x'; rect{fill:red}",
        '@charset "utf-8"; rect{fill:red}',
        "@page{margin:0} rect{fill:red}",
        "@counter-style c{symbols:a} rect{fill:red}",
        r"@\69mport 'x'; rect{fill:red}",
        "@/**/import 'x'; rect{fill:red}",
        "rect{fill:red} .a::after{content:'@'}",
    ],
)
def test_an_at_rule_not_known_to_be_harmless_drops_the_style_element(css: str) -> None:
    assert style_text(passed(doc(f"<style>{css}</style><rect/>"))) is None


@pytest.mark.parametrize(
    "css",
    [
        "@media (min-width:1px){.a{fill:red}}",
        "@MEDIA screen{.a{fill:red}}",
        "@supports (fill:red){.a{fill:red}}",
        "@keyframes k{to{opacity:1}}",
        "@-webkit-keyframes k{to{opacity:1}}",
        "@layer a, b; @layer a{.a{fill:red}}",
    ],
)
def test_a_harmless_at_rule_keeps_the_style_element(css: str) -> None:
    assert style_text(passed(doc(f"<style>{css}</style><rect/>"))) == css


def test_the_harmless_at_rules_are_named() -> None:
    assert HARMLESS_AT_RULES == {"media", "supports", "keyframes", "-webkit-keyframes", "layer"}


@pytest.mark.parametrize(
    "unsafe",
    [
        ".b{mask:url(http://x/y)}",
        ".b{fill:url(http://x) foo}",
        r".b{stroke:u\72l(x)}",
        ".b[x='url(http://x)']{fill:blue}",
    ],
)
def test_a_shape_coloured_by_a_class_keeps_its_colour_beside_an_unsafe_rule(unsafe: str) -> None:
    svg = doc(
        f"<style>.a{{fill:#e8590c}} {unsafe}</style>"
        '<rect width="200" height="100" fill="#fff"/><rect class="a" width="200" height="100"/>'
    )
    assert render_pixel(passed(svg).svg) == (0xE8, 0x59, 0x0C, 255)


def test_css_fragment_url_in_a_style_element_is_kept() -> None:
    out = passed(doc('<marker id="arrow"/><style>.e path{marker-end:url(#arrow)}</style>'))
    assert find_all(out, "style")[0].text == ".e path{marker-end:url(#arrow)}"


def test_css_url_in_a_style_attribute_drops_only_that_declaration() -> None:
    out = passed(doc('<rect style="mask:url(file:///etc/x);stroke:red;opacity:0.5"/>'))
    assert find_all(out, "rect")[0].get("style") == "stroke:red; opacity:0.5"


def test_css_escaped_url_in_a_style_attribute_drops_that_declaration() -> None:
    out = passed(doc(r'<rect style="stroke:blue; mask:u\72l(http://e/x)"/>'))
    assert find_all(out, "rect")[0].get("style") == "stroke:blue"


def test_css_import_in_a_style_attribute_drops_that_declaration() -> None:
    out = passed(doc('<rect style="@import url(x); fill:red"/>'))
    assert find_all(out, "rect")[0].get("style") == "fill:red"


def test_css_import_without_url_in_a_style_attribute_drops_that_declaration() -> None:
    out = passed(doc("<rect style='@import \"x.css\"; fill:red'/>"))
    assert find_all(out, "rect")[0].get("style") == "fill:red"


def test_css_comments_are_removed_so_they_cannot_move_declaration_boundaries() -> None:
    out = passed(doc('<rect style="stroke:red /* x; y */; fill:blue"/>'))
    assert find_all(out, "rect")[0].get("style") == "stroke:red; fill:blue"


def test_css_comment_inside_url_does_not_survive_as_a_url() -> None:
    out = passed(doc('<rect style="fill:ur/**/l(http://e/x)"/>'))
    assert "url(" not in out.svg


def test_style_attribute_with_nothing_left_is_dropped() -> None:
    out = passed(doc('<rect style="clip-path:url(http://e/)" width="1"/>'))
    assert find_all(out, "rect")[0].attrib == {"width": "1"}


# An unsafe fill or stroke: removed, it would paint black (measured 2026-10-21), so it
# keeps the colour after its url() or becomes none.

OFF_DOCUMENT = "a url() outside the document"
LOADER = "a function that loads a URL"
UNSAFE_PAINTS = [
    ("url(http://x)", "none", OFF_DOCUMENT),
    ("url(http://x) red", "red", OFF_DOCUMENT),
    ("url('http://x/a)b') #0a0", "#0a0", OFF_DOCUMENT),
    ('URL( "file:///etc/x" ) rgb(1, 2, 3)', "rgb(1, 2, 3)", OFF_DOCUMENT),
    ("url(http://x/a(b)) red", "none", OFF_DOCUMENT),
    ("url(http://x) url(http://y)", "none", OFF_DOCUMENT),
    ("url(http://x) image('y.png')", "none", LOADER),
    ("url(http://x", "none", OFF_DOCUMENT),
    ("red url(http://x)", "none", OFF_DOCUMENT),
    ("image('y.png') red", "none", LOADER),
    ("url(http://x) foo", "none", OFF_DOCUMENT),
    ("url(http://x) red blue", "none", OFF_DOCUMENT),
    ("url(http://x) rgb(1, 2)", "none", OFF_DOCUMENT),
    ("url(http://x) RebeccaPurple", "#663399", OFF_DOCUMENT),
]


@pytest.mark.parametrize(("value", "painted", "why"), UNSAFE_PAINTS)
@pytest.mark.parametrize("prop", ["fill", "stroke"])
def test_an_unsafe_paint_attribute_keeps_its_fallback_or_becomes_none(
    prop: str, value: str, painted: str, why: str
) -> None:
    out = passed(doc(f'<rect {prop}="{quoted(value)}"/>'))
    assert find_all(out, "rect")[0].get(prop) == painted
    assert out.notes == (f"{prop}: {why}; painted {painted}",)


@pytest.mark.parametrize(("value", "painted", "why"), UNSAFE_PAINTS)
@pytest.mark.parametrize("prop", ["fill", "stroke"])
def test_an_unsafe_paint_declaration_keeps_its_fallback_or_becomes_none(
    prop: str, value: str, painted: str, why: str
) -> None:
    out = passed(doc(f'<rect style="{quoted(f"{prop}: {value}; opacity:0.5")}"/>'))
    assert find_all(out, "rect")[0].get("style") == f"{prop}:{painted}; opacity:0.5"
    assert out.notes == (f"{prop}: {why}; painted {painted}",)


def test_an_unsafe_paint_keeps_important() -> None:
    out = passed(doc('<rect style="fill:url(http://x) red !important"/>'))
    assert find_all(out, "rect")[0].get("style") == "fill:red !important"


def test_an_escape_in_a_paint_makes_it_none_whatever_follows() -> None:
    # Where an escaped URL ends cannot be known without interpreting the escape.
    out = passed(doc(r'<rect fill="url(http://x/\29 red) blue" stroke="u\72l(x) red"/>'))
    rect = find_all(out, "rect")[0]
    assert (rect.get("fill"), rect.get("stroke")) == ("none", "none")
    assert out.notes == (
        "fill: a backslash escape; painted none",
        "stroke: a backslash escape; painted none",
    )


def test_unsafe_paints_are_noted_once_each_and_capped() -> None:
    body = "".join(f'<rect fill="url(http://x) #{n:06x}"/>' for n in range(MAX_NOTES + 3))
    out = passed(doc(body + '<rect fill="url(http://x) #000000"/>'))
    assert len(out.notes) == MAX_NOTES + 1
    assert out.notes[0] == "fill: a url() outside the document; painted #000000"
    assert out.notes[-1] == "and more paint that was repainted"


def test_a_var_note_and_a_paint_note_are_both_kept() -> None:
    out = passed(doc('<rect fill="var(--x)" stroke="url(http://x) red"/>'))
    assert out.notes == (
        "fill: var(--x) is not declared; painted none",
        "stroke: a url() outside the document; painted red",
    )


@pytest.mark.parametrize(
    ("body", "pixel"),
    [
        ('fill="url(http://x)"', (255, 255, 255, 255)),
        ('fill="url(http://x) #e8590c"', (0xE8, 0x59, 0x0C, 255)),
        ('style="fill:url(http://x)"', (255, 255, 255, 255)),
        ('style="fill:url(http://x) #e8590c"', (0xE8, 0x59, 0x0C, 255)),
    ],
)
def test_an_unsafe_fill_is_not_painted_black(body: str, pixel: tuple[int, ...]) -> None:
    resvg_py = pytest.importorskip("resvg_py")
    image = pytest.importorskip("PIL.Image")
    import io

    svg = doc(
        f'<rect width="200" height="100" fill="#fff"/><rect width="200" height="100" {body}/>'
    )
    png = resvg_py.svg_to_bytes(svg_string=passed(svg).svg, width=200)
    with image.open(io.BytesIO(bytes(png))) as pixels:
        assert pixels.convert("RGBA").getpixel((100, 50)) == pixel


# Step 5: caps -------------------------------------------------------------------------


def test_oversize_input_is_refused() -> None:
    body = "<!--" + "x" * MAX_INPUT_BYTES + "-->"
    assert "larger than" in refused(doc(body))


def test_input_at_the_byte_cap_counts_bytes_not_characters() -> None:
    # Two UTF-8 bytes per character: under the cap in characters, over it in bytes.
    body = "<text>" + "é" * (MAX_INPUT_BYTES // 2 + 10) + "</text>"
    assert "larger than" in refused(doc(body))


def test_element_count_at_the_cap_passes_and_one_more_is_refused() -> None:
    # The root is one element.
    passed(doc("<rect/>" * (MAX_ELEMENTS - 1)))
    assert "elements" in refused(doc("<rect/>" * MAX_ELEMENTS))


def test_nested_use_fan_out_is_refused() -> None:
    # Each level uses the one below ten times: 10^6 instances from ~70 source elements.
    levels = ['<g id="l0"><rect/></g>']
    for i in range(1, 7):
        uses = "".join(f'<use href="#l{i - 1}"/>' for _ in range(10))
        levels.append(f'<g id="l{i}">{uses}</g>')
    svg = doc("<defs>" + "".join(levels) + '</defs><use href="#l6"/>')
    assert "expansion" in within_bound(lambda: refused(svg), 0.5)


def test_fan_out_through_url_references_is_counted() -> None:
    marker = '<marker id="m">' + "<circle/>" * 100 + "</marker>"
    paths = '<path marker-end="url(#m)"/>' * 60
    assert "expansion" in refused(doc(f"<defs>{marker}</defs>{paths}"))


def test_modest_reuse_is_counted_and_passes() -> None:
    symbol = '<symbol id="s"><rect/><circle/><path/></symbol>'
    passed(doc(f"<defs>{symbol}</defs>" + '<use href="#s"/>' * 100))


def test_use_cycle_is_refused() -> None:
    svg = doc('<g id="a"><use href="#b"/></g><g id="b"><use href="#a"/></g>')
    assert "cycle" in refused(svg)


def test_use_of_its_own_ancestor_is_refused() -> None:
    assert "cycle" in refused(doc('<g id="a"><g><use href="#a"/></g></g>'))


def test_use_of_a_missing_id_passes() -> None:
    passed(doc('<use href="#nowhere"/>'))


def test_nesting_past_the_depth_cap_is_refused() -> None:
    deep = "<g>" * MAX_DEPTH + "</g>" * MAX_DEPTH
    assert "deeper" in refused(doc(deep))
    passed(doc("<g>" * (MAX_DEPTH - 1) + "</g>" * (MAX_DEPTH - 1)))


def test_very_deep_nesting_does_not_raise() -> None:
    deep = "<g>" * 50_000 + "</g>" * 50_000
    assert "deeper" in refused(doc(deep))


def _mask_chain(n: int) -> str:
    levels = "".join(
        f'<mask id="m{i}"><rect' + (f' mask="url(#m{i + 1})"' if i + 1 < n else "") + "/></mask>"
        for i in range(n)
    )
    return doc(f'<defs>{levels}</defs><rect mask="url(#m0)"/>')


def _clip_chain(n: int) -> str:
    levels = "".join(
        f'<clipPath id="c{i}"'
        + (f' clip-path="url(#c{i + 1})"' if i + 1 < n else "")
        + "><rect/></clipPath>"
        for i in range(n)
    )
    return doc(f'<defs>{levels}</defs><rect clip-path="url(#c0)"/>')


@pytest.mark.parametrize("chain", [_mask_chain, _clip_chain])
def test_a_reference_chain_of_layers_past_the_cap_is_refused(chain: Callable[[int], str]) -> None:
    passed(chain(MAX_LAYER_CHAIN))
    assert f"over {MAX_LAYER_CHAIN}" in refused(chain(MAX_LAYER_CHAIN + 1))


def test_layers_nested_and_referenced_count_together() -> None:
    inner = '<clipPath id="p"><rect/></clipPath>'
    nested = (
        "<mask>" * (MAX_LAYER_CHAIN - 1)
        + '<rect clip-path="url(#p)"/>'
        + "</mask>" * (MAX_LAYER_CHAIN - 1)
    )
    passed(doc(f"<defs>{inner}</defs>{nested}"))
    assert "deep" in refused(doc(f"<defs>{inner}</defs><mask>{nested}</mask>"))


def test_many_layers_side_by_side_are_not_a_chain() -> None:
    passed(doc('<defs><mask id="m"><rect/></mask></defs>' + '<rect mask="url(#m)"/>' * 200))


def test_a_pattern_is_dropped_and_what_it_filled_takes_its_fallback() -> None:
    # resvg allocates a tile whole under every transform above it, and macOS has no
    # RLIMIT_AS to catch that in the render child (probe R3).
    out = passed(
        doc(
            '<defs><pattern id="p" width="10" height="10" patternUnits="userSpaceOnUse">'
            '<rect width="10" height="10"/></pattern></defs>'
            '<g transform="scale(1000)"><rect fill="url(#p) green" width="5"/></g>'
        )
    )
    assert find_all(out, "pattern") == []
    assert find_all(out, "rect")[0].get("fill") == "green"


@pytest.mark.parametrize(
    "body",
    [
        '<rect filter="blur(20px)" width="5"/>',
        '<rect FILTER="drop-shadow(1px 1px 2px red)" width="5"/>',
        '<rect style="filter: blur(20px); opacity: 1" width="5"/>',
    ],
)
def test_the_filter_property_never_survives(body: str) -> None:
    out = passed(doc(body))
    rect = find_all(out, "rect")[0]
    assert {k.lower() for k in rect.attrib} <= {"width", "style"}
    assert "filter" not in (rect.get("style") or "")


def test_a_filter_declaration_in_a_stylesheet_is_removed() -> None:
    out = passed(doc("<style>.a{filter:blur(9px);fill:red}</style>"))
    assert find_all(out, "style")[0].text == ".a{;fill:red}"


def test_input_elements_past_the_cap_are_refused_before_cleaning() -> None:
    # Dropped elements count: what is bounded is the work, not what survives.
    passed(doc("<x/>" * (MAX_INPUT_ELEMENTS - 1)))
    assert f"more than {MAX_INPUT_ELEMENTS} elements" in refused(doc("<x/>" * MAX_INPUT_ELEMENTS))


# Step 5: time on the frame thread -------------------------------------------------------

# sanitise runs on the frame thread, once per fence. Measured worst here at the byte cap
# is about 0.5 s (35,000 repainted declarations in one <style>, 2026-10-21); before every
# pattern was made linear, the whitespace runs below took 8-26 s at 100,000 spaces and
# over five minutes at the cap. The bound leaves twice the measured worst for a slower
# machine, and is enforced by an alarm so a regression fails here in seconds rather
# than hanging the suite.
SANITISE_BOUND_S = 1.0


def _at_cap(head: str, unit: str, tail: str) -> str:
    return head + unit * ((MAX_INPUT_BYTES - len(head) - len(tail)) // len(unit)) + tail


def _spaces(before: str, after: str) -> str:
    return before + " " * (MAX_INPUT_BYTES - len(before) - len(after)) + after


ROOT = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
WORST = {
    "font-family run in style=": _spaces(ROOT + '<text style="font-family:a', 'b">x</text></svg>'),
    "font-family run in <style>": _spaces(ROOT + "<style>text{font-family:a", "b}</style></svg>"),
    "font run with !important": _spaces(
        ROOT + '<text style="font:a', '! important x">x</text></svg>'
    ),
    "length run in the root width": _spaces(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1', 'x" viewBox="0 0 9 9"/>'
    ),
    "length run in max-width": _spaces(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100%" style="max-width:1',
        'x" viewBox="0 0 9 9"/>',
    ),
    "length run in a foreignObject": _spaces(
        ROOT + '<foreignObject width="1', 'x"><p>a</p></foreignObject></svg>'
    ),
    "dangling paint run": _spaces(ROOT + '<rect fill="url(#gone)', 'x"/></svg>'),
    "url( run": _spaces(ROOT + '<rect fill="url(', '#a x"/></svg>'),
    "viewBox run": _spaces('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 9', 'x"/>'),
    "font declarations": _at_cap(ROOT + '<rect style="', "font-family:a;", '"/></svg>'),
    "style declarations": _at_cap(ROOT + '<rect style="', "fill:red;", '"/></svg>'),
    "stylesheet rules": _at_cap(ROOT + "<style>", "a{fill:red}", "</style></svg>"),
    "css comments": _at_cap(ROOT + '<rect style="', "/**/", '"/></svg>'),
    "unclosed quotes": _at_cap(ROOT + '<rect style="', "a:'", '"/></svg>'),
    "attributes": ROOT + "<rect " + " ".join(f'a{i}="1"' for i in range(60_000)) + "/></svg>",
    "dropped elements": _at_cap(ROOT, "<x/>", "</svg>"),
    "dropped elements' tails": _at_cap(ROOT + "<text>", "<x/>a", "</text></svg>"),
    "kept elements": _at_cap(ROOT, "<g/>", "</svg>"),
    "var uses in style=": _at_cap(ROOT + '<rect style="--c:red;', "fill:var(--c);", '"/></svg>'),
    "var uses in <style>": _at_cap(
        ROOT + "<style>:root{--c:red}", "a{fill:var(--c)}", "</style></svg>"
    ),
    "var uses in attributes": ROOT
    + '<g style="--c:red"><rect '
    + " ".join(f'a{i}="var(--c)"' for i in range(40_000))
    + "/></g></svg>",
    "nested var fallbacks": ROOT
    + '<rect fill="'
    + "var(--x," * 100_000
    + "red"
    + ")" * 100_000
    + '"/></svg>',
    "distinct var failures, then repeats": _at_cap(
        ROOT + "<style>" + "".join(f"a{{fill:var(--x{i})}}" for i in range(MAX_VAR_VALUES)),
        "a{fill:var(--x)}",
        "</style></svg>",
    ),
    "unclosed var run": _at_cap(ROOT + '<rect fill="', "var(--x,", '"/></svg>'),
    "custom declarations": _at_cap(ROOT + '<rect style="', "--c:var(--c);", '"/></svg>'),
    "unsafe declarations in <style>": _at_cap(
        ROOT + "<style>a{", "fill:url(http://x) rgb(1,2,3);", "}</style></svg>"
    ),
    "unsafe selectors in <style>": _at_cap(
        ROOT + "<style>", "a[x='url(h)']{b{fill:red}}", "</style></svg>"
    ),
    "harmless at-rules": _at_cap(ROOT + "<style>", "@media x{a{fill:red}}", "</style></svg>"),
    "dangling paint fallbacks": _at_cap(
        ROOT + '<rect style="', "fill:url(#g) rgb(1 2 3 4);", '"/></svg>'
    ),
    "spelled colours in <style>": _at_cap(
        ROOT + "<style>a{", "fill:rebeccapurple;", "}</style></svg>"
    ),
    "spelled colours in style=": _at_cap(
        ROOT + '<rect style="', "stroke:rebeccapurple;", '"/></svg>'
    ),
    "var paints that are not colours": _at_cap(
        ROOT + '<rect style="--c:foo;', "fill:var(--c);", '"/></svg>'
    ),
    "one long colour function": _at_cap(ROOT + '<rect fill="url(#g) rgb(', "1,", ')"/></svg>'),
    "var fan-out": ROOT
    + '<g style="--a0:#000000000;'
    + ";".join(f"--a{i}:" + f"var(--a{i - 1})" * 10 for i in range(1, 8))
    + '"><rect fill="var(--a7)"/></g></svg>',
}


class _Late(BaseException):
    """
    Raised by the alarm; a BaseException so `sanitise`'s total arm does not catch it.
    """


# Tries a timed call gets. The bound is on the best of them, because one wall-clock
# run under a loaded machine or a full suite's collector can stall past it (seen once on
# "var fan-out", measured at 0.08 s alone), while a real regression is slow every time.
# The bound is not loosened, and a linearity ratio was not used: several inputs here are
# not a size swept, and a ratio of two short runs is noisier than one bound.
TIMING_TRIES = 3


def within_bound(call: Callable[[], T], bound: float) -> T:
    """
    `call()`'s result from the first of `TIMING_TRIES` runs to finish within `bound`
    seconds; fails the test if none does. Each run is cut off by an alarm at the bound,
    so a hang costs `TIMING_TRIES` bounds and not the suite.
    """

    def late(signum: int, frame: object) -> None:
        raise _Late

    previous = signal.signal(signal.SIGALRM, late)
    try:
        for _ in range(TIMING_TRIES):
            signal.setitimer(signal.ITIMER_REAL, bound)
            try:
                return call()
            except _Late:
                continue
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, previous)
    pytest.fail(f"no run of {TIMING_TRIES} finished within {bound} s")


def test_within_bound_fails_what_is_slow_every_time_and_passes_one_slow_run() -> None:
    runs: list[float] = []

    def slow_once() -> str:
        runs.append(0.0)
        if len(runs) == 1:
            time.sleep(0.2)
        return "ok"

    assert within_bound(slow_once, 0.1) == "ok"
    assert len(runs) == 2
    with pytest.raises(pytest.fail.Exception, match="no run of 3"):
        within_bound(lambda: time.sleep(0.2), 0.05)


@pytest.mark.parametrize("svg", WORST.values(), ids=WORST.keys())
def test_the_worst_inputs_at_the_byte_cap_sanitise_within_the_bound(svg: str) -> None:
    assert len(svg.encode()) <= MAX_INPUT_BYTES
    within_bound(lambda: sanitise(svg), SANITISE_BOUND_S)


@pytest.mark.parametrize(
    "value, px",
    [
        ("10", 10.0),
        (" 10 px ", 10.0),
        ("1.5e1mm", 1.5e1 * 96 / 25.4),
        ("+.5in", 48.0),
        ("10%", None),
        ("10em", None),
        ("10PX", None),
        ("px", None),
        ("1 x", None),
        ("1e999", None),
    ],
)
def test_a_length_is_a_number_and_an_absolute_unit(value: str, px: float | None) -> None:
    from pptmstr.figures.sanitise import _number

    assert _number(value) == px


# Step 5: natural size -----------------------------------------------------------------

SIZES = {
    "width and height": ('width="200" height="100"', (200, 100)),
    "width and height in px": ('width="200px" height=" 100px "', (200, 100)),
    "width and height in inches": ('width="2in" height="1in"', (192, 96)),
    "width with viewBox": ('width="300" viewBox="0 0 600 400"', (300, 200)),
    "height with viewBox": ('height="100" viewBox="0,0,600,400"', (150, 100)),
    "100% with max-width and viewBox": (
        'width="100%" style="max-width: 320px;" viewBox="0 0 640 480"',
        (320, 240),
    ),
    "100% with viewBox only": ('width="100%" viewBox="-10 -10 640 480"', (640, 480)),
    "viewBox only": ('viewBox="0 0 64 32"', (64, 32)),
    "width and height win over viewBox": ('width="10" height="10" viewBox="0 0 64 32"', (10, 10)),
}


@pytest.mark.parametrize("attrs, size", SIZES.values(), ids=SIZES.keys())
def test_natural_size_for_each_input_shape(attrs: str, size: tuple[float, float]) -> None:
    out = passed(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}><rect/></svg>')
    assert (out.width, out.height) == size
    root = tree(out)
    assert (float(root.get("width", "")), float(root.get("height", ""))) == size


UNSIZED = {
    "nothing": "",
    "100% without viewBox": 'width="100%" height="100%"',
    "max-width without viewBox": 'width="100%" style="max-width: 300px"',
    "width only": 'width="300"',
    "zero viewBox": 'viewBox="0 0 0 10"',
    "negative width": 'width="-5" height="5"',
    "malformed viewBox": 'viewBox="0 0 10"',
    "infinite width": 'width="1e999" height="5"',
    "height that underflows to zero": 'width="10" viewBox="0 0 1e300 1e-300"',
}


@pytest.mark.parametrize("attrs", UNSIZED.values(), ids=UNSIZED.keys())
def test_natural_size_that_cannot_be_determined_is_refused(attrs: str) -> None:
    assert "natural size" in refused(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}/>')


@pytest.mark.parametrize(
    "attrs", ['width="100000" height="10"', 'width="5000" height="5000"', 'viewBox="0 0 1e6 1"']
)
def test_natural_size_over_the_pixel_cap_is_refused(attrs: str) -> None:
    assert "over the cap" in refused(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}/>')


# Step 7: font families ----------------------------------------------------------------


@pytest.mark.parametrize(
    "given, expected",
    [
        ("Arial", "Arial, sans-serif"),
        ("'Trebuchet MS', Verdana", "'Trebuchet MS', Verdana, sans-serif"),
        ('"sans-serif"', '"sans-serif", sans-serif'),
        ("cursive", "cursive, sans-serif"),
        ("Arial, serif", "Arial, serif"),
        ("MONOSPACE", "MONOSPACE"),
        ("inherit", "inherit"),
        ("", "sans-serif"),
    ],
)
def test_font_family_attribute_without_a_mapped_generic_gets_one(given: str, expected: str) -> None:
    out = passed(doc(f'<text font-family="{quoted(given)}">a</text>'))
    assert find_all(out, "text")[0].get("font-family") == expected


def test_font_family_in_a_style_attribute_gets_a_generic() -> None:
    out = passed(doc('<text style="font-size:12px;font-family:Arial !important">a</text>'))
    assert find_all(out, "text")[0].get("style") == (
        "font-size:12px; font-family:Arial, sans-serif !important"
    )


def test_font_family_in_a_style_element_gets_a_generic() -> None:
    css = '#m{font-family:"trebuchet ms",verdana,arial;fill:#333}.a text{font-family: x}'
    out = passed(doc(f"<style>{css}</style>"))
    assert find_all(out, "style")[0].text == (
        '#m{font-family:"trebuchet ms",verdana,arial, sans-serif;fill:#333}'
        ".a text{font-family: x, sans-serif}"
    )


def test_font_shorthand_gets_a_generic() -> None:
    out = passed(doc('<text font="12px Arial" style="font: bold 10px serif">a</text>'))
    text = find_all(out, "text")[0]
    assert text.get("font") == "12px Arial, sans-serif"
    assert text.get("style") == "font: bold 10px serif"


# Step 8: references to what is not there -----------------------------------------------

SHADOW = '<defs><filter id="merman-drop-shadow"><feDropShadow dx="2" dy="2"/></filter></defs>'


def test_dangling_filter_attribute_is_removed_and_the_shape_kept() -> None:
    out = passed(doc(SHADOW + '<rect filter="url(#merman-drop-shadow)" width="5"/>'))
    assert find_all(out, "rect")[0].attrib == {"width": "5"}


def test_dangling_filter_in_a_style_attribute_drops_that_declaration() -> None:
    out = passed(doc(SHADOW + '<g style="filter:url(#merman-drop-shadow);opacity:1"><rect/></g>'))
    assert find_all(out, "g")[0].get("style") == "opacity:1"
    assert len(find_all(out, "rect")) == 1


def test_dangling_filter_in_a_style_element_drops_that_declaration() -> None:
    out = passed(doc(SHADOW + "<style>.node{filter:url(#merman-drop-shadow);fill:#eee}</style>"))
    assert find_all(out, "style")[0].text == ".node{;fill:#eee}"


@pytest.mark.parametrize("name", ["clip-path", "mask", "marker-end", "filter"])
def test_dangling_non_paint_reference_attribute_is_removed(name: str) -> None:
    out = passed(doc(f'<path d="M0 0" {name}="url(#gone)"/>'))
    assert find_all(out, "path")[0].attrib == {"d": "M0 0"}


def test_dangling_fill_becomes_none() -> None:
    out = passed(doc('<rect fill="url(#gone)"/>'))
    assert find_all(out, "rect")[0].get("fill") == "none"


def test_dangling_stroke_keeps_its_fallback() -> None:
    out = passed(doc('<rect stroke="url(#gone) blue"/>'))
    assert find_all(out, "rect")[0].get("stroke") == "blue"


@pytest.mark.parametrize(
    ("value", "painted"),
    [
        ("url(#gone) foo", "none"),
        ("url(#gone) foo bar", "none"),
        ("url(#gone) hsl(120deg, 50%, 50%)", "none"),
        ("url(#gone) rgb(1, 2, 3)", "rgb(1, 2, 3)"),
        ("url(#gone) rebeccapurple", "#663399"),
    ],
)
def test_a_dangling_paint_keeps_its_fallback_only_if_it_is_a_colour(
    value: str, painted: str
) -> None:
    out = passed(doc(f'<rect fill="{value}" style="stroke:{value}"/>'))
    rect = find_all(out, "rect")[0]
    assert (rect.get("fill"), rect.get("style")) == (painted, f"stroke: {painted}")


# What `_colour_or_none` keeps. Every one is drawn by resvg below, so the grammar is held
# to what the renderer paints and not only to what CSS allows.
COLOURS = [
    "red",
    " Red ",
    "GREY",
    "currentColor",
    "currentcolor",
    "none",
    "#e85",
    "#E85F",
    "#e8590c",
    "#e8590c80",
    "rgb(1,2,3)",
    "RGB( 1 , 2 , 3 )",
    "rgb(1 2 3)",
    "rgba(1,2,3,0.5)",
    "rgb(1,2,3,.5)",
    "rgb(10%,20%,30%)",
    "rgb(+1.5,2e1,-3)",
    "hsl(120,50%,50%)",
    "hsla(120,50%,50%,0.5)",
    "hsl(-120 50% 50%)",
]
NOT_COLOURS = [
    "",
    "foo",
    "red red",
    "red icc-color(x)",
    "NONE",
    "inherit",
    "transparent",
    "#e8590",
    "#e8590c8",
    "#ggg",
    "rgb(1,2)",
    "rgb(1,2,3,4,5)",
    "rgb(1,2,3,)",
    "rgb(,1,2,3)",
    "rgb(1, 2 3)",
    "rgb(1,2,3%)",
    "rgba(1,2,3,50%)",
    "rgb(1 2 3 / 0.5)",
    "rgb (1,2,3)",
    "rgb(1,2,3",
    "rgb(1,2,(3))",
    "hsl(120deg,50%,50%)",
    "hsl(120,50,50)",
    "hsl(120%,50%,50%)",
    "lab(50 0 0)",
    "url(#a)",
]


@pytest.mark.parametrize("colour", COLOURS)
def test_a_colour_is_kept_as_written(colour: str) -> None:
    from pptmstr.figures.sanitise import _colour_or_none

    assert _colour_or_none(colour) == colour.strip()


@pytest.mark.parametrize("text", NOT_COLOURS)
def test_anything_else_is_none(text: str) -> None:
    from pptmstr.figures.sanitise import _colour_or_none

    assert _colour_or_none(text) == "none"


def test_every_kept_colour_is_one_resvg_paints() -> None:
    from pptmstr.figures.sanitise import _colour_or_none

    # currentColor is black here because no `color` is set, which is what it means.
    black = (0, 0, 0, 255)
    names = sorted(NAMED_COLOURS - {"black"})
    for colour in [*names, *(c for c in COLOURS if c.strip().lower() != "currentcolor")]:
        kept = _colour_or_none(colour)
        svg = doc(
            '<rect width="200" height="100" fill="#fff"/>'
            f'<rect width="200" height="100" fill="{kept}"/>'
        )
        assert render_pixel(svg) != black, (colour, kept)
    colour_set = doc('<rect width="200" height="100" color="#e8590c" fill="currentColor"/>')
    assert render_pixel(colour_set) == (0xE8, 0x59, 0x0C, 255)


@pytest.mark.parametrize(
    "body",
    [
        'fill="url(#gone) foo"',
        'fill="url(#gone) foo bar"',
        'fill="url(http://x) foo"',
        'style="fill:url(#gone) foo"',
        'style="fill:url(http://x) foo bar"',
        'class="a"',
    ],
)
def test_an_invalid_fallback_is_not_painted_black(body: str) -> None:
    svg = doc(
        "<style>.a{fill:url(#gone) rgba(1,2,3,50%)}</style>"
        f'<rect width="200" height="100" fill="#fff"/><rect width="200" height="100" {body}/>'
    )
    assert render_pixel(passed(svg).svg) == (255, 255, 255, 255)


def test_dangling_paint_in_css_becomes_its_fallback_or_none() -> None:
    out = passed(
        doc(
            "<style>.a{fill:url(#gone) red}</style>"
            '<rect style="stroke:url(#gone) !important; fill:url(#gone)"/>'
        )
    )
    assert find_all(out, "style")[0].text == ".a{fill: red}"
    assert find_all(out, "rect")[0].get("style") == "stroke: none !important; fill: none"


def test_a_repainted_dangling_paint_is_noted() -> None:
    out = passed(
        doc(
            '<defs><rect id="r"/></defs>'
            "<style>.a{fill:url(#gone) red}</style>"
            '<rect fill="url(#gone)" stroke="url(#r) blue" clip-path="url(#gone)"'
            ' style="Stroke:url(#gone2) foo"/>'
        )
    )
    # Removing a clip-path draws the shape without it, so only repaints are noted.
    assert out.notes == (
        "fill: url(#gone) names nothing in the output; painted red",
        "fill: url(#gone) names nothing in the output; painted none",
        "stroke: url(#r) is not a paint server; painted blue",
        "Stroke: url(#gone2) names nothing in the output; painted none",
    )


def test_a_paint_that_resolves_is_not_noted() -> None:
    out = passed(doc('<linearGradient id="g"/><rect fill="url(#g) red" stroke="red"/>'))
    assert out.notes == ()


# rebeccapurple is the one CSS named colour resvg_py 0.5.0 paints black (measured
# 2026-10-21), so it is spelled #663399 in every value that can hold a colour.
REBECCA = (0x66, 0x33, 0x99, 255)
FULL = 'width="200" height="100"'


@pytest.mark.parametrize(
    "body",
    [
        f'<rect {FULL} fill="rebeccapurple"/>',
        f'<rect {FULL} fill="RebeccaPurple"/>',
        f'<rect {FULL} style="fill: REBECCAPURPLE !important"/>',
        f'<style>.a{{fill:rebeccapurple}}</style><rect class="a" {FULL}/>',
        f'<rect {FULL} fill="none" stroke="rebeccapurple" stroke-width="400"/>',
        f'<style>.a{{stroke:rebeccapurple}}</style><rect class="a" {FULL} stroke-width="400"/>',
        '<linearGradient id="g"><stop stop-color="rebeccapurple"/></linearGradient>'
        f'<rect {FULL} fill="url(#g)"/>',
        '<linearGradient id="g"><stop style="stop-color:rebeccapurple"/></linearGradient>'
        f'<rect {FULL} fill="url(#g)"/>',
        "<style>stop{stop-color:rebeccapurple}</style>"
        f'<linearGradient id="g"><stop/></linearGradient><rect {FULL} fill="url(#g)"/>',
        f'<g color="rebeccapurple"><rect {FULL} fill="currentColor"/></g>',
        f'<rect {FULL} style="color:rebeccapurple" fill="currentColor"/>',
        f'<g style="--c:rebeccapurple"><rect {FULL} fill="var(--c)"/></g>',
        f'<g style="--c:rebeccapurple"><rect {FULL} style="fill:var(--c)"/></g>',
        f'<rect {FULL} fill="url(#gone) rebeccapurple"/>',
        f'<rect {FULL} fill="url(http://x) rebeccapurple"/>',
    ],
)
def test_rebeccapurple_is_painted_in_every_colour_value(body: str) -> None:
    svg = doc(f'<rect {FULL} fill="#fff"/>{body}')
    assert render_pixel(passed(svg).svg) == REBECCA
    assert "rebeccapurple" not in passed(svg).svg.lower()


def test_rebeccapurple_is_spelled_only_as_a_whole_colour_name() -> None:
    out = passed(
        doc(
            "<style>.rebeccapurple{fill:rebeccapurple2; stroke:x-rebeccapurple}</style>"
            '<rect class="rebeccapurple" font-family="rebeccapurple, serif"'
            ' fill="rgb(1,2,3) rebeccapurple"/>'
        )
    )
    assert find_all(out, "style")[0].text == (
        ".rebeccapurple{fill:rebeccapurple2; stroke:x-rebeccapurple}"
    )
    rect = find_all(out, "rect")[0]
    assert rect.get("class") == "rebeccapurple"
    assert rect.get("font-family") == "rebeccapurple, serif"
    assert rect.get("fill") == "rgb(1,2,3) #663399"


def test_a_filter_reference_to_a_kept_element_is_removed() -> None:
    # resvg erases a shape whose filter names a <rect> (measured), and no <filter>
    # survives, so every filter reference is removed.
    out = passed(doc('<rect id="someRect"/><rect filter="url(#someRect)" width="5"/>'))
    assert find_all(out, "rect")[1].attrib == {"width": "5"}
    out = passed(doc('<rect id="a"/><g style="filter:url(#a);opacity:1"><rect/></g>'))
    assert find_all(out, "g")[0].get("style") == "opacity:1"


WRONG_KIND = (
    '<defs><rect id="r"/><linearGradient id="g"/><pattern id="p"/><clipPath id="c"/>'
    '<mask id="m"/><marker id="k"/></defs>'
)


@pytest.mark.parametrize(
    "attribute, right, wrong",
    [
        ("clip-path", "c", ["r", "m", "g"]),
        ("mask", "m", ["r", "c"]),
        ("marker-start", "k", ["r", "c"]),
        ("marker-mid", "k", ["r"]),
        ("marker-end", "k", ["r", "g"]),
    ],
)
def test_a_reference_to_the_wrong_kind_of_element_is_dangling(
    attribute: str, right: str, wrong: list[str]
) -> None:
    out = passed(doc(WRONG_KIND + f'<path d="M0 0" {attribute}="url(#{right})"/>'))
    assert find_all(out, "path")[0].get(attribute) == f"url(#{right})"
    for target in wrong:
        out = passed(doc(WRONG_KIND + f'<path d="M0 0" {attribute}="url(#{target})"/>'))
        assert find_all(out, "path")[0].attrib == {"d": "M0 0"}, target


def test_paint_naming_something_other_than_a_paint_server_takes_its_fallback() -> None:
    out = passed(
        doc(
            WRONG_KIND + '<rect fill="url(#r) green" stroke="url(#c)"/>'
            '<rect fill="url(#g)" stroke="url(#p) red"/>'
            '<rect style="fill:url(#m) blue"/>'
        )
    )
    first, second, third = find_all(out, "rect")[1:]
    assert (first.get("fill"), first.get("stroke")) == ("green", "none")
    # <pattern> does not survive, so a stroke naming one takes its fallback.
    assert (second.get("fill"), second.get("stroke")) == ("url(#g)", "red")
    assert third.get("style") == "fill: blue"


def test_an_id_on_two_kinds_of_element_resolves_only_for_a_property_both_suit() -> None:
    out = passed(doc('<clipPath id="d"/><rect id="d"/><path clip-path="url(#d)"/>'))
    assert "clip-path" not in find_all(out, "path")[0].attrib


def test_a_kept_plain_href_wins_over_xlink_and_a_dropped_one_does_not() -> None:
    # HEAD declares the xlink prefix.
    out = passed(doc('<g id="a"/><g id="b"/><use xlink:href="#a" href="#b"/>'))
    assert find_all(out, "use")[0].get("href") == "#b"
    out = passed(doc('<g id="a"/><g id="b"/><use href="#b" xlink:href="#a"/>'))
    assert find_all(out, "use")[0].get("href") == "#b"
    out = passed(doc('<g id="a"/><use href="http://x" xlink:href="#a"/>'))
    assert find_all(out, "use")[0].get("href") == "#a"


def test_dangling_href_is_removed() -> None:
    out = passed(doc('<use href="#gone" x="1"/><textPath href="#gone">a</textPath>'))
    assert find_all(out, "use")[0].attrib == {"x": "1"}
    assert find_all(out, "textPath")[0].attrib == {}


def test_a_reference_to_a_dropped_element_is_dangling() -> None:
    out = passed(doc('<image id="i"/><use href="#i"/><rect fill="url(#i)"/>'))
    assert find_all(out, "use")[0].attrib == {}
    assert find_all(out, "rect")[0].get("fill") == "none"


def test_references_to_kept_ids_are_untouched() -> None:
    out = passed(
        doc(
            '<defs><linearGradient id="g"/><clipPath id="c"><rect/></clipPath></defs>'
            '<rect fill="url(#g) red" clip-path="url(\'#c\')" style="stroke:url(#g)"/>'
            '<use href="#c"/>'
        )
    )
    rect = find_all(out, "rect")[1]
    assert rect.attrib == {
        "fill": "url(#g) red",
        "clip-path": "url('#c')",
        "style": "stroke:url(#g)",
    }
    assert find_all(out, "use")[0].get("href") == "#c"


# Step 9: custom properties ------------------------------------------------------------

# The case measured on 2026-10-21: resvg paints `fill:var(--c)` black.
DATAVIZ = doc(
    "<style>.viz{--c:#e8590c} .a{fill:var(--c)}</style>"
    '<g class="viz"><rect class="a" width="200" height="100"/></g>'
)


def test_a_custom_property_in_a_stylesheet_is_substituted_and_its_declaration_removed() -> None:
    out = passed(DATAVIZ)
    style = find_all(out, "style")[0].text or ""
    assert ".a{fill:#e8590c}" in style
    assert "var(" not in out.svg and "--c" not in out.svg
    assert out.notes == ()


def test_the_substituted_colour_is_what_resvg_paints() -> None:
    resvg_py = pytest.importorskip("resvg_py")
    image = pytest.importorskip("PIL.Image")
    import io

    png = resvg_py.svg_to_bytes(svg_string=passed(DATAVIZ).svg, width=200)
    with image.open(io.BytesIO(bytes(png))) as pixels:
        assert pixels.convert("RGBA").getpixel((100, 50)) == (0xE8, 0x59, 0x0C, 255)


def test_a_var_with_a_fallback_takes_it_when_undeclared() -> None:
    out = passed(doc('<rect fill="var(--gone, #123456)" style="stroke: var(--gone,red)"/>'))
    rect = find_all(out, "rect")[0]
    assert rect.attrib == {"fill": "#123456", "style": "stroke: red"}
    assert out.notes == ()


def test_a_declared_var_ignores_its_fallback() -> None:
    out = passed(doc('<g style="--c:blue"><rect fill="var(--c, red)"/></g>'))
    assert find_all(out, "rect")[0].get("fill") == "blue"
    assert find_all(out, "g")[0].get("style") is None


def test_nested_vars_resolve_through_declarations_and_fallbacks() -> None:
    out = passed(
        doc(
            "<style>:root{--base:#0a0b0c; --role:var(--base)}</style>"
            '<rect fill="var(--role)" stroke="var(--none, var(--base))"/>'
        )
    )
    rect = find_all(out, "rect")[0]
    assert (rect.get("fill"), rect.get("stroke")) == ("#0a0b0c", "#0a0b0c")


def test_the_last_declaration_wins_whatever_its_selector() -> None:
    # The cascade is not modelled: one document-wide table.
    out = passed(doc('<style>.a{--c:red} .b{--c:blue}</style><rect class="a" fill="var(--c)"/>'))
    assert find_all(out, "rect")[0].get("fill") == "blue"


def test_a_var_nested_past_the_depth_bound_fails() -> None:
    chain = (
        "".join(f"--v{i}:var(--v{i + 1});" for i in range(MAX_VAR_DEPTH))
        + f"--v{MAX_VAR_DEPTH}:red"
    )
    out = passed(doc(f'<g style="{chain}"><rect fill="var(--v1)"/><rect fill="var(--v0)"/></g>'))
    shallow, deep = find_all(out, "rect")
    assert shallow.get("fill") == "red"
    assert deep.get("fill") == "none"
    assert any(f"nested deeper than {MAX_VAR_DEPTH}" in n for n in out.notes)


def test_a_cycle_fails_its_declaration_and_leaves_the_rest() -> None:
    out = passed(
        doc('<g style="--a:var(--b);--b:var(--a)"><rect style="fill:var(--a);stroke:blue"/></g>')
    )
    assert find_all(out, "rect")[0].get("style") == "fill:none; stroke:blue"
    assert any("cycle" in n for n in out.notes)


def test_a_var_naming_itself_is_a_cycle() -> None:
    out = passed(doc('<g style="--a:var(--a)"><rect fill="var(--a)"/></g>'))
    assert find_all(out, "rect")[0].get("fill") == "none"


def test_an_undeclared_var_in_fill_or_stroke_becomes_none() -> None:
    out = passed(doc('<rect fill="var(--x)" stroke="var(--y)"/>'))
    rect = find_all(out, "rect")[0]
    assert (rect.get("fill"), rect.get("stroke")) == ("none", "none")
    assert out.notes == (
        "fill: var(--x) is not declared; painted none",
        "stroke: var(--y) is not declared; painted none",
    )


def test_an_undeclared_var_in_css_paint_becomes_none() -> None:
    out = passed(doc('<style>.a{fill:var(--x);stroke-width:var(--w)}</style><rect class="a"/>'))
    assert find_all(out, "style")[0].text == ".a{fill:none;}"


def test_a_var_resolving_to_nothing_fails() -> None:
    # An empty fallback is legal CSS, and an empty fill paints black.
    out = passed(doc('<rect fill="var(--x,)" stroke="var(--x, )" opacity="var(--x,)"/>'))
    assert find_all(out, "rect")[0].attrib == {"fill": "none", "stroke": "none"}


def test_a_var_naming_something_other_than_a_custom_property_fails() -> None:
    out = passed(doc('<rect fill="var(red, blue)" stroke="var(--, blue)"/>'))
    rect = find_all(out, "rect")[0]
    assert (rect.get("fill"), rect.get("stroke")) == ("none", "none")


def test_an_unresolved_var_elsewhere_removes_the_attribute() -> None:
    out = passed(
        doc('<g style="--o:0.5"><rect width="var(--w)" opacity="var(--o" height="1"/></g>')
    )
    assert find_all(out, "rect")[0].attrib == {"height": "1"}


@pytest.mark.parametrize(
    "declared",
    ["url(http://x)", "url('http://x')", "u\\72l(http://x)", "image('x.png')"],
)
def test_a_var_resolving_to_a_url_is_dropped(declared: str) -> None:
    out = passed(
        doc(
            f"<style>:root{{--u:{declared}}} .a{{fill:var(--u)}}</style>"
            f'<g style="--v:{quoted(declared)}">'
            '<rect class="a" fill="var(--v)" mask="var(--v)"/></g>'
        )
    )
    assert "//x" not in out.svg and "image(" not in out.svg and "72l" not in out.svg
    assert find_all(out, "style")[0].text == ":root{} .a{fill:none}"
    assert find_all(out, "rect")[0].attrib == {"class": "a", "fill": "none"}


def test_a_url_assembled_from_two_vars_is_dropped() -> None:
    out = passed(doc('<g style="--a:ur;--b:l(http://x)"><rect fill="var(--a)var(--b)"/></g>'))
    assert find_all(out, "rect")[0].get("fill") == "none"


def test_a_fragment_url_through_a_var_is_kept_and_still_resolved() -> None:
    out = passed(
        doc(
            '<defs><linearGradient id="g"/></defs>'
            '<g style="--p:url(#g);--q:url(#gone) red">'
            '<rect fill="var(--p)"/><rect fill="var(--q)"/></g>'
        )
    )
    assert [r.get("fill") for r in find_all(out, "rect")] == ["url(#g)", "red"]


@pytest.mark.parametrize(
    "body",
    [
        f'<g style="--c: foo"><rect {FULL} style="fill: var(--c)"/></g>',
        f'<g style="--c: foo"><rect {FULL} fill="var(--c)"/></g>',
        f'<style>:root{{--c:1px solid}} .a{{fill:var(--c)}}</style><rect class="a" {FULL}/>',
        f'<rect {FULL} fill="var(--gone, foo bar)"/>',
        f'<g style="--c: NONE"><rect {FULL} fill="var(--c)"/></g>',
        f'<g style="--c: hsl(120deg, 50%, 50%)"><rect {FULL} fill="var(--c)"/></g>',
    ],
)
def test_a_var_resolving_to_a_non_colour_paint_is_not_painted_black(body: str) -> None:
    out = passed(doc(f'<rect {FULL} fill="#fff"/>{body}'))
    assert render_pixel(out.svg) == (255, 255, 255, 255)
    assert len(out.notes) == 1 and "not a colour; painted none" in out.notes[0]


def test_a_var_paint_that_is_not_a_colour_is_noted_and_keeps_important() -> None:
    out = passed(doc('<g style="--c:foo"><rect style="stroke: var(--c) !important"/></g>'))
    assert find_all(out, "rect")[0].get("style") == "stroke: none !important"
    assert out.notes == ("stroke: var() resolves to 'foo', not a colour; painted none",)


def test_a_var_paint_that_is_a_colour_inherit_or_a_url_is_kept_as_written() -> None:
    out = passed(
        doc(
            '<linearGradient id="g"/>'
            '<g style="--a: url(#g) foo; --b: inherit; --c: rgb(1 2 3)">'
            '<rect fill="var(--a)" stroke="var(--b)" style="fill: var(--c) !important"/></g>'
        )
    )
    rect = find_all(out, "rect")[0]
    assert (rect.get("fill"), rect.get("stroke")) == ("url(#g) foo", "inherit")
    assert rect.get("style") == "fill: rgb(1 2 3) !important"
    assert out.notes == ()


def test_a_var_paint_of_inherit_inherits() -> None:
    svg = doc(f'<g fill="#e8590c" style="--c:inherit"><rect {FULL} fill="var(--c)"/></g>')
    assert render_pixel(passed(svg).svg) == (0xE8, 0x59, 0x0C, 255)


def test_a_var_outside_a_paint_is_not_held_to_a_colour() -> None:
    out = passed(doc('<g style="--c:foo"><text font-family="var(--c)">x</text></g>'))
    assert find_all(out, "text")[0].get("font-family") == "foo, sans-serif"


def test_a_var_in_a_selector_empties_the_stylesheet() -> None:
    out = passed(doc("<style>.a:not(var(--x)){fill:red}</style><rect/>"))
    assert not find_all(out, "style")[0].text


def test_no_var_survives_in_any_value() -> None:
    out = passed(
        doc(
            '<style>.a{fill:VAR(--c)} .b{font-family:"var(--c)"}</style>'
            '<text style="font:var(--f, 9px serif)" x="var(--c)">var(--c)</text>'
        )
    )
    values = [*tree(out).iter()]
    assert all("var(" not in v.lower() for e in values for v in e.attrib.values())
    assert "var(" not in (find_all(out, "style")[0].text or "").lower()
    # Text content is not a value.
    assert find_all(out, "text")[0].text == "var(--c)"


def test_a_var_that_fans_out_past_the_byte_cap_is_refused() -> None:
    # Each name naming the one before ten times, as deep as MAX_VAR_DEPTH allows:
    # 10^8 characters if expanded.
    names = [f"--a{i}:" + f"var(--a{i - 1})" * 10 for i in range(1, MAX_VAR_DEPTH)]
    style = ";".join(["--a0:#000000000", *names])
    reason = refused(doc(f'<g style="{style}"><rect fill="var(--a{MAX_VAR_DEPTH - 1})"/></g>'))
    assert "var() substitution" in reason


def test_a_var_that_grows_the_document_past_the_cap_is_refused() -> None:
    # Within the scanning budget, but the document is already large: 600 KiB of text
    # and 100 KiB declared once, used five times. Not in a paint, which would be `none`
    # for not being a colour.
    big = "#" + "0" * (100 * 1024)
    uses = '<rect stroke-dasharray="var(--c)"/>' * 5
    padding = "<desc>" + "x" * (600 * 1024) + "</desc>"
    reason = refused(doc(f'{padding}<g style="--c:{big}">{uses}</g>'))
    assert reason == f"var() substitution makes it larger than {MAX_INPUT_BYTES} bytes"


def test_distinct_var_values_past_the_cap_are_refused_and_repeats_are_free() -> None:
    def rules(n: int) -> str:
        return "".join(f"a{{fill:var(--c,#{i:06x})}}" for i in range(n))

    passed(doc(f"<style>{rules(MAX_VAR_VALUES)}{rules(1) * 10_000}</style>"))
    reason = refused(doc(f"<style>{rules(MAX_VAR_VALUES + 1)}</style>"))
    assert reason == f"more than {MAX_VAR_VALUES} distinct values that use var()"


def test_notes_stop_at_the_cap_and_say_there_are_more() -> None:
    out = passed(doc("".join(f'<rect fill="var(--x{i})"/>' for i in range(MAX_NOTES + 5))))
    assert len(out.notes) == MAX_NOTES + 1
    assert out.notes[0] == "fill: var(--x0) is not declared; painted none"
    assert out.notes[-1] == "and more var() that could not be resolved"


def test_sanitising_substituted_output_twice_changes_nothing_more() -> None:
    once = passed(DATAVIZ)
    again = passed(once.svg)
    assert again.svg == once.svg


# Round trip ---------------------------------------------------------------------------

MERMAID_LIKE = """<svg id="m1" width="100%" xmlns="http://www.w3.org/2000/svg"
 xmlns:xlink="http://www.w3.org/1999/xlink" class="flowchart" style="max-width: 205px;"
 viewBox="-8 -8 205 174" role="graphics-document document" aria-roledescription="flowchart-v2">
<style>#m1{font-family:"trebuchet ms",verdana,arial,sans-serif;font-size:16px;fill:#333;}
#m1 .node rect{fill:#ECECFF;stroke:#9370DB;stroke-width:1px;}
#m1 .marker{fill:#333333;stroke:#333333;}</style>
<g><marker id="m1_end" class="marker" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="8"
 markerHeight="8" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" class="arrowMarkerPath"/></marker>
<g class="root"><g class="edgePaths"><path d="M94.5,47L94.5,90" id="L_A_B_0" class="flowchart-link"
 marker-end="url(#m1_end)"/></g>
<g class="nodes">
<g class="node default" id="flowchart-A-0" transform="translate(94.5, 23.5)">
<rect class="basic label-container" x="-50" y="-23.5" width="100" height="47"/>
<g class="label" transform="translate(-25, -11.5)"><rect/><foreignObject width="50" height="23">
<div xmlns="http://www.w3.org/1999/xhtml" style="display: table-cell; white-space: nowrap;">
<span class="nodeLabel"><p>Start</p></span></div></foreignObject></g></g>
<g class="node default" id="flowchart-B-1" transform="translate(94.5, 134.5)">
<rect class="basic label-container" x="-94.5" y="-23.5" width="189" height="47"/>
<g class="label" transform="translate(-69.5, -11.5)"><rect/><foreignObject width="139" height="23">
<div xmlns="http://www.w3.org/1999/xhtml"><span class="nodeLabel"><p>Done, at last</p></span>
</div></foreignObject></g></g></g></g></g></svg>"""


def test_a_benign_mermaid_like_svg_round_trips_with_shapes_and_text() -> None:
    out = passed(MERMAID_LIKE)
    assert (out.width, out.height) == (205, 174)
    assert len(find_all(out, "rect")) == 4
    assert len(find_all(out, "path")) == 2
    assert [t.text for t in find_all(out, "text")] == ["Start", "Done, at last"]
    assert find_all(out, "path")[1].get("marker-end") == "url(#m1_end)"
    assert find_all(out, "marker")[0].get("orient") == "auto"
    nodes = [g for g in find_all(out, "g") if "node" in (g.get("class") or "").split()]
    assert [g.get("transform") for g in nodes] == [
        "translate(94.5, 23.5)",
        "translate(94.5, 134.5)",
    ]
    style = find_all(out, "style")[0].text or ""
    assert "#m1 .node rect{fill:#ECECFF" in style
    assert tree(out).get("aria-roledescription") == "flowchart-v2"


def test_sanitising_twice_changes_nothing() -> None:
    once = passed(MERMAID_LIKE)
    assert passed(once.svg) == once


# Totality -----------------------------------------------------------------------------


def _mutate(rng: random.Random, seed: str) -> str:
    chars = list(seed)
    for _ in range(rng.randint(1, 4)):
        op = rng.randrange(4)
        at = rng.randrange(len(chars) + 1)
        if op == 0 and chars:
            del chars[min(at, len(chars) - 1)]
        elif op == 1:
            chars.insert(at, rng.choice("<>/\"'=&;#!?-[]{}()\\: xX\x00é\ud800"))
        elif op == 2:
            piece = rng.choice(["<!DOCTYPE", "url(", "&x;", "<use href='#a'/>", "]]>", "<g>"])
            chars[at:at] = piece
        else:
            other = rng.randrange(len(chars) + 1)
            chars[at:other] = chars[at:other][::-1]
    return "".join(chars)


def test_no_input_raises_and_no_input_meets_the_internal_error_arm() -> None:
    rng = random.Random(20261020)
    seeds = [MERMAID_LIKE, doc('<rect style="fill:url(#a)"/><text font="9px x">a</text>')]
    inputs: list[str] = []
    for _ in range(200):
        raw = bytes(rng.randrange(256) for _ in range(rng.randint(0, 200)))
        inputs.append(raw.decode("utf-8", "surrogateescape"))
    for _ in range(400):
        inputs.append(_mutate(rng, rng.choice(seeds)))
    passed_count = 0
    for svg in inputs:
        out = sanitise(svg)
        if isinstance(out, Refused):
            assert not out.reason.startswith("internal error"), (out, svg)
        else:
            assert_safe(out.svg)
            passed_count += 1
    # The corpus reaches the full pipeline, not only the parser's refusal.
    assert passed_count > 20
