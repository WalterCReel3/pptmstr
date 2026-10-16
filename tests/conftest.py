"""
What every test file gets, of two kinds: fixtures that keep the suite out of the
operator's home, and the typeset seam kept from leaking from one test into another.

Out of the home: one fixture stops the suite writing a brief there, one stops it
being handed the configuration directory there, one stops it writing a board log
there, and one stops it reading the credential there and calling the API with it.

The seam is module state -- one library context, one font registry, one operator
setting -- and one thing about it is a property of a *process* that a test must
not inherit: **the operator's settings.** A test that switches typesetting off,
a pane off, or gives a role another face or size, leaves the seam as it found
it: the next file's tests are not run with a switch somebody else threw.

A library context whose ImGui context is gone is not such a thing: the library
answers that it cannot draw in any context but the one its fonts were loaded
in, a destroyed one included (``typesetter.ready``), so a test file that
destroys its context without ``typeset.unload()`` leaves nothing the next one
can draw into.

**With the library and without.** imscribe finds an installed imtypeset by
itself, which is what installing pptmstr's requirements leaves, and
``IMSCRIBE_NO_TYPESET=1`` is the run without it: ``make test`` and ``make test
IMSCRIBE_NO_TYPESET=1``. A test that needs the library asks for ``seam``, which
runs where the library draws, skips with the reason where there is none to
find, and **fails** where one was found and cannot draw (``needs_the_library``).

It deliberately does not switch the seam off for tests that did not ask for it: the
files that draw panes against a mocked ``imgui`` do that themselves.

**A run says what it ran against, first** (``ran_against``): the imscribe that
is imported, and the library a process with this environment loads, by the
library's own answer -- its version, the route it was found by, the headers it
was compiled against -- or that there is none. "Green with the library" is a
claim about one library; one built against stand-in headers is accepted like
any other, and beside an installed imtypeset the two variables choose nothing.
Then the mcp and claude-agent-sdk the bus is served through: the SDK allows
either mcp major, and a fresh install resolves the newest.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from pptmstr import boardsync, brief, model_catalog, pairing, sessionfile, settings, theme, version
from pptmstr.ui import typeset
from pptmstr.ui.typeset import gate, views
from tests.mcp_wire import versions_line

BOLD = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"

# ``IMSCRIBE_NO_TYPESET=1`` is a run with no library whatever else is set: imscribe
# still loads a library ``IMTYPESET_LIBRARY`` names under its switch, so the two
# variables are taken out of this process's environment before anything asks it.
# Nothing has asked yet: the seam asks imscribe for the library on first use.
WITHHELD = (
    pairing.withhold(os.environ) if pairing.asks(os.environ, pairing.DISABLE_VARIABLE) else ()
)


_CHOOSING = (pairing.DISABLE_VARIABLE, pairing.DEPS_VARIABLE)


def child_environment(**given: str) -> dict[str, str]:
    """
    This process's environment with nothing in it that chooses the library,
    and then ``given``: for a test that starts an interpreter of its own.

    Out go the library's two variables, ``IMSCRIBE_NO_TYPESET``, pptmstr's own
    ``PPTMSTR_TYPESET_DEPS``, and the built pair's package directory where the
    Makefile put it on ``PYTHONPATH`` for that last one: a child that is told
    nothing finds what a process started by hand in this environment finds.
    """
    environ = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("IMTYPESET_") and name not in _CHOOSING
    }
    ahead = os.environ.get(pairing.PACKAGE_VARIABLE)
    if ahead and "PYTHONPATH" in environ:
        kept = [part for part in environ["PYTHONPATH"].split(os.pathsep) if part != ahead]
        if kept:
            environ["PYTHONPATH"] = os.pathsep.join(kept)
        else:
            del environ["PYTHONPATH"]
    return {**environ, **given}


def without_the_library(**given: str) -> dict[str, str]:
    """
    ``child_environment`` for a child that is to run with no library:
    ``IMSCRIBE_NO_TYPESET=1``, which is the one way to ask.
    """
    return child_environment(**{pairing.DISABLE_VARIABLE: "1"}, **given)


def refused_library(top: Path) -> dict[str, str]:
    """
    The variables under which a child finds a library and has it refused,
    whether or not an imtypeset is installed: a package of that name first on
    the path whose import raises, as imtypeset's own does for a library that
    is not there, and ``IMTYPESET_LIBRARY`` naming a file under ``top``.

    Naming a missing file is not enough beside an installed imtypeset: that
    package reads neither variable and loads the library it carries.
    """
    package = top / pairing.PACKAGE_DIR
    (package / "imtypeset").mkdir(parents=True)
    (package / "imtypeset" / "__init__.py").write_text(
        "import os\n"
        "raise ImportError('there is no library at ' + os.environ['IMTYPESET_LIBRARY'])\n",
        encoding="utf-8",
    )
    return {
        "PYTHONPATH": str(package),
        pairing.LIBRARY_VARIABLE: str(top / pairing.LIBRARY_NAME),
        pairing.PACKAGE_VARIABLE: str(package),
    }


def asked_of_a_new_process(environ: Mapping[str, str]) -> dict[str, Any]:
    """
    What imscribe says of the library in a process with ``environ``: its
    ``pairing()``, asked of a child (``python -m imscribe.typesetter``, one line
    of JSON), so that nothing is loaded here to find out. Empty where the child
    did not answer.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-P", "-m", "imscribe.typesetter"],
            env=dict(environ),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        said = json.loads(done.stdout.strip().splitlines()[-1])
    except Exception:
        return {}
    return said if isinstance(said, dict) else {}


