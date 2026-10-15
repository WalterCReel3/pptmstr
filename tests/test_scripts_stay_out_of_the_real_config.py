"""
No script under ``scripts/`` starts the application on the operator's own
configuration.

``app.main`` reads ``settings.json`` and gives hello_imgui an ini under the user's
configuration directory whatever its arguments, and a start without ``--fake``
also moves every board log and recalls the fleet (``app._start``). A script that
calls either is therefore a script that reads the operator's configuration, and
may rearrange it, unless it moved the configuration first.

Two halves, because either alone passes a script that is not safe:

- **Read from the source**, for every script: each reference to ``app.main`` or
  ``app._start`` is preceded, unconditionally, by a call to the script's own
  ``_scratch_config``. This is the half a new script meets.
- **Run**, for each script that has one: ``_scratch_config`` is called and what
  the application would then resolve is read back from the application --
  ``settings.config_dir``, the session and board roots, hello_imgui's ini folder,
  the SDK's listing, the brief root. A function of that name that moved nothing
  satisfies the first half and fails here.

``--fake`` is not accepted as an arrangement on its own: ``main`` calls
``settings.read`` before it looks at the flag.

**What the walk cannot see**: an entry reached without naming it -- ``getattr(app,
"main")``, the module passed to something that calls it. No script does that.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from pptmstr import boardsync, brief, sessionfile, settings

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

ENTRIES = ("main", "_start")
ARRANGEMENT = "_scratch_config"

# The scripts known to start the application. The walk is held to finding at
# least these, since a walk that finds nothing passes every script.
STARTS_THE_APP = ("bench_idle.py", "screenshot.py", "verify_splash.py")


def _is_app(node: ast.expr, modules: set[str]) -> bool:
    """
    Whether ``node`` names the ``pptmstr.app`` module: a name bound to it, or the
    dotted ``pptmstr.app`` itself.
    """
    if isinstance(node, ast.Name):
        return node.id in modules
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "app"
        and isinstance(node.value, ast.Name)
        and node.value.id == "pptmstr"
    )


def _entries(tree: ast.Module) -> list[ast.AST]:
    """
    Every reference to ``app.main`` or ``app._start`` in ``tree``, called or not:
    a reference kept and called later is the same start.
    """
    modules: set[str] = set()
    direct: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pptmstr":
            modules |= {a.asname or a.name for a in node.names if a.name == "app"}
        elif isinstance(node, ast.ImportFrom) and node.module == "pptmstr.app":
            direct |= {a.asname or a.name for a in node.names if a.name in ENTRIES}
        elif isinstance(node, ast.Import):
            modules |= {a.asname for a in node.names if a.name == "pptmstr.app" and a.asname}
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ENTRIES:
            if _is_app(node.value, modules):
                found.append(node)
        elif isinstance(node, ast.Name) and node.id in direct:
            found.append(node)
    return found


def _is_arrangement(stmt: ast.stmt) -> bool:
    """
    Whether ``stmt`` is a bare call of the arrangement, or an assignment of one.
    """
    value = stmt.value if isinstance(stmt, ast.Expr | ast.Assign) else None
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == ARRANGEMENT
    )


def _arranged(scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef, entry: ast.AST) -> bool:
    """
    Whether the arrangement is made ahead of ``entry`` on every path to it: a
    statement of its own in the body of ``scope`` or of a function nested around
    the entry, before the statement the entry is in. One inside an ``if``, a
    ``try`` or a loop is not counted, since it is a call that may not happen.
    """
    seen = False
    for stmt in scope.body:
        if entry in set(ast.walk(stmt)):
            if seen:
                return True
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                return _arranged(stmt, entry)
            inner = [
                node
                for node in ast.walk(stmt)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and entry in set(ast.walk(node))
            ]
            # The outermost function around the entry inside this statement.
            return bool(inner) and _arranged(inner[0], entry)
        seen = seen or _is_arrangement(stmt)
    return False


def unarranged(source: str) -> list[int]:
    """
    The line of every start of the application in ``source`` that the
    arrangement does not precede. A script that never defines the arrangement
    has none of its starts arranged, whatever it calls.
    """
    tree = ast.parse(source)
    defined = any(isinstance(n, ast.FunctionDef) and n.name == ARRANGEMENT for n in tree.body)
    return sorted(
        getattr(entry, "lineno", 0)
        for entry in _entries(tree)
        if not (defined and _arranged(tree, entry))
    )


def _scripts() -> list[Path]:
    return sorted(SCRIPTS.glob("*.py"))


def test_the_walk_finds_the_scripts_known_to_start_the_application() -> None:
    starting = {p.name for p in _scripts() if _entries(ast.parse(p.read_text()))}
    assert starting >= set(STARTS_THE_APP)


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_no_script_starts_the_application_before_moving_its_configuration(script: Path) -> None:
    assert unarranged(script.read_text()) == []


_SAFE = """
def _scratch_config():
    pass

