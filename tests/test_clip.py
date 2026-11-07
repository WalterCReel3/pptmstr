"""
The head-anchored character bound the panes share.
"""

from __future__ import annotations

from pptmstr.ui.clip import clip


def test_a_bound_keeps_the_head_and_reports_what_it_dropped() -> None:
    assert clip("abcdef", 4) == ("abcd", 2)


def test_a_string_within_the_bound_is_untouched() -> None:
    assert clip("abc", 4) == ("abc", 0)


def test_a_string_exactly_at_the_bound_drops_nothing() -> None:
    assert clip("abcd", 4) == ("abcd", 0)


def test_a_zero_bound_drops_everything_and_says_how_much() -> None:
    assert clip("abc", 0) == ("", 3)


def test_the_count_is_in_characters_not_bytes() -> None:
    assert clip("ééé", 2) == ("éé", 1)
