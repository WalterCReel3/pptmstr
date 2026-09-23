#!/usr/bin/env python3
"""
Does `shellscan._split_segments` find every command bash would actually run?

The segment split is the whole of the read-only table's protection against a second
command: `_METACHARACTERS` deliberately does not contain `;` or `|`, so a separator that
survives into a segment is refused by nothing downstream -- `shlex.split` makes it an
ordinary token, `_unrecognised_flag` ignores any token that does not start with `-`, and
`_refuse_git` reads `rest` only for `--output`. Traced by hand,
`_refuse_git(["status", ";", "rm", "-rf", "/tmp/x"])` returns None. So a quote-tracking
mistake that misses one operator admits whatever follows it.

A quote-aware split admits more than a blind one, which is why it is checked against
bash rather than against a second Python implementation of the same rule. Two hand
implementations by one author agree on the case neither author thought of.

=== THE PROPERTY ===

For every candidate string, bash is asked what it executed and the classifier is asked
for its verdict. The requirement is one-directional, because over-refusing is what this
module is allowed to do:

    if bash ran any command that `refusal` refuses on its own,
    then `refusal` must refuse the whole string.

Over-refusal is not a failure here and is not reported as one -- `cat a & cat b` runs
two reads and is refused for its `&`, which is a decision recorded at `_SEPARATORS`.

=== HOW BASH IS ASKED ===

`bash -c 'set -x\n<candidate>'`, and every `+ ` line of the trace is a simple command
bash executed. The command word is the first token of the line. That is bash's own
parse, reported after the fact, rather than a prediction about it.

Candidates are built from an alphabet of the characters that decide the question --
quotes, a backslash, the three operators, a bare `&`, a newline, a carriage return, a
space, and enough letters to spell a command word -- exhaustively to length 3 and
sampled above that, under four prefixes. `l`, `s` and `a` are the only letters, so the
worst command word a candidate can spell is `ls` or a name that does not exist, and
everything runs in a scratch directory with stdin closed.

`NAMED` carries the structured cases as well, and it is not redundant with the random
arm: a tracker that honours a backslash inside single quotes is only caught by a string
spelling `'a\' ; cmd`, which is six characters in a fixed order out of a twelve-
character alphabet. Measured by mutating the tracker that way, the random arm at length
6 found it zero times and `NAMED` finds it every run. Exhaustive coverage stops at
length 3 and the cases that matter are longer than that, so the two arms cover different
things: `NAMED` the mistakes that have been thought of, the random arm the rest.

A candidate bash rejects as a syntax error imposes no requirement: bash ran nothing, so
any verdict is safe. Those are counted separately rather than silently dropped, because
if the alphabet is ever changed into one that is mostly syntax errors, this probe stops
testing anything and the count is the only thing that says so.

=== EXIT STATUS ===

Non-zero when a hole is found, when `/bin/bash` is absent, or when fewer than 1000
*multi-segment* candidates reached the requirement. The last one is deliberately not a
count of candidates that merely refused: a one-character body makes bash run an unknown
command and makes `refusal` refuse, meeting the requirement with no segmentation
anywhere, so gating on that would pass a run in which the split was never tested.

=== HOW TO RUN IT ===

`make verify`, which is outside `make check` because this shells out tens of thousands
of times. `tests/test_shellscan.py` runs the `NAMED` arm alone on every `make test`,
which is the 20 cases and the arm proven to catch a mutation; the exhaustive and
sampled arms are what this target adds.
"""

from __future__ import annotations

import itertools
import random
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pptmstr.shellscan import _split_segments, refusal  # noqa: E402

BASH = "/bin/bash"

# Quotes and a backslash decide where a quoted region begins and ends; `;`, `|` and `&`
# are the operators and the one character that looks like an operator and is not; the
# newline is a separator and the carriage return is refused instead. `l`, `s` and `a`
# spell `ls` and a handful of names that do not exist.
ALPHABET = "ls a'\"\\;|&\n\r"

# Each puts the candidate somewhere different relative to a real command word: at
# `argv[0]`, after a command, after a flag-free operand.
PREFIXES = ("", "cat ", "ls ", "grep a ")

# The four ways found so far to make bash close a quoted region that a tracker misses,
# plus the operator-recognition cases. `a` is the hidden command word throughout: it
# does not exist, and `refusal("a")` refuses it, which is what makes the requirement
# below bite when bash reaches it.
NAMED = (
    # A backslash is literal inside single quotes, so `'a\'` is a closed word and the
    # `;` after it is an operator.
    r"grep 'a\' ; a",
    r"cat 'a\' ; a",
    # A backslash outside quotes escapes a quote character, so no region opens.
    r"grep \' ; a",
    r"grep \" ; a",
    # A quote of the other kind inside a quoted region is an ordinary character.
    'grep "it\'s" f ; a',
    "grep 'say \"hi\"' f ; a",
    # A closing quote immediately followed by an operator, including the empty and
    # abutting regions, which is where an index that skips a region goes wrong.
    'cat "a"|a',
    'cat ""|a',
    'cat a""|a',
    "cat ''|a",
    "cat a''|a",
    'cat "a";a',
    # `|&` is not one operator, and a bare `&` is not an operator at all.
    "ls |& a",
    "ls & a",
    # A quoted or escaped operator is not one, so nothing after it is a command.
    "cat 'a;a' ; a",
    'cat "a;a" ; a',
    r"cat a\;a ; a",
    r"cat a\|a ; a",
    # A backslash-newline is a line continuation, not a separator.
    "ls \\\n a",
    # A carriage return is not a separator and `shlex` drops it.
    "ls\ra",
    # `_DISCARD_STDERR`'s anchors. The first is the one that matters: bash
    # redirects fd 2 to `/dev/nullcat` and then runs `a` as the command word,
    # so a pattern without a right anchor turns the exception into argv[0]
    # substitution. It is reported here only if bash reaches the command, and
    # on a machine whose `/dev` is root-owned devfs the redirect fails first --
    # which is why the anchor is the defence and this row is a backstop.
    "2>/dev/nullcat a",
    "cat f 2>/dev/nullcat a",
    "cat f 12>/dev/null ; a",
    "cat f 2>/dev/null ; a",
    "cat f 2> /dev/null ; a",
    'cat "2>/dev/null" ; a',
    "ls 2>/dev/null\na",
)