def main():
    _scratch_config()
    from pptmstr import app as the_app
    try:
        the_app.main(["--layout", "TRIAGE"])
    finally:
        pass
"""


def test_a_start_behind_its_arrangement_is_passed() -> None:
    assert unarranged(_SAFE) == []


@pytest.mark.parametrize(
    "source",
    [
        # No arrangement at all, which is what the two scripts were.
        "from pptmstr import app\napp.main([])\n",
        # --fake is not one: settings.json is read before the flag is.
        "from pptmstr import app\napp.main(['--fake'])\n",
        "def _scratch_config(): pass\nfrom pptmstr import app\napp.main([])\n_scratch_config()\n",
        "def _scratch_config(): pass\nfrom pptmstr import app\nif 0:\n    _scratch_config()\n"
        "app.main([])\n",
        # Arranged in one function and started from another.
        "def _scratch_config(): pass\ndef a():\n    _scratch_config()\ndef b():\n"
        "    from pptmstr import app\n    app._start(None, None)\n",
        "def _scratch_config(): pass\nimport pptmstr.app\npptmstr.app.main([])\n",
        "def _scratch_config(): pass\nimport pptmstr.app as a\nrun = a.main\n",
        "def _scratch_config(): pass\nfrom pptmstr.app import main as go\ngo([])\n",
        "def _scratch_config(): pass\nfrom pptmstr.app import _start\n_start(None, None)\n",
        # A function of another name is not the one the run half exercises.
        "def redirect(): pass\nfrom pptmstr import app\nredirect()\napp.main([])\n",
    ],
)
def test_a_start_the_arrangement_does_not_precede_is_found(source: str) -> None:
    assert unarranged(source) != []


def test_a_start_inside_a_nested_function_is_covered_by_the_function_around_it() -> None:
    source = (
        "def _scratch_config(): pass\n"
        "def main():\n"
        "    _scratch_config()\n"
        "    from pptmstr import app\n"
        "    def go():\n"
        "        return app.main([])\n"
        "    return go()\n"
    )
    assert unarranged(source) == []
    assert unarranged(source.replace("    _scratch_config()\n", "")) != []


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"pptmstr_test_script_{name[:-3]}", SCRIPTS / name
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # A dataclass with string annotations looks its module up by name while the
    # class is being made.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules[spec.name]
    return module


_SESSION = "22222222-2222-4222-8222-222222222222"
_TRANSCRIPT = (
    '{"type":"user","uuid":"11111111-1111-4111-8111-111111111111",'
    f'"sessionId":"{_SESSION}","cwd":"/tmp/x","timestamp":"2026-10-13T00:00:00Z",'
    '"message":{"role":"user","content":"hello there"}}\n'
)


@pytest.fixture
def operator(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """
    A stand-in for the operator's own environment, with both variables already
    set: the arrangement has to replace a set one too. Temporary directories are
    made under ``tmp_path``, and everything the arrangement changes in this
    process is put back afterwards.
    """
    own = tmp_path / "operator"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(own / "config"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(own / "claude"))
    monkeypatch.setattr(brief, "default_root", lambda: own / "claude" / "projects")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return own


@pytest.mark.parametrize("name", STARTS_THE_APP)
def test_the_arrangement_moves_everything_the_application_resolves(
    name: str, operator: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from claude_agent_sdk import list_sessions
    from imgui_bundle import hello_imgui

    scratch = _load(name)._scratch_config()

    said = capsys.readouterr().out.splitlines()
    assert str(scratch) in said[0]
    assert scratch.is_dir() and not scratch.is_relative_to(operator)
    for resolved in (
        settings.config_dir(),
        sessionfile.root(),
        boardsync.legacy_root(),
        brief.default_root(),
        Path(hello_imgui.ini_folder_location(hello_imgui.IniFolderType.app_user_config_folder)),
    ):
        assert resolved.is_relative_to(scratch), resolved

    # The SDK lists from where the arrangement pointed it, and nowhere else.
    assert list_sessions() == []
    project = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "-tmp-x"
    assert project.is_relative_to(scratch)
    project.mkdir()
    (project / f"{_SESSION}.jsonl").write_text(_TRANSCRIPT)
    assert [row.session_id for row in list_sessions()] == [_SESSION]


def test_two_runs_do_not_share_a_configuration(operator: Path) -> None:
    script = _load("bench_idle.py")
    assert script._scratch_config() != script._scratch_config()


def test_a_capture_of_a_real_session_keeps_the_cli_directory_and_says_so_first(
    operator: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    A session cannot start without the credentials under the CLI's directory, so
    that one run leaves it where it is -- and its first line of output says it is
    acting on the operator's, before anything has been started.
    """
    scratch = _load("screenshot.py")._scratch_config(real_session=True)

    first = capsys.readouterr().out.splitlines()[0]
    assert "REAL SESSION" in first and "credentials" in first
    assert os.environ["CLAUDE_CONFIG_DIR"] == str(operator / "claude")
    assert brief.default_root() == operator / "claude" / "projects"
    assert settings.config_dir().is_relative_to(scratch)
    assert sessionfile.root().is_relative_to(scratch)


