"""
Settings: XDG location, atomic writes, and tolerance of a bad file.

The load path is the interesting one. This file is hand-editable and survives
version changes, so it is an untrusted input to a program that must still start.
"""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import pptmstr
from pptmstr import sessions as sessions_mod
from pptmstr import settings as settings_mod
from pptmstr.settings import (
    LEGACY_FACES,
    BoardLogUnread,
    Loaded,
    RoleChoice,
    Settings,
    Unsaved,
    load,
    read,
    save,
)


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings(theme="light", concurrency_cap=8, fps_idle=15.0)
    save(original, path, unread=None)
    assert load(path) == original


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json") == Settings()


def test_corrupt_file_gives_defaults_and_records_no_board(tmp_path: Path) -> None:
    """
    Losing a theme preference is a nuisance; refusing to launch over one is a bug.
    The board log is the one key whose default cannot stand in for a file that
    was there and could not be read: see the tests of ``read`` below.
    """
    path = tmp_path / "settings.json"
    path.write_text("{not json at all")
    assert load(path) == Settings(board_log=False)


def test_non_object_json_gives_defaults_and_records_no_board(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]")
    assert load(path) == Settings(board_log=False)


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    """A file written by a later version must still load here."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "ghost", "some_future_key": {"a": 1}}))
    assert load(path).theme == "ghost"


def test_wrong_types_fall_back_per_field(tmp_path: Path) -> None:
    """
    A string in fps_idle would survive construction and fail much later, inside a
    comparison in the frame loop -- a long way from the file that caused it.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "light", "fps_idle": "fast"}))
    got = load(path)
    assert got.theme == "light"
    assert got.fps_idle == Settings().fps_idle


def test_wrap_inputs_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save(Settings(wrap_inputs=False), path, unread=None)
    assert load(path).wrap_inputs is False


def test_wrap_inputs_rejects_non_bool(tmp_path: Path) -> None:
    """
    ``bool`` is an ``int``, so the bool guard has to be tried before the numeric one
    or a JSON ``1`` would load as True and a ``2`` would load as an int in a field
    the UI passes straight into a flag mask.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"wrap_inputs": 1}))
    assert load(path).wrap_inputs is Settings().wrap_inputs


def test_subagent_cap_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save(Settings(subagent_cap=7), path, unread=None)
    assert load(path).subagent_cap == 7


def test_the_subagent_cap_default_matches_the_drivers_own() -> None:
    """
    Two defaults for one number, in modules that must not import each other: a
    session built without a cap has to end up at the same ceiling as one built from
    a settings file the operator never edited. Deliberate duplication, pinned.
    """
    from pptmstr.driver import DEFAULT_SUBAGENT_CAP

    assert Settings().subagent_cap == DEFAULT_SUBAGENT_CAP


def test_the_two_caps_are_separate_settings(tmp_path: Path) -> None:
    """
    Sub-agents share their parent's CLI process, so their ceiling is API concurrency
    rather than the RAM one concurrency_cap bounds. Sizing one from the other would
    make a machine with more memory run more agents per session, which is a category
    error, not a tuning decision.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"concurrency_cap": 12}))
    assert load(path).subagent_cap == Settings().subagent_cap


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    """
    Written to a temp file in the same directory and renamed, so an interrupted
    write cannot leave a truncated config behind.
    """
    path = tmp_path / "settings.json"
    save(Settings(theme="ghost"), path, unread=None)
    save(Settings(theme="dark"), path, unread=None)
    assert load(path).theme == "dark"
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_save_creates_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "settings.json"
    save(Settings(theme="light"), path, unread=None)
    assert load(path).theme == "light"


def test_unwritable_target_does_not_raise(tmp_path: Path) -> None:
    """Called from the UI thread on a theme change; must not take down a frame."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    save(Settings(), blocked / "settings.json", unread=None)


# The three below assert on the paths the real rule gives, which are outside the
# suite's tree by construction, so each opts out of the suite's redirect by name.
# They read a path and touch nothing at it.


@pytest.mark.real_config_dir
def test_config_dir_honours_absolute_xdg(monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
    assert settings_mod.config_dir() == Path("/custom/xdg/pptmstr")


@pytest.mark.real_config_dir
def test_config_dir_ignores_relative_xdg(monkeypatch) -> None:
    """
    The spec requires a relative XDG_CONFIG_HOME to be treated as unset. Honouring
    it anyway would scatter config into whatever the working directory happened to be.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    assert settings_mod.config_dir() == Path.home() / ".config" / "pptmstr"


