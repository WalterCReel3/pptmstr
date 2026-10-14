"""
What pptmstr is built against, and where a process of it finds the text library.

Typeset text is four things that belong together: pptmstr, one ``imscribe``,
one ``imtypeset`` (a shared library with its Python package) and one exact
``imgui-bundle`` wheel. ``pyproject.toml`` pins the last three as published
versions, and that is what an install is. ``imscribe.ref`` and
``imtypeset.ref`` name the commits those versions were built from, for the
development override: ``make deps`` builds both from source into ``.deps``,
for working against a commit nobody has published. This module is the rule for
reading all five and for holding what was built to them.

**Standard library only, and it imports nothing of pptmstr.**
``scripts/typeset_deps.py`` imports it in a ``.venv`` that holds nothing yet, and
``pptmstr.__main__`` calls it before anything else is imported.

**Where the library is found** (``decide``), once, before imscribe is asked
anything. The first line that applies:

    either variable is set by the caller   ``IMTYPESET_LIBRARY``,
                                           ``IMTYPESET_PYTHON``: nothing is
                                           added, empty or not. A library named
                                           from outside never meets this
                                           checkout's package. **An installed
                                           imtypeset reads neither variable**,
                                           so beside one they choose nothing
                                           and ``pptmstr --version`` says they
                                           were not read.
    ``IMSCRIBE_NO_TYPESET`` is set         nothing: this is how to run without
                                           the library, imscribe's own switch
                                           for not looking for an installed
                                           one, and pptmstr does not hand it
                                           ``.deps`` behind that.
    an ``imtypeset`` is installed          nothing is set: imscribe imports it
                                           and it finds the library it
                                           carries. What ``pip install`` of
                                           pptmstr leaves, and the normal
                                           route.
    neither, in a source checkout          both variables are set when
                                           ``.deps/imtypeset`` is a whole pair,
                                           built from the commit
                                           ``imtypeset.ref`` names, for the
                                           environment that is running.
    neither, installed with no checkout    nothing: there is no ``.deps`` to
                                           look in, and a wheel must not go
                                           looking beside itself.

**``PPTMSTR_TYPESET_DEPS`` asks for ``.deps`` beside an installed imtypeset**
(``DEPS_VARIABLE``; anything but empty or ``0``). The third line is then
skipped. Setting the two variables is not enough there: imscribe puts the
directory ``IMTYPESET_PYTHON`` names at the END of the path, the installed
package is found first, and that one reads no variable. So the pair's package
directory is also put FIRST on this process's path (``Found.ahead``), where the
built package is the one imported and reads the variable. Where the pair cannot
be handed over the installed one runs and the reason is said.

**What a checkout set for itself is its own, and is not handed on**
(``release``). A process pptmstr launches inherits pptmstr's environment: an
agent's shell, and through it another checkout's ``make test`` or another
``pptmstr``. Both variables read there as "set by the caller", so that checkout
would run its tests on this one's library without one of its own checks. So
once imscribe has been asked for the library the pair this process set is taken
back out of its environment, and ``named`` still answers what it was. A pair
the caller set is the caller's and is passed on as it came.

The three pins and the variable names are spelled here as well as where they
are decided (``pyproject.toml``; ``imscribe.typesetter``), because this module
is read before either can be: ``tests/test_version.py`` holds each to its
source, and the three pins to what the installed distributions require of each
other.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent

# One compatible unit: the imtypeset library is linked against that one
# imgui-bundle binary (the fourth number of its version is the wheel's line),
# and imscribe requires exactly these two.
IMGUI_BUNDLE_PIN = "1.92.900"
IMSCRIBE_PIN = "0.2.0b1"
IMTYPESET_PIN = "0.4.1.192900"

IMSCRIBE_DISTRIBUTION = "imscribe"
IMTYPESET_DISTRIBUTION = "imtypeset"
# What a wheel of imtypeset carries beside its modules, and what is left to
# know an installed one by where a freezer dropped the package metadata.
ATTESTATION = "_attestation.json"

LIBRARY_VARIABLE = "IMTYPESET_LIBRARY"
PACKAGE_VARIABLE = "IMTYPESET_PYTHON"
# imscribe's: an installed imtypeset is not looked for.
DISABLE_VARIABLE = "IMSCRIBE_NO_TYPESET"
# pptmstr's own: the pair in `.deps` although an imtypeset is installed.
DEPS_VARIABLE = "PPTMSTR_TYPESET_DEPS"

IMSCRIBE_REF = "imscribe.ref"
IMTYPESET_REF = "imtypeset.ref"

# Where `scripts/typeset_deps.py` builds, under a checkout's root, and what a
# pair is made of.
DEPS_DIR = ".deps"
PAIR_DIR = "imtypeset"
IMSCRIBE_DIR = "imscribe"
LIBRARY_NAME = "libimtypeset_bundle.so"
PACKAGE_DIR = "pysnap"
BUILT_FROM = "BUILT-FROM"

ROUTE_ENVIRONMENT = "environment"
ROUTE_DISTRIBUTION = "distribution"
ROUTE_CHECKOUT = "checkout"
ROUTE_NONE = "none"

_HASH = re.compile(r"[0-9a-f]{40}")


class PinError(Exception):
    """
    Something the tree names could not be read or was not met; the message is the sentence.
    """


def read_ref(path: Path) -> str:
    """
    The commit a ref file names: its first line that is neither blank nor a comment.

    A full hash and nothing shorter. An abbreviated hash is not fetchable from a
    remote, and a branch name would make the pin whatever the branch is today.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise PinError(f"{path} cannot be read ({error.strerror}).") from error
    for line in lines:
        ref = line.strip()
        if not ref or ref.startswith("#"):
            continue
        if not _HASH.fullmatch(ref):
            raise PinError(f"{path} names {ref!r}, which is not a full 40-digit commit hash.")
        return ref
    raise PinError(f"{path} names no commit.")


