#!/usr/bin/env python3
"""
imscribe at the commit `imscribe.ref` names, and the imtypeset library for this interpreter.

Stdlib only, and run by the interpreter the result is for (`.venv/bin/python`):
`bootstrap.sh` and `make deps` are the two callers. Everything it makes is under
`.deps/`, which is ignored:

    .deps/imscribe/           a clone checked out at the commit in imscribe.ref
    .deps/imtypeset-src/      a clone checked out at the commit in imtypeset.ref
    .deps/imgui_bundle/       objects only: Dear ImGui Bundle's tag for the wheel
    .deps/imgui/              objects only: Dear ImGui's docking tag for the wheel
    .deps/imtypeset/          the pair a process is pointed at:
        libimtypeset_bundle.so    IMTYPESET_LIBRARY
        pysnap/                   IMTYPESET_PYTHON (holds the `imtypeset` package)
        BUILT-FROM                what it was built from, and against which headers
        libimtypeset_bundle.so.attestation.json
                                  the build's record that its full probe accepted this
                                  library beside this wheel, by the content of both
                                  files; `import imtypeset` holds the library to it

Three steps that make it, because the install between the first two belongs to
the caller, which knows whether the installer is pip or uv, and one that reads it:

    imscribe  fetch and check out the pinned commit, then take what an install
              under the library's earlier name left out of this interpreter.
              Failing here fails the caller.
    verify    the clone is at the commit imscribe.ref names, unedited, the
              `imscribe` this interpreter imports is that clone's, file for file,
              and nothing imports under the earlier name.
    library   build the pair. Never fails the caller: without the library pptmstr
              still runs and typeset panes fall back, so a missing compiler is a
              paragraph on standard error and exit status 0.
    pair      one line for the Makefile: `ok` when the pair is whole and was built
              from the commit imtypeset.ref names, and otherwise why it is not.

Where each source comes from is a variable holding anything `git fetch` accepts,
GitHub by default:

    IMSCRIBE_REPO       https://github.com/WalterCReel3/imgui-adv-text.git
    IMTYPESET_REPO      https://github.com/WalterCReel3/imtypeset.git
    IMGUI_BUNDLE_REPO   https://github.com/pthom/imgui_bundle.git
    IMGUI_REPO          https://github.com/ocornut/imgui.git

The last two are read by imtypeset's build script: the first for the bundle's
ImGui configuration header, the second only when stand-in headers are asked
for. Either may instead name a local checkout, which is then read in place and
not copied.

Which Dear ImGui headers the library is compiled against is imtypeset's own
choice of two, and its two variables are passed through as they are set:

    IMTYPESET_BUNDLE_HEADERS     fork (the default): the wheel's own headers,
                                 from https://github.com/pthom/imgui.git at the
                                 commit imtypeset records for the wheel's
                                 version. The correct build. When they cannot
                                 be fetched nothing is built: neither this
                                 script nor imtypeset's substitutes others.
                                 stand-in: upstream Dear ImGui's docking tag of
                                 the same version, from IMGUI_REPO. Dear
                                 ImGui's own layout check refuses such a
                                 library and imtypeset accepts it on its field
                                 probe alone.
    IMTYPESET_IMGUI_FORK_SOURCE  another copy of the fork, a URL or a local
                                 repository, in place of GitHub's.

BUILT-FROM records which headers were used, `pptmstr --version` prints it, and
so does the last line of the build.

The library is linked against one installed `imgui-bundle` binary and is for
that interpreter alone, which is why `pyproject.toml` pins the wheel exactly.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The rule for reading a ref file and for which pair a process may be handed is
# the package's, which applies it to itself at start-up; `pptmstr.pairing` is
# standard library only, so it imports in a .venv that holds nothing yet.
sys.path.insert(0, str(ROOT))
from pptmstr import pairing  # noqa: E402
from pptmstr.pairing import (  # noqa: E402
    BUILT_FROM,
    LIBRARY_NAME,
    package_differences,
)
from pptmstr.pairing import PinError as DepsError  # noqa: E402

DEPS = ROOT / pairing.DEPS_DIR

BUILD_SCRIPT = Path("python") / "host" / "build_bundle_library.sh"
PACKAGE = Path("python") / "imtypeset"

# imtypeset's own variables for which headers it compiles against, and what the
# build script leaves in its output directory: HEADERS once it has headers
# (`dir=`, `label=`), BUILD once it has linked (`headers=`, `bind=`, key=value).
HEADERS_VARIABLE = "IMTYPESET_BUNDLE_HEADERS"
FORK_VARIABLE = "IMTYPESET_IMGUI_FORK_SOURCE"
FORK, STAND_IN = "fork", "stand-in"
HEADERS_FILE = "HEADERS"
BUILD_FILE = "BUILD"


@dataclass(frozen=True, slots=True)
class Source:
    """
    One repository: the variable that names it and where it is when nothing does.
    """

    variable: str
    default: str

    def repo(self) -> str:
        return os.environ.get(self.variable) or self.default


IMSCRIBE = Source("IMSCRIBE_REPO", "https://github.com/WalterCReel3/imgui-adv-text.git")
IMTYPESET = Source("IMTYPESET_REPO", "https://github.com/WalterCReel3/imtypeset.git")
IMGUI_BUNDLE = Source("IMGUI_BUNDLE_REPO", "https://github.com/pthom/imgui_bundle.git")
IMGUI = Source("IMGUI_REPO", "https://github.com/ocornut/imgui.git")

# The name the library was distributed and imported under up to the commit
# before cd437c1 of its repository. An environment made on that side of it holds
# a distribution an installer does not know is the same project, so installing
# imscribe leaves it importable, and a module that still imported the old name
# would run there and nowhere else.
EARLIER_NAME = "advtext"
EARLIER_VARIABLES = {
    "ADVTEXT_REPO": IMSCRIBE.variable,
    "ADVTEXT_PIP_ARGS": "IMSCRIBE_PIP_ARGS",
}


def read_ref(path: Path) -> str:
    """
    The commit a ref file names (`pptmstr.pairing.read_ref`).
    """
    return pairing.read_ref(path)


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


def _imported(name: str) -> Path | None:
    """
    The directory `import <name>` would load in this interpreter, without importing it.
    """
    importlib.invalidate_caches()
    spec = importlib.util.find_spec(name)
    if spec is None:
        return None
    if spec.origin is not None:
        return Path(spec.origin).resolve().parent
    # A directory with no `__init__.py` imports too, as a namespace package.
    places = list(spec.submodule_search_locations or ())
    return Path(places[0]).resolve() if places else None


def imported_imscribe() -> Path | None:
    """
    The directory `import imscribe` would load in this interpreter, or None.
    """
    return _imported("imscribe")


def imported_earlier_name() -> Path | None:
    """
    The directory an import under the library's earlier name would load, or None.
    """
    return _imported(EARLIER_NAME)


def unread_variables(environ: Mapping[str, str]) -> str | None:
    """
    One line naming each variable of the earlier name that is set, and what is read instead.
    """
    told = [
        f"{old} is set and is not read ({new} is)"
        for old, new in EARLIER_VARIABLES.items()
        if environ.get(old)
    ]
    return f"note: {'; '.join(told)}." if told else None


def own_site_directories() -> set[Path]:
    """
    Where this interpreter's environment installs packages: the only places an install here wrote.
    """
    return {Path(sysconfig.get_path(kind)).resolve() for kind in ("purelib", "platlib")}


def remove_earlier_name(deps: Path) -> list[Path]:
    """
    Take the distribution under the earlier name out of this environment; what was removed.

    What an installer's own uninstall does, by the distribution's record, since
    a .venv uv made has no pip to ask: every file the record lists, the package
    directory, the metadata directory. **Only in this environment's own site
    directories.** sys.path can reach another environment's (a .pth file naming
    it), and a distribution found there is that environment's: it is left, and
    so is a copy that is no distribution at all (a directory on PYTHONPATH).
    `verify` refuses whichever of them still imports. The clone under `deps`
    goes too: nothing reads it, and it is what the old distribution's record
    names as its source.
    """
    removed: list[Path] = []
    own = own_site_directories()
    for distribution in importlib.metadata.distributions(name=EARLIER_NAME):
        site = Path(str(distribution.locate_file(""))).resolve()
        if site not in own:
            continue
        listed = [Path(str(distribution.locate_file(file))) for file in distribution.files or ()]
        files = [
            path
            for path in listed
            if site in path.resolve().parents and (path.is_file() or path.is_symlink())
        ]
        left = [
            path
            for path in (site / EARLIER_NAME, *site.glob(f"{EARLIER_NAME}-*.dist-info"))
            if path.is_dir()
        ]
        # A directory listing importlib.metadata remembers can name a
        # distribution that is no longer on disk; that one was not removed now.
        if not files and not left:
            continue
        for path in files:
            path.unlink()
        for path in left:
            shutil.rmtree(path)
        removed.append(site / EARLIER_NAME)
    clone = deps / EARLIER_NAME
    if clone.is_dir():
        shutil.rmtree(clone)
        removed.append(clone)
    importlib.invalidate_caches()
    return removed


def step_imscribe(deps: Path) -> int:
    unread = unread_variables(os.environ)
    if unread:
        print(unread, file=sys.stderr)
    ref = read_ref(ROOT / "imscribe.ref")
    clone = deps / "imscribe"
    fetch_commit(IMSCRIBE, ref, clone)
    print(f"imscribe {ref} from {IMSCRIBE.repo()} -> {clone}")
    for gone in remove_earlier_name(deps):
        print(f"removed {gone}: the library under its earlier name, {EARLIER_NAME}")
    return 0


def clone_is_at(clone: Path, ref: str) -> None:
    """
    Raise unless `clone` is checked out at `ref` with no Python file of its package edited.

    What `verify` compares the import against is the clone's working tree, so the
    tree is only evidence about `ref` while it is that commit's and nothing else.
    """
    # Its own `.git`, or git would answer for whichever repository encloses it.
    head = (
        _git("rev-parse", "--verify", "--quiet", "HEAD^{commit}", cwd=clone)
        if (clone / ".git").exists()
        else None
    )
    if head is None or head.returncode != 0:
        raise DepsError(
            f"{clone} is not a checkout of imscribe, so nothing here says which commit is "
            f"installed. `make deps` fetches {ref}, which imscribe.ref names, and installs it."
        )
    at = head.stdout.strip()
    if at != ref:
        raise DepsError(
            f"imscribe.ref names {ref} and {clone} is at {at}: the ref file moved and what is "
            f"installed did not. `make deps` fetches and installs the commit it names now."
        )
    edited = [
        line[3:]
        for line in _git("status", "--porcelain", "--", "imscribe", cwd=clone).stdout.splitlines()
        if line.endswith(".py")
    ]
    if edited:
        raise DepsError(
            f"{clone} is at {ref} with {len(edited)} Python file(s) of the package edited or "
            f"added since, the first {edited[0]}; `make deps` checks the commit out clean."
        )


def step_verify(deps: Path) -> int:
    ref = read_ref(ROOT / "imscribe.ref")
    clone_is_at(deps / "imscribe", ref)
    pinned = deps / "imscribe" / "imscribe"
    installed = imported_imscribe()
    if installed is None:
        raise DepsError(f"imscribe is not installed for {sys.executable}.")
    differing = package_differences(pinned, installed)
    if differing:
        raise DepsError(
            f"{sys.executable} imports imscribe from {installed}, and that is not commit {ref} "
            f"as checked out in {pinned}: {len(differing)} file(s) differ, the first "
            f"{differing[0]}. Something ahead of the installed copy on sys.path (an editable "
            f"install, a .pth file) is supplying another imscribe."
        )
    earlier = imported_earlier_name()
    if earlier is not None:
        raise DepsError(
            f"{sys.executable} can still import {EARLIER_NAME}, from {earlier}: the name imscribe "
            f"had before, left by an earlier install. Nothing here is tested against it, and a "
            f"module that still imported that name would run in this environment and in no "
            f"other. `make deps` removes the distribution of that name; a copy that is not one "
            f"(a directory on PYTHONPATH, a .pth file) is removed by hand."
        )
    print(f"imscribe imports from {installed}, identical to {ref}")
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
    # The last three lines: anything the wheel prints while it is imported comes first.
    answer = asked.stdout.splitlines()[-3:]
    if len(answer) != 3:
        raise DepsError(f"imgui-bundle in {sys.executable} did not say what it is ({answer})")
    wheel, imgui, module = answer
    return wheel, imgui, module


def _run_build(command: list[str], env: dict[str, str]) -> int:
    """
    Run the build script with its output passed through, indented; its status.
    """
    with subprocess.Popen(
        command, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    ) as build:
        assert build.stdout is not None
        for line in build.stdout:
            sys.stdout.write("    " + line)
            sys.stdout.flush()
    return build.returncode


def _facts(path: Path) -> dict[str, str]:
    """
    The `key=value` lines of a file the build script wrote; empty when it is not there.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    return {key: value for key, equals, value in (line.partition("=") for line in lines) if equals}