EXHAUSTIVE_TO = 3
SAMPLED_LENGTHS = (4, 5, 6, 7, 8)
SAMPLES_PER_LENGTH = 900
MINIMUM_EXERCISED = 1000


def bash_ran(candidate: str, cwd: str) -> tuple[list[str], str]:
    """
    The command word of every simple command bash executed, plus the raw trace.
    """
    done = subprocess.run(
        [BASH, "-c", "set -x\n" + candidate],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        cwd=cwd,
    )
    words = []
    for line in done.stderr.splitlines():
        if not line.startswith("+ "):
            continue
        traced = line[2:].strip()
        if traced:
            words.append(traced.split()[0])
    return words, done.stderr


def candidates() -> list[str]:
    bodies = [
        "".join(t)
        for n in range(1, EXHAUSTIVE_TO + 1)
        for t in itertools.product(ALPHABET, repeat=n)
    ]
    random.seed(20260922)
    for length in SAMPLED_LENGTHS:
        bodies += [
            "".join(random.choice(ALPHABET) for _ in range(length))
            for _ in range(SAMPLES_PER_LENGTH)
        ]
    return list(NAMED) + [p + b for p in PREFIXES for b in bodies if (p + b).strip()]


@dataclass(frozen=True, slots=True)
class Result:
    """
    What one pass over a candidate list found.

    ``exercised`` and ``split_exercised`` are two different claims and only the
    second says the split was involved. A one-character candidate makes bash run
    an unknown command and makes ``refusal`` refuse the whole string, which meets
    the requirement with no segmentation anywhere -- that is ``exercised``.
    ``split_exercised`` counts only candidates `_split_segments` cut into more
    than one piece, and it is the one the gate below reads.
    """

    holes: list[tuple[list[str], str]]
    syntax: int
    no_hazard: int
    exercised: int
    split_exercised: int
    timeouts: int
    total: int


def check(probes: Sequence[str]) -> Result:
    """
    Run each candidate through bash and hold the classifier to the requirement.
    """
    holes: list[tuple[list[str], str]] = []
    syntax = no_hazard = exercised = split_exercised = timeouts = 0

    with tempfile.TemporaryDirectory(prefix="verify-split-") as cwd:
        Path(cwd, "a").write_text("scratch\n")
        Path(cwd, "ls").write_text("scratch\n")
        for candidate in probes:
            try:
                words, trace = bash_ran(candidate, cwd)
            except subprocess.TimeoutExpired:
                timeouts += 1
                continue
            if "syntax error" in trace or "unexpected EOF" in trace:
                syntax += 1
                continue
            hazards = sorted({word for word in words if refusal(word) is not None})
            if not hazards:
                no_hazard += 1
                continue
            if refusal(candidate) is None:
                holes.append((hazards, candidate))
                continue
            exercised += 1
            if len(_split_segments(candidate)) > 1:
                split_exercised += 1

    return Result(
        holes=holes,
        syntax=syntax,
        no_hazard=no_hazard,
        exercised=exercised,
        split_exercised=split_exercised,
        timeouts=timeouts,
        total=len(probes),
    )


def main() -> int:
    if not Path(BASH).exists():
        print(f"No {BASH} on this machine, so there is no oracle to compare against.")
        print("This probe's whole value is that the answer comes from the shell itself.")
        return 1

    result = check(candidates())

    print(f"candidates run through {BASH}: {result.total}")
    print(f"  bash refused as a syntax error, so nothing ran: {result.syntax}")
    print(f"  bash ran only commands the table admits alone:  {result.no_hazard}")
    print(f"  bash ran a refused command AND we refused:      {result.exercised}")
    print(f"    of those, ones the split actually cut up:     {result.split_exercised}")
    print(f"  timed out:                                      {result.timeouts}")
    print(f"  HOLES (bash ran a refused command, we admitted): {len(result.holes)}")
    for hazards, candidate in result.holes[:40]:
        print(f"    {candidate!r} -> bash ran {hazards}")

    if result.holes:
        return 1
    if result.split_exercised < MINIMUM_EXERCISED:
        print(f"\nOnly {result.split_exercised} multi-segment candidates reached the")
        print(f"requirement, under the {MINIMUM_EXERCISED} this probe needs to be")
        print("evidence of anything about the split.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
