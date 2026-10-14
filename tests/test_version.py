"""
``pptmstr --version``, the one source of pptmstr's version, and where a process
finds the text library (``pptmstr.pairing``, ``pptmstr.version``,
``pptmstr.__main__``).

The report is for installs where something is wrong, so most of what is held
here is that each wrong thing is a sentence and the exit status is 0: no
library, a library that is refused, an ``imgui-bundle`` that is not the pinned
one, no imscribe at all. Runs of the whole report are in an interpreter of their
own, because the report's first act is to settle the library's two variables in
its own environment, which must not become this process's.

**Three configurations, and a test says which it needs.** An imtypeset is
installed (what installing pptmstr's requirements leaves, and how the library
is normally found); the checkout has a built pair (the development override,
asked for with ``PPTMSTR_TYPESET_DEPS`` where one is also installed); and
neither. The rule itself (``pairing.decide``) is held for all three with
stand-in directories; a run of the report is of whichever this environment is,
and the tests of the others skip and say so.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from imscribe import typesetter

from pptmstr import __main__ as entry
from pptmstr import app, pairing, version
from pptmstr.ui.typeset import gate
from tests.conftest import child_environment, described, refused_library, without_the_library

ROOT = Path(__file__).resolve().parent.parent
REF = "a" * 40
OTHER = "b" * 40
VARIABLES = (pairing.LIBRARY_VARIABLE, pairing.PACKAGE_VARIABLE)


def _pyproject() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _run(*args: str, script: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
    """
    ``python -m pptmstr ARGS``, or ``script``, with what chooses the library as given and no more.
    """
    command = ["-c", script] if script is not None else ["-m", "pptmstr", *args]
    return subprocess.run(
        [sys.executable, *command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=child_environment(**env),
    )


OFF = {pairing.DISABLE_VARIABLE: "1"}
ASKED = {pairing.DEPS_VARIABLE: "1"}

needs_a_distribution = pytest.mark.skipif(
    pairing.installed() is None, reason="no imtypeset distribution is installed here"
)
not_the_run_without = pytest.mark.skipif(
    pairing.asks(os.environ, pairing.DISABLE_VARIABLE),
    reason="this run is the one without the library",
)


def _lines(done: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """
    The report by the word each line starts with, having held it to an exit of 0 and no traceback.
    """
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr and "Traceback" not in done.stdout
    lines = done.stdout.splitlines()
    assert len(lines) == 6, done.stdout
    return {re.split(r"[ :]", line, maxsplit=1)[0]: line for line in lines}


# -- one source for each thing that is named twice ---------------------------------


def test_the_version_is_written_once_and_the_build_reads_it_from_the_package() -> None:
    project = _pyproject()
    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "pptmstr/__init__.py"
    import pptmstr

    assert re.fullmatch(r"\d+\.\d+\.\d+", pptmstr.__version__)
    assert version._pptmstr().startswith(f"pptmstr {pptmstr.__version__} ")


def _pinned(name: str) -> str:
    """
    The requirement of ``name`` in ``pyproject.toml``, which must be there once.
    """
    (spec,) = [
        d for d in _pyproject()["project"]["dependencies"] if re.split(r"[=<>;\[ ]", d)[0] == name
    ]
    return str(spec)


def test_the_pins_the_report_names_are_the_ones_the_build_declares() -> None:
    assert _pinned("imgui-bundle") == f"imgui-bundle=={pairing.IMGUI_BUNDLE_PIN}"
    assert _pinned("imscribe") == f"imscribe=={pairing.IMSCRIBE_PIN}"
    assert _pinned("imtypeset").split(";")[0].strip() == f"imtypeset=={pairing.IMTYPESET_PIN}"


def _requires(distribution: str, name: str) -> str:
    """
    What an installed distribution requires of ``name``, as its metadata writes it.
    """
    (spec,) = [
        r
        for r in importlib.metadata.requires(distribution) or ()
        if re.split(r"[=<>;\[ ]", r)[0] == name and "extra ==" not in r
    ]
    return str(spec)


def test_the_three_pins_are_one_unit_by_what_the_installed_imscribe_requires() -> None:
    """
    The three move together or an install cannot resolve. Held to the imscribe
    that is installed, whose own metadata names the other two: the same
    imgui-bundle, and the same imtypeset under the same marker, character for
    character -- so pptmstr asks for the library exactly where imscribe does,
    and on a platform outside the marker installs without it as imscribe does.
    """
    assert importlib.metadata.version("imscribe") == pairing.IMSCRIBE_PIN
    assert _requires("imscribe", "imgui-bundle") == _pinned("imgui-bundle")
    assert _requires("imscribe", "imtypeset") == _pinned("imtypeset")
    assert ";" in _pinned("imtypeset"), "under a marker"
    # The fourth number of imtypeset's version is the wheel's line.
    assert pairing.IMTYPESET_PIN.rsplit(".", 1)[1] == pairing.IMGUI_BUNDLE_PIN.replace(".", "")


@needs_a_distribution
def test_the_installed_imtypeset_is_the_pinned_one_and_requires_the_pinned_wheel() -> None:
    assert importlib.metadata.version("imtypeset") == pairing.IMTYPESET_PIN
    assert _requires("imtypeset", "imgui-bundle") == _pinned("imgui-bundle")


def test_whether_an_imtypeset_is_installed_is_asked_as_imscribe_asks_it() -> None:
    """
    pptmstr decides before imscribe is imported, by its own asking. If the two
    disagreed, pptmstr would set the pair's variables beside an installed
    imtypeset that reads neither, or leave them unset beside none.
    """
    assert (pairing.installed() or "") == typesetter._distribution()
    assert pairing.DISABLE_VARIABLE == typesetter.DISABLE_VARIABLE
    assert pairing.ROUTE_DISTRIBUTION == typesetter.ROUTE_DISTRIBUTION
    for value, on in (("1", True), ("yes", True), ("0", False), ("", False), (" 0 ", False)):
        assert pairing.asks({pairing.DISABLE_VARIABLE: value}, pairing.DISABLE_VARIABLE) is on
    assert not pairing.asks({}, pairing.DISABLE_VARIABLE)


def test_an_attestation_beside_the_package_is_an_installed_imtypeset_without_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A frozen application: the dist-info dropped, the wheel's own record kept.
    package = tmp_path / "imtypeset"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")

    def none(name: str) -> Any:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", none)
    monkeypatch.setattr(sys, "path", [str(tmp_path), *sys.path])
    monkeypatch.delitem(sys.modules, "imtypeset", raising=False)
    importlib.invalidate_caches()
    assert pairing.installed() is None, "a source directory on the path is not one"
    (package / pairing.ATTESTATION).write_text(
        json.dumps({"distribution_version": "9.9.9.192900"}), encoding="utf-8"
    )
    assert pairing.installed() == "imtypeset 9.9.9.192900"
    (package / pairing.ATTESTATION).write_text("not json", encoding="utf-8")
    assert pairing.installed() == "imtypeset"


def test_the_variables_pptmstr_sets_are_the_ones_imscribe_reads() -> None:
    assert VARIABLES == (typesetter.LIBRARY_VARIABLE, typesetter.PACKAGE_VARIABLE)


def test_the_ref_files_go_into_the_wheel_inside_the_package_and_into_the_sdist() -> None:
    hatch = _pyproject()["tool"]["hatch"]["build"]["targets"]
    assert hatch["wheel"]["force-include"] == {
        pairing.IMSCRIBE_REF: f"pptmstr/{pairing.IMSCRIBE_REF}",
        pairing.IMTYPESET_REF: f"pptmstr/{pairing.IMTYPESET_REF}",
    }
    assert {f"/{pairing.IMSCRIBE_REF}", f"/{pairing.IMTYPESET_REF}"} <= set(
        hatch["sdist"]["include"]
    )


def test_the_rule_imports_where_nothing_but_the_standard_library_does() -> None:
    # `scripts/typeset_deps.py` imports it in a .venv that holds nothing yet.
    # -S: no site-packages, so anything third-party would fail the import.
    done = subprocess.run(
        [sys.executable, "-S", "-c", "import pptmstr.pairing; print(pptmstr.pairing.__file__)"],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(ROOT)},
    )
    assert done.stdout.strip() == str(ROOT / "pptmstr" / "pairing.py"), done.stderr


# -- where a process finds the library ----------------------------------------------


def _checkout(top: Path, *, commit: str = REF, prefix: Path | None = None) -> Path:
    """
    A checkout's root with a pair in its ``.deps`` as ``make deps`` leaves one.
    """
    pair = top / pairing.DEPS_DIR / pairing.PAIR_DIR
    (pair / pairing.PACKAGE_DIR / "imtypeset").mkdir(parents=True)
    (pair / pairing.PACKAGE_DIR / "imtypeset" / "__init__.py").write_text("", encoding="utf-8")
    (pair / pairing.LIBRARY_NAME).write_bytes(b"")
    built_for = f"for: {prefix / 'bin' / 'python'}\n" if prefix else ""
    (pair / pairing.BUILT_FROM).write_text(
        f"imtypeset: somewhere\ncommit: {commit}\n{built_for}", encoding="utf-8"
    )
    (top / pairing.IMTYPESET_REF).write_text(f"# a comment\n{REF}\n", encoding="utf-8")
    (top / "pyproject.toml").write_text("", encoding="utf-8")
    return pair


def test_a_checkout_with_a_whole_pair_at_the_ref_sets_both_variables(tmp_path: Path) -> None:
    pair = _checkout(tmp_path, prefix=tmp_path / "venv")
    environ: dict[str, str] = {"PATH": "/bin"}

    found = pairing.decide(environ, tmp_path, tmp_path / "venv")

    assert found.route == pairing.ROUTE_CHECKOUT
    assert environ == {
        "PATH": "/bin",
        pairing.LIBRARY_VARIABLE: str(pair / pairing.LIBRARY_NAME),
        pairing.PACKAGE_VARIABLE: str(pair / pairing.PACKAGE_DIR),
    }


@pytest.mark.parametrize("mine", VARIABLES)
@pytest.mark.parametrize("value", ["/another/project", ""])
def test_a_variable_the_caller_set_is_never_paired_with_this_checkouts(
    tmp_path: Path, mine: str, value: str
) -> None:
    # Empty too, and whatever else is so: beside an installed imtypeset and
    # with the override asked for. Switched off, a variable is taken out
    # (``test_switched_off_the_two_variables_are_taken_out_and_said``).
    _checkout(tmp_path)
    for more in ({}, ASKED):
        for distribution in (None, "imtypeset 0.4.1.192900"):
            environ = {mine: value, **more}

            found = pairing.decide(environ, tmp_path, tmp_path / "venv", distribution)

            assert found.route == pairing.ROUTE_ENVIRONMENT and mine in found.why
            assert environ == {mine: value, **more} and found.ahead is None


def test_an_installed_imtypeset_is_what_runs_and_the_checkouts_pair_is_left_alone(
    tmp_path: Path,
) -> None:
    """
    The normal route. imscribe imports an installed imtypeset by itself and
    that package reads neither variable, so pptmstr sets neither: beside it
    they would choose nothing and be handed on to every process it starts.
    """
    _checkout(tmp_path, prefix=tmp_path / "venv")
    environ: dict[str, str] = {"PATH": "/bin"}

    found = pairing.decide(environ, tmp_path, tmp_path / "venv", "imtypeset 0.4.1.192900")

    assert found.route == pairing.ROUTE_DISTRIBUTION
    assert found.why == "imtypeset 0.4.1.192900 is installed"
    assert environ == {"PATH": "/bin"} and found.given == () and found.ahead is None


def test_the_checkouts_pair_is_taken_beside_an_installed_imtypeset_only_when_asked_for(
    tmp_path: Path,
) -> None:
    """
    The development override: a library built from a commit nobody has
    published. Both variables are set, and the pair's package directory is
    handed back to be put first on the path, because the installed package is
    otherwise the one imported and it reads no variable.
    """
    pair = _checkout(tmp_path, prefix=tmp_path / "venv")
    environ = dict(ASKED)

    found = pairing.decide(environ, tmp_path, tmp_path / "venv", "imtypeset 0.4.1.192900")

    assert found.route == pairing.ROUTE_CHECKOUT and pairing.DEPS_VARIABLE in found.why
    assert environ[pairing.LIBRARY_VARIABLE] == str(pair / pairing.LIBRARY_NAME)
    assert environ[pairing.PACKAGE_VARIABLE] == found.ahead == str(pair / pairing.PACKAGE_DIR)
    # With none installed the pair's package is found where imscribe puts it.
    alone = pairing.decide(dict(ASKED), tmp_path, tmp_path / "venv")
    assert alone.route == pairing.ROUTE_CHECKOUT and alone.ahead is None
    for off in ("0", ""):
        environ = {pairing.DEPS_VARIABLE: off}
        found = pairing.decide(environ, tmp_path, tmp_path / "venv", "imtypeset 0.4.1.192900")
        assert found.route == pairing.ROUTE_DISTRIBUTION and environ == {pairing.DEPS_VARIABLE: off}


def test_a_pair_asked_for_that_cannot_be_handed_over_leaves_the_installed_one_and_says_so(
    tmp_path: Path,
) -> None:
    _checkout(tmp_path, commit=OTHER)
    environ = dict(ASKED)

    found = pairing.decide(environ, tmp_path, tmp_path / "venv", "imtypeset 0.4.1.192900")

    assert found.route == pairing.ROUTE_DISTRIBUTION and environ == ASKED
    assert "imtypeset 0.4.1.192900 is installed, and it is what runs" in found.why
    assert pairing.DEPS_VARIABLE in found.why and OTHER in found.why


@pytest.mark.parametrize("distribution", [None, "imtypeset 0.4.1.192900"])
@pytest.mark.parametrize("asked", [{}, ASKED])
def test_switched_off_nothing_is_set_whatever_is_built_or_installed(
    tmp_path: Path, distribution: str | None, asked: dict[str, str]
) -> None:
    """
    ``IMSCRIBE_NO_TYPESET`` is how to run without the library, and it is
    imscribe's: it stops imscribe looking for an installed imtypeset and does
    not stop a library that is named. So pptmstr must not name one behind it,
    or a checkout with a built pair would draw through it in the run that was
    asked to have none.
    """
    _checkout(tmp_path, prefix=tmp_path / "venv")
    environ = {**OFF, **asked}

    found = pairing.decide(environ, tmp_path, tmp_path / "venv", distribution)

    assert found.route == pairing.ROUTE_NONE and pairing.DISABLE_VARIABLE in found.why
    assert environ == {**OFF, **asked} and found.given == ()


def test_an_installed_pptmstr_with_no_checkout_looks_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A wheel: the package in a site-packages, the ref files inside it, and a
    # `.deps` beside it that is somebody else's and must not be read.
    site = tmp_path / "site-packages"
    _checkout(site)
    (site / "pyproject.toml").unlink()
    (site / "pptmstr").mkdir()
    (site / pairing.IMTYPESET_REF).rename(site / "pptmstr" / pairing.IMTYPESET_REF)
    monkeypatch.setattr(pairing, "PACKAGE", site / "pptmstr")
    assert pairing.checkout() is None
    assert pairing.ref_path(pairing.IMTYPESET_REF) == site / "pptmstr" / pairing.IMTYPESET_REF

    environ: dict[str, str] = {}
    found = pairing.decide(environ, pairing.checkout())

    assert found.route == pairing.ROUTE_NONE and "no source checkout" in found.why
    assert "no imtypeset is installed" in found.why
    assert environ == {}


def test_this_tree_is_a_checkout_and_its_ref_files_are_at_its_root() -> None:
    assert pairing.checkout() == ROOT
    assert pairing.ref_path(pairing.IMSCRIBE_REF) == ROOT / pairing.IMSCRIBE_REF


def _spoil_nothing(pair: Path) -> None:
    pass


def _spoil_the_library(pair: Path) -> None:
    (pair / pairing.LIBRARY_NAME).unlink()


def _spoil_the_package(pair: Path) -> None:
    (pair / pairing.PACKAGE_DIR / "imtypeset" / "__init__.py").unlink()


def _spoil_the_record(pair: Path) -> None:
    (pair / pairing.BUILT_FROM).write_bytes(b"\xff\n")


def _spoil_the_ref(pair: Path) -> None:
    (pair.parent.parent / pairing.IMTYPESET_REF).write_text("main\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("commit", "spoil", "says"),
    [
        (OTHER, _spoil_nothing, OTHER),
        (REF, _spoil_the_library, pairing.LIBRARY_NAME),
        (REF, _spoil_the_package, "not a whole pair"),
        (REF, _spoil_the_record, "UnicodeDecodeError"),
        (REF, _spoil_the_ref, "not a full 40-digit commit hash"),
    ],
)
def test_a_pair_that_is_not_the_pinned_one_whole_is_left_alone_and_the_reason_said(
    tmp_path: Path, commit: str, spoil: Any, says: str
) -> None:
    pair = _checkout(tmp_path, commit=commit)
    spoil(pair)
    environ: dict[str, str] = {}

    found = pairing.decide(environ, tmp_path, tmp_path / "venv")

    assert found.route == pairing.ROUTE_NONE and says in found.why
    assert "\n" not in found.why
    assert environ == {}


def test_a_checkout_with_nothing_built_says_what_builds_it(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    found = pairing.decide(environ, tmp_path, tmp_path / "venv")
    assert found.route == pairing.ROUTE_NONE and "make deps" in found.why
    assert environ == {}


def test_a_pair_built_for_another_environment_is_not_this_ones(tmp_path: Path) -> None:
    # Linked against that environment's wheel binary: taken, it would be
    # "refused", a fault, where the truth is that this one has no library.
    _checkout(tmp_path, prefix=tmp_path / "theirs")
    environ: dict[str, str] = {}

    found = pairing.decide(environ, tmp_path, tmp_path / "mine")

    assert found.route == pairing.ROUTE_NONE
    assert str(tmp_path / "theirs") in found.why and str(tmp_path / "mine") in found.why
    assert environ == {}


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches any directory")
def test_a_deps_directory_that_cannot_be_searched_is_a_reason_and_not_a_traceback(
    tmp_path: Path,
) -> None:
    # Every start of pptmstr decides this first, --version included.
    _checkout(tmp_path)
    deps = tmp_path / pairing.DEPS_DIR
    deps.chmod(0o000)
    try:
        environ: dict[str, str] = {}
        found = pairing.decide(environ, tmp_path)
    finally:
        deps.chmod(0o755)
    assert found.route == pairing.ROUTE_NONE and environ == {}
    assert "could not be checked (PermissionError" in found.why


def test_the_process_decides_once_and_does_not_mistake_its_own_answer_for_the_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = _checkout(tmp_path, prefix=Path(sys.prefix))
    for name in (*VARIABLES, pairing.DISABLE_VARIABLE, pairing.DEPS_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pairing, "_FOUND", None)
    monkeypatch.setattr(pairing, "checkout", lambda: tmp_path)
    monkeypatch.setattr(pairing, "installed", lambda: None)
    try:
        first = pairing.adopt()
        assert first.route == pairing.ROUTE_CHECKOUT
        assert os.environ[pairing.LIBRARY_VARIABLE] == str(pair / pairing.LIBRARY_NAME)
        assert pairing.adopt() is first
    finally:
        # monkeypatch restores what it deleted and knows nothing of what `adopt` set.
        for name in VARIABLES:
            os.environ.pop(name, None)


def test_the_process_puts_the_pair_it_was_asked_for_ahead_of_an_installed_imtypeset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``decide`` only says which directory; the path is the process's. Without
    this the override sets two variables an installed imtypeset never reads,
    and the report would name a pair that is not the library in use.
    """
    pair = _checkout(tmp_path, prefix=Path(sys.prefix))
    for name in (*VARIABLES, pairing.DISABLE_VARIABLE):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(pairing.DEPS_VARIABLE, "1")
    monkeypatch.setattr(pairing, "_FOUND", None)
    monkeypatch.setattr(pairing, "checkout", lambda: tmp_path)
    monkeypatch.setattr(pairing, "installed", lambda: "imtypeset 0.4.1.192900")
    monkeypatch.setattr(sys, "path", list(sys.path))
    try:
        found = pairing.adopt()
        assert found.route == pairing.ROUTE_CHECKOUT
        assert sys.path[0] == str(pair / pairing.PACKAGE_DIR)
        pairing.adopt()
        assert sys.path.count(str(pair / pairing.PACKAGE_DIR)) == 1
    finally:
        for name in VARIABLES:
            os.environ.pop(name, None)