@pytest.mark.real_config_dir
def test_config_dir_falls_back_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert settings_mod.config_dir() == Path.home() / ".config" / "pptmstr"


def test_text_is_typeset_in_proportional_prose_unless_the_operator_says_otherwise() -> None:
    """
    The defaults are the experiment's "on" arm: a fresh install shows the new
    renderer in every pane, and the old one is one menu entry away.
    """
    from pptmstr.ui import typeset

    assert Settings().typeset is True
    assert Settings().typeset_scale == 1.0
    # The default is spelled in `settings` so that module stays free of the UI.
    assert Settings().typeset_family == typeset.DEFAULT_PROPORTIONAL
    assert Settings().typeset_roles == () and Settings().typeset_panes == ()
    assert typeset.DEFAULTS[typeset.Role.PROSE].slot is typeset.Slot.PROPORTIONAL
    assert typeset.panes_from(Settings().typeset_panes) == {}, "a pane not named is on"


def test_the_text_switches_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    chosen = (
        Settings(typeset=False, typeset_family="droid", typeset_scale=1.25)
        .with_role("prose", step=2)
        .with_role("heading", slot="monospace")
        .with_pane("context", False)
        .with_pane("board", True)
    )
    save(chosen, path, unread=None)
    assert load(path) == chosen
    # By name in the file, as a person editing it would write them.
    written = json.loads(path.read_text())
    assert written["typeset_roles"] == {"heading": {"slot": "monospace"}, "prose": {"step": 2}}
    assert written["typeset_panes"] == {"board": True, "context": False}


def test_a_role_keeps_what_was_chosen_for_it_when_one_half_changes() -> None:
    """
    The size and the family of a role are chosen in two places in the window. A
    click on one must not put the other back to its default.
    """
    chosen = Settings().with_role("prose", slot="monospace").with_role("prose", step=2)
    assert chosen.typeset_roles == (RoleChoice("prose", "monospace", 2),)
    chosen = chosen.with_role("prose", slot="proportional").with_role("label", step=-1)
    assert chosen.typeset_roles == (
        RoleChoice("label", None, -1),
        RoleChoice("prose", "proportional", 2),
    )
    assert chosen.with_pane("brief", False).with_pane("brief", True).typeset_panes == (
        ("brief", True),
    )


@pytest.mark.parametrize("name", list(LEGACY_FACES))
def test_a_saved_face_preset_is_read_as_the_role_choices_it_stood_for(
    tmp_path: Path, name: str
) -> None:
    """
    ``typeset_faces`` named one of four presets before text was configured by role.
    An operator who had chosen one must find the same text after an upgrade, and
    the seam must mean the same by the name: the table is spelled in both.
    """
    from pptmstr.ui import typeset

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"typeset": True, "typeset_faces": name, "theme": "light"}))
    got = load(path)
    family, roles = LEGACY_FACES[name]
    assert (got.typeset_family, got.typeset_roles, got.theme) == (family, roles, "light")

    assert set(LEGACY_FACES) == set(typeset._LEGACY)
    seam_family, seam_roles = typeset.legacy(name)
    assert seam_family == family
    read = typeset.roles_from((each.role, each.slot, each.step) for each in roles)
    assert typeset.checked(read) == typeset.checked(seam_roles)

    save(got, path, unread=None)
    assert "typeset_faces" not in json.loads(path.read_text())
    assert load(path) == got


def test_the_presets_meant_what_the_migration_says() -> None:
    assert {each.role for each in LEGACY_FACES["mono"][1]} == {"prose", "heading", "table", "label"}
    assert all(each.slot == "monospace" for each in LEGACY_FACES["mono"][1])
    assert LEGACY_FACES["roboto-large"][1] == (RoleChoice("prose", "proportional", 2),)
    assert LEGACY_FACES["droid"] == ("droid", ())
    assert LEGACY_FACES["roboto"] == ("roboto", ())


