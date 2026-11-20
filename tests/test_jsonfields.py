"""
The strict JSON field decoders ``commandfile`` and ``sessionfile`` share.

The cases are those the two modules' own tests spoil their files with
(``_WRONG`` in ``test_commandfile``, ``_UNREADABLE`` in ``test_sessionfile``),
taken down to the one value each decoder sees.
"""

from __future__ import annotations

import json

import pytest

from pptmstr import jsonfields
from pptmstr.jsonfields import BAD

# -- text --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "7", "a\nb", "\udc80", "\x00"])
def test_text_takes_any_str_as_it_is(value: str) -> None:
    assert jsonfields.text(value) is value


@pytest.mark.parametrize(
    "value", [None, True, False, 0, 7, 1.5, [], {}, ["7"], float("inf")], ids=repr
)
def test_text_refuses_what_is_not_a_str(value: object) -> None:
    assert jsonfields.text(value) is BAD


# -- number ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [(0, 0.0), (7, 7.0), (-3, -3.0), (1.5, 1.5), (1_760_000_000.5, 1_760_000_000.5)],
)
def test_number_takes_a_finite_int_or_float_as_a_float(value: object, expected: float) -> None:
    got = jsonfields.number(value)
    assert type(got) is float and got == expected


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        None,
        "7",
        "1.5",
        [],
        {},
        [7],
        float("inf"),
        float("-inf"),
        float("nan"),
        10**400,
    ],
    ids=repr,
)
def test_number_refuses_a_bool_text_a_container_a_non_finite_and_an_overflow(
    value: object,
) -> None:
    assert jsonfields.number(value) is BAD


def test_number_refuses_what_1e999_parses_to() -> None:
    assert jsonfields.number(json.loads("1e999")) is BAD


# -- whole -------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, -3, 10**400])
def test_whole_takes_an_int_as_it_is(value: int) -> None:
    assert jsonfields.whole(value) is value


@pytest.mark.parametrize(
    "value", [True, False, None, 1.0, 1.5, "1", [], {}, [1], float("inf")], ids=repr
)
def test_whole_refuses_a_bool_a_float_and_what_is_not_a_number(value: object) -> None:
    assert jsonfields.whole(value) is BAD


# -- BAD ---------------------------------------------------------------------------


def test_bad_is_no_value_json_can_hold() -> None:
    value: object
    for value in (None, False, 0, "", [], {}):
        assert value is not BAD and value != BAD


# -- shown -------------------------------------------------------------------------


def test_shown_is_the_repr_up_to_sixty_characters() -> None:
    value = "x" * 58
    assert len(repr(value)) == 60
    assert jsonfields.shown(value) == repr(value)
    assert jsonfields.shown(7) == "7"
    assert jsonfields.shown(None) == "None"


def test_shown_cuts_a_longer_repr_to_fifty_seven_and_an_ellipsis() -> None:
    value = "x" * 59
    assert jsonfields.shown(value) == repr(value)[:57] + "..."
    assert len(jsonfields.shown("y" * 1000)) == 60


def test_shown_is_one_line() -> None:
    assert jsonfields.shown("a\nb\rc") == "'a\\nb\\rc'"


# -- not_json ----------------------------------------------------------------------


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_not_json_refuses_each_constant_by_name(constant: str) -> None:
    with pytest.raises(ValueError) as caught:
        json.loads(f'{{"x": {constant}}}', parse_constant=jsonfields.not_json)
    assert str(caught.value) == f"{constant} is not JSON"


def test_not_json_leaves_json_alone() -> None:
    assert json.loads('{"x": 1e999, "y": null}', parse_constant=jsonfields.not_json) == {
        "x": float("inf"),
        "y": None,
    }
