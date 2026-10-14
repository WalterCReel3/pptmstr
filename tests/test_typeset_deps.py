"""
`scripts/typeset_deps.py` and the Makefile's use of what it builds.

The script fetches imscribe and imtypeset at pinned commits and builds a library;
none of that is run here against a real remote or a compiler. What is held is
what the rest depends on: a ref file names one full hash, a pinned commit that
is not a branch tip can be fetched from a `file://` repository and checked out
clean, the library step cannot fail its caller, the interpreter imports the
imscribe the pin names and the clone it is compared with is at that pin, and the
Makefile exports the pair only when it is whole, built from the commit
`imtypeset.ref` names, and not mixed with a value the caller set.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import child_environment

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    # `scripts/` is not a package. Registered before it is executed because a
    # dataclass resolves its annotations through `sys.modules`.
    spec = importlib.util.spec_from_file_location(
        "typeset_deps", ROOT / "scripts" / "typeset_deps.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deps = _load()

# The environment these tests run in, named: the fetch step deletes from the
# environment it runs in and takes this checkout's `.venv` unless told.
HERE = ("--environment", sys.prefix)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
needs_venv = pytest.mark.skipif(
    not (ROOT / ".venv" / "bin" / "python").is_file(),
    reason="the Makefile checks a pair with .venv/bin/python, and there is none",
)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str, str]:
    """
    A repository with two commits and a tag on the first: its path, and both hashes.
    """
    repo = tmp_path / "upstream"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--initial-branch=main")
    (repo / "pkg.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "pkg.py")
    _git(repo, "commit", "--quiet", "-m", "first")
    first = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "v1.0")
    (repo / "pkg.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "added.py").write_text("", encoding="utf-8")
    _git(repo, "add", "pkg.py", "added.py")
    _git(repo, "commit", "--quiet", "-m", "second")
    return repo, first, _git(repo, "rev-parse", "HEAD")


# --- ref files -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["imscribe.ref", "imtypeset.ref"])
def test_each_tracked_ref_file_names_one_full_hash(name: str) -> None:
    ref = deps.read_ref(ROOT / name)
    assert len(ref) == 40 and set(ref) <= set("0123456789abcdef")


def test_a_ref_is_the_first_line_that_is_not_a_comment(tmp_path: Path) -> None:
    path = tmp_path / "x.ref"
    path.write_text(f"# why\n\n  {'a' * 40}  \n{'b' * 40}\n", encoding="utf-8")
    assert deps.read_ref(path) == "a" * 40


@pytest.mark.parametrize("body", ["38f5fac\n", "main\n", "# only a comment\n", "", "A" * 40 + "\n"])
def test_anything_but_a_full_hash_is_refused(tmp_path: Path, body: str) -> None:
    path = tmp_path / "x.ref"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(deps.DepsError):
        deps.read_ref(path)


# --- fetching --------------------------------------------------------------------


@needs_git
def test_a_commit_that_is_no_branch_tip_is_fetched_from_a_file_url(
    tmp_path: Path, upstream: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, first, second = upstream
    _git(repo, "tag", "--delete", "v1.0")  # nothing names `first` now
    source = deps.Source("TEST_REPO", "https://example.invalid/never-fetched.git")
    monkeypatch.setenv("TEST_REPO", repo.as_uri())
    clone = tmp_path / "clone"

    assert deps.fetch_commit(source, first, clone) == first
    assert (clone / "pkg.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert _git(clone, "rev-parse", "HEAD") == first


@needs_git
def test_moving_the_pin_leaves_nothing_of_the_old_checkout_or_of_a_build(
    tmp_path: Path, upstream: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, first, second = upstream
    source = deps.Source("TEST_REPO", "unused")
    monkeypatch.setenv("TEST_REPO", repo.as_uri())
    clone = tmp_path / "clone"
    deps.fetch_commit(source, second, clone)
    (clone / "pkg.py").write_text("VALUE = 'edited'\n", encoding="utf-8")
    (clone / "build").mkdir()
    (clone / "build" / "stale.o").write_text("", encoding="utf-8")

    deps.fetch_commit(source, first, clone)

    assert (clone / "pkg.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (clone / "added.py").exists()
    assert not (clone / "build").exists()


@needs_git
def test_a_fetch_that_fails_names_the_variable_that_would_change_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = deps.Source("TEST_REPO", "unused")
    monkeypatch.setenv("TEST_REPO", (tmp_path / "no-such-repo").as_uri())
    with pytest.raises(deps.DepsError, match="TEST_REPO="):
        deps.fetch_commit(source, "c" * 40, tmp_path / "clone")


@needs_git
def test_a_tag_fetched_from_a_url_is_a_tag_in_the_clone(
    tmp_path: Path, upstream: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, first, _ = upstream
    source = deps.Source("TEST_REPO", "unused")
    monkeypatch.setenv("TEST_REPO", repo.as_uri())
    clone = tmp_path / "clone"

    assert deps.fetch_tag(source, "v1.0", clone) == clone
    # Both forms imtypeset's build script uses to read one.
    assert _git(clone, "rev-parse", "v1.0^{commit}") == first
    assert _git(clone, "show", "v1.0:pkg.py") == "VALUE = 1"


@needs_git
def test_a_local_checkout_is_read_where_it_is(
    tmp_path: Path, upstream: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _, _ = upstream
    source = deps.Source("TEST_REPO", "unused")
    monkeypatch.setenv("TEST_REPO", str(repo))
    clone = tmp_path / "clone"

    assert deps.fetch_tag(source, "v1.0", clone) == repo
    assert not clone.exists()
    with pytest.raises(deps.DepsError, match="no tag v9.9"):
        deps.fetch_tag(source, "v9.9", clone)


# --- which imscribe is imported ---------------------------------------------------


def test_package_differences_names_changed_added_and_missing_files(tmp_path: Path) -> None:
    pinned, installed = tmp_path / "pinned", tmp_path / "installed"
    for top in (pinned, installed):
        (top / "ui").mkdir(parents=True)
        (top / "same.py").write_text("x = 1\n", encoding="utf-8")
        (top / "ui" / "changed.py").write_text("x = 1\n", encoding="utf-8")
    assert deps.package_differences(pinned, installed) == []

    (installed / "ui" / "changed.py").write_text("x = 2\n", encoding="utf-8")
    (installed / "only_installed.py").write_text("", encoding="utf-8")
    (pinned / "only_pinned.py").write_text("", encoding="utf-8")
    assert deps.package_differences(pinned, installed) == [
        "only_installed.py",
        "only_pinned.py",
        os.path.join("ui", "changed.py"),
    ]


def _imscribe_clone(deps_dir: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """
    A checkout standing for `.deps/imscribe`, and `imscribe.ref` naming its commit.
    """
    clone = deps_dir / "imscribe"
    (clone / "imscribe").mkdir(parents=True)
    (clone / "imscribe" / "__init__.py").write_text("PINNED = True\n", encoding="utf-8")
    _git(clone, "init", "--quiet")
    _git(clone, "add", ".")
    _git(clone, "commit", "--quiet", "-m", "pinned")
    commit = _git(clone, "rev-parse", "HEAD")
    monkeypatch.setattr(deps, "read_ref", lambda path: commit)
    return clone / "imscribe", commit


@needs_git
def test_verify_refuses_an_imscribe_that_is_not_the_pinned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned, _ = _imscribe_clone(tmp_path, monkeypatch)
    other = tmp_path / "elsewhere" / "imscribe"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("PINNED = False\n", encoding="utf-8")
    monkeypatch.setattr(deps, "imported_imscribe", lambda: other)

    assert deps.main(["verify", "--deps", str(tmp_path)]) == 1
    said = capsys.readouterr().err
    assert str(other) in said and "__init__.py" in said

    monkeypatch.setattr(deps, "imported_imscribe", lambda: pinned)
    assert deps.main(["verify", "--deps", str(tmp_path)]) == 0


@needs_git
def test_verify_refuses_a_clone_the_ref_file_has_moved_away_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The bump with no `make deps` after it: what is imported is the clone's,
    # file for file, and neither is the commit the ref file names.
    pinned, commit = _imscribe_clone(tmp_path, monkeypatch)
    monkeypatch.setattr(deps, "imported_imscribe", lambda: pinned)
    assert deps.main(["verify", "--deps", str(tmp_path)]) == 0
    assert commit in capsys.readouterr().out

    bumped = "f" * 40
    monkeypatch.setattr(deps, "read_ref", lambda path: bumped)

    assert deps.main(["verify", "--deps", str(tmp_path)]) == 1
    told = capsys.readouterr()
    assert bumped in told.err and commit in told.err and "make deps" in told.err
    assert "identical" not in told.out


@needs_git
def test_verify_refuses_a_clone_edited_since_it_was_checked_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned, _ = _imscribe_clone(tmp_path, monkeypatch)
    monkeypatch.setattr(deps, "imported_imscribe", lambda: pinned)
    (pinned / "__init__.py").write_text("PINNED = 'edited'\n", encoding="utf-8")

    assert deps.main(["verify", "--deps", str(tmp_path)]) == 1
    assert "__init__.py" in capsys.readouterr().err


@needs_git
def test_verify_refuses_a_directory_that_is_no_checkout_inside_one_that_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `.deps` is inside pptmstr's own repository, whose HEAD git would answer with.
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "commit", "--quiet", "--allow-empty", "-m", "the enclosing repository")
    enclosing = _git(tmp_path, "rev-parse", "HEAD")
    package = tmp_path / "deps" / "imscribe" / "imscribe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(deps, "read_ref", lambda path: enclosing)
    monkeypatch.setattr(deps, "imported_imscribe", lambda: package)

    assert deps.main(["verify", "--deps", str(tmp_path / "deps")]) == 1
    assert "not a checkout" in capsys.readouterr().err


@needs_git
def test_verify_refuses_the_earlier_name_in_this_environment_and_says_what_removes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Everything else about the install is right: the old copy is the only fault.
    pinned, _ = _imscribe_clone(tmp_path, monkeypatch)
    monkeypatch.setattr(deps, "imported_imscribe", lambda: pinned)
    own = tmp_path / "site"
    monkeypatch.setattr(deps, "own_site_directories", lambda: {own.resolve()})
    monkeypatch.syspath_prepend(str(own))

    # A distribution, as an installer left it: the fetch step removes that.
    _earlier_distribution(own)
    assert deps.main(["verify", "--deps", str(tmp_path)]) == 1
    said = capsys.readouterr()
    assert str(own / deps.EARLIER_NAME) in said.err and "identical" not in said.out
    assert "`make deps` removes the distribution of that name." in said.err

    # Files with no record of them: nothing here removes those, and it says so.
    shutil.rmtree(own / f"{deps.EARLIER_NAME}-0.2.0.dev0.dist-info")
    assert deps.main(["verify", "--deps", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "`make deps` does not remove it" in err
    assert f"remove {(own / deps.EARLIER_NAME).resolve()} by hand" in err

    # With no `__init__.py` it imports all the same, as a namespace package.
    shutil.rmtree(own / deps.EARLIER_NAME)
    (own / deps.EARLIER_NAME).mkdir()
    assert deps.main(["verify", "--deps", str(tmp_path)]) == 1
    assert "by hand" in capsys.readouterr().err

    shutil.rmtree(own / deps.EARLIER_NAME)
    assert deps.main(["verify", "--deps", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""


@needs_git
def test_verify_names_the_earlier_name_another_environment_supplies_and_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    sys.path here reaches another environment's site directory. A copy under
    the earlier name there is one the fetch step leaves by design, so telling
    its reader that `make deps` removes it would be untrue, and refusing would
    stop `make deps` before the library is built for a reason nothing in this
    checkout can clear. It is named, with where it is and that it is not ours,
    and the step succeeds: what keeps the name out of the source is the test
    below, which reads the source.
    """
    pinned, _ = _imscribe_clone(tmp_path, monkeypatch)
    monkeypatch.setattr(deps, "imported_imscribe", lambda: pinned)
    own, other = tmp_path / "own" / "site", tmp_path / "other" / "site"
    own.mkdir(parents=True)
    _earlier_distribution(other)
    monkeypatch.setattr(deps, "own_site_directories", lambda: {own.resolve()})
    monkeypatch.syspath_prepend(str(other))

    assert deps.main(["verify", "--deps", str(tmp_path)]) == 0
    said = capsys.readouterr()
    assert "identical" in said.out
    (note,) = said.err.splitlines()
    assert note.startswith("note: ") and str((other / deps.EARLIER_NAME).resolve()) in note
    assert f"outside this environment ({own.resolve()})" in note
    assert "not this checkout's to remove and `make deps` leaves it" in note
    assert "removes the distribution" not in note