# -- what a process pptmstr launches is given ------------------------------------------


def _built_pair() -> Path | None:
    """
    This checkout's pair when a process started here would take it, else
    ``None``: asked for (``ASKED``) where an imtypeset is also installed, and
    by itself where none is, so the runs below ask.
    """
    environ: dict[str, str] = {}
    if pairing.decide(environ, ROOT).route != pairing.ROUTE_CHECKOUT:
        return None
    return Path(environ[pairing.LIBRARY_VARIABLE]).parent


needs_a_built_pair = pytest.mark.skipif(
    _built_pair() is None, reason="no pair built for this checkout's interpreter"
)


def _adopted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str]) -> Path:
    """
    ``pairing`` as it is once ``adopt`` has decided for a checkout in ``environ``; the pair.
    """
    pair = _checkout(tmp_path, prefix=Path(sys.prefix))
    monkeypatch.setattr(pairing, "_FOUND", pairing.decide(environ, tmp_path))
    return pair


def test_what_a_checkout_set_for_itself_is_taken_back_and_still_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    environ = {"PATH": "/bin"}
    pair = _adopted(tmp_path, monkeypatch, environ)
    assert set(VARIABLES) < set(environ)

    pairing.release(environ)

    assert environ == {"PATH": "/bin"}
    assert pairing.named(pairing.LIBRARY_VARIABLE) == str(pair / pairing.LIBRARY_NAME)
    assert pairing.named(pairing.PACKAGE_VARIABLE) == str(pair / pairing.PACKAGE_DIR)
    pairing.release(environ)
    assert environ == {"PATH": "/bin"}


