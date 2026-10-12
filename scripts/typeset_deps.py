#!/usr/bin/env python3
"""
advtext at the commit `advtext.ref` names, and the imtypeset library for this interpreter.

Stdlib only, and run by the interpreter the result is for (`.venv/bin/python`):
`bootstrap.sh` and `make deps` are the two callers. Everything it makes is under
`.deps/`, which is ignored:

    .deps/advtext/            a clone checked out at the commit in advtext.ref
    .deps/imtypeset-src/      a clone checked out at the commit in imtypeset.ref
    .deps/imgui_bundle/       objects only: Dear ImGui Bundle's tag for the wheel
    .deps/imgui/              objects only: Dear ImGui's docking tag for the wheel
    .deps/imtypeset/          the pair a process is pointed at:
        libimtypeset_bundle.so    IMTYPESET_LIBRARY
        pysnap/                   IMTYPESET_PYTHON (holds the `imtypeset` package)
        BUILT-FROM                what it was built from, and against which headers

Three steps, because the install between the first two belongs to the caller,
which knows whether the installer is pip or uv:

    advtext   fetch and check out the pinned commit. Failing here fails the caller.
    verify    the `advtext` this interpreter imports is that commit's, file for file.
    library   build the pair. Never fails the caller: without the library pptmstr
              still runs and typeset panes fall back, so a missing compiler is a
              paragraph on standard error and exit status 0.

Where each source comes from is a variable holding anything `git fetch` accepts,
GitHub by default:

    ADVTEXT_REPO        https://github.com/WalterCReel3/imgui-adv-text.git
    IMTYPESET_REPO      https://github.com/WalterCReel3/imtypeset.git
    IMGUI_BUNDLE_REPO   https://github.com/pthom/imgui_bundle.git
    IMGUI_REPO          https://github.com/ocornut/imgui.git

The last two are read by imtypeset's build script for Dear ImGui's headers,
which the wheel does not ship. Either may instead name a local checkout, which
is then read in place and not copied; that is the only way to the wheel's own
(fork) headers, which the build script uses when the bundle checkout's
`external/imgui/imgui` submodule is at the commit the wheel's tag pins. Fetched
from a URL there is no submodule, so the headers are upstream Dear ImGui's as a
stand-in. BUILT-FROM and the last line of the build say which.

The library is linked against one installed `imgui-bundle` binary and is for
that interpreter alone, which is why `pyproject.toml` pins the wheel exactly.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPS = ROOT / ".deps"

LIBRARY_NAME = "libimtypeset_bundle.so"
BUILD_SCRIPT = Path("python") / "host" / "build_bundle_library.sh"

_HASH = re.compile(r"[0-9a-f]{40}")
# The build script's closing line, the one place it names the headers it used.
_BUILT = re.compile(r"^built .*\((?P<label>(?:fork|stand-in):[^)]*)\)\s*$")


class DepsError(Exception):
    """
    A step could not be done; the message is the sentence to print.
    """


@dataclass(frozen=True, slots=True)
class Source:
    """
    One repository: the variable that names it and where it is when nothing does.
    """

    variable: str
    default: str

    def repo(self) -> str:
        return os.environ.get(self.variable) or self.default


ADVTEXT = Source("ADVTEXT_REPO", "https://github.com/WalterCReel3/imgui-adv-text.git")
IMTYPESET = Source("IMTYPESET_REPO", "https://github.com/WalterCReel3/imtypeset.git")
IMGUI_BUNDLE = Source("IMGUI_BUNDLE_REPO", "https://github.com/pthom/imgui_bundle.git")
IMGUI = Source("IMGUI_REPO", "https://github.com/ocornut/imgui.git")


def read_ref(path: Path) -> str:
    """
    The commit a ref file names: its first line that is neither blank nor a comment.

    A full hash and nothing shorter. An abbreviated hash is not fetchable from a
    remote, and a branch name would make the pin whatever the branch is today.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise DepsError(f"{path} cannot be read ({error.strerror}).") from error
    for line in lines:
        ref = line.strip()
        if not ref or ref.startswith("#"):
            continue
        if not _HASH.fullmatch(ref):
            raise DepsError(f"{path} names {ref!r}, which is not a full 40-digit commit hash.")
        return ref
    raise DepsError(f"{path} names no commit.")


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=False
        )
    except FileNotFoundError as error:
        raise DepsError(
            "git is not installed, and the pinned sources are fetched with it."
        ) from error


