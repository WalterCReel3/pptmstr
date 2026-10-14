"""
What every test file gets: the typeset seam cannot leak from one test into another.

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
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from pptmstr import pairing, theme, version
from pptmstr.ui import typeset
from pptmstr.ui.typeset import gate
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
    """
    bundle = pytest.importorskip("imgui_bundle")
    im = bundle.imgui
    context = im.create_context()
    io = im.get_io()
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