def test_a_pair_the_caller_set_is_passed_on_as_it_came(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _checkout(tmp_path)
    environ = {pairing.LIBRARY_VARIABLE: "/another/lib.so", pairing.PACKAGE_VARIABLE: ""}
    monkeypatch.setattr(pairing, "_FOUND", pairing.decide(dict(environ), tmp_path))
    assert pairing._FOUND is not None and pairing._FOUND.route == pairing.ROUTE_ENVIRONMENT

    kept = dict(environ)
    pairing.release(kept)

    assert kept == environ
    monkeypatch.setenv(pairing.LIBRARY_VARIABLE, "/another/lib.so")
    assert pairing.named(pairing.LIBRARY_VARIABLE) == "/another/lib.so"


def test_a_variable_changed_since_is_somebody_elses_and_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ: dict[str, str] = {}
    pair = _adopted(tmp_path, monkeypatch, environ)
    environ[pairing.LIBRARY_VARIABLE] = "/set/later.so"

    pairing.release(environ)

    assert environ == {pairing.LIBRARY_VARIABLE: "/set/later.so"}
    assert pair.is_dir()


def test_nothing_is_taken_back_before_the_process_has_decided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pairing, "_FOUND", None)
    environ = {pairing.LIBRARY_VARIABLE: "/x.so"}
    pairing.release(environ)
    assert environ == {pairing.LIBRARY_VARIABLE: "/x.so"}
    monkeypatch.delenv(pairing.LIBRARY_VARIABLE, raising=False)
    assert pairing.named(pairing.LIBRARY_VARIABLE) is None


def test_the_library_is_asked_before_its_variables_are_taken_back_whatever_asking_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(typesetter, "module", lambda: order.append("asked"))
    monkeypatch.setattr(pairing, "release", lambda: order.append("released"))
    gate.settle()
    assert order == ["asked", "released"]

    def gone() -> Any:
        raise ModuleNotFoundError("No module named 'imscribe'")

    monkeypatch.setattr(gate, "_typesetter", gone)
    gate.settle()
    assert order == ["asked", "released", "released"]


_CHILD = (
    "import json, os, subprocess, sys\n"
    "from pptmstr import __main__ as entry, app, pairing\n"
    "def application(argv):\n"
    "    # What the SDK's transport starts a session's process with: os.environ.\n"
    "    child = subprocess.run(\n"
    "        [sys.executable, '-c', 'import json, os; print(json.dumps('\n"
    "         '{k: v for k, v in os.environ.items() if k.startswith(\"IMTYPESET_\")}))'],\n"
    "        capture_output=True, text=True, check=True)\n"
    "    from imgui_bundle import imgui\n"
    "    from pptmstr import theme\n"
    "    from pptmstr.ui import typeset\n"
    "    context = imgui.create_context()\n"
    "    io = imgui.get_io()\n"
    "    io.display_size = imgui.ImVec2(800.0, 600.0)\n"
    "    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures.value\n"
    "    io.fonts.add_font_default()\n"
    "    bold = theme._ASSETS / 'fonts' / 'Inconsolata-Bold.ttf'\n"
    "    loaded = [typeset.load_fonts(bold)]\n"
    "    cache = typeset.ViewCache()\n"
    "    for _ in range(2):\n"
    "        typeset.pre_frame(); imgui.new_frame(); imgui.begin('w')\n"
    "        typeset.markdown(cache, 'k', 'Some *text* to set.')\n"
    "        imgui.end(); imgui.render()\n"
    "    loaded.append(typeset.load_fonts(bold))\n"
    "    print(json.dumps({'child': json.loads(child.stdout), 'loaded': loaded,\n"
    "        'unset': cache.unbuilt('k'), 'route': pairing.adopt().route,\n"
    "        'named': pairing.named(pairing.LIBRARY_VARIABLE),\n"
    "        'reason': typeset.unavailable_reason()}))\n"
    "    typeset.unload(); imgui.destroy_context(context)\n"
    "    return 0\n"
    "app.main = application\n"
    "raise SystemExit(entry.main(['--fake']))\n"
)