def described(said: Mapping[str, Any], environ: Mapping[str, str]) -> list[str]:
    """
    The lines for one answer of ``asked_of_a_new_process``: the imscribe and the
    imtypeset sentences are ``pptmstr --version``'s, and a line of its own for
    each thing a green run would otherwise be misread over -- headers that are
    not the wheel's, variables that were set and not read. Never raises.
    """
    given = dict(environ)
    found = pairing.decide(
        given, pairing.checkout(), Path(sys.prefix), distribution=said.get("distribution")
    )
    lines = [
        version._said("imscribe", version._imscribe),
        version._said(
            "imtypeset",
            lambda: version._imtypeset(found, said, environ.get(pairing.LIBRARY_VARIABLE)),
        ),
    ]
    if found.given:
        # ``decide`` would hand the pair over, and nothing here does: a test run
        # is given it only by the Makefile, which exports it.
        lines.append(
            f"NOT HANDED OVER: {pairing.DEPS_VARIABLE} asks for {found.why.split(',')[0]}, "
            f"and only `make` and pptmstr's entry point hand it to a process; this run "
            f"draws through what the line above names"
        )
    kind = said.get("kind")
    if kind != gate.READY:
        lines.append(
            f"NO LIBRARY ({kind or 'imscribe did not answer'}): "
            + (
                "every test that draws through it is skipped"
                if kind == gate.NOT_INSTALLED
                else f"every test that draws through it FAILS ({said.get('reason')})"
            )
        )
        return lines
    headers = str(said.get("headers") or "")
    if not headers.startswith(pairing.FORK_HEADERS):
        lines.append(
            f"STAND-IN: the library was built against {headers or 'headers it does not state'}, "
            f"NOT the wheel's own Dear ImGui headers (the fork), and is accepted on "
            f"imtypeset's field probe alone"
        )
    if said.get("ignored_environment"):
        lines.append(
            f"IGNORED: {said['ignored_environment']} set and not read; the installed "
            f"{said.get('distribution')} is what every test draws through"
        )
    return lines


def ran_against(environ: Mapping[str, str]) -> list[str]:
    """
    What a test run with this environment draws through, a line a fact.
    """
    lines = [*described(asked_of_a_new_process(environ), environ), versions_line()]
    if WITHHELD:
        lines.append(
            f"WITHHELD: {' and '.join(WITHHELD)} set with {pairing.DISABLE_VARIABLE}, and taken "
            f"out of this run's environment: no library is loaded"
        )
    return lines


def pytest_report_header(config: pytest.Config) -> list[str]:
    return ran_against(os.environ)


def pytest_sessionstart(session: pytest.Session) -> None:
    """
    The same lines for a quiet run, which is what ``make test`` is: pytest
    prints no header under ``-q``.
    """
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None and not reporter.showheader:
        for line in ran_against(os.environ):
            reporter.write_line(line)


@pytest.fixture(autouse=True)
def _typeset_does_not_leak() -> Iterator[None]:
    state = typeset._STATE
    settings = (state.typeset, state.scale, state.family, dict(state.roles), state.panes_off)
    live = state.live_px
    yield
    if state.live_px != live:
        # A test told the seam the UI font was another height.
        state.live_px = live
        typeset.roles.moved()
    if (state.typeset, state.scale, state.family, state.roles, state.panes_off) != settings:
        typeset.configure(
            typeset=settings[0],
            scale=settings[1],
            proportional=settings[2],
            roles=settings[3],
            panes={pane: pane not in settings[4] for pane in typeset.Pane},
        )


