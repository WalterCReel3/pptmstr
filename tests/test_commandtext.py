"""
A break-glass command is read by the operator and then run as them, outside
the sandbox. These tests hold `pptmstr.commandtext` to the two properties that
reading depends on:

- `visible` is injective and emits only printable ASCII and newline, so two
  commands cannot be made to look alike and nothing in one can go undrawn;
- a `Breakdown`'s segments and separators add back up to the command, so the
  commentary never shows less than the block does.

Injectivity is checked two ways that share no code: an inverse written here,
and a direct collision search over every short string in the characters the
escape scheme is built from. The claims the module's sentences make about
bash are run against /bin/bash rather than recalled.
"""

from __future__ import annotations

import ast
import itertools
import random
import subprocess
from pathlib import Path

import pytest

from pptmstr import commandtext, shellscan
from pptmstr.commandtext import (
    ADVISORY,
    HEADING,
    RUNS_FROM_THE_TREE,
    SENTENCES,
    Breakdown,
    FlagKind,
    Kind,
    breakdown,
    describe,
    invisibles,
    visible,
)

BASH = Path("/bin/bash")
needs_bash = pytest.mark.skipif(not BASH.exists(), reason="no /bin/bash to ask")

CONTROLS = "\x00\x01\x07\x08\x1b\x7f\x85\t"
BIDI = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"
# Cyrillic a, e, o, p, c and Greek omicron, nu, Alpha: each drawn like a Latin
# letter in most fonts.
LOOKALIKES = "\u0430\u0435\u043e\u0440\u0441\u03bf\u03bd\u0391"
SHELL = "'\";|&$()`<>#{} -"

# Weighted towards what the escape scheme has to keep apart: the backslash and
# the letters and digits that follow it in an escape, the two line endings, and
# the space whose rendering depends on what comes after it.
ALPHABET = (
    list(CONTROLS + BIDI + ZERO_WIDTH + LOOKALIKES + SHELL)
    + ["\\"] * 8
    + ["x", "u", "n", "r", "{", "}"] * 3
    + list("0123456789ABCDEFabcdef")
    + [" "] * 10
    + ["\n"] * 6
    + ["\r"] * 3
    + ["\t"] * 2
    + list("lsat")
    + ["\U0001f600", "\ud800", "\u00e9", "\u3000", "\u2212"]
)


def corpus(count: int = 4000, seed: int = 20261013) -> list[str]:
    rng = random.Random(seed)
    strings = ["", " ", "\n", "\\", "\\n", "\\\n", " \n ", "a ", "a\t", "a \r\n"]
    for _ in range(count):
        length = rng.choice((1, 2, 3, 5, 8, 13, 40))
        strings.append("".join(rng.choice(ALPHABET) for _ in range(length)))
    return strings


def unvisible(shown: str) -> str:
    """
    The inverse of `visible`, written from the scheme's description and not
    from its code. Strict: anything the scheme could not have produced raises,
    so a round trip cannot succeed by the decoder being forgiving.
    """
    hexdigits = "0123456789ABCDEF"
    out: list[str] = []
    index = 0
    while index < len(shown):
        char = shown[index]
        if char != "\\":
            assert 0x20 <= ord(char) <= 0x7E, f"bare {char!r} at {index}"
            out.append(char)
            index += 1
            continue
        tag = shown[index + 1]
        if tag == "\\":
            out.append("\\")
            index += 2
        elif tag == "n":
            assert shown[index + 2] == "\n", "a newline marker with no line break after it"
            out.append("\n")
            index += 3
        elif tag == "r":
            out.append("\r")
            index += 2
        elif tag == "x":
            digits = shown[index + 2 : index + 4]
            assert len(digits) == 2 and all(d in hexdigits for d in digits), digits
            code = int(digits, 16)
            assert code < 0x80
            out.append(chr(code))
            index += 4
        elif tag == "u":
            assert shown[index + 2] == "{"
            close = shown.index("}", index)
            digits = shown[index + 3 : close]
            assert digits and all(d in hexdigits for d in digits), digits
            assert not digits.startswith("0"), "padded, so one code point has two spellings"
            code = int(digits, 16)
            assert code >= 0x80
            out.append(chr(code))
            index = close + 1
        else:
            raise AssertionError(f"unknown escape \\{tag} at {index}")
    return "".join(out)


# --- visible ---------------------------------------------------------------


def test_visible_round_trips_through_an_independent_inverse() -> None:
    strings = corpus()
    assert len(set(strings)) > 3000, "the corpus collapsed, so the round trip proves little"
    for text in strings:
        assert unvisible(visible(text)) == text, repr(text)


