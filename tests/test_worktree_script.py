"""
`scripts/worktree.py`: where a worktree goes, and what its borrowed environment is.

The path arithmetic and the dependency comparison are pure and tested as such. The
composite environment is tested by building one against a real repository and a real
`venv`, because the claims worth pinning are about what an interpreter then imports,
and a fixture of strings would agree with whatever the script wrote.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _load() -> ModuleType:
    """
    By path, as `tests/test_splash_art.py` does: `scripts/` is not a package.
    """
    source = Path(__file__).resolve().parent.parent / "scripts" / "worktree.py"
    spec = importlib.util.spec_from_file_location("pptmstr_test_worktree", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


worktree = _load()

PYPROJECT = """
[project]
name = "demo"
version = "0"
requires-python = ">=3.11"
dependencies = [
    # a comment is not a dependency
    "alpha>=1",
    "beta",
]

[project.optional-dependencies]
dev = ["pytest"]
"""


def test_a_slug_is_what_the_cli_would_make_of_the_branch() -> None:
    assert worktree.slug_for("enhanced-sessions") == "enhanced-sessions"
    assert worktree.slug_for("feat/x") == "feat-x"
    assert worktree.slug_for("feat.x") == worktree.slug_for("feat_x") == "feat-x"


def test_a_branch_with_nothing_nameable_is_refused() -> None:
    with pytest.raises(worktree.WorktreeError):
        worktree.slug_for("///")


def test_a_worktree_is_a_sibling_of_the_main_checkout() -> None:
    path = worktree.worktree_path(Path("/src/pptmstr"), "feat/x")
    assert path == Path("/src/pptmstr-worktrees/feat-x")


def test_only_components_the_two_sanitisers_disagree_on_are_reported() -> None:
    assert worktree.unsanitary_parts(Path("/home/w/Source/pptmstr-worktrees/a-b")) == ()
    assert worktree.unsanitary_parts(Path("/home/w.r/.pptmstr/my_tree")) == (
        "w.r",
        ".pptmstr",
        "my_tree",
    )


def test_an_editable_path_follows_the_worktree_and_a_foreign_one_is_kept() -> None:
    """
    The first is what keeps `import pptmstr` from resolving to the source checkout.
    The second is how a composite made from a composite keeps its borrowed directories.
    """
    lines = [
        "/src/repo",
        "/src/repo/vendor/lib",
        "/elsewhere/site-packages",
        "import os; os.environ.setdefault('X', '1')",
        "# comment",
        "",
        "/src/repo",
    ]
    assert worktree.composite_paths(
        Path("/src/repo"), Path("/src/repo-worktrees/a"), lines, Path("/src/repo/.venv/sp")
    ) == [
        "/src/repo-worktrees/a",
        "/src/repo-worktrees/a/vendor/lib",
        "/elsewhere/site-packages",
        "/src/repo/.venv/sp",
    ]


def test_a_path_that_merely_shares_the_source_prefix_is_not_re_rooted() -> None:
    lines = ["/src/repo-other/lib"]
    assert worktree.composite_paths(
        Path("/src/repo"), Path("/src/new"), lines, Path("/src/repo/.venv/sp")
    ) == ["/src/repo-other/lib", "/src/repo/.venv/sp"]


def test_comments_and_ordering_are_not_drift() -> None:
    reordered = PYPROJECT.replace('"alpha>=1",\n    "beta",', '"beta",\n    "alpha>=1",')
    assert reordered != PYPROJECT
    assert worktree.dependency_drift(PYPROJECT, reordered) == ()


@pytest.mark.parametrize(
    ("old", "new", "table"),
    [
        ('"alpha>=1"', '"alpha>=2"', "dependencies"),
        ('dev = ["pytest"]', 'dev = ["pytest", "mypy"]', "optional-dependencies"),
        ('">=3.11"', '">=3.12"', "requires-python"),
    ],
)
def test_a_changed_requirement_is_drift_in_its_own_table(old: str, new: str, table: str) -> None:
    assert worktree.dependency_drift(PYPROJECT.replace(old, new), PYPROJECT) == (table,)


def _run(cwd: Path, *argv: str) -> str:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """
    A committed checkout with a real environment: an editable-style `.pth` naming the
    checkout, one installed module, one console script and one native binary.
    """
    root = tmp_path / "repo"
    (root / "proj").mkdir(parents=True)
    (root / "proj" / "__init__.py").write_text("")
    (root / "pyproject.toml").write_text(PYPROJECT)
    (root / ".gitignore").write_text(".venv/\n__pycache__/\n")
    _run(root, "git", "init", "-q", "-b", "main")
    _run(root, "git", "add", "-A")
    _run(
        root,
        *("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base"),
    )

    _run(root, sys.executable, "-m", "venv", "--without-pip", ".venv")
    env = worktree.read_source_env(root)
    (env.site_packages / "_editable.pth").write_text(f"{root}\n")
    (env.site_packages / "installed_dep.py").write_text("VALUE = 7\n")
    script = env.venv / "bin" / "tool"
    script.write_text(f"#!{env.venv}/bin/python\nimport proj; print(proj.__file__)\n")
    script.chmod(0o755)
    (env.venv / "bin" / "native").write_bytes(b"\x7fELF\0\0\0")
    return root


def test_a_created_worktree_imports_its_own_tree_and_the_sources_packages(source: Path) -> None:
    path = worktree.create(source, "feat/x", None)

    assert path == source.parent / "repo-worktrees" / "feat-x"
    assert _run(path, "git", "branch", "--show-current").strip() == "feat/x"
    python = str(path / ".venv" / "bin" / "python")
    # From a neutral directory, so the answer is the path file's and not cwd's.
    found = _run(
        source.parent,
        python,
        "-c",
        "import proj, installed_dep as d; print(proj.__file__, d.VALUE)",
    )
    assert found.split() == [str(path / "proj" / "__init__.py"), "7"]


def test_a_console_script_runs_under_the_new_interpreter_and_a_binary_is_linked(
    source: Path,
) -> None:
    path = worktree.create(source, "a", None)
    bin_dir = path / ".venv" / "bin"

    assert _run(source.parent, str(bin_dir / "tool")).strip() == str(path / "proj" / "__init__.py")
    assert (bin_dir / "native").is_symlink()
    assert (bin_dir / "native").resolve() == source / ".venv" / "bin" / "native"


def test_a_worktree_made_from_a_worktree_borrows_without_nesting(source: Path) -> None:
    """
    A stack: the second environment names the first's borrowed directories directly,
    since a `.pth` reached through another `.pth` would never be read.
    """
    first = worktree.create(source, "a", None)
    second = worktree.create(first, "b", None)

    assert second == source.parent / "repo-worktrees" / "b"
    python = str(second / ".venv" / "bin" / "python")
    found = _run(
        source.parent,
        python,
        "-c",
        "import proj, installed_dep as d; print(proj.__file__, d.VALUE)",
    )
    assert found.split() == [str(second / "proj" / "__init__.py"), "7"]
    assert (second / ".venv" / worktree.SOURCE_MARKER).read_text().strip() == str(first)


def test_a_second_branch_with_the_same_slug_is_refused(source: Path) -> None:
    worktree.create(source, "feat/x", None)
    with pytest.raises(worktree.WorktreeError, match="already exists"):
        worktree.create(source, "feat.x", None)


def test_an_existing_branch_is_attached_and_cannot_be_rebased_by_a_flag(source: Path) -> None:
    _run(source, "git", "branch", "kept")
    with pytest.raises(worktree.WorktreeError, match="--base"):
        worktree.create(source, "kept", "main")

    path = worktree.create(source, "kept", None)
    assert _run(path, "git", "branch", "--show-current").strip() == "kept"


def test_a_source_without_an_environment_is_refused_before_git_is_touched(tmp_path: Path) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    _run(root, "git", "init", "-q")
    with pytest.raises(worktree.WorktreeError, match="bootstrap"):
        worktree.create(root, "a", None)
    assert not (tmp_path / "bare-worktrees").exists()


def test_check_is_silent_until_the_branch_declares_other_dependencies(source: Path) -> None:
    path = worktree.create(source, "a", None)
    assert worktree.check(path) is None

    theirs = path / "pyproject.toml"
    theirs.write_text(theirs.read_text().replace('"beta"', '"gamma"'))
    problem = worktree.check(path)
    assert problem is not None and "dependencies" in problem and "bootstrap.sh" in problem


def test_check_fails_when_the_source_environment_is_gone(source: Path) -> None:
    path = worktree.create(source, "a", None)
    (source / ".venv").rename(source / ".venv-gone")
    problem = worktree.check(path)
    assert problem is not None and "no longer has an environment" in problem


def test_check_has_nothing_to_say_about_a_real_environment(source: Path) -> None:
    assert worktree.check(source) is None