def needs_the_library(reason: str | None) -> None:
    """
    Stop a test that draws through the library where the library cannot draw:
    skipped where there is none to find (not installed and none named, or
    ``IMSCRIBE_NO_TYPESET`` says not to look), failed where one was found.
    """
    kind, _ = gate.library()
    if kind != gate.NOT_INSTALLED:
        pytest.fail(f"a typesetting library was found and cannot draw ({kind}): {reason}")
    pytest.skip(f"needs the typesetting library: {reason}")


@pytest.fixture(scope="module")
def imgui() -> Iterator[Any]:
    """
    A bare ImGui context for a module, with Inconsolata as its first font.

    A frame needs a context and not a display, so nothing opens a window: the
    context has no backend and the atlas is built from the real font files.
    Destroyed afterwards, the library's context first.

    **No ini file.** ImGui's default is ``imgui.ini`` in the working directory,
    read on the first frame and written as windows move, so docking state (a
    tab bar's order) would carry from one module and one run to the next. A
    test that wants one sets its own path under ``tmp_path``.

    **The frame's lexing budget starts unspent** (``views._LEXING``): it is
    kept by frame number, and each module's context counts its frames from
    zero again, so a number the module before ended on would start with that
    frame's budget already spent.
    """
    bundle = pytest.importorskip("imgui_bundle")
    im = bundle.imgui
    context = im.create_context()
    io = im.get_io()
    io.set_ini_filename(None)
    views._LEXING.frame, views._LEXING.spent = -1, 0.0
    io.display_size = im.ImVec2(1200.0, 800.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= im.BackendFlags_.renderer_has_textures.value
    medium = Path(bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"
    io.fonts.add_font_from_file_ttf(str(medium), 16.0)
    yield im
    typeset.unload()
    im.destroy_context(context)


@pytest.fixture
def seam(imgui: Any) -> Iterator[Any]:
    """
    The seam with the library's fonts loaded and the settings at their defaults.

    **Skips, with the reason, where there is no library to find** (the
    library's kind is not-installed). **Where one was found and cannot draw,
    the test fails**: a library that is refused, or one some earlier test
    stopped for the rest of the process, would otherwise skip every test that
    draws and leave the run green. The fonts are loaded once a module and kept across its
    tests; a test that unloads them or loads its own has them put back here.
    """
    theme.set_theme("dark")
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})
    if not typeset.can_draw() and not typeset.load_fonts(BOLD):
        needs_the_library(typeset.unavailable_reason())
    yield imgui
    theme.set_theme("dark")
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})


@pytest.fixture
def columns_hold(seam: Any) -> Iterator[Any]:
    """
    The seam, held to the branch the product takes: the library that is running
    keeps a monospace column by pptmstr's own measurement (``gate.columns``),
    at the settings the product starts with, so a pane draws a diff with the
    library's diff view. Nothing is faked to get there.

    Fails, and does not skip, if the measurement says the columns do not hold:
    every test on this fixture would otherwise pass on the other branch.
    """
    from pptmstr.ui.typeset import diffs

    found = diffs.columns()
    assert found.holds, f"the library does not keep a column at the product's defaults: {found}"
    assert diffs.ADOPT == "measured" and diffs.adopted()
    yield seam