def test_no_two_short_strings_over_the_scheme_s_own_characters_render_alike() -> None:
    """
    Injectivity with no decoder in the argument: every string up to length
    four over the characters an escape is spelled with, plus one of each thing
    that gets escaped. A scheme that forgot to double the backslash renders
    the control character U+0001 and the four typed characters backslash, x,
    0, 1 identically, and this is where that would show.
    """
    characters = ["\\", "x", "u", "n", "r", "0", "1", "2", "{", "}", " ", "\n", "\r", "\x01"]
    characters += ["\x10", "\x12", "\u0100", "\u1001", "\u202e"]
    seen: dict[str, str] = {}
    for length in range(0, 5):
        for letters in itertools.product(characters, repeat=length):
            text = "".join(letters)
            shown = visible(text)
            assert seen.setdefault(shown, text) == text, (seen[shown], text, shown)
    assert len(seen) > 100_000


def test_visible_emits_only_printable_ascii_and_newline() -> None:
    for text in corpus():
        shown = visible(text)
        assert all(char == "\n" or 0x20 <= ord(char) <= 0x7E for char in shown), repr(text)


def test_a_line_break_in_the_output_always_follows_a_newline_marker() -> None:
    """
    What lets the pane wrap the block: a break with no marker before it is the
    pane's, and a break with one is the command's.
    """
    for text in corpus():
        shown = visible(text)
        assert shown.count("\n") == text.count("\n"), repr(text)
        for line in shown.split("\n")[:-1]:
            body = line[: -len("\\n")]
            assert line.endswith("\\n"), repr(text)
            # The marker's backslash is its own, not the second half of a
            # doubled one: an odd number of backslashes ends the line.
            assert (len(body) - len(body.rstrip("\\"))) % 2 == 0, repr(text)


def test_no_line_of_the_output_ends_in_a_space() -> None:
    for text in corpus():
        for line in visible(text).split("\n"):
            assert not line.endswith(" "), repr(text)
            assert not line.removesuffix("\\n").endswith(" "), repr(text)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("ls -la", "ls -la"),
        ("a\\b", "a\\\\b"),
        # The typed characters backslash-x-4-1 are not the letter A, and not
        # an escape either.
        ("\\x41", "\\\\x41"),
        ("\x01", "\\x01"),
        ("\x7f", "\\x7F"),
        ("a\tb", "a\\x09b"),
        ("a\rb", "a\\rb"),
        ("a\nb", "a\\n\nb"),
        ("a\\nb", "a\\\\nb"),
        ("ls \n", "ls\\x20\\n\n"),
        ("ls  ", "ls\\x20\\x20"),
        ("ls \t ", "ls\\x20\\x09\\x20"),
        ("a b", "a b"),
        # A space before a carriage return is followed by something drawn.
        ("a \r", "a \\r"),
        ("\u00e9", "\\u{E9}"),
        ("\u202e", "\\u{202E}"),
        ("\U0001f600", "\\u{1F600}"),
        ("\ud800", "\\u{D800}"),
        ("r\u043c", "r\\u{43C}"),
    ],
)
def test_the_scheme_as_documented(text: str, shown: str) -> None:
    assert visible(text) == shown


# --- invisibles ------------------------------------------------------------


@pytest.mark.parametrize(
    ("char", "kind"),
    [
        ("\x00", Kind.CONTROL),
        ("\x1b", Kind.CONTROL),
        ("\x7f", Kind.CONTROL),
        ("\x85", Kind.CONTROL),
        ("\u2028", Kind.CONTROL),
        ("\ud800", Kind.CONTROL),
        ("\r", Kind.CARRIAGE_RETURN),
        *[(char, Kind.BIDI) for char in BIDI + "\u200e\u200f"],
        *[(char, Kind.ZERO_WIDTH) for char in ZERO_WIDTH + "\u00ad\u2060\ufe0f\U000e0041"],
        *[(char, Kind.LOOKALIKE) for char in LOOKALIKES],
        # A no-break space, an ideographic space, a hyphen that is not `-`, a
        # minus sign, a fullwidth semicolon and the Greek question mark.
        *[(char, Kind.LOOKALIKE) for char in "\u00a0\u3000\u2010\u2212\uff1b\u037e"],
        ("\u00e9", Kind.NON_ASCII),
        ("\u4e2d", Kind.NON_ASCII),
        ("\U0001f600", Kind.NON_ASCII),
    ],
)
def test_each_character_is_sorted_into_the_kind_its_sentence_names(char: str, kind: Kind) -> None:
    (finding,) = invisibles(f"a{char}b")
    assert (finding.kind, finding.index, finding.text) == (kind, 1, char)
    assert finding.name.startswith(f"U+{ord(char):04X}")


