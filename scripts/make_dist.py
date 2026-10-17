#!/usr/bin/env python3
"""
`make dist`: a wheel and an sdist of a COMMIT, built twice, with their hashes.

What `.github/workflows/release.yml` uploads to PyPI, and what `make dist`
makes on a developer's machine. The shape is imscribe's `scripts/make_dist.py`.
What it does, in order, stopping at the first thing wrong:

1.  Resolve the commit (`--rev`, default `HEAD`) of the repository this file
    is in. **The working tree is not read**: what is built is `git archive`
    of the commit, so an uncommitted edit is not in the artefact, and the
    script says how many paths were uncommitted so nobody thinks it is.
2.  Export the commit twice, into two directories with different names, and
    build each: `python -m build --no-isolation --outdir <out> <export>`,
    which makes the sdist and then the wheel FROM that sdist.
    `SOURCE_DATE_EPOCH` is the commit's own time, so the files' timestamps
    come from the commit and not from the clock.
3.  Compare the two builds by sha256. **Two builds that differ are not
    installed**: the script says which members differ and how, exits 3, and
    leaves both under the staging directory to look at. Two directory names
    are used because a path leaking into the output is the commonest way a
    build stops being reproducible.
4.  Hold the wheel to what `pyproject.toml` says it is: `pptmstr/` (which
    carries `imscribe.ref` and `imtypeset.ref`, force-included from the root)
    and its own metadata, and nothing else; and the sdist to the `include`
    list of `[tool.hatch.build.targets.sdist]`, which `SDIST_INCLUDE` spells
    again, and what the backend adds (`PKG-INFO`, `.gitignore`). The version
    is dynamic (`[tool.hatch.version]` reads `pptmstr/__init__.py`), so the
    one string is `__version__`, the file names and the wheel's `METADATA`.
5.  Put the two files into `--out` (default `dist/`) with `<file>.sha256`
    beside each (`sha256sum -c` reads them) and `BUILT-FROM`, and print the
    requirement strings.

Exit codes: 0 built and installed; 2 it could not start (no git checkout, no
such commit, no `build` or `hatchling` in this interpreter); 3 it built and
refused what it built (the two builds differ, or the contents are wrong).
`--out` is not touched unless the exit is 0.

**The builder is `build` with `hatchling`, from the interpreter that runs
this script, with no isolated environment and so no network.** Both are in
`pyproject.toml`'s `dev` extra. `tests/test_make_dist.py` runs every step
above against a stand-in builder that writes a zip and a tar the way the real
one does.

**`--out` holds more than the two files**: `<file>.sha256` beside each, and
`BUILT-FROM`. An upload names the two (`dist/*.whl dist/*.tar.gz`), never
`dist/*`: `twine check dist/*` stops on the `.sha256` files ("Unknown
distribution format", measured with imscribe's), and `release.yml` copies the
two into a directory of their own for the same reason.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path

REPOSITORY = "https://github.com/WalterCReel3/pptmstr"
"""
Where a requirement string points. Nothing here contacts it.
"""

NAME = "pptmstr"

TOOLS = ("build", "hatchling")
"""
What the default builder imports. Asked for by name before anything is written.
"""

FORCE_INCLUDED = ("imscribe.ref", "imtypeset.ref")
"""
The files `[tool.hatch.build.targets.wheel.force-include]` puts into
`pptmstr/` from the root of the checkout: an installed pptmstr reads the
commits its pins were published from there (`pptmstr/pairing.py`).
`tests/test_make_dist.py` holds the two lists equal.
"""

SDIST_INCLUDE = (
    "pptmstr",
    "scripts",
    "tests",
    "planning",
    "LICENSE",
    "README.md",
    "CLAUDE.md",
    "orchestrator-design.md",
    "Makefile",
    "bootstrap.sh",
    "pyproject.toml",
    "imscribe.ref",
    "imtypeset.ref",
)
"""
What the sdist may carry under its one top directory, each a file or a
directory: `[tool.hatch.build.targets.sdist] include`, without the leading
`/` (`tests/test_make_dist.py` holds the two equal). A member under none of
these is a refusal.
"""

SDIST_REQUIRED = ("pyproject.toml", "LICENSE", "README.md", f"{NAME}/__init__.py", "PKG-INFO")
"""
What a build of the wheel from the sdist reads, and the metadata the backend writes.
"""

SDIST_ADDED = ("PKG-INFO", ".gitignore")
"""
What hatchling puts in an sdist whatever `include` says. `PKG-INFO` must be
there; `.gitignore` is there when the source has one (measured on imscribe's
`git archive` export, hatchling 1.32.4) and is allowed, not required.
"""

EXIT_CANNOT_START = 2
EXIT_REFUSED = 3


class Stop(Exception):
    """
    A sentence for the person who ran `make dist`, and the exit code that goes with it.
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def default_builder() -> list[str]:
    """
    `python -m build --no-isolation`, in the interpreter that is running this script.

    `-P` because the staging directory is `build/dist-stage`: with the working
    directory on the path and no `build` installed, `import build` finds that
    directory as a namespace package and the failure reads as something else.
    """
    return [sys.executable, "-P", "-m", "build", "--no-isolation"]