def test_a_file_saved_with_the_monospace_switch_keeps_monospace_prose(tmp_path: Path) -> None:
    """
    ``typeset_mono`` is the boolean the face presets replaced. An operator who had
    chosen monospace prose must not find proportional prose after an upgrade.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"typeset": True, "typeset_mono": True, "theme": "light"}))
    got = load(path)
    assert got.typeset_roles == LEGACY_FACES["mono"][1] and got.theme == "light"

    save(got, path, unread=None)
    assert "typeset_mono" not in json.loads(path.read_text())
    assert load(path).typeset_roles == LEGACY_FACES["mono"][1]


@pytest.mark.parametrize(
    ("saved", "faces"),
    [
        ({"typeset_mono": False}, "roboto"),
        ({"typeset_mono": 1}, "roboto"),
        ({"typeset_mono": "true"}, "roboto"),
        ({"typeset_mono": True, "typeset_faces": "droid"}, "droid"),
        ({"typeset_mono": True, "typeset_faces": 7}, "mono"),
        ({"typeset_faces": "from-a-later-build"}, "roboto"),
        # What the file says about roles is newer than a preset's name and wins.
        ({"typeset_faces": "mono", "typeset_roles": {}}, "roboto"),
        ({"typeset_faces": "droid", "typeset_family": "roboto"}, "roboto"),
    ],
)
def test_the_old_keys_are_read_only_when_the_file_says_nothing_newer(
    tmp_path: Path, saved: dict[str, object], faces: str
) -> None:
    """
    A named preset wins over the older switch, a preset that is not a string is no
    preset, and a name that was never a preset is the defaults.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(saved))
    got = load(path)
    assert (got.typeset_family, got.typeset_roles) == LEGACY_FACES[faces]


def test_hand_edited_role_choices_and_pane_switches_lose_only_what_is_malformed(
    tmp_path: Path,
) -> None:
    """
    ``"false"`` is truthy and ``true`` is an integer to Python. A pane switched off
    by a string would be on, and a step of ``true`` would be one pixel.
    """
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "typeset_roles": {
                    "prose": {"slot": "monospace", "step": True},
                    "heading": {"slot": 7, "step": 1},
                    "table": "monospace",
                    "from-a-later-build": {"slot": "serif"},
                },
                "typeset_panes": {"context": False, "board": "false", "brief": 0},
            }
        )
    )
    got = load(path)
    assert got.typeset_roles == (
        RoleChoice("from-a-later-build", "serif", None),
        RoleChoice("heading", None, 1),
        RoleChoice("prose", "monospace", None),
    )
    assert got.typeset_panes == (("context", False),)

    path.write_text(json.dumps({"typeset_roles": ["prose"], "typeset_panes": "off"}))
    assert load(path) == Settings()


def test_the_retired_context_mode_survives_a_restart_as_it_was(tmp_path: Path) -> None:
    """
    Nothing reads it any more, and a file that names a mode -- one this build
    still has, or ``rich`` -- loads and saves with it unchanged. A value that is
    not a string is not a name.
    """
    path = tmp_path / "settings.json"
    assert Settings().context_mode == ""
    for named in ("typeset", "rich", "raw"):
        save(Settings(context_mode=named), path, unread=None)
        assert json.loads(path.read_text())["context_mode"] == named
        assert load(path).context_mode == named

    path.write_text(json.dumps({"context_mode": 3, "theme": "light"}))
    assert load(path) == Settings(theme="light")


def test_reasoning_is_shown_by_default_and_a_choice_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert Settings().show_reasoning is True
    save(Settings(show_reasoning=False), path, unread=None)
    assert json.loads(path.read_text())["show_reasoning"] is False
    assert load(path).show_reasoning is False

    path.write_text(json.dumps({"show_reasoning": "no", "theme": "light"}))
    assert load(path) == Settings(theme="light"), "not a boolean is the default"


