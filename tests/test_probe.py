"""
`scripts/probe.py`'s decision about what the install does on a platform, and
that bootstrap.sh stops on it before a virtualenv is made.

The decision is held with patched platform facts, because the platforms it is
about -- macOS 13, an Intel Mac, old glibc, musl -- are not the one this runs
on. What it encodes is pip's resolver against the wheels the two projects
publish: imtypeset's marker is on wherever the requirement is ON, and a marker
cannot test the macOS version or the libc, so there the install fails rather
than leaving the library out.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from pptmstr import pairing

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("probe", ROOT / "scripts" / "probe.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def facts(**changes: object) -> object:
    base = dict(
        system="linux",
        machine="x86_64",
        implementation="CPython",
        python=pairing.IMTYPESET_PYTHONS[0],
        macos=None,
        glibc=(2, 36),
    )
    base.update(changes)
    return probe.PlatformFacts(**base)


def mac(version: tuple[int, ...] | None, machine: str = "arm64") -> object:
    return facts(system="darwin", machine=machine, macos=version, glibc=None)


@pytest.mark.parametrize(
    "case",
    [
        mac((14, 0)),
        mac((15, 6, 1)),
        facts(glibc=(2, 28)),
        facts(python=pairing.IMTYPESET_PYTHONS[0]),
        facts(python=pairing.IMTYPESET_PYTHONS[1]),
    ],
)
def test_the_supported_floor_installs_the_library(case: object) -> None:
    assert probe.install_outlook(case).verdict == probe.LIBRARY


# What each imtypeset pin's wheels declare as Requires-Python, as CPython minors:
# 0.4.1.192900 from PyPI's JSON for every one of its eight wheels ("==3.11.*",
# read 2026-10-15); 0.4.2 and later declare ">=3.11,<3.15" (imscribe's H52, relayed).
DECLARED_PYTHONS = {
    "0.4.1.192900": ((3, 11), (3, 11)),
    "0.4.3.192900": ((3, 11), (3, 14)),
}


def test_the_supported_pythons_are_what_the_pinned_imtypeset_declares() -> None:
    """
    The one constant moves with the pin: a pin this table does not know is a
    Requires-Python somebody has to read before the probe can be believed.
    """
    assert pairing.IMTYPESET_PIN in DECLARED_PYTHONS, pairing.IMTYPESET_PIN
    assert pairing.IMTYPESET_PYTHONS == DECLARED_PYTHONS[pairing.IMTYPESET_PIN]


@pytest.mark.parametrize("name", ["IMGUI_BUNDLE_PIN", "IMTYPESET_PIN", "IMTYPESET_PYTHONS"])
def test_the_probe_spells_pairings_pins_and_pythons_alike(name: str) -> None:
    """
    The probe cannot import pptmstr.pairing (``test_the_probe_runs_without_pptmstr``).
    """
    assert getattr(probe, name) == getattr(pairing, name)


def test_the_probe_runs_without_pptmstr(tmp_path: Path) -> None:
    """
    Stage 1 runs under bootstrap's interpreter before any virtualenv: loaded in
    an isolated interpreter whose working directory is empty, the probe answers
    and has imported nothing of pptmstr, although this interpreter could.
    """
    script = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('probe', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "sys.modules['probe'] = module\n"
        "spec.loader.exec_module(module)\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'pptmstr'))\n"
        "print(module.install_outlook(module.platform_facts()).verdict)\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", script, str(ROOT / "scripts" / "probe.py")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0] == "[]"


@pytest.mark.parametrize("minor", [11, 12, 13, 14])
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_the_library_installs_only_on_the_pythons_the_pinned_imtypeset_declares(
    minor: int, platform: str
) -> None:
    """
    imscribe requires imtypeset on CPython 3.11 to 3.14 here, and pip takes no
    wheel whose Requires-Python excludes the interpreter: outside the pin's
    range the whole install fails, and the sentence says so and what to do.
    """
    case = mac((14, 0)) if platform == "darwin" else facts()
    case = probe.PlatformFacts(**{**case.__dict__, "python": (3, minor)})
    outlook = probe.install_outlook(case)
    low, high = pairing.IMTYPESET_PYTHONS
    if low <= (3, minor) <= high:
        assert outlook.verdict == probe.LIBRARY
        assert "install as wheels" in outlook.sentence
        return
    span = probe._span(pairing.IMTYPESET_PYTHONS)
    assert outlook.verdict == probe.UNSUPPORTED
    assert outlook.sentence.startswith(
        f"imtypeset {pairing.IMTYPESET_PIN}, which pptmstr pins, installs only on CPython {span}"
    )
    assert f"imscribe requires it on CPython 3.{minor}, so the whole install fails" in (
        outlook.sentence
    )
    assert f"./bootstrap.sh --python python3.{low[1]}" in outlook.sentence
    assert "a pptmstr that pins imtypeset 0.4.2 or later" in outlook.sentence
    assert "install as wheels" not in outlook.sentence


@pytest.mark.parametrize("minor", [12, 14])
def test_today_cpython_3_12_and_3_14_are_told_the_install_fails(
    minor: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    At imtypeset 0.4.1.192900, the pin this was written against: the DIAGNOSIS
    line of stage 1, which stops bootstrap before a virtualenv, and the
    Supported line names only what does install.
    """
    monkeypatch.setattr(probe, "IMTYPESET_PIN", "0.4.1.192900")
    monkeypatch.setattr(probe, "IMTYPESET_PYTHONS", DECLARED_PYTHONS["0.4.1.192900"])
    monkeypatch.setattr(probe, "platform_facts", lambda: facts(python=(3, minor)))
    r = probe.Report()
    probe.check_install(r)
    out = capsys.readouterr().out
    assert r.fatal and r.fatal[0].startswith(
        "install: pptmstr will not install here: imtypeset 0.4.1.192900, which pptmstr "
        "pins, installs only on CPython 3.11 "
    )
    assert "Run bootstrap with CPython 3.11 (./bootstrap.sh --python python3.11)" in out
    assert out.rstrip().endswith(
        "Supported: CPython 3.11 on macOS 14 or later on Apple Silicon, or on Linux x86-64 "
        "with glibc 2.28 or later"
    )


