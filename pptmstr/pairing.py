"""
What pptmstr is built against, and where a process of it finds the text library.

Typeset text is four things that belong together: pptmstr, an advtext commit,
the imtypeset library with its Python package from one commit, and one exact
``imgui-bundle`` wheel. The tree names the last three -- ``advtext.ref``,
``imtypeset.ref``, the pin in ``pyproject.toml`` -- and this module is the rule
for reading them and for holding what was built to them.

**Standard library only, and it imports nothing of pptmstr.**
``scripts/typeset_deps.py`` imports it in a ``.venv`` that holds nothing yet, and
``pptmstr.__main__`` calls it before anything else is imported.

**The library is found through two environment variables, and a source
checkout sets them for itself** (``adopt``). advtext looks for the library only
when ``IMTYPESET_LIBRARY`` names it, and the Makefile exports the pair
``make deps`` built to its recipes. A ``pptmstr`` started any other way got
neither variable and said "not installed" beside a built library. So, once,
before advtext is asked anything:

    either variable is set by the caller   nothing is added, empty or not, as
                                           the Makefile has it: a library named
                                           from outside never meets this
                                           checkout's package, and
                                           ``IMTYPESET_LIBRARY=`` is how to run
                                           without the library.
    neither, in a source checkout          both are set when ``.deps/imtypeset``
                                           is a whole pair, built from the
                                           commit ``imtypeset.ref`` names, for
                                           the environment that is running.
    neither, installed with no checkout    nothing: there is no ``.deps`` to
                                           look in, and a wheel must not go
                                           looking beside itself.

``IMGUI_BUNDLE_PIN`` and the two variable names are spelled here as well as
where they are decided (``pyproject.toml``; ``advtext.typesetter``), because
this module is read before either can be: ``tests/test_version.py`` holds each
to its source.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent

IMGUI_BUNDLE_PIN = "1.92.900"

LIBRARY_VARIABLE = "IMTYPESET_LIBRARY"
PACKAGE_VARIABLE = "IMTYPESET_PYTHON"

ADVTEXT_REF = "advtext.ref"
IMTYPESET_REF = "imtypeset.ref"

# Where `scripts/typeset_deps.py` builds, under a checkout's root, and what a
# pair is made of.
DEPS_DIR = ".deps"
PAIR_DIR = "imtypeset"
ADVTEXT_DIR = "advtext"
LIBRARY_NAME = "libimtypeset_bundle.so"
PACKAGE_DIR = "pysnap"
BUILT_FROM = "BUILT-FROM"

ROUTE_ENVIRONMENT = "environment"
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


def package_differences(pinned: Path, installed: Path) -> list[str]:
    """
    Python files that differ between two ``advtext`` package directories, by relative path.
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


@dataclass(frozen=True, slots=True)
class Found:
    """
    How this process came by the library's two variables, and the sentence for it.
    """

    route: str
    why: str


def decide(
    environ: MutableMapping[str, str], root: Path | None, prefix: Path | None = None
) -> Found:
    """
    Set the pair's two variables in ``environ`` when the rule in the module docstring says to.

    ``root`` is the checkout (``checkout()``), ``prefix`` the running
    environment's. Never raises: whatever goes wrong reading a pair is the
    reason it was not taken.
    """
    named = [name for name in (LIBRARY_VARIABLE, PACKAGE_VARIABLE) if name in environ]
    if named:
        return Found(ROUTE_ENVIRONMENT, f"{' and '.join(named)} set by the caller")
    if root is None:
        return Found(
            ROUTE_NONE,
            f"neither {LIBRARY_VARIABLE} nor {PACKAGE_VARIABLE} is set, and pptmstr is "
            f"installed with no source checkout to look in",
        )
    pair = root / DEPS_DIR / PAIR_DIR
    if not pair.is_dir():
        return Found(ROUTE_NONE, f"there is no {pair}; `make deps` builds the pair")
    try:
        refusal = pair_refusal(pair, read_ref(root / IMTYPESET_REF)) or _another_environment(
            pair, prefix or Path(sys.prefix)
        )
    except Exception as error:  # whatever it is, it is the reason
        refusal = f"{pair} could not be checked ({type(error).__name__}: {error})"
    if refusal:
        return Found(ROUTE_NONE, " ".join(refusal.split()))
    environ[LIBRARY_VARIABLE] = str(pair / LIBRARY_NAME)
    environ[PACKAGE_VARIABLE] = str(pair / PACKAGE_DIR)
    return Found(ROUTE_CHECKOUT, f"the pair in {pair}, built from the commit {IMTYPESET_REF} names")


_FOUND: Found | None = None


def adopt() -> Found:
    """
    Decide once for this process, in its own environment; the same answer afterwards.

    For the entry point, before advtext's typesetter is first asked. Once,
    because a second reading would find the variables the first one set and
    call them the caller's.
    """
    global _FOUND
    if _FOUND is None:
        _FOUND = decide(os.environ, checkout())
    return _FOUND
