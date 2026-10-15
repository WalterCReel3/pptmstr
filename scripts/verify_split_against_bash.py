#!/usr/bin/env python3
"""
Does `shellscan.refusal` see every command bash would actually run, as bash runs it?

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

"On its own" is the command's whole argument vector, not its name. A second command is
one way for the string that runs to differ from the string that was read; a word bash
assembles differently from `shlex` is another, and it has no second command in it --
`git log --out\\<newline>put=f` is one `git log`, and the argument bash hands it is
`--output=f`.

Over-refusal is not a failure here and is not reported as one -- `cat a & cat b` runs
two reads and is refused for its `&`, which is a decision recorded at `_SEPARATORS`.

=== HOW BASH IS ASKED ===

`bash -c '<preamble><candidate>'` with `PATH` set to an empty directory, so no
candidate can start a program whatever it spells. Bash hands every command it fails to
find to `command_not_found_handle`, with the arguments it would have executed, and the
preamble defines that function to append them to a file. `echo`, `pwd` and `cd` are
builtins that would be found, so the preamble shadows each with a function that
records the same way. What is read back is bash's own argument vector for every simple
command it reached, after its quoting and line joining -- not a prediction of it, and
not a trace re-quoted for display.

Two limits of that channel, both in the safe direction for the candidates here. A
command word containing a `/` is not looked up, so the handler is not called and
nothing is recorded; no alphabet below contains one. And a command bash never reached
is not recorded, which is the point: a redirect that fails ahead of its command leaves
no record because the command did not run.

Candidates come from three places:

- `NAMED`, the structured cases somebody worked out.
- An alphabet of the characters that decide the question -- quotes, a backslash, the
  three operators, a bare `&`, a newline, a carriage return, a `#`, a space, and enough
  letters to spell a command word -- exhaustively to length 3 and sampled above that,
  under four prefixes.
- `biased()`, three generators in rotation. One draws whole tokens rather than
  characters, weighted toward `#`, newlines and blanks with quotes placed in pairs. One
  builds two or three lines, each a command with or without a trailing comment that
  may carry a quote, which is the shape that hides a line from a tracker blind to
  comments. One cuts a line the table refuses for an argument -- `git log --output=x`,
  `tail -f a` -- with a backslash-newline inside one of its words.

`NAMED` is not redundant with the generated arms: a tracker that honours a backslash
inside single quotes is only caught by a string spelling `'a\' ; cmd`, which is six
characters in a fixed order. Measured by mutating the tracker that way, the random arm
at length 6 found it zero times and `NAMED` finds it every run. Exhaustive coverage
stops at length 3 and the cases that matter are longer than that, so the arms cover
different things: `NAMED` the mistakes that have been thought of, the rest the others.

=== THE PLANTED ARM ===

Everything above runs in an empty directory, where a glob matches nothing and bash
passes it through as written. So none of it can see the one way a word changes with no
quoting involved: `git log *` in a directory holding a file named `--output=x` is
`git log --output=x`. `globbed()` is run a second time in a directory holding `PLANTED`
-- names that are flags the table refuses -- under the same requirement, read off the
vector bash built after expanding.

What this arm cannot say is whether a planted flag is harmless to a row that restricts
none: the oracle for "refused alone" is `refusal`, and `refusal("ls -f")` is None
because the row says so. It holds the glob rule to the flag rules, not the flag rules
to the binaries. Nor does it say that a `--` ends the options of the real `git`, `sed`
or `tail`, since none of them is on `PATH` here; `tests/test_shellscan.py` runs those.

A candidate that makes bash run nothing imposes no requirement, so any verdict on it is
safe. One that is a syntax error on its second line has still run its first, and is
held to the requirement for what it ran. The ones that ran nothing and failed to parse
are counted separately rather than silently dropped, because if the alphabet is ever
changed into one that is mostly syntax errors, this probe stops testing anything and
the count is the only thing that says so.

=== EXIT STATUS ===

Non-zero when a hole is found in either arm, when `/bin/bash` is absent, when fewer
than 500 planted candidates reached the requirement, or when fewer than 1000
*multi-segment* candidates reached it. The last one is deliberately not a
count of candidates that merely refused: a one-character body makes bash run an unknown
command and makes `refusal` refuse, meeting the requirement with no segmentation
anywhere, so gating on that would pass a run in which the split was never tested.

=== HOW TO RUN IT ===

`make verify`, which is outside `make check` because this shells out tens of thousands
of times. `tests/test_shellscan.py` runs the `NAMED` arm and a few hundred of
`biased()` on every `make test`; the exhaustive and sampled arms, and the rest of the
biased one, are what this target adds.
"""

from __future__ import annotations

import itertools
import random
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pptmstr.shellscan import _split_segments, refusal  # noqa: E402