@needs_a_built_pair
@not_the_run_without
def test_a_process_pptmstr_starts_is_not_given_the_pair_pptmstr_found_for_itself() -> None:
    """
    Started with neither variable and its pair asked for, a checkout sets both
    for itself; a session's
    process started from it would read them as set by its caller, and another
    checkout's tests would run on this one's library. By the time the
    application runs a child is given neither, and the library that was found
    through them still loads its fonts, draws, and loads them again.
    """
    pair = _built_pair()
    assert pair is not None
    done = _run(script=_CHILD, **ASKED)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["route"] == pairing.ROUTE_CHECKOUT
    assert seen["child"] == {}
    assert seen["loaded"] == [True, True] and seen["unset"] == 0, seen["reason"]
    assert seen["named"] == str(pair / pairing.LIBRARY_NAME)


_AGAIN = (
    "import json, os, subprocess, sys\n"
    "from pptmstr import __main__ as entry, app, pairing\n"
    "def named():\n"
    "    return sorted(k for k in os.environ if k.startswith('IMTYPESET_'))\n"
    "def application(argv):\n"
    "    from imscribe import typesetter\n"
    "    seen = {'route': pairing.adopt().route, 'released': named(),\n"
    "            'first': typesetter.kind().value}\n"
    "    typesetter.reset()\n"
    "    seen['again'] = typesetter.module() is not None\n"
    "    seen['kind'] = typesetter.kind().value\n"
    "    seen['reason'] = typesetter.reason()\n"
    "    seen['named_by'] = typesetter.pairing()['named_by']\n"
    "    seen['library'] = typesetter.pairing()['library']\n"
    "    seen['after'] = named()\n"
    "    child = subprocess.run(\n"
    "        [sys.executable, '-c', 'import os; print(sorted('\n"
    "         'k for k in os.environ if k.startswith(\"IMTYPESET_\")))'],\n"
    "        capture_output=True, text=True, check=True)\n"
    "    seen['child'] = child.stdout.strip()\n"
    "    print(json.dumps(seen))\n"
    "    return 0\n"
    "app.main = application\n"
    "raise SystemExit(entry.main(['--fake']))\n"
)


@needs_a_built_pair
@not_the_run_without
def test_the_library_asked_again_is_found_again_after_its_variables_were_taken_back() -> None:
    """
    The library is asked again after a ``typesetter.reset()``: a test process
    that provoked a failure, an operator's "try again". By then the entry
    point has taken both variables back out of the environment, and a library
    that looked only there would answer "not installed" beside the pair it had
    just been drawing with. It remembers the pair a load by the environment was
    accepted with, so the second asking loads it; nothing is put back in the
    environment for that, and a child is still given neither variable.
    """
    pair = _built_pair()
    assert pair is not None
    done = _run(script=_AGAIN, **ASKED)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["route"] == pairing.ROUTE_CHECKOUT
    assert seen["released"] == [] and seen["first"] == gate.READY
    assert seen["again"] is True and seen["kind"] == gate.READY, seen["reason"]
    assert seen["named_by"] == typesetter.NAMED_BY_MEMORY
    assert seen["library"] == str(pair / pairing.LIBRARY_NAME)
    assert seen["after"] == [] and seen["child"] == "[]"


@needs_a_built_pair
@not_the_run_without
def test_a_process_pptmstr_starts_is_given_the_pair_pptmstrs_caller_named() -> None:
    pair = _built_pair()
    assert pair is not None
    named = {
        pairing.LIBRARY_VARIABLE: str(pair / pairing.LIBRARY_NAME),
        pairing.PACKAGE_VARIABLE: str(pair / pairing.PACKAGE_DIR),
    }
    done = _run(script=_CHILD, **named)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["route"] == pairing.ROUTE_ENVIRONMENT
    assert seen["child"] == named
    assert seen["loaded"] == [True, True]


@needs_a_built_pair
def test_the_report_asked_for_through_the_application_still_names_the_checkouts_pair() -> None:
    # `--fake --version` is the application's parser's, after the variables were taken back.
    lines = _lines(_run("--fake", "--version", **ASKED))
    assert "found through this checkout's .deps" in lines["imtypeset"]
    assert lines["typeset"].startswith("typeset text: the library is accepted beside this wheel")


# -- the entry point ----------------------------------------------------------------


def test_the_command_and_the_module_are_one_entry_that_decides_before_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _pyproject()["project"]["scripts"] == {"pptmstr": "pptmstr.__main__:main"}
    order: list[Any] = []
    monkeypatch.setattr(pairing, "adopt", lambda: order.append("adopt"))
    monkeypatch.setattr(gate, "settle", lambda: order.append("settle"))
    monkeypatch.setattr(app, "main", lambda argv: order.append(argv) or 7)

    assert entry.main(["--fake"]) == 7

    # The library is asked for, and the variables taken back, before the
    # application can start a session that would inherit them.
    assert order == ["adopt", "settle", ["--fake"]]


def test_the_applications_own_parser_answers_version_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Whoever calls `app.main` directly, or abbreviates the flag past the entry.
    monkeypatch.setattr(version, "main", lambda: 0)
    monkeypatch.setattr(app.settings_mod, "load", lambda: pytest.fail("the application started"))
    assert app.main(["--version"]) == 0
    assert app.main(["--vers"]) == 0


def test_version_is_answered_without_the_application_or_an_imgui_context() -> None:
    done = _run(
        script=(
            "import sys\n"
            "from pptmstr.__main__ import main\n"
            "status = main(['--version'])\n"
            "from imgui_bundle import imgui\n"
            "assert 'pptmstr.app' not in sys.modules, 'the application was imported'\n"
            "assert 'OpenGL' not in sys.modules\n"
            "assert imgui.get_current_context() is None\n"
            "raise SystemExit(status)\n"
        ),
        **OFF,
    )
    assert _lines(done)["pptmstr"].startswith("pptmstr ")


