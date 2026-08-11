"""
Duration formatting for every place the operator reads a clock: how long an
obligation has been waiting, and how long an agent has been alive.

These were once two functions, both written as
``f"{seconds / 60:.0f}m{int(seconds) % 60:02d}s"`` -- which rounds the leading
field and truncates the trailing one, so any remainder of 30s or more produced a
string whose two halves disagreed and 3m40s read as "4m40s". The cases below are
the ones that distinguish truncation from rounding, which is the whole point.

There is one formatter now. The queue used to render past an hour as bare "1h"
while the tree rendered "1h00m"; two formats for one quantity is a thing to learn
twice, and the inbox's wait column has the width for the longer one.
"""

from __future__ import annotations

import pytest

from pptmstr.ui.widgets import format_elapsed


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (1.4, "1s"),
        (59, "59s"),
        (59.9, "59s"),  # must not round up into a minutes-shaped string
        (60, "1m00s"),
        (90, "1m30s"),  # rounding gave "2m30s"
        (220, "3m40s"),  # rounding gave "4m40s"
        (252, "4m12s"),  # remainder < 30: rounding happened to agree
        (3599, "59m59s"),  # rounding gave "60m59s"
    ],
)
def test_sub_hour_durations_truncate_every_field(seconds: float, expected: str) -> None:
    assert format_elapsed(seconds) == expected


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (3600, "1h00m"),
        (5400, "1h30m"),
        (7140, "1h59m"),
        (7199, "1h59m"),  # rounding gave "2h59m"
        (86_399, "23h59m"),
    ],
)
def test_hour_durations_truncate(seconds: float, expected: str) -> None:
    assert format_elapsed(seconds) == expected


def test_negative_durations_clamp_rather_than_render_a_minus_sign() -> None:
    """
    A clock that has gone backwards is a bug elsewhere; it must not render as a
    negative duration that looks like a real reading.
    """
    assert format_elapsed(-5) == "0s"
