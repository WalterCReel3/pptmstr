"""
The strict field decoders ``commandfile`` and ``sessionfile`` read their JSON with.

Pure: no IO, no clock, no state. Each decoder takes one value as ``json.loads``
produced it and returns it, possibly converted, or ``BAD`` when the value is not
the type its key holds. ``BAD`` is a sentinel rather than ``None`` because
``null`` is a value a key may hold. The refusal is a return rather than a raise
so a caller can name the key in its own reason.

``shown`` and ``not_json`` produce text that reaches an operator in a refusal's
reason, so their wording is the reason's wording.
"""

from __future__ import annotations

import math
from typing import Any

# What a decoder returns for a value it does not take.
BAD: Any = object()


def text(value: object) -> Any:
    return value if isinstance(value, str) else BAD


def number(value: object) -> Any:
    # ``bool`` is an ``int`` to ``isinstance``, and ``true`` is not a time.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return BAD
    try:
        converted = float(value)
    except OverflowError:
        return BAD
    return converted if math.isfinite(converted) else BAD


def whole(value: object) -> Any:
    return value if isinstance(value, int) and not isinstance(value, bool) else BAD


def shown(value: object) -> str:
    """
    A value for a reason an operator reads: short, and on one line.
    """
    rendered = repr(value)
    return rendered if len(rendered) <= 60 else rendered[:57] + "..."


def not_json(constant: str) -> Any:
    """
    A ``parse_constant`` for ``json.loads``, which otherwise takes NaN and
    Infinity: they are not JSON, and neither module could write them back.
    """
    raise ValueError(f"{constant} is not JSON")
