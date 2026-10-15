#!/usr/bin/env python3
"""
Create a linked worktree for a feature branch, with an environment borrowed from the
checkout it was made from.

    scripts/worktree.py create <branch> [--base REF]
    scripts/worktree.py check

**What `create` makes.** A sibling of the main checkout, `<repo>-worktrees/<slug>`,
which is the placement `planning/2026-10-07-a-session-can-run-in-its-own-worktree.md`
D4 decided: outside the repository, named from the branch, no character in the name
that the CLI's transcript slug and `brief.session_dir` would sanitise differently.
The base is the launching checkout's `HEAD` unless `--base` names another ref. Nothing
is fetched.

**The environment is a composite, not an install.** A fresh worktree has no `.venv`,
and a real one is half a gigabyte and a network round trip. A branch forked from
`main`, or stacked shallowly on something that was, declares the same dependencies as
its source, so the new `.venv` is an empty one whose single `.pth` file puts the
source's site-packages on the path. What that costs is in the open:

- **The source's `.venv` is shared state.** Re-bootstrapping it changes every worktree
  borrowing from it at once.
- **A branch that changes its dependencies is tested against the source's.** Nothing
  in the interpreter notices. `check` is what does: it compares the dependency tables
  of the two `pyproject.toml` files and fails when they differ. `make` runs it ahead
  of every target that uses the environment. The remedy is a real install --
  `rm -rf .venv && ./bootstrap.sh`.
- **`.pth` files inside the borrowed site-packages are not processed.** Python reads
  `.pth` files only in a site directory, and a directory named *by* a `.pth` is not
  one. Their path lines are therefore carried across explicitly (`composite_paths`);
  their `import` lines are not, so a package that depends on one does not load.
- **Distribution metadata is the source's.** `importlib.metadata.version("pptmstr")`
  and the entry-point table describe what the source installed.

**Why the tools in `bin/` are not all wrappers.** A console script is text with the
source interpreter's path in it, and is copied with that path rewritten. `ruff` is a
native binary, and its Python launcher finds that binary by looking in the
environment's own `bin/` -- so a wrapper there that runs `python -m ruff` finds
itself and execs forever. A binary is linked to instead.

**Removal** is `git worktree remove <path>`, which deletes ignored files without
asking. Here that is this script's own output and nothing else, which is the point of
not installing into it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

# The file that makes a `.venv` a composite, and the record of where it borrows from.
# `check` treats an environment without the second as a real install and says nothing.
PTH_NAME = "_composite.pth"
SOURCE_MARKER = "composite-source"

# Left where `venv` put them: they name the new environment already.
_VENV_OWNED = re.compile(r"^(python[\d.]*|pip[\d.]*|[Aa]ctivate.*)$")

# The tables whose contents decide what an install would put in site-packages.
_DEPENDENCY_KEYS = ("requires-python", "dependencies", "optional-dependencies")


class WorktreeError(Exception):
    """
    A precondition `create` or `check` cannot work around, worded for the operator.
    """


@dataclass(frozen=True, slots=True)
class SourceEnv:
    """
    The environment being borrowed from, as its own interpreter describes it.
    """

    root: Path
    venv: Path
    base_python: Path
    site_packages: Path


def slug_for(branch: str) -> str:
    """
    The directory name for a branch: every character outside `[A-Za-z0-9-]` becomes `-`.

    That is the CLI's own sanitiser applied early, so the name on disk is already the
    name the transcript directory will have. Distinct branches can share a slug
    (`feat/x`, `feat.x`), which is why `create` refuses a directory that exists.
    """
    slug = re.sub(r"[^A-Za-z0-9-]", "-", branch).strip("-")
    if not slug:
        raise WorktreeError(f"branch name {branch!r} leaves nothing to name a directory with")
    return slug


def worktree_path(main_root: Path, branch: str) -> Path:
    return main_root.parent / f"{main_root.name}-worktrees" / slug_for(branch)


def unsanitary_parts(path: Path) -> tuple[str, ...]:
    """
    The components of `path` the CLI's slug and `brief.session_dir` disagree about.

    The CLI turns every non-alphanumeric into `-`; `brief.session_dir` turns only the
    separator. Any other character in the path puts a session's briefs in a different
    directory from its transcripts.
    """
    return tuple(p for p in path.parts[1:] if re.search(r"[^A-Za-z0-9-]", p))


def composite_paths(
    source_root: Path, new_root: Path, pth_lines: list[str], source_site: Path
) -> list[str]:
    """
    The lines of the new environment's `.pth` file.

    `pth_lines` is every line of every `.pth` in the source's site-packages. A path
    under the source checkout is re-rooted at the new one: that is how an editable
    install of the project follows the worktree instead of pointing back at the source.
    Any other path is kept, which is what carries a composite's own borrowed
    directories one level further without nesting. `import` lines and comments are
    dropped, and the source's site-packages goes last so the re-rooted project wins.
    """
    out: list[str] = []
    for raw in pth_lines:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(("import ", "import\t")):
            continue
        path = Path(line)
        if not path.is_absolute():
            path = source_site / path
        if path == source_root or source_root in path.parents:
            path = new_root / path.relative_to(source_root)
        if str(path) not in out:
            out.append(str(path))
    if str(source_site) not in out:
        out.append(str(source_site))
    return out


def dependency_tables(pyproject_text: str) -> dict[str, object]:
    """
    What `pyproject.toml` asks an install for, with comments and ordering parsed away.
    """
    project = tomllib.loads(pyproject_text).get("project", {})
    tables: dict[str, object] = {}
    for key in _DEPENDENCY_KEYS:
        value = project.get(key)
        if isinstance(value, list):
            value = sorted(value)
        elif isinstance(value, dict):
            value = {k: sorted(v) for k, v in sorted(value.items())}
        tables[key] = value
    return tables


def dependency_drift(ours: str, theirs: str) -> tuple[str, ...]:
    """
    The dependency tables that differ between two `pyproject.toml` texts.
    """
    a, b = dependency_tables(ours), dependency_tables(theirs)
    return tuple(key for key in _DEPENDENCY_KEYS if a[key] != b[key])


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def main_root_of(checkout: Path) -> Path:
    """
    The main checkout of the repository `checkout` belongs to, from any of its worktrees.
    """
    common = Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common.parent


def read_source_env(root: Path) -> SourceEnv:
    venv = root / ".venv"
    python = venv / "bin" / "python"
    if not python.exists():
        raise WorktreeError(f"{root} has no .venv to borrow from -- run ./bootstrap.sh there first")
    # Asked of the source's interpreter and not of this one, so the script gives the
    # same answer whichever Python happens to run it.
    probe = (
        "import sys, sysconfig; print(sys._base_executable); print(sysconfig.get_path('purelib'))"
    )
    base, site = subprocess.run(
        [str(python), "-c", probe], capture_output=True, text=True, check=True
    ).stdout.split("\n")[:2]
    return SourceEnv(root=root, venv=venv, base_python=Path(base), site_packages=Path(site))


def _is_text(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" not in handle.read(4096)


def build_composite(source: SourceEnv, new_root: Path) -> Path:
    """
    Make `new_root/.venv` as a composite over `source`, and return it.
    """
    venv = new_root / ".venv"
    subprocess.run([str(source.base_python), "-m", "venv", "--without-pip", str(venv)], check=True)
    site = Path(
        subprocess.run(
            [
                str(venv / "bin" / "python"),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )

    pth_lines: list[str] = []
    for pth in sorted(source.site_packages.glob("*.pth")):
        pth_lines.extend(pth.read_text().splitlines())
    paths = composite_paths(source.root, new_root, pth_lines, source.site_packages)
    (site / PTH_NAME).write_text("\n".join(paths) + "\n")
    (venv / SOURCE_MARKER).write_text(f"{source.root}\n")

    for tool in sorted((source.venv / "bin").iterdir()):
        target = venv / "bin" / tool.name
        if _VENV_OWNED.match(tool.name) or target.exists() or not tool.is_file():
            continue
        if _is_text(tool):
            target.write_text(tool.read_text().replace(str(source.venv), str(venv)))
            target.chmod(0o755)
        else:
            target.symlink_to(tool.resolve())
    return venv


def create(launching: Path, branch: str, base: str | None) -> Path:
    source = read_source_env(Path(_git(launching, "rev-parse", "--show-toplevel")))
    path = worktree_path(main_root_of(launching), branch)
    if path.exists():
        raise WorktreeError(
            f"{path} already exists; branch names that differ only in punctuation share it"
        )

    known = subprocess.run(
        ["git", "-C", str(launching), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
    )
    if known.returncode == 0:
        if base is not None:
            raise WorktreeError(f"branch {branch} already exists, so --base has nothing to set")
        _git(launching, "worktree", "add", str(path), branch)
        print(f"worktree  {path}\nbranch    {branch} (existing)")
    else:
        ref = base or "HEAD"
        at = _git(launching, "rev-parse", "--short", ref)
        _git(launching, "worktree", "add", "-b", branch, str(path), ref)
        print(f"worktree  {path}\nbranch    {branch}, new at {ref} ({at})")

    build_composite(source, path)
    print(f"env       composite over {source.venv}")

    if _git(launching, "status", "--porcelain"):
        print(f"note      {source.root} has uncommitted changes; they are not in the new tree")
    odd = unsanitary_parts(path)
    if odd:
        print(
            "note      a session here keeps its briefs apart from its transcripts: "
            f"{', '.join(odd)} in the path is sanitised differently by the CLI and by pptmstr"
        )
    drift = dependency_drift(
        (path / "pyproject.toml").read_text(), (source.root / "pyproject.toml").read_text()
    )
    if drift:
        print(_drift_message(path, source.root, drift))
    return path


def _drift_message(root: Path, source_root: Path, drift: tuple[str, ...]) -> str:
    return (
        f"{root / 'pyproject.toml'} and {source_root / 'pyproject.toml'} disagree on "
        f"{', '.join(drift)}.\n"
        f"    .venv here borrows {source_root}'s packages, so this tree is being run "
        "against dependencies it does not declare.\n"
        "    For an environment of its own:  rm -rf .venv && ./bootstrap.sh"
    )


def check(root: Path) -> str | None:
    """
    Why the environment in `root` cannot be trusted, or None.

    A `.venv` this script did not make has no marker and is not this function's
    business.
    """
    marker = root / ".venv" / SOURCE_MARKER
    if not marker.exists():
        return None
    source_root = Path(marker.read_text().strip())
    theirs = source_root / "pyproject.toml"
    if not (source_root / ".venv").is_dir() or not theirs.exists():
        return (
            f".venv here borrows from {source_root}, which no longer has an environment.\n"
            "    For an environment of its own:  rm -rf .venv && ./bootstrap.sh"
        )
    drift = dependency_drift((root / "pyproject.toml").read_text(), theirs.read_text())
    return _drift_message(root, source_root, drift) if drift else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="worktree.py", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("create", help="add a worktree and its composite environment")
    make.add_argument("branch")
    make.add_argument("--base", help="ref to branch from (default: this checkout's HEAD)")
    commands.add_parser("check", help="fail if this tree's borrowed environment is stale")
    args = parser.parse_args(argv)

    here = Path(__file__).resolve().parent.parent
    try:
        if args.command == "create":
            create(here, args.branch, args.base)
            return 0
        problem = check(here)
    except WorktreeError as err:
        print(f"worktree.py: {err}", file=sys.stderr)
        return 1
    if problem:
        print(problem, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
