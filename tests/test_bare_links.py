"""
A bare ``http(s)://`` address in markdown is a link (``gate._bare_addresses``).

Every test reads through the seam a pane draws with -- ``typeset.markdown`` or
``typeset.stream`` in a frame, on the parser ``gate._reader`` put pptmstr's rules
on -- and reads back what the view was given to set (``texts``) and where its
links go (``links``).
"""

from __future__ import annotations

from typing import Any

import pytest

from pptmstr.ui import typeset
from tests.test_typeset_views import _view, frame


def _linked(view: Any) -> list[tuple[str, str]]:
    """
    Each link as the characters it covers and where it goes.
    """
    return [(view.texts[link.block][link.start : link.end], link.target) for link in view.links]


@pytest.mark.parametrize(
    ("source", "linked"),
    [
        ("see https://x.org now", [("https://x.org", "https://x.org")]),
        ("see http://x.org now", [("http://x.org", "http://x.org")]),
        (
            "go to https://x.org/a/b?q=1&r=2#frag then",
            [("https://x.org/a/b?q=1&r=2#frag", "https://x.org/a/b?q=1&r=2#frag")],
        ),
        ("(see https://x.org/a)", [("https://x.org/a", "https://x.org/a")]),
        ("It is at https://x.org.", [("https://x.org", "https://x.org")]),
        ("Is it https://x.org/a?", [("https://x.org/a", "https://x.org/a")]),
        ("'https://x.org/a'", [("https://x.org/a", "https://x.org/a")]),
        (
            "Read https://en.wikipedia.org/wiki/Foo_(bar) first",
            [
                (
                    "https://en.wikipedia.org/wiki/Foo_(bar)",
                    "https://en.wikipedia.org/wiki/Foo_(bar)",
                )
            ],
        ),
        (
            "(https://en.wikipedia.org/wiki/Foo_(bar))",
            [
                (
                    "https://en.wikipedia.org/wiki/Foo_(bar)",
                    "https://en.wikipedia.org/wiki/Foo_(bar)",
                )
            ],
        ),
        ("at https://x.org/s?q=a&hl; ok", [("https://x.org/s?q=a", "https://x.org/s?q=a")]),
        ("at https://x.org/a; ok", [("https://x.org/a", "https://x.org/a")]),
        ("at http://localhost:8000/x ok", [("http://localhost:8000/x", "http://localhost:8000/x")]),
        ("a https://x.org<b>", [("https://x.org", "https://x.org")]),
        ('<a href="https://x.org/">', [("https://x.org/", "https://x.org/")]),
        (
            "https://a.org and https://b.org",
            [("https://a.org", "https://a.org"), ("https://b.org", "https://b.org")],
        ),
        ("- one\n- see https://x.org/l\n", [("https://x.org/l", "https://x.org/l")]),
        ("# Heading https://x.org/h\n", [("https://x.org/h", "https://x.org/h")]),
        ("> quoted https://x.org/q\n", [("https://x.org/q", "https://x.org/q")]),
        ("**https://x.org/b**", [("https://x.org/b", "https://x.org/b")]),
        ("詳細はhttps://x.org/a。次", [("https://x.org/a", "https://x.org/a")]),
        ("「https://x.org/a」を見て", [("https://x.org/a", "https://x.org/a")]),
        ("参照（https://x.org/a）", [("https://x.org/a", "https://x.org/a")]),
        (
            "https://x.org/a、https://y.org/b",
            [
                ("https://x.org/a", "https://x.org/a"),
                ("https://y.org/b", "https://y.org/b"),
            ],
        ),
        ("https://x.org/a！", [("https://x.org/a", "https://x.org/a")]),
        (
            "[https://x.org/a]",
            [("https://x.org/a] (https://x.org/a%5D)", "https://x.org/a%5D")],
        ),
        ("`x`https://a.org", [("https://a.org", "https://a.org")]),
        ("**x**https://a.org", [("https://a.org", "https://a.org")]),
        ("https://x.org/a@b", [("https://x.org/a@b", "https://x.org/a@b")]),
    ],
)
def test_a_bare_address_is_a_link_to_itself(seam: Any, source: str, linked: Any) -> None:
    view = _view(seam, typeset.ViewCache(), "k", source)
    assert _linked(view) == linked


def test_an_upper_case_scheme_is_a_link_that_says_its_lower_case_destination(seam: Any) -> None:
    """
    The scheme is lower-cased for the reader's normalising, which punycodes a host
    only under a lower-case scheme; the text differs from the destination, so it
    is said.
    """
    view = _view(seam, typeset.ViewCache(), "k", "see HTTPS://X.org/a now")
    assert tuple(view.texts) == ("see HTTPS://X.org/a (https://X.org/a) now",)
    assert [link.target for link in view.links] == ["https://X.org/a"]


def test_a_bare_address_in_a_table_cell_is_a_link(seam: Any) -> None:
    view = _view(seam, typeset.ViewCache(), "k", "| a | b |\n|---|---|\n| https://t.org/c | x |\n")
    (table,) = view.tables
    assert [(link.block, link.target) for link in view.links] == [(table, "https://t.org/c")]
    (link,) = view.links
    assert view.texts[table][link.start : link.end] == "https://t.org/c"


