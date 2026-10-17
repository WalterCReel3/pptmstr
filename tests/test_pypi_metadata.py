"""
The metadata PyPI publishes, held to the Pythons the pinned unit installs on
(``pptmstr.pairing.IMTYPESET_PYTHONS``).

``requires-python`` is what a resolver reads before it reads anything else, so
it is the one place an interpreter the unit has no wheels for is refused with a
reason rather than met by a source build of imgui-bundle. It and the
classifiers move with ``IMTYPESET_PYTHONS`` or the published metadata promises
interpreters the install cannot satisfy.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet

from pptmstr import pairing

ROOT = Path(__file__).resolve().parent.parent

with (ROOT / "pyproject.toml").open("rb") as _f:
    PROJECT = tomllib.load(_f)["project"]

PYTHON_CLASSIFIER = "Programming Language :: Python :: "


def _minors() -> list[tuple[int, int]]:
    (major, low), (high_major, high) = pairing.IMTYPESET_PYTHONS
    assert major == high_major == 3
    return [(3, minor) for minor in range(low, high + 1)]


def test_requires_python_is_exactly_the_units_range() -> None:
    low, high = pairing.IMTYPESET_PYTHONS
    assert SpecifierSet(PROJECT["requires-python"]) == SpecifierSet(
        f">={low[0]}.{low[1]},<{high[0]}.{high[1] + 1}"
    )


def test_requires_python_admits_each_minor_and_refuses_either_side() -> None:
    allowed = SpecifierSet(PROJECT["requires-python"])
    minors = _minors()
    for major, minor in minors:
        assert f"{major}.{minor}.0" in allowed
    below = (minors[0][0], minors[0][1] - 1)
    above = (minors[-1][0], minors[-1][1] + 1)
    assert f"{below[0]}.{below[1]}.99" not in allowed
    assert f"{above[0]}.{above[1]}.0" not in allowed


def test_the_python_classifiers_are_the_units_range() -> None:
    # A minor's classifier only: "3 :: Only" and "Implementation :: CPython"
    # share the prefix and say nothing about the range.
    named = {
        c.removeprefix(PYTHON_CLASSIFIER)
        for c in PROJECT["classifiers"]
        if re.fullmatch(re.escape(PYTHON_CLASSIFIER) + r"\d+\.\d+", c)
    }
    assert named == {f"{major}.{minor}" for major, minor in _minors()}


def test_the_classifiers_name_macos_and_linux_and_not_windows() -> None:
    classifiers = PROJECT["classifiers"]
    assert "Operating System :: MacOS" in classifiers
    assert "Operating System :: POSIX :: Linux" in classifiers
    assert not [c for c in classifiers if "Windows" in c or "Microsoft" in c]
    assert "Operating System :: OS Independent" not in classifiers