BASH = "/bin/bash"

# Quotes and a backslash decide where a quoted region begins and ends; `;`, `|` and `&`
# are the operators and the one character that looks like an operator and is not; the
# newline is a separator and the carriage return is refused instead; `#` begins a
# comment where it begins a word and is an ordinary character where it does not. `l`,
# `s` and `a` spell `ls` and a handful of names that do not exist.
ALPHABET = "ls a'\"\\;|&\n\r#"

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
    # A `#` that begins a word ends the line for bash, so a quote after it opens
    # nothing and the newline after that is a separator. A tracker that reads on
    # through the comment toggles on the quote and takes the next line for quoted
    # text. Each spelling is a different place a word can begin.
    "ls # '\na #'",
    'ls # "\na #"',
    "ls #'\na #'",
    "ls\t# '\na #'",
    "ls;# '\na #'",
    "ls |# '\na #'",
    "ls &&# '\na #'",
    "# '\na #'",
    "ls\n# '\na #'",
    "echo ONE # '\na #'",
    # A backslash-newline is deleted before bash reads words, so the `#` after one
    # begins a word exactly when the character before the backslash ended one.
    "ls \\\n# '\na #'",
    # Not comments, so the quote is real and bash runs one command: mid-word, after a
    # closed quote, and escaped. Nothing is hidden in these and no verdict on them is
    # wrong; they are here so that a rule refusing every `#` is not what passes above.
    "ls a#'\na #'",
    "ls ''#'\na #'",
    "ls \\# '\na #'",
    "ls '# ' ; a",
    # A backslash-newline inside a word. Bash joins the halves; `shlex` keeps the
    # newline in the token, so the flag the table checks is not the flag that runs.
    "git log --out\\\nput=x",
    'git log "--out\\\nput=x"',
    "git log -\\\n-output=x",
    "tail -\\\nf a",
    "tail -n1\\\nf a",
    "l\\\ns ; a",
    # `$'...'` honours `\'` and the tracker does not, so the tracker closes the region
    # early and reads the rest as quoted. Refused for the `$`.
    "ls $'it\\'s' ; a #'",
    # A heredoc body is not shell text, so a quote in it opens nothing. Refused for
    # the `<`.
    "cat <<EOF\n'\nEOF\na #'",
    # The body split five ways and read by bash as one command's input: more segments
    # than commands, which can only refuse more.
    "cat <<EOF\na | b ; c\nEOF",
)

# Whole tokens rather than characters, so a candidate of a dozen characters can carry a
# comment, a quoted region and a second line in the order that matters. The repeats are
# the bias: `#`, the newline and the blanks are what decide whether a comment begins and
# what it hides. Quotes are not drawn here -- `biased` places them in pairs, because a
# candidate with an odd one is refused by `shlex` whatever else is true of it and a
# stream of those tests nothing.
BIASED_TOKENS = (
    "#",
    "#",
    "#",
    "\n",
    "\n",
    "\n",
    " ",
    " ",
    " ",
    "\t",
    "\\",
    "\\\n",
    ";",
    "|",
    "&&",
    "a",
    "a",
    "ls",
    "ls",
    "git log",
    "tail",
    "-f",
)
BIASED_QUOTES = "''\""
BIASED_LENGTHS = (2, 3, 4, 5, 6, 7, 8)
# Mostly a command the table admits, because a candidate whose first word is refused is
# refused whatever follows and says nothing about what a comment or a quote can hide
# behind an admitted one.
BIASED_PREFIXES = ("ls", "ls", "ls ", "ls ", "git log ", "")

# Command lines the table refuses for an argument and not for the command word, each cut
# by `biased` at a random point inside a random word with a backslash-newline. Bash
# rejoins the word; the question is whether the classifier is still looking at it.
JOINED = (
    "git log --output=x",
    "git diff --output=x",
    "git branch -d a",
    "tail -f a",
    "sed -n 1w a",
    "a",
)

# A candidate built a line at a time: a command, whatever stands between it and the `#`,
# the `#` or its absence, and a comment's text. `COMMENT_GAPS` is the half that decides
# whether bash reads a comment at all -- a blank or an operator begins a word, a letter
# or a closed quote does not, and a backslash-newline leaves it as it was -- and
# `COMMENT_TEXTS` is what a tracker reading on through the comment trips over.
COMMENT_COMMANDS = ("ls", "ls", "ls a", "git log", "a", "tail -f a", "")
COMMENT_GAPS = (" ", " ", " ", "\t", "", ";", "\\\n", " \\\n", "a", " ''")
COMMENT_MARKS = ("#", "#", "#", "\\#", "")
COMMENT_TEXTS = ("'", "'", " '", '"', "x'", "''", "", " x")
COMMENT_LINES = (2, 2, 3)