def test_a_finding_says_where_in_lines_and_columns() -> None:
    findings = invisibles("ls\n  a\u200bb \nc\td")
    assert [(f.kind, f.index, f.line, f.column, f.text) for f in findings] == [
        (Kind.ZERO_WIDTH, 6, 2, 4, "\u200b"),
        (Kind.TRAILING_WHITESPACE, 8, 2, 6, " "),
        (Kind.CONTROL, 11, 3, 2, "\t"),
    ]


def test_trailing_whitespace_is_one_finding_for_the_run() -> None:
    (finding,) = invisibles("ls \t \nrm")
    assert (finding.kind, finding.index, finding.text) == (Kind.TRAILING_WHITESPACE, 2, " \t ")
    # A final newline leaves an empty last line, which ends in nothing.
    assert invisibles("ls\n") == ()
    assert [f.kind for f in invisibles("ls ")] == [Kind.TRAILING_WHITESPACE]


def test_every_escape_but_newline_and_backslash_is_reported() -> None:
    """
    The sentence may not be quieter than the block: each character `visible`
    rewrites is inside some finding, apart from the two that hide nothing.
    """
    for text in corpus():
        covered: set[int] = set()
        for finding in invisibles(text):
            covered.update(range(finding.index, finding.index + len(finding.text)))
        escaped = {
            index
            for index, char in enumerate(text)
            if char not in "\\\n" and visible(text[: index + 1])[-1:] != char
        }
        # `visible` of a prefix escapes a space that ends the prefix, so the
        # check above over-collects spaces; recompute those against the whole.
        escaped = {
            index
            for index in escaped
            if text[index] != " " or text[index:].lstrip(" \t")[:1] in ("", "\n")
        }
        assert escaped <= covered, repr(text)
        assert all(text[i] in " \t" or not 0x20 <= ord(text[i]) <= 0x7E for i in covered)


def test_plain_ascii_has_nothing_to_report() -> None:
    assert invisibles("git commit -m 'a \\ b' && make install") == ()
    assert describe(()) == ""


def test_the_sentence_counts_and_names_what_was_found() -> None:
    text = "ls\u200b \u200d\u202e x "
    assert describe(invisibles(text)) == (
        "This command contains 1 right-to-left override, 2 zero-width characters "
        "and 1 line ending in whitespace."
    )
    assert describe(invisibles("a\rb")) == "This command contains 1 carriage return."
    assert describe(invisibles("\u0430\u0440\x00")) == (
        "This command contains 1 control character and 2 non-ASCII characters that "
        "can pass for ASCII ones."
    )
    assert describe(invisibles("\u202e\u202e\u2066")) == (
        "This command contains 1 left-to-right isolate and 2 right-to-left overrides."
    )


# --- breakdown: reassembly -------------------------------------------------


def named_cases() -> tuple[str, ...]:
    """
    `NAMED` out of the bash probe, read as a literal and not by importing the
    script, which sits outside any package.
    """
    path = Path(__file__).resolve().parent.parent / "scripts" / "verify_split_against_bash.py"
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and [ast.unparse(t) for t in node.targets] == ["NAMED"]:
            named: tuple[str, ...] = ast.literal_eval(node.value)
            assert len(named) >= 20 and all(isinstance(case, str) for case in named)
            return named
    raise AssertionError("the probe no longer assigns NAMED")


def shell_corpus(count: int = 3000) -> list[str]:
    rng = random.Random(20261014)
    alphabet = "ls a'\"\\;|&\n\r#$()`<> "
    return ["".join(rng.choice(alphabet) for _ in range(rng.randint(1, 12))) for _ in range(count)]


def assert_exact(text: str) -> Breakdown:
    result = breakdown(text)
    assert result.reassembled() == text, repr(text)
    assert len(result.separators) == len(result.segments) - 1, repr(text)
    assert all(sep in ("||", "&&", ";", "|", "\n") for sep in result.separators), repr(text)
    return result


def test_segments_and_separators_reproduce_the_command_exactly() -> None:
    split = 0
    for text in [*corpus(), *shell_corpus(), *named_cases()]:
        result = assert_exact(text)
        assert FlagKind.NOT_SPLIT not in {flag.kind for flag in result.flags}, repr(text)
        split += len(result.segments) > 1
    assert split > 1500, f"almost nothing split, so reassembly was barely tested: {split}"