def test_a_text_switch_that_is_not_a_boolean_falls_back_to_its_default(tmp_path: Path) -> None:
    """
    ``"false"`` is truthy. Accepted as written it would turn the renderer *on* for
    someone who hand-edited the file to turn it off.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"typeset": "false", "typeset_scale": "1.25"}))
    got = load(path)
    assert got.typeset is Settings().typeset
    assert got.typeset_scale == Settings().typeset_scale


# -- the suite stays in its own tree ---------------------------------------------------

_PACKAGE = Path(pptmstr.__file__).parent

# The settings of the variable a test can make, each of which the real rule answers
# with a directory outside the suite's tree: unset (the home), absolute, and relative
# (the home again).
_CONFIG_HOMES = [None, "/nonexistent/config", "relative/config"]


def _set_config_home(monkeypatch: pytest.MonkeyPatch, config_home: str | None) -> None:
    if config_home is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", config_home)


def _modules() -> Iterator[tuple[str, ast.Module]]:
    """
    Every module of ``pptmstr/`` as its dotted name and its tree.
    """
    for path in sorted(_PACKAGE.rglob("*.py")):
        relative = path.relative_to(_PACKAGE).with_suffix("")
        parts = [part for part in relative.parts if part != "__init__"]
        yield ".".join(["pptmstr", *parts]), ast.parse(path.read_text(encoding="utf-8"))


def _is_config_dir_call(node: ast.AST, module: str) -> bool:
    """
    Whether ``node`` is a call that asks for the config directory at the time it
    runs: ``<anything>.config_dir()``, or the bare name inside ``settings`` itself,
    where it is a module global and a replacement of it is what the call finds.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr == "config_dir"
    return isinstance(func, ast.Name) and func.id == "config_dir" and module == "pptmstr.settings"


