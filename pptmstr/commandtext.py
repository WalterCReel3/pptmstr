"""
A command string made readable: nothing hidden in it, and cut where bash cuts.

Pure functions: no UI, no IO, and nothing here runs the command it describes.
Nothing here decides whether a command may run either -- there is no allowlist
and no refusal. A break-glass command runs outside the sandbox as the operator,
and the operator reading the exact string is the only gate on it
(``planning/2026-10-13-a-break-glass-command-the-operator-runs.md``). This
module is what makes the string something a person can read exactly.

Two things, and they are not equally trustworthy:

- `visible` is the block the operator approves. It is **injective**: two
  different commands never render the same, so whatever the shell will receive
  is on the screen in a form that cannot be mistaken for something else. It
  emits printable ASCII and newline and nothing more, so no terminal, font or
  text-shaping rule gets a say in what is drawn. `invisibles` says the same
  thing in words, because an explicit sentence is what gets noticed.
- `breakdown` is commentary. It reuses ``shellscan._split_segments``, which
  tracks bash's quoting and knows five operators, and that is all it knows:
  it cuts through the middle of a substitution, a here-document and a compound
  command, and a quote mark in a comment can hide a command from it entirely.
  `Breakdown.flags` names what it cannot see into. The one thing it does
  guarantee is arithmetic: its segments and separators add back up to the
  command, so it never shows less than the block does.

``ADVISORY`` exists because of that asymmetry. A breakdown read *instead of*
the block weakens the gate, and the row has to say so every time.
"""

from __future__ import annotations

import enum
import re
import shlex
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .shellscan import _SEPARATORS, _split_segments

HEADING = "segments, as bash would sequence them"
ADVISORY = (
    "This breakdown is advisory: it is pptmstr's reading of the command, it can be "
    "wrong, and a note missing from it proves nothing. The block above is exactly "
    "what runs. Read that."
)

# --- visible ---------------------------------------------------------------
#
# The scheme, whole. A backslash in the output always begins one of these and
# the character after it says which, so no two overlap:
#
#   \\          a backslash
#   \n + break  a newline; the marker comes first and the real line break
#               follows it, so a break with no marker in front is the pane
#               wrapping and never the command
#   \r          a carriage return
#   \xNN        a C0 control or DEL, two upper-case hex digits; also \x20 for a
#               space, and only where the space ends a line
#   \u{H...}    any code point from U+0080 up, upper-case hex, no padding
#
# `\xNN` is confined to code points below 0x80 on purpose. There the code
# point and the byte the shell receives are the same number; above it they are
# not (U+00E9 reaches the shell as C3 A9), and `\xE9` would read as a byte that
# is never sent.

_BACKSLASH = "\\"
_PRINTABLE_LOW = 0x20
_PRINTABLE_HIGH = 0x7E

# Spaces and tabs with nothing after them on their line. A tab is escaped
# wherever it is; a space only here, where it would otherwise be drawn as
# nothing at all. `\Z` and not `$`: `$` also matches before a final newline,
# which is already covered by the `\n` arm.
_TRAILING = re.compile(r"[ \t]+(?=\n|\Z)")


def _trailing_runs(text: str) -> list[tuple[int, int]]:
    return [match.span() for match in _TRAILING.finditer(text)]


def _shown(char: str, *, ends_line: bool) -> str:
    if char == _BACKSLASH:
        return _BACKSLASH * 2
    if char == "\n":
        return _BACKSLASH + "n\n"
    if char == "\r":
        return _BACKSLASH + "r"
    code = ord(char)
    if char == " ":
        return _BACKSLASH + "x20" if ends_line else " "
    if _PRINTABLE_LOW <= code <= _PRINTABLE_HIGH:
        return char
    if code < 0x80:
        return f"{_BACKSLASH}x{code:02X}"
    return f"{_BACKSLASH}u{{{code:X}}}"