def test_the_segments_are_the_splitter_s_own_and_whole() -> None:
    for text in [*shell_corpus(), *named_cases()]:
        assert list(breakdown(text).segments) == shellscan._split_segments(text), repr(text)
    result = breakdown("  rm  -rf  /tmp/x  &&make install||echo 'a|b' ; ls\ntrue")
    assert result.segments == ("  rm  -rf  /tmp/x  ", "make install", "echo 'a|b' ", " ls", "true")
    assert result.separators == ("&&", "||", ";", "\n")


@pytest.mark.parametrize(
    "lossy",
    [
        lambda text: [segment.strip() for segment in text.split(";")],
        lambda text: [segment.upper() for segment in text.split(";")],
        lambda text: text.split(";")[:-1],
        lambda text: [],
        lambda text: [text, ""],
        lambda text: list(reversed(text.split(";"))),
    ],
)
def test_a_splitter_that_loses_text_yields_the_whole_command_and_says_so(
    monkeypatch: pytest.MonkeyPatch, lossy: object
) -> None:
    """
    The splitter is another module's private function. If it ever trims or
    drops, the command is still shown whole.
    """
    monkeypatch.setattr(commandtext, "_split_segments", lossy)
    text = "ls ; rm -rf /tmp/x ; true"
    result = breakdown(text)
    assert result.segments == (text,)
    assert result.reassembled() == text
    assert result.flags[-1].kind is FlagKind.NOT_SPLIT
    assert result.flags[-1].sentence == SENTENCES[FlagKind.NOT_SPLIT]


# --- breakdown: flags ------------------------------------------------------