# Of every five candidates: two token draws, two built from commented lines, one
# `JOINED` line.
BIASED_CYCLE = ("tokens", "tokens", "lines", "lines", "joined")
BIASED_SAMPLES = 6000

# File names that are flags some rule of the table refuses, and one that is not. Empty
# files, not executable: a glob standing as a command word expands to one of these and
# bash looks it up on the empty `PATH` like any other name.
PLANTED = ("--output=x", "-f", "-i", "-d", "a")

# A candidate is a command, what stands between it and the glob, the glob, and what
# follows. `GLOB_COMMANDS` has rows that restrict flags and rows that do not, since the
# rule is meant to tell them apart. `GLOB_GAPS` is where a `--` protects and where it
# only looks as if it does -- behind an option that takes it as a value. `GLOB_WORDS`
# spells each planted name through every glob character, and through the quoted and
# escaped forms bash does not expand.
GLOB_COMMANDS = (
    "git log",
    "git diff",
    "git show",
    "git blame",
    "git status",
    "git ls-files",
    "git branch",
    "tail",
    "tail -n 1",
    "sed -n 1p",
    "sed",
    "ls",
    "cat",
    "grep a",
    "head",
    "echo",
    "cd",
)
GLOB_GAPS = ("", "--", "a --", "-- a", "-S --", "-n --", "-q --", "'--'", "-- --", "a")
GLOB_WORDS = ("*", "-*", "--*", "?f", "[-]i", "--out*", "--outpu[t]=x", "./*", "'*'", "\\*", "-?")
GLOB_TAILS = ("", " a", " -- *")
MINIMUM_PLANTED = 500

EXHAUSTIVE_TO = 3
SAMPLED_LENGTHS = (4, 5, 6, 7, 8)
SAMPLES_PER_LENGTH = 900
MINIMUM_EXERCISED = 1000


# Defined ahead of every candidate. `REC` is replaced with the quoted path of the record
# directory. Each process appends to a file of its own, named for its pid: the two sides
# of a pipeline run at once, and `printf` does not put a record down in one write, so a
# shared file interleaves them. The count leads because an argument may be empty and a
# terminator could not tell that from the end of a record.
PREAMBLE = """\
__rec() { printf '%s\\0' "$#" "$@" >> REC/"$BASHPID"; }
command_not_found_handle() { __rec "$@"; }
echo() { __rec echo "$@"; }
pwd() { __rec pwd "$@"; }
cd() { __rec cd "$@"; }
"""


def _records(raw: str) -> list[list[str]]:
    fields = raw.split("\0")[:-1]
    commands, index = [], 0
    while index < len(fields):
        count = int(fields[index])
        commands.append(fields[index + 1 : index + 1 + count])
        index += 1 + count
    return commands


def bash_ran(candidate: str, cwd: str) -> tuple[list[list[str]], str]:
    """
    The argument vector of every simple command bash reached, plus its stderr.

    ``cwd`` holds an empty ``bin`` directory, which is the whole of ``PATH``, and the
    ``rec`` directory the records land in. Neither is visible to a candidate as a
    relative name it could spell by accident: the working directory is a third
    directory beside them, ``work``, holding whatever the caller planted there and
    otherwise empty. The vectors come back in no particular order.
    """
    records = Path(cwd, "rec")
    preamble = PREAMBLE.replace("REC", shlex.quote(str(records)))
    done = subprocess.run(
        [BASH, "-c", preamble + candidate],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=10,
        cwd=str(Path(cwd, "work")),
        env={"PATH": str(Path(cwd, "bin"))},
    )
    ran: list[list[str]] = []
    for record in sorted(records.iterdir()):
        ran += _records(record.read_text(errors="replace"))
        record.unlink()
    return ran, done.stderr


def _token_candidate(rng: random.Random) -> str:
    tokens = [rng.choice(BIASED_TOKENS) for _ in range(rng.choice(BIASED_LENGTHS))]
    for _ in range(rng.choice((0, 1, 1, 2))):
        quote = rng.choice(BIASED_QUOTES)
        for _ in range(2):
            tokens.insert(rng.randrange(len(tokens) + 1), quote)
    return rng.choice(BIASED_PREFIXES) + "".join(tokens)


def _lines_candidate(rng: random.Random) -> str:
    lines = [
        rng.choice(COMMENT_COMMANDS)
        + rng.choice(COMMENT_GAPS)
        + rng.choice(COMMENT_MARKS)
        + rng.choice(COMMENT_TEXTS)
        for _ in range(rng.choice(COMMENT_LINES))
    ]
    return "\n".join(lines)