def visible(text: str) -> str:
    """
    ``text`` with every character the eye could miss or misread spelled out.

    Injective, and emitting only 0x20-0x7E and newline. The scheme is in the
    comment above; the two properties are what the row relies on and what
    ``tests/test_commandtext.py`` holds it to, with an inverse written
    independently in the test.
    """
    trailing: set[int] = set()
    for begin, end in _trailing_runs(text):
        trailing.update(range(begin, end))
    return "".join(_shown(char, ends_line=index in trailing) for index, char in enumerate(text))


# --- invisibles ------------------------------------------------------------


class Kind(enum.Enum):
    """
    Why a character is worth a sentence.

    A classification for the wording only. `visible` escapes every character
    outside printable ASCII whatever its kind, so a character sorted into the
    wrong one of these is described loosely and is still on the screen.
    """

    CONTROL = "control"
    CARRIAGE_RETURN = "carriage return"
    BIDI = "bidirectional control"
    ZERO_WIDTH = "zero-width"
    LOOKALIKE = "look-alike"
    NON_ASCII = "non-ASCII"
    TRAILING_WHITESPACE = "trailing whitespace"


@dataclass(frozen=True, slots=True)
class Finding:
    """
    One thing in a command that `visible` had to spell out.

    ``index`` is an offset into the command; ``line`` and ``column`` are the
    same place counted from one, a line ending at each newline. ``text`` is
    the raw character, or the whole run for trailing whitespace. ``name`` is
    the code point and its Unicode name where it has one.
    """

    kind: Kind
    index: int
    line: int
    column: int
    text: str
    name: str


# The explicit and implicit directional formatting characters, plus the Arabic
# letter mark. Each reorders how its neighbours are drawn without being drawn.
_BIDI = frozenset("؜‎‏‪‫‬‭‮⁦⁧⁨⁩")

# Drawn as nothing. Unicode's `Cf` category is added to this in `_kind`; these
# are the ones it misses -- the Hangul fillers are letters by category.
_ZERO_WIDTH = frozenset("­ᅟᅠ​‌‍⁠ㅤ﻿")
_VARIATION_SELECTORS = ((0xFE00, 0xFE0F), (0xE0100, 0xE01EF))

# Punctuation that passes for an ASCII operator or flag dash and that
# compatibility normalisation does not fold to one: the minus sign, the
# fraction and division slashes, the ratio, and the Greek question mark that
# is drawn as `;`.
_LOOKALIKE_PUNCTUATION = frozenset("−⁄∕∶;ǀ∣⧸")
_LOOKALIKE_SCRIPTS = ("CYRILLIC", "GREEK")


def _is_lookalike(char: str, category: str) -> bool:
    # Not a confusables table, and not presented as one: other spaces, every
    # dash, anything compatibility-equivalent to ASCII (fullwidth and
    # mathematical letters, ligatures), and the two scripts whose letters most
    # often stand in for Latin ones. A look-alike this misses is reported as
    # `NON_ASCII` and is escaped all the same.
    if category in ("Zs", "Pd") or char in _LOOKALIKE_PUNCTUATION:
        return True
    if unicodedata.name(char, "").startswith(_LOOKALIKE_SCRIPTS):
        return True
    folded = unicodedata.normalize("NFKC", char)
    return bool(folded) and folded.isascii()


def _kind(char: str) -> Kind:
    if char == "\r":
        return Kind.CARRIAGE_RETURN
    code = ord(char)
    if code < 0x80:
        return Kind.CONTROL
    if char in _BIDI:
        return Kind.BIDI
    category = unicodedata.category(char)
    if (
        char in _ZERO_WIDTH
        or category == "Cf"
        or any(low <= code <= high for low, high in _VARIATION_SELECTORS)
    ):
        return Kind.ZERO_WIDTH
    # C1 controls, the line and paragraph separators, and a lone surrogate,
    # which is not a character at all.
    if category in ("Cc", "Zl", "Zp", "Cs"):
        return Kind.CONTROL
    if _is_lookalike(char, category):
        return Kind.LOOKALIKE
    return Kind.NON_ASCII


def _name(char: str) -> str:
    code = f"U+{ord(char):04X}"
    name = unicodedata.name(char, "")
    return f"{code} {name}" if name else code


