"""
``prose_parts``: the one rule for whether a body somebody wrote is markdown. Pure.
"""

from __future__ import annotations

import pytest

from pptmstr.ui import typeset
from tests.test_typeset_views import TLE, _fleet_bodies


def test_ordinary_markdown_is_one_part_and_not_a_character_of_it_is_touched() -> None:
    """
    The report, the parked concern, the declaration whose third list item runs over
    three lines, and every concern on the board but the one that is not markdown.
    Each is hard-wrapped by a model at about seventy-five columns, which is to say
    unevenly, and one has a line that could not be wrapped at all.
    """
    bodies = _fleet_bodies()
    assert len(bodies) >= 12
    for name, body in bodies.items():
        if name != "fake-c6":
            assert typeset.prose_parts(body) == (typeset.Part(body),), name
    assert "3. The one behaviour that can break someone: a line with a wrong\n" in bodies["declare"]
    assert TLE in bodies["fake-c4"]


@pytest.mark.parametrize(
    "body",
    [
        # An indented line under a list item is the item's, whatever came first.
        "Lead-in:\n1. item\n   continuation of the item\n",
        "- a list\n  whose item continues\n\n  and has a second paragraph\n",
        # A colon near the start of a line does not make a sentence a record.
        "Note: this is a sentence that was wrapped by whoever wrote it at a column near\n"
        "seventy-five, and the colon in its first words does not make it a field.\n",
        "Proposed instead: `tle/checksum.py` exposes `verify(line)` and the two\n"
        "callers that want validation call it first. That is a smaller diff.\n",
        "Note: the first line of this paragraph was wrapped at about seventy-five columns\n"
        "and so: was the second, which happens to have a colon near its start as well,\n"
        "and the third line has none at all.\n",
        # Wrapped by feel, as a model wraps: a line a dozen columns short of the
        # longest, with a short word waiting on the next, was not ended on purpose.
        "The reader is correct on every field that it reads and it is wrong about the one\n"
        "that it does not read, which is the modulo-ten checksum digit sitting in\n"
        "a column of its own at the very end of each line, computed and then never compared.\n",
        # One line that could not be wrapped does not make the others short.
        "The test is named for the minus rule and its input has no minus sign in it,\n"
        f"so it proves nothing about the rule: `{TLE} {TLE}`\n"
        "and it passes against a `checksum` that ignores minus signs entirely. Use the\n"
        "NOAA 19 pair, whose drag term is negative, and mutation-test it against the\n"
        "current line.\n",
        # A link carried to the next line whole is not a line ended early.
        "The format is described in the\n"
        "[CelesTrak TLE documentation](https://celestrak.org/NORAD/documentation/tle-fmt.php),\n"
        "which is also where the reference pair comes from.\n",
        "| a | b |\n|---|---|\n| 1 | 2 |\n",
        "Title\n=====\n\ntext\n",
    ],
)
def test_a_paragraph_markdown_already_reads_rightly_is_left_alone(body: str) -> None:
    assert typeset.prose_parts(body) == (typeset.Part(body),)


def test_each_paragraph_is_judged_on_its_own() -> None:
    """
    A paragraph of short lines keeps its breaks. It does not turn the prose above it
    or the list below it into anything else, and nothing becomes monospace.
    """
    prose = (
        "The fixed-width reader in `tle/parse.py` is correct on every field it reads and\n"
        "wrong about the one it does not, which is the checksum in the last column.\n"
    )
    listed = "- one thing\n- and another\n"
    body = f"{prose}\nfirst short line\nsecond short line\nthird\n\n{listed}"
    (part,) = typeset.prose_parts(body, colour="text_dim")
    assert part.kind is typeset.Kind.MARKDOWN and part.colour == "text_dim"
    assert part.text.split("\n\n") == [
        prose.rstrip("\n"),
        "first short line  \nsecond short line  \nthird",
        listed.rstrip("\n"),
    ]


def test_a_body_that_is_empty_is_no_parts() -> None:
    assert typeset.prose_parts("") == () and typeset.prose_parts(" \n\n") == ()
