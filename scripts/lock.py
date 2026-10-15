#!/usr/bin/env python3
"""
The release lock: written by uv on a machine with the network, checked offline.

  lock.py lock [--upgrade] [--uv PATH]   resolve both lock files (`make lock`)
  lock.py check [--if-present]           they agree with pyproject.toml (`make lock-check`)
  lock.py covers                         this platform is one the lock covers (bootstrap.sh)
  lock.py subset LOCK NAME...            LOCK's entries for NAME..., hashes and all

Two files at the root of the checkout, both requirements files that plain pip
installs with --require-hashes, so the install side needs no uv:

  requirements-dev.lock   pyproject.toml's dependencies, its `dev` extra, and the
                          build backend an editable install of pptmstr needs.
                          What bootstrap.sh installs into a checkout's .venv.
  requirements.lock       pyproject.toml's dependencies only, resolved with the
                          dev lock's versions as constraints: a subset of it,
                          version for version and hash for hash. What a user's
                          install takes.

The dev lock is resolved first because it is the environment the suite runs in;
a constraint can only narrow, so the runtime lock is then exactly the versions
that were tested and cannot be a second, independent resolve.

The resolve is universal (one file for every supported target, with markers
where the versions fork) and limited to the targets by `[tool.uv]` in
pyproject.toml. `make lock` without --upgrade keeps every version the current
lock holds that still satisfies pyproject.toml, as uv does with an existing
output file; UPGRADE=1 takes the newest.

`lock` and `covers` are stdlib-only, because `covers` runs with the interpreter
bootstrap.sh was given before any virtualenv exists. `check` needs `packaging`.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_LOCK = "requirements.lock"
DEV_LOCK = "requirements-dev.lock"
# Inside the checkout and at a fixed name, because uv writes the path of each
# source into the lock's "# via" annotations: a fresh temporary name per run
# would change the lock on every run.
WORK_DIR = ".lock-work"
BUILD_INPUT = "build-system.in"

# The one compatible unit (pyproject.toml): three exact pins that move together.
UNIT = ("imgui-bundle", "imscribe", "imtypeset")
# hatchling's get_requires_for_build_editable asks for `editables` on top of the
# [build-system] requirements (hatchling 1.32.4, build.py), and the dev lock is
# installed with --no-build-isolation, so both have to be in it.
EDITABLE_EXTRA = ("editables",)


def _probe() -> ModuleType:
    """
    scripts/probe.py, which knows the platform's facts a marker cannot test.
    """
    if "probe" not in sys.modules:
        spec = importlib.util.spec_from_file_location("probe", Path(__file__).parent / "probe.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["probe"] = module
        spec.loader.exec_module(module)
    return sys.modules["probe"]


# What the lock covers, as marker environments. pyproject.toml's
# [tool.uv].environments says the same in markers, and probe.install_outlook's
# LIBRARY verdict says it from the platform's facts; tests/test_lock.py holds the
# three to each other. A marker cannot test glibc 2.28 or macOS 14; the probe can.
# The Pythons are the probe's IMTYPESET_PYTHONS (pairing's, held equal by
# tests/test_probe.py): the lock covers no interpreter the pinned library does
# not install on.
TARGET_PLATFORMS = (
    {"sys_platform": "linux", "platform_system": "Linux", "platform_machine": "x86_64"},
    {"sys_platform": "darwin", "platform_system": "Darwin", "platform_machine": "arm64"},
)
_LOW, _HIGH = _probe().IMTYPESET_PYTHONS
TARGET_PYTHONS = tuple(f"{_LOW[0]}.{minor}" for minor in range(_LOW[1], _HIGH[1] + 1))
COVERAGE = _probe().library_coverage()

DEFAULT_INDEX = "https://pypi.org/simple/"
_HASH = re.compile(r"sha256:[0-9a-f]{64}")


class Refusal(Exception):
    """
    A reason `make lock` will not run, as the one sentence it prints.
    """


# --- the lock file, read ------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    name: str  # canonical: lower case, runs of -_. as one -
    version: str
    marker: str | None
    hashes: tuple[str, ...]
    line: str  # the requirement as written, before its hashes


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def logical_lines(text: str) -> list[str]:
    """
    The lines of a requirements file with continuations joined and comments dropped.
    """
    out: list[str] = []
    pending = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].strip() + " "
            continue
        pending += stripped
        if pending.strip():
            out.append(pending.strip())
        pending = ""
    if pending.strip():
        out.append(pending.strip())
    return out


def parse_lock(text: str, label: str) -> tuple[list[Entry], list[str]]:
    """
    The entries of a lock and what is wrong with its shape, one sentence each.
    """
    from packaging.requirements import InvalidRequirement, Requirement

    entries: list[Entry] = []
    problems: list[str] = []
    for line in logical_lines(text):
        head, _, _ = line.partition(" --hash=")
        head = head.strip()
        hashes = tuple(re.findall(r"--hash=(\S+)", line))
        options = [t for t in line.split() if t.startswith("-") and not t.startswith("--hash=")]
        if head.startswith("-") or options:
            problems.append(f"{label} carries an option, which a lock must not: {line}")
            continue
        try:
            req = Requirement(head)
        except InvalidRequirement as bad:
            problems.append(f"{label} has a line that is not a requirement ({bad}): {head}")
            continue
        specs = list(req.specifier)
        if req.url or len(specs) != 1 or specs[0].operator != "==" or "*" in specs[0].version:
            problems.append(f"{label} has a requirement that is not one exact version: {head}")
            continue
        name = canonical(req.name)
        if not hashes:
            problems.append(f"{label} has no hash for {name}=={specs[0].version}")
        for digest in hashes:
            if not _HASH.fullmatch(digest):
                problems.append(f"{label} has a hash for {name} that is not sha256: {digest}")
        entries.append(
            Entry(name, specs[0].version, str(req.marker) if req.marker else None, hashes, head)
        )
    if not entries and not problems:
        problems.append(f"{label} names no package")
    return entries, problems


# --- the lock file, held to pyproject.toml ------------------------------------


def target_environments() -> list[dict[str, str]]:
    """
    One marker environment for each target platform and Python the lock covers.
    """
    envs = []
    for platform_env in TARGET_PLATFORMS:
        for python in TARGET_PYTHONS:
            envs.append(
                {
                    "implementation_name": "cpython",
                    "platform_python_implementation": "CPython",
                    "os_name": "posix",
                    "python_version": python,
                    "python_full_version": f"{python}.0",
                    "implementation_version": f"{python}.0",
                    "platform_release": "",
                    "platform_version": "",
                    "extra": "",
                    **platform_env,
                }
            )
    return envs


def _describe(env: Mapping[str, str]) -> str:
    return f"{env['sys_platform']} {env['platform_machine']} CPython {env['python_version']}"


def _applies(marker: str | None, env: Mapping[str, str]) -> bool:
    from packaging.markers import Marker

    return marker is None or Marker(marker).evaluate(dict(env))


def requirements_of(pyproject: Mapping[str, object]) -> tuple[list[str], list[str], list[str]]:
    """
    pyproject.toml's runtime requirements, its `dev` extra, and what building an editable needs.
    """
    project = pyproject.get("project", {})
    assert isinstance(project, dict)
    runtime = list(project.get("dependencies", []))
    dev = list(project.get("optional-dependencies", {}).get("dev", []))
    system = pyproject.get("build-system", {})
    assert isinstance(system, dict)
    build = [*system.get("requires", []), *EDITABLE_EXTRA]
    return runtime, dev, build


def check_against(
    entries: Sequence[Entry], required: Iterable[str], label: str, source: str
) -> list[str]:
    """
    Each requirement is in the lock, at a version it allows, on every target it applies to.
    """
    from packaging.requirements import Requirement

    problems: list[str] = []
    by_name: dict[str, list[Entry]] = {}
    for entry in entries:
        by_name.setdefault(entry.name, []).append(entry)
    for text in required:
        req = Requirement(text)
        name = canonical(req.name)
        found = by_name.get(name, [])
        if not found:
            problems.append(f"{name} is required by {source} and is not in {label}")
            continue
        for entry in found:
            if not req.specifier.contains(entry.version, prereleases=True):
                problems.append(
                    f"{label} has {name}=={entry.version}, which {source}'s "
                    f"{req.specifier} does not allow"
                )
        wanted = str(req.marker) if req.marker else None
        for env in target_environments():
            if _applies(wanted, env) and not any(_applies(e.marker, env) for e in found):
                problems.append(
                    f"{label} has no {name} for {_describe(env)}, where {source} requires it"
                )
    return problems


def check_unit(pyproject: Mapping[str, object], locks: Mapping[str, Sequence[Entry]]) -> list[str]:
    """
    The unit's three are pinned exactly in pyproject.toml, and at that version in each lock.
    """
    from packaging.requirements import Requirement

    runtime, _, _ = requirements_of(pyproject)
    pins: dict[str, str] = {}
    for text in runtime:
        req = Requirement(text)
        specs = list(req.specifier)
        if len(specs) == 1 and specs[0].operator == "==":
            pins[canonical(req.name)] = specs[0].version
    problems = []
    for name in UNIT:
        if name not in pins:
            problems.append(
                f"pyproject.toml no longer pins {name} exactly, and it is one of {UNIT}"
            )
            continue
        for label, entries in locks.items():
            versions = {e.version for e in entries if e.name == name}
            if versions != {pins[name]}:
                said = ", ".join(sorted(versions)) or "nothing"
                problems.append(f"{label} has {name} at {said}, and pptmstr pins {pins[name]}")
    return problems


def check_layers(runtime: Sequence[Entry], dev: Sequence[Entry]) -> list[str]:
    """
    The runtime lock is the dev lock's versions and hashes, for the packages it has.
    """

    def index(entries: Sequence[Entry]) -> dict[str, tuple[set[str], set[str]]]:
        out: dict[str, tuple[set[str], set[str]]] = {}
        for e in entries:
            versions, hashes = out.setdefault(e.name, (set(), set()))
            versions.add(e.version)
            hashes.update(e.hashes)
        return out

    problems = []
    dev_index = index(dev)
    for name, (versions, hashes) in sorted(index(runtime).items()):
        if name not in dev_index:
            problems.append(f"{RUNTIME_LOCK} has {name}, which {DEV_LOCK} does not")
            continue
        dev_versions, dev_hashes = dev_index[name]
        if versions != dev_versions:
            problems.append(
                f"{name} is {', '.join(sorted(versions))} in {RUNTIME_LOCK} and "
                f"{', '.join(sorted(dev_versions))} in {DEV_LOCK}"
            )
        elif hashes != dev_hashes:
            problems.append(f"{name} has different hashes in {RUNTIME_LOCK} and {DEV_LOCK}")
    return problems


def check_locks(
    pyproject: Mapping[str, object], runtime_text: str, dev_text: str, project: str = "pptmstr"
) -> list[str]:
    """
    Everything `make lock-check` holds the two lock files to; empty when they agree.
    """
    runtime_req, dev_req, build_req = requirements_of(pyproject)
    runtime, problems = parse_lock(runtime_text, RUNTIME_LOCK)
    dev, dev_problems = parse_lock(dev_text, DEV_LOCK)
    problems += dev_problems
    for label, entries in ((RUNTIME_LOCK, runtime), (DEV_LOCK, dev)):
        if any(e.name == canonical(project) for e in entries):
            problems.append(f"{label} has {project} itself, which is installed from the checkout")
    problems += check_against(runtime, runtime_req, RUNTIME_LOCK, "pyproject.toml")
    problems += check_against(dev, runtime_req, DEV_LOCK, "pyproject.toml")
    problems += check_against(dev, dev_req, DEV_LOCK, "pyproject.toml's dev extra")
    problems += check_against(dev, build_req, DEV_LOCK, "an editable build of pptmstr")
    problems += check_unit(pyproject, {RUNTIME_LOCK: runtime, DEV_LOCK: dev})
    problems += check_layers(runtime, dev)
    return problems


def run_check(root: Path, if_present: bool) -> int:
    runtime_path, dev_path = root / RUNTIME_LOCK, root / DEV_LOCK
    present = [p.name for p in (runtime_path, dev_path) if p.is_file()]
    if not present and if_present:
        print(f"lock-check: there is no release lock yet ({DEV_LOCK}); `make lock` writes one")
        return 0
    if len(present) != 2:
        missing = sorted({RUNTIME_LOCK, DEV_LOCK} - set(present))
        print(f"lock-check: {' and '.join(missing)} missing; `make lock` writes both")
        return 1
    with (root / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    problems = check_locks(
        pyproject,
        runtime_path.read_text(encoding="utf-8"),
        dev_path.read_text(encoding="utf-8"),
    )
    for problem in problems:
        print(f"lock-check: {problem}")
    if problems:
        return 1
    print(f"lock-check: {RUNTIME_LOCK} and {DEV_LOCK} agree with pyproject.toml")
    return 0


# --- the platform -------------------------------------------------------------


def covers_refusal(verdict: str, sentence: str) -> str | None:
    """
    None where the lock covers the platform the probe described, else the sentence why not.

    The lock covers exactly where the install brings the library (LIBRARY). Where
    it installs without it (Linux aarch64) the lock has no entries to install
    from, so that is a refusal here, and `--unlocked` is the way on.
    """
    if verdict == _probe().LIBRARY:
        return None
    return (
        f"the release lock covers {COVERAGE}, and this is not one of those ({sentence}); "
        f"./bootstrap.sh --unlocked installs from pyproject.toml's floors instead"
    )


def run_covers() -> int:
    probe = _probe()
    outlook = probe.install_outlook(probe.platform_facts())
    refusal = covers_refusal(outlook.verdict, outlook.sentence)
    if refusal is not None:
        print(f"    {refusal}")
        return 1
    return 0


def run_subset(lock: Path, names: Sequence[str]) -> int:
    """
    The logical lines of `lock` for each of `names` (a name, or name==version).
    """
    wanted = {canonical(n.split("==", 1)[0]) for n in names}
    found = set()
    for line in logical_lines(lock.read_text(encoding="utf-8")):
        head = re.split(r"[\s=<>!~;\[]", line, maxsplit=1)[0]
        if canonical(head) in wanted:
            found.add(canonical(head))
            print(line)
    missing = wanted - found
    if missing:
        print(f"{lock.name} has no {', '.join(sorted(missing))}", file=sys.stderr)
        return 1
    return 0


# --- the resolve --------------------------------------------------------------


def index_url(environ: Mapping[str, str]) -> str:
    """
    The index uv will resolve against, as uv chooses it from the environment.
    """
    for variable in ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "PIP_INDEX_URL"):
        if environ.get(variable):
            return environ[variable]
    return DEFAULT_INDEX


def reachable(url: str, timeout: float = 15.0) -> str | None:
    """
    None when `url` answers at all; otherwise why not.
    """
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=timeout):
            return None
    except urllib.error.HTTPError:
        return None  # it answered
    except (urllib.error.URLError, OSError, ValueError) as why:
        return str(getattr(why, "reason", why))


def compile_command(
    uv: str,
    *,
    output: str,
    sources: Sequence[str],
    extras: Sequence[str] = (),
    constraints: str | None = None,
    upgrade: bool = False,
) -> list[str]:
    """
    One `uv pip compile` of the release lock.

    --universal: one file for every target, restricted to them by
    [tool.uv].environments, which `uv pip compile --universal` reads, and held to a
    wheel for each by [tool.uv].required-environments. --only-binary :all:,
    because the install claims nothing is compiled: a package with no wheel fails
    the resolve here, by name, instead of building at install. No index URL is
    written (uv's default), so the lock does not carry a machine's index setting.
    """
    command = [uv, "pip", "compile", *sources, "--universal", "--generate-hashes"]
    command += ["--only-binary", ":all:", "--custom-compile-command", "make lock"]
    for extra in extras:
        command += ["--extra", extra]
    if constraints is not None:
        command += ["--constraints", constraints]
    if upgrade:
        command.append("--upgrade")
    command += ["--output-file", output]
    return command


def constraints_from(text: str) -> str:
    """
    A lock as constraints: each requirement with its marker and without its hashes.
    """
    lines = [line.partition(" --hash=")[0].strip() for line in logical_lines(text)]
    return "".join(f"{line}\n" for line in lines)


def run_lock(root: Path, uv: str, upgrade: bool, environ: Mapping[str, str]) -> int:
    try:
        return _lock(root, uv, upgrade, environ)
    except Refusal as refusal:
        print(f"make lock: {refusal}")
        return 1


def _lock(root: Path, uv: str, upgrade: bool, environ: Mapping[str, str]) -> int:
    found = shutil.which(uv)
    if found is None:
        raise Refusal(
            f"there is no uv ({uv!r} is not on PATH), and the lock is resolved by uv; "
            f"install it, or name one: make lock UV=/path/to/uv"
        )
    url = index_url(environ)
    why = reachable(url)
    if why is not None:
        raise Refusal(
            f"the package index cannot be reached ({url}: {why}), and the resolve needs it"
        )
    version = subprocess.run([found, "--version"], capture_output=True, text=True).stdout.strip()
    print(f"make lock: resolving with {version or found} against {url}")

    with (root / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)
    _, _, build = requirements_of(pyproject)
    work = root / WORK_DIR
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    try:
        (work / BUILD_INPUT).write_text("".join(f"{r}\n" for r in build), encoding="utf-8")
        dev_out, runtime_out = work / DEV_LOCK, work / RUNTIME_LOCK
        # The current lock is the preference: uv keeps a version an existing
        # output file holds unless --upgrade.
        for name, out in ((DEV_LOCK, dev_out), (RUNTIME_LOCK, runtime_out)):
            if (root / name).is_file():
                shutil.copy2(root / name, out)
        env = dict(environ)
        # uv's documented minimum for a macOS target is 13.0 unless this says
        # otherwise; every wheel the unit publishes is macosx_14_0_arm64.
        env.setdefault("MACOSX_DEPLOYMENT_TARGET", "14.0")
        dev_command = compile_command(
            found,
            output=f"{WORK_DIR}/{DEV_LOCK}",
            sources=["pyproject.toml", f"{WORK_DIR}/{BUILD_INPUT}"],
            extras=["dev"],
            upgrade=upgrade,
        )
        if subprocess.run(dev_command, cwd=root, env=env).returncode != 0 or not dev_out.is_file():
            raise Refusal(f"uv could not resolve {DEV_LOCK}; what it said is above")
        constraints = work / "constraints.txt"
        constraints.write_text(
            constraints_from(dev_out.read_text(encoding="utf-8")), encoding="utf-8"
        )
        runtime_command = compile_command(
            found,
            output=f"{WORK_DIR}/{RUNTIME_LOCK}",
            sources=["pyproject.toml"],
            constraints=f"{WORK_DIR}/constraints.txt",
            upgrade=upgrade,
        )
        done = subprocess.run(runtime_command, cwd=root, env=env)
        if done.returncode != 0 or not runtime_out.is_file():
            raise Refusal(
                f"uv could not resolve {RUNTIME_LOCK} within {DEV_LOCK}'s versions; "
                f"what it said is above, and neither lock was replaced"
            )
        problems = check_locks(
            pyproject,
            runtime_out.read_text(encoding="utf-8"),
            dev_out.read_text(encoding="utf-8"),
        )
        for problem in problems:
            print(f"make lock: {problem}")
        if problems:
            raise Refusal("what uv wrote fails lock-check (above), so neither lock was replaced")
        # Both or neither: a runtime lock from this resolve beside a dev lock from
        # the last one would be two resolves, which is what the layering forbids.
        shutil.copy2(dev_out, root / DEV_LOCK)
        shutil.copy2(runtime_out, root / RUNTIME_LOCK)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"make lock: wrote {DEV_LOCK} and {RUNTIME_LOCK}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", required=True)
    lock = sub.add_parser("lock")
    lock.add_argument("--upgrade", action="store_true")
    lock.add_argument("--uv", default="uv")
    check = sub.add_parser("check")
    check.add_argument("--if-present", action="store_true")
    sub.add_parser("covers")
    subset = sub.add_parser("subset")
    subset.add_argument("lock", type=Path)
    subset.add_argument("names", nargs="+")
    args = ap.parse_args(argv)
    if args.command == "lock":
        return run_lock(args.root, args.uv, args.upgrade, os.environ)
    if args.command == "check":
        return run_check(args.root, args.if_present)
    if args.command == "covers":
        return run_covers()
    return run_subset(args.lock, args.names)


if __name__ == "__main__":
    raise SystemExit(main())
