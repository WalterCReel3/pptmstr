"""
Claim 1: ui/review.head_lines("a\\n", 1) == ("a", 1), and what a splitlines-based
cut would return instead.
"""

from __future__ import annotations

import _common  # noqa: F401  (isolation + path)
from _common import verdict

from pptmstr.ui.review import head_lines


def splitlines_cut(text: str, limit: int) -> tuple[str, int]:
    """The same contract, built on str.splitlines -- the 'eleven-way bound()' shape."""
    lines = text.splitlines()
    if len(lines) <= limit:
        return text, 0
    return "\n".join(lines[:limit]), len(lines) - limit


CASES = [
    ("a\n", 1),
    ("a", 1),
    ("a\nb", 1),
    ("a\nb\n", 1),
    ("a\nb\n", 2),
    ("\n", 0),
    ("", 0),
    ("a\r\nb", 1),
    ("a b", 1),
]

print(f"{'input':<16} {'limit':>5}  {'head_lines':<22} {'splitlines cut':<22} same")
for text, limit in CASES:
    real = head_lines(text, limit)
    alt = splitlines_cut(text, limit)
    print(f"{text!r:<16} {limit:>5}  {real!r:<22} {alt!r:<22} {real == alt}")

got = head_lines("a\n", 1)
alt = splitlines_cut("a\n", 1)
print()
print(f'head_lines("a\\n", 1) = {got!r}')
print(f'splitlines cut("a\\n", 1) = {alt!r}')
if got == ("a", 1):
    verdict(
        "CONFIRMED",
        'head_lines("a\\n", 1) == ("a", 1); a splitlines cut returns ("a\\n", 0), '
        "i.e. reports nothing unset where head_lines reports one line held back",
    )
else:
    verdict("REFUTED", f"head_lines returned {got!r}")