def _bindings_of_config_dir(module: str, tree: ast.Module) -> list[str]:
    """
    Every place ``tree`` holds ``config_dir`` itself, or its answer, from before a
    test could replace it. Empty for a module that only ever calls through.
    """
    called = {
        id(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_config_dir_call(node, module)
    }
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "settings":
            for alias in node.names:
                if alias.name in ("config_dir", "*"):
                    found.append(f"{module}:{node.lineno}: from-import of {alias.name}")
        elif isinstance(node, ast.Name) and node.id == "config_dir" and id(node) not in called:
            found.append(f"{module}:{node.lineno}: the name, not a call of it")
        elif isinstance(node, ast.Attribute) and node.attr == "config_dir":
            if id(node) not in called:
                found.append(f"{module}:{node.lineno}: the attribute, not a call of it")

    # A call that runs when the module is imported is an answer held from then on:
    # at module or class level, in a decorator, or as a parameter's default.
    def at_import(node: ast.AST) -> Iterator[ast.AST]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = node.args
            held: list[ast.AST] = [*arguments.defaults, *(d for d in arguments.kw_defaults if d)]
            if not isinstance(node, ast.Lambda):
                held += node.decorator_list
            for part in held:
                yield from at_import(part)
            return
        yield node
        for child in ast.iter_child_nodes(node):
            yield from at_import(child)

    for node in at_import(tree):
        if _is_config_dir_call(node, module):
            found.append(
                f"{module}:{getattr(node, 'lineno', 0)}: called when the module is imported"
            )
    return found


def _parsed(module: str, source: str) -> list[str]:
    return _bindings_of_config_dir(module, ast.parse(source))


def test_no_module_holds_config_dir_from_before_a_test_could_replace_it() -> None:
    """
    The suite's redirect replaces ``settings.config_dir``. A module that bound the
    function by name at import, or called it at import and kept the answer, is one
    the redirect does not reach -- and its paths are the operator's real ones in
    every test.
    """
    found = [line for module, tree in _modules() for line in _bindings_of_config_dir(module, tree)]

    assert found == []


@pytest.mark.parametrize(
    "source",
    [
        "from pptmstr.settings import config_dir\n",
        "from .settings import config_dir\n",
        "from pptmstr.settings import config_dir as where\n",
        "from pptmstr.settings import APP_NAME, config_dir\n",
        "from .settings import *\n",
        "from pptmstr import settings\nwhere = settings.config_dir\n",
        "from pptmstr import settings\nROOT = settings.config_dir() / 'boards'\n",
        "from pptmstr import settings\ndef f(root=settings.config_dir()):\n    return root\n",
        "from pptmstr import settings\nclass W:\n    root = settings.config_dir()\n",
    ],
)
def test_the_walk_finds_each_way_of_holding_it(source: str) -> None:
    assert _parsed("pptmstr.elsewhere", source) != []


def test_the_walk_passes_a_call_made_when_it_is_needed() -> None:
    source = "from pptmstr import settings\ndef f():\n    return settings.config_dir() / 'x'\n"

    assert _parsed("pptmstr.elsewhere", source) == []
    assert (
        _parsed("pptmstr.settings", "def settings_path():\n    return config_dir() / 'x'\n") == []
    )
    # The bare name is a module global only in the module that defines it.
    assert _parsed("pptmstr.elsewhere", "def f():\n    return config_dir()\n") != []


# A deriver that cannot be called with nothing, and how it is asked for the path it
# gives when no caller chose one. ``_derivers`` fails on one that is not here.
_ASKED_WITH: dict[str, tuple[object, ...]] = {
    "pptmstr.sessionfile._under": (None,),
}


def _derivers() -> dict[str, Callable[[], Path]]:
    """
    Every function of ``pptmstr/`` whose body asks for the config directory, by
    dotted name -- found by reading, so one added later is here without being listed.
    """
    found: dict[str, Callable[[], Path]] = {}
    for module, tree in _modules():
        nested = {
            id(inner)
            for outer in ast.walk(tree)
            if isinstance(outer, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for inner in ast.walk(outer)
            if inner is not outer
        }
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_config_dir_call(inner, module) for inner in ast.walk(node)):
                continue
            name = f"{module}.{node.name}"
            # Reached by attribute, which a method or a closure is not.
            assert id(node) not in nested, f"{name} is not a module-level function"
            function = getattr(importlib.import_module(module), node.name)
            required = [
                parameter.name
                for parameter in inspect.signature(function).parameters.values()
                if parameter.default is inspect.Parameter.empty
                and parameter.kind
                not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            ]
            given = _ASKED_WITH.get(name, ())
            assert len(given) == len(required), (
                f"{name} takes {required}: say in _ASKED_WITH what it is called with"
                " when no caller chose the path"
            )
            found[name] = functools.partial(function, *given)
    assert set(_ASKED_WITH) <= set(found), "_ASKED_WITH names a function that is not a deriver"
    return found


def test_the_derivers_found_include_the_ones_known() -> None:
    """
    The property below is over what the walk finds, so a walk that finds nothing
    would pass it.
    """
    assert {
        "pptmstr.settings.settings_path",
        "pptmstr.sessions.overlay_path",
        "pptmstr.boardsync.legacy_root",
        "pptmstr.sessionfile._under",
    } <= set(_derivers())


@pytest.mark.parametrize("config_home", _CONFIG_HOMES)
def test_every_path_derived_from_the_config_directory_is_inside_the_suite(
    config_home: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """
    Whatever a test does to the variable, short of the marker: the directory itself
    and everything ``pptmstr/`` derives from it.
    """
    _set_config_home(monkeypatch, config_home)
    tree = tmp_path_factory.getbasetemp()

    paths = {"pptmstr.settings.config_dir": settings_mod.config_dir()}
    paths |= {name: function() for name, function in _derivers().items()}

    outside = {name: path for name, path in paths.items() if not path.is_relative_to(tree)}
    assert outside == {}
    # One directory for the test, so what one function writes another reads.
    directory = paths["pptmstr.settings.config_dir"]
    assert paths["pptmstr.settings.settings_path"] == directory / "settings.json"
    assert paths["pptmstr.sessions.overlay_path"] == directory / "sessions.json"
    assert paths["pptmstr.boardsync.legacy_root"] == directory / "boards"
    assert paths["pptmstr.sessionfile._under"] == directory / "sessions"
    assert importlib.import_module("pptmstr.sessionfile").root() == directory / "sessions"
    assert directory.name == settings_mod.APP_NAME and not directory.exists()


@pytest.mark.parametrize("config_home", _CONFIG_HOMES)
def test_a_save_with_no_path_lands_inside_the_suite(
    config_home: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """
    The two writers that default their target to the config directory, called the
    way the application calls them.
    """
    _set_config_home(monkeypatch, config_home)
    tree = tmp_path_factory.getbasetemp()

    assert save(Settings(theme="light"), unread=None) is None
    sessions_mod.save_overlay(sessions_mod.set_bookmark({}, "abc", True))

    directory = settings_mod.config_dir()
    assert directory.is_relative_to(tree)
    assert sorted(p.name for p in directory.iterdir()) == ["sessions.json", "settings.json"]
    assert load().theme == "light"
    assert sessions_mod.load_overlay()["abc"].bookmarked


def test_a_config_directory_a_test_chose_inside_the_suite_is_the_one_it_gets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert settings_mod.config_dir() == tmp_path / "xdg" / "pptmstr"

    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    assert settings_mod.config_dir() == tmp_path / "home" / ".config" / "pptmstr"


def test_two_tests_do_not_share_a_config_directory_first() -> None:
    save(Settings(theme="light"), unread=None)
    assert load().theme == "light"


def test_two_tests_do_not_share_a_config_directory_second() -> None:
    """
    Meaningful beside the test above in either order: one of them runs second, and
    finds nothing the other wrote.
    """
    assert not settings_mod.config_dir().exists()
    assert load() == Settings()


@pytest.mark.real_config_dir
def test_the_marker_is_the_whole_of_the_opt_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """
    The other half of the property: the marked test really is unredirected, so the
    tests of the real rule above assert on the rule and not on a stand-in.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent/config")

    assert settings_mod.config_dir() == Path("/nonexistent/config/pptmstr")
    assert not settings_mod.config_dir().is_relative_to(tmp_path_factory.getbasetemp())


# -- hiding ended sub-agents -----------------------------------------------------------


def test_ended_subagents_are_hidden_unless_the_file_says_otherwise(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    assert Settings().hide_ended_subagents is True
    assert load(path).hide_ended_subagents is True

    path.write_text(json.dumps({"theme": "light"}))
    assert load(path).hide_ended_subagents is True

    path.write_text(json.dumps({"hide_ended_subagents": False}))
    assert load(path).hide_ended_subagents is False

    save(Settings(hide_ended_subagents=False), path, unread=None)
    assert load(path).hide_ended_subagents is False


@pytest.mark.parametrize("value", ["false", "no", 0, 1, None, [], {}])
def test_a_malformed_hide_ended_subagents_is_the_default_and_nothing_else_is_refused(
    tmp_path: Path, value: object
) -> None:
    """
    Not ``board_log``'s rule. That key fails closed because its default writes
    something that is never deleted; this one changes what is drawn, so a value
    that cannot be read costs a preference and the default stands in for it --
    with the rest of the file read, recording left on, and saving not refused.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"hide_ended_subagents": value, "theme": "light"}))

    loaded = read(path)

    assert loaded == Loaded(Settings(theme="light"), None)
    assert loaded.settings.hide_ended_subagents is True
    assert loaded.settings.board_log is True
    assert save(loaded.settings, path, unread=loaded.board_log_unread) is None


