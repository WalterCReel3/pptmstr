"""
`scripts/typeset_deps.py` and the Makefile's use of what it builds.

The script fetches advtext and imtypeset at pinned commits and builds a library;
none of that is run here against a real remote or a compiler. What is held is
what the rest depends on: a ref file names one full hash, a pinned commit that
is not a branch tip can be fetched from a `file://` repository and checked out
clean, the library step cannot fail its caller, the interpreter imports the
advtext the pin names and the clone it is compared with is at that pin, and the
Makefile exports the pair only when it is whole, built from the commit
`imtypeset.ref` names, and not mixed with a value the caller set.
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


def _advtext_clone(deps_dir: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    """
    A checkout standing for `.deps/advtext`, and `advtext.ref` naming its commit.
    """
    clone = deps_dir / "advtext"
    (clone / "advtext").mkdir(parents=True)
    (clone / "advtext" / "__init__.py").write_text("PINNED = True\n", encoding="utf-8")
    _git(clone, "init", "--quiet")
    _git(clone, "add", ".")
    _git(clone, "commit", "--quiet", "-m", "pinned")
    commit = _git(clone, "rev-parse", "HEAD")
    monkeypatch.setattr(deps, "read_ref", lambda path: commit)
    return clone / "advtext", commit


@needs_git
def test_verify_refuses_an_advtext_that_is_not_the_pinned_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned, _ = _advtext_clone(tmp_path, monkeypatch)
    other = tmp_path / "elsewhere" / "advtext"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("PINNED = False\n", encoding="utf-8")
    monkeypatch.setattr(deps, "imported_advtext", lambda: other)

    assert deps.main(["verify", "--deps", str(tmp_path)]) == 1
    said = capsys.readouterr().err
    assert str(other) in said and "__init__.py" in said

    monkeypatch.setattr(deps, "imported_advtext", lambda: pinned)
    assert deps.main(["verify", "--deps", str(tmp_path)]) == 0


@needs_git
def test_verify_refuses_a_clone_the_ref_file_has_moved_away_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The bump with no `make deps` after it: what is imported is the clone's,
    # file for file, and neither is the commit the ref file names.
    pinned, commit = _advtext_clone(tmp_path, monkeypatch)
    monkeypatch.setattr(deps, "imported_advtext", lambda: pinned)
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
    pinned, _ = _advtext_clone(tmp_path, monkeypatch)
    monkeypatch.setattr(deps, "imported_advtext", lambda: pinned)
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
    package = tmp_path / "deps" / "advtext" / "advtext"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(deps, "read_ref", lambda path: enclosing)
    monkeypatch.setattr(deps, "imported_advtext", lambda: package)

    assert deps.main(["verify", "--deps", str(tmp_path / "deps")]) == 1
    assert "not a checkout" in capsys.readouterr().err


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
    assert capsys.readouterr().out == "ok\n"

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


needs_venv = pytest.mark.skipif(
    not (ROOT / ".venv" / "bin" / "python").is_file(),
    reason="the Makefile checks a pair with .venv/bin/python, and there is none",
)


def _make_typeset_env(
    *make_args: str, env: dict[str, str] | None = None
) -> tuple[dict[str, str], str]:
    """
    What a recipe gets, and what `make typeset-env` says on standard error about it.
    """
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
    return dict(line.split("=", 1) for line in done.stdout.splitlines()), done.stderr


def _typeset_env(*make_args: str, env: dict[str, str] | None = None) -> dict[str, str]:
    return _make_typeset_env(*make_args, env=env)[0]


def _pair(top: Path, *, package: bool = True, commit: str | None = None) -> Path:
    """
    A pair as `library` leaves one, built from `commit` or from what `imtypeset.ref` names.
    """
    top.mkdir(parents=True)
    (top / deps.LIBRARY_NAME).write_bytes(b"")
    built = commit or deps.read_ref(ROOT / "imtypeset.ref")
    (top / "BUILT-FROM").write_text(f"imtypeset: somewhere\ncommit: {built}\n", encoding="utf-8")
    if package:
        (top / "pysnap" / "imtypeset").mkdir(parents=True)
        (top / "pysnap" / "imtypeset" / "__init__.py").write_text("", encoding="utf-8")
    return top


NOTHING = {"IMTYPESET_LIBRARY": "", "IMTYPESET_PYTHON": ""}


@needs_make
@needs_venv
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
    assert _typeset_env(f"TYPESET_DIR={_pair(tmp_path / 'half', package=False)}") == NOTHING
    assert _typeset_env(f"TYPESET_DIR={tmp_path / 'absent'}") == NOTHING


@needs_make
@needs_venv
def test_make_exports_nothing_from_a_pair_built_from_another_commit_and_says_why(
    tmp_path: Path,
) -> None:
    # What a bump of imtypeset.ref leaves when the build after it fails.
    stale = "e" * 40
    pair = _pair(tmp_path / "pair", commit=stale)

    got, said = _make_typeset_env(f"TYPESET_DIR={pair}")

    assert got == NOTHING
    assert stale in said and deps.read_ref(ROOT / "imtypeset.ref") in said
    # Once as the reason typeset-env gives, once as the warning every target gets.
    assert "NOT exported" in said and len(said.strip().splitlines()) == 2


@needs_make
@needs_venv
@pytest.mark.parametrize("mine", ["IMTYPESET_LIBRARY", "IMTYPESET_PYTHON"])
def test_a_value_the_caller_set_is_never_paired_with_half_of_this_checkouts(
    tmp_path: Path, mine: str
) -> None:
    pair = _pair(tmp_path / "pair")
    assert _typeset_env(f"TYPESET_DIR={pair}") != NOTHING

    got, said = _make_typeset_env(f"TYPESET_DIR={pair}", env={mine: "/another/project"})

    assert got == {**NOTHING, mine: "/another/project"}
    assert mine in said and "set by the caller" in said


@needs_make
@needs_venv
def test_both_values_from_the_caller_win_and_an_empty_one_turns_the_library_off(
    tmp_path: Path,
) -> None:
    pair = _pair(tmp_path / "pair")
    both = {"IMTYPESET_LIBRARY": "/mine/lib.so", "IMTYPESET_PYTHON": "/mine/pysnap"}
    assert _typeset_env(f"TYPESET_DIR={pair}", env=both) == both
    assert _typeset_env(f"TYPESET_DIR={pair}", "IMTYPESET_LIBRARY=") == NOTHING


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


def test_bootstrap_verifies_after_each_install_then_builds_and_guards_the_build() -> None:
    script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    stage = script[script.index("stage 3/4") : script.index("stage 4/4")]
    _order(
        stage,
        "typeset_deps.py advtext\n",
        " .deps/advtext\n",
        "typeset_deps.py verify\n",
        '-e ".[dev]"\n',
        # pyproject.toml names advtext too: the last install is the one to hold.
        "typeset_deps.py verify\n",
        "typeset_deps.py library || {",
    )
    assert "${INSTALL[@]}" not in stage[stage.rindex("typeset_deps.py verify\n") :]


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
        "typeset_deps.py advtext\n",
        " .deps/advtext\n",
        "typeset_deps.py verify\n",
        "typeset_deps.py library\n",
    )