def missing_tools(
    find: Callable[[str], object | None] = importlib.util.find_spec,
) -> list[str]:
    """
    Which of `TOOLS` this interpreter cannot import as a real package.

    A namespace package (a directory that merely has the name) does not count:
    its spec has no origin.
    """
    absent: list[str] = []
    for tool in TOOLS:
        try:
            spec = find(tool)
        except (ImportError, ValueError):
            spec = None
        if spec is None or getattr(spec, "origin", None) is None:
            absent.append(tool)
    return absent


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise Stop(
            EXIT_CANNOT_START,
            f"git {' '.join(args)} failed in {root}: {done.stderr.strip() or 'no message'}",
        )
    return done.stdout


def resolve(root: Path, rev: str) -> tuple[str, int, int]:
    """
    The full hash of `rev`, its commit time, and how many paths of the tree are uncommitted.

    `root` must be the TOP of a git checkout: a directory that merely lies
    inside one (an unpacked sdist under somebody's repository) would otherwise
    be built from the repository that encloses it.
    """
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise Stop(EXIT_CANNOT_START, f"git could not be run: {exc}") from exc
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        raise Stop(
            EXIT_CANNOT_START,
            f"{root} is not the top of a git checkout. 'make dist' builds a commit, not a "
            "directory: run it in a clone of the repository.",
        )
    found = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    commit = found.stdout.strip()
    if found.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise Stop(EXIT_CANNOT_START, f"{rev!r} names no commit of {root}.")
    epoch = int(_git(root, "log", "-1", "--format=%ct", commit).strip())
    dirty = len([line for line in _git(root, "status", "--porcelain").splitlines() if line])
    return commit, epoch, dirty


def export(root: Path, commit: str, where: Path) -> None:
    """
    `git archive` of the commit, unpacked into `where`. Reads the object store, writes there.
    """
    where.mkdir(parents=True)
    archive = where.with_suffix(".tar")
    _git(root, "archive", "--format=tar", "-o", str(archive), commit)
    done = subprocess.run(
        ["tar", "-xf", str(archive), "-C", str(where)], capture_output=True, text=True, check=False
    )
    archive.unlink()
    if done.returncode != 0:
        raise Stop(EXIT_CANNOT_START, f"tar could not unpack the export: {done.stderr.strip()}")