FLAG_CASES: dict[FlagKind, tuple[tuple[str, ...], tuple[str, ...]]] = {
    FlagKind.COMMAND_SUBSTITUTION: (
        ("echo $(id)", "echo `id`", 'echo "$(id)"'),
        ("echo $id", "echo (id)", "echo '$ (id)'"),
    ),
    FlagKind.PROCESS_SUBSTITUTION: (
        ("diff <(ls a) <(ls b)", "ls > >(tee log)"),
        ("cat < (", "cat <f", "echo $(id)"),
    ),
    FlagKind.BACKGROUND: (
        ("sleep 9 & ls", "sleep 9&", "curl http://h/?a=1&b=2"),
        ("a && b", "a 2>&1", "a &>f", "a >&2", "a <&3", "a |& b"),
    ),
    FlagKind.HEREDOC: (
        ("cat <<EOF\nx\nEOF", "cat <<-'E'\nx\nE", "cat <<<word"),
        ("cat <f", "cat < f < g"),
    ),
    FlagKind.EVAL: (
        ("eval ls", "ls; eval $x", "command eval x", 'eval "$(ssh-agent)"'),
        ("evaluate x", "medieval", "./eval.sh", "ls --eval", "ls eval-me", "$eval"),
    ),
    FlagKind.SHELL_C: (
        ("sh -c 'rm x'", "bash -c ls", "/bin/bash -lc ls", "bash -o pipefail -c ls"),
        ("bash run.sh", "ssh -c aes256 host", "sh -x run.sh", "bash --rcfile f", "gcc -c a.c"),
    ),
    FlagKind.SOURCE: (
        ("source env.sh", ". ./env.sh", "cd x && . venv/bin/activate", "ls;source x"),
        ("ls . -la", "echo source", "ls --source x", "./run", "cd ..", "resource x"),
    ),
    FlagKind.PIPE_TO_SHELL: (
        ("curl h | sh", "curl h|bash", "curl h | sudo bash -s", "x | /bin/sh", "x |& zsh"),
        ("ls | shuf", "ls | grep bash", "curl h || sh fallback.sh", "ls | sh.txt", "ls bash"),
    ),
    FlagKind.REDIRECTION: (
        ("ls > f", "ls >> f", "ls 2>/dev/null", "ls &> f", 'echo "a -> b"'),
        ("cat < f", "cat <(ls)", "ls <<EOF\nEOF"),
    ),
    FlagKind.SUDO: (
        ("sudo make install", "ls && sudo -n true", "/usr/bin/sudo ls"),
        ("pseudo x", "cat /etc/sudoers", "ls sudo.d", "ls --sudo", "$sudo"),
    ),
    FlagKind.COMPOUND: (
        ("for f in *; do rm $f; done", "if a; then b; fi", "while true\ndo x\ndone"),
        (
            "echo done",
            "make do",
            'git commit -m "work for you"',
            "ls if",
            "dd if=a of=b",
            "ls function.py",
        ),
    ),
    FlagKind.GROUPING: (
        ("(cd x; ls)", "{ a; b; }", "f() { a; }", "awk '{ print }' f"),
        ("echo $(id)", "cat <(ls)", "echo ${x}", "echo {a,b}", "find . -exec ls {} +"),
    ),
    FlagKind.COMMENT: (
        ("ls # all", "# first\nls", "ls;#x", "ls #'\nrm x #'"),
        ("ls a#b", "echo $#", "echo ${#x}", "curl h/p#frag"),
    ),
    FlagKind.CONTINUATION: (
        ("git log --out\\\nput=x", 'ls "a\\\nb"', "ls \\\n-la", "ls 'a\\\nb'"),
        ("ls a\nls b", "printf 'a\\nb'", "ls a\\ b", "ls a\\"),
    ),
    FlagKind.DOLLAR_QUOTE: (
        ("echo $'a\\'' ; x '", 'echo $"hi"'),
        ("echo '$x'", "echo $ 'a'", 'echo "$x"'),
    ),
    FlagKind.EXPANSION: (
        ("rm -rf $DIR/", "echo ${x}", 'echo "$1"', "echo $?", "kill $$"),
        ("grep 'x$' f", "echo $ x", "echo $(id)", "echo 'a$'"),
    ),
    FlagKind.UNTOKENISABLE: (
        ("echo 'a", 'echo "a', "ls # it's\nrm x"),
        ("echo 'a'", 'echo "it\'s"', "echo a\\'b"),
    ),
    FlagKind.RUNS_GIT: (
        ("git push origin main", "/usr/bin/git status", "cd x && git commit -m y", "sudo git gc"),
        ("legit x", "ls .git", "cat git.log", "./configure --with-git", "echo $git", "gitk"),
    ),
    FlagKind.RUNS_MAKE: (
        ("make install", "gmake -j4", "cd b; make", "/usr/bin/make test"),
        ("cmake ..", "cat make.log", "ls Makefile", "tool --make", "remake x"),
    ),
    FlagKind.RUNS_PACKAGE_SCRIPTS: (
        (
            "npm install",
            "npx tsc",
            "yarn",
            "pnpm i --frozen-lockfile",
            "bun install",
            "bunx tsc",
            "pnpx tsc",
        ),
        ("cat npm-debug.log", "ls .npmrc", "echo yarn.lock", "bundle install"),
    ),
    FlagKind.RUNS_PYTHON_BUILD: (
        (
            "pip install -e .",
            "pip3 install .",
            "python -m pip install x",
            "uv sync",
            "poetry run x",
            "uvx ruff check",
            "pipenv run x",
        ),
        ("cat pip.conf", "ls uv.lock", "pipe x", "echo --pip"),
    ),
    FlagKind.RUNS_INTERPRETER: (
        (
            "python setup.py install",
            "python3.12 -m build",
            "node build.js",
            "bash run.sh",
            "sh ./x",
            "/usr/bin/perl x.pl",
            "pytest -q",
            "py.test -q",
            "nodejs build.js",
            "ipython x.py",
        ),
        ("cat python.txt", "ls node_modules", "echo --python", "bashful x", "cat x.sh"),
    ),
    FlagKind.RUNS_RELATIVE_PATH: (
        (
            "./script.sh",
            "../tools/run",
            "scripts/deploy.sh --now",
            "cd x && bin/go",
            "sudo ./install",
            "pip install -e ./pkg",
            "echo a; tools/x",
            # A program that runs its argument, and an assignment, stand
            # ahead of the word that is run.
            "nohup bin/run",
            "exec bin/run",
            "command bin/run",
            "env bin/run",
            "env -i FOO=1 bin/run",
            "xargs bin/run",
            "xargs -n 1 bin/run",
            "FOO=1 bin/run",
            "FOO=1 BAR=two bin/run",
            "timeout 30 bin/run",
            "ls; nohup nice -n 10 bin/run",
        ),
        (
            "/usr/bin/env x",
            "ls /tmp/a/b",
            "cat a/b",
            "echo ~/x",
            "ls $HOME/bin",
            "echo a.b",
            "echo nohup a/b",
            "echo FOO=1 a/b",
        ),
    ),
    FlagKind.RUNS_SESSION_PATH: (
        ('"$PWD"/x', "$PWD/bin/run", "${PWD}/x", "$(pwd)/x", "cat `pwd`/a", "ls $( pwd )"),
        ("$PWDX/x", "echo PWD", "$OLDPWD/x", "pwd", "ls /work/tree/x", "$(pwdx 1)"),
    ),
    FlagKind.RUNS_PROJECT_TOOL: (
        (
            "bundle exec rspec",
            "rake db:migrate",
            "composer install",
            "cmake --build .",
            "just build",
            "mvn test",
            "gradle build",
            "conda run x",
            "nox",
            "pre-commit run --all-files",
            "gh pr create",
            "mypy .",
            "eslint .",
            "jest",
            "cargo build",
            "go test",
            "docker build .",
            "docker-compose up",
            "/usr/local/bin/cargo test",
        ),
        ("ls go.mod", "cat cargo.toml", "tool --go", "echo $go", "adjust x", "ghc x", "mongo"),
    ),
}