def headers_asked() -> str:
    """
    Which headers this build is to use: the fork's unless the variable names the stand-in.
    """
    asked = os.environ.get(HEADERS_VARIABLE) or FORK
    if asked not in (FORK, STAND_IN):
        raise DepsError(f"{HEADERS_VARIABLE} is '{FORK}' or '{STAND_IN}', not {asked!r}")
    return asked


def _no_headers(asked: str, status: int) -> str:
    """
    The sentence for a build that ended before it had headers, naming what would change it.
    """
    if asked == STAND_IN:
        return (
            f"the stand-in headers that {HEADERS_VARIABLE}={STAND_IN} asks for could not be "
            f"had (imtypeset's build script ended with status {status}; its reason is above). "
            f"{IMGUI.variable}=... names another copy of upstream Dear ImGui"
        )
    return (
        f"the wheel's own Dear ImGui headers could not be had (imtypeset's build script ended "
        f"with status {status}; its reason is above). They are the fork's, fetched from "
        f"{os.environ.get(FORK_VARIABLE) or 'https://github.com/pthom/imgui.git'}, and nothing "
        f"is put in their place unasked. Either {FORK_VARIABLE}=... names a copy of the fork "
        f"that can be reached, a URL or a local repository; or {HEADERS_VARIABLE}={STAND_IN} "
        f"builds against upstream Dear ImGui's headers instead, which Dear ImGui's own layout "
        f"check refuses and imtypeset accepts on its field probe, and which BUILT-FROM records "
        f"and `pptmstr --version` prints"
    )


