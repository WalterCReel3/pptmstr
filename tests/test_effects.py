"""
``effects.between_quotes``: the one renderer of a field an agent wrote, laid out
between double quotes on a line this tool writes.

The bus and ``peer_text`` each had a copy; they were shown equal for every input
(the bus's was the effects' with ``" ".join(text.splitlines())`` named
``_one_line``) and merged. These tests pin the outputs the copies produced, the
rule against an oracle that does not call ``splitlines``, the spelling of
``bus._one_line``'s rule inside it, and that the bus calls this one.
"""

from __future__ import annotations

import random

import pytest

from pptmstr import bus, effects
from pptmstr.effects import PeerIdentity, between_quotes, peer_text

# Probe p04's corpus (planning/2026-11-06-refactor-graph/probes/p04_decoders_quoted.py),
# with what both copies rendered for each before the merge.
CORPUS: tuple[tuple[str, str], ...] = (
    ("", '""'),
    ("plain", '"plain"'),
    ('a "q" b', '"a \\"q\\" b"'),
    ("back\\slash", '"back\\\\slash"'),
    ('\\"', '"\\\\\\""'),
    ("two\nlines", '"two lines"'),
    ("cr\rlf\r\n", '"cr lf"'),
    ("\n", '""'),
    ("trail\n", '"trail"'),
    ("\x0b\x0c\x1c\x1d\x1e\x85", '"     "'),
    ("ls ps ", '"ls ps"'),
    ("\t tab", '"\t tab"'),
    ("\x00nul", '"\x00nul"'),
    ("\ud800lone", '"\ud800lone"'),
    ("é ü 字", '"é ü 字"'),
    ("''", "\"''\""),
    ('"""', '"\\"\\"\\""'),
    ("a\n\nb", '"a  b"'),
)


@pytest.mark.parametrize(("text", "rendered"), CORPUS)
def test_each_awkward_input_renders_as_it_did_before_the_merge(text: str, rendered: str) -> None:
    assert between_quotes(text) == rendered


# Every break ``str.splitlines`` recognises. ``\r\n`` is one break, not two.
_BREAKS = frozenset("\n\r\x0b\x0c\x1c\x1d\x1e\x85  ")


def _oracle(text: str) -> str:
    """
    The rule written out by hand: each break a space, ``\\r\\n`` one break, a
    final break dropped, then a backslash and a quote mark escaped.
    """
    out: list[str] = []
    i = 0
    ended_on_break = False
    while i < len(text):
        c = text[i]
        if c in _BREAKS:
            out.append(" ")
            i += 2 if text.startswith("\r\n", i) else 1
            ended_on_break = True
            continue
        out.append("\\\\" if c == "\\" else '\\"' if c == '"' else c)
        ended_on_break = False
        i += 1
    if ended_on_break:
        out.pop()
    return '"' + "".join(out) + '"'


_ALPHABET = (
    *_BREAKS,
    "\r\n",
    "\\",
    '"',
    "'",
    "\t",
    " ",
    "\x00",
    "\x01",
    "\x0e",
    "\x7f",
    "\ud800",
    "\udc80",
    "a",
    "é",
    "字",
    "·",
    "|",
    "\U0001f600",
)


def _generated(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 12))) for _ in range(count)]


def test_the_rule_matches_one_written_without_splitlines_over_generated_strings() -> None:
    texts = _generated(20261106, 20_000) + [a + b for a in _ALPHABET for b in _ALPHABET]
    for text in texts:
        assert between_quotes(text) == _oracle(text), repr(text)


def test_the_oracle_fails_a_rule_that_splits_on_newline_alone() -> None:
    """
    The oracle can tell the rule from the nearest wrong one, so agreeing with it
    says something.
    """

    def naive(t: str) -> str:
        return '"' + " ".join(t.split("\n")).replace("\\", "\\\\").replace('"', '\\"') + '"'

    assert any(naive(t) != _oracle(t) for t in _generated(1, 2_000))


def test_the_one_line_rule_inside_it_is_the_bus_one() -> None:
    """
    ``between_quotes`` spells ``bus._one_line``'s rule because the bus imports this
    module; this holds the two spellings together.
    """
    for text in _generated(7, 5_000):
        flat = bus._one_line(text).replace("\\", "\\\\").replace('"', '\\"')
        assert between_quotes(text) == f'"{flat}"', repr(text)


def test_the_bus_and_a_peers_description_quote_through_this_one_function() -> None:
    assert bus.between_quotes is effects.between_quotes
    assert not hasattr(bus, "_said")
    peer = PeerIdentity(session="s1", title='say "hi"\\', project="p q", held=True)
    assert peer_text(peer) == 'session s1, card "say \\"hi\\"\\\\", project "p q"'
