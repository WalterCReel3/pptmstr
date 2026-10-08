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
from pptmstr.settings import LEGACY_FACES, RoleChoice, Settings, load, save


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
    save(chosen, path)
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

    save(got, path)
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

    save(got, path)
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


def test_where_the_context_log_starts_survives_a_restart(tmp_path: Path) -> None:
    """
    Kept as a name, and empty for "this build's": a build that changes its default
    then reaches every file that never chose. A value that is not a string is not
    a name.
    """
    path = tmp_path / "settings.json"
    assert Settings().context_mode == ""
    save(Settings(context_mode="typeset"), path)
    assert json.loads(path.read_text())["context_mode"] == "typeset"
    assert load(path).context_mode == "typeset"

    path.write_text(json.dumps({"context_mode": 3, "theme": "light"}))
    assert load(path) == Settings(theme="light")


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