@pytest.mark.parametrize(
    "args",
    [["--task", "--version"], ["--theme", "--version"], ["--", "--version"]],
)
def test_version_where_another_options_value_goes_is_the_applications_parser_to_read(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    """
    Only the application's parser knows which options take a value, and it
    refuses each of these. The entry answers ``--version`` itself where nothing
    before it could be an option waiting for one, and hands the rest over.
    """
    order: list[Any] = []
    monkeypatch.setattr(pairing, "adopt", lambda: None)
    monkeypatch.setattr(gate, "settle", lambda: None)
    monkeypatch.setattr(version, "main", lambda: order.append("report") or 0)
    monkeypatch.setattr(app, "main", lambda argv: order.append(argv) or 2)
    assert entry.main(args) == 2
    assert order == [args]
    with pytest.raises(SystemExit) as refused:
        monkeypatch.undo()
        monkeypatch.setattr(version, "main", lambda: pytest.fail("the report was printed"))
        app.main(args)
    assert refused.value.code == 2


@pytest.mark.parametrize(
    "args", [["--version"], ["--theme=dark", "--version"], ["--version", "--fake"]]
)
def test_version_where_a_flag_goes_is_answered_before_the_application(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setattr(pairing, "adopt", lambda: None)
    monkeypatch.setattr(version, "main", lambda: 0)
    monkeypatch.setattr(app, "main", lambda argv: pytest.fail("the application was asked"))
    assert entry.main(args) == 0


def test_the_report_is_printed_to_an_output_that_cannot_encode_it(tmp_path: Path) -> None:
    """
    A path with a character the terminal's encoding lacks, under a locale that
    is not UTF-8: the character is written as its escape and the rest whole.
    """
    named = refused_library(tmp_path / "caf\u00e9")
    done = _run("--version", **named, PYTHONIOENCODING="ascii")
    lines = _lines(done)
    assert "/caf\\xe9/" + pairing.LIBRARY_NAME in lines["imtypeset"]


def test_the_report_into_a_pipe_nobody_reads_is_not_a_traceback() -> None:
    # `pptmstr --version | head -1`, with the reader gone before the first line.
    reading, writing = os.pipe()
    os.close(reading)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pptmstr", "--version"],
            cwd=ROOT,
            stdout=writing,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
            env=without_the_library(),
        )
    finally:
        os.close(writing)
    assert done.stderr == "" and done.returncode == 0


# -- the report, each wrong thing a sentence ------------------------------------------


def test_switched_off_the_report_says_so_and_not_that_nothing_is_installed_and_exits_0() -> None:
    lines = _lines(_run("--version", **OFF))

    assert lines["typeset"] == (
        "typeset text: not in use (not-installed): "
        "typeset text is switched off (IMSCRIBE_NO_TYPESET=1)"
    )
    assert lines["imtypeset"] == (
        f"imtypeset: no library ({pairing.DISABLE_VARIABLE} is set, which is how to run without "
        f"the library: none is looked for); pptmstr pins {pairing.IMTYPESET_PIN}"
    )
    assert lines["python"].endswith(f"({sys.executable})")


def test_a_library_that_is_found_and_cannot_be_loaded_is_refused_in_a_sentence(
    tmp_path: Path,
) -> None:
    named = refused_library(tmp_path)
    lines = _lines(_run("--version", **named))

    assert lines["typeset"].startswith(
        "typeset text: not in use (refused): the typesetting library was refused ("
    )
    assert "there is no library at " in lines["typeset"]
    assert lines["imtypeset"].startswith(
        f"imtypeset at an unrecorded commit: {named[pairing.LIBRARY_VARIABLE]}, "
    )
    assert "found through the environment" in lines["imtypeset"]


@needs_a_distribution
def test_with_an_imtypeset_installed_the_report_names_it_its_route_and_its_headers() -> None:
    """
    What an install from the index is, said from the library's own answers:
    the distribution and its version against the pin, the library it carries
    and that library's version, that it was found as an installed
    distribution, and the headers it says it was compiled against. No commit
    is claimed from a ref file: a wheel records none.
    """
    lines = _lines(_run("--version"))

    assert lines["imtypeset"].startswith(
        f"imtypeset {pairing.IMTYPESET_PIN}, the version pptmstr pins: library "
    )
    assert ", found as an installed distribution" in lines["imtypeset"]
    assert re.search(
        r"; headers \S+, accepted by \S+, built for imgui-bundle==", lines["imtypeset"]
    )
    assert pairing.IMTYPESET_REF not in lines["imtypeset"]
    assert "NOT read" not in lines["imtypeset"]
    assert lines["typeset"].startswith("typeset text: the library is accepted beside this wheel")
    assert lines["imscribe"].startswith(
        f"imscribe {pairing.IMSCRIBE_PIN}, the version pptmstr pins: imported from "
    )


@needs_a_distribution
def test_beside_an_installed_imtypeset_a_library_the_variables_name_is_said_not_to_be_read(
    tmp_path: Path,
) -> None:
    """
    An installed imtypeset reads neither variable. A report that went by the
    environment would name a library that is not the one in the process, so
    the line is of the one that loaded and says the variables chose nothing;
    and ``IMTYPESET_LIBRARY=``, which ran without the library when the library
    was found only through that variable, is said not to.
    """
    lines = _lines(
        _run(
            "--version",
            IMTYPESET_LIBRARY=str(tmp_path / pairing.LIBRARY_NAME),
            IMTYPESET_PYTHON=str(tmp_path / pairing.PACKAGE_DIR),
        )
    )
    assert ", found as an installed distribution" in lines["imtypeset"]
    assert "IMTYPESET_LIBRARY, IMTYPESET_PYTHON set and NOT read" in lines["imtypeset"]
    assert str(tmp_path) not in lines["imtypeset"]
    assert lines["typeset"].startswith("typeset text: the library is accepted beside this wheel")

    lines = _lines(_run("--version", IMTYPESET_LIBRARY=""))
    assert ", found as an installed distribution" in lines["imtypeset"]
    assert f"{pairing.DISABLE_VARIABLE}=1 is how to run without the library" in lines["imtypeset"]


def test_an_imgui_bundle_that_is_not_the_pinned_one_is_a_sentence_naming_both() -> None:
    done = _run(
        script=(
            "import imgui_bundle\n"
            "from pptmstr import version\n"
            "imgui_bundle.__version__ = '1.0.1'\n"
            "raise SystemExit(version.main())\n"
        ),
        **OFF,
    )
    assert _lines(done)["imgui-bundle"] == (
        f"imgui-bundle 1.0.1 is running and pptmstr pins {pairing.IMGUI_BUNDLE_PIN}: the "
        f"typesetting library is built for one wheel and is refused beside another"
    )


def test_the_pinned_imgui_bundle_is_said_to_be_the_pinned_one() -> None:
    import imgui_bundle

    assert version._imgui_bundle() == (
        f"imgui-bundle {imgui_bundle.__version__}, the version pptmstr pins"
    )


def test_with_imscribe_unimportable_the_report_says_so_twice_and_exits_0() -> None:
    done = _run(
        script=(
            "import sys\n"
            "sys.modules['imscribe'] = None\n"
            "from pptmstr import version\n"
            "raise SystemExit(version.main())\n"
        ),
        **OFF,
    )
    lines = _lines(done)
    assert lines["imscribe"].startswith("imscribe: not installed (")
    assert lines["imscribe"].endswith(f"pptmstr pins {pairing.IMSCRIBE_PIN}")
    assert lines["typeset"].startswith(
        "typeset text: not in use (not-installed): the text library is not installed ("
    )


def test_a_part_that_raises_costs_its_own_line_and_no_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> str:
        raise RuntimeError("no such thing")

    monkeypatch.setattr(version, "_python", broken)
    monkeypatch.setattr(version, "_typeset", lambda: "typeset text: not asked")

    lines = version.report(pairing.Found(pairing.ROUTE_NONE, "for the test")).splitlines()

    assert lines[1] == "python: could not be read (RuntimeError: no such thing)"
    assert len(lines) == 6 and lines[0].startswith("pptmstr ") and lines[5].endswith("not asked")


@needs_a_built_pair
def test_a_checkout_asked_for_its_own_pair_finds_it_and_it_is_accepted() -> None:
    lines = _lines(_run("--version", **ASKED))

    asked = pairing.read_ref(ROOT / pairing.IMTYPESET_REF)
    assert re.match(rf"imtypeset \S+ {asked}: ", lines["imtypeset"]), "its version and its commit"
    assert "found through this checkout's .deps" in lines["imtypeset"]
    assert lines["imtypeset"].endswith(f"the commit {pairing.IMTYPESET_REF} names")
    assert lines["typeset"].startswith("typeset text: the library is accepted beside this wheel")


@needs_a_built_pair
def test_the_same_pair_named_by_the_caller_is_said_to_come_from_the_environment() -> None:
    # As the Makefile names it for a recipe: the package first on the path too,
    # which is what makes it the one imported beside an installed imtypeset.
    pair = _built_pair()
    assert pair is not None
    lines = _lines(
        _run(
            "--version",
            IMTYPESET_LIBRARY=str(pair / pairing.LIBRARY_NAME),
            IMTYPESET_PYTHON=str(pair / pairing.PACKAGE_DIR),
            PYTHONPATH=str(pair / pairing.PACKAGE_DIR),
        )
    )
    assert "found through the environment (IMTYPESET_LIBRARY and IMTYPESET_PYTHON set by" in (
        lines["imtypeset"]
    )
    assert lines["typeset"].startswith("typeset text: the library is accepted beside this wheel")


# -- what is installed, against what the tree asks for --------------------------------


def _installed_imscribe(
    top: Path, monkeypatch: pytest.MonkeyPatch, direct_url: dict[str, Any] | None
) -> tuple[Path, importlib.metadata.Distribution]:
    """
    An ``imscribe`` package in a site directory of its own, with a distribution
    that records ``direct_url``, or none where it is ``None``, as an install
    by name leaves it; the package's directory and the distribution.
    """
    site = top / "site"
    package = site / "imscribe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("NAME = 'imscribe'\n", encoding="utf-8")
    record = site / "imscribe-0.1.0.dist-info"
    record.mkdir()
    (record / "METADATA").write_text("Name: imscribe\nVersion: 0.1.0\n", encoding="utf-8")
    if direct_url is not None:
        (record / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")
    distribution = importlib.metadata.Distribution.at(record)
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: distribution)
    return package, distribution


def _clone(top: Path, head: str) -> Path:
    clone = top / "clone"
    (clone / ".git").mkdir(parents=True)
    (clone / ".git" / "HEAD").write_text(f"{head}\n", encoding="utf-8")
    (clone / "imscribe").mkdir()
    (clone / "imscribe" / "__init__.py").write_text("NAME = 'imscribe'\n", encoding="utf-8")
    return clone


def test_a_copy_installed_by_name_records_no_origin_and_none_is_claimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    What an install of pptmstr's requirements leaves: no ``direct_url.json``.
    **That is not evidence of a package index.** Run on 2026-10-13 with pip
    26.2.1: ``pip install --no-index --find-links DIR imscribe==0.2.0b1``
    writes none either, so a wheel somebody built and put in a directory
    reads the same as the one on PyPI. The line says how it was installed and
    does not say "published"; no ref file is compared with it.
    """
    package, distribution = _installed_imscribe(tmp_path, monkeypatch, None)
    commit, how = version._origin(distribution, package)
    assert commit is None
    assert how == (
        "installed by name from an index or a directory of wheels, which records neither "
        "that nor a commit"
    )
    assert "published" not in how


def test_a_copy_installed_from_a_file_names_the_file_and_its_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheel = tmp_path / "imscribe-0.1.0-py3-none-any.whl"
    package, distribution = _installed_imscribe(
        tmp_path, monkeypatch, {"url": wheel.as_uri(), "archive_info": {"hash": "sha256=abc"}}
    )
    assert version._origin(distribution, package) == (
        None,
        f"installed from the file {wheel} (sha256 abc)",
    )


def test_a_copy_installed_from_a_checkout_is_that_checkouts_commit_only_file_for_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, REF)
    package, distribution = _installed_imscribe(
        tmp_path, monkeypatch, {"url": clone.as_uri(), "dir_info": {}}
    )

    assert version._origin(distribution, package) == (
        REF,
        f"file for file the checkout at {clone}",
    )

    # The clone moved on after the install: `make deps` fetched and did not install.
    (clone / "imscribe" / "__init__.py").write_text("NAME = 'newer'\n", encoding="utf-8")
    commit, how = version._origin(distribution, package)
    assert commit is None and "1 file(s) differ" in how and "__init__.py" in how


def test_a_checkout_on_a_branch_names_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, "ref: refs/heads/main")
    package, distribution = _installed_imscribe(
        tmp_path, monkeypatch, {"url": clone.as_uri(), "dir_info": {}}
    )
    commit, how = version._origin(distribution, package)
    assert commit is None and "on a branch" in how


def test_a_copy_installed_from_version_control_is_the_commit_its_record_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://example.invalid/imscribe.git"
    package, distribution = _installed_imscribe(
        tmp_path, monkeypatch, {"url": url, "vcs_info": {"vcs": "git", "commit_id": OTHER}}
    )
    assert version._origin(distribution, package) == (OTHER, f"installed from {url}")


def test_a_distribution_that_is_another_copy_says_nothing_of_the_one_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A path that reaches two site-packages can find the distribution in one and
    the module in the other: this worktree's does. A version read from the
    wrong one would be said of a copy that is not running.
    """
    package, _ = _installed_imscribe(tmp_path, monkeypatch, None)
    assert version._installed("imscribe", package) is not None
    assert version._installed("imscribe", tmp_path / "elsewhere" / "imscribe") is None

    monkeypatch.setattr(version, "_installed", lambda name, found: None)
    line = version._imscribe()
    assert line.startswith("imscribe at an unrecorded version: imported from ")
    assert f"pptmstr pins {pairing.IMSCRIBE_PIN} and nothing here says whether this is it" in line


class _Stood:
    """
    A distribution of one version, for the line and not for its origin.
    """

    def __init__(self, said: str) -> None:
        self.version = said


def test_the_imscribe_line_says_the_version_against_the_pin_and_a_commit_only_where_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = Path(typesetter.__file__).resolve().parent
    pin = pairing.IMSCRIBE_PIN
    monkeypatch.setattr(version, "_asked_for", lambda name: REF)
    monkeypatch.setattr(version, "_installed", lambda name, found: _Stood(pin))

    monkeypatch.setattr(version, "_origin", lambda distribution, found: (None, "from an index"))
    assert version._imscribe() == (
        f"imscribe {pin}, the version pptmstr pins: imported from {package}, from an index"
    )

    monkeypatch.setattr(version, "_origin", lambda distribution, found: (REF, "as recorded"))
    assert version._imscribe() == (
        f"imscribe {pin}, the version pptmstr pins: imported from {package}, as recorded; "
        f"commit {REF}, the commit imscribe.ref names"
    )

    monkeypatch.setattr(version, "_origin", lambda distribution, found: (OTHER, "as recorded"))
    line = version._imscribe()
    assert f"commit {OTHER}, NOT the commit imscribe.ref names, which is {REF}" in line

    monkeypatch.setattr(version, "_installed", lambda name, found: _Stood("0.1.0"))
    assert version._imscribe().startswith(
        f"imscribe 0.1.0 is running and pptmstr pins {pin}: imported from "
    )


def test_the_library_line_of_a_built_pair_is_held_to_built_from_and_not_to_the_ref_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = _checkout(tmp_path, commit=OTHER)
    library = str(pair / pairing.LIBRARY_NAME)
    monkeypatch.setattr(version, "_asked_for", lambda name: REF)
    found = pairing.Found(pairing.ROUTE_ENVIRONMENT, "set by the caller")
    # The library's own answer for a pair the two variables named and it loaded.
    said: dict[str, str | None] = {
        "package": str(pair / pairing.PACKAGE_DIR / "imtypeset"),
        "route": pairing.ROUTE_ENVIRONMENT,
        "library": library,
        "imtypeset_version": "0.4.1",
    }

    line = version._imtypeset(found, said)
    assert line.startswith(f"imtypeset 0.4.1 {OTHER}: {library}, ")
    assert f"NOT the commit imtypeset.ref names, which is {REF}" in line

    (pair / pairing.BUILT_FROM).unlink()
    line = version._imtypeset(found, said)
    assert line.startswith("imtypeset 0.4.1 at an unrecorded commit: ") and REF in line

    # Named and not loaded: the line is still of what was named.
    assert version._imtypeset(found, {}, library).startswith(
        f"imtypeset at an unrecorded commit: {library}, "
    )
    assert version._imtypeset(found, {}).startswith("imtypeset: no library (")


def _said_of_a_distribution(package: Path, **more: str | None) -> dict[str, str | None]:
    return {
        "package": str(package),
        "route": pairing.ROUTE_DISTRIBUTION,
        "library": str(package / pairing.LIBRARY_NAME),
        "imtypeset_version": "0.4.1",
        "distribution": f"imtypeset {pairing.IMTYPESET_PIN}",
        "headers": "fork:0123abc",
        "accepted_by": "imgui-check+probe",
        "built_for": f"imgui-bundle=={pairing.IMGUI_BUNDLE_PIN}",
        **more,
    }


def test_the_library_line_of_an_installed_imtypeset_is_the_librarys_own_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    No ``BUILT-FROM`` and no ref file: a wheel has neither. The version is the
    installed distribution's against the pin, how it was installed is what
    its metadata records and no more, the commit is the one its own
    attestation names, and the headers and the acceptance are what the
    library says of itself.
    """
    site = tmp_path / "site"
    package = site / "imtypeset"
    package.mkdir(parents=True)
    record = site / f"imtypeset-{pairing.IMTYPESET_PIN}.dist-info"
    record.mkdir()
    (record / "METADATA").write_text(
        f"Name: imtypeset\nVersion: {pairing.IMTYPESET_PIN}\n", encoding="utf-8"
    )
    distribution = importlib.metadata.Distribution.at(record)
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: distribution)
    found = pairing.Found(pairing.ROUTE_DISTRIBUTION, "imtypeset is installed")

    line = version._imtypeset(found, _said_of_a_distribution(package))
    assert line == (
        f"imtypeset {pairing.IMTYPESET_PIN}, the version pptmstr pins: library 0.4.1 at "
        f"{package / pairing.LIBRARY_NAME}, found as an installed distribution, installed by "
        f"name from an index or a directory of wheels, which records neither that nor a commit; "
        f"headers fork:0123abc, accepted by imgui-check+probe, built for "
        f"imgui-bundle=={pairing.IMGUI_BUNDLE_PIN}"
    )
    assert "published" not in line

    (package / pairing.ATTESTATION).write_text(
        json.dumps({"attested_where": f"a build at {REF}"}), encoding="utf-8"
    )
    assert f"; attested by a build at {REF}; headers " in version._imtypeset(
        found, _said_of_a_distribution(package)
    )

    # Installed from a file named on the command line: the file is said.
    (record / "direct_url.json").write_text(
        json.dumps({"url": "file:///tmp/built.whl", "archive_info": {}}), encoding="utf-8"
    )
    line = version._imtypeset(found, _said_of_a_distribution(package))
    assert "installed from the file /tmp/built.whl" in line

    # Installed and refused at import: nothing is loaded to describe.
    assert version._imtypeset(found, {"kind": gate.REFUSED}) == (
        f"imtypeset: not loaded (imtypeset is installed, and it did not load: the next line "
        f"says why); pptmstr pins {pairing.IMTYPESET_PIN}"
    )

    # Another version than the pin, and variables that were set and not read.
    (record / "METADATA").write_text("Name: imtypeset\nVersion: 0.5.0.192900\n", encoding="utf-8")
    line = version._imtypeset(
        pairing.Found(pairing.ROUTE_ENVIRONMENT, "IMTYPESET_LIBRARY set by the caller"),
        _said_of_a_distribution(package, ignored_environment="IMTYPESET_LIBRARY"),
    )
    assert line.startswith(
        f"imtypeset 0.5.0.192900 is running and pptmstr pins {pairing.IMTYPESET_PIN}: "
    )
    assert line.endswith(
        "; IMTYPESET_LIBRARY set and NOT read: an installed imtypeset reads neither, and the "
        "library named there is not the one in use"
    )


# -- the kind, asked of the library where it can say ---------------------------------


def test_the_librarys_own_kind_is_the_answer_whatever_the_variables_say(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A library found with neither variable set, one named and refused once
    # its variable was taken back, and a platform the library does not serve.
    # Not switched off, whatever the run is: that sentence is pptmstr's own
    # (``gate.not_installed``).
    monkeypatch.delenv(pairing.LIBRARY_VARIABLE, raising=False)
    monkeypatch.delenv(pairing.DISABLE_VARIABLE, raising=False)
    monkeypatch.setattr(typesetter, "reason", lambda: "its own words")

    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.READY)
    assert gate.library() == (gate.READY, "")

    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.REFUSED)
    assert gate.library() == (
        gate.REFUSED,
        "the typesetting library was refused (its own words)",
    )

    monkeypatch.setenv(pairing.LIBRARY_VARIABLE, "/some/lib.so")
    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.NOT_INSTALLED)
    assert gate.library() == (
        gate.NOT_INSTALLED,
        "the typesetting library is not installed (its own words)",
    )

    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.UNSUPPORTED)
    assert gate.library() == (
        "unsupported",
        "the typesetting library is not in use (unsupported: its own words)",
    )


def test_what_the_report_reads_of_the_librarys_answer_are_keys_the_library_gives() -> None:
    """
    The report is built from ``typesetter.pairing()``, whose keys are imscribe's.
    A key the report reads and imscribe does not give is a line that says "not
    stated" of something the library did state under another name.
    """
    read = set(re.findall(r'said(?:\.get\(|\[)"([a-z_]+)"', _source(version)))
    read |= set(
        re.findall(r'said(?:\.get\(|\[)"([a-z_]+)"', _source(sys.modules[described.__module__]))
    )
    assert {"route", "library", "headers", "ignored_environment", "kind"} <= read
    assert read <= set(typesetter.pairing())


def _source(module: Any) -> str:
    return Path(module.__file__).read_text(encoding="utf-8")


def test_the_kinds_the_gate_names_are_the_librarys_words_for_them() -> None:
    assert (gate.READY, gate.NOT_INSTALLED, gate.REFUSED, gate.FAILED) == tuple(
        typesetter.Kind[name].value for name in ("READY", "NOT_INSTALLED", "REFUSED", "FAILED")
    )


def test_the_gate_answers_in_a_sentence_whatever_asking_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gone() -> Any:
        raise ModuleNotFoundError("No module named 'imscribe'")

    def broken() -> Any:
        raise RuntimeError("the gate broke")

    monkeypatch.setattr(typesetter, "module", broken)
    kind, sentence = gate.library()
    assert kind == gate.FAILED and "RuntimeError: the gate broke" in sentence

    monkeypatch.setattr(gate, "_typesetter", gone)
    kind, sentence = gate.library()
    assert kind == gate.NOT_INSTALLED
    assert sentence.startswith("the text library is not installed (ModuleNotFoundError")


# -- a pair built against other headers than the wheel's is said to be one ---------------


def _built(pair: Path, headers: str | None) -> Path:
    pair.mkdir(parents=True)
    against = f"headers: {headers}\n" if headers else ""
    (pair / pairing.BUILT_FROM).write_text(f"commit: {REF}\n{against}", encoding="utf-8")
    (pair / pairing.LIBRARY_NAME).write_bytes(b"")
    (pair / pairing.PACKAGE_DIR / "imtypeset").mkdir(parents=True)
    (pair / pairing.PACKAGE_DIR / "imtypeset" / "__init__.py").write_text("", encoding="utf-8")
    return pair


def test_a_pair_is_noted_unless_it_was_built_against_the_wheels_own_headers(
    tmp_path: Path,
) -> None:
    """
    Not a refusal: a stand-in is the only build there is where the wheel's
    fork cannot be fetched. A note, for wherever the pair is handed over, and
    a pair whose record names no headers at all is not taken for the fork's.
    """
    assert pairing.headers_note(_built(tmp_path / "fork", "fork:pthom-0123abc")) is None
    stood = pairing.headers_note(_built(tmp_path / "stood", "stand-in:ocornut-v1.92.9-docking"))
    assert stood is not None and str(tmp_path / "stood") in stood
    assert "built against stand-in:ocornut-v1.92.9-docking, NOT the wheel's own" in stood
    silent = pairing.headers_note(_built(tmp_path / "silent", None))
    assert silent is not None and "headers its BUILT-FROM does not record" in silent
    # And handing such a pair over is not refused for it.
    for name in ("fork", "stood", "silent"):
        assert pairing.pair_refusal(tmp_path / name, REF) is None


def test_a_test_run_says_first_what_it_ran_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The lines every run of the suite opens with (``tests/conftest.py``), from
    what imscribe says of the library a process with that environment loads:
    the imscribe imported and the library, as ``pptmstr --version`` says them,
    and a line of its own for each thing a green run would be misread over --
    a stand-in's headers, variables that were set and not read, no library.
    """
    monkeypatch.setattr(version, "_asked_for", lambda name: REF)
    stood = _built(tmp_path / "stood", "stand-in:ocornut-v1.92.9-docking")
    named = {pairing.LIBRARY_VARIABLE: str(stood / pairing.LIBRARY_NAME)}
    loaded: dict[str, Any] = {
        "kind": gate.READY,
        "package": str(stood / pairing.PACKAGE_DIR / "imtypeset"),
        "route": pairing.ROUTE_ENVIRONMENT,
        "library": named[pairing.LIBRARY_VARIABLE],
        "imtypeset_version": "0.4.1",
        "headers": "stand-in:ocornut-v1.92.9-docking",
    }
    lines = described(loaded, named)
    assert [line.split()[0] for line in lines] == ["imscribe", "imtypeset", "STAND-IN:"]
    assert REF in lines[1] and "with headers stand-in:ocornut-v1.92.9-docking" in lines[1]
    assert "NOT the wheel's own Dear ImGui headers" in lines[2]

    assert len(described({**loaded, "headers": "fork:pthom-0123abc"}, named)) == 2

    # An installed imtypeset loaded although the variables name another library.
    lines = described(
        _said_of_a_distribution(
            tmp_path / "site" / "imtypeset",
            kind=gate.READY,
            ignored_environment="IMTYPESET_LIBRARY",
        ),
        named,
    )
    assert [line.split()[0] for line in lines] == ["imscribe", "imtypeset", "IGNORED:"]
    assert f"the installed imtypeset {pairing.IMTYPESET_PIN} is what every test draws" in lines[2]

    none = described({"kind": gate.NOT_INSTALLED}, OFF)
    assert none[1].startswith(f"imtypeset: no library ({pairing.DISABLE_VARIABLE} is set")
    assert none[2] == ("NO LIBRARY (not-installed): every test that draws through it is skipped")
    refused = described({"kind": gate.REFUSED, "reason": "its own words"}, named)
    assert refused[-1] == (
        "NO LIBRARY (refused): every test that draws through it FAILS (its own words)"
    )
    # A child that did not answer is said, not raised.
    assert described({}, {})[-1].startswith("NO LIBRARY (imscribe did not answer)")


@pytest.mark.parametrize("quiet", [True, False], ids=["-q, as make test runs it", "as it comes"])
def test_the_lines_open_a_run_of_the_suite_quiet_or_not(tmp_path: Path, quiet: bool) -> None:
    """
    Asked of a new process with the run's own environment, here one that is
    switched off: the same lines whatever is installed or built where this runs.
    """
    (tmp_path / "test_nothing.py").write_text("def test_nothing():\n    pass\n", encoding="utf-8")
    environ = {k: v for k, v in without_the_library().items() if not k.startswith("PYTEST_")}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *(["-q"] if quiet else []),
            "-p",
            "no:cacheprovider",
            "--confcutdir",
            str(ROOT),
            "-c",
            str(ROOT / "pyproject.toml"),
            "--rootdir",
            str(ROOT),
            "-p",
            "tests.conftest",
            str(tmp_path / "test_nothing.py"),
        ],
        cwd=ROOT,
        env=environ,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout
    said = [line for line in out.splitlines() if line.startswith(("imscribe", "imtypeset", "NO "))]
    assert [line.split()[0] for line in said] == ["imscribe", "imtypeset:", "NO"]
    assert said[2] == "NO LIBRARY (not-installed): every test that draws through it is skipped"
    assert out.index(said[0]) < out.index("1 passed"), "before any result"