def build_library(deps: Path) -> str:
    """
    Build the pair into `deps/imtypeset`; the headers it was compiled against.

    Made in `imtypeset.building` and put in place by `_install`, so a build that
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

    asked = headers_asked()
    ref = read_ref(ROOT / "imtypeset.ref")
    wheel, imgui, module = wheel_facts()
    source = deps / "imtypeset-src"
    fetch_commit(IMTYPESET, ref, source)
    for needed in (BUILD_SCRIPT, PACKAGE / "__init__.py"):
        if not (source / needed).is_file():
            raise DepsError(f"imtypeset {ref} has no {needed}")
    bundle = fetch_tag(IMGUI_BUNDLE, f"v{wheel}", deps / "imgui_bundle")
    # Upstream is the stand-in's source and nothing else's: a build against the
    # fork neither needs it nor fails for want of it.
    upstream = fetch_tag(IMGUI, f"v{imgui}-docking", deps / "imgui") if asked == STAND_IN else None

    stage = deps / "imtypeset.building"
    final = deps / "imtypeset"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(
        source / PACKAGE,
        stage / "pysnap" / "imtypeset",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    env = dict(os.environ, CXX=cxx, IMTYPESET_BUNDLE_OUT=str(stage))
    env[HEADERS_VARIABLE] = asked
    if upstream is not None:
        env["IMTYPESET_IMGUI_CLONE"] = str(upstream)
    status = _run_build(["sh", str(source / BUILD_SCRIPT), str(bundle), sys.executable], env)
    library = stage / LIBRARY_NAME
    if status != 0 and "label" not in _facts(stage / HEADERS_FILE):
        raise DepsError(_no_headers(asked, status))
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

    built = _facts(stage / BUILD_FILE)
    headers = built.get("headers") or "unknown (the build script did not say)"
    if not headers.startswith(asked + ":"):
        raise DepsError(
            f"{asked} headers were asked for and the build script says it used {headers}"
        )
    (stage / BUILT_FROM).write_text(
        f"imtypeset: {IMTYPESET.repo()}\n"
        f"commit: {ref}\n"
        f"for: {sys.executable}\n"
        f"wheel: imgui-bundle {wheel} (Dear ImGui {imgui})\n"
        f"module: {module}\n"
        f"headers: {headers}\n"
        f"bind: {built.get('bind') or 'unknown'}\n"
        f"bundle: {bundle}\n"
        f"date: {datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ}\n",
        encoding="utf-8",
    )
    _install(stage, final)
    return headers


def _install(stage: Path, final: Path) -> None:
    """
    Put the built pair where the old one is, by renames alone.

    The old pair is moved aside whole before the new one is moved in, and moved
    back if that fails, so `final` is at every instant the old pair, no pair, or
    the new one, and never part of either. Deleting comes last, on a directory
    nothing points a process at.
    """
    aside = final.with_name(final.name + ".previous")
    if aside.exists():
        shutil.rmtree(aside)
    had = final.exists()
    if had:
        final.rename(aside)
    try:
        stage.rename(final)
    except OSError:
        if had:
            aside.rename(final)
        raise
    shutil.rmtree(aside, ignore_errors=True)


def pair_refusal(pair: Path) -> str | None:
    """
    Why the pair in `pair` is not to be handed to a process; None when it is.

    `pptmstr.pairing.pair_refusal`, against the commit `imtypeset.ref` names now.
    """
    return pairing.pair_refusal(pair, read_ref(ROOT / "imtypeset.ref"))


def step_pair(pair: Path) -> int:
    """
    Print `ok`, or the one line saying why the Makefile exports nothing.

    Always one line and always status 0: the Makefile reads the line while it is
    being parsed, for every target, and has nowhere to put a failure.
    """
    try:
        refusal = pair_refusal(pair)
    except Exception as error:  # whatever it is, it is the reason
        refusal = f"{pair} could not be checked ({type(error).__name__}: {error})"
    print(" ".join((refusal or "ok").split()))
    return 0


def step_library(deps: Path) -> int:
    final = deps / "imtypeset"
    try:
        headers = build_library(deps)
    except Exception as error:  # the promise is about every failure
        # A DepsError is a sentence already; anything else is named for what it is.
        cause = str(error) if isinstance(error, DepsError) else f"{type(error).__name__}: {error}"
        kept = (
            f" The pair already in {final} was left as it was."
            if (final / LIBRARY_NAME).is_file()
            else ""
        )
        print(
            f"\nThe imtypeset library was NOT built: {cause}. pptmstr does not need it to "
            f"run: without it imscribe cannot draw, so typeset panes fall back, and nothing "
            f"else is affected.{kept} Once the cause is dealt with, `make deps` builds it "
            f"without repeating the rest of bootstrap.\n",
            file=sys.stderr,
        )
        return 0
    kind = (
        "the wheel's own Dear ImGui headers (the fork)"
        if headers.startswith("fork:")
        else f"upstream Dear ImGui's headers as a STAND-IN for the wheel's fork, as "
        f"{HEADERS_VARIABLE}={STAND_IN} asked, which Dear ImGui's own layout check refuses "
        f"and imtypeset accepts on its field probe"
    )
    print(f"imtypeset built against {headers}: {kind}.")
    for line in (final / BUILT_FROM).read_text(encoding="utf-8").splitlines():
        if line.startswith("module: "):
            print(
                f"    linked against {line.removeprefix('module: ')} and for that file "
                f"alone: beside another imgui-bundle binary the library is refused and "
                f"imscribe.typesetter.reason() says so."
            )
    print(f"    IMTYPESET_LIBRARY={final / LIBRARY_NAME}")
    print(f"    IMTYPESET_PYTHON={final / 'pysnap'}")
    print(
        "    (the Makefile exports both while imtypeset.ref names the commit this was built from)"
    )
    return 0


STEPS = {"imscribe": step_imscribe, "verify": step_verify, "library": step_library}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    parser.add_argument("step", choices=sorted([*STEPS, "pair"]))
    parser.add_argument("--deps", type=Path, default=DEPS, help="where to build (default: .deps)")
    parser.add_argument(
        "--pair", type=Path, help="the pair `pair` reads (default: imtypeset under --deps)"
    )
    args = parser.parse_args(argv)
    deps = args.deps.resolve()
    if args.step == "pair":
        return step_pair(args.pair.resolve() if args.pair else deps / "imtypeset")
    try:
        return STEPS[args.step](deps)
    except DepsError as error:
        print(f"typeset_deps {args.step}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