def invisibles(text: str) -> tuple[Finding, ...]:
    """
    Everything `visible` escaped in ``text``, in order, with where it is.

    Two escapes are not findings, because neither is something hiding: a
    newline, which the block shows as a marked line break, and a backslash,
    which is doubled. Whitespace ending a line is one finding for the whole
    run, tabs included.
    """
    runs = dict(_trailing_runs(text))
    findings: list[Finding] = []
    line = column = 1
    skip_to = 0
    for index, char in enumerate(text):
        if index >= skip_to:
            if index in runs:
                skip_to = runs[index]
                findings.append(
                    Finding(
                        kind=Kind.TRAILING_WHITESPACE,
                        index=index,
                        line=line,
                        column=column,
                        text=text[index:skip_to],
                        name="whitespace ending a line",
                    )
                )
            elif char != "\n" and not _PRINTABLE_LOW <= ord(char) <= _PRINTABLE_HIGH:
                findings.append(
                    Finding(
                        kind=_kind(char),
                        index=index,
                        line=line,
                        column=column,
                        text=char,
                        name=_name(char),
                    )
                )
        if char == "\n":
            line += 1
            column = 1
        else:
            column += 1
    return tuple(findings)


_NOUNS: dict[Kind, tuple[str, str]] = {
    Kind.CONTROL: ("control character", "control characters"),
    Kind.CARRIAGE_RETURN: ("carriage return", "carriage returns"),
    Kind.ZERO_WIDTH: ("zero-width character", "zero-width characters"),
    Kind.LOOKALIKE: (
        "non-ASCII character that can pass for an ASCII one",
        "non-ASCII characters that can pass for ASCII ones",
    ),
    Kind.NON_ASCII: ("other non-ASCII character", "other non-ASCII characters"),
    Kind.TRAILING_WHITESPACE: ("line ending in whitespace", "lines ending in whitespace"),
}