@pytest.mark.parametrize(
    "key", ["hide-ended-subagents", "Hide_Ended_Subagents", "hideendedsubagents"]
)
def test_a_misspelled_hide_ended_subagents_is_an_unknown_key_and_no_more(
    tmp_path: Path, key: str
) -> None:
    """
    The look-alike rule is ``board_log``'s alone: a near miss of this key is ignored
    like any key this build does not know.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({key: False}))

    assert read(path) == Loaded(Settings(), None)


def test_the_board_log_is_on_unless_the_file_says_otherwise(tmp_path: Path) -> None:
    """
    A file written before the key existed loads with the default, and the
    default is on: the first launch after an upgrade records team boards.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "light"}))
    assert load(path).board_log is True
    assert Settings().board_log is True


def test_the_board_log_switch_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save(Settings(board_log=False), path, unread=None)
    assert read(path) == Loaded(Settings(board_log=False), None)
    save(Settings(board_log=True), path, unread=None)
    assert read(path) == Loaded(Settings(board_log=True), None)


# -- an operator who tried to switch recording off is never recorded ---------------
#
# The switch has no menu entry: it is edited by hand, in a file whose every other
# key falls back to its default when it cannot be read. For this key the default
# is ON, so the same tolerance turns a slip of the hand into a board written to
# disk for somebody who asked for none.


