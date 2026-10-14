"""
The declared environment, held to the thing each bound exists for.

`pyproject.toml` pins imgui-bundle to one version because the imtypeset library
imscribe draws through is built against one wheel binary, and pins imscribe
and imtypeset to the versions that are for it. A pin is a line in a file and
the reason is what is installed; these tests hold both, so an environment
that drifted from a pin fails here by name and not as typeset panes that fall
back. `tests/test_version.py` holds the three pins to each other.
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


def _exact(name: str) -> str:
    """
    The one version ``pyproject.toml`` pins ``name`` to; fails for anything but ``==`` one.
    """
    (spec,) = [d for d in _dependencies() if _name(d) == name]
    match = re.fullmatch(rf"{name}==([0-9a-z.]+)", spec.split(";")[0].strip())
    assert match, f"{spec!r} is not an exact pin of one version"
    return match[1]


def test_imscribe_and_imtypeset_are_requirements_pinned_to_exactly_one_version_each() -> None:
    """
    Both are published, so an install of pptmstr is one resolver's work and
    brings the library: no step of ``bootstrap.sh`` builds or fetches either.
    Exact, like the wheel's pin and for its reason; and imscribe's is a beta,
    which an installer takes only where a requirement names it.
    """
    assert _exact("imscribe") and _exact("imtypeset")
    run = [
        line.strip()
        for line in (ROOT / "bootstrap.sh").read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not [
        line for line in run if "typeset_deps.py" in line or ".deps" in line
    ], "the build from source is the developer's"
    installs = [line for line in run if line.startswith('"${INSTALL[@]}"')]
    assert installs == ['"${INSTALL[@]}" -e ".[dev]"'], "one install, and it names no version"


def test_the_imscribe_that_is_imported_is_the_pinned_one() -> None:
    # Which module is imported and which distribution the metadata finds first
    # are two questions on a path that reaches more than one site-packages.
    import imscribe

    assert imscribe.__version__ == _exact("imscribe")
    assert importlib.metadata.version("imscribe") == _exact("imscribe")
    found = importlib.metadata.distribution("imscribe").locate_file("imscribe")
    assert Path(str(found)).resolve() == Path(imscribe.__file__).resolve().parent


def test_bootstrap_ends_by_naming_the_report_that_says_what_was_installed() -> None:
    last = (ROOT / "bootstrap.sh").read_text(encoding="utf-8").rstrip().splitlines()[-1]
    assert ".venv/bin/python -m pptmstr --version" in last


def test_imscribe_and_its_metrics_library_import() -> None:
    import fontTools.ttLib
    import imscribe

    assert imscribe.__all__
    assert fontTools.ttLib.TTFont