def _counted(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def describe(findings: tuple[Finding, ...]) -> str:
    """
    ``findings`` as one sentence for the row, or "" when there are none.

    Bidirectional controls are counted under their own names -- "1
    right-to-left override" -- since which one it is says what it does to the
    line. Everything else is counted by kind.
    """
    if not findings:
        return ""
    by_kind = Counter(finding.kind for finding in findings)
    bidi = Counter(
        unicodedata.name(finding.text, "bidirectional control").lower()
        for finding in findings
        if finding.kind is Kind.BIDI
    )
    parts = [_counted(count, name, name + "s") for name, count in sorted(bidi.items())]
    parts += [_counted(by_kind[kind], *nouns) for kind, nouns in _NOUNS.items() if by_kind[kind]]
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"This command contains {listed}."


# --- breakdown -------------------------------------------------------------


class FlagKind(enum.Enum):
    COMMAND_SUBSTITUTION = "command substitution"
    PROCESS_SUBSTITUTION = "process substitution"
    BACKGROUND = "background"
    HEREDOC = "here-document"
    EVAL = "eval"
    SHELL_C = "shell -c"
    SOURCE = "source"
    PIPE_TO_SHELL = "pipe into a shell"
    REDIRECTION = "redirection"
    SUDO = "sudo"
    COMPOUND = "compound command"
    GROUPING = "grouping"
    COMMENT = "comment"
    CONTINUATION = "line continuation"
    DOLLAR_QUOTE = "dollar quote"
    EXPANSION = "expansion"
    UNTOKENISABLE = "unbalanced quotes"
    NOT_SPLIT = "not split"
    RUNS_GIT = "git"
    RUNS_MAKE = "make"
    RUNS_PACKAGE_SCRIPTS = "package scripts"
    RUNS_PYTHON_BUILD = "python build"
    RUNS_INTERPRETER = "interpreter"
    RUNS_RELATIVE_PATH = "relative path"
    RUNS_SESSION_PATH = "session path"
    RUNS_PROJECT_TOOL = "project tool"


@dataclass(frozen=True, slots=True)
class Flag:
    """
    Something in the command the segments cannot be trusted about.

    ``at`` is where the scan matched, as offsets into the command; empty for
    the two kinds that are a fact about the whole string.
    """

    kind: FlagKind
    sentence: str
    at: tuple[int, ...]


SENTENCES: dict[FlagKind, str] = {
    FlagKind.COMMAND_SUBSTITUTION: (
        "It contains $(...) or backticks: bash runs the commands inside and pastes "
        "their output into the line, and the segments may be cut through the middle "
        "of one."
    ),
    FlagKind.PROCESS_SUBSTITUTION: (
        "It contains <(...) or >(...): bash runs the commands inside as processes of "
        "their own, and the segments may be cut through the middle of one."
    ),
    FlagKind.BACKGROUND: (
        "It contains a lone &: bash starts what is before it in the background and "
        "goes straight on to what follows. The breakdown does not cut there."
    ),
    FlagKind.HEREDOC: (
        "It contains <<: the lines of a here-document are input to a command and not "
        "commands, though the breakdown cuts them as if they were."
    ),
    FlagKind.EVAL: (
        "It names eval, which runs its arguments as a new command line that no "
        "breakdown can show."
    ),
    FlagKind.SHELL_C: (
        "It hands a string to a shell with -c: that string is a command line of its "
        "own, and it appears here only as an argument."
    ),
    FlagKind.SOURCE: (
        "It uses source or a lone dot, which runs the commands in a file inside this " "same shell."
    ),
    FlagKind.PIPE_TO_SHELL: (
        "It pipes into a shell: whatever the left side prints is run as commands, and "
        "none of that text is on this row."
    ),
    FlagKind.REDIRECTION: (
        "It contains >: output redirected to a file creates that file, empties it or "
        "appends to it."
    ),
    FlagKind.SUDO: (
        "It names sudo: what follows runs as another user, usually root. Where sudo "
        "asks you for no password -- one given in the last few minutes, or a NOPASSWD "
        "rule -- it runs at once and nothing stops it. Where it asks, there is no "
        "terminal to answer at and it waits until the timeout."
    ),
    FlagKind.COMPOUND: (
        "It uses shell keywords (if, for, while, case, function and the like): the "
        "segments are pieces of one compound command, not commands that each run once "
        "in order."
    ),
    FlagKind.GROUPING: (
        "It contains ( ... ) or { ... }: a subshell, a group or a function body, which "
        "the breakdown cuts into pieces."
    ),
    FlagKind.COMMENT: (
        "It contains # where bash starts a comment: bash ignores the rest of that "
        "line and the breakdown does not, so a quote mark inside the comment can hide "
        "where the next command starts."
    ),
    FlagKind.CONTINUATION: (
        "It contains a backslash at the end of a line: bash deletes the backslash and "
        "the line break and reads the two lines as one, so a word cut there is a "
        "single word when it runs and no line of the block shows it whole."
    ),
    FlagKind.DOLLAR_QUOTE: (
        "It contains $'...' or $\"...\": bash reads a backslash inside these "
        "differently from ordinary quotes, and the breakdown may misjudge where the "
        "quoting ends."
    ),
    FlagKind.EXPANSION: (
        "It contains $ expansions: bash substitutes their values when it runs, and "
        "this row shows the names and not the values."
    ),
    FlagKind.UNTOKENISABLE: (
        "Its quotes do not pair up as pptmstr reads them. bash refuses an unclosed "
        "quote as a syntax error, but only from the line it is on: lines above it "
        "have already run. A quote inside a comment, a here-document or $'...' is one "
        "bash does not count, and then the command runs."
    ),
    FlagKind.NOT_SPLIT: (
        "pptmstr could not cut this command into segments that add back up to it, so "
        "it is shown as one piece."
    ),
    FlagKind.RUNS_GIT: (
        "It names git: git runs this repository's hooks (pre-commit, pre-push, "
        "post-checkout and the rest) and honours its config -- aliases, pagers, "
        "credential and diff helpers -- and this session can write all of them."
    ),
    FlagKind.RUNS_MAKE: (
        "It names make: make runs the recipes in the Makefile it finds in the "
        "directory, and every file they include or call. This session can write "
        "them."
    ),
    FlagKind.RUNS_PACKAGE_SCRIPTS: (
        "It names npm, npx, yarn, pnpm, pnpx, bun or bunx: these run the scripts in package.json "
        "-- preinstall and postinstall among them, and those of every dependency -- "
        "and read .npmrc from the directory. This session can write them."
    ),
    FlagKind.RUNS_PYTHON_BUILD: (
        "It names pip, pipx, pipenv, uv, uvx, poetry, pdm, hatch or tox: installing or "
        "building a project from a local path runs its setup.py or build backend, and "
        "these read pyproject.toml and their own config from the directory. This "
        "session can write them."
    ),
    FlagKind.RUNS_INTERPRETER: (
        "It names an interpreter (python, node, ruby, perl, a shell and the like): "
        "what it is handed to run -- a script, a module, a test suite and what that "
        "imports -- is read from the directory unless the path says otherwise, and "
        "is then files this session can write."
    ),
    FlagKind.RUNS_RELATIVE_PATH: (
        "It names a path inside the session's directory (./, ../ or a program given "
        "as dir/name): where that is the program, what runs IS a file this session "
        "can write, and none of that file is on this row."
    ),
    FlagKind.RUNS_SESSION_PATH: (
        "It names the session's directory in full ($PWD, $(pwd), or its path written "
        "out): what is under that is files this session can write, as much as under "
        "./ is, and none of them is on this row."
    ),
    FlagKind.RUNS_PROJECT_TOOL: (
        "It names a tool that does what files in the directory tell it to (bundle, "
        "rake, composer, cmake, just, mvn, gradle, conda, nox, pre-commit, gh, mypy, "
        "eslint, jest, cargo, go, docker and their like): a Rakefile, a justfile, a "
        "build script, a plugin or a config file there is code to it, and this "
        "session can write them."
    ),
}

# The flags that say WHAT in the session's directory the command will run,
# beyond the string itself. A row draws these apart from the rest, ahead of
# the breakdown: they are about the files behind the command and not about how
# its text was cut. Like every other flag they are quote-blind, they over-flag
# (``go`` and ``just`` are words of English too), and one missing proves
# nothing -- a program not named here may run files from the directory all the
# same (anything with a config file or a plugin), and ``ADVISORY`` is what
# says so.
RUNS_FROM_THE_TREE = frozenset(
    {
        FlagKind.RUNS_GIT,
        FlagKind.RUNS_MAKE,
        FlagKind.RUNS_PACKAGE_SCRIPTS,
        FlagKind.RUNS_PYTHON_BUILD,
        FlagKind.RUNS_INTERPRETER,
        FlagKind.RUNS_RELATIVE_PATH,
        FlagKind.RUNS_SESSION_PATH,
        FlagKind.RUNS_PROJECT_TOOL,
    }
)

# Every scan below is over the raw string and is quote-blind on purpose, the
# same choice `shellscan._METACHARACTERS` makes and for the same reason: a
# scan that honours quoting has to get quoting right, and this one is here to
# cover for a splitter that sometimes does not. The price is a note about a
# `>` that was inside quotes, which costs the operator a second look.
#
# What no scan of the text can do is see a word that is assembled when the
# command runs -- `ev""al`, `$cmd`, a backslash in the middle of `sudo`. That
# is why `ADVISORY` says a missing note proves nothing.

# A name standing as a word: not the tail of a longer name, an option or a
# variable, and not the stem of a file name. A leading `/` is allowed so that
# `/usr/bin/sudo` and `/bin/bash -c` are caught.
_BEFORE_WORD = r"(?<![\w.$-])"
_AFTER_WORD = r"(?![\w./=-])"

_SHELL_NAME = r"(?:(?:sh|bash|zsh|dash|ksh|fish)" + _AFTER_WORD + r"|\$SHELL\b|\$\{SHELL\})"


def _named(*programs: str) -> re.Pattern[str]:
    """
    Any of ``programs`` standing as a word, with or without a directory ahead
    of it and a version behind it: ``python``, ``/usr/bin/python3.12``,
    ``pip3``. Not a file name's stem (``make.log``) and not an option's value
    glued to its name (``--with-git``).
    """
    return re.compile(
        _BEFORE_WORD + "(?:" + "|".join(programs) + r")[0-9]*(?:\.[0-9]+)*" + _AFTER_WORD
    )


_SCANS: tuple[tuple[FlagKind, re.Pattern[str]], ...] = (
    (FlagKind.COMMAND_SUBSTITUTION, re.compile(r"\$\(|`")),
    (FlagKind.PROCESS_SUBSTITUTION, re.compile(r"[<>]\(")),
    # Not `&&`, and not the `&` of a redirection (`2>&1`, `&>f`, `<&3`) or of
    # `|&`.
    (FlagKind.BACKGROUND, re.compile(r"(?<![&|<>])&(?![&>])")),
    (FlagKind.HEREDOC, re.compile(r"<<")),
    (FlagKind.EVAL, re.compile(_BEFORE_WORD + "eval" + _AFTER_WORD)),
    # Anything up to the next operator may stand between the shell and its
    # `-c`, since an option taking a value (`-o pipefail`) puts a bare word
    # there. `bash run.sh -c x` is caught with it, which is the cheap error.
    (
        FlagKind.SHELL_C,
        re.compile(_BEFORE_WORD + _SHELL_NAME + r"[\"']?[^;&|\n]*?[ \t]-[A-Za-z]*c(?![\w-])"),
    ),
    (
        FlagKind.PIPE_TO_SHELL,
        re.compile(
            r"(?<!\|)\|(?!\|)&?[ \t]*(?:(?:sudo|exec|env|command)[ \t]+)*[\"']?(?:[\w./-]*/)?"
            + _SHELL_NAME
        ),
    ),
    # `>(` is counted too: a process substitution that output is sent into.
    (FlagKind.REDIRECTION, re.compile(r"(?<!>)>>?")),
    (FlagKind.SUDO, re.compile(_BEFORE_WORD + "sudo" + _AFTER_WORD)),
    # `(` that is not the opening of a substitution, `{` standing alone as a
    # word. `${x}`, `{a,b}` and `{}` are none of these.
    (FlagKind.GROUPING, re.compile(r"(?<![$<>])\(|(?<![\w$])\{(?=\s)")),
    # Bash starts a comment at `#` only where a word could start.
    (FlagKind.COMMENT, re.compile(r"(?:^|(?<=[\s;&|()]))#")),
    # Inside single quotes the pair is literal, and a doubled backslash ahead
    # of a line break is not a join; both are noted all the same.
    (FlagKind.CONTINUATION, re.compile(r"\\\n")),
    (FlagKind.DOLLAR_QUOTE, re.compile(r"\$['\"]")),
    (FlagKind.EXPANSION, re.compile(r"\$[A-Za-z_{@*#?!$0-9-]")),
    (FlagKind.RUNS_GIT, _named("git")),
    (FlagKind.RUNS_MAKE, _named("make", "gmake")),
    (FlagKind.RUNS_PACKAGE_SCRIPTS, _named("npm", "npx", "yarn", "pnpm", "pnpx", "bun", "bunx")),
    (
        FlagKind.RUNS_PYTHON_BUILD,
        _named("pip", "pipx", "pipenv", "uv", "uvx", "poetry", "pdm", "hatch", "tox"),
    ),
    (
        FlagKind.RUNS_INTERPRETER,
        _named(
            "python",
            "ipython",
            "pypy",
            "node",
            "nodejs",
            "deno",
            "ruby",
            "perl",
            "php",
            "sh",
            "bash",
            "zsh",
            "dash",
            "ksh",
            "fish",
            "pytest",
            r"py\.test",
        ),
    ),
    (
        FlagKind.RUNS_PROJECT_TOOL,
        _named(
            "bundle",
            "rake",
            "composer",
            "cmake",
            "just",
            "mvn",
            "mvnw",
            "gradle",
            "gradlew",
            "conda",
            "nox",
            "pre-commit",
            "gh",
            "mypy",
            "eslint",
            "jest",
            "cargo",
            "go",
            "docker",
            "docker-compose",
            "podman",
        ),
    ),
    # The directory the command starts in, named by the shell. ``$(pwd)`` and
    # a backticked ``pwd`` with it: either is that directory until a ``cd``.
    (
        FlagKind.RUNS_SESSION_PATH,
        re.compile(r"\$PWD\b|\$\{PWD\}|\$\([ \t]*pwd[ \t]*\)|`[ \t]*pwd[ \t]*`"),
    ),
    # A word that begins ``./`` or ``../`` wherever it stands -- an argument
    # that is a path under the directory is noted with the program that is one.
    (FlagKind.RUNS_RELATIVE_PATH, re.compile(r"(?<![\w.$/~-])\.\.?/")),
)

# Reserved words are reserved only where a command could begin, and `source`
# and `.` are commands only there, so each match is kept only if what precedes
# it ends at such a place: the start, an operator or bracket, or a word after
# which bash expects another command.
_RESERVED = re.compile(
    _BEFORE_WORD
    + r"(?:if|then|elif|else|fi|for|while|until|do|done|case|esac|select|function)"
    + _AFTER_WORD
)
# A program given as ``dir/name``: a word with a slash that does not begin with
# one, with ``~`` or with ``$``, standing where a command could begin.
_RELATIVE_PROGRAM = re.compile(r"(?<![\w.$/~-])[\w.+-]+/[\w./+-]*")
_SOURCE = re.compile(_BEFORE_WORD + "source" + _AFTER_WORD + r"|(?<![\w.$/-])\.(?=[ \t]+\S)")
# What may stand between where a command could begin and the word that is run:
# a program that runs its argument (``nohup bin/run``, ``xargs -n 1 bin/run``),
# with its options and their numbers, and an assignment (``FOO=1 bin/run``).
# Not a reading of any one of them: an option that takes a word for a value
# ends the run of prefixes, and what follows it is then not noted.
_RUNS_ITS_ARGUMENT = (
    r"(?:nohup|exec|command|builtin|env|xargs|time|nice|ionice|setsid|stdbuf|timeout|sudo|doas)"
)
_PREFIX = (
    r"(?:"
    + _RUNS_ITS_ARGUMENT
    + r"(?:[ \t]+(?:-[^\s;&|]*|[0-9.]+[smhd]?))*|[A-Za-z_][A-Za-z0-9_]*=[^\s;&|]*)[ \t]+"
)
_COMMAND_MAY_BEGIN = re.compile(
    r"(?:^|[;&|\n(){}!]|"
    + _BEFORE_WORD
    + r"(?:then|do|else|elif|if|while|until|time))[ \t]*(?:"
    + _PREFIX
    + r")*\Z"
)


def _where_a_command_begins(pattern: re.Pattern[str], text: str) -> tuple[int, ...]:
    return tuple(
        match.start()
        for match in pattern.finditer(text)
        if _COMMAND_MAY_BEGIN.search(text, 0, match.start())
    )


def _tokenises(text: str) -> bool:
    try:
        shlex.split(text)
    except ValueError:
        return False
    return True


def spellings(cwd: str, home: str | None = None) -> tuple[str, ...]:
    """
    The ways a command may write the directory ``cwd`` out in full: as it is,
    and, where it is under ``home``, from ``~``, ``$HOME`` and ``${HOME}``.
    What ``breakdown`` is handed as ``roots``.

    A string and nothing read from a disk: a link to the directory, and a path
    that reaches it through ``..``, are spellings this does not know.
    """
    plain = cwd.rstrip("/")
    if not plain.startswith("/"):
        return ()
    found = [plain]
    base = (home or "").rstrip("/")
    if base.startswith("/") and plain.startswith(base + "/"):
        rest = plain[len(base) :]
        found += [f"~{rest}", f"$HOME{rest}", f"${{HOME}}{rest}"]
    return tuple(found)


def _rooted(text: str, roots: tuple[str, ...]) -> tuple[int, ...]:
    """
    Where ``text`` writes one of ``roots`` out: the whole of it, not the stem of a longer name.
    """
    if not roots:
        return ()
    pattern = re.compile("(?:" + "|".join(re.escape(root) for root in roots) + r")(?![\w.+-])")
    return tuple(match.start() for match in pattern.finditer(text))


def _flags(text: str, roots: tuple[str, ...] = ()) -> tuple[Flag, ...]:
    found: dict[FlagKind, tuple[int, ...]] = {
        kind: tuple(match.start() for match in pattern.finditer(text)) for kind, pattern in _SCANS
    }
    found[FlagKind.RUNS_SESSION_PATH] = tuple(
        sorted({*found[FlagKind.RUNS_SESSION_PATH], *_rooted(text, roots)})
    )
    found[FlagKind.COMPOUND] = _where_a_command_begins(_RESERVED, text)
    found[FlagKind.SOURCE] = _where_a_command_begins(_SOURCE, text)
    found[FlagKind.RUNS_RELATIVE_PATH] = tuple(
        sorted(
            {
                *found[FlagKind.RUNS_RELATIVE_PATH],
                *_where_a_command_begins(_RELATIVE_PROGRAM, text),
            }
        )
    )
    flags = [Flag(kind, SENTENCES[kind], found[kind]) for kind in FlagKind if found.get(kind)]
    if not _tokenises(text):
        flags.append(Flag(FlagKind.UNTOKENISABLE, SENTENCES[FlagKind.UNTOKENISABLE], ()))
    return tuple(flags)


@dataclass(frozen=True, slots=True)
class Breakdown:
    """
    A command as segments, the separators between them, and what to distrust.

    ``separators`` has one entry fewer than ``segments``: ``separators[i]``
    stands between ``segments[i]`` and ``segments[i + 1]``. Each segment is
    the whole substring, whitespace included, and is to be shown whole -- a
    first word alone is exactly the summary that hides an argument.
    """

    segments: tuple[str, ...]
    separators: tuple[str, ...]
    flags: tuple[Flag, ...]

    def reassembled(self) -> str:
        """
        The segments and separators interleaved: the command, exactly.
        """
        pieces = [self.segments[0]]
        for separator, segment in zip(self.separators, self.segments[1:], strict=True):
            pieces += [separator, segment]
        return "".join(pieces)


def _separators(text: str, segments: list[str]) -> tuple[str, ...] | None:
    """
    The operator the splitter consumed after each segment but the last, or
    None if ``segments`` are not ``text`` cut at operators.

    The splitter returns what lies between the operators and not the
    operators, so each is recovered by asking the same pattern at the same
    offset. None is the answer for a splitter that has started trimming,
    dropping or reordering, which this module has no other way to notice.
    """
    separators: list[str] = []
    cursor = 0
    for position, segment in enumerate(segments):
        if not text.startswith(segment, cursor):
            return None
        cursor += len(segment)
        if position == len(segments) - 1:
            break
        operator = _SEPARATORS.match(text, cursor)
        if operator is None:
            return None
        separators.append(operator.group())
        cursor = operator.end()
    return tuple(separators) if cursor == len(text) else None


def breakdown(text: str, roots: tuple[str, ...] = ()) -> Breakdown:
    """
    ``text`` as ``shellscan``'s splitter cuts it, with nothing left out.

    ``roots`` is the session's directory as a command might write it out
    (``spellings``), for ``RUNS_SESSION_PATH``. With none, only the shell's own
    names for it are noted.

    ``Breakdown.reassembled()`` is ``text`` for every input. If the splitter's
    answer cannot be laid back over the command, the command is returned as a
    single segment under a `NOT_SPLIT` flag rather than as pieces that have
    lost part of it.
    """
    flags = _flags(text, roots)
    segments = _split_segments(text)
    separators = _separators(text, segments) if segments else None
    if separators is None:
        unsplit = Flag(FlagKind.NOT_SPLIT, SENTENCES[FlagKind.NOT_SPLIT], ())
        return Breakdown(segments=(text,), separators=(), flags=(*flags, unsplit))
    return Breakdown(segments=tuple(segments), separators=separators, flags=flags)