@pytest.mark.parametrize(
    "text",
    [
        '{"board_log": false,}',  # the trailing comma a hand edit leaves
        '{"board_log": false',
        "",
        "[1, 2, 3]",
        '"board_log: false"',
        "null",
    ],
)
def test_a_settings_file_that_cannot_be_read_records_no_board_and_says_why(
    tmp_path: Path, text: str
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(text)

    got = read(path)

    assert got.settings.board_log is False
    assert got.board_log_unread is BoardLogUnread.FILE
    assert load(path).board_log is False
    # Every other key keeps the tolerance it had.
    assert got.settings == Settings(board_log=False)


def test_a_settings_file_that_is_not_text_records_no_board(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b'{"board_log": false}\xff\xfe')
    assert read(path) == Loaded(Settings(board_log=False), BoardLogUnread.FILE)


def test_a_settings_path_that_is_there_and_cannot_be_opened_records_no_board(
    tmp_path: Path,
) -> None:
    """
    A directory where the file should be is the one unopenable thing a test can
    build as any user: it exists, so "no file" is not what was found.
    """
    path = tmp_path / "settings.json"
    path.mkdir()
    assert read(path) == Loaded(Settings(board_log=False), BoardLogUnread.FILE)


@pytest.mark.parametrize("value", ["false", "true", "off", 0, 1, None, 0.0, [], {}, [False]])
def test_a_board_log_that_is_not_true_or_false_records_no_board_and_says_why(
    tmp_path: Path, value: object
) -> None:
    """
    ``"false"`` is the spelling this exists for, and ``"true"`` is refused with
    it: guessing which words mean on is how a string comes to switch a log on.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "light", "board_log": value}))

    got = read(path)

    assert got.settings.board_log is False
    assert got.board_log_unread is BoardLogUnread.VALUE
    # The rest of a file that parsed is still taken.
    assert got.settings.theme == "light"
    assert load(path).board_log is False


def test_only_an_absent_file_or_an_absent_key_means_the_default(tmp_path: Path) -> None:
    absent = tmp_path / "nope.json"
    under_a_file = tmp_path / "a-file" / "settings.json"
    (tmp_path / "a-file").write_text("x")
    keyless = tmp_path / "settings.json"
    keyless.write_text(json.dumps({"theme": "light", "fps_idle": "fast"}))

    for path in (absent, under_a_file, keyless):
        got = read(path)
        assert got.settings.board_log is True, path
        assert got.board_log_unread is None, path


def test_a_stated_board_log_is_taken_as_stated(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    for stated in (True, False):
        path.write_text(json.dumps({"board_log": stated}))
        assert read(path) == Loaded(Settings(board_log=stated), None)


def test_each_reason_has_a_sentence_for_the_pane_that_names_the_cause() -> None:
    """
    What ``app.board_logging`` hands the board pane as the reason nothing is
    recorded. Pinned because the two are different mistakes with different
    repairs, and one sentence for both would send an operator with a string
    where a bool belongs to look for a syntax error.
    """
    assert BoardLogUnread.FILE.value == "settings could not be read"
    assert BoardLogUnread.VALUE.value == "board_log in settings.json is not true or false"


def test_the_readme_tells_the_operator_what_a_misspelled_switch_does(tmp_path: Path) -> None:
    """
    The README is where an operator learns the switch exists, so what it says a
    misspelling does is held to what ``read`` does with each spelling it quotes,
    and the line it tells them to look for is the one the pane draws.
    """
    from pptmstr.ui import board_pane

    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    section = readme.split("### The board is written to disk", 1)[1].split("\n## ", 1)[0]
    sentence = section.split("misspelled:", 1)[1].split("switches recording **off**", 1)[0]
    quoted = re.findall(r"`([^`]+)`", sentence)

    assert quoted == ["board-log", "boardlog", "Board_Log"]
    for key in quoted:
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps({key: True, "board_log": True}))
        assert read(path) == Loaded(Settings(board_log=False), BoardLogUnread.KEY)
    line = board_pane.recording_line(None, None, BoardLogUnread.KEY.value)
    assert line is not None and line.text.startswith("not recorded:")
    assert "`not recorded:`" in " ".join(section.split())
    assert "Closing a task mails its holder" in section


@pytest.mark.parametrize(
    "key",
    ["board-log", "boardlog", "Board_Log", "board_log ", "BOARD LOG", "board.log", "boardLog"],
)
@pytest.mark.parametrize("value", [False, True, "false"])
def test_a_key_that_is_board_log_misspelled_records_no_board_and_says_why(
    tmp_path: Path, key: str, value: object
) -> None:
    """
    The switch has no menu entry, so a key with these letters is an operator who
    tried to set it. Skipped as an unknown key it would leave recording on --
    for the one operator who asked for it off. Off whatever it is set to: a
    spelling this does not take is not read for its value either.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "light", key: value}))

    got = read(path)

    assert got == Loaded(Settings(theme="light", board_log=False), BoardLogUnread.KEY)
    assert load(path).board_log is False
    assert "board_log" in BoardLogUnread.KEY.value


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ('{"board_log": true, "board-log": false}', BoardLogUnread.KEY),
        ('{"board-log": false, "board_log": true}', BoardLogUnread.KEY),
        ('{"board_log": "false", "boardlog": false}', BoardLogUnread.VALUE),
        ('{"boardlog": false, "board_log": "false"}', BoardLogUnread.VALUE),
    ],
)
def test_a_misspelled_switch_beside_the_real_one_still_records_no_board(
    tmp_path: Path, text: str, why: BoardLogUnread
) -> None:
    """
    Two keys that disagree are not a file that says "on", in either order; and
    the reason given does not depend on the order either.
    """
    path = tmp_path / "settings.json"
    path.write_text(text)

    assert read(path) == Loaded(Settings(board_log=False), why)


