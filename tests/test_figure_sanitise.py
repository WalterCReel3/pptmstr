"""
The SVG sanitiser: each hostile case by name, the natural-size rule, and totality.
"""

from __future__ import annotations

import random
import re
import time
import xml.etree.ElementTree as ET

import pytest

from pptmstr.figures.sanitise import (
    ALLOWED_ELEMENTS,
    MAX_DEPTH,
    MAX_ELEMENTS,
    MAX_INPUT_BYTES,
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
    start = time.perf_counter()
    reason = refused(laughs)
    assert time.perf_counter() - start < 0.5
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
    assert (text.get("class"), text.get("transform"), text.get("style")) == ("c", None, "color:red")


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
    "fill",
    "stroke",
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


@pytest.mark.parametrize("value", ["url(#a)", "url('#a')", 'url( "#a" )', "URL(#a) none"])
def test_fragment_url_in_a_presentation_attribute_is_kept(value: str) -> None:
    out = passed(doc(f'<linearGradient id="a"/><path fill="{quoted(value)}"/>'))
    assert find_all(out, "path")[0].get("fill") == value


def test_url_taking_functions_are_dropped() -> None:
    out = passed(doc("<rect fill=\"image-set('x.png' 1x)\" stroke=\"image('y.png')\"/>"))
    assert find_all(out, "rect")[0].attrib == {}


def test_backslash_in_an_attribute_drops_it() -> None:
    out = passed(doc(r'<rect fill="u\72l(http://e/)" width="1"/>'))
    assert find_all(out, "rect")[0].attrib == {"width": "1"}


def test_other_namespaced_attributes_are_dropped_and_xml_space_kept() -> None:
    out = passed(
        doc('<text xml:space="preserve" xml:base="http://e/" xlink:title="t" x="1">a</text>')
    )
    assert find_all(out, "text")[0].attrib == {f"{{{XML_NS}}}space": "preserve", "x": "1"}


# Step 4: CSS --------------------------------------------------------------------------


def test_css_url_in_a_style_element_drops_the_element() -> None:
    out = passed(doc("<style>rect{fill:url(http://e/x.svg#p)}</style><rect/>"))
    assert find_all(out, "style") == []


def test_css_escaped_url_in_a_style_element_drops_the_element() -> None:
    out = passed(doc(r"<style>rect{fill:u\72l(http://e/x)}</style><rect/>"))
    assert find_all(out, "style") == []


def test_css_import_drops_the_style_element() -> None:
    out = passed(doc('<style>@IMPORT "http://e/x.css"; rect{fill:red}</style>'))
    assert find_all(out, "style") == []


def test_css_font_face_drops_the_style_element() -> None:
    out = passed(doc('<style>@font-face{font-family:x;src:local("y")}</style>'))
    assert find_all(out, "style") == []


def test_css_fragment_url_in_a_style_element_is_kept() -> None:
    out = passed(doc('<marker id="arrow"/><style>.e path{marker-end:url(#arrow)}</style>'))
    assert find_all(out, "style")[0].text == ".e path{marker-end:url(#arrow)}"


def test_css_url_in_a_style_attribute_drops_only_that_declaration() -> None:
    out = passed(doc('<rect style="fill:url(file:///etc/x);stroke:red;opacity:0.5"/>'))
    assert find_all(out, "rect")[0].get("style") == "stroke:red; opacity:0.5"


def test_css_escaped_url_in_a_style_attribute_drops_that_declaration() -> None:
    out = passed(doc(r'<rect style="stroke:blue; fill:u\72l(http://e/x)"/>'))
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
    out = passed(doc('<rect style="fill:url(http://e/)" width="1"/>'))
    assert find_all(out, "rect")[0].attrib == {"width": "1"}


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
    start = time.perf_counter()
    assert "expansion" in refused(svg)
    assert time.perf_counter() - start < 0.5


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


def test_dangling_paint_in_css_becomes_its_fallback_or_none() -> None:
    out = passed(
        doc(
            "<style>.a{fill:url(#gone) red}</style>"
            '<rect style="stroke:url(#gone) !important; fill:url(#gone)"/>'
        )
    )
    assert find_all(out, "style")[0].text == ".a{fill: red}"
    assert find_all(out, "rect")[0].get("style") == "stroke: none !important; fill: none"


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
