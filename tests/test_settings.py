"""
Settings: XDG location, atomic writes, and tolerance of a bad file.

The load path is the interesting one. This file is hand-editable and survives
version changes, so it is an untrusted input to a program that must still start.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pptmstr import settings as settings_mod
from pptmstr.settings import Settings, load, save


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings(theme="light", concurrency_cap=8, fps_idle=15.0)
    save(original, path)
    assert load(path) == original


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert load(tmp_path / "nope.json") == Settings()


def test_corrupt_file_gives_defaults(tmp_path: Path) -> None:
    """
    Losing a theme preference is a nuisance; refusing to launch over one is a bug.
    """
    path = tmp_path / "settings.json"
    path.write_text("{not json at all")
    assert load(path) == Settings()


def test_non_object_json_gives_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]")
    assert load(path) == Settings()


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
    save(Settings(wrap_inputs=False), path)
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
    save(Settings(subagent_cap=7), path)
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
    save(Settings(theme="ghost"), path)
    save(Settings(theme="dark"), path)
    assert load(path).theme == "dark"
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_save_creates_the_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "settings.json"
    save(Settings(theme="light"), path)
    assert load(path).theme == "light"


def test_unwritable_target_does_not_raise(tmp_path: Path) -> None:
    """Called from the UI thread on a theme change; must not take down a frame."""
    blocked = tmp_path / "afile"
    blocked.write_text("not a directory")
    save(Settings(), blocked / "settings.json")


def test_config_dir_honours_absolute_xdg(monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
    assert settings_mod.config_dir() == Path("/custom/xdg/pptmstr")


def test_config_dir_ignores_relative_xdg(monkeypatch) -> None:
    """
    The spec requires a relative XDG_CONFIG_HOME to be treated as unset. Honouring
    it anyway would scatter config into whatever the working directory happened to be.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    assert settings_mod.config_dir() == Path.home() / ".config" / "pptmstr"


def test_config_dir_falls_back_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert settings_mod.config_dir() == Path.home() / ".config" / "pptmstr"


def test_text_is_typeset_in_proportional_prose_unless_the_operator_says_otherwise() -> None:
    """
    The defaults are the experiment's "on" arm: a fresh install shows the new
    renderer, and the old one is one menu entry away.
    """
    from pptmstr.ui import typeset

    assert Settings().typeset is True
    assert Settings().typeset_scale == 1.0
    # The default is spelled in `settings` so that module stays free of the UI.
    assert Settings().typeset_faces == typeset.DEFAULT_PRESET
    assert not typeset.PRESETS[0].mono


def test_the_text_switches_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save(Settings(typeset=False, typeset_faces="droid", typeset_scale=1.25), path)
    got = load(path)
    assert (got.typeset, got.typeset_faces, got.typeset_scale) == (False, "droid", 1.25)


def test_a_file_saved_with_the_monospace_switch_keeps_monospace_prose(tmp_path: Path) -> None:
    """
    ``typeset_mono`` is the boolean the face presets replaced. An operator who had
    chosen monospace prose must not find proportional prose after an upgrade.
    """
    from pptmstr.ui import typeset

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"typeset": True, "typeset_mono": True, "theme": "light"}))
    got = load(path)
    assert got.typeset_faces == "mono" and got.theme == "light"
    assert any(preset.name == "mono" and preset.mono for preset in typeset.PRESETS)

    save(got, path)
    assert "typeset_mono" not in json.loads(path.read_text())
    assert load(path).typeset_faces == "mono"


@pytest.mark.parametrize(
    ("saved", "faces"),
    [
        ({"typeset_mono": False}, "roboto"),
        ({"typeset_mono": 1}, "roboto"),
        ({"typeset_mono": "true"}, "roboto"),
        ({"typeset_mono": True, "typeset_faces": "droid"}, "droid"),
        ({"typeset_mono": True, "typeset_faces": 7}, "mono"),
        ({"typeset_faces": "from-a-later-build"}, "from-a-later-build"),
    ],
)
def test_the_old_switch_is_read_only_when_it_is_true_and_no_preset_is_named(
    tmp_path: Path, saved: dict[str, object], faces: str
) -> None:
    """
    A named preset wins over the old key, a preset that is not a string is no
    preset, and a name this build does not know is kept for the build that does.
    """
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(saved))
    assert load(path).typeset_faces == faces


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