def test_linux_aarch64_needs_no_imtypeset_on_any_python_imgui_bundle_has() -> None:
    """
    The marker is off there, so the pin's Requires-Python is never asked.
    """
    for minor in (11, 12, 14):
        outlook = probe.install_outlook(facts(machine="aarch64", python=(3, minor)))
        assert outlook.verdict == probe.WITHOUT_LIBRARY, minor


@pytest.mark.parametrize(
    ("case", "named"),
    [
        # imtypeset's marker is on, no wheel matches, there is no sdist: pip fails.
        (mac((13, 6, 1)), "macOS 13.6.1 is below 14"),
        (facts(glibc=(2, 17)), "glibc 2.17 is below 2.28"),
        (facts(glibc=None), "Linux x86-64 without glibc"),
        # The marker is off, and imgui-bundle has no wheel: pip compiles it.
        (mac((14, 2), machine="x86_64"), "no wheel for an Intel Mac"),
        (facts(python=(3, 15)), "CPython 3.15 is outside 3.11 to 3.14"),
        (facts(python=(3, 10)), "CPython 3.10 is outside"),
        (facts(implementation="PyPy"), "PyPy is not CPython"),
        (facts(machine="riscv64"), "Linux on riscv64 is not supported"),
        (facts(machine="aarch64", glibc=(2, 17)), "glibc 2.17 is below 2.28"),
        (mac(None), "macOS version cannot be read"),
    ],
)
def test_a_platform_the_install_fails_on_is_unsupported_and_says_what(
    case: object, named: str
) -> None:
    outlook = probe.install_outlook(case)
    assert outlook.verdict == probe.UNSUPPORTED
    assert named in outlook.sentence


def test_linux_aarch64_installs_without_the_library_and_says_so() -> None:
    for libc in ((2, 28), None):
        outlook = probe.install_outlook(facts(machine="aarch64", glibc=libc))
        assert outlook.verdict == probe.WITHOUT_LIBRARY
        assert "leaves the typesetting library out" in outlook.sentence