# -- IMSCRIBE_NO_TYPESET is no library, whatever else is set ------------------------


def test_switched_off_the_two_variables_are_taken_out_and_said(tmp_path: Path) -> None:
    """
    imscribe still loads a library ``IMTYPESET_LIBRARY`` names under its switch,
    so a left-over variable would put the library in a run asked to have none.
    """
    _checkout(tmp_path, prefix=tmp_path / "venv")
    named = {name: f"/left/over/{name}" for name in VARIABLES}
    environ = {**OFF, **ASKED, **named, "PATH": "/bin"}

    found = pairing.decide(environ, tmp_path, tmp_path / "venv", "imtypeset 0.4.1.192900")

    assert found.route == pairing.ROUTE_NONE and found.given == ()
    assert found.withheld == VARIABLES
    assert environ == {**OFF, **ASKED, "PATH": "/bin"}
    assert "IMTYPESET_LIBRARY and IMTYPESET_PYTHON were set as well" in found.why
    line = version._imtypeset(found, {}, pairing.named(pairing.LIBRARY_VARIABLE))
    assert line.startswith("imtypeset: no library (IMSCRIBE_NO_TYPESET is set")
    assert "taken out of this process's environment" in line


def _a_real_library() -> dict[str, str]:
    """
    Both variables naming a library that loads here: this checkout's pair where
    it is whole and this environment's, or else the installed imtypeset's own.
    """
    pair = ROOT / pairing.DEPS_DIR / pairing.PAIR_DIR
    try:
        whole = pair.is_dir() and not pairing.pair_refusal(
            pair, pairing.read_ref(ROOT / pairing.IMTYPESET_REF), Path(sys.prefix)
        )
    except Exception:
        whole = False
    if whole:
        return {
            pairing.LIBRARY_VARIABLE: str(pair / pairing.LIBRARY_NAME),
            pairing.PACKAGE_VARIABLE: str(pair / pairing.PACKAGE_DIR),
        }
    from tests.conftest import asked_of_a_new_process

    said = asked_of_a_new_process(child_environment())
    if said.get("kind") != gate.READY or not said.get("library"):
        pytest.skip("no library loads here to name")
    library = Path(str(said["library"]))
    return {
        pairing.LIBRARY_VARIABLE: str(library),
        pairing.PACKAGE_VARIABLE: str(library.parent.parent),
    }


