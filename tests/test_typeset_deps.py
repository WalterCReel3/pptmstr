"""
`scripts/typeset_deps.py` and the Makefile's use of what it builds.

The script fetches advtext and imtypeset at pinned commits and builds a library;
none of that is run here against a real remote or a compiler. What is held is
what the rest depends on: a ref file names one full hash, a pinned commit that
is not a branch tip can be fetched from a `file://` repository and checked out
clean, the library step cannot fail its caller, the interpreter imports the
advtext the pin names, and the Makefile exports the pair only when it is whole.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

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

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
needs_make = pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")


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


@pytest.mark.parametrize("name", ["advtext.ref", "imtypeset.ref"])
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


# --- which advtext is imported ---------------------------------------------------


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


def test_verify_refuses_an_advtext_that_is_not_the_pinned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned, other = tmp_path / "advtext" / "advtext", tmp_path / "elsewhere" / "advtext"
    for top, text in ((pinned, "PINNED = True\n"), (other, "PINNED = False\n")):
        top.mkdir(parents=True)
        (top / "__init__.py").write_text(text, encoding="utf-8")
    monkeypatch.setattr(deps, "imported_advtext", lambda: other)

    assert deps.main(["verify", "--deps", str(tmp_path)]) == 1
    said = capsys.readouterr().err
    assert str(other) in said and "__init__.py" in said

    monkeypatch.setattr(deps, "imported_advtext", lambda: pinned)
    assert deps.main(["verify", "--deps", str(tmp_path)]) == 0


def test_this_interpreter_imports_the_advtext_the_pin_names() -> None:
    if not (deps.DEPS / "advtext" / "advtext").is_dir():
        pytest.skip(".deps/advtext is not there: `make deps` has not run in this checkout")
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
    assert "left as it was" in capsys.readouterr().err


def test_a_platform_without_a_recipe_is_not_a_failure_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(deps.platform, "system", lambda: "Darwin")
    assert deps.main(["library", "--deps", str(tmp_path)]) == 0
    assert "Darwin" in capsys.readouterr().err


@needs_git
def test_advtext_that_cannot_be_fetched_does_fail_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ADVTEXT_REPO", (tmp_path / "no-such-repo").as_uri())
    assert deps.main(["advtext", "--deps", str(tmp_path / "deps")]) == 1
    assert "ADVTEXT_REPO=" in capsys.readouterr().err


# --- the build, around a stand-in for imtypeset's script --------------------------

FAKE_BUILD = """\
#!/bin/sh
[ -z "${FAKE_FAIL:-}" ] || exit 3
: > "$IMTYPESET_BUNDLE_OUT/libimtypeset_bundle.so"
: > "$IMTYPESET_BUNDLE_OUT/libimtypeset_bundle.so.gate"
echo "built $IMTYPESET_BUNDLE_OUT/libimtypeset_bundle.so (stand-in:fake-headers)"
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
    monkeypatch.delenv("FAKE_FAIL", raising=False)
    monkeypatch.delenv("FAKE_REFUSE", raising=False)
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
        assert not (pair / (deps.LIBRARY_NAME + ".gate")).exists()
        assert not (out / "imtypeset.building").exists()
        built_from = (pair / "BUILT-FROM").read_text(encoding="utf-8")
        assert f"commit: {fake_imtypeset}\n" in built_from
        assert "headers: stand-in:fake-headers\n" in built_from
        assert f"for: {sys.executable}\n" in built_from
        assert "module: /wheel/_imgui_bundle.so\n" in built_from


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


# --- the Makefile ----------------------------------------------------------------


def _typeset_env(*make_args: str, env: dict[str, str] | None = None) -> dict[str, str]:
    # This test may itself be running under `make test`, which exports the pair.
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("IMTYPESET_", "MAKE"))}
    done = subprocess.run(
        ["make", "--no-print-directory", "typeset-env", *make_args],
        cwd=ROOT,
        env={**clean, **(env or {})},
        text=True,
        capture_output=True,
        check=True,
    )
    return dict(line.split("=", 1) for line in done.stdout.splitlines())


def _pair(top: Path, *, package: bool = True) -> Path:
    top.mkdir(parents=True)
    (top / deps.LIBRARY_NAME).write_bytes(b"")
    if package:
        (top / "pysnap" / "imtypeset").mkdir(parents=True)
        (top / "pysnap" / "imtypeset" / "__init__.py").write_text("", encoding="utf-8")
    return top


@needs_make
def test_make_exports_a_whole_pair_by_absolute_path(tmp_path: Path) -> None:
    pair = _pair(tmp_path / "pair")
    # Named relative to the Makefile, as the default is: a recipe that changes
    # directory, or a child that does, still finds the pair.
    assert _typeset_env(f"TYPESET_DIR={os.path.relpath(pair, ROOT)}") == {
        "IMTYPESET_LIBRARY": str(pair / deps.LIBRARY_NAME),
        "IMTYPESET_PYTHON": str(pair / "pysnap"),
    }


@needs_make
def test_make_exports_nothing_for_half_a_pair_or_none(tmp_path: Path) -> None:
    nothing = {"IMTYPESET_LIBRARY": "", "IMTYPESET_PYTHON": ""}
    assert _typeset_env(f"TYPESET_DIR={_pair(tmp_path / 'half', package=False)}") == nothing
    assert _typeset_env(f"TYPESET_DIR={tmp_path / 'absent'}") == nothing


@needs_make
def test_the_callers_own_value_wins_and_an_empty_one_turns_the_library_off(
    tmp_path: Path,
) -> None:
    pair = _pair(tmp_path / "pair")
    mine = _typeset_env(f"TYPESET_DIR={pair}", env={"IMTYPESET_LIBRARY": "/mine/lib.so"})
    assert mine["IMTYPESET_LIBRARY"] == "/mine/lib.so"
    assert _typeset_env(f"TYPESET_DIR={pair}", "IMTYPESET_LIBRARY=")["IMTYPESET_LIBRARY"] == ""


def test_the_default_pair_is_where_the_script_builds_it() -> None:
    # The directory is spelled in both files; this is what keeps them in step.
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    (default,) = re.findall(r"^TYPESET_DIR\s*\?=\s*(\S+)$", makefile, flags=re.MULTILINE)
    assert ROOT / default == deps.DEPS / "imtypeset"
    assert f"{default}/{deps.LIBRARY_NAME}" in makefile.replace("$(TYPESET_DIR)", default)


# --- the steps are called, in the order that makes them true ---------------------


def _order(text: str, *needles: str) -> None:
    at = [text.find(needle) for needle in needles]
    assert -1 not in at, [n for n, i in zip(needles, at, strict=True) if i == -1]
    assert at == sorted(at), dict(zip(needles, at, strict=True))


def test_bootstrap_pins_installs_verifies_then_builds_and_guards_the_build() -> None:
    script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    stage = script[script.index("stage 3/4") : script.index("stage 4/4")]
    _order(
        stage,
        "typeset_deps.py advtext\n",
        " .deps/advtext\n",
        "typeset_deps.py verify\n",
        '-e ".[dev]"\n',
        "typeset_deps.py library || {",
    )


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
        "typeset_deps.py advtext\n",
        " .deps/advtext\n",
        "typeset_deps.py verify\n",
        "typeset_deps.py library\n",
    )