def test_an_unsupported_platform_is_the_probes_diagnosis(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    The install check runs first, so a platform pip will fail on is the
    DIAGNOSIS line and stage 1 exits non-zero, which stops bootstrap.sh under
    ``set -e`` before stage 2.
    """
    monkeypatch.setattr(probe, "platform_facts", lambda: facts(glibc=(2, 17)))
    assert probe.run_pre() == 1
    out = capsys.readouterr().out
    assert "DIAGNOSIS: install: pptmstr will not install here: glibc 2.17" in out


def test_glibc_is_read_from_confstr_and_musl_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "confstr", lambda name: "glibc 2.27", raising=False)
    assert probe.glibc_version() == (2, 27)

    def refused(name: str) -> str:
        raise ValueError(name)

    monkeypatch.setattr(os, "confstr", refused, raising=False)
    monkeypatch.setattr(probe.platform, "libc_ver", lambda: ("", ""))
    assert probe.glibc_version() is None
    monkeypatch.setattr(probe.platform, "libc_ver", lambda: ("glibc", "2.31"))
    assert probe.glibc_version() == (2, 31)


def test_a_compatibility_macos_version_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    An interpreter built against an old SDK reads every macOS from 11 on as
    10.16; pip asks again with SYSTEM_VERSION_COMPAT=0, and so does the probe.
    """
    monkeypatch.setattr(probe.platform, "mac_ver", lambda: ("10.16", ("", "", ""), ""))
    seen: dict[str, str] = {}

    def run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        seen.update(env)
        return subprocess.CompletedProcess(cmd, 0, stdout="13.5\n", stderr="")

    monkeypatch.setattr(probe.subprocess, "run", run)
    assert probe.macos_version() == (13, 5)
    assert seen["SYSTEM_VERSION_COMPAT"] == "0"


# Linux x86-64 with glibc 2.17, as pip would read it. (Not a Mac: making
# sys.platform "darwin" breaks the probe's own `ctypes.util` import here.)
SITECUSTOMIZE = """
import os
os.confstr = lambda name: "glibc 2.17"
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_bootstrap_stops_on_old_glibc_before_it_makes_a_virtualenv(tmp_path: Path) -> None:
    """
    bootstrap.sh in a scratch copy, run with an interpreter that reports glibc
    2.17 on x86-64, where imtypeset's requirement is on and no wheel of it
    matches: it says what will not install and stops, and no .venv is
    made. Never this worktree's own bootstrap, whose .venv it would replace.
    """
    work = tmp_path / "checkout"
    (work / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "bootstrap.sh", work / "bootstrap.sh")
    shutil.copy2(ROOT / "scripts" / "probe.py", work / "scripts" / "probe.py")
    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    python = tmp_path / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "VIRTUAL_ENV", "PYTHONSTARTUP")
    }
    env["PYTHONPATH"] = str(fake)
    proc = subprocess.run(
        ["bash", str(work / "bootstrap.sh"), "--no-window", "--python", str(python)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "pptmstr will not install here: glibc 2.17 is below 2.28" in proc.stdout
    assert "stage 2/4" not in proc.stdout
    assert not (work / ".venv").exists()


# -- bootstrap.sh over an existing .venv, with stand-ins ----------------------------
#
# Never this worktree's own bootstrap: it would install into its .venv. A copy
# runs with an interpreter, a venv interpreter and a uv that only record what
# they were asked, and answer the one question whose answer is the decision.

PYTHON_STAND_IN = """#!/bin/sh
echo "python $*" >> "$STANDIN_LOG"
case "$1" in
  -c) exit 0 ;;
  scripts/probe.py) echo "probe stand-in"; exit 0 ;;
  -m) if [ "$2" = venv ]; then
        mkdir -p .venv/bin
        cp "$STANDIN_VENV_PYTHON" .venv/bin/python
      fi
      exit 0 ;;
