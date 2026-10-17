"""
`make dist` (`scripts/make_dist.py`): a wheel and an sdist of a commit, built twice.

The script's own promises are held here against a STAND-IN builder: a small
script that writes a zip and a `tar.gz` from the export it is given, with the
commit's time on every member, which is what hatchling does under
`SOURCE_DATE_EPOCH`. So these tests hold the commit and not the working tree,
two builds compared, the contents checked, nothing installed on a refusal,
the hashes and the strings; they say nothing about hatchling's own output.
What the real builder made is in the result of board task REL, which ran the
script with it. `test_what_pyproject_says_the_wheel_and_the_sdist_hold` reads
the configuration hatchling reads and holds the script's lists to it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import pptmstr

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "make_dist.py"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


def _script() -> ModuleType:
    """
    `scripts/make_dist.py` as a module, without putting `scripts/` on `sys.path`.
    """
    spec = importlib.util.spec_from_file_location("_make_dist_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pyproject() -> dict[str, Any]:
    with (_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


# ---------------------------------------------------------------------------
# What pyproject.toml says is built. Read, not built.


def test_the_script_reads_the_version_and_the_pins_the_way_this_file_does() -> None:
    """
    The regular expressions in the script, against the real files and not only a fixture.
    """
    assert _script().declared_version(_ROOT) == pptmstr.__version__
    # The three that are one unit, exact; the imtypeset line carries a marker
    # the expression has to read past.
    assert _script().pins(_ROOT) == [
        "imgui-bundle==1.92.900",
        "imscribe==0.2.0",
        "imtypeset==0.4.3.192900",
    ]


def test_what_pyproject_says_the_wheel_and_the_sdist_hold() -> None:
    project = _pyproject()
    script = _script()
    assert project["build-system"]["build-backend"] == "hatchling.build"
    assert project["tool"]["hatch"]["version"] == {"path": f"{script.NAME}/__init__.py"}
    targets = project["tool"]["hatch"]["build"]["targets"]
    assert targets["wheel"]["packages"] == [script.NAME]
    assert targets["wheel"]["force-include"] == {
        ref: f"{script.NAME}/{ref}" for ref in script.FORCE_INCLUDED
    }
    for ref in script.FORCE_INCLUDED:
        assert (_ROOT / ref).is_file(), ref
    included = targets["sdist"]["include"]
    assert sorted(included) == sorted("/" + entry for entry in script.SDIST_INCLUDE)
    for needed in script.SDIST_REQUIRED:
        if needed != "PKG-INFO":
            assert any(
                needed == entry or needed.startswith(f"{entry}/") for entry in script.SDIST_INCLUDE
            ), needed
    assert project["project"]["license-files"] == ["LICENSE"]
    assert project["project"]["readme"] in script.SDIST_REQUIRED
    assert set(project["project"]["scripts"]) == {script.NAME}
    # The two tools `make dist` runs are what the dev extra installs.
    dev = " ".join(project["project"]["optional-dependencies"]["dev"])
    for tool in script.TOOLS:
        assert re.search(rf"(^| ){tool}>=", dev), tool


# ---------------------------------------------------------------------------
# The script, against a stand-in builder

_STANDIN = r'''
"""A stand-in for `python -m build`: a zip and a tar.gz of the source it is given."""
import gzip, io, os, re, sys, tarfile, time, zipfile
from pathlib import Path

out = Path(sys.argv[sys.argv.index("--outdir") + 1])
source = Path(sys.argv[-1])
fault = os.environ.get("STANDIN_FAULT", "")
epoch = int(os.environ["SOURCE_DATE_EPOCH"])
stated = (source / "pptmstr" / "__init__.py").read_text()
version = re.search(r'^__version__\s*=\s*"([^"]+)"', stated, re.M).group(1)
stamp = time.gmtime(epoch)[:6]

def files(*tops):
    found = []
    for top in tops:
        path = source / top
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found.extend(sorted(p for p in path.rglob("*") if p.is_file()))
    return found

info_dir = f"pptmstr-{version}.dist-info"
said = "9.9.9" if fault == "metadata" else version
metadata = f"Metadata-Version: 2.4\nName: pptmstr\nVersion: {said}\n"
if fault == "path":
    metadata += f"Built-In: {source}\n"
wheel_tops = ["pptmstr"] + (["tests"] if fault == "tests" else [])
with zipfile.ZipFile(out / f"pptmstr-{version}-py3-none-any.whl", "w") as wheel:
    for path in files(*wheel_tops):
        name = path.relative_to(source).as_posix()
        wheel.writestr(zipfile.ZipInfo(name, stamp), path.read_bytes())
    if fault != "unref":
        for ref in ("imscribe.ref", "imtypeset.ref"):
            wheel.writestr(zipfile.ZipInfo(f"pptmstr/{ref}", stamp), (source / ref).read_bytes())
    wheel.writestr(zipfile.ZipInfo(f"{info_dir}/METADATA", stamp), metadata)
    wheel.writestr(zipfile.ZipInfo(f"{info_dir}/WHEEL", stamp), "Wheel-Version: 1.0\n")
    if fault != "no-command":
        entry = "[console_scripts]\npptmstr = pptmstr.__main__:main\n"
        wheel.writestr(zipfile.ZipInfo(f"{info_dir}/entry_points.txt", stamp), entry)
    licence = (source / "LICENSE").read_bytes()
    wheel.writestr(zipfile.ZipInfo(f"{info_dir}/licenses/LICENSE", stamp), licence)

top = f"pptmstr-{version}"
sdist_tops = ["pptmstr", "scripts", "tests", "pyproject.toml", "LICENSE", "imscribe.ref",
              "imtypeset.ref", ".gitignore"]
if fault != "bare":
    sdist_tops.append("README.md")
if fault == "notes":
    sdist_tops += ["HANDOFF-advtext.md", "notes"]
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w") as sdist:
    def add(name, data):
        member = tarfile.TarInfo(f"{top}/{name}")
        member.size, member.mtime, member.mode = len(data), epoch, 0o644
        sdist.addfile(member, io.BytesIO(data))
    for path in files(*sdist_tops):
        add(path.relative_to(source).as_posix(), path.read_bytes())
    add("PKG-INFO", metadata.encode())
header_time = int.from_bytes(os.urandom(3), "big") if fault == "clock" else epoch
with open(out / f"{top}.tar.gz", "wb") as handle:
    with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=header_time) as packed:
        packed.write(raw.getvalue())
'''


def _git(where: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(where), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return done.stdout.strip()


_TREE = {
    "pyproject.toml": (
        '[project]\nname = "pptmstr"\ndynamic = ["version"]\n'
        "dependencies = [\n"
        '    "imgui-bundle==1.92.900",\n'
        "    \"imtypeset==0.4.3.192900; sys_platform == 'linux'\",\n"
        '    "fonttools>=4.50",\n'
        "]\n"
    ),
    "pptmstr/__init__.py": '"""committed"""\n__version__ = "9.8.7.dev6"\n',
    "pptmstr/__main__.py": "def main():\n    return 0\n",
    "pptmstr/ui/view.py": "X = 1\n",
    "scripts/probe.py": "print('probe')\n",
    "tests/test_it.py": "def test_it():\n    pass\n",
    "notes/a.md": "a note\n",
    "HANDOFF-advtext.md": "to advtext\n",
    "README.md": "readme\n",
    "LICENSE": "MIT\n",
    "imscribe.ref": "a" * 40 + "\n",
    "imtypeset.ref": "b" * 40 + "\n",
    ".gitignore": "dist/\n",
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """
    A committed miniature of this repository, and the stand-in builder beside it.
    """
    root = tmp_path / "repo"
    for name, text in _TREE.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "--no-gpg-sign", "-m", "a tree")
    (tmp_path / "standin.py").write_text(_STANDIN, encoding="utf-8")
    return root


def _run(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str = "",
    rev: str = "HEAD",
) -> tuple[int, str, Path, Path]:
    """
    `make()` with the stand-in: the exit code, what was said, the out and stage directories.
    """
    script = _script()
    monkeypatch.setenv("STANDIN_FAULT", fault)
    out, stage = project.parent / "dist", project.parent / "stage"
    said: list[str] = []
    builder = [sys.executable, "-P", "-B", str(project.parent / "standin.py")]
    try:
        code = script.make(project, out, stage, rev, builder, said.append)
    except script.Stop as stop:
        return stop.code, str(stop), out, stage
    return code, "\n".join(said), out, stage


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_WHEEL = "pptmstr-9.8.7.dev6-py3-none-any.whl"
_SDIST = "pptmstr-9.8.7.dev6.tar.gz"


@needs_git
def test_it_builds_the_commit_and_not_the_working_tree(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = _git(project, "rev-parse", "HEAD")
    epoch = int(_git(project, "log", "-1", "--format=%ct"))
    # Two things the commit does not have: an edit, and a file nobody added.
    (project / "pptmstr" / "__init__.py").write_text(
        '"""edited"""\n__version__ = "0.0.0"\n', encoding="utf-8"
    )
    (project / "pptmstr" / "stray.py").write_text("STRAY = 1\n", encoding="utf-8")

    code, said, out, _ = _run(project, monkeypatch)
    assert code == 0, said

    wheel, sdist = out / _WHEEL, out / _SDIST
    assert sorted(path.name for path in out.iterdir()) == sorted(
        [wheel.name, sdist.name, wheel.name + ".sha256", sdist.name + ".sha256", "BUILT-FROM"]
    )
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert archive.read("pptmstr/__init__.py").decode().startswith('"""committed"""')
        # A zip keeps a time to two seconds, so the commit's own second may be the one before.
        written = archive.getinfo("pptmstr/__init__.py").date_time
        assert written == tuple(time.gmtime(epoch - epoch % 2)[:6])
    assert "pptmstr/stray.py" not in names and "pptmstr/imtypeset.ref" in names
    with tarfile.open(sdist) as archive:
        inside = sorted(name.removeprefix("pptmstr-9.8.7.dev6/") for name in archive.getnames())
    assert inside == [
        ".gitignore",
        "LICENSE",
        "PKG-INFO",
        "README.md",
        "imscribe.ref",
        "imtypeset.ref",
        "pptmstr/__init__.py",
        "pptmstr/__main__.py",
        "pptmstr/ui/view.py",
        "pyproject.toml",
        "scripts/probe.py",
        "tests/test_it.py",
    ]
    assert "2 path(s) of the working tree" in said and "NOT in what is built" in said

    # The hashes: beside each file in the form `sha256sum -c` reads, and in the record.
    record = (out / "BUILT-FROM").read_text(encoding="utf-8")
    for built in (wheel, sdist):
        assert (out / f"{built.name}.sha256").read_text() == f"{_sha(built)}  {built.name}\n"
        assert f"sha256: {_sha(built)}  {built.name}" in record
    assert f"commit: {commit}" in record and f"source date epoch: {epoch}" in record
    assert "version: 9.8.7.dev6" in record
    assert "uncommitted paths at the time, none of them built: 2" in record

    # The strings a person copies.
    repository = _script().REPOSITORY
    assert f"pptmstr @ git+{repository}.git@{commit}" in said
    assert (
        f"pptmstr @ {repository}/releases/download/v9.8.7.dev6/{wheel.name}#sha256={_sha(wheel)}"
        in said
    )
    assert f"pptmstr==9.8.7.dev6 --hash=sha256:{_sha(wheel)} --hash=sha256:{_sha(sdist)}" in said
    assert "    imgui-bundle==1.92.900\n    imtypeset==0.4.3.192900\n" in said + "\n"
    assert "fonttools" not in said
    assert "development version" in said
    assert "Nothing below has been published or pushed" in said


@needs_git
def test_the_two_builds_are_made_from_two_exports_and_are_the_same_bytes(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, said, out, stage = _run(project, monkeypatch)
    assert code == 0, said
    assert "two builds from two exports: the same bytes" in said
    for name in (_WHEEL, _SDIST):
        first, second = stage / "out-first" / name, stage / "out-second" / name
        assert _sha(first) == _sha(second) == _sha(out / name)
    exports = sorted(path.name for path in stage.iterdir() if path.name.startswith("export-"))
    assert exports == ["export-first", "export-second"]
    assert not (stage / "export-first" / "pptmstr-src" / ".git").exists()


@needs_git
@pytest.mark.parametrize(
    ("fault", "named"),
    [
        # The export's path written into a member: the two exports have two names.
        ("path", ("pptmstr-9.8.7.dev6.dist-info/METADATA", "PKG-INFO", "sha256")),
        # A clock in the gzip header and every member equal.
        ("clock", (_SDIST, "every member is equal", "container")),
    ],
)
def test_two_builds_that_differ_are_said_and_nothing_is_installed(
    project: Path, monkeypatch: pytest.MonkeyPatch, fault: str, named: tuple[str, ...]
) -> None:
    out = project.parent / "dist"
    out.mkdir()
    (out / "pptmstr-0.0.1-py3-none-any.whl").write_text("the build before", encoding="utf-8")

    code, said, _, stage = _run(project, monkeypatch, fault)
    assert code == _script().EXIT_REFUSED
    assert "two builds of one commit differ" in said
    for word in named:
        assert word in said, said
    if fault == "clock":
        assert "whl" not in said.split("Both are under")[0], "the wheel did not differ"
    assert [path.name for path in out.iterdir()] == ["pptmstr-0.0.1-py3-none-any.whl"]
    assert (stage / "out-first").is_dir() and (stage / "out-second").is_dir()


@needs_git
@pytest.mark.parametrize(
    ("fault", "named"),
    [
        ("tests", "1 member(s) outside pptmstr/ and pptmstr-9.8.7.dev6.dist-info/: tests/"),
        ("unref", "the wheel has no pptmstr/imscribe.ref"),
        ("no-command", "the wheel has no pptmstr-9.8.7.dev6.dist-info/entry_points.txt"),
        (
            "metadata",
            "pptmstr/__init__.py states version '9.8.7.dev6' and the wheel's METADATA '9.9.9'",
        ),
        ("bare", "the sdist has no README.md"),
        (
            "notes",
            "the sdist holds 2 member(s) that pyproject.toml's sdist include does not name: "
            "HANDOFF-advtext.md, notes/a.md",
        ),
    ],
)
def test_a_build_that_is_not_what_pyproject_describes_is_not_installed(
    project: Path, monkeypatch: pytest.MonkeyPatch, fault: str, named: str
) -> None:
    code, said, out, _ = _run(project, monkeypatch, fault)
    assert code == _script().EXIT_REFUSED
    assert named in said, said
    assert not out.exists()


def test_the_include_list_admits_a_directory_and_not_a_name_that_merely_begins_with_it() -> None:
    """
    `scripts` admits `scripts/x.py`; it does not admit `scripts-old/x.py` or `scriptsx`.
    """
    script = _script()
    top = "pptmstr-1.0/"
    base = [f"{top}{name}" for name in ("PKG-INFO", "pyproject.toml", "LICENSE", "README.md")]
    base.append(f"{top}pptmstr/__init__.py")
    assert script.sdist_problems([*base, f"{top}scripts/x.py"], "1.0") == []
    for stranger in ("scripts-old/x.py", "scriptsx", "pptmstr.egg-info/PKG-INFO"):
        problems = script.sdist_problems([*base, f"{top}{stranger}"], "1.0")
        assert len(problems) == 1 and stranger in problems[0], (stranger, problems)
    compiled = script.sdist_problems([*base, f"{top}tests/__pycache__/a.pyc"], "1.0")
    assert compiled == ["the sdist holds compiled files: tests/__pycache__/a.pyc"]


@needs_git
def test_a_build_replaces_the_files_of_the_build_before_and_nothing_else(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = project.parent / "dist"
    out.mkdir()
    for name in ("pptmstr-0.0.1-py3-none-any.whl", "pptmstr-0.0.1.tar.gz.sha256", "BUILT-FROM"):
        (out / name).write_text("before", encoding="utf-8")
    (out / "somebody-elses.whl").write_text("kept", encoding="utf-8")
    code, said, _, _ = _run(project, monkeypatch)
    assert code == 0, said
    left = {path.name for path in out.iterdir()}
    assert "somebody-elses.whl" in left
    assert not {name for name in left if "0.0.1" in name}
    assert "commit: " in (out / "BUILT-FROM").read_text(encoding="utf-8")


@needs_git
def test_another_commit_can_be_named_and_one_that_does_not_exist_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _git(project, "rev-parse", "HEAD")
    (project / "pptmstr" / "ui" / "view.py").write_text("X = 2\n", encoding="utf-8")
    _git(project, "commit", "-q", "--no-gpg-sign", "-am", "a second")

    code, said, out, _ = _run(project, monkeypatch, rev=first)
    assert code == 0, said
    with zipfile.ZipFile(out / _WHEEL) as archive:
        assert archive.read("pptmstr/ui/view.py") == b"X = 1\n"
    assert f"@{first}" in said

    shutil.rmtree(out)
    code, said, out, _ = _run(project, monkeypatch, rev="no-such-rev")
    assert code == _script().EXIT_CANNOT_START
    assert "'no-such-rev' names no commit" in said
    assert not out.exists()


@needs_git
def test_a_directory_that_is_not_the_top_of_a_checkout_is_refused(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Inside a checkout is not enough: an sdist unpacked under a repository is not it.
    """
    script = _script()
    for where in (project / "pptmstr", project.parent):
        with pytest.raises(script.Stop) as stopped:
            script.make(where, project.parent / "dist", project.parent / "stage", "HEAD", ["true"])
        assert stopped.value.code == script.EXIT_CANNOT_START
        assert "is not the top of a git checkout" in str(stopped.value)
    assert not (project.parent / "dist").exists() and not (project.parent / "stage").exists()