def _imports(path: Path, name: str) -> list[str]:
    """
    Every module a file imports that is `name` or under it, wherever in the file.
    """
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom) and not node.level:
            found.append(node.module or "")
    return [module for module in found if module.split(".")[0] == name]


def test_nothing_in_the_tree_imports_the_library_by_its_earlier_name() -> None:
    """
    What `verify` leaves to this test where the earlier name still imports from
    another environment: a module that imported it would pass there and fail
    in a new one. Read from the source, so it holds whatever is installed.
    """
    sources = [
        path
        for top in ("pptmstr", "scripts", "tests")
        for path in sorted((ROOT / top).rglob("*.py"))
    ]
    # The search finds an import it is known to be looking at, or "none" is
    # said of a search that finds nothing anywhere.
    assert _imports(Path(__file__), "pytest") == ["pytest"]
    assert len(sources) > 100
    assert [str(path) for path in sources if _imports(path, deps.EARLIER_NAME)] == []


def _earlier_distribution(site: Path) -> None:
    """
    The library as an installer left it under its earlier name: a package, and a record of it.
    """
    name = deps.EARLIER_NAME
    (site / name / "ui").mkdir(parents=True)
    (site / name / "__init__.py").write_text("", encoding="utf-8")
    (site / name / "ui" / "view.py").write_text("", encoding="utf-8")
    (site / name / "__pycache__").mkdir()
    (site / name / "__pycache__" / "unlisted.pyc").write_bytes(b"")
    (site / f"__editable__.{name}.pth").write_text("", encoding="utf-8")
    record = site / f"{name}-0.2.0.dev0.dist-info"
    record.mkdir()
    (record / "METADATA").write_text(f"Name: {name}\nVersion: 0.2.0.dev0\n", encoding="utf-8")
    (record / "RECORD").write_text(
        f"{name}/__init__.py,,\n{name}/ui/view.py,,\n__editable__.{name}.pth,,\n"
        f"{name}-0.2.0.dev0.dist-info/METADATA,,\n{name}-0.2.0.dev0.dist-info/RECORD,,\n"
        f"../outside.py,,\n",
        encoding="utf-8",
    )