def checkout() -> Path | None:
    """
    The source checkout this package is running from, or ``None`` when it is installed.

    A checkout has the package directly under a root that holds
    ``pyproject.toml`` and the ref files. A wheel carries the ref files inside
    the package and has neither beside it.
    """
    root = PACKAGE.parent
    if (root / "pyproject.toml").is_file() and (root / IMTYPESET_REF).is_file():
        return root
    return None


def ref_path(name: str) -> Path:
    """
    Where a ref file is for this process: a checkout's root, or inside an installed package.
    """
    return (checkout() or PACKAGE) / name


def built_from(pair: Path) -> dict[str, str]:
    """
    What a pair's ``BUILT-FROM`` records, by key; the first line of each key.
    """
    record: dict[str, str] = {}
    for line in (pair / BUILT_FROM).read_text(encoding="utf-8").splitlines():
        key, colon, value = line.partition(": ")
        if colon:
            record.setdefault(key, value.strip())
    return record


def pair_refusal(pair: Path, ref: str) -> str | None:
    """
    Why the pair in ``pair`` is not to be handed to a process; ``None`` when it is.

    Whole, and built from the commit ``ref``, which is what ``imtypeset.ref``
    names now: a pair left from before a bump of the ref is a library nobody
    pinned.
    """
    parts = (Path(LIBRARY_NAME), Path(PACKAGE_DIR) / "imtypeset" / "__init__.py", Path(BUILT_FROM))
    missing = [str(part) for part in parts if not (pair / part).is_file()]
    if missing:
        return f"{pair} is not a whole pair (no {', no '.join(missing)}); `make deps` builds one"
    commits = [
        line.removeprefix("commit: ").strip()
        for line in (pair / BUILT_FROM).read_text(encoding="utf-8").splitlines()
        if line.startswith("commit: ")
    ]
    if commits != [ref]:
        built = commits[0] if len(commits) == 1 else "no one commit"
        return (
            f"{pair} was built from {built} and {IMTYPESET_REF} names {ref}; "
            f"`make deps` builds that one"
        )
    return None


FORK_HEADERS = "fork:"
"""
How ``BUILT-FROM`` begins its ``headers`` line for a library compiled against
the wheel's own Dear ImGui headers. ``scripts/typeset_deps.py`` records what
imtypeset's build says it used and holds that to the kind it asked for by
this prefix; ``tests/test_typeset_deps.py`` holds the two to one spelling.
"""