esac
exit 0
"""

VENV_PYTHON_STAND_IN = """#!/bin/sh
echo "venv-python $*" >> "$STANDIN_LOG"
if [ "$1" = -m ] && [ "$2" = pptmstr.pairing ] && [ "$3" = unpublished ]; then
  echo "$STANDIN_REPLACE"
fi
exit 0
"""

UV_STAND_IN = """#!/bin/sh
echo "uv $*" >> "$STANDIN_LOG"
if [ "$1" = venv ]; then
  mkdir -p .venv/bin
  [ -e .venv/bin/python ] || cp "$STANDIN_VENV_PYTHON" .venv/bin/python
fi
exit 0
"""


def _stand_in(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _bootstrap(tmp_path: Path, *, uv: bool, replace: str) -> tuple[str, list[str], Path]:
    work = tmp_path / "checkout"
    (work / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "bootstrap.sh", work / "bootstrap.sh")
    stubs = tmp_path / "bin"
    stubs.mkdir()
    python = _stand_in(stubs / "python-stand-in", PYTHON_STAND_IN)
    venv_python = _stand_in(tmp_path / "venv-python", VENV_PYTHON_STAND_IN)
    if uv:
        _stand_in(stubs / "uv", UV_STAND_IN)
    # An existing .venv, as a run of `make deps` leaves one.
    (work / ".venv" / "bin").mkdir(parents=True)
    shutil.copy2(venv_python, work / ".venv" / "bin" / "python")
    (work / ".venv" / "kept").write_text("", encoding="utf-8")
    log = tmp_path / "log"
    system = os.pathsep.join(p for p in ("/usr/bin", "/bin") if Path(p).is_dir())
    env = {
        "PATH": f"{stubs}{os.pathsep}{system}",
        "HOME": str(tmp_path),
        "STANDIN_LOG": str(log),
        "STANDIN_VENV_PYTHON": str(venv_python),
        "STANDIN_REPLACE": replace,
    }
    done = subprocess.run(
        ["bash", str(work / "bootstrap.sh"), "--no-window", "--python", str(python)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout, log.read_text(encoding="utf-8").splitlines(), work


needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")


@needs_bash
@pytest.mark.parametrize("uv", [pytest.param(True, id="uv"), pytest.param(False, id="pip")])
def test_bootstrap_keeps_an_existing_venv_and_says_so_first(tmp_path: Path, uv: bool) -> None:
    if not uv and shutil.which("uv", path="/usr/bin:/bin"):
        pytest.skip("a uv in /usr/bin or /bin would be taken where the pip branch is under test")
    out, log, work = _bootstrap(tmp_path, uv=uv, replace="")
    assert ".venv exists and is kept as it is" in out
    assert out.index(".venv exists") < out.index("stage 3/4")
    assert (work / ".venv" / "kept").is_file()
    if uv:
        assert any(line.startswith("uv venv --allow-existing ") for line in log)
    # Nothing to replace: the one install and no other.
    installs = [line for line in log if " install " in line and "--upgrade pip" not in line]
    assert len(installs) == 1 and installs[0].endswith("-e .[dev]")


@needs_bash
@pytest.mark.parametrize("uv", [pytest.param(True, id="uv"), pytest.param(False, id="pip")])
def test_bootstrap_reinstalls_what_was_not_installed_by_name(tmp_path: Path, uv: bool) -> None:
    if not uv and shutil.which("uv", path="/usr/bin:/bin"):
        pytest.skip("a uv in /usr/bin or /bin would be taken where the pip branch is under test")
    out, log, _ = _bootstrap(tmp_path, uv=uv, replace="imscribe==0.2.0 imtypeset==0.4.3.192900")
    assert (
        "not installed by name, so reinstalled from the package index: "
        "imscribe==0.2.0 imtypeset==0.4.3.192900"
    ) in out
    first = next(n for n, line in enumerate(log) if line.endswith("-e .[dev]"))
    asked = next(n for n, line in enumerate(log) if "pptmstr.pairing unpublished" in line)
    again = log[-1]
    assert first < asked < len(log) - 1
    if uv:
        assert again == (
            "uv pip install --python .venv/bin/python --no-deps --reinstall-package imscribe "
            "imscribe==0.2.0 --reinstall-package imtypeset imtypeset==0.4.3.192900"
        )
    else:
        assert again == (
            "venv-python -m pip install --no-deps --force-reinstall "
            "imscribe==0.2.0 imtypeset==0.4.3.192900"
        )


def test_an_install_by_name_is_not_replaced_and_one_from_a_file_or_a_build_is(
    tmp_path: Path,
) -> None:
    """
    ``pairing.not_by_name``: an installer writes ``direct_url.json`` for every
    install that is not by name, and a source build of imtypeset carries a
    ``BUILT-FROM`` in its package.
    """
    from pptmstr import pairing

    def installed(name: str, *, direct: str | None = None, built: bool = False) -> Path:
        site = tmp_path / name / "site"
        info = site / f"{name}-1.0.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\n", encoding="utf-8")
        if direct is not None:
            (info / "direct_url.json").write_text(direct, encoding="utf-8")
        (site / name).mkdir()
        if built:
            (site / name / pairing.BUILT_FROM).write_text("commit: x\n", encoding="utf-8")
        return site

    assert pairing.not_by_name("imscribe", [str(installed("by-name"))]) is None
    assert pairing.not_by_name("absent", [str(tmp_path)]) is None
    site = installed("imscribe", direct='{"url": "file:///src/imscribe", "dir_info": {}}')
    assert pairing.not_by_name("imscribe", [str(site)]) == (
        "imscribe 1.0 was installed from file:///src/imscribe, not by name"
    )
    site = installed("imtypeset", built=True)
    said = pairing.not_by_name("imtypeset", [str(site)])
    assert said is not None and "BUILT-FROM" in said
    # What bootstrap.sh is told to reinstall: each at its exact pin.
    both = [str(installed("both", direct='{"url": "file:///x"}'))]
    (tmp_path / "both" / "site" / "both-1.0.dist-info").rename(
        tmp_path / "both" / "site" / "imscribe-1.0.dist-info"
    )
    assert pairing.unpublished(both) == [
        (
            f"imscribe=={pairing.IMSCRIBE_PIN}",
            "imscribe 1.0 was installed from file:///x, not by name",
        )
    ]


# -- which interpreter bootstrap.sh takes, with stand-ins ----------------------------
#
# Never this worktree's own bootstrap. Each stand-in is named for a CPython minor
# and runs this interpreter, with sys.version_info reporting that minor, so the
# real probe in a scratch copy decides; after stage 1 it stops bootstrap, so no
# virtualenv is made. PATH holds only the stand-ins and the one tool bootstrap
# needs before stage 1, so no interpreter of this machine is taken.

FAKE_VERSION = """
import sys