def fetch(source: Source, ref: str, clone: Path) -> str:
    """
    Fetch `ref` of the source's repository into `clone`; the commit it resolved to.

    One commit deep. Nothing is checked out.
    """
    repo = source.repo()
    if not (clone / ".git").is_dir():
        clone.mkdir(parents=True, exist_ok=True)
        made = _git("init", "--quiet", str(clone))
        if made.returncode != 0:
            raise DepsError(f"{clone} could not be made a git repository: {made.stderr.strip()}")
    got = _git("fetch", "--quiet", "--depth", "1", "--no-tags", repo, ref, cwd=clone)
    if got.returncode != 0:
        detail = (got.stderr.strip().splitlines() or ["git said nothing"])[-1]
        raise DepsError(
            f"{ref} could not be fetched from {repo} ({detail}). Either there is no network, "
            f"or the repository has no such commit or tag. {source.variable}=... names another "
            f"repository, and takes a local path or a file:// URL."
        )
    commit = _git("rev-parse", "--verify", "--quiet", "FETCH_HEAD^{commit}", cwd=clone)
    if commit.returncode != 0:
        raise DepsError(f"{ref} of {repo} was fetched and is not a commit.")
    return commit.stdout.strip()


def fetch_commit(source: Source, ref: str, clone: Path) -> str:
    """
    Make `clone` a clean checkout of the pinned commit `ref`.
    """
    commit = fetch(source, ref, clone)
    if commit != ref:
        raise DepsError(f"{source.repo()} answered {commit} when asked for {ref}.")
    for step in (("checkout", "--quiet", "--force", "--detach", commit), ("clean", "-fdxq")):
        done = _git(*step, cwd=clone)
        if done.returncode != 0:
            raise DepsError(f"git {step[0]} failed in {clone}: {done.stderr.strip()}")
    return commit


def fetch_tag(source: Source, tag: str, clone: Path) -> Path:
    """
    The repository imtypeset's build script reads `tag` from.

    A local checkout is used where it is. A URL is fetched into `clone`, where
    the tag is then a ref the script can name.
    """
    local = Path(source.repo()).expanduser()
    if local.is_dir():
        if _git("rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}", cwd=local).returncode != 0:
            raise DepsError(f"{local} ({source.variable}) has no tag {tag}.")
        return local
    commit = fetch(source, f"refs/tags/{tag}", clone)
    _git("update-ref", f"refs/tags/{tag}", commit, cwd=clone)
    return clone


def package_differences(pinned: Path, installed: Path) -> list[str]:
    """
    Python files that differ between two `advtext` package directories, by relative path.
    """

    def sources(top: Path) -> dict[str, Path]:
        return {str(path.relative_to(top)): path for path in top.rglob("*.py")}

    want, have = sources(pinned), sources(installed)
    return sorted(
        name
        for name in want.keys() | have.keys()
        if name not in want
        or name not in have
        or want[name].read_bytes() != have[name].read_bytes()
    )


def imported_advtext() -> Path | None:
    """
    The directory `import advtext` would load in this interpreter, without importing it.
    """
    spec = importlib.util.find_spec("advtext")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve().parent


def step_advtext(deps: Path) -> int:
    ref = read_ref(ROOT / "advtext.ref")
    clone = deps / "advtext"
    fetch_commit(ADVTEXT, ref, clone)
    print(f"advtext {ref} from {ADVTEXT.repo()} -> {clone}")
    return 0


def step_verify(deps: Path) -> int:
    ref = read_ref(ROOT / "advtext.ref")
    pinned = deps / "advtext" / "advtext"
    installed = imported_advtext()
    if installed is None:
        raise DepsError(f"advtext is not installed for {sys.executable}.")
    differing = package_differences(pinned, installed)
    if differing:
        raise DepsError(
            f"{sys.executable} imports advtext from {installed}, and that is not commit {ref} "
            f"as checked out in {pinned}: {len(differing)} file(s) differ, the first "
            f"{differing[0]}. Something ahead of the installed copy on sys.path (an editable "
            f"install, a .pth file) is supplying another advtext."
        )
    print(f"advtext imports from {installed}, identical to {ref}")
    return 0


def wheel_facts() -> tuple[str, str, str]:
    """
    The wheel a child interpreter imports: its version, its Dear ImGui's, its module file.

    The module file is what the library is linked against, by name and with a
    run path to its directory, so it is the one thing the library cannot outlive.
    """
    code = (
        "import imgui_bundle; from imgui_bundle import imgui; "
        "print(imgui_bundle.__version__); print(imgui.get_version()); "
        "print(imgui_bundle._imgui_bundle.__file__)"
    )
    asked = subprocess.run(
        [sys.executable, "-P", "-B", "-c", code], text=True, capture_output=True, check=False
    )
    if asked.returncode != 0:
        detail = (asked.stderr.strip().splitlines() or ["no message"])[-1]
        raise DepsError(f"imgui-bundle does not import in {sys.executable} ({detail})")
    wheel, imgui, module = asked.stdout.splitlines()
    return wheel, imgui, module