def _joined_candidate(rng: random.Random) -> str:
    words = rng.choice(JOINED).split(" ")
    which = rng.randrange(len(words))
    cut = rng.randrange(len(words[which]) + 1)
    word = words[which][:cut] + "\\\n" + words[which][cut:]
    words[which] = f'"{word}"' if rng.random() < 0.3 else word
    return rng.choice(("", "", "ls ; ", "ls\n")) + " ".join(words)


def biased(count: int = BIASED_SAMPLES, seed: int = 20261013) -> list[str]:
    """
    ``count`` candidates weighted toward `#`, quotes, newlines and joined lines.

    Its own generator rather than the module one, so the suite can ask for a few
    hundred without moving the sample `candidates` draws.
    """
    rng = random.Random(seed)
    drawn: list[str] = []
    while len(drawn) < count:
        match BIASED_CYCLE[len(drawn) % len(BIASED_CYCLE)]:
            case "tokens":
                candidate = _token_candidate(rng)
            case "lines":
                candidate = _lines_candidate(rng)
            case _:
                candidate = _joined_candidate(rng)
        if candidate.strip():
            drawn.append(candidate)
    return drawn


def globbed() -> list[str]:
    """
    Every command, gap, glob and tail of the `GLOB_` tables, for a directory holding
    `PLANTED`.
    """
    return [
        " ".join(part for part in (command, gap, word) if part) + tail
        for command in GLOB_COMMANDS
        for gap in GLOB_GAPS
        for word in GLOB_WORDS
        for tail in GLOB_TAILS
    ]


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
    return list(NAMED) + biased() + [p + b for p in PREFIXES for b in bodies if (p + b).strip()]


@dataclass(frozen=True, slots=True)
class Result:
    """
    What one pass over a candidate list found.

    ``holes`` pairs each admitted candidate with the commands bash ran from it that
    are refused alone, each written out as the command line it amounts to.

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


def check(probes: Sequence[str], planted: Sequence[str] = ()) -> Result:
    """
    Run each candidate through bash and hold the classifier to the requirement.

    ``planted`` names empty files to create in the working directory first, which is
    otherwise empty.
    """
    holes: list[tuple[list[str], str]] = []
    syntax = no_hazard = exercised = split_exercised = timeouts = 0

    with tempfile.TemporaryDirectory(prefix="verify-split-") as cwd:
        for name in ("bin", "rec", "work"):
            Path(cwd, name).mkdir()
        for name in planted:
            Path(cwd, "work", name).touch(mode=0o644)
        for candidate in probes:
            try:
                ran, stderr = bash_ran(candidate, cwd)
            except subprocess.TimeoutExpired:
                timeouts += 1
                continue
            if not ran and ("syntax error" in stderr or "unexpected EOF" in stderr):
                syntax += 1
                continue
            # `shlex.join` quotes every argument that is not plain, so the line asked
            # about is the vector bash ran and carries no operator, comment or join
            # of its own.
            lines = {shlex.join(argv) for argv in ran}
            hazards = sorted(line for line in lines if refusal(line) is not None)
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


def _report(title: str, result: Result, began: float) -> None:
    print(f"{title}: {result.total}")
    print(f"  took:                                           {time.monotonic() - began:.0f}s")
    print(f"  a syntax error before anything ran:             {result.syntax}")
    print(f"  bash ran nothing the table refuses alone:       {result.no_hazard}")
    print(f"  bash ran a refused command AND we refused:      {result.exercised}")
    print(f"    of those, ones the split actually cut up:     {result.split_exercised}")
    print(f"  timed out:                                      {result.timeouts}")
    print(f"  HOLES (bash ran a refused command, we admitted): {len(result.holes)}")
    for hazards, candidate in result.holes[:40]:
        print(f"    {candidate!r} -> bash ran {hazards}")


def main() -> int:
    if not Path(BASH).exists():
        print(f"No {BASH} on this machine, so there is no oracle to compare against.")
        print("This probe's whole value is that the answer comes from the shell itself.")
        return 1

    began = time.monotonic()
    result = check(candidates())
    _report(f"candidates run through {BASH}", result, began)

    began = time.monotonic()
    among = check(globbed(), PLANTED)
    print()
    _report(f"glob candidates run among files named {', '.join(PLANTED)}", among, began)

    if result.holes or among.holes:
        return 1
    if among.exercised < MINIMUM_PLANTED:
        print(f"\nOnly {among.exercised} glob candidates reached the requirement, under")
        print(f"the {MINIMUM_PLANTED} the planted arm needs to be evidence of anything.")
        return 1
    if result.split_exercised < MINIMUM_EXERCISED:
        print(f"\nOnly {result.split_exercised} multi-segment candidates reached the")
        print(f"requirement, under the {MINIMUM_EXERCISED} this probe needs to be")
        print("evidence of anything about the split.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
