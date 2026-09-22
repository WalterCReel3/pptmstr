"""
Whether a shell command only reads.

Pure functions: no SDK, no UI, no IO. Nothing here runs the command it is
deciding about, and nothing here looks at the filesystem the command names.

This is the allowlist behind the `PERMISSIVE` policy
(``planning/2026-08-11-research-phase-auto-approval.md`` §2, scopes (b) and
(c)). It sits beside ``approval.py`` rather than inside it because the table
plus the reason attached to each row is larger than the whole of that module,
and ``approval.py``'s docstring commits it to being a small policy file.

08-11 recommended (b) without (c) and deferred pipelines on the grounds that
"the quoting edge cases are where it will be wrong". That deferral was reversed
on 2026-09-21, for a reason the record could not have had: measured against
2335 real Bash calls, separator support takes the admit rate from 3.51% to
4.84%, and it multiplies rather than adds against any later widening of the
table. The quoting edge cases are real and they are handled by over-rejecting
-- see ``_SEPARATORS`` and the tilde rule below.

The table itself moved the same day, in both directions: ``grep`` came back
after being measured against 2271 real Bash calls and found to be 439 of
them, and ``sed`` gained a narrow print-range admission that its script
language cannot use to write. See the comments at ``_TABLE`` and
``_refuse_sed`` for what each reversal cost and what it did not.

The parser *is* the security property here, so the shape is deliberately blunt:
a command is refused unless every token of it is recognised. Refusing a command
that was in fact safe costs one approval; admitting one that writes costs the
premise of the policy. There is no symmetry between those two errors, and the
whole module is written from that asymmetry.

"Only reads" is the summary line and it is one clause short. ``git status``
refreshes ``.git/index``, and with ``core.untrackedCache`` or ``core.fsmonitor``
configured it writes cache extensions as well. Always inside ``.git/``, never
the worktree, and it destroys nothing -- it is the same write the operator's own
shell does every time they run it. The row stays; the clause is here so that the
next reader who discovers the write does not assume something worse of the rest
of the table.

Three rules that are easy to read past:

- The command is **split into segments first**, on the separators bash uses to
  sequence one command into the next, and then every segment is put through the
  whole of the rest of this module independently. One failing segment refuses
  the command. A pipeline widens nothing: if ``git branch`` is refused alone it
  is refused in a pipeline, because the segment carrying it is checked exactly
  as it would have been on its own.
- The metacharacter scan runs over the **raw segment**, before ``shlex.split``.
  What it catches would otherwise not survive tokenisation --
  ``shlex.split("ls\\rrm -rf /tmp/x")`` is ``["ls", "rm", "-rf", "/tmp/x"]``,
  which matches ``ls`` with "any flags". Scanning tokens would admit exactly the
  command the scan exists to stop.
- ``argv[0]`` is matched literally, so ``/bin/ls`` is refused. A path is not the
  command it resembles, and resolving one would mean reading the filesystem.

This table and the corpus in ``tests/test_shellscan.py`` move together. A row
added here without an adversarial case there is a widening that nobody checked.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# The separators bash uses to run one command after another. `_split_segments`
# matches this at a cursor it has tracked quoting with, so one of these is
# consumed only where bash would read it as an operator: outside quotes and
# unescaped. Every segment is then checked as if it had been typed on its own.
#
# A quoted or escaped one therefore survives into a segment, where nothing
# refuses it -- `;` and `|` are absent from `_METACHARACTERS` below. That is the
# admission this shape exists for: `ls ';'` lists a file named `;` and
# `grep 'a|b' f` greps for a literal pipe, and neither sequences anything.
#
# `||` and `&&` are listed ahead of the single-character class deliberately:
# `re` alternation is first-match, not longest-match, so a bare `[|]` earlier in
# the pattern would cut `||` into two separators with an empty segment between
# them. That would still refuse -- the empty-segment rule below catches it --
# but it would be refusing a legitimate `a || b` for a malformed-input reason,
# and the two are different facts about the command.
#
# Bare `&` is NOT here, and its absence is the rule. `a & b` backgrounds `a`
# rather than sequencing it, so it is not a separator in the sense this split
# means; it stays in `_METACHARACTERS` and refuses the segment it lands in.
# That is what makes `ls |& cat` refuse: the split consumes the `|` and the `&`
# survives into the next segment, where the scan rejects it. Pinned by
# `test_the_pipe_stderr_form_refuses_because_bare_ampersand_is_not_a_separator`.
_SEPARATORS = re.compile(r"\|\||&&|[;\n|]")

# Measured 2026-09-21: across 2335 real Bash calls, no command this module
# admits had more than four segments, so eight is headroom and not a
# constraint.
#
# This is NOT a security bound and must not be read as one: every segment is
# checked whatever the count, so a command under the cap is no more trusted
# than one over it. The cap exists so that a pathological string is refused on
# its shape instead of being tokenised a thousand times first.
_MAX_SEGMENTS = 8

# Scanned against the raw segment, so no quoting can hide one and no
# tokenisation can drop one. `$` is refused on its own rather than only as
# `$(`: `$VAR` and `${VAR}` expand to text the table below never sees, so a
# token containing one is not the token that will run.
#
# `;` and `|` are absent because `_SEPARATORS` consumes them wherever bash
# would sequence on them, and what they separate is then checked as a command
# rather than rejected as a character. Where bash would *not* sequence on one --
# quoted, or escaped -- it reaches a segment and is admitted as the literal
# character bash passes to the command.
#
# Those two are the only characters the split is allowed to launder, and the
# quote-blindness here is what keeps the rest out of reach. `$'...'` is bash's
# other quoting form and it is refused by this scan on its `$`, so
# `_split_segments` does not have to know about it.
_METACHARACTERS = frozenset("&<>`$(){}")

# `\r` is not a separator: bash does not sequence on it, so splitting there
# would be inventing a boundary the shell does not have. It is refused instead,
# because `shlex` counts it as whitespace and drops it -- `shlex.split(
# "ls\rrm -rf /tmp/x")` is `["ls", "rm", "-rf", "/tmp/x"]`, which the table
# reads as `ls` with three flags.
#
# Do not tidy this into `_SEPARATORS` alongside the newline. The two look like
# the same character class and they need opposite treatments: a newline is a
# real boundary, so splitting on it puts the second command under the table,
# while `\r` is not one, so splitting on it would hand the table a fragment
# bash never meant to run as a command. Folding them together reopens exactly
# the hole the newline rule was written for. Pinned by
# `test_a_carriage_return_is_caught_before_tokenisation`.
_CARRIAGE_RETURN = "\r"

# `~` is the one character in 08-11 §2's list that is not special everywhere,
# so it is refused in the three places bash actually expands it and admitted in
# the one place it does not. Measured on /bin/bash 3.2.57, 2026-09-17:
#
#   echo ~/x           -> /Users/.../x        word start
#   echo foo=~/x       -> foo=/Users/.../x    after `=` in an assignment-shaped
#   echo PATH=a:~/b    -> PATH=a:/Users/.../b  argument word, and after its `:`
#   echo HEAD~1        -> HEAD~1              mid-token: not expanded
#   echo --prefix=~/x  -> --prefix=~/x        `--prefix` is not an identifier
#   echo HEAD:~/x      -> HEAD:~/x            no `=`, so not an assignment word
#
# The last two are refused anyway: the rule tests only the preceding character,
# not whether the whole token is assignment-shaped. That over-refuses, which is
# the direction this module is allowed to be wrong in.
#
# Admitting mid-token is a deliberate loosening and it buys a specific thing:
# `git diff HEAD~1`, `git show HEAD~3` and `git log HEAD~5..HEAD` are what
# orienting in a repository looks like, and refusing `~` everywhere parks all
# three. The loosening is affordable because a tilde expands to a path and
# cannot execute anything, and because 08-11's consequences section already
# declines cwd-containment for this allowlist on the grounds that `Read`
# auto-approves any path.
#
# The rule is stated above in bash's terms -- "word start" -- and implemented
# in the segment's terms: index 0, or after an `.isspace()` character. Those
# two coincide because within a segment there is nowhere else a word can begin.
# Every *other* character that can start a word in bash is accounted for, and
# now in two different ways rather than one:
#
#   `;` `|` and a newline    consumed by `_SEPARATORS` wherever bash sequences
#                            on one, so the character after it is at index 0 of
#                            the next segment -- a word start, and read as one.
#                            Quoted, one survives into a segment, and bash does
#                            not begin a word after it either: reaching a word
#                            start still takes the whitespace this rule tests
#                            for, so `ls ';'~/x` is one word to both of us.
#   `&` `(` `<` `>`          still refused by the metacharacter scan, so no
#                            segment carrying one reaches this rule.
#
# That is load-bearing rather than incidental, and it is load-bearing on both
# halves now. Drop a character from `_METACHARACTERS` without adding it to
# `_SEPARATORS` and `ls&~/x` starts being admitted with the tilde rule looking
# untouched. Pinned by
# `test_the_tilde_rule_depends_on_the_split_and_the_scan_between_them`.
#
# A quoted tilde is admitted, because bash does not expand one -- `ls '~'`
# names a file called `~`. Nothing is conceded by that: an unquoted
# `cat /Users/someone/.ssh/id_rsa` is admitted outright, for the same reason
# 08-11 declines cwd-containment here.
_TILDE = "~"
_TILDE_EXPANDS_AFTER = "=:"

# Refused by name rather than by falling through the table, because each of
# these is a decision and an absence does not argue back with the next reader
# who wants to add a row. Pinned against the table by
# `test_the_refused_commands_are_not_also_in_the_table`.
_NEVER_ALLOWED: dict[str, str] = {
    # `{print > "f"}` writes, and there is no flag to detect it.
    "awk": "awk's script language writes files, with no flag to detect it",
    "gawk": "awk's script language writes files, with no flag to detect it",
    "mawk": "awk's script language writes files, with no flag to detect it",
    # Each of these runs a program chosen by an argument, so whatever the table
    # decided about this command is not what ends up running.
    "xargs": "xargs runs a command this table never saw",
    "env": "env runs a command this table never saw",
    "sudo": "sudo runs a command this table never saw",
    "nohup": "nohup runs a command this table never saw",
    "timeout": "timeout runs a command this table never saw",
    "eval": "eval runs a command this table never saw",
    "sh": "sh runs a command this table never saw",
    "bash": "bash runs a command this table never saw",
    "zsh": "zsh runs a command this table never saw",
    "python": "python runs a program this table never saw",
    "python3": "python3 runs a program this table never saw",
    "perl": "perl runs a program this table never saw",
    "ruby": "ruby runs a program this table never saw",
    "node": "node runs a program this table never saw",
}


@dataclass(frozen=True, slots=True)
class _Rule:
    """
    Which flags of an otherwise-reading command are recognised.

    ``allowed is None`` means every documented flag of this command only reads.
    That is a claim about the command, made once and written beside its row --
    not a default, and not the same statement as an empty set, which is why the
    two are spelled differently.

    There is no deny field, and its absence is the rule. A deny-list of flags
    cannot be written down against a parser that resolves abbreviations:
    getopt_long(3) accepts ``--foll`` for ``--follow``, so a hazardous long flag
    has a whole family of spellings and a list can only name one member of it.
    An allowlist fails the other way -- a spelling nobody listed parks, which
    costs one approval. ``_GIT_BRANCH_FLAGS`` below is the same shape for the
    same reason, and was the pattern first.
    """

    allowed: frozenset[str] | None = None
    # Short flags whose numeric value may abut the letter, so `-n20` is `-n`
    # carrying a count rather than the cluster `-n -2 -0`. A subset of
    # `allowed`, pinned by `test_a_value_taking_flag_is_one_its_row_allows`.
    takes_number: frozenset[str] = frozenset()


# What is absent from this table is a decision. `grep`, `rg`, `ag`, `find`,
# `file` and `tree` were rows here and were dropped on 2026-09-17, then `grep`
# came back on 2026-09-21 and the other five did not. The two dates are two
# different questions, and it matters that the second was not "undo the
# first":
#
# The 09-17 shrink was argued from danger alone, before any of these rows had
# been measured against what a session actually runs. `Read`, `Glob` and
# `Grep` auto-approve natively at every policy (`approval._AUTO`), so the
# reasoning was that the Bash table only has to cover what those three cannot
# do -- which is `git`, plus trivial inspection -- and that search and
# traversal binaries buy a session almost nothing on top of that. Measured
# 2026-09-21 against 2271 real Bash calls, that reasoning was wrong about one
# row and right about the rest: `grep` is 439 of them, the second most
# frequent command in the corpus, and dropping it cost most of what the table
# was worth.
#
# `rg`, `ag`, `find`, `file` and `tree` stay dropped, each for a reason that
# survives the correction. Every one of the five produced a review finding,
# and two were reachable execution: `ag --pager CMD` runs CMD through
# `popen()`, and `file --compile` writes a `magic.mgc`. `rg --pre CMD` is the
# same shape: an allowlist admitting `rg` would have to enumerate every safe
# flag on the one row with a literal "run this program" option, for 8 calls
# in the corpus against `grep`'s 439 -- the surface is not worth the value.
# `grep`'s own option set was gone over the same way and nothing was found:
# no write, no exec, `-P` is PCRE without callouts enabled, `-f`/`--file`
# reads a pattern file rather than writing one, and `GREP_OPTIONS` was removed
# in grep 2.21. It is re-admitted unrestricted rather than flag-restricted
# because nothing in its surface needed restricting -- an allowlist with every
# flag on it is not a stronger claim than `_Rule()` already makes, it is the
# same claim spelled out.
_TABLE: dict[str, _Rule] = {
    "ls": _Rule(),
    "pwd": _Rule(),
    "wc": _Rule(),
    "stat": _Rule(),
    "du": _Rule(),
    "df": _Rule(),
    "which": _Rule(),
    "head": _Rule(),
    "grep": _Rule(),
    # The one row that restricts flags, and the one whose hazard is not a
    # mutation. `tail -f somefile` never returns: the sales pitch of the
    # preset is that the operator does not have to be present, and an
    # unattended session wedged until the Bash tool times out looks to them
    # like nothing at all.
    #
    # Listed below is what reads. Refused by not being listed: `-f`, `-F`,
    # `--follow`, `--retry`, `--pid`, `-s`/`--sleep-interval` and
    # `--max-unchanged-stats`, every one of which exists only to follow -- and
    # with them every abbreviation and every cluster containing one, which is
    # the property this shape has and a deny-list does not. Measured on BSD
    # tail(1), 2026-09-17: `tail -2f file` follows, because the obsolescent
    # `-NUM[lbc][f]` form packs the count and the follow flag into one token.
    # A deny-list stops that only if somebody thought of it first.
    "tail": _Rule(
        allowed=frozenset(
            {
                "-b",
                "-c",
                "-n",
                "-q",
                "-r",
                "-v",
                "-z",
                "--bytes",
                "--lines",
                "--quiet",
                "--silent",
                "--verbose",
                "--zero-terminated",
            }
        ),
        takes_number=frozenset({"-b", "-c", "-n"}),
    ),
    "cat": _Rule(),
    "nl": _Rule(),
    # The row that is not a read. It is here because the admit rate of a
    # sequence is its weakest segment: `sed -n 1,80p a.py; echo "==="; sed -n
    # 1,80p b.py` is two admitted reads and one separator label, and without
    # this row the label parks the whole command.
    #
    # Unrestricted, and it is a stronger claim than `grep`'s row makes rather
    # than the same one. The builtin's whole option set is `-neE` -- bash
    # 3.2.57's `help echo` and zsh 5.9's zshbuiltins(1) both document exactly
    # those three -- and beyond them it has no option grammar at all: run on
    # bash 3.2.57, `echo -x foo`, `echo --output=/tmp/pwned` and `echo -- foo`
    # each print their arguments verbatim, because the builtin does not error
    # on an unrecognised option and does not read `--` as ending them. So a
    # flag allowlist here would not be a narrower claim, it would be a wrong
    # one: it would park tokens that are text.
    #
    # `/bin/echo` takes `-n` alone and GNU coreutils adds `--help` and
    # `--version`; both sets are unreachable, because `argv[0]` is matched
    # literally and a bare `echo` is the builtin.
    #
    # What makes `echo` write or execute is never a flag: `>`, `>>`, `$(`, a
    # backtick and `{}` are refused by the raw metacharacter scan before
    # dispatch reaches this table, and `echo x | tee f` is refused by `tee`'s
    # own absence from it.
    "echo": _Rule(),
}

# `git <sub>` with no further restriction beyond the shared `--output` rule.
# `branch` and `remote` are handled separately: both have read-only and
# mutating forms under the same subcommand name.
#
# NOT DETECTED, and named here rather than left to be rediscovered: a
# repo-local `.git/config` can set `diff.external` or a `diff.<driver>.textconv`
# and `log -p`, `show` and `diff` will then run that program. Seeing it would
# mean reading `.git/config`, and this module does no IO. The hole is the price
# of that commitment, so what an exploit costs is worth being exact about --
# each leg below was run on 2026-09-17, not reasoned about:
#   - `.gitattributes` alone does not reach execution. A clone carries
#     `* diff=evil`, since it is tracked, but with no matching `diff.evil.*`
#     anywhere in config `git log -p` produced an ordinary diff and ran
#     nothing. Adding `diff.evil.textconv` to the clone's own config, with the
#     tracked content untouched, then executed it.
#   - `git clone` does not carry `.git/config`. A clone of a repository whose
#     config set `diff.external` had no `diff.external` in it.
#   - `git config` is absent from the allowlist below, so this module refuses
#     the command that would install the setting.
# A hostile repository on its own therefore cannot reach it. `.gitattributes`
# is tracked and does travel, but it only names *which* driver applies; the
# half that says what to RUN lives in `.git/config` or `~/.gitconfig`, and a
# clone carries neither. Reaching execution takes one of two things, and they
# have different shapes:
#   - a prior local write to `.git/config`, which under the preset parks at the
#     operator because `Write`/`Edit`/`MultiEdit` are not auto-approved; or
#   - a `textconv` driver the operator already has in their own `~/.gitconfig`,
#     which some developers do, and which needs no write at all.
# The second is the live one. It is environment-dependent and not reachable by
# the model acting alone, and it is recorded rather than defended against:
# defending means reading git config from a module committed to doing no IO.
# The pager variant is moot -- git spawns a pager only on a TTY, and the Bash
# tool gives it a pipe.
_GIT_READ_SUBCOMMANDS = frozenset(
    {
        "status",
        "log",
        "show",
        "diff",
        "blame",
        "describe",
        "rev-parse",
        "ls-files",
    }
)

# Listing forms only. Anything else -- a positional, `-d`, `-m`, `-u` --
# creates, deletes, renames or repoints a branch.
_GIT_BRANCH_FLAGS = frozenset(
    {
        "-a",
        "--all",
        "-r",
        "--remotes",
        "-v",
        "-vv",
        "--verbose",
        "-l",
        "--list",
        "--show-current",
        "--color",
        "--no-color",
        "--merged",
        "--no-merged",
    }
)
_GIT_BRANCH_PREFIXES = ("--format=", "--sort=")

# `sed`'s write is in its script language, not behind a flag: `w FILE` and
# `s///w FILE` both create FILE with no flag present at all, and GNU sed's `e`
# command runs a shell command from the script. A flag rule cannot see either
# one, which is why `sed` was refused outright rather than restricted -- the
# same shape `awk` is refused for.
#
# What is admitted instead is one shape the script language cannot smuggle a
# write or an `e` through: a print range and nothing else. `[0-9,$]+p` is
# digits, commas and `$` -- sed's own address syntax -- followed by the one
# letter that only prints. `w`, `e`, `r`, `R`, `s`, `y` and every other sed
# command are outside that character class, so a script containing any of them
# fails the pattern rather than needing to be denied by name. This is the
# allowlist shape `_Rule` already uses for flags, carried into the one place a
# flag rule cannot reach.
#
# `$` in this pattern is unreachable in practice, and that is worth being
# honest about rather than trimming it: the raw metacharacter scan in
# `_refuse_segment` refuses any `$` anywhere in the segment before `sed` is
# ever dispatched to, quoted or not, because `$VAR` expands to text this
# module never sees. So sed's last-line address parks like every other `$`
# does, not because this pattern denies it. The class stays `[0-9,$]+p`
# because that is sed's actual print-range grammar; the scan upstream is what
# makes the `$` branch of it dead code today, and the two facts are recorded
# separately rather than folded into one.
_SED_ALLOWED_FLAGS = frozenset({"-n", "--quiet", "--silent"})
_SED_PRINT_RANGE = re.compile(r"^[0-9,$]+p$")


def _refuse_sed(args: list[str]) -> str | None:
    """
    `sed`'s script is a positional argument, not a flag, so `_Rule` cannot
    check it -- this is the bespoke check that stands in for a table row.

    Only `-n`/`--quiet`/`--silent` are recognised flags; `-e`, `-f`, `-i` and
    every clustered or abbreviated form of them are refused by not being in
    the set, which is the same abbreviation-proof shape every other flag rule
    in this module uses. The first non-flag token is the script and must be a
    print range; every non-flag token after it is a file operand and sed does
    not read those as further script, so nothing past the first is checked.
    """
    script: str | None = None
    for token in args:
        if token.startswith("-") and token not in ("-", "--"):
            if token.split("=", 1)[0] not in _SED_ALLOWED_FLAGS:
                return f"sed {token} is not a recognised read-only flag"
            continue
        if script is None:
            script = token
            if not _SED_PRINT_RANGE.match(script):
                return "sed's script language writes with `w` and takes no flag to do it"
    if script is None:
        return "sed with no script"
    return None


def _refuse_cd(args: list[str]) -> str | None:
    """
    `cd` with no argument goes home, which is not an orienting move and is not
    what a session typing `cd` unattended means to do. Everything else about
    `cd` is already covered: a substitution in its argument -- `cd $(...)`,
    `` cd `...` `` -- is refused by the metacharacter scan before dispatch
    ever reaches here, because `$` and a backtick are refused wherever they
    appear in the segment, not only when they follow a recognised command.
    """
    if not args:
        return "cd with no argument goes home, which is not an orienting move"
    return None


def _split_segments(command: str) -> list[str]:
    """
    ``command`` cut where bash would read a `_SEPARATORS` operator, and nowhere
    else.

    Quote-aware because bash's own operators are: `grep 'a|b' f` is one command
    whose pattern contains a literal pipe, and cutting it there produces a
    fragment carrying an unbalanced quote. The scan in `_refuse_segment` stays
    quote-blind, and the two are different questions -- a quoted `$` still
    expands nothing and is still refused, while a quoted `|` sequences nothing
    and is kept.

    The state machine is bash's three quoting constructs and nothing else:

    - Outside single quotes a backslash escapes the next character, so `a\\|b`
      is one word. Inside them it does not, so `'a\\'` is a closed word whose
      content is `a\\` and the `;` after it *is* an operator. Getting that one
      backwards admits `grep 'a\\' ; rm -rf /tmp/x`, because `shlex` reads it
      bash's way and hands the table a `;` token no rule refuses.
    - A `'` inside double quotes and a `"` inside single quotes are ordinary
      characters.
    - `$'...'` is bash's fourth quoting form and is not tracked here, because
      `_METACHARACTERS` refuses its `$` before this ever decides anything.

    One cursor, advancing by one character or by two across an escape. There is
    no skip-to-the-end-of-the-quoted-region step: an off-by-one there swallows
    the operator in `cat "a"|rm -rf /tmp/x` and the `rm` becomes an operand of
    an unrestricted row.
    """
    segments: list[str] = []
    start = index = 0
    single = double = False
    while index < len(command):
        char = command[index]
        if char == "\\" and not single:
            index += 2
            continue
        if char == "'" and not double:
            single = not single
        elif char == '"' and not single:
            double = not double
        elif not single and not double:
            operator = _SEPARATORS.match(command, index)
            if operator is not None:
                segments.append(command[start:index])
                start = index = operator.end()
                continue
        index += 1
    segments.append(command[start:])
    return segments


def refusal(command: str) -> str | None:
    """
    None when ``command`` only reads; otherwise why it still needs a human.

    ``command`` is split by `_split_segments` and every segment must pass on
    its own, so a sequence is admitted only when each of its commands would
    have been admitted alone. The first failing segment decides, and the reason
    names it -- a pipeline refused for its fourth command is otherwise
    indistinguishable from one refused for its first.

    The reason is returned rather than discarded for two reasons. A refusal the
    operator cannot account for reads as a bug in the gate rather than as the
    gate working. And a test that can only assert "refused" cannot tell a rule
    that fired from one that never ran -- every entry in the adversarial corpus
    would pass against a function that refused everything.
    """
    if not command.strip():
        return "empty command"

    segments = _split_segments(command)
    if len(segments) > _MAX_SEGMENTS:
        return (
            f"{len(segments)} segments is more than the {_MAX_SEGMENTS} an "
            f"orienting command needs"
        )

    for position, segment in enumerate(segments, start=1):
        if not segment.strip():
            return (
                f"segment {position} is empty, so a separator has no command " f"on one side of it"
            )
        reason = _refuse_segment(segment)
        if reason is None:
            continue
        # A single-segment command has no segment to name, and saying "segment
        # 1" about a command nobody split would make every existing refusal
        # read as though a pipeline were involved.
        if len(segments) == 1:
            return reason
        return f"segment {position} ({segment.strip()!r}): {reason}"
    return None


def _refuse_segment(command: str) -> str | None:
    """
    The same question as `refusal`, asked about one segment of a pipeline.

    Split out so that a segment cannot be checked by anything lighter than the
    whole path -- the metacharacter scan, `shlex.split`, `_NEVER_ALLOWED`, the
    table, the git rules and the tilde rule all run here, and `refusal` has no
    other way to reach them. A second, cheaper check for segments is the defect
    this shape exists to make unavailable.
    """
    for index, char in enumerate(command):
        if char == _CARRIAGE_RETURN:
            return "a carriage return is dropped by tokenisation"
        if char == _TILDE:
            previous = command[index - 1] if index else ""
            if not previous or previous.isspace() or previous in _TILDE_EXPANDS_AFTER:
                return f"shell metacharacter {char!r}"
            continue
        if char in _METACHARACTERS:
            return f"shell metacharacter {char!r}"

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"not parseable as one command: {exc}"
    if not argv:
        return "empty command"

    name = argv[0]
    refused = _NEVER_ALLOWED.get(name)
    if refused is not None:
        return refused
    if name == "git":
        return _refuse_git(argv[1:])
    if name == "sed":
        return _refuse_sed(argv[1:])
    if name == "cd":
        return _refuse_cd(argv[1:])

    rule = _TABLE.get(name)
    if rule is None:
        return f"{name!r} is not in the read-only table"
    for token in argv[1:]:
        unrecognised = _unrecognised_flag(token, rule)
        if unrecognised is not None:
            return f"{name} {unrecognised} is not a recognised read-only flag"
    return None


def is_read_only(command: str) -> bool:
    """
    Whether ``command`` may run without an operator looking at it.

    Derived from `refusal` rather than deciding again, so the predicate and the
    explanation cannot disagree.
    """
    return refusal(command) is None


# The first token the row does not recognise, or None if it recognises all of
# them. Three spellings have to be read as the same option, and the direction
# of every judgement call below is the same: getting one wrong refuses a real
# flag, which costs one approval, rather than admitting an unlisted one.
#
# 1. Long, whole or up to an `=`. Abbreviations are deliberately NOT resolved:
#    `--line` is not `--lines` here. Resolving one would mean deciding what it
#    is unique against, and that is the binary's option set, which this module
#    cannot see -- run on 2026-09-17, `rg --pr` was rejected by clap and
#    `git log --outpu` by git's own diff option parsing, while anything on
#    getopt_long accepts the abbreviation. Under an allowlist that question
#    does not have to be answered: an abbreviation nobody listed parks.
# 2. Clustered short options, walked letter by letter, because getopt packs
#    them. `-qr` is `-q -r`, and one unlisted letter anywhere in the cluster
#    makes the token unlisted.
# 3. A numeric value abutting its letter, `-n20`, for the letters the row says
#    take one. The digits are a value and not option letters.
#
# A token that is all digits after the `-` is the obsolescent count form,
# `tail -20`, and it is admitted only while the digits run to the end of the
# token -- `-2f` is that form with a follow flag welded on, and BSD tail does
# follow for it.
def _unrecognised_flag(token: str, rule: _Rule) -> str | None:
    if rule.allowed is None:
        return None
    if not token.startswith("-") or token in ("-", "--"):
        return None

    head = token.split("=", 1)[0]
    if head.startswith("--"):
        return None if head in rule.allowed else head

    body = token[1:]
    if body.isdigit():
        return None
    for index, letter in enumerate(body):
        flag = f"-{letter}"
        if flag not in rule.allowed:
            return flag
        if flag in rule.takes_number and body[index + 1 :].isdigit():
            return None
    return None


def _refuse_git(args: list[str]) -> str | None:
    if not args:
        return "git with no subcommand"

    subcommand = args[0]
    if subcommand.startswith("-"):
        # `git -c core.pager=... log` and `git --exec-path=... log` both hand
        # git a program to run before the subcommand is reached, so the
        # allowlist below would be deciding about the wrong thing.
        return f"git global option {subcommand!r} ahead of the subcommand"

    rest = args[1:]
    for token in rest:
        if token.startswith("--output"):
            # Verified by execution 2026-09-17: `git log --output=FILE` and
            # `git diff --output=FILE` both create FILE. It is a diff option,
            # so it is accepted by more subcommands than `diff`.
            return "git --output writes a file"

    if subcommand == "branch":
        for token in rest:
            if not token.startswith("-"):
                return "git branch with an argument creates, renames or deletes one"
            if token.startswith(_GIT_BRANCH_PREFIXES):
                continue
            if token.split("=", 1)[0] not in _GIT_BRANCH_FLAGS:
                return f"git branch {token} is not a listing flag"
        return None

    if subcommand == "remote":
        if rest and rest not in (["-v"], ["--verbose"]):
            return "git remote beyond -v adds, renames and rewrites remotes"
        return None

    if subcommand not in _GIT_READ_SUBCOMMANDS:
        return f"git {subcommand} is not in the read-only table"
    return None