def kinds(text: str) -> set[FlagKind]:
    return {flag.kind for flag in breakdown(text).flags}


def test_every_flag_kind_has_cases_and_a_sentence() -> None:
    # `NOT_SPLIT` is the one no command text can produce; its test is above.
    assert set(FLAG_CASES) == set(FlagKind) - {FlagKind.NOT_SPLIT}
    assert set(SENTENCES) == set(FlagKind)
    assert len(set(SENTENCES.values())) == len(SENTENCES)
    for sentence in SENTENCES.values():
        assert sentence.isascii() and sentence.endswith(".") and "\n" not in sentence


@pytest.mark.parametrize("kind", list(FLAG_CASES), ids=lambda kind: kind.name)
def test_a_flag_is_raised_for_the_construct_and_not_for_its_near_miss(kind: FlagKind) -> None:
    positives, near_misses = FLAG_CASES[kind]
    for text in positives:
        assert kind in kinds(text), f"{kind.name} missed {text!r}"
        assert_exact(text)
    for text in near_misses:
        assert kind not in kinds(text), f"{kind.name} raised for {text!r}"


def test_a_flag_carries_its_sentence_and_where_it_matched() -> None:
    result = breakdown("ls > a; sudo ls >> b")
    by_kind = {flag.kind: flag for flag in result.flags}
    assert set(by_kind) == {FlagKind.REDIRECTION, FlagKind.SUDO}
    assert by_kind[FlagKind.REDIRECTION].at == (3, 16)
    assert by_kind[FlagKind.SUDO].at == (8,)
    assert by_kind[FlagKind.SUDO].sentence == SENTENCES[FlagKind.SUDO]
    # In the enum's order, so the row reads the same for the same command.
    assert [flag.kind for flag in result.flags] == [FlagKind.REDIRECTION, FlagKind.SUDO]


def test_an_ordinary_command_raises_nothing() -> None:
    for text in ("ls -la ; pwd", "echo hello", "cp a b && rm c", "/usr/bin/id"):
        assert breakdown(text).flags == (), text


@pytest.mark.parametrize(
    ("text", "kind", "named"),
    [
        ("git push", FlagKind.RUNS_GIT, "hooks"),
        ("pip install -e .", FlagKind.RUNS_PYTHON_BUILD, "setup.py"),
        ("npm install", FlagKind.RUNS_PACKAGE_SCRIPTS, "package.json"),
        ("make install", FlagKind.RUNS_MAKE, "Makefile"),
        ("./script.sh", FlagKind.RUNS_RELATIVE_PATH, "IS a file this session"),
        ("python manage.py migrate", FlagKind.RUNS_INTERPRETER, "interpreter"),
    ],
)
def test_the_advertised_uses_each_say_what_in_the_directory_runs(
    text: str, kind: FlagKind, named: str
) -> None:
    """
    The string is not the whole of what runs for the commands this tool is
    for. Each gets a sentence of its own naming the files, and each says the
    session can write them.
    """
    by_kind = {flag.kind: flag for flag in breakdown(text).flags}
    assert kind in by_kind and kind in RUNS_FROM_THE_TREE
    assert named in by_kind[kind].sentence
    assert "this session" in by_kind[kind].sentence.lower()


def test_the_flags_about_the_directory_are_a_named_set_and_no_all_clear() -> None:
    """
    ``RUNS_FROM_THE_TREE`` is what a row draws apart from the breakdown. A
    command that names none of the programs it knows still runs whatever that
    program reads, so what stands under the breakdown has to go on saying a
    missing note proves nothing.
    """
    assert RUNS_FROM_THE_TREE == {kind for kind in FlagKind if kind.name.startswith("RUNS_")}
    for text in ("terraform apply", "ansible-playbook site.yml", "bazel build //...", "tsc -b"):
        found = {flag.kind for flag in breakdown(text).flags}
        # Each reads a file of the directory's and runs what it says, and
        # none is a name the scan knows.
        assert not found & RUNS_FROM_THE_TREE, text
    assert "a note missing from it proves nothing" in ADVISORY