def build(builder: Sequence[str], source: Path, out: Path, epoch: int) -> tuple[Path, Path]:
    """
    Run the builder on one export; answer the wheel and the sdist it left in `out`.

    The names are the builder's to choose: exactly one `*.whl` and one
    `*.tar.gz` are expected, and anything else is said.
    """
    out.mkdir(parents=True)
    environment = dict(os.environ)
    environment["SOURCE_DATE_EPOCH"] = str(epoch)
    command = [*builder, "--outdir", str(out), str(source)]
    try:
        done = subprocess.run(command, check=False, env=environment, cwd=str(out))
    except OSError as exc:
        raise Stop(
            EXIT_CANNOT_START, f"the builder could not be run ({shlex.join(command)}): {exc}"
        ) from exc
    if done.returncode != 0:
        raise Stop(EXIT_REFUSED, f"the builder exited {done.returncode}: {shlex.join(command)}")
    wheels = sorted(out.glob("*.whl"))
    sdists = sorted(out.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise Stop(
            EXIT_REFUSED,
            f"the builder left {len(wheels)} wheel(s) and {len(sdists)} sdist(s) in {out}; "
            "one of each was expected.",
        )
    return wheels[0], sdists[0]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            digest.update(piece)
    return digest.hexdigest()


def wheel_members(path: Path) -> dict[str, tuple[object, ...]]:
    """
    Every member of a wheel with what a zip records of it: checksum, size, time, mode bits.
    """
    with zipfile.ZipFile(path) as archive:
        return {
            info.filename: (info.CRC, info.file_size, info.date_time, info.external_attr)
            for info in archive.infolist()
        }


def sdist_members(path: Path) -> dict[str, tuple[object, ...]]:
    """
    Every member of an sdist with what a tar records of it, and a hash of a file's bytes.
    """
    members: dict[str, tuple[object, ...]] = {}
    with tarfile.open(path, "r:gz") as archive:
        for info in archive:
            content = ""
            if info.isfile():
                handle = archive.extractfile(info)
                content = hashlib.sha256(handle.read() if handle else b"").hexdigest()
            members[info.name] = (
                content,
                info.size,
                info.mtime,
                info.mode,
                info.uid,
                info.gid,
                info.uname,
                info.gname,
            )
    return members


def differences(
    first: dict[str, tuple[object, ...]],
    second: dict[str, tuple[object, ...]],
    fields: Sequence[str],
) -> list[str]:
    """
    What differs between two member tables, a line a member, naming the fields.
    """
    lines: list[str] = []
    for name in sorted(set(first) | set(second)):
        if name not in second:
            lines.append(f"{name}: only in the first build")
        elif name not in first:
            lines.append(f"{name}: only in the second build")
        elif first[name] != second[name]:
            which = [
                f"{field} {a!r} / {b!r}"
                for field, a, b in zip(fields, first[name], second[name], strict=True)
                if a != b
            ]
            lines.append(f"{name}: {'; '.join(which)}")
    if not lines and list(first) != list(second):
        lines.append("the same members in another order")
    return lines


WHEEL_FIELDS = ("crc", "size", "time", "mode")
SDIST_FIELDS = ("sha256", "size", "mtime", "mode", "uid", "gid", "uname", "gname")


def compare(first: tuple[Path, Path], second: tuple[Path, Path]) -> list[str]:
    """
    Why two builds are not the same build; empty when their files are byte for byte equal.

    When the members are all equal and the files still differ, what is left
    is the container (a gzip header's time or file name, a zip's comment or
    order), and the line says that much and no more.
    """
    lines: list[str] = []
    readers = (
        (wheel_members, WHEEL_FIELDS),
        (sdist_members, SDIST_FIELDS),
    )
    for (a, b), (members, fields) in zip(zip(first, second, strict=True), readers, strict=True):
        if a.name != b.name:
            lines.append(f"file names differ: {a.name} / {b.name}")
            continue
        if sha256(a) == sha256(b):
            continue
        found = differences(members(a), members(b), fields)
        lines.append(f"{a.name}: sha256 {sha256(a)} / {sha256(b)}")
        lines.extend(f"  {line}" for line in found[:40])
        if len(found) > 40:
            lines.append(f"  ... and {len(found) - 40} more")
        if not found:
            lines.append(
                "  every member is equal: the difference is in the container "
                "(a gzip header, or the archive's own records)"
            )
    return lines


def declared_version(source: Path) -> str | None:
    """
    The version `pptmstr/__init__.py` states: the one `[tool.hatch.version]` reads.
    """
    try:
        text = (source / NAME / "__init__.py").read_text(encoding="utf-8")
    except OSError:
        return None
    found = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return found.group(1) if found else None


def metadata_version(wheel: Path, version: str) -> str | None:
    """
    The `Version:` the wheel's own `METADATA` states, or `None` where it has none.
    """
    try:
        with zipfile.ZipFile(wheel) as archive:
            text = archive.read(f"{NAME}-{version}.dist-info/METADATA").decode("utf-8")
    except (KeyError, OSError, zipfile.BadZipFile, UnicodeDecodeError):
        return None
    found = re.search(r"^Version: (\S+)$", text, re.MULTILINE)
    return found.group(1) if found else None


def pins(source: Path) -> list[str]:
    """
    The `==` requirements `pyproject.toml` states: what a host must hold with this artefact.
    """
    try:
        text = (source / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return []
    # With or without an environment marker after it (the imtypeset line has one).
    return re.findall(r'^\s*"([A-Za-z0-9._-]+==[^";\s]+)\s*(?:;[^"]*)?"', text, re.MULTILINE)


def wheel_problems(names: Sequence[str], version: str) -> list[str]:
    """
    What is wrong with a wheel's member list; empty when it is the package and nothing else.
    """
    problems: list[str] = []
    allowed = (f"{NAME}/", f"{NAME}-{version}.dist-info/")
    strangers = sorted(name for name in names if not name.startswith(allowed))
    if strangers:
        shown = ", ".join(strangers[:8]) + (" ..." if len(strangers) > 8 else "")
        problems.append(
            f"the wheel holds {len(strangers)} member(s) outside {allowed[0]} and "
            f"{allowed[1]}: {shown}"
        )
    for needed in (
        f"{NAME}/__init__.py",
        f"{NAME}/__main__.py",
        *(f"{NAME}/{ref}" for ref in FORCE_INCLUDED),
        f"{allowed[1]}METADATA",
        # `[project.scripts]`: without it the install has no `pptmstr` command.
        f"{allowed[1]}entry_points.txt",
        # `license-files = ["LICENSE"]`: an MIT package that ships without its
        # notice is not what pyproject.toml describes.
        f"{allowed[1]}licenses/LICENSE",
    ):
        if needed not in names:
            problems.append(f"the wheel has no {needed}")
    compiled = sorted(name for name in names if "__pycache__" in name or name.endswith(".pyc"))
    if compiled:
        problems.append(f"the wheel holds compiled files: {', '.join(compiled[:4])}")
    return problems


def sdist_problems(names: Sequence[str], version: str) -> list[str]:
    """
    What is wrong with an sdist's member list; empty when it is what `include`
    names and what the backend adds, and nothing else.

    An sdist's file name is never accepted twice by an index, so what should
    not have gone out in one cannot be taken back: a member that is not on
    the list is a refusal here, before there is a file to upload.
    """
    top = f"{NAME}-{version}/"
    problems: list[str] = []
    outside = sorted(name for name in names if not (name + "/").startswith(top))
    if outside:
        problems.append(f"the sdist holds members outside {top}: {', '.join(outside[:4])}")
    inside = [name[len(top) :] for name in names if name.startswith(top)]
    for needed in SDIST_REQUIRED:
        if needed not in inside:
            problems.append(f"the sdist has no {needed}")

    def included(name: str) -> bool:
        return name in SDIST_ADDED or any(
            name == entry or name.startswith(f"{entry}/") for entry in SDIST_INCLUDE
        )

    strangers = sorted(name for name in inside if name and not included(name))
    if strangers:
        shown = ", ".join(strangers[:8]) + (" ..." if len(strangers) > 8 else "")
        problems.append(
            f"the sdist holds {len(strangers)} member(s) that pyproject.toml's sdist include "
            f"does not name: {shown}"
        )
    compiled = sorted(name for name in inside if "__pycache__" in name or name.endswith(".pyc"))
    if compiled:
        problems.append(f"the sdist holds compiled files: {', '.join(compiled[:4])}")
    return problems


def requirement_strings(
    version: str, commit: str, wheel: tuple[str, str], sdist: tuple[str, str], held: Sequence[str]
) -> list[str]:
    """
    The lines a person can copy, with what each one needs to be true before it resolves.

    `wheel` and `sdist` are `(file name, sha256)`.
    """
    release = f"{REPOSITORY}/releases/download/v{version}"
    lines = [
        "Requirement strings. Nothing below has been published or pushed by this script;",
        "each line says what must exist first.",
        "",
        "  A git reference (pip clones and builds; the commit must be on GitHub):",
        f"    {NAME} @ git+{REPOSITORY}.git@{commit}",
        "",
        "  A release artefact, a direct URL with its hash (resolves only once a release",
        f"  named v{version} holding THIS file exists at that URL; pip checks the hash):",
        f"    {NAME} @ {release}/{wheel[0]}#sha256={wheel[1]}",
        f"    {NAME} @ {release}/{sdist[0]}#sha256={sdist[1]}",
        "",
        "  From a directory or a mirror that holds the files (pip install --require-hashes",
        "  --find-links <dir>, or --index-url <mirror>):",
        f"    {NAME}=={version} --hash=sha256:{wheel[1]} --hash=sha256:{sdist[1]}",
    ]
    if held:
        lines += [
            "",
            "  Held exactly by this artefact's metadata, one unit that moves together (the",
            "  imtypeset wheel is built for that imgui-bundle):",
            *(f"    {pin}" for pin in held),
        ]
    if re.search(r"(a|b|rc)\d+", version):
        lines += [
            "",
            f"  {version} is a pre-release: pip takes it only when asked (--pre, or the exact",
            "  version as above).",
        ]
    if ".dev" in version:
        lines += [
            "",
            f"  {version} is a development version: it names no one commit. The hash or the",
            "  commit above is what identifies what was built.",
        ]
    return lines


def tool_versions() -> list[str]:
    """
    `name version` for the interpreter and each of `TOOLS` that is installed here.
    """
    from importlib import metadata

    lines = [f"python {sys.version.split()[0]}"]
    for tool in TOOLS:
        try:
            lines.append(f"{tool} {metadata.version(tool)}")
        except metadata.PackageNotFoundError:
            lines.append(f"{tool} not installed in this interpreter")
    return lines


def make(
    root: Path,
    out: Path,
    stage: Path,
    rev: str = "HEAD",
    builder: Sequence[str] | None = None,
    say: Callable[[str], None] = print,
) -> int:
    """
    The whole of `make dist`. Returns 0, or raises `Stop` with the code and the sentence.
    """
    commit, epoch, dirty = resolve(root, rev)
    if builder is None:
        absent = missing_tools()
        if absent:
            raise Stop(
                EXIT_CANNOT_START,
                f"{sys.executable} has no {' and no '.join(absent)}. 'make dist' builds with "
                "'python -m build --no-isolation' and the hatchling backend, both in the dev "
                "extra: 'make bootstrap' installs them (needs network). Nothing was built.",
            )
        builder = default_builder()
    say(f"commit {commit} ({rev}), SOURCE_DATE_EPOCH={epoch}")
    if dirty:
        say(
            f"note: {dirty} path(s) of the working tree are uncommitted or untracked and are "
            "NOT in what is built: the build is of the commit."
        )
    if stage.exists():
        shutil.rmtree(stage)
    builds: list[tuple[Path, Path]] = []
    for label in ("first", "second"):
        source = stage / f"export-{label}" / f"{NAME}-src"
        export(root, commit, source)
        builds.append(build(builder, source, stage / f"out-{label}", epoch))
    differing = compare(builds[0], builds[1])
    if differing:
        raise Stop(
            EXIT_REFUSED,
            "two builds of one commit differ, so neither is installed:\n"
            + "\n".join(differing)
            + f"\nBoth are under {stage}.",
        )
    wheel, sdist = builds[0]
    source = stage / "export-first" / f"{NAME}-src"
    stated = declared_version(source)
    problems: list[str] = []
    if stated is None:
        problems.append(f"{NAME}/__init__.py states no __version__")
    version = stated or ""
    if not wheel.name.startswith(f"{NAME}-{version}-") or sdist.name != f"{NAME}-{version}.tar.gz":
        problems.append(
            f"the files are named {wheel.name} and {sdist.name}, not for version {version}"
        )
    in_metadata = metadata_version(wheel, version)
    if in_metadata != stated:
        problems.append(
            f"{NAME}/__init__.py states version {stated!r} and the wheel's METADATA "
            f"{in_metadata!r}"
        )
    problems += wheel_problems(list(wheel_members(wheel)), version)
    problems += sdist_problems(list(sdist_members(sdist)), version)
    if problems:
        raise Stop(
            EXIT_REFUSED,
            "what was built is not what pyproject.toml describes, so it is not installed:\n"
            + "\n".join(f"  {problem}" for problem in problems)
            + f"\nIt is under {stage}.",
        )

    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():
        if old.is_file() and (old.name.startswith(f"{NAME}-") or old.name == "BUILT-FROM"):
            old.unlink()
    hashes: dict[str, str] = {}
    for built in (wheel, sdist):
        shutil.copyfile(built, out / built.name)
        hashes[built.name] = sha256(out / built.name)
        (out / f"{built.name}.sha256").write_text(
            f"{hashes[built.name]}  {built.name}\n", encoding="utf-8"
        )
    (out / "BUILT-FROM").write_text(
        "\n".join(
            [
                f"{NAME}: {REPOSITORY}",
                f"commit: {commit}",
                f"version: {version}",
                f"source date epoch: {epoch}",
                f"uncommitted paths at the time, none of them built: {dirty}",
                "built twice from two exports and compared by sha256: the same",
                f"builder: {shlex.join(builder)}",
                *tool_versions(),
                *(f"sha256: {digest}  {name}" for name, digest in hashes.items()),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    say("two builds from two exports: the same bytes")
    for name, digest in hashes.items():
        say(f"{digest}  {out / name}")
    say("")
    for line in requirement_strings(
        version,
        commit,
        (wheel.name, hashes[wheel.name]),
        (sdist.name, hashes[sdist.name]),
        pins(source),
    ):
        say(line)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    here = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("--out", type=Path, default=here / "dist", help="where the files go")
    parser.add_argument(
        "--stage",
        type=Path,
        default=here / "build" / "dist-stage",
        help="where the exports and the two builds are made; removed at the start of a run",
    )
    parser.add_argument("--rev", default="HEAD", help="the commit to build (default HEAD)")
    parser.add_argument("--root", type=Path, default=here, help=argparse.SUPPRESS)
    parser.add_argument(
        "--builder",
        default=None,
        help="the build command, as one shell-quoted string, in place of "
        "'python -m build --no-isolation'; it is given --outdir <dir> <source>",
    )
    args = parser.parse_args(argv)
    builder = shlex.split(args.builder) if args.builder else None
    try:
        return make(
            args.root.resolve(), args.out.resolve(), args.stage.resolve(), args.rev, builder
        )
    except Stop as stop:
        print(f"make dist: {stop}", file=sys.stderr)
        return stop.code


if __name__ == "__main__":
    raise SystemExit(main())