@pytest.mark.parametrize(
    ("source", "targets"),
    [
        ("run `curl https://x.org/a` now", []),
        ("```\ncurl https://x.org/a\n```\n", []),
        ("    curl https://x.org/a\n", []),
        ("[text](https://x.org/a)", ["https://x.org/a"]),
        ("[see https://y.org/b](https://x.org/a)", ["https://x.org/a"]),
        ("<https://x.org/a>", ["https://x.org/a"]),
        ("xhttps://x.org/a", []),
        ("1https://x.org/a", []),
        ("git+https://x.org/a", []),
        ("svn-https://x.org/a", []),
        ("end.https://x.org/a", []),
        ("@https://x.org/a", []),
        ("/https://x.org/a", []),
        ("ftp://x.org/a", []),
        ("http://", []),
        ("http:// x.org", []),
        ("https://.", []),
        ("https://", []),
        ("see www.x.org and a@b.org", []),
        ("javascript:alert(1)//https://", []),
        ("see https://docs.python.org@evil.example/3/ now", []),
        ("see https://user:pw@x.org/ now", []),
        ("see https://@x.org/ now", []),
        ("see https://www.google.com\\@evil.example now", []),
    ],
)
def test_what_is_not_a_bare_address_is_not_linked(seam: Any, source: str, targets: Any) -> None:
    view = _view(seam, typeset.ViewCache(), "k", source)
    assert [link.target for link in view.links] == targets


@pytest.mark.parametrize(
    "source",
    [
        "see https://x.org/a now",
        "(see https://x.org/a), and https://y.org.",
        "It is https://en.wikipedia.org/wiki/Foo_(bar)!",
        "https://x.org/a**/*.py and https://x.org/R&amp;D",
        "<b>https://x.org/</b>",
        'a "https://x.org/q" b',
    ],
)
def test_the_characters_set_are_the_characters_written(seam: Any, source: str) -> None:
    """
    An address that is its own destination says nothing more, so the text set is
    the source to the character.
    """
    view = _view(seam, typeset.ViewCache(), "k", source)
    assert tuple(view.texts) == (source,)
    assert view.links


@pytest.mark.parametrize(
    ("source", "said", "target"),
    [
        ("at https://x.org/a\\_b ok", "https://x.org/a\\_b", "https://x.org/a_b"),
        (
            "at https://en.wikipedia.org/wiki/Foo\\_(bar) ok",
            "https://en.wikipedia.org/wiki/Foo\\_(bar)",
            "https://en.wikipedia.org/wiki/Foo_(bar)",
        ),
        ("at https://x.org/a\\\\b ok", "https://x.org/a\\\\b", "https://x.org/a%5Cb"),
        ("at https://x.org/a\\b ok", "https://x.org/a\\b", "https://x.org/a%5Cb"),
    ],
)
def test_an_escape_in_an_address_is_set_as_written_and_goes_where_commonmark_reads_it(
    seam: Any, source: str, said: str, target: str
) -> None:
    """
    The seam sets a backslash as written (``_written_escapes``); the link goes
    where CommonMark reads the escape to, and since the text then differs from
    the destination, the destination is said after it. A backslash before what
    is not ASCII punctuation is no escape, and goes as ``%5C``.
    """
    view = _view(seam, typeset.ViewCache(), "k", source)
    (text,) = view.texts
    assert text == f"at {said} ({target}) ok"
    assert [link.target for link in view.links] == [target]


def test_a_lookalike_host_is_set_with_the_destination_it_goes_to(seam: Any) -> None:
    """
    ``аpple`` with a Cyrillic ``а``: the reader sends it to its ``xn--`` host, and
    the text, which only normalises to that, is followed by it.
    """
    view = _view(seam, typeset.ViewCache(), "k", "log in at https://аpple.com/id now")
    assert tuple(view.texts) == (
        "log in at https://аpple.com/id (https://xn--pple-43d.com/id) now",
    )
    assert [link.target for link in view.links] == ["https://xn--pple-43d.com/id"]
    (link,) = view.links
    assert view.texts[0][link.start : link.end] == (
        "https://аpple.com/id (https://xn--pple-43d.com/id)"
    )


@pytest.mark.parametrize("piece", [1, 3, 7])
def test_an_address_arriving_in_pieces_is_a_link_once_it_has_arrived(seam: Any, piece: int) -> None:
    """
    The stream re-reads its live block as it grows, so at every piece its links are
    those of the same text settled, nothing is linked before a host arrives, and
    once closed an address cut anywhere is the link the whole text has.
    """
    source = "see (https://x.org/a_(b)) and https://y.org.\n"
    cache = typeset.ViewCache()
    handle = typeset.stream(cache, "s")

    def draw() -> None:
        handle.draw()

    for at in range(0, len(source), piece):
        so_far = source[: at + piece]
        handle.append(source[at : at + piece])
        frame(seam, draw)
        assert _linked(handle.view) == _linked(_view(seam, cache, f"m{at}", so_far)), so_far
        if "https://x" not in so_far:
            assert list(handle.view.links) == []
    handle.close()
    frame(seam, draw)
    settled = _view(seam, cache, "m", source)
    assert (
        _linked(handle.view)
        == _linked(settled)
        == [
            ("https://x.org/a_(b)", "https://x.org/a_(b)"),
            ("https://y.org", "https://y.org"),
        ]
    )
    assert tuple(handle.view.texts) == tuple(settled.texts) == (source.strip(),)


def test_an_explicit_link_with_a_user_before_its_host_says_where_it_goes(seam: Any) -> None:
    """
    ``_names_itself`` leaves a destination unsaid only where the text is it to the
    character, so a user before the host is never hidden behind a link's text;
    ``links.followable`` refuses the click as well.
    """
    source = "[the docs](https://docs.python.org@evil.example/3/)"
    view = _view(seam, typeset.ViewCache(), "k", source)
    assert tuple(view.texts) == ("the docs (https://docs.python.org@evil.example/3/)",)