class _Version(tuple):
    major = property(lambda self: self[0])
    minor = property(lambda self: self[1])
    micro = property(lambda self: self[2])
    releaselevel = property(lambda self: self[3])
    serial = property(lambda self: self[4])


sys.version_info = _Version((3, {minor}, 0, "final", 0))
"""

MINOR_STAND_IN = """#!/bin/sh
echo "python3.{minor} $*" >> "$STANDIN_LOG"
[ "$1" = -c ] && exit 0
PYTHONPATH="{fake}" "{real}" "$@"
[ "$2" = --stage ] && exit 1
exit 0
"""


def test_the_probe_names_the_library_pythons_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    assert probe.library_pythons() == [
        f"python3.{minor}"
        for minor in range(probe.IMTYPESET_PYTHONS[1][1], probe.IMTYPESET_PYTHONS[0][1] - 1, -1)
    ]
    monkeypatch.setattr(probe, "IMTYPESET_PYTHONS", ((3, 11), (3, 14)))
    assert probe.library_pythons() == ["python3.14", "python3.13", "python3.12", "python3.11"]


def _choose(
    tmp_path: Path,
    minors: list[int],
    pythons: tuple[tuple[int, int], tuple[int, int]],
    *args: str,
) -> tuple[subprocess.CompletedProcess[str], list[str], Path]:
    work = tmp_path / "checkout"
    (work / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "bootstrap.sh", work / "bootstrap.sh")
    source = (ROOT / "scripts" / "probe.py").read_text(encoding="utf-8")
    line = next(s for s in source.splitlines() if s.startswith("IMTYPESET_PYTHONS = "))
    (work / "scripts" / "probe.py").write_text(
        source.replace(line, f"IMTYPESET_PYTHONS = {pythons!r}"), encoding="utf-8"
    )
    stubs = tmp_path / "bin"
    stubs.mkdir()
    dirname = shutil.which("dirname")
    assert dirname is not None
    (stubs / "dirname").symlink_to(dirname)
    for minor in minors:
        fake = tmp_path / "fake" / str(minor)
        fake.mkdir(parents=True)
        (fake / "sitecustomize.py").write_text(FAKE_VERSION.format(minor=minor), encoding="utf-8")
        _stand_in(
            stubs / f"python3.{minor}",
            MINOR_STAND_IN.format(minor=minor, fake=fake, real=sys.executable),
        )
    log = tmp_path / "log"
    log.touch()
    env = {"PATH": str(stubs), "HOME": str(tmp_path), "STANDIN_LOG": str(log)}
    bash = shutil.which("bash")
    assert bash is not None
    done = subprocess.run(
        [bash, str(work / "bootstrap.sh"), "--no-window", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=120,
    )
    assert "stage 2/4" not in done.stdout, done.stdout + done.stderr
    assert not (work / ".venv").exists()
    return done, log.read_text(encoding="utf-8").splitlines(), stubs


def _staged(log: list[str]) -> list[str]:
    return [line.split()[0] for line in log if "scripts/probe.py --stage pre" in line]


@needs_bash
def test_bootstrap_takes_the_newest_python_the_pinned_imtypeset_installs_on(
    tmp_path: Path,
) -> None:
    done, log, stubs = _choose(tmp_path, [13, 11], ((3, 11), (3, 11)))
    assert _staged(log) == ["python3.11"]
    assert (
        f"python: {stubs / 'python3.11'}, the newest on PATH that the pinned imtypeset "
        "installs on (python3.11)"
    ) in done.stdout
    assert f"(using {stubs / 'python3.11'})" in done.stdout


@needs_bash
def test_bootstrap_takes_the_newest_on_the_floor_when_none_is_in_range_and_stage_1_stops(
    tmp_path: Path,
) -> None:
    done, log, stubs = _choose(tmp_path, [13], ((3, 11), (3, 11)))
    assert _staged(log) == ["python3.13"]
    assert (
        f"python: {stubs / 'python3.13'}, the newest on PATH on the 3.11 floor: none that the "
        "pinned imtypeset installs on (python3.11) is on PATH and runs"
    ) in done.stdout
    assert "installs only on CPython 3.11" in done.stdout
    assert "imscribe requires it on CPython 3.13" in done.stdout
    assert done.returncode != 0


@needs_bash
def test_bootstrap_takes_the_python_it_is_given(tmp_path: Path) -> None:
    given = str(tmp_path / "bin" / "python3.13")
    done, log, _ = _choose(tmp_path, [13, 11], ((3, 11), (3, 11)), "--python", given)
    assert _staged(log) == ["python3.13"]
    assert f"python: {given}, given by --python" in done.stdout
    assert not any("--pythons" in line for line in log)


@needs_bash
def test_bootstrap_takes_the_newest_of_a_wider_range(tmp_path: Path) -> None:
    """
    The move to an imtypeset whose wheels admit 3.11 to 3.14 widens the probe's
    range, and bootstrap follows it with no change of its own.
    """
    done, log, stubs = _choose(tmp_path, [14, 13, 11], ((3, 11), (3, 14)))
    assert _staged(log) == ["python3.14"]
    assert "installs on (python3.14 python3.13 python3.12 python3.11)" in done.stdout
    _, log, _ = _choose(tmp_path / "narrow", [14, 13, 11], ((3, 11), (3, 11)))
    assert _staged(log) == ["python3.11"]