@pytest.fixture
def own_rows(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """
    The seam with every diff its pane's own rows: ``diffs.ADOPT`` is ``"never"``,
    the one override. The branch a pane takes wherever the library's diff view
    is not what draws a diff, forced, since the library as it is keeps its
    columns at every size the product sets.
    """
    from pptmstr.ui.typeset import diffs

    monkeypatch.setattr(diffs, "ADOPT", "never")
    assert not diffs.adopted()
    yield seam


NO_COLUMN = gate.Columns(
    False,
    "lays out a 7.626 px column where ImGui draws 8 px",
    cell=7.626,
    baked=8.0,
    size=16.0,
    glyphs=156,
    off=148,
    mishit=131,
)
"""
A measurement that says no, as ``gate.columns`` gave it at 16 px of the library
before imtypeset 0.4 with no cell passed: the font file's advance against the
whole pixel ImGui bakes.
"""


@pytest.fixture
def no_column(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """
    The seam with the measurement answering no (``NO_COLUMN``), stood in: the
    library as it is keeps its columns at every size the product sets, so no
    setting of it gives the other answer, and what a pane does with one is
    still the product's to hold. ``own_rows`` forces the same branch by the
    override; this is the branch taken on the measurement's word.
    """
    from pptmstr.ui.typeset import diffs

    monkeypatch.setattr(diffs, "columns", lambda: NO_COLUMN)
    assert diffs.ADOPT == "measured" and not diffs.adopted()
    yield seam


# Read once, when the suite is collected, so it is the home this process was started
# with and not whatever a test has since put in its place.
_REAL_ROOT = brief.default_root()
_REAL_LEGACY_ROOT = brief.legacy_root()


@pytest.fixture(autouse=True)
def _no_brief_under_the_real_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """
    Neither ``brief.default_root()`` nor ``brief.legacy_root()`` answers with the
    operator's real directory while a test runs: the configuration directory's
    ``briefs`` and ``~/.claude/projects``.

    ``app._seed_brief`` writes a team launch's task under the first, so any test that
    drives ``app._launch`` with a template that has roles leaves a directory in the
    home of whoever ran the suite -- and passes. Redirecting it in each such test is
    a thing to remember, and the test that forgot wrote there on every run. The
    configuration-directory fixture already redirects the first for an unmarked test;
    this one stays for a test marked ``real_config_dir``. The second is read for a
    resume and for a transcript, and a test reading the operator's real one passes or
    fails by what that operator happens to have.

    **Only the real root is replaced.** The answer still comes from the function the
    application calls, so a test that moves the home itself (``Path.home`` patched to
    its ``tmp_path``) gets the root under the home it chose, which is what its
    assertions were written against. A test that replaces either function outright
    replaces this too, which is the same redirection made by hand.

    **Autouse, because the hazard is a test that does not know it has it.** A fixture
    a test must ask for protects exactly the tests that already knew.

    A name bound by ``from pptmstr.brief import default_root`` is not reached: it
    holds the original function. ``tests/test_sandbox.py`` does that to compare the
    real root against the sandbox's credential paths, and it reads nothing and writes
    nothing there.

    The replacement directory is made on first use, so a test that never asks for the
    root costs no directory.
    """

    def redirect(name: str, real: Path) -> None:
        answer: Callable[[], Path] = getattr(brief, name)
        scratch: list[Path] = []

        def redirected() -> Path:
            root = answer()
            if root != real:
                return root
            if not scratch:
                scratch.append(tmp_path_factory.mktemp(name.replace("_", "-")))
            return scratch[0]

        monkeypatch.setattr(brief, name, redirected)

    redirect("default_root", _REAL_ROOT)
    redirect("legacy_root", _REAL_LEGACY_ROOT)


REAL_CONFIG_DIR = "real_config_dir"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{REAL_CONFIG_DIR}: settings.config_dir() is not redirected for this test, which"
        " asserts on the path the real rule gives and must write nothing under it",
    )


@pytest.fixture(autouse=True)
def _no_config_dir_under_the_real_home(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """
    ``settings.config_dir()`` never answers with a directory outside the suite's own
    temporary tree while a test runs -- the operator's real ``~/.config/pptmstr``
    above all.

    Everything pptmstr keeps is a path under that directory: the settings file, the
    session overlay, the board logs. Redirecting each derived root as it is added is
    a thing to remember; redirecting the one they are all derived from is not, so a
    root added later is covered on the day it is written.

    **That holds only for code that asks ``settings`` for it at the time.** A name
    bound by ``from pptmstr.settings import config_dir`` holds the original function
    and is not reached; ``tests/test_settings.py`` fails on any module of
    ``pptmstr/`` that binds it so.

    **An answer under the suite's temporary tree is left alone**, as the session
    fixture below does and for its reason: a test that points ``XDG_CONFIG_HOME`` or
    ``Path.home`` at its own ``tmp_path`` gets the directory under the one it chose,
    which is what it then asserts on. Every other answer is replaced, whatever it
    is, by a directory that is this test's alone.

    **A test of the real rule opts out by name**: ``@pytest.mark.real_config_dir``.
    The rule itself -- which variable, which fallback -- can only be asserted against
    the paths it really gives, and those are outside the tree. The marker is the
    whole of the opt-out; a test without it cannot get the real directory by setting
    a variable. A marked test is one that reads the path and writes nothing.

    The replacement is named and not made, as the function it stands in for makes
    nothing; its parent is made on first use, so a test that never asks costs no
    directory.
    """
    if request.node.get_closest_marker(REAL_CONFIG_DIR) is not None:
        return
    answer: Callable[[], Path] = settings.config_dir
    scratch: list[Path] = []

    def redirected() -> Path:
        directory = answer()
        if directory.is_relative_to(tmp_path_factory.getbasetemp()):
            return directory
        if not scratch:
            scratch.append(tmp_path_factory.mktemp("config-home") / settings.APP_NAME)
        return scratch[0]

    monkeypatch.setattr(settings, "config_dir", redirected)


@pytest.fixture(autouse=True)
def _no_session_directory_under_the_real_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """
    Neither root a board log is found under -- ``sessionfile.root()``, where one is
    read and appended, and ``boardsync.legacy_root()``, where one is read and moved
    from -- answers with a directory outside the suite's own temporary tree while a
    test runs: the operator's real ``~/.config/pptmstr/sessions`` and ``boards``
    above all.

    The fixture above already gives every unmarked test both roots under the tree,
    since each is derived from the configuration directory. This one stays for the
    test that fixture lets through: one marked ``real_config_dir``, where the
    configuration directory is the real one and a writer, or a move, must still
    not be.

    A ``BoardWriter`` built without a root appends under the first, and
    ``boardsync.migrate()`` called without one renames what is under the second into
    the first. Nothing ever deletes what a writer appends, and a rename of the
    operator's logs by a test is not undone by the test ending. A test that does
    either, or drives code that does, would pass. Where the home is read-only the
    same test fails instead, so the two machines would disagree about a test that
    is wrong on both.

    **A root under the suite's temporary tree is left alone**, for the reason the
    brief fixture above gives: a test that points ``XDG_CONFIG_HOME`` or
    ``Path.home`` at its own ``tmp_path`` gets the root under the directory it chose,
    which is what it then asserts on. So is a root a caller passed: only the answer
    for "no caller chose" is this fixture's.

    **Every other answer is replaced, whatever it is.** The real root is two
    directories, not one -- under ``XDG_CONFIG_HOME`` when that is set and under the
    home when it is not -- so comparing against the one the suite was started with
    would let the other through for a test that sets or unsets the variable.

    **The two replacements are ``sessions`` and ``boards`` in one directory**, as
    the real ones are, so a move between them is a rename within a filesystem and
    the link it leaves is the relative one it would be for the operator.

    ``sessionfile._under`` is the one place that module turns "no root given" into
    a path, so replacing it reaches ``root()``, ``session_dir``, ``board_dir`` and
    the file functions alike. A name bound by ``from pptmstr.boardsync import
    legacy_root`` is not reached; the modules' own calls are, since each looks the
    name up when it runs.

    The replacement directory is made on first use.
    """
    under: Callable[[Path | None], Path] = sessionfile._under
    legacy: Callable[[], Path] = boardsync.legacy_root
    scratch: list[Path] = []

    def inside(path: Path, name: str) -> Path:
        if path.is_relative_to(tmp_path_factory.getbasetemp()):
            return path
        if not scratch:
            scratch.append(tmp_path_factory.mktemp("session-root"))
        return scratch[0] / name

    def redirected_under(root: Path | None) -> Path:
        return under(root) if root is not None else inside(under(None), "sessions")

    def redirected_legacy() -> Path:
        return inside(legacy(), "boards")

    monkeypatch.setattr(sessionfile, "_under", redirected_under)
    monkeypatch.setattr(boardsync, "legacy_root", redirected_legacy)


@pytest.fixture(autouse=True)
def _no_probe_with_the_real_credential(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """
    ``model_catalog.probe_catalog()`` never reads the operator's
    ``~/.claude/.credentials.json`` while a test runs, so it never reaches the network.

    ``app._draw_launcher`` calls ``Catalog.request()`` on every frame that has a pool,
    and ``AppState`` builds a default ``Catalog``. A test that draws such a frame
    therefore starts a worker that reads the real token and opens a TLS connection to
    the API -- and passes, because the list in code is drawable without the answer. The
    connection outlives the test, and is reported as an unclosed socket by whichever
    later test happens to collect garbage with warnings turned into errors.

    **The path is replaced, not the probe.** ``probe_catalog`` still runs and takes its
    own no-credential branch, so a frame test sees what an install with no credential
    file sees. A test that passes ``path=`` is untouched.

    **Workers are joined before the path is put back.** ``probe_catalog`` reads the
    module's path when the worker gets to it, which can be after the test body has
    returned; without the join, a worker scheduled late would read the real one.
    ``monkeypatch`` is a dependency of this fixture, so this teardown runs first.

    The replacement names a file in a directory that is never made: absent is the whole
    of what it has to be, so no test pays for it.
    """
    absent = tmp_path_factory.getbasetemp() / "no-credential" / ".credentials.json"
    monkeypatch.setattr(model_catalog, "CREDENTIAL_PATH", str(absent))

    request = model_catalog.Catalog.request
    workers: list[threading.Thread] = []

    def tracked(self: model_catalog.Catalog) -> threading.Thread | None:
        worker = request(self)
        if worker is not None:
            workers.append(worker)
        return worker

    monkeypatch.setattr(model_catalog.Catalog, "request", tracked)
    yield
    for worker in workers:
        worker.join(timeout=10)