def test_switched_off_a_named_library_that_loads_is_not_loaded_by_pptmstr() -> None:
    named = _a_real_library()
    # The hazard is real: without pptmstr, imscribe loads the named library.
    from tests.conftest import asked_of_a_new_process

    assert asked_of_a_new_process(child_environment(**OFF, **named)).get("kind") == gate.READY

    lines = _lines(_run("--version", **OFF, **named))

    assert "taken out of this process's environment" in lines["imtypeset"]
    assert lines["typeset"].startswith("typeset text: not in use (not-installed)")


def test_switched_off_a_named_library_that_loads_skips_the_seams_tests() -> None:
    named = _a_real_library()
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_typeset_literal.py::"
            "test_literal_text_a_caller_asks_for_is_one_host_code_block_of_the_librarys",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=child_environment(**OFF, **named),
    )
    assert "1 skipped" in done.stdout, done.stdout + done.stderr
    assert "WITHHELD: IMTYPESET_LIBRARY and IMTYPESET_PYTHON" in done.stdout


# -- PPTMSTR_TYPESET_DEPS: a refusal is said, and nothing reaches a session ---------


def test_a_refused_deps_request_beside_an_installed_imtypeset_is_said(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _checkout(tmp_path, commit=OTHER, prefix=tmp_path / "venv")
    environ = dict(ASKED)

    found = pairing.decide(environ, tmp_path, tmp_path / "venv", "imtypeset 0.4.1.192900")

    assert found.route == pairing.ROUTE_DISTRIBUTION and OTHER in found.refused
    said = {
        "package": str(tmp_path / "site" / "imtypeset"),
        "route": pairing.ROUTE_DISTRIBUTION,
        "library": "/somewhere/libimtypeset_bundle.so",
        "distribution": "imtypeset 0.4.1.192900",
    }
    line = version._imtypeset(found, said)
    assert (
        f"; PPTMSTR_TYPESET_DEPS asked for .deps and it was refused: {found.refused}; "
        f"the installed library runs"
    ) in line
    # And in the first lines of a test run, which ask the same way.
    monkeypatch.setattr(pairing, "checkout", lambda: tmp_path)
    assert any("asked for .deps and it was refused" in row for row in described(said, ASKED))


def test_a_pair_built_for_another_environment_is_refused_by_the_make_step_too(
    tmp_path: Path,
) -> None:
    pair = _checkout(tmp_path, prefix=tmp_path / "another-venv")
    assert pairing.pair_refusal(pair, REF) is None
    said = pairing.pair_refusal(pair, REF, tmp_path / "venv")
    assert said is not None and "another-venv" in said
    assert pairing.pair_refusal(pair, REF, tmp_path / "another-venv") is None


def test_the_test_header_finds_this_checkout_and_does_not_say_there_is_none() -> None:
    """
    ``described`` decides with this checkout, so with ``PPTMSTR_TYPESET_DEPS``
    the header does not say pptmstr is installed with no checkout to look in.
    """
    rows = described({"distribution": "imtypeset 0.4.1.192900"}, ASKED)
    assert not any("no source checkout" in row for row in rows)


def test_the_header_names_the_pair_the_makefile_exports_as_the_makefiles(tmp_path: Path) -> None:
    pair = _checkout(tmp_path)
    exported = {
        **ASKED,
        pairing.LIBRARY_VARIABLE: str(pair / pairing.LIBRARY_NAME),
        pairing.PACKAGE_VARIABLE: str(pair / pairing.PACKAGE_DIR),
    }
    found = pairing.decide(dict(exported), tmp_path, distribution="imtypeset 0.4.1.192900")
    assert found.route == pairing.ROUTE_ENVIRONMENT and "as the Makefile exports them" in found.why
    other = {**exported, pairing.LIBRARY_VARIABLE: "/elsewhere/lib.so"}
    found = pairing.decide(other, tmp_path, distribution="imtypeset 0.4.1.192900")
    assert found.why.endswith("set by the caller")


SESSION_SEES = """
import json, os, sys
from pptmstr import pairing
from pptmstr.ui.typeset import gate
pairing.adopt()
gate.settle()
print(json.dumps({
    "route": pairing.adopt().route,
    "ahead": pairing.adopt().ahead,
    "environ": {
        k: os.environ.get(k) for k in ("IMTYPESET_LIBRARY", "IMTYPESET_PYTHON", "PYTHONPATH")
    },
}))
"""


@needs_a_distribution
def test_a_session_launched_under_deps_inherits_none_of_the_pair() -> None:
    """
    What pptmstr's entry point does before it launches anything (``adopt``,
    then ``gate.settle``): under ``PPTMSTR_TYPESET_DEPS=1`` with a whole pair,
    the pair is this process's, and a session's process, which the SDK starts
    with ``os.environ`` as it is then, is given neither variable and no
    ``PYTHONPATH``. ``make run`` gives pptmstr none of the three either
    (``tests/test_typeset_deps.py``).
    """
    pair = ROOT / pairing.DEPS_DIR / pairing.PAIR_DIR
    if not pair.is_dir() or pairing.pair_refusal(
        pair, pairing.read_ref(ROOT / pairing.IMTYPESET_REF), Path(sys.prefix)
    ):
        pytest.skip("this checkout has no whole pair for this environment in .deps")
    done = _run(script=SESSION_SEES, **ASKED)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen["route"] == pairing.ROUTE_CHECKOUT and seen["ahead"]
    assert seen["environ"] == {
        "IMTYPESET_LIBRARY": None,
        "IMTYPESET_PYTHON": None,
        # As it was given: pptmstr puts the pair's package first on its own
        # ``sys.path`` and never on ``PYTHONPATH``.
        "PYTHONPATH": child_environment().get("PYTHONPATH"),
    }
