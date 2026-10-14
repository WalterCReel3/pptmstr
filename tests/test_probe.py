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
        python=(3, 12),
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
        facts(python=(3, 11)),
        facts(python=(3, 14)),
    ],
)
def test_the_supported_floor_installs_the_library(case: object) -> None:
    assert probe.install_outlook(case).verdict == probe.LIBRARY


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
    out, log, _ = _bootstrap(tmp_path, uv=uv, replace="imscribe==0.2.0b1 imtypeset==0.4.1.192900")
    assert (
        "not installed by name, so reinstalled from the package index: "
        "imscribe==0.2.0b1 imtypeset==0.4.1.192900"
    ) in out
    first = next(n for n, line in enumerate(log) if line.endswith("-e .[dev]"))
    asked = next(n for n, line in enumerate(log) if "pptmstr.pairing unpublished" in line)
    again = log[-1]
    assert first < asked < len(log) - 1
    if uv:
        assert again == (
            "uv pip install --python .venv/bin/python --no-deps --reinstall-package imscribe "
            "imscribe==0.2.0b1 --reinstall-package imtypeset imtypeset==0.4.1.192900"
        )
    else:
        assert again == (
            "venv-python -m pip install --no-deps --force-reinstall "
            "imscribe==0.2.0b1 imtypeset==0.4.1.192900"
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