def headers_note(pair: Path) -> str | None:
    """
    One line saying a pair was not built against the wheel's own Dear ImGui
    headers, with what it was built against; ``None`` when it was.

    Not a refusal. A library compiled against upstream's headers as a stand-in
    for the wheel's fork is accepted by imtypeset on its field probe and is the
    only build there is where the fork cannot be fetched; and a build that
    fails for want of the fork leaves the pair before it in place. So such a
    pair is handed over, and is **said** wherever it is: by the Makefile on
    every run that exports it, and in the first lines of a test run
    (``tests/conftest.py``), because "green with the pair" is otherwise read
    as green against the wheel's headers. Raises what reading the record does.
    """
    headers = built_from(pair).get("headers", "")
    if headers.startswith(FORK_HEADERS):
        return None
    against = headers or f"headers its {BUILT_FROM} does not record"
    return (
        f"the imtypeset pair in {pair} was built against {against}, NOT the wheel's own "
        f"Dear ImGui headers (the fork): a stand-in, accepted on imtypeset's field probe alone"
    )


def package_differences(pinned: Path, installed: Path) -> list[str]:
    """
    Python files that differ between two ``imscribe`` package directories, by relative path.
    """

    def sources(top: Path) -> dict[str, Path]:
        return {str(path.relative_to(top)): path for path in top.rglob("*.py")}

    want, have = sources(pinned), sources(installed)
    return sorted(
        name
        for name in want.keys() | have.keys()
        if name not in want
        or name not in have
        or want[name].read_bytes() != have[name].read_bytes()
    )


def _another_environment(pair: Path, prefix: Path) -> str | None:
    """
    Why a pair is not this environment's, or ``None`` when it is or does not say.

    The library is linked against one environment's ``imgui-bundle`` binary.
    Beside another it is refused, which the operator would read as a fault in
    an install that only has no library of its own. The environment is compared
    and not the interpreter: every name in a venv's ``bin`` resolves to the one
    base interpreter, which another venv shares.
    """
    built_for = built_from(pair).get("for")
    if not built_for:
        return None
    theirs = Path(built_for).parent.parent
    if theirs.resolve() == prefix.resolve():
        return None
    return (
        f"{pair} was built for {built_for} and this is {prefix}; "
        f"`make deps` builds it for the .venv of its checkout"
    )


def asks(environ: Mapping[str, str], name: str) -> bool:
    """
    Whether a switch variable is on: set to anything but an empty string or ``0``.

    imscribe's reading of ``IMSCRIBE_NO_TYPESET``, blanks around the value not
    counted, and the same for pptmstr's own ``PPTMSTR_TYPESET_DEPS``.
    """
    return (environ.get(name) or "").strip() not in ("", "0")


def installed() -> str | None:
    """
    The installed ``imtypeset`` distribution as ``imtypeset <version>``, or ``None``.

    Asked the way imscribe asks, without importing the package, so that the
    two cannot disagree about whether there is one: package metadata first, and
    then the ``_attestation.json`` a wheel carries inside the package. A source
    directory of imtypeset on the path -- the ``pysnap`` of a built pair -- has
    neither and is not one. Never raises: whatever cannot be read is "none".
    """
    name = IMTYPESET_DISTRIBUTION
    try:
        from importlib import metadata

        return f"{name} {metadata.distribution(name).version or ''}".strip()
    except Exception:  # not installed, or not readable: asked the second way
        pass
    try:
        import json
        from importlib import util

        spec = util.find_spec(name)
        origin = getattr(spec, "origin", None)
        if not origin:
            return None
        record = Path(origin).parent / ATTESTATION
        if not record.is_file():
            return None
    except Exception:
        return None
    try:
        said = json.loads(record.read_text(encoding="utf-8")).get("distribution_version")
    except Exception:  # unreadable: the package's own gate will say so
        said = None
    return f"{name} {said}" if isinstance(said, str) and said else name


@dataclass(frozen=True, slots=True)
class Found:
    """
    How this process comes by the library, and the sentence for it.
    """

    route: str
    why: str
    # The variables ``decide`` set, with their values; empty on any other route.
    given: tuple[tuple[str, str], ...] = ()
    # The directory to put first on the path so that the pair's package is the
    # one imported beside an installed imtypeset; ``None`` wherever it is not
    # needed. ``adopt`` does it: the path is the process's and not ``environ``'s.
    ahead: str | None = None


