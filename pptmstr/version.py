"""
``pptmstr --version``: what is running, what pptmstr pins, and where they differ.

A report for a bug report and for whoever replaces an install: pptmstr's
version, the interpreter, and then the three that are one unit -- the
``imgui-bundle`` that is imported, the ``imscribe`` that is imported, and the
``imtypeset`` that is loaded -- each against its pin, with how the library was
found and whether it is accepted beside the wheel. It opens no window and
makes no ImGui context.

**What is pinned and what runs are two facts, and both are printed.** A
version is what the installed distribution says, and only of the copy that is
imported. A commit is claimed only by something that records one: an install
from version control, a checkout the import is file for file, a built pair's
``BUILT-FROM``, the attestation a wheel of imtypeset carries. A wheel
installed by name records none, **and not where it came from either**: an
installer writes the same metadata for one from PyPI and one from a directory
of wheels, so the line says "by name" and does not say "published".
``imscribe.ref`` and ``imtypeset.ref`` are compared only with something built
from source.

**The library is asked first, and its own answer is what is printed**
(``gate.pairing_said``): which library is in the process, by which route, against
which headers. Where it was found is not inferred from the environment: an
installed imtypeset reads neither variable, and a line that went by them would
name a library that is not the one in use.

**Every line is a sentence and none is a traceback.** Each part is asked on its
own and whatever it raises becomes that part's line, so a missing imscribe, a
refused library or a wheel that is not the pinned one each cost one line and
the exit status is 0: the report is for exactly the installs where something
is wrong.

imscribe is located by name and not imported here; the one place pptmstr
imports it is ``pptmstr.ui.typeset``, which is asked for the library.
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
from collections.abc import Callable, Mapping
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


def _origin(distribution: importlib.metadata.Distribution, package: Path) -> tuple[str | None, str]:
    """
    The commit an installed distribution is, when something records it, and
    how it came to be installed. ``package`` is the directory that is imported.

    An install by name records no origin at all: from an index and from a
    directory of wheels (``--find-links``) alike, so neither is claimed. One
    from a file named on the command line records the file and its hash. One from a
    version-control URL records the commit. One from a directory records the
    directory; if that is a checkout with a detached ``HEAD``, and the imported
    package is its working tree file for file, the commit is that ``HEAD``'s.
    The working tree is not compared with the commit here:
    ``scripts/typeset_deps.py verify`` does that.
    """
    written = distribution.read_text("direct_url.json")
    if written is None:
        return None, (
            "installed by name from an index or a directory of wheels, which records "
            "neither that nor a commit"
        )
    record = json.loads(written or "{}")
    commit = str(record.get("vcs_info", {}).get("commit_id", ""))
    if _HASH.fullmatch(commit):
        return commit, f"installed from {record.get('url')}"
    url = urlparse(str(record.get("url", "")))
    if url.scheme != "file":
        return None, f"installed from {record.get('url')}, which records no commit"
    source = Path(unquote(url.path))
    if "archive_info" in record:
        digest = str(record["archive_info"].get("hash", "")).replace("=", " ", 1)
        return None, f"installed from the file {source}" + (f" ({digest})" if digest else "")
    try:
        head = (source / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None, f"installed from {source}, which is not a checkout now"
    if not _HASH.fullmatch(head):
        return None, f"installed from {source}, which is on a branch and not at a commit"
    differing = pairing.package_differences(source / package.name, package)
    if differing:
        return None, (
            f"installed from {source}, now at {head}, and {len(differing)} file(s) differ "
            f"from it, the first {differing[0]}"
        )
    return head, f"file for file the checkout at {source}"


def _installed(name: str, package: Path) -> importlib.metadata.Distribution | None:
    """
    The installed distribution that the imported ``package`` belongs to, or ``None``.

    A path that reaches two site-packages can find the distribution in one and
    the module in the other, and a version read from the wrong one is a claim
    about a copy that is not running.
    """
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    if Path(str(distribution.locate_file(package.name))).resolve() != package:
        return None
    return distribution


def _against(running: str, pin: str) -> str:
    """
    A version beside its pin.
    """
    if running == pin:
        return f"{running}, the version pptmstr pins"
    return f"{running} is running and pptmstr pins {pin}"


def _imscribe() -> str:
    pin = pairing.IMSCRIBE_PIN
    try:
        spec = importlib.util.find_spec("imscribe")
    except Exception as failure:
        spec, missing = None, f"{type(failure).__name__}: {failure}"
    else:
        missing = "no module of that name is on the path"
    if spec is None or spec.origin is None:
        return f"imscribe: not installed ({missing}); pptmstr pins {pin}"
    package = Path(spec.origin).resolve().parent
    distribution = _installed(pairing.IMSCRIBE_DISTRIBUTION, package)
    if distribution is None:
        return (
            f"imscribe at an unrecorded version: imported from {package}, and no installed "
            f"distribution is that copy; pptmstr pins {pin} and nothing here says whether "
            f"this is it"
        )
    commit, how = _origin(distribution, package)
    line = f"imscribe {_against(distribution.version, pin)}: imported from {package}, {how}"
    if commit is None:
        return line
    asked = _asked_for(pairing.IMSCRIBE_REF)
    if commit == asked:
        return f"{line}; commit {commit}, the commit {pairing.IMSCRIBE_REF} names"
    return (
        f"{line}; commit {commit}, NOT the commit {pairing.IMSCRIBE_REF} names, which is "
        f"{asked} (`make deps` installs that one)"
    )


def _route(found: pairing.Found) -> str:
    if found.route == pairing.ROUTE_CHECKOUT:
        asked = f", as {pairing.DEPS_VARIABLE} asks" if found.ahead else ""
        return f"found through this checkout's .deps{asked}"
    return f"found through the environment ({found.why})"


def _built_pair(library: Path, found: pairing.Found, version: str = "") -> str:
    """
    The line for a library the two variables name: what the ``BUILT-FROM`` beside it records.
    """
    asked = _asked_for(pairing.IMTYPESET_REF)
    try:
        record = pairing.built_from(library.parent)
    except (OSError, UnicodeDecodeError):
        record = {}
    commit = record.get("commit", "")
    name = f"imtypeset {version}".strip()
    if not _HASH.fullmatch(commit):
        return (
            f"{name} at an unrecorded commit: {library}, {_route(found)}, with no "
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
    return f"{name} {commit}: {library}, {_route(found)}, {built}; {verdict}"


def _attested(package: Path) -> str:
    """
    Where a wheel of imtypeset says it was built and checked, or an empty string.
    """
    try:
        record = json.loads((package / pairing.ATTESTATION).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    where = record.get("attested_where") if isinstance(record, dict) else None
    return str(where) if where else ""


def _distribution(said: Mapping[str, str | None], found: pairing.Found) -> str:
    """
    The line for an installed imtypeset that is loaded: the library's own answers.
    """
    pin = pairing.IMTYPESET_PIN
    package = Path(said.get("package") or "")
    distribution = _installed(pairing.IMTYPESET_DISTRIBUTION, package.resolve())
    if distribution is None:
        version, how = f"{said.get('distribution') or 'a distribution'}; pptmstr pins {pin}", ""
    else:
        version = _against(distribution.version, pin)
        how = _origin(distribution, package.resolve())[1]
    attested = _attested(package)
    line = (
        f"imtypeset {version}: library {said.get('imtypeset_version') or 'of no stated version'} "
        f"at {said.get('library')}, found as an installed distribution"
        + (f", {how}" if how else "")
        + (f"; attested by {attested}" if attested else "")
        + f"; headers {said.get('headers') or 'not stated'}, accepted by "
        f"{said.get('accepted_by') or 'nothing stated'}, built for "
        f"{said.get('built_for') or 'no stated wheel'}"
    )
    ignored = said.get("ignored_environment")
    if ignored:
        line += (
            f"; {ignored} set and NOT read: an installed imtypeset reads neither, and the "
            f"library named there is not the one in use"
        )
    elif found.route == pairing.ROUTE_ENVIRONMENT:
        line += (
            f"; {found.why} and empty, which names no library and does not stop an installed "
            f"one being found: {pairing.DISABLE_VARIABLE}=1 is how to run without the library"
        )
    elif found.route != pairing.ROUTE_DISTRIBUTION:
        line += f"; pptmstr had decided otherwise ({found.why})"
    return line


def _imtypeset(
    found: pairing.Found, said: Mapping[str, str | None], named: str | None = None
) -> str:
    """
    The library's line. ``said`` is ``gate.pairing_said()``, what is in the
    process, and ``named`` what ``IMTYPESET_LIBRARY`` held for it: read only
    where nothing was loaded, to say which library that was.
    """
    if said.get("package") and said.get("route") == pairing.ROUTE_DISTRIBUTION:
        return _distribution(said, found)
    if said.get("package") and said.get("library"):
        return _built_pair(Path(str(said["library"])), found, said.get("imtypeset_version") or "")
    if named:
        return _built_pair(Path(named), found)
    if found.route == pairing.ROUTE_DISTRIBUTION:
        return (
            f"imtypeset: not loaded ({found.why}, and it did not load: the next line says "
            f"why); pptmstr pins {pairing.IMTYPESET_PIN}"
        )
    why = found.why
    if found.route == pairing.ROUTE_ENVIRONMENT:
        why += f", and {pairing.LIBRARY_VARIABLE} names no library"
    return f"imtypeset: no library ({why}); pptmstr pins {pairing.IMTYPESET_PIN}"


def _asked() -> dict[str, str | None]:
    """
    ``gate.pairing_said()``: the library asked for, and what it says of itself.
    """
    gate = importlib.import_module("pptmstr.ui.typeset.gate")
    return dict(gate.pairing_said())


def library_line() -> str:
    """
    The report's line for the library this process draws through, asked now.

    For a script that measures: its numbers are of one library, and this is
    which. ``pairing.adopt()`` is what decided, so a script calls that first,
    as the entry point does. Never raises.
    """
    return _said(
        "imtypeset",
        lambda: _imtypeset(pairing.adopt(), _asked(), pairing.named(pairing.LIBRARY_VARIABLE)),
    )


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
    The whole report, one line a part. ``found`` is where pptmstr decided the library is.
    """
    try:
        said = _asked()
    except Exception:  # the last line says what asking raised
        said = {}
    return "\n".join(
        [
            _said("pptmstr", _pptmstr),
            _said("python", _python),
            _said("imgui-bundle", _imgui_bundle),
            _said("imscribe", _imscribe),
            _said(
                "imtypeset",
                lambda: _imtypeset(found, said, pairing.named(pairing.LIBRARY_VARIABLE)),
            ),
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