@pytest.mark.parametrize("key", ["board_logs", "log", "board", "keyboard_log", "board_log_dir"])
def test_an_unknown_key_that_is_not_the_switch_is_still_ignored(tmp_path: Path, key: str) -> None:
    """
    The other half: a rule that took every unknown key for the switch would stop
    a file written by a later version from recording here.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({key: False}))

    assert read(path) == Loaded(Settings())


def test_load_is_read_without_the_reason(tmp_path: Path) -> None:
    """
    The value ``load`` hands over has to decide on its own: nothing is recorded
    whether or not the caller asks why.
    """
    path = tmp_path / "settings.json"
    for text in ('{"board_log": false,}', '{"board_log": "false"}', '{"board_log": true}', "{}"):
        path.write_text(text)
        assert load(path) == read(path).settings


_UNREADABLE = (
    ('{"board_log": false,}', BoardLogUnread.FILE),
    ("[]", BoardLogUnread.FILE),
    ("", BoardLogUnread.FILE),
    ('{"theme": "ghost", "board_log": "false"}', BoardLogUnread.VALUE),
    ('{"board_log": 0}', BoardLogUnread.VALUE),
    ('{"board-log": false}', BoardLogUnread.KEY),
)


@pytest.mark.parametrize(("text", "why"), _UNREADABLE)
def test_a_file_that_could_not_be_read_is_never_written_over(
    tmp_path: Path, text: str, why: BoardLogUnread
) -> None:
    """
    The hazard is the whole start-to-save path: a run that started from a file
    it could not read holds ``board_log`` off, and a theme change that saved
    those values would make a typo a standing instruction. The file is left
    byte for byte, nothing is left beside it, and the refusal names it.
    """
    path = tmp_path / "settings.json"
    path.write_text(text)
    found = read(path)
    assert found.board_log_unread is why

    refused = save(found.settings.merged(theme="light"), path, unread=found.board_log_unread)

    assert refused == Unsaved(path, why)
    assert path.read_text() == text
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


@pytest.mark.parametrize(("text", "why"), _UNREADABLE)
def test_a_file_broken_since_the_run_started_is_not_written_over(
    tmp_path: Path, text: str, why: BoardLogUnread
) -> None:
    """
    The caller read a good file and says so; the file there now is somebody's
    later edit, and it is the file that decides.
    """
    path = tmp_path / "settings.json"
    path.write_text("{}")
    found = read(path)
    assert found.board_log_unread is None
    path.write_text(text)

    assert save(found.settings, path, unread=found.board_log_unread) == Unsaved(path, why)
    assert path.read_text() == text


def test_a_value_nobody_chose_is_not_saved_over_a_file_repaired_since(tmp_path: Path) -> None:
    """
    Repairing the file mid-run does not make the values in memory the
    operator's: ``board_log`` there is still the fail-safe of the broken read,
    and the repaired file says on.
    """
    path = tmp_path / "settings.json"
    path.write_text('{"board_log": true,}')
    found = read(path)
    assert found.settings.board_log is False
    repaired = '{"board_log": true}'
    path.write_text(repaired)

    refused = save(found.settings, path, unread=found.board_log_unread)

    assert refused == Unsaved(path, BoardLogUnread.FILE)
    assert path.read_text() == repaired
    assert read(path).settings.board_log is True


def test_a_save_that_is_not_refused_says_nothing(tmp_path: Path) -> None:
    """
    None is "not refused", for a first save and for one over a readable file --
    and for a write that failed, which is swallowed as it always was.
    """
    path = tmp_path / "settings.json"
    assert save(Settings(theme="light"), path, unread=None) is None
    assert save(Settings(theme="dark"), path, unread=None) is None
    assert load(path).theme == "dark"
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    assert save(Settings(), blocked / "settings.json", unread=None) is None


def test_the_refusal_tells_the_operator_which_file_and_what_is_wrong_with_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    for why in BoardLogUnread:
        told = Unsaved(path, why).told
        assert str(path) in told
        assert why.value in told
        assert "left as it is" in told
