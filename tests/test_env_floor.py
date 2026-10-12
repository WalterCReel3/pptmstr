"""
The declared environment, held to the thing each bound exists for.

`pyproject.toml` pins imgui-bundle to one version because the imtypeset library
imscribe draws through is built against one wheel binary. A pin is a line in a
file and the reason is the wheel that is installed; these tests hold both, so
an environment that drifted from the pin fails here by name and not as typeset
panes that fall back.
"""

from __future__ import annotations

import importlib.metadata
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


def test_the_imgui_bundle_that_runs_and_the_one_that_is_installed_are_the_pinned_one() -> None:
    # Two questions with one answer in a clean environment and possibly two on
    # a path that reaches more than one site-packages: which module is
    # imported, and which distribution the metadata finds first.
    import imgui_bundle

    assert imgui_bundle.__version__ == _imgui_pin()
    assert importlib.metadata.version("imgui-bundle") == _imgui_pin()


def test_what_imscribe_needs_at_run_time_is_a_runtime_dependency() -> None:
    assert "fonttools" in {_name(d) for d in _dependencies()}


def test_imscribe_is_asked_of_no_package_index() -> None:
    # The name is unclaimed on PyPI: a requirement that names it bare installs
    # whatever is registered under it. bootstrap.sh installs imscribe, at the
    # commit imscribe.ref names, and nothing in the build's metadata asks for it.
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    named = [*project["dependencies"]]
    for group in project.get("optional-dependencies", {}).values():
        named += group
    assert [spec for spec in named if _name(spec) == "imscribe"] == []
    assert "typeset_deps.py imscribe" in (ROOT / "bootstrap.sh").read_text(encoding="utf-8")


def test_imscribe_and_its_metrics_library_import() -> None:
    import fontTools.ttLib
    import imscribe

    assert imscribe.__all__
    assert fontTools.ttLib.TTFont
