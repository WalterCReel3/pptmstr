"""
Settings: XDG location, atomic writes, and tolerance of a bad file.

The load path is the interesting one. This file is hand-editable and survives
version changes, so it is an untrusted input to a program that must still start.
"""

from __future__ import annotations

import json
from pathlib import Path

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