def test_the_sessions_directory_written_out_in_full_is_noted_when_the_row_knows_it() -> None:
    """
    ``/work/tree/bin/run`` is a file the session wrote as much as ``./bin/run``
    is, and nothing in the text says so: only the caller knows the directory.
    """
    roots = commandtext.spellings("/home/op/tree/", "/home/op")
    assert roots == ("/home/op/tree", "~/tree", "$HOME/tree", "${HOME}/tree")

    def noted(text: str, given: tuple[str, ...] = roots) -> bool:
        found = breakdown(text, given)
        assert found.reassembled() == text
        return FlagKind.RUNS_SESSION_PATH in {flag.kind for flag in found.flags}

    for text in (
        "/home/op/tree/bin/run",
        "cat /home/op/tree",
        "sh ~/tree/x.sh",
        'cat "$HOME/tree/a"',
        "ls ${HOME}/tree/",
        "cd /tmp && /home/op/tree/bin/run",
    ):
        assert noted(text), text
        assert not noted(text, ()), text
    # The whole of the name: a sibling whose name begins the same is not it.
    for text in ("/home/op/tree2/x", "/home/op/tree-old/x", "/home/op/tre/x", "~/trees/x"):
        assert not noted(text), text
    assert breakdown("/home/op/tree/a /home/op/tree/b", roots).flags[-1].at == (0, 16)

    # A directory that is not under the home has only its own spelling, one
    # that is not absolute has none, and no home is no reason to fail.
    assert commandtext.spellings("/srv/tree", "/home/op") == ("/srv/tree",)
    assert commandtext.spellings("/home/op", "/home/op") == ("/home/op",)
    assert commandtext.spellings("/home/op/tree", None) == ("/home/op/tree",)
    assert commandtext.spellings("(no directory)", "/home/op") == ()
    assert commandtext.spellings("tree", "/home/op") == ()
    # A character of a path that means something to a pattern is itself.
    assert noted("/a+b/c.d/x", commandtext.spellings("/a+b/c.d"))
    assert not noted("/aab/cxd/x", commandtext.spellings("/a+b/c.d"))


def test_sudo_is_not_said_to_be_stopped_by_having_no_terminal() -> None:
    """
    With a password given a moment ago, or a NOPASSWD rule, sudo asks nothing
    and runs: a sentence that only says no prompt can be answered reads as
    "this will fail".
    """
    said = SENTENCES[FlagKind.SUDO]
    assert "runs at once" in said and "NOPASSWD" in said
    assert "no terminal" in said and "waits until the timeout" in said
    assert said.index("runs at once") < said.index("no terminal")


def test_the_scan_is_quote_blind() -> None:
    """
    Deliberately. A construct inside quotes is noted anyway, because the scan
    covers for a splitter whose reading of the quotes may be the thing that is
    wrong.
    """
    assert FlagKind.REDIRECTION in kinds("echo 'a > b'")
    assert FlagKind.COMMAND_SUBSTITUTION in kinds("echo '$(id)'")
    assert FlagKind.SUDO in kinds("echo 'sudo'")


def test_the_heading_and_the_advisory_are_the_agreed_words() -> None:
    assert HEADING == "segments, as bash would sequence them"
    assert "advisory" in ADVISORY and "The block above is exactly what runs" in ADVISORY
    assert ADVISORY.isascii()


def test_the_module_is_pure() -> None:
    """
    No UI and no IO: its imports are the standard library's text modules and
    the splitter.
    """
    tree = ast.parse(Path(commandtext.__file__).read_text())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert imported == {
        "__future__",
        "enum",
        "re",
        "shlex",
        "unicodedata",
        "collections",
        "dataclasses",
        ".shellscan",
    }


# --- what bash does, asked of bash -----------------------------------------


def bash(text: str, *, trace: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(BASH), "-c", ("set -x\n" if trace else "") + text],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )


def traced(text: str) -> list[str]:
    done = bash(text, trace=True)
    return [line.lstrip("+ ") for line in done.stderr.splitlines() if line.startswith("+")]


@needs_bash
@pytest.mark.parametrize("text", ["echo RAN 'a", 'echo RAN "a', "echo RAN; echo 'a"])
def test_bash_refuses_an_unclosed_quote_and_runs_nothing_on_that_line(text: str) -> None:
    assert FlagKind.UNTOKENISABLE in kinds(text)
    done = bash(text)
    assert done.returncode == 2
    assert "unexpected EOF while looking for matching" in done.stderr
    assert done.stdout == ""


@needs_bash
def test_bash_has_already_run_the_lines_above_an_unclosed_quote() -> None:
    text = "echo RAN\necho 'a"
    assert FlagKind.UNTOKENISABLE in kinds(text)
    done = bash(text)
    assert done.returncode == 2
    assert done.stdout == "RAN\n"