def test_the_earlier_name_is_removed_from_this_environment_and_from_no_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # sys.path here can reach another environment's site directory, and a
    # distribution found there is not this checkout's to delete.
    own, other = tmp_path / "own" / "site", tmp_path / "other" / "site"
    for site in (own, other):
        _earlier_distribution(site)
        (site.parent / "outside.py").write_text("", encoding="utf-8")
        (site / "imscribe").mkdir()
        monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(deps, "own_site_directories", lambda: {own.resolve()})
    monkeypatch.setattr(deps, "foreign_interpreter", lambda environment=None: None)
    clone = tmp_path / "deps" / deps.EARLIER_NAME
    clone.mkdir(parents=True)
    theirs = sorted(other.rglob("*"))

    removed = deps.remove_earlier_name(tmp_path / "deps")

    assert removed == [own / deps.EARLIER_NAME, clone]
    assert sorted(path.name for path in own.iterdir()) == ["imscribe"]
    assert (own.parent / "outside.py").is_file(), "a record's path out of the site directory"
    assert not clone.exists()
    assert sorted(other.rglob("*")) == theirs and len(theirs) > 8
    assert deps.imported_earlier_name() == (other / deps.EARLIER_NAME).resolve()
    assert deps.remove_earlier_name(tmp_path / "deps") == []


def test_the_site_directories_removed_from_are_this_interpreters_own() -> None:
    own = deps.own_site_directories()
    assert own and all(Path(sys.prefix).resolve() in site.parents for site in own)


def test_nothing_is_removed_by_an_interpreter_that_is_not_this_checkouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    "This environment" is whichever interpreter runs the script. Run with the
    library's own checkout active, whose environment holds an editable install
    under the earlier name, the removal would take that project's install out
    of it. So the step refuses any environment but this checkout's `.venv` or
    the one named, before it fetches, and the function that deletes refuses
    for itself.
    """
    theirs = tmp_path / "library" / ".venv"
    site = theirs / "site"
    _earlier_distribution(site)
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(deps, "own_site_directories", lambda: {site.resolve()})
    monkeypatch.setattr(sys, "prefix", str(theirs))
    monkeypatch.setattr(deps, "fetch_commit", lambda *given: pytest.fail("fetched"))
    before = sorted(site.rglob("*"))
    clone = tmp_path / "deps" / deps.EARLIER_NAME
    clone.mkdir(parents=True)

    assert deps.main(["imscribe", "--deps", str(tmp_path / "deps")]) == 1
    said = capsys.readouterr().err
    assert f"whose environment is {theirs.resolve()}" in said and "nothing was removed" in said
    assert str((ROOT / ".venv").resolve()) in said and "--environment" in said
    with pytest.raises(deps.DepsError, match="nothing was removed"):
        deps.remove_earlier_name(tmp_path / "deps")
    with pytest.raises(deps.DepsError, match="which is what --environment names"):
        deps.remove_earlier_name(tmp_path / "deps", tmp_path / "another")
    assert sorted(site.rglob("*")) == before and clone.is_dir()

    # Named, it is the environment that is meant.
    assert deps.foreign_interpreter(theirs) is None
    assert deps.remove_earlier_name(tmp_path / "deps", theirs) == [
        site / deps.EARLIER_NAME,
        clone,
    ]


@needs_venv
def test_this_checkouts_own_environment_is_one_the_step_runs_in() -> None:
    done = subprocess.run(
        [
            str(ROOT / ".venv" / "bin" / "python"),
            "-c",
            "import runpy, sys; sys.argv = ['typeset_deps']; "
            f"m = runpy.run_path({str(ROOT / 'scripts' / 'typeset_deps.py')!r}); "
            "print(m['foreign_interpreter']())",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert done.stdout.strip() == "None"


@needs_git
def test_the_fetch_step_removes_the_earlier_name_and_names_a_variable_it_does_not_read(
    upstream: tuple[Path, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, first, _ = upstream
    monkeypatch.setattr(deps, "read_ref", lambda path: first)
    monkeypatch.setenv("IMSCRIBE_REPO", repo.as_uri())
    monkeypatch.setenv("ADVTEXT_REPO", "file:///nowhere")
    monkeypatch.setenv("ADVTEXT_PIP_ARGS", "--no-index")
    called: list[Path] = []
    monkeypatch.setattr(
        deps, "remove_earlier_name", lambda where, environment: called.append(where) or []
    )

    assert deps.main(["imscribe", "--deps", str(tmp_path / "deps"), *HERE]) == 0

    assert called == [(tmp_path / "deps").resolve()]
    (note,) = capsys.readouterr().err.splitlines()
    assert "ADVTEXT_REPO is set and is not read (IMSCRIBE_REPO is)" in note
    assert "ADVTEXT_PIP_ARGS is set and is not read (IMSCRIBE_PIP_ARGS is)" in note

    monkeypatch.delenv("ADVTEXT_REPO")
    monkeypatch.delenv("ADVTEXT_PIP_ARGS")
    assert deps.main(["imscribe", "--deps", str(tmp_path / "deps"), *HERE]) == 0
    assert capsys.readouterr().err == ""


def test_the_imscribe_this_interpreter_imports_is_the_commit_the_ref_names_file_for_file() -> None:
    """
    What ties the version ``pyproject.toml`` pins to the commit ``imscribe.ref``
    names: with a clone at that commit in ``.deps`` (``make deps``, or its
    first step alone), the package this interpreter imports is that commit's,
    file for file, wherever it was installed from -- the published wheel
    included. Skips where there is no such clone: an install from the index
    makes none, and one left from before a bump of the ref is not evidence
    about the commit it names now.
    """
    clone = deps.DEPS / "imscribe"
    try:
        deps.clone_is_at(clone, deps.read_ref(ROOT / "imscribe.ref"))
    except deps.DepsError as none:
        pytest.skip(f"no clone to hold the import to: {none}")
    assert deps.step_verify(deps.DEPS) == 0


# --- the library step never fails its caller -------------------------------------


def test_no_compiler_is_a_paragraph_and_not_a_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(deps.platform, "system", lambda: "Linux")
    monkeypatch.setenv("CXX", "no-such-compiler-xyz")

    assert deps.main(["library", "--deps", str(tmp_path)]) == 0

    said = capsys.readouterr().err
    assert "NOT built" in said and "no-such-compiler-xyz" in said and "fall back" in said
    assert "left as it was" not in said
    assert not any(tmp_path.iterdir()), "nothing is fetched before the compiler is looked for"


def test_a_failed_build_leaves_the_pair_that_was_there_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    library = tmp_path / "imtypeset" / deps.LIBRARY_NAME
    library.parent.mkdir()
    library.write_bytes(b"the previous build")
    monkeypatch.setattr(deps.platform, "system", lambda: "Linux")
    monkeypatch.setenv("CXX", "no-such-compiler-xyz")

    assert deps.main(["library", "--deps", str(tmp_path)]) == 0

    assert library.read_bytes() == b"the previous build"
    assert "left as it was." in capsys.readouterr().err

    # What a build that asked for the fork and could not have it leaves behind
    # is said to be what it is: the stand-in pair from before.
    (library.parent / "BUILT-FROM").write_text("headers: stand-in:upstream\n", encoding="utf-8")
    assert deps.main(["library", "--deps", str(tmp_path)]) == 0
    err = " ".join(capsys.readouterr().err.split())
    assert "left as it was: the imtypeset pair in" in err
    assert "built against stand-in:upstream, NOT the wheel's own" in err


def test_a_platform_without_a_recipe_is_not_a_failure_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(deps.platform, "system", lambda: "Darwin")
    assert deps.main(["library", "--deps", str(tmp_path)]) == 0
    assert "Darwin" in capsys.readouterr().err


@needs_git
def test_imscribe_that_cannot_be_fetched_does_fail_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IMSCRIBE_REPO", (tmp_path / "no-such-repo").as_uri())
    assert deps.main(["imscribe", "--deps", str(tmp_path / "deps"), *HERE]) == 1
    assert "IMSCRIBE_REPO=" in capsys.readouterr().err


# --- the build, around a stand-in for imtypeset's script --------------------------

# The shape of imtypeset's script where `build_library` meets it: which headers
# are asked for by its own variable, the fork's unless that says otherwise and
# never one in place of the other; HEADERS once it has them, BUILD once linked.
FAKE_BUILD = """\
#!/bin/sh
out="$IMTYPESET_BUNDLE_OUT"
kind="${IMTYPESET_BUNDLE_HEADERS:-fork}"
: > "$out/HEADERS"
if [ "$kind" = fork ]; then
    [ -n "${FAKE_FORK:-}" ] || { echo "the fork cannot be fetched" >&2; exit 1; }
