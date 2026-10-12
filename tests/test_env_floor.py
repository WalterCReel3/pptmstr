"""
The declared environment, held to the thing each bound exists for.

`pyproject.toml` pins imgui-bundle to one version because the imtypeset library
advtext draws through is built against one wheel binary. A pin is a line in a
file and the reason is the wheel that is installed; these tests hold both, so
an environment that drifted from the pin fails here by name and not as typeset
panes that fall back.
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


def _imgui_pin() -> str:
    (spec,) = [d for d in _dependencies() if _name(d) == "imgui-bundle"]
    match = re.fullmatch(r"imgui-bundle==(\d+\.\d+\.\d+)", spec)
    assert match, f"{spec!r} is not an exact pin of one version"
    return match[1]


def test_imgui_bundle_is_pinned_to_exactly_one_version() -> None:
    assert _imgui_pin()


def test_the_installed_imgui_bundle_is_the_pinned_one() -> None:
    import imgui_bundle

    assert imgui_bundle.__version__ == _imgui_pin()


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