@needs_bash
@pytest.mark.parametrize(
    "text",
    [
        "echo ONE # it's\necho TWO",
        "echo $'it\\'s' ; echo TWO",
        "cat <<EOF\nit's\nEOF\necho TWO",
    ],
)
def test_bash_runs_what_shlex_cannot_tokenise_when_the_quote_is_not_one(text: str) -> None:
    """
    The other half of the unbalanced-quotes sentence: in a comment, a
    here-document and `$'...'`, the quote pptmstr trips on is not a quote to
    bash, and the command runs to its last line.
    """
    assert FlagKind.UNTOKENISABLE in kinds(text)
    done = bash(text)
    assert done.returncode == 0
    assert done.stdout.endswith("TWO\n")


@needs_bash
@pytest.mark.parametrize("text", ["-x", "+x", "-", "-n; echo RAN", "-- echo RAN", "--version"])
def test_bash_reads_a_command_string_starting_with_a_dash_or_plus_as_options(text: str) -> None:
    """
    Why a request whose first character is `-` or `+` is refused where it is
    accepted. `bash -c STRING` takes STRING as the command only if it does not
    look like an option; one that does is consumed as options, `-c` is left
    with no argument or the option is rejected, and nothing in STRING runs.
    """
    done = bash(text)
    assert done.returncode == 2
    assert "RAN" not in done.stdout
    assert "option" in done.stderr


@needs_bash
def test_bash_takes_the_argument_after_an_option_looking_string_as_the_command() -> None:
    """
    The hazard and not only the failure: with a second argument present, the
    first is options and the second is what runs. The runner passes one
    string, so this is the shape it must never be handed.
    """
    done = subprocess.run(
        [str(BASH), "-c", "-x", "echo RAN"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (done.returncode, done.stdout, done.stderr) == (0, "RAN\n", "+ echo RAN\n")


@needs_bash
def test_a_leading_space_makes_the_same_string_a_command() -> None:
    done = bash(" -x")
    assert done.returncode == 127
    assert "command not found" in done.stderr


# What the splitter does not see, each with what bash actually ran. The
# segment count is the splitter's; the trace is bash's.
BLIND = [
    # (command, segments the splitter makes, commands bash traced, a flag that says so)
    ("echo $(echo a; echo b)", 2, ["echo a", "echo b", "echo a b"], FlagKind.COMMAND_SUBSTITUTION),
    ("echo `echo a; echo b`", 2, ["echo a", "echo b", "echo a b"], FlagKind.COMMAND_SUBSTITUTION),
    ("true & echo B", 1, ["echo B", "true"], FlagKind.BACKGROUND),
    ("cat <<EOF\na | b ; c\nEOF", 5, ["cat"], FlagKind.HEREDOC),
    ("if true; then echo x; fi", 3, ["true", "echo x"], FlagKind.COMPOUND),
    ("(echo a; echo b)", 2, ["echo a", "echo b"], FlagKind.GROUPING),
    ("f() { echo a; }; f", 3, ["f", "echo a"], FlagKind.GROUPING),
    ("case x in x) echo a;; esac", 3, ["case x in", "echo a"], FlagKind.COMPOUND),
    # One segment to the splitter, two commands to bash: the quote that opens
    # in the first line's comment closes in the second line's.
    ("echo ONE # '\necho HIDDEN #'", 1, ["echo ONE", "echo HIDDEN"], FlagKind.COMMENT),
    # One segment here too, and one command to bash, but not the one any line
    # of it spells: the option is assembled across the line break. `echo`
    # stands in for the `git log` this was reported with, which would write
    # the file.
    ("echo --out\\\nput=x", 1, ["echo --output=x"], FlagKind.CONTINUATION),
    ('echo "--out\\\nput=x"', 1, ["echo --output=x"], FlagKind.CONTINUATION),
]


@needs_bash
@pytest.mark.parametrize(("text", "segments", "ran", "kind"), BLIND)
def test_where_the_segments_are_not_what_bash_runs_a_flag_says_so(
    text: str, segments: int, ran: list[str], kind: FlagKind
) -> None:
    result = assert_exact(text)
    assert len(result.segments) == segments
    assert sorted(traced(text)) == sorted(ran)
    assert kind in {flag.kind for flag in result.flags}


@needs_bash
def test_where_the_segments_are_what_bash_runs_there_is_nothing_to_flag() -> None:
    """
    The positive control for the table above: a plain sequence, one traced
    command per segment bash reaches, and no flag.
    """
    text = "echo a; echo b && echo c || echo d | cat"
    result = assert_exact(text)
    assert [segment.strip() for segment in result.segments][:3] == traced(text)
    assert result.flags == ()