else
    [ -d "${IMTYPESET_IMGUI_CLONE:-}" ] || { echo "no upstream clone" >&2; exit 1; }
fi
printf 'dir=%s\\nlabel=%s:fake-headers\\n' "$out" "$kind" > "$out/HEADERS"
[ -z "${FAKE_FAIL:-}" ] || exit 3
: > "$out/libimtypeset_bundle.so"
echo attested > "$out/libimtypeset_bundle.so.attestation.json"
printf 'library=%s\\nheaders=%s:fake-headers\\nbind=link\\n' \\
    "$out/libimtypeset_bundle.so" "${FAKE_LABEL:-$kind}" > "$out/BUILD"
echo "built $out/libimtypeset_bundle.so ($kind:fake-headers, bind=link)"
"""

FAKE_PACKAGE = """\
import os
if os.environ.get("FAKE_REFUSE"):
    raise ImportError("the fake package refuses the library")
assert os.path.isfile(os.environ["IMTYPESET_LIBRARY"])
"""


@pytest.fixture
def fake_imtypeset(
    tmp_path: Path, upstream: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch
) -> str:
    """
    Every input of `build_library` replaced by a local one; the pinned commit.
    """
    repo = tmp_path / "imtypeset-upstream"
    (repo / "python" / "host").mkdir(parents=True)
    (repo / "python" / "imtypeset").mkdir()
    (repo / "python" / "host" / "build_bundle_library.sh").write_text(FAKE_BUILD, encoding="utf-8")
    (repo / "python" / "imtypeset" / "__init__.py").write_text(FAKE_PACKAGE, encoding="utf-8")
    _git(repo, "init", "--quiet")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "fake")
    commit = _git(repo, "rev-parse", "HEAD")

    headers, _, _ = upstream
    _git(headers, "tag", "v9.9-docking")
    monkeypatch.setenv("IMTYPESET_REPO", repo.as_uri())
    monkeypatch.setenv("IMGUI_BUNDLE_REPO", str(headers))
    monkeypatch.setenv("IMGUI_REPO", str(headers))
    monkeypatch.setenv("CXX", "sh")
    monkeypatch.setenv("IMTYPESET_BUNDLE_HEADERS", "stand-in")
    for variable in ("FAKE_FAIL", "FAKE_REFUSE", "FAKE_FORK", "FAKE_LABEL"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(deps.platform, "system", lambda: "Linux")
    monkeypatch.setattr(deps, "wheel_facts", lambda: ("1.0", "9.9", "/wheel/_imgui_bundle.so"))
    monkeypatch.setattr(deps, "read_ref", lambda path: commit)
    return commit


@needs_git
def test_a_build_installs_a_whole_pair_and_a_second_one_replaces_it(
    tmp_path: Path, fake_imtypeset: str
) -> None:
    out = tmp_path / "deps"
    for _ in range(2):
        assert deps.build_library(out) == "stand-in:fake-headers"

        pair = out / "imtypeset"
        assert (pair / deps.LIBRARY_NAME).is_file()
        assert (pair / "pysnap" / "imtypeset" / "__init__.py").is_file()
        # The build's record that the library was accepted goes where the library goes.
        assert (pair / (deps.LIBRARY_NAME + ".attestation.json")).read_text() == "attested\n"
        assert not (out / "imtypeset.building").exists()
        built_from = (pair / "BUILT-FROM").read_text(encoding="utf-8")
        assert f"commit: {fake_imtypeset}\n" in built_from
        assert "headers: stand-in:fake-headers\n" in built_from
        assert "bind: link\n" in built_from
        assert f"for: {sys.executable}\n" in built_from
        assert "module: /wheel/_imgui_bundle.so\n" in built_from


@needs_git
def test_the_forks_headers_are_asked_for_unless_the_stand_in_is_named(
    tmp_path: Path,
    fake_imtypeset: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "deps"
    monkeypatch.delenv("IMTYPESET_BUNDLE_HEADERS")
    fetch_tag, read = deps.fetch_tag, []

    def recorded(source: object, tag: str, clone: Path) -> Path:
        read.append(source)
        return fetch_tag(source, tag, clone)

    monkeypatch.setattr(deps, "fetch_tag", recorded)

    assert deps.main(["library", "--deps", str(out)]) == 0

    # The fork could not be had, and no library built against other headers stands in.
    assert not (out / "imtypeset").exists()
    err = capsys.readouterr().err
    assert "NOT built" in err and "fall back" in err
    assert "IMTYPESET_BUNDLE_HEADERS=stand-in" in err and "IMTYPESET_IMGUI_FORK_SOURCE=" in err
    assert "status 1" in err and "status 3" not in err

    monkeypatch.setenv("FAKE_FORK", "1")
    assert deps.build_library(out) == "fork:fake-headers"
    assert "headers: fork:fake-headers\n" in (out / "imtypeset" / "BUILT-FROM").read_text()
    # Upstream is the stand-in's source: a build against the fork never waits on it.
    assert read == [deps.IMGUI_BUNDLE, deps.IMGUI_BUNDLE]


@needs_git
def test_a_build_that_fails_once_it_has_headers_is_not_called_a_want_of_headers(
    tmp_path: Path,
    fake_imtypeset: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_FAIL", "1")
    assert deps.main(["library", "--deps", str(tmp_path / "deps")]) == 0
    err = capsys.readouterr().err
    assert "status 3" in err and "IMTYPESET_BUNDLE_HEADERS" not in err


@needs_git
def test_the_stand_in_that_cannot_be_had_names_its_own_repository(
    tmp_path: Path,
    fake_imtypeset: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(deps, "fetch_tag", lambda source, tag, clone: tmp_path / "not-there")
    assert deps.main(["library", "--deps", str(tmp_path / "deps")]) == 0
    err = capsys.readouterr().err
    assert "NOT built" in err and "IMGUI_REPO=" in err
    assert "IMTYPESET_IMGUI_FORK_SOURCE" not in err


@needs_git
@pytest.mark.parametrize(
    ("variable", "value", "said"),
    [
        ("IMTYPESET_BUNDLE_HEADERS", "standin", "'fork' or 'stand-in', not 'standin'"),
        ("FAKE_LABEL", "fork", "stand-in headers were asked for"),
    ],
)
def test_headers_that_are_not_the_ones_asked_for_are_never_recorded_as_them(
    tmp_path: Path,
    fake_imtypeset: str,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
    said: str,
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(deps.DepsError, match=said):
        deps.build_library(tmp_path / "deps")
    assert not (tmp_path / "deps" / "imtypeset").exists()


@needs_git
@pytest.mark.parametrize(
    ("variable", "said"), [("FAKE_FAIL", "status 3"), ("FAKE_REFUSE", "refuses")]
)
def test_a_build_that_fails_late_installs_nothing_and_fails_nobody(
    tmp_path: Path,
    fake_imtypeset: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    variable: str,
    said: str,
) -> None:
    out = tmp_path / "deps"
    deps.build_library(out)
    before = (out / "imtypeset" / "BUILT-FROM").read_bytes()
    (out / "imtypeset" / "marker").write_text("the pair that was there", encoding="utf-8")
    monkeypatch.setenv(variable, "1")

    assert deps.main(["library", "--deps", str(out)]) == 0

    assert (out / "imtypeset" / "BUILT-FROM").read_bytes() == before
    assert (out / "imtypeset" / "marker").is_file()
    err = capsys.readouterr().err
    assert "NOT built" in err and said in err and "left as it was" in err


def _whole_pair(out: Path) -> dict[str, bytes]:
    pair = out / "imtypeset"
    return {
        str(path.relative_to(pair)): path.read_bytes() for path in pair.rglob("*") if path.is_file()
    }


@needs_git
@pytest.mark.parametrize("failure", ["no package", "wheel", "rename", "unexpected"])
def test_no_failure_of_the_build_escapes_or_touches_the_pair_that_was_there(
    tmp_path: Path,
    fake_imtypeset: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    out = tmp_path / "deps"
    deps.build_library(out)
    (out / "imtypeset" / "marker").write_text("the pair that was there", encoding="utf-8")
    before = _whole_pair(out)

    if failure == "no package":
        repo = tmp_path / "imtypeset-upstream"
        _git(repo, "rm", "--quiet", "-r", "python/imtypeset")
        _git(repo, "commit", "--quiet", "-m", "a commit with no package")
        bare = _git(repo, "rev-parse", "HEAD")
        monkeypatch.setattr(deps, "read_ref", lambda path: bare)
        said = "python/imtypeset"
    elif failure == "wheel":
        monkeypatch.setattr(deps, "wheel_facts", lambda: (_ for _ in ()).throw(ValueError("odd")))
        said = "ValueError: odd"
    elif failure == "rename":
        rename = Path.rename

        def refuse_the_stage(self: Path, target: Path) -> Path:
            if self.name == "imtypeset.building":
                raise OSError(39, "Directory not empty", str(self))
            return rename(self, target)

        monkeypatch.setattr(Path, "rename", refuse_the_stage)
        said = "Directory not empty"
    else:
        monkeypatch.setattr(deps, "_install", lambda stage, final: 1 / 0)
        said = "ZeroDivisionError"

    assert deps.main(["library", "--deps", str(out)]) == 0

    assert _whole_pair(out) == before
    assert not (out / "imtypeset.previous").exists()
    err = capsys.readouterr().err
    assert "NOT built" in err and said in err and "fall back" in err and "left as it was" in err
    assert "Traceback" not in err


@needs_git
def test_replacing_a_pair_never_deletes_inside_the_one_in_place(
    tmp_path: Path, fake_imtypeset: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A delete that stops half way leaves a directory with a library in it and
    # half a package, which is a pair by every test of existence.
    out = tmp_path / "deps"
    deps.build_library(out)
    rmtree = shutil.rmtree

    def stops_half_way(path: Path, *args: object, **kwargs: object) -> None:
        if Path(path) == out / "imtypeset":
            (Path(path) / "pysnap" / "imtypeset" / "__init__.py").unlink()
            return
        rmtree(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(deps.shutil, "rmtree", stops_half_way)

    assert deps.main(["library", "--deps", str(out)]) == 0

    assert deps.pair_refusal(out / "imtypeset") is None
    assert not (out / "imtypeset.building").exists()
    assert not (out / "imtypeset.previous").exists()


def test_a_wheel_that_prints_while_it_is_imported_is_still_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = tmp_path / "imgui_bundle"
    fake.mkdir()
    (fake / "__init__.py").write_text(
        "print('a notice on import')\n__version__ = '1.0'\nfrom . import imgui, _imgui_bundle\n",
        encoding="utf-8",
    )
    (fake / "imgui.py").write_text("def get_version(): return '9.9'\n", encoding="utf-8")
    (fake / "_imgui_bundle.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))

    assert deps.wheel_facts() == ("1.0", "9.9", str(fake / "_imgui_bundle.py"))


# --- which pair may be handed to a process ----------------------------------------


@needs_git
def test_a_pair_is_refused_once_the_ref_file_names_another_commit(
    tmp_path: Path,
    fake_imtypeset: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Built by the real step, so what `pair` reads is what `library` writes.
    out = tmp_path / "deps"
    deps.build_library(out)
    capsys.readouterr()
    assert deps.main(["pair", "--deps", str(out)]) == 0
    # The fixture's build asks for the stand-in, and `pair` says it got one.
    (line,) = capsys.readouterr().out.splitlines()
    assert line.startswith("ok; ") and "built against stand-in:fake-headers, NOT" in line

    bumped = "f" * 40
    monkeypatch.setattr(deps, "read_ref", lambda path: bumped)

    assert deps.main(["pair", "--pair", str(out / "imtypeset")]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert fake_imtypeset in line and bumped in line and "make deps" in line


@pytest.mark.parametrize(
    "part", [deps.LIBRARY_NAME, os.path.join("pysnap", "imtypeset", "__init__.py"), "BUILT-FROM"]
)
def test_a_pair_missing_any_part_is_refused_by_name(tmp_path: Path, part: str) -> None:
    pair = _pair(tmp_path / "pair")
    assert deps.pair_refusal(pair) is None
    (pair / part).unlink()
    assert part in (deps.pair_refusal(pair) or "")


def test_the_pair_step_is_one_line_and_no_failure_whatever_goes_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The Makefile reads this at parse time and takes anything but `ok` as the reason.
    pair = _pair(tmp_path / "pair")
    monkeypatch.setattr(deps, "ROOT", tmp_path)  # where there is no imtypeset.ref
    assert deps.main(["pair", "--pair", str(pair)]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert line != "ok" and "imtypeset.ref" in line

    (pair / "BUILT-FROM").write_bytes(b"\xff\n\n")
    (tmp_path / "imtypeset.ref").write_text("a" * 40 + "\n", encoding="utf-8")
    assert deps.main(["pair", "--pair", str(pair)]) == 0
    (line,) = capsys.readouterr().out.splitlines()
    assert line != "ok" and "UnicodeDecodeError" in line


# --- the Makefile ----------------------------------------------------------------


def _make_typeset_env(
    *make_args: str, env: dict[str, str] | None = None
) -> tuple[dict[str, str], str]:
    """
    What a recipe gets, and what `make typeset-env` says on standard error about it.
    """
    # This test may itself be running under `make test`, which exports what it found.
    clean = {k: v for k, v in child_environment().items() if not k.startswith("MAKE")}
    done = subprocess.run(
        ["make", "--no-print-directory", "typeset-env", *make_args],
        cwd=ROOT,
        env={**clean, **(env or {})},
        text=True,
        capture_output=True,
        check=True,
    )
    got = dict(line.split("=", 1) for line in done.stdout.splitlines())
    return {name: value for name, value in got.items() if name != "PYTHONPATH" or value}, (
        done.stderr
    )


def _typeset_env(*make_args: str, env: dict[str, str] | None = None) -> dict[str, str]:
    return _make_typeset_env(*make_args, env=env)[0]


# What the Makefile reads from `typeset_deps.py installed`, given on the command
# line, which wins over its own assignment: both configurations from any .venv.
NONE = "TYPESET_INSTALLED="
INSTALLED = "TYPESET_INSTALLED=imtypeset 0.4.1.192900"


def _pair(
    top: Path,
    *,
    package: bool = True,
    commit: str | None = None,
    headers: str | None = "fork:pthom-abc",
) -> Path:
    """
    A pair as `library` leaves one, built from `commit` or from what `imtypeset.ref` names,
    against the wheel's own headers unless `headers` says otherwise.
    """
    top.mkdir(parents=True)
    (top / deps.LIBRARY_NAME).write_bytes(b"")
    built = commit or deps.read_ref(ROOT / "imtypeset.ref")
    against = f"headers: {headers}\n" if headers else ""
    (top / "BUILT-FROM").write_text(
        f"imtypeset: somewhere\ncommit: {built}\n{against}", encoding="utf-8"
    )
    if package:
        (top / "pysnap" / "imtypeset").mkdir(parents=True)
        (top / "pysnap" / "imtypeset" / "__init__.py").write_text("", encoding="utf-8")
    return top


NOTHING = {"IMTYPESET_LIBRARY": "", "IMTYPESET_PYTHON": "", "IMSCRIBE_NO_TYPESET": ""}


def _exported(pair: Path) -> dict[str, str]:
    return {
        **NOTHING,
        "IMTYPESET_LIBRARY": str(pair / deps.LIBRARY_NAME),
        "IMTYPESET_PYTHON": str(pair / "pysnap"),
    }


def test_the_installed_step_prints_what_the_package_decides_by(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert deps.main(["installed"]) == 0
    assert capsys.readouterr().out == f"{deps.pairing.installed() or ''}\n"


@needs_make
@needs_venv
def test_beside_an_installed_imtypeset_make_exports_nothing_and_says_what_runs(
    tmp_path: Path,
) -> None:
    """
    The normal route: imscribe finds an installed imtypeset by itself, and that
    package reads neither variable. A whole pair in ``.deps`` is left alone.
    """
    pair = _pair(tmp_path / "pair")
    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", INSTALLED)
    assert got == NOTHING
    assert said.strip() == "# imtypeset 0.4.1.192900 is installed, and imscribe finds it by itself"


@needs_make
@needs_venv
def test_the_built_pair_is_exported_beside_an_installed_imtypeset_when_it_is_asked_for(
    tmp_path: Path,
) -> None:
    """
    The development override. With the two variables alone the installed
    package is still the one imported, and it reads neither: the pair's
    package directory goes first on ``PYTHONPATH`` as well, ahead of whatever
    the caller had there.
    """
    pair = _pair(tmp_path / "pair")
    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", INSTALLED, "PPTMSTR_TYPESET_DEPS=1")
    assert got == {**_exported(pair), "PYTHONPATH": str(pair / "pysnap")}
    assert "asked for by PPTMSTR_TYPESET_DEPS in place of the installed imtypeset" in said

    got = _typeset_env(
        f"TYPESET_DIR={pair}", INSTALLED, "PPTMSTR_TYPESET_DEPS=1", env={"PYTHONPATH": "/theirs"}
    )
    assert got["PYTHONPATH"] == f"{pair / 'pysnap'}:/theirs"
    # With none installed the pair is found where imscribe puts it: no path is touched.
    assert _typeset_env(f"TYPESET_DIR={pair}", NONE, "PPTMSTR_TYPESET_DEPS=1") == _exported(pair)
    assert _typeset_env(f"TYPESET_DIR={pair}", INSTALLED, "PPTMSTR_TYPESET_DEPS=0") == NOTHING

    # Asked for and not to be had: the installed one runs, and every target says so.
    stale = _pair(tmp_path / "stale", commit="e" * 40)
    got, said = _make_typeset_env(f"TYPESET_DIR={stale}", INSTALLED, "PPTMSTR_TYPESET_DEPS=1")
    assert got == NOTHING
    assert "NOT exported and the installed imtypeset 0.4.1.192900 is what runs" in said


@needs_make
@needs_venv
@pytest.mark.parametrize("installed", [NONE, INSTALLED])
@pytest.mark.parametrize("asked", [(), ("PPTMSTR_TYPESET_DEPS=1",)])
def test_switched_off_make_exports_nothing_whatever_is_built_or_installed(
    tmp_path: Path, installed: str, asked: tuple[str, ...]
) -> None:
    """
    ``make test IMSCRIBE_NO_TYPESET=1`` is the run without the library, and the
    one way to ask for it. The variable reaches the recipe, which is what stops
    imscribe looking for an installed imtypeset, and no pair is named behind it.
    """
    pair = _pair(tmp_path / "pair")
    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", installed, "IMSCRIBE_NO_TYPESET=1", *asked)
    assert got == {**NOTHING, "IMSCRIBE_NO_TYPESET": "1"}
    assert said.strip() == (
        "# IMSCRIBE_NO_TYPESET is set: no library is looked for and none is exported"
    )
    # From the environment as from the command line; and `0` is not the switch.
    got = _typeset_env(f"TYPESET_DIR={pair}", NONE, env={"IMSCRIBE_NO_TYPESET": "1"})
    assert got == {**NOTHING, "IMSCRIBE_NO_TYPESET": "1"}
    assert _typeset_env(f"TYPESET_DIR={pair}", NONE, "IMSCRIBE_NO_TYPESET=0") == {
        **_exported(pair),
        "IMSCRIBE_NO_TYPESET": "0",
    }


@needs_make
@needs_venv
def test_an_empty_library_variable_beside_an_installed_imtypeset_is_said_not_to_switch_it_off(
    tmp_path: Path,
) -> None:
    """
    ``make test IMTYPESET_LIBRARY=`` ran without the library while the library
    was found only through that variable. An installed imtypeset reads neither,
    so the same command now runs WITH it: every target says so, and names
    the switch that does what was meant.
    """
    pair = _pair(tmp_path / "pair")
    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", INSTALLED, "IMTYPESET_LIBRARY=")
    assert got == NOTHING
    warning, why = said.strip().splitlines()
    assert warning.startswith("Makefile:")
    assert "IMTYPESET_LIBRARY= does NOT run without the library here" in warning
    assert "IMSCRIBE_NO_TYPESET=1 is how to run without it" in warning
    assert "imtypeset 0.4.1.192900 is installed and reads neither variable" in why
    # Said once the switch is thrown as well: nothing to warn of.
    _, said = _make_typeset_env(
        f"TYPESET_DIR={pair}", INSTALLED, "IMTYPESET_LIBRARY=", "IMSCRIBE_NO_TYPESET=1"
    )
    assert "does NOT run" not in said


@needs_make
@needs_venv
def test_make_exports_a_whole_pair_by_absolute_path(tmp_path: Path) -> None:
    pair = _pair(tmp_path / "pair")
    # Named relative to the Makefile, as the default is: a recipe that changes
    # directory, or a child that does, still finds the pair.
    assert _typeset_env(f"TYPESET_DIR={os.path.relpath(pair, ROOT)}", NONE) == _exported(pair)


def test_the_pair_step_says_ok_alone_only_of_a_pair_built_against_the_wheels_headers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert deps.FORK + ":" == deps.pairing.FORK_HEADERS
    for name, headers, said in (
        ("fork", "fork:pthom-abc", ""),
        ("stand-in", "stand-in:ocornut-v1.92.9-docking", "stand-in:ocornut-v1.92.9-docking"),
        ("unrecorded", None, "headers its BUILT-FROM does not record"),
    ):
        pair = _pair(tmp_path / name, headers=headers)
        assert deps.main(["pair", "--pair", str(pair)]) == 0
        (line,) = capsys.readouterr().out.splitlines()
        if not said:
            assert line == "ok"
            continue
        assert line.startswith(f"ok; the imtypeset pair in {pair} was built against {said}, NOT ")


@needs_make
@needs_venv
@pytest.mark.parametrize("headers", ["stand-in:ocornut-v1.92.9-docking", None])
def test_make_exports_a_stand_in_pair_and_says_on_every_run_that_it_is_one(
    tmp_path: Path, headers: str | None
) -> None:
    """
    A pair built against upstream's headers is the only build there is where
    the wheel's fork cannot be fetched, and a default build that fails there
    leaves it in place and exits 0. It is exported, and the run says what it
    is: once as a warning every target gets, once in the reason.
    """
    pair = _pair(tmp_path / "pair", headers=headers)
    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", NONE)
    assert got == _exported(pair)
    warning, why = said.strip().splitlines()
    against = headers or "headers its BUILT-FROM does not record"
    for line in (warning, why):
        assert f"was built against {against}, NOT the wheel's own Dear ImGui headers" in line
    assert warning.startswith("Makefile:") and "the pair in " in why

    # And a pair built against the wheel's own says nothing more than it did.
    fork = _pair(tmp_path / "fork")
    got, said = _make_typeset_env(f"TYPESET_DIR={fork}", NONE)
    assert got["IMTYPESET_LIBRARY"] == str(fork / deps.LIBRARY_NAME)
    assert said.strip() == f"# the pair in {fork}, built from the commit imtypeset.ref names"


@needs_make
def test_make_exports_nothing_for_half_a_pair_or_none(tmp_path: Path) -> None:
    half = _pair(tmp_path / "half", package=False)
    assert _typeset_env(f"TYPESET_DIR={half}", NONE) == NOTHING
    got, said = _make_typeset_env(f"TYPESET_DIR={tmp_path / 'absent'}", NONE)
    assert got == NOTHING
    assert "no imtypeset is installed" in said and "'make deps' builds the pair" in said


@needs_make
@needs_venv
def test_make_exports_nothing_from_a_pair_built_from_another_commit_and_says_why(
    tmp_path: Path,
) -> None:
    # What a bump of imtypeset.ref leaves when the build after it fails.
    stale = "e" * 40
    pair = _pair(tmp_path / "pair", commit=stale)

    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", NONE)

    assert got == NOTHING
    assert stale in said and deps.read_ref(ROOT / "imtypeset.ref") in said
    # Once as the reason typeset-env gives, once as the warning every target gets.
    assert "NOT exported and typeset panes fall back" in said
    assert len(said.strip().splitlines()) == 2


@needs_make
@needs_venv
@pytest.mark.parametrize("mine", ["IMTYPESET_LIBRARY", "IMTYPESET_PYTHON"])
def test_a_value_the_caller_set_is_never_paired_with_half_of_this_checkouts(
    tmp_path: Path, mine: str
) -> None:
    pair = _pair(tmp_path / "pair")
    assert _typeset_env(f"TYPESET_DIR={pair}", NONE) != NOTHING

    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", NONE, env={mine: "/another/project"})

    assert got == {**NOTHING, mine: "/another/project"}
    assert mine in said and "set by the caller" in said


@needs_make
@needs_venv
def test_both_values_from_the_caller_win_and_with_none_installed_an_empty_one_is_no_library(
    tmp_path: Path,
) -> None:
    pair = _pair(tmp_path / "pair")
    both = {"IMTYPESET_LIBRARY": "/mine/lib.so", "IMTYPESET_PYTHON": "/mine/pysnap"}
    assert _typeset_env(f"TYPESET_DIR={pair}", NONE, env=both) == {**NOTHING, **both}
    assert _typeset_env(f"TYPESET_DIR={pair}", INSTALLED, env=both) == {**NOTHING, **both}
    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", NONE, "IMTYPESET_LIBRARY=")
    assert got == NOTHING and "does NOT run" not in said


def test_the_default_pair_is_where_the_script_builds_it() -> None:
    # The directory is spelled in both files; this is what keeps them in step.
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    (default,) = re.findall(r"^TYPESET_DIR\s*\?=\s*(\S+)$", makefile, flags=re.MULTILINE)
    assert ROOT / default == deps.DEPS / "imtypeset"
    assert f"{default}/{deps.LIBRARY_NAME}" in makefile.replace("$(TYPESET_DIR)", default)


# --- the steps are called, in the order that makes them true ---------------------


def _order(text: str, *needles: str) -> None:
    """
    Each needle is found after the one before it, so one named twice is there twice.
    """
    at = 0
    for needle in needles:
        found = text.find(needle, at)
        assert found != -1, f"{needle!r} is not after {text[:at][-60:]!r}"
        at = found + len(needle)


def test_bootstrap_installs_once_from_the_index_and_builds_nothing() -> None:
    """
    The published versions are requirements, so the install stage is one
    install and the library comes with it. Nothing is fetched or compiled:
    the build from source is ``make deps``, the development override. The
    one other install is a reinstall from the index, with no dependencies,
    of what ``pptmstr.pairing.unpublished`` names (``tests/test_probe.py``
    runs it with stand-ins).
    """
    script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    stage = script[script.index("stage 3/4") : script.index("stage 4/4")]
    run = [line for line in stage.splitlines()[1:] if line and not line.lstrip().startswith("#")]
    assert run[0] == '"${INSTALL[@]}" -e ".[dev]"'
    calls = [line.strip() for line in run if line.strip().startswith('"${')]
    assert calls == ['"${INSTALL[@]}" -e ".[dev]"', '"${REINSTALL[@]}"'], "one install"
    assert "typeset_deps" not in "".join(run) and ".deps" not in "".join(run)


def test_bootstrap_help_is_the_whole_header_and_none_of_the_script() -> None:
    done = subprocess.run(
        [str(ROOT / "bootstrap.sh"), "--help"],
        env={k: v for k, v in os.environ.items() if k != "ADVTEXT_SRC"},
        text=True,
        capture_output=True,
        check=True,
    )
    lines = done.stdout.splitlines()
    assert lines[0].startswith("Bootstrap:")
    assert lines[-1].startswith("Usage: ./bootstrap.sh")
    script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8").splitlines()
    header = [line for line in script[1 : script.index("set -euo pipefail")]]
    assert len(lines) == len(header) and all(line.startswith("#") for line in header)


@needs_make
def test_make_deps_runs_the_same_steps_in_the_same_order() -> None:
    recipe = subprocess.run(
        ["make", "--no-print-directory", "-n", "deps"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    _order(
        recipe,
        "typeset_deps.py imscribe\n",
        " .deps/imscribe\n",
        "typeset_deps.py verify\n",
        "typeset_deps.py library\n",
    )


@needs_make
@needs_venv
def test_switched_off_with_the_variables_set_make_warns_that_they_are_taken_out(
    tmp_path: Path,
) -> None:
    pair = _pair(tmp_path / "pair")
    named = {"IMTYPESET_LIBRARY": "/left/over/lib.so", "IMTYPESET_PYTHON": "/left/over/pysnap"}
    for installed in (NONE, INSTALLED):
        got, said = _make_typeset_env(
            f"TYPESET_DIR={pair}", installed, "IMSCRIBE_NO_TYPESET=1", env=named
        )
        assert got == {**NOTHING, "IMSCRIBE_NO_TYPESET": "1", **named}
        warning, why = said.strip().splitlines()
        assert "IMSCRIBE_NO_TYPESET is set and so is IMTYPESET_LIBRARY IMTYPESET_PYTHON" in warning
        assert "taken out of the environment of pptmstr and of a test run" in why


@needs_make
@needs_venv
def test_a_pair_built_for_another_environment_is_not_exported(tmp_path: Path) -> None:
    pair = _pair(tmp_path / "pair")
    with (pair / "BUILT-FROM").open("a", encoding="utf-8") as record:
        record.write("for: /another/project/.venv/bin/python\n")
    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", NONE)
    assert got == NOTHING
    assert "was built for /another/project/.venv/bin/python" in said


RUN_STAND_IN = """#!/bin/sh
case "$*" in
  *typeset_deps.py\\ pair*) echo ok; exit 0 ;;
  *typeset_deps.py*) exit 0 ;;