def decide(
    environ: MutableMapping[str, str],
    root: Path | None,
    prefix: Path | None = None,
    distribution: str | None = None,
) -> Found:
    """
    Decide where the library is found, by the rule in the module docstring, and
    set the pair's two variables in ``environ`` where the rule says to.

    ``root`` is the checkout (``checkout()``), ``prefix`` the running
    environment's, ``distribution`` what ``installed()`` answered. Never
    raises: whatever goes wrong reading a pair is the reason it was not taken.
    """
    named = [name for name in (LIBRARY_VARIABLE, PACKAGE_VARIABLE) if name in environ]
    if named:
        return Found(ROUTE_ENVIRONMENT, f"{' and '.join(named)} set by the caller")
    if asks(environ, DISABLE_VARIABLE):
        return Found(
            ROUTE_NONE,
            f"{DISABLE_VARIABLE} is set, which is how to run without the library: none is "
            f"looked for",
        )
    wanted = asks(environ, DEPS_VARIABLE)
    if distribution and not wanted:
        return Found(ROUTE_DISTRIBUTION, f"{distribution} is installed")
    pair, refusal = _pair(root, prefix)
    if pair is None or refusal:
        if distribution:
            return Found(
                ROUTE_DISTRIBUTION,
                f"{distribution} is installed, and it is what runs although {DEPS_VARIABLE} "
                f"asks for this checkout's pair: {refusal}",
            )
        return Found(
            ROUTE_NONE,
            f"no {IMTYPESET_DISTRIBUTION} is installed, which installing pptmstr's "
            f"requirements brings, and {refusal}",
        )
    given = (
        (LIBRARY_VARIABLE, str(pair / LIBRARY_NAME)),
        (PACKAGE_VARIABLE, str(pair / PACKAGE_DIR)),
    )
    environ.update(given)
    why = f"the pair in {pair}, built from the commit {IMTYPESET_REF} names"
    if not distribution:
        return Found(ROUTE_CHECKOUT, why, given)
    return Found(
        ROUTE_CHECKOUT,
        f"{why}, asked for by {DEPS_VARIABLE} in place of the installed {distribution}",
        given,
        ahead=str(pair / PACKAGE_DIR),
    )


def _pair(root: Path | None, prefix: Path | None) -> tuple[Path | None, str]:
    """
    A checkout's pair and why it is not to be handed over; the reason is empty when it is.
    """
    if root is None:
        return None, "pptmstr is installed with no source checkout to look in for a built pair"
    pair = root / DEPS_DIR / PAIR_DIR
    try:
        if not pair.is_dir():
            return pair, f"there is no {pair}; `make deps` builds a pair from source"
        refusal = pair_refusal(pair, read_ref(root / IMTYPESET_REF)) or _another_environment(
            pair, prefix or Path(sys.prefix)
        )
    except Exception as error:  # whatever it is, it is the reason
        refusal = f"{pair} could not be checked ({type(error).__name__}: {error})"
    return pair, " ".join((refusal or "").split())


_FOUND: Found | None = None


def adopt() -> Found:
    """
    Decide once for this process, in its own environment; the same answer afterwards.

    For the entry point, before imscribe's typesetter is first asked. Once,
    because a second reading would find the variables the first one set and
    call them the caller's.
    """
    global _FOUND
    if _FOUND is None:
        _FOUND = decide(os.environ, checkout(), distribution=installed())
        if _FOUND.ahead and _FOUND.ahead not in sys.path:
            sys.path.insert(0, _FOUND.ahead)
    return _FOUND


def release(environ: MutableMapping[str, str] | None = None) -> None:
    """
    Take back out of the environment the variables ``adopt`` set there.

    For the entry point, once imscribe has been asked for the library: imscribe
    remembers the pair a load by the two variables was accepted with, and is
    made with it again when it is asked again after a ``typesetter.reset()``,
    so from then on the variables are only what a child process would inherit
    (the module docstring says why it must not). A variable the caller set, and
    one that no longer holds what ``adopt`` set, is left. Does nothing before
    ``adopt``.
    """
    target = os.environ if environ is None else environ
    for name, value in _FOUND.given if _FOUND is not None else ():
        if target.get(name) == value:
            del target[name]


def named(name: str) -> str | None:
    """
    What one of the two variables holds for this process, or ``None``: the
    environment's value, or the one ``adopt`` set and ``release`` took back out.
    """
    if name in os.environ:
        return os.environ[name]
    return dict(_FOUND.given).get(name) if _FOUND is not None else None
