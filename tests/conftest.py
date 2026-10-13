"""
Suite-wide fixtures. Each exists to keep the suite out of the operator's home: one
stops it writing a brief there, one stops it being handed the configuration directory
there, one stops it writing a board log there, and one stops it reading the credential
there and calling the API with it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from pptmstr import boardsync, brief, model_catalog, sessionfile, settings

# Read once, when the suite is collected, so it is the home this process was started
# with and not whatever a test has since put in its place.
_REAL_ROOT = brief.default_root()


@pytest.fixture(autouse=True)
def _no_brief_under_the_real_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """
    ``brief.default_root()`` never answers with the operator's real
    ``~/.claude/projects`` while a test runs.

    ``app._seed_brief`` writes a team launch's task under that root, so any test that
    drives ``app._launch`` with a template that has roles leaves a directory in the
    home of whoever ran the suite -- and passes. Redirecting it in each such test is
    a thing to remember, and the test that forgot wrote there on every run.

    **Only the real root is replaced.** The answer still comes from the function the
    application calls, so a test that moves the home itself (``Path.home`` patched to
    its ``tmp_path``) gets the root under the home it chose, which is what its
    assertions were written against. A test that replaces ``default_root`` outright
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
    answer: Callable[[], Path] = brief.default_root
    scratch: list[Path] = []

    def redirected() -> Path:
        root = answer()
        if root != _REAL_ROOT:
            return root
        if not scratch:
            scratch.append(tmp_path_factory.mktemp("brief-root"))
        return scratch[0]

    monkeypatch.setattr(brief, "default_root", redirected)


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
