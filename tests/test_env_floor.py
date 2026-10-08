"""
The declared environment floor, held to the thing each bound exists for.

`pyproject.toml` raises imgui-bundle to 1.92.900 for one draw-list flag that
advtext sets on every draw. A floor is a version number and the reason is an
attribute; these tests assert the attribute, so an imgui-bundle that renames it
fails here by name rather than as an AttributeError on the first frame drawn.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _dependencies() -> list[str]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        deps = tomllib.load(handle)["project"]["dependencies"]
    assert isinstance(deps, list)
    return deps


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].lower()


def test_imgui_has_the_draw_flag_the_floor_was_raised_for() -> None:
    from imgui_bundle import imgui

    assert hasattr(imgui.ImDrawListFlags_, "text_no_pixel_snap")


def test_the_declared_imgui_floor_is_at_least_the_release_with_the_flag() -> None:
    (spec,) = [d for d in _dependencies() if _name(d) == "imgui-bundle"]
    match = re.fullmatch(r"imgui-bundle>=(\d+)\.(\d+)\.(\d+)", spec)
    assert match, spec
    assert tuple(int(part) for part in match.groups()) >= (1, 92, 900)


def test_what_advtext_needs_at_run_time_is_a_runtime_dependency() -> None:
    names = {_name(d) for d in _dependencies()}
    assert {"advtext", "fonttools"} <= names


def test_advtext_is_declared_without_a_machine_path() -> None:
    (spec,) = [d for d in _dependencies() if _name(d) == "advtext"]
    assert "@" not in spec and "/" not in spec, spec


def test_advtext_and_its_metrics_library_import() -> None:
    import advtext
    import fontTools.ttLib

    assert advtext.__all__
    assert fontTools.ttLib.TTFont