def test_a_directory_that_only_has_the_name_is_not_the_tool() -> None:
    """
    `make dist` stages under `build/`. `find_spec("build")` answers with that
    directory when the working directory is on the path, and it is not `python -m build`.
    """
    script = _script()

    class Spec:
        def __init__(self, origin: str | None) -> None:
            self.origin = origin

    found = {"build": Spec(None), "hatchling": Spec("/site/hatchling/__init__.py")}
    assert script.missing_tools(found.get) == ["build"]
    assert script.missing_tools(lambda name: None) == ["build", "hatchling"]
    assert script.missing_tools(lambda name: Spec("/site/x.py")) == []
    assert "-P" in script.default_builder() and "--no-isolation" in script.default_builder()


@needs_git
def test_without_the_tools_it_stops_before_anything_is_written(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _script()
    monkeypatch.setattr(script, "missing_tools", lambda: ["build", "hatchling"])
    out, stage = project.parent / "dist", project.parent / "stage"
    with pytest.raises(script.Stop) as stopped:
        script.make(project, out, stage)
    assert stopped.value.code == script.EXIT_CANNOT_START
    assert "has no build and no hatchling" in str(stopped.value)
    assert "make bootstrap" in str(stopped.value)
    assert not out.exists() and not stage.exists()


@needs_git
def test_the_command_line_answers_with_the_exit_code_and_one_sentence(project: Path) -> None:
    """
    The script as `make dist` runs it, with `--builder` naming the stand-in.
    """
    standin = f"{sys.executable} -P -B {project.parent / 'standin.py'}"
    arguments = [
        sys.executable,
        "-P",
        str(_SCRIPT),
        "--root",
        str(project),
        "--out",
        str(project.parent / "dist"),
        "--stage",
        str(project.parent / "stage"),
        "--builder",
        standin,
    ]
    environment = {name: value for name, value in os.environ.items() if name != "STANDIN_FAULT"}
    good = subprocess.run(
        arguments, capture_output=True, text=True, timeout=120, check=False, env=environment
    )
    assert good.returncode == 0, good.stderr
    assert "pptmstr @ git+" in good.stdout
    bad = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**environment, "STANDIN_FAULT": "tests"},
    )
    assert bad.returncode == 3
    assert bad.stderr.startswith("make dist: what was built is not what pyproject.toml describes")


# ---------------------------------------------------------------------------
# The Makefile's target

_INHERITED = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "DIST_DIR", "DIST_REV", "ARGS")


@needs_make
def test_the_dist_target_hands_the_script_the_commit_and_the_directory() -> None:
    clean = {name: value for name, value in os.environ.items() if name not in _INHERITED}

    def recipe(*args: str) -> str:
        done = subprocess.run(
            [
                "make",
                "-C",
                str(_ROOT),
                "--no-print-directory",
                "-n",
                "dist",
                "PY=python-stub",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=clean,
        )
        assert done.returncode == 0, done.stderr
        return done.stdout.strip().splitlines()[-1]

    assert recipe() == 'python-stub -P scripts/make_dist.py --out "dist" --rev "HEAD"'
    assert (
        recipe("DIST_DIR=/tmp/else", "DIST_REV=v0.2.0", "ARGS=--stage /tmp/s")
        == 'python-stub -P scripts/make_dist.py --out "/tmp/else" --rev "v0.2.0" --stage /tmp/s'
    )