def _run_build(command: list[str], env: dict[str, str]) -> tuple[int, str | None]:
    """
    Run the build script with its output passed through; its status and headers label.
    """
    label: str | None = None
    with subprocess.Popen(
        command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as build:
        assert build.stdout is not None
        for line in build.stdout:
            sys.stdout.write("    " + line)
            sys.stdout.flush()
            found = _BUILT.match(line)
            if found:
                label = found["label"]
    return build.returncode, label


def build_library(deps: Path) -> str:
    """
    Build the pair into `deps/imtypeset`; the headers it was compiled against.

    Made in `imtypeset.building` and renamed into place whole, so a build that
    fails at any step leaves the pair that was there as it was.
    """
    if platform.system() != "Linux":
        raise DepsError(
            f"the imtypeset commit pinned here has a build recipe for Linux only, and this is "
            f"{platform.system()}"
        )
    cxx = os.environ.get("CXX") or "g++"
    if shutil.which(cxx) is None:
        raise DepsError(f"there is no C++ compiler ({cxx} is not on PATH; CXX=... names another)")

    ref = read_ref(ROOT / "imtypeset.ref")
    wheel, imgui, module = wheel_facts()
    source = deps / "imtypeset-src"
    fetch_commit(IMTYPESET, ref, source)
    if not (source / BUILD_SCRIPT).is_file():
        raise DepsError(f"imtypeset {ref} has no {BUILD_SCRIPT}")
    bundle = fetch_tag(IMGUI_BUNDLE, f"v{wheel}", deps / "imgui_bundle")
    upstream = fetch_tag(IMGUI, f"v{imgui}-docking", deps / "imgui")

    stage = deps / "imtypeset.building"
    final = deps / "imtypeset"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(
        source / "python" / "imtypeset",
        stage / "pysnap" / "imtypeset",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    env = dict(
        os.environ,
        CXX=cxx,
        IMTYPESET_BUNDLE_OUT=str(stage),
        IMTYPESET_IMGUI_CLONE=str(upstream),
    )
    status, label = _run_build(["sh", str(source / BUILD_SCRIPT), str(bundle), sys.executable], env)
    library = stage / LIBRARY_NAME
    if status != 0 or not library.is_file():
        raise DepsError(
            f"imtypeset's build script ended with status {status} (its own output is above; "
            f"what it left is in {stage})"
        )
    # The copy of the package against the new library, before either is put in
    # place: a pair that does not load is never the installed one.
    loads = subprocess.run(
        [
            sys.executable,
            "-P",
            "-B",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import imtypeset",
            str(stage / "pysnap"),
        ],
        env=dict(os.environ, IMTYPESET_LIBRARY=str(library)),
        text=True,
        capture_output=True,
        check=False,
    )
    if loads.returncode != 0:
        detail = (loads.stderr.strip().splitlines() or ["no message"])[-1]
        raise DepsError(f"the library was built and its own package refuses it ({detail})")
    # The gate's stamp names the library by its staging path.
    (stage / (LIBRARY_NAME + ".gate")).unlink(missing_ok=True)

    headers = label or "unknown (the build script did not say)"
    (stage / "BUILT-FROM").write_text(
        f"imtypeset: {IMTYPESET.repo()}\n"
        f"commit: {ref}\n"
        f"for: {sys.executable}\n"
        f"wheel: imgui-bundle {wheel} (Dear ImGui {imgui})\n"
        f"module: {module}\n"
        f"headers: {headers}\n"
        f"bundle: {bundle}\n"
        f"date: {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}\n",
        encoding="utf-8",
    )
    shutil.rmtree(final, ignore_errors=True)
    stage.rename(final)
    return headers


def step_library(deps: Path) -> int:
    final = deps / "imtypeset"
    try:
        headers = build_library(deps)
    except DepsError as error:
        kept = (
            f" The pair already in {final} was left as it was."
            if (final / LIBRARY_NAME).is_file()
            else ""
        )
        print(
            f"\nThe imtypeset library was NOT built: {error}. pptmstr does not need it to "
            f"run: without it advtext cannot draw, so typeset panes fall back, and nothing "
            f"else is affected.{kept} Once the cause is dealt with, `make deps` builds it "
            f"without repeating the rest of bootstrap.\n",
            file=sys.stderr,
        )
        return 0
    kind = (
        "the wheel's own Dear ImGui headers (the fork)"
        if headers.startswith("fork:")
        else "upstream Dear ImGui's headers as a STAND-IN for the wheel's fork, which Dear "
        "ImGui's own layout check refuses and imtypeset accepts on its field probe"
    )
    print(f"imtypeset built against {headers}: {kind}.")
    for line in (final / "BUILT-FROM").read_text(encoding="utf-8").splitlines():
        if line.startswith("module: "):
            print(
                f"    linked against {line.removeprefix('module: ')} and for that file "
                f"alone: beside another imgui-bundle binary the library is refused and "
                f"advtext.typesetter.reason() says so."
            )
    print(f"    IMTYPESET_LIBRARY={final / LIBRARY_NAME}")
    print(f"    IMTYPESET_PYTHON={final / 'pysnap'}")
    print("    (the Makefile exports both whenever this pair exists)")
    return 0


STEPS = {"advtext": step_advtext, "verify": step_verify, "library": step_library}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("step", choices=sorted(STEPS))
    parser.add_argument("--deps", type=Path, default=DEPS, help="where to build (default: .deps)")
    args = parser.parse_args(argv)
    try:
        return STEPS[args.step](args.deps.resolve())
    except DepsError as error:
        print(f"typeset_deps {args.step}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