@pytest.mark.parametrize(
    ("argv", "starts"),
    [
        (["--fake"], False),
        (["--layout", "FOCUS"], False),
        (["--task", "do it"], True),
        (["--task=do it"], True),
        # main reads --fake and --task separately, so this one launches too.
        (["--fake", "--task", "do it"], True),
    ],
)
def test_a_capture_is_of_a_real_session_exactly_when_it_names_a_task(
    argv: list[str], starts: bool
) -> None:
    assert _load("screenshot.py")._starts_a_session(argv) is starts


def test_the_capture_decides_from_the_arguments_it_hands_the_application() -> None:
    """
    ``_starts_a_session`` is worth nothing unless ``main`` asks it about the same
    list ``app.main`` is given, and passes the answer to the arrangement.
    """
    source = (SCRIPTS / "screenshot.py").read_text()
    assert "_scratch_config(real_session=_starts_a_session(argv))" in source
    assert "pptmstr_app.main(argv)" in source


def _recipes() -> Iterator[tuple[str, str]]:
    """
    Each Makefile target with a recipe, and that recipe.
    """
    target = ""
    lines: list[str] = []
    for line in (ROOT / "Makefile").read_text().splitlines():
        head = re.match(r"^([A-Za-z_-]+):", line)
        if head:
            if target:
                yield target, "\n".join(lines)
            target, lines = head.group(1), []
        elif line.startswith("\t"):
            lines.append(line)
    if target:
        yield target, "\n".join(lines)


def test_the_makefile_starts_the_application_only_through_a_checked_script() -> None:
    """
    ``bench`` and ``shot`` are safe because the scripts they run are. A target that
    ran the application some other way would be outside everything above, so the
    only one allowed to is ``run``, which is the operator starting their own.
    """
    recipes = dict(_recipes())
    assert "scripts/bench_idle.py" in recipes["bench"]
    assert "scripts/screenshot.py" in recipes["shot"]
    direct = {target for target, recipe in recipes.items() if "-m pptmstr" in recipe}
    assert direct == {"run"}