esac
exec {python} -c 'import json, os; print(json.dumps({{k: os.environ.get(k) for k in \
  ("IMTYPESET_LIBRARY", "IMTYPESET_PYTHON", "PYTHONPATH", "PPTMSTR_TYPESET_DEPS")}}))'
"""


@needs_make
@needs_venv
@pytest.mark.parametrize("caller_path", [None, "/the/callers/own"])
@pytest.mark.parametrize("installed", [NONE, INSTALLED])
def test_make_run_hands_pptmstr_none_of_what_it_exports_for_the_pair(
    tmp_path: Path, caller_path: str | None, installed: str
) -> None:
    """
    ``make run PPTMSTR_TYPESET_DEPS=1`` with a whole pair: the Makefile exports
    the two variables (and, beside an installed imtypeset, the pair's package
    first on PYTHONPATH) for tests and scripts, and pptmstr, which sets them for
    its own process and takes them back before a session starts, is given none
    of them; the caller's own PYTHONPATH reaches it as the caller gave it.
    """
    pair = _pair(tmp_path / "pair")
    python = tmp_path / "python"
    python.write_text(RUN_STAND_IN.format(python=sys.executable), encoding="utf-8")
    python.chmod(0o755)
    clean = {k: v for k, v in child_environment().items() if not k.startswith("MAKE")}
    clean.pop("PYTHONPATH", None)
    if caller_path is not None:
        clean["PYTHONPATH"] = caller_path
    make = ["make", "--no-print-directory", f"PY={python}", f"TYPESET_DIR={pair}", installed]
    exported = _make_typeset_env(f"TYPESET_DIR={pair}", installed, "PPTMSTR_TYPESET_DEPS=1")[0]
    assert exported["IMTYPESET_LIBRARY"] == str(pair / deps.LIBRARY_NAME)
    done = subprocess.run(
        [*make, "PPTMSTR_TYPESET_DEPS=1", "HEADLESS=0", "GUI=", "run"],
        cwd=ROOT,
        env=clean,
        text=True,
        capture_output=True,
        check=True,
    )
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen == {
        "IMTYPESET_LIBRARY": None,
        "IMTYPESET_PYTHON": None,
        "PYTHONPATH": caller_path,
        "PPTMSTR_TYPESET_DEPS": "1",
    }
