"""
``pptmstr --version``: what is running, what the tree asks for, and where they differ.

A report for a bug report and for whoever replaces an install: pptmstr's
version, the interpreter, the ``imgui-bundle`` that is imported against the
pin, the advtext and the imtypeset that are in this process against the two ref
files, how the library was found, and whether it is accepted beside the wheel.
It opens no window and makes no ImGui context.

**What the tree asks for and what is installed are two facts, and both are
printed.** ``advtext.ref`` and ``imtypeset.ref`` say what was asked. What runs
is the ``advtext`` the interpreter imports, and the library the environment
names with the ``BUILT-FROM`` beside it. A commit is claimed for what runs only
by something that records one: for advtext, the checkout its install names,
compared with the imported package file for file; for the library,
``BUILT-FROM``. Where nothing records it the line says so.

**Every line is a sentence and none is a traceback.** Each part is asked on its
own and whatever it raises becomes that part's line, so a missing advtext, a
refused library or a wheel that is not the pinned one each cost one line and
the exit status is 0: the report is for exactly the installs where something
is wrong.

advtext is located by name and not imported here; the one place pptmstr
imports it is ``pptmstr.ui.typeset``, which is asked for the library's kind.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__, pairing

_HASH = re.compile(r"[0-9a-f]{40}")


def _said(part: str, line: Callable[[], str]) -> str:
    """
    One part's line, or the sentence for whatever asking it raised.
    """
    try:
        return line()
    except Exception as failure:
        return f"{part}: could not be read ({type(failure).__name__}: {failure})"


def _asked_for(name: str) -> str:
    """
    The commit a ref file names, or a phrase saying it names none that can be read.
    """
    try:
        return pairing.read_ref(pairing.ref_path(name))
    except pairing.PinError as failure:
        return f"no commit ({failure})"


def _pptmstr() -> str:
    root = pairing.checkout()
    where = f"a source checkout at {root}" if root else f"installed at {pairing.PACKAGE}"
    return f"pptmstr {__version__} ({where})"


def _python() -> str:
    return f"python {platform.python_version()} ({sys.executable})"


def _imgui_bundle() -> str:
    pin = pairing.IMGUI_BUNDLE_PIN
    try:
        running = str(importlib.import_module("imgui_bundle").__version__)
    except Exception as failure:
        return (
            f"imgui-bundle: does not import ({type(failure).__name__}: {failure}); "
            f"pptmstr pins {pin}"
        )
    if running == pin:
        return f"imgui-bundle {running}, the version pptmstr pins"
    return (
        f"imgui-bundle {running} is running and pptmstr pins {pin}: the typesetting library "
        f"is built for one wheel and is refused beside another"
    )


def _installed_from(package: Path) -> tuple[str | None, str]:
    """
    The commit the imported advtext is, when something records it, and how that is known.

    A distribution installed from a version-control URL records the commit. One
    installed from a directory records the directory; if that is a checkout
    with a detached ``HEAD``, and the imported package is its working tree file
    for file, the commit is that ``HEAD``'s. The working tree is not compared
    with the commit here: ``scripts/typeset_deps.py verify`` does that.
    """
    try:
        distribution = importlib.metadata.distribution("advtext")
    except importlib.metadata.PackageNotFoundError:
        return None, "no distribution of that name is installed"
    # A path that reaches two site-packages can find the distribution in one
    # and the module in the other.
    if Path(str(distribution.locate_file("advtext"))).resolve() != package:
        return None, "the installed distribution of that name is another copy"
    record = json.loads(distribution.read_text("direct_url.json") or "{}")
    commit = str(record.get("vcs_info", {}).get("commit_id", ""))
    if _HASH.fullmatch(commit):
        return commit, f"installed from {record.get('url')}"
    url = urlparse(str(record.get("url", "")))
    if url.scheme != "file":
        return None, f"distribution {distribution.version} records no commit"
    clone = Path(unquote(url.path))
    try:
        head = (clone / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None, f"installed from {clone}, which is not a checkout now"
    if not _HASH.fullmatch(head):
        return None, f"installed from {clone}, which is on a branch and not at a commit"
    differing = pairing.package_differences(clone / "advtext", package)
    if differing:
        return None, (
            f"installed from {clone}, now at {head}, and {len(differing)} file(s) differ "
            f"from it, the first {differing[0]}"
        )
    return head, f"file for file the checkout at {clone}"


def _advtext() -> str:
    asked = _asked_for(pairing.ADVTEXT_REF)
    try:
        spec = importlib.util.find_spec("advtext")
    except Exception as failure:
        spec, missing = None, f"{type(failure).__name__}: {failure}"
    else:
        missing = "no module of that name is on the path"
    if spec is None or spec.origin is None:
        return f"advtext: not installed ({missing}); {pairing.ADVTEXT_REF} names {asked}"
    package = Path(spec.origin).resolve().parent
    commit, how = _installed_from(package)
    if commit is None:
        return (
            f"advtext at an unrecorded commit: imported from {package} ({how}); "
            f"{pairing.ADVTEXT_REF} names {asked} and nothing here says whether this is it"
        )
    if commit == asked:
        return (
            f"advtext {commit}: imported from {package}, {how}; "
            f"the commit {pairing.ADVTEXT_REF} names"
        )
    return (
        f"advtext {commit}: imported from {package}, {how}; NOT the commit "
        f"{pairing.ADVTEXT_REF} names, which is {asked} (`make deps` installs that one)"
    )


def _route(found: pairing.Found) -> str:
    if found.route == pairing.ROUTE_CHECKOUT:
        return "found through this checkout's .deps"
    return f"found through the environment ({found.why})"


def _imtypeset(found: pairing.Found) -> str:
    asked = _asked_for(pairing.IMTYPESET_REF)
    named = pairing.named(pairing.LIBRARY_VARIABLE)
    if not named:
        why = found.why
        if found.route == pairing.ROUTE_ENVIRONMENT:
            why += f", and {pairing.LIBRARY_VARIABLE} names no library"
        return f"imtypeset: no library ({why}); {pairing.IMTYPESET_REF} names {asked}"
    library = Path(named)
    try:
        record = pairing.built_from(library.parent)
    except (OSError, UnicodeDecodeError):
        record = {}
    commit = record.get("commit", "")
    if not _HASH.fullmatch(commit):
        return (
            f"imtypeset at an unrecorded commit: {library}, {_route(found)}, with no "
            f"{pairing.BUILT_FROM} beside it that names one; {pairing.IMTYPESET_REF} names "
            f"{asked} and nothing here says whether this is it"
        )
    built = f"built for {record.get('wheel', 'an unrecorded wheel')}"
    if record.get("headers"):
        built += f" with headers {record['headers']}"
    verdict = (
        f"the commit {pairing.IMTYPESET_REF} names"
        if commit == asked
        else f"NOT the commit {pairing.IMTYPESET_REF} names, which is {asked} "
        f"(`make deps` builds that one)"
    )
    return f"imtypeset {commit}: {library}, {_route(found)}, {built}; {verdict}"


def _typeset() -> str:
    gate = importlib.import_module("pptmstr.ui.typeset.gate")
    kind, sentence = gate.library()
    if kind == gate.READY:
        return (
            f"typeset text: the library is accepted beside this wheel ({kind}); "
            f"its fonts are loaded when a window opens"
        )
    return f"typeset text: not in use ({kind}): {sentence}"


def report(found: pairing.Found) -> str:
    """
    The whole report, one line a part. ``found`` is how the library's variables were come by.
    """
    return "\n".join(
        [
            _said("pptmstr", _pptmstr),
            _said("python", _python),
            _said("imgui-bundle", _imgui_bundle),
            _said("advtext", _advtext),
            _said("imtypeset", lambda: _imtypeset(found)),
            _said("typeset text", _typeset),
        ]
    )


def _write(text: str) -> None:
    """
    Put the report on standard output, whatever standard output is.

    A character the output's encoding lacks is written as its escape: a path
    under a locale that is not UTF-8 costs that character and not the report.
    An output nobody reads, or none at all, costs the report and nothing else.
    """
    out = sys.stdout
    if out is None:
        return
    try:
        encoding = out.encoding or "utf-8"
        out.write(text.encode(encoding, "backslashreplace").decode(encoding))
        out.flush()
    except BrokenPipeError:
        # The interpreter flushes standard output again as it exits, and says
        # so on standard error when that fails; there is nobody to flush to.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), out.fileno())
        except (OSError, ValueError):
            pass
    except (OSError, ValueError, LookupError):
        pass


def main() -> int:
    """
    Print the report. Always 0: a report of what is wrong is not itself a failure.
    """
    _write(report(pairing.adopt()) + "\n")
    return 0
