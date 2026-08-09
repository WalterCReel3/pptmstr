#!/usr/bin/env python3
"""Environment probe for the pptmstr orchestrator.

Runs in two stages:

  probe.py --stage pre    stdlib-only diagnostics; safe to run before any install.
  probe.py --stage post   runs the import/window smoke test inside the built venv.

Nothing here installs or modifies the system. When something is missing the probe
prints the exact command to run and exits non-zero with a one-line diagnosis.

Platforms: macOS is where this was written; Debian-family Linux is the reference
Linux, in the sense that it is the one whose remedies are tested verbatim. Other
distributions are supported best-effort — the checks are all soname- and
capability-based, so they give the right answer anywhere, and the suggested
commands are translated for dnf/pacman where the package names are known and
fall back to naming the sonames where they are not.

The Linux failure modes differ from the macOS ones, which is why the checks do:

  * `python3 -m venv` is a separate package on Debian/Ubuntu, so the stdlib
    `venv` module imports fine and then produces a venv with no pip;
  * the vendored libglfw.so is an X11-only build that dlopen()s several X
    extension libraries during glfwInit(), so a missing libXrandr looks like
    "the window won't open" rather than an import error;
  * a Wayland session without XWayland has no X display for it to use;
  * a headless box has no display at all and needs Xvfb, which the Makefile's
    window targets can wrap for you.
"""

from __future__ import annotations

import argparse
import ctypes.util
import glob
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

OK = "  ok "
WARN = " warn"
FAIL = " FAIL"
INFO = " info"

IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Hard floor. 3.11 is what Debian 12 ships; older interpreters have no prebuilt
# imgui-bundle wheel on recent releases and fall back to a long source build.
MIN_PYTHON = (3, 11)
MIN_PY_STR = ".".join(str(p) for p in MIN_PYTHON)

# manylinux tag the current imgui-bundle wheels are built against. Below this
# glibc, pip skips the wheel and falls back to a source build.
MIN_GLIBC = (2, 28)

# Architectures with prebuilt Linux wheels on PyPI for the whole dependency set.
LINUX_WHEEL_ARCHES = {"x86_64", "aarch64"}


# --------------------------------------------------------------------------
# Package naming
#
# Checks resolve sonames, which are the same everywhere. Only the advice needs
# translating, so package names live in one table keyed by manager.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PkgMgr:
    key: str  # table key: apt / dnf / pacman
    install: str  # command prefix
    tested: bool  # is this the reference platform?


PKG_MANAGERS = (
    ("apt-get", PkgMgr("apt", "sudo apt install", True)),
    ("dnf", PkgMgr("dnf", "sudo dnf install", False)),
    ("pacman", PkgMgr("pacman", "sudo pacman -S", False)),
    ("zypper", PkgMgr("zypper", "sudo zypper install", False)),
)


def detect_pkg_mgr() -> PkgMgr | None:
    for exe, mgr in PKG_MANAGERS:
        if shutil.which(exe):
            return mgr
    return None


@dataclass(frozen=True)
class Lib:
    soname: str
    required: bool  # does GLFW abort without it, or merely degrade?
    apt: str
    dnf: str
    pacman: str

    def package(self, mgr: PkgMgr | None) -> str:
        return getattr(self, mgr.key, self.soname) if mgr else self.soname


# The imgui-bundle wheel vendors its own libglfw.so, and on Linux that build is
# X11-only: `strings libglfw.so` reports "3.4.0 X11 GLX Null EGL OSMesa". It
# links some of these and dlopen()s the rest during glfwInit(), which is why a
# missing one shows up as "no window" rather than an ImportError.
#                  soname             req    apt            dnf            pacman
X11_RUNTIME_LIBS: tuple[Lib, ...] = (
    Lib("libX11.so.6",      True,  "libx11-6",     "libX11",      "libx11"),
    Lib("libXext.so.6",     True,  "libxext6",     "libXext",     "libxext"),
    Lib("libXcursor.so.1",  True,  "libxcursor1",  "libXcursor",  "libxcursor"),
    Lib("libXi.so.6",       True,  "libxi6",       "libXi",       "libxi"),
    Lib("libXinerama.so.1", True,  "libxinerama1", "libXinerama", "libxinerama"),
    Lib("libXrandr.so.2",   True,  "libxrandr2",   "libXrandr",   "libxrandr"),
    Lib("libGL.so.1",       True,  "libgl1",       "mesa-libGL",  "libglvnd"),
    Lib("libxcb.so.1",      False, "libxcb1",      "libxcb",      "libxcb"),
    Lib("libXrender.so.1",  False, "libxrender1",  "libXrender",  "libxrender"),
    Lib("libXxf86vm.so.1",  False, "libxxf86vm1",  "libXxf86vm",  "libxxf86vm"),
)  # fmt: skip

# Single-purpose packages the remedies reference by name.
TOOL_PKGS = {
    "xvfb": {"apt": "xvfb", "dnf": "xorg-x11-server-Xvfb", "pacman": "xorg-server-xvfb"},
    "xwayland": {"apt": "xwayland", "dnf": "xorg-x11-server-Xwayland", "pacman": "xorg-xwayland"},
    "glxinfo": {"apt": "mesa-utils", "dnf": "glx-utils", "pacman": "mesa-utils"},
}


def install_hint(mgr: PkgMgr | None, packages: list[str]) -> str:
    """An install command, or a soname list when we cannot name the packages."""
    if mgr and packages:
        return f"{mgr.install} " + " ".join(packages)
    return "install (package names vary by distribution): " + " ".join(packages)


def packages_for(mgr: PkgMgr | None, libs: list[Lib]) -> list[str]:
    return sorted({lb.package(mgr) for lb in libs})


def tool_hint(mgr: PkgMgr | None, tool: str) -> str:
    names = TOOL_PKGS[tool]
    if mgr is None:
        return f"install {tool} with your package manager"
    return f"{mgr.install} {names.get(mgr.key, tool)}"


@dataclass
class Report:
    fatal: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    remedies: list[str] = field(default_factory=list)

    def line(self, status: str, label: str, detail: str) -> None:
        print(f"[{status}] {label:<22} {detail}")

    def ok(self, label: str, detail: str) -> None:
        self.line(OK, label, detail)

    def info(self, label: str, detail: str) -> None:
        self.line(INFO, label, detail)

    def warn(self, label: str, detail: str, remedy: str | None = None) -> None:
        self.line(WARN, label, detail)
        self.warnings.append(f"{label}: {detail}")
        if remedy:
            self.remedies.append(remedy)

    def fail(self, label: str, detail: str, remedy: str | None = None) -> None:
        self.line(FAIL, label, detail)
        self.fatal.append(f"{label}: {detail}")
        if remedy:
            self.remedies.append(remedy)


# --------------------------------------------------------------------------
# Shared
# --------------------------------------------------------------------------


def python_remedy(mgr: PkgMgr | None = None) -> str:
    if IS_MAC:
        return "brew install python@3.13"
    if IS_LINUX:
        # A distro older than the floor cannot fix this from its own repos, so
        # name the escape hatch alongside the package.
        base = (
            f"{mgr.install} python3 python3-venv"
            if mgr and mgr.key == "apt"
            else (f"{mgr.install} python3" if mgr else f"install Python {MIN_PY_STR}+")
        )
        return f"{base}   # if your release predates {MIN_PY_STR}, use pyenv or a backport"
    return f"install Python {MIN_PY_STR} or newer"


def check_python(r: Report, mgr: PkgMgr | None = None) -> None:
    v = sys.version_info
    label = f"{v.major}.{v.minor}.{v.micro} at {sys.executable}"
    if v[:2] < MIN_PYTHON:
        r.fail(
            "python version",
            f"{label} — below the {MIN_PY_STR} floor required by this project",
            python_remedy(mgr),
        )
    else:
        r.ok("python version", f"{label} — meets the {MIN_PY_STR}+ floor")


# --------------------------------------------------------------------------
# macOS
# --------------------------------------------------------------------------


def check_architecture_mac(r: Report) -> None:
    machine = platform.machine()
    if machine == "arm64":
        r.ok("architecture", f"{machine} (Apple Silicon) — prebuilt wheels available")
    elif machine == "x86_64":
        r.warn(
            "architecture",
            "x86_64: upstream imgui-bundle no longer publishes macOS Intel wheels. "
            "pip will fall back to a source build, which needs a full C++ toolchain "
            "and takes 10+ minutes. This is the most likely thing to break.",
            "xcode-select --install",
        )
    else:
        r.warn("architecture", f"{machine}: untested for this project")


def check_homebrew(r: Report) -> str | None:
    brew = shutil.which("brew")
    if not brew:
        r.info("homebrew", "not found — not required, wheels are self-contained")
        return None
    try:
        prefix = subprocess.run(
            [brew, "--prefix"], capture_output=True, text=True, timeout=30, check=True
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError) as exc:
        r.warn("homebrew", f"found at {brew} but `brew --prefix` failed: {exc}")
        return None
    expected = "/opt/homebrew" if platform.machine() == "arm64" else "/usr/local"
    detail = f"prefix {prefix}"
    if prefix != expected:
        detail += f" (expected {expected} for {platform.machine()})"
    r.ok("homebrew", detail)
    return prefix


def check_glfw_mac(r: Report, brew_prefix: str | None) -> None:
    # Deliberately informational. The imgui-bundle wheels statically bundle their own
    # GLFW3 + OpenGL3 backend, so a missing Homebrew glfw is normally irrelevant. It
    # only becomes a suspect if the --post smoke test below actually fails to open a
    # window; that is the check that decides.
    brew = shutil.which("brew")
    formula = "absent"
    if brew:
        proc = subprocess.run(
            [brew, "list", "--versions", "glfw"], capture_output=True, text=True, timeout=60
        )
        line = proc.stdout.strip().splitlines()
        installed = [ln for ln in line if ln.startswith("glfw ")]
        formula = installed[0] if installed else "absent"

    dylibs: list[str] = []
    if brew_prefix:
        dylibs = sorted(glob.glob(os.path.join(brew_prefix, "lib", "libglfw*.dylib")))

    if formula != "absent" or dylibs:
        r.ok("glfw (homebrew)", f"{formula}; dylibs: {len(dylibs)} found")
    else:
        r.info(
            "glfw (homebrew)",
            "absent — expected and fine. imgui-bundle wheels bundle GLFW3+OpenGL3. "
            "Only install it if the --post smoke test fails to open a window "
            "(then: brew install glfw).",
        )


def check_display_mac(r: Report) -> None:
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        r.warn(
            "display",
            "running over SSH — macOS cannot open a GLFW window without a local "
            "window server session; the smoke test will fail here",
        )
    else:
        r.ok("display", "local session")


# --------------------------------------------------------------------------
# Linux
# --------------------------------------------------------------------------


def os_release() -> dict[str, str]:
    """Parse /etc/os-release. Empty dict if it is missing or unreadable."""
    values: dict[str, str] = {}
    try:
        with open("/etc/os-release", encoding="utf-8") as fh:
            for raw in fh:
                if "=" not in raw or raw.lstrip().startswith("#"):
                    continue
                key, _, val = raw.partition("=")
                values[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def check_distro(r: Report, mgr: PkgMgr | None) -> None:
    rel = os_release()
    pretty = rel.get("PRETTY_NAME") or rel.get("NAME") or "unknown Linux"
    if mgr is None:
        r.warn(
            "distribution",
            f"{pretty} — no known package manager found, so remedies below name "
            "sonames and tools rather than packages",
        )
    elif mgr.tested:
        r.ok("distribution", f"{pretty} ({mgr.key}) — the reference Linux; remedies are verbatim")
    else:
        r.info(
            "distribution",
            f"{pretty} ({mgr.key}) — supported best-effort. The checks are soname-based "
            "so they are accurate here; the package names in the remedies are a "
            "translation from the Debian ones and may need adjusting.",
        )


def check_architecture_linux(r: Report, mgr: PkgMgr | None) -> None:
    machine = platform.machine()
    if machine in LINUX_WHEEL_ARCHES:
        r.ok("architecture", f"{machine} — prebuilt manylinux wheels available")
    else:
        r.warn(
            "architecture",
            f"{machine}: no manylinux wheels for imgui-bundle, so pip will attempt a "
            "source build needing a C++ toolchain, CMake and the X11 dev headers",
            install_hint(
                mgr,
                (
                    ["build-essential", "cmake", "python3-dev", "libx11-dev", "libxrandr-dev"]
                    if mgr and mgr.key == "apt"
                    else ["a C++ toolchain", "cmake", "python3 headers", "X11 dev headers"]
                ),
            ),
        )


def check_glibc(r: Report) -> None:
    name, version = platform.libc_ver()
    if name != "glibc" or not version:
        r.warn(
            "libc",
            f"{name or 'unknown'} {version or ''}".strip()
            + " — manylinux wheels target glibc; musl (Alpine) needs a source build",
        )
        return
    try:
        parts = tuple(int(p) for p in version.split(".")[:2])
    except ValueError:
        r.info("libc", f"glibc {version} (unparsed)")
        return
    if parts < MIN_GLIBC:
        r.warn(
            "libc",
            f"glibc {version} — imgui-bundle wheels are manylinux_2_28, so pip will "
            f"skip them below glibc {MIN_GLIBC[0]}.{MIN_GLIBC[1]} and try a source build",
        )
    else:
        r.ok("libc", f"glibc {version} — manylinux_2_28 wheels are installable")


def check_venv_module(r: Report, mgr: PkgMgr | None) -> None:
    """Debian's classic bootstrap failure.

    Debian and Ubuntu split ensurepip out of the stdlib into python3-venv, so
    `import venv` succeeds and `python3 -m venv` then produces a venv with no
    pip. Other distributions ship it whole, and this check simply passes there.
    """
    v = sys.version_info
    pkg = f"python{v.major}.{v.minor}-venv"
    apt_remedy = f"{mgr.install} {pkg} python3-pip-whl" if mgr and mgr.key == "apt" else None

    try:
        import venv  # noqa: F401
    except Exception as exc:
        r.fail("venv module", f"{type(exc).__name__}: {exc}", apt_remedy)
        return

    try:
        import ensurepip
    except Exception:
        r.fail(
            "venv module",
            "`venv` imports but `ensurepip` is missing, so `python3 -m venv` will fail",
            apt_remedy,
        )
        return

    # ensurepip can import and still have no pip to install. Upstream keeps the
    # wheel in ensurepip/_bundled/; Debian deletes that and repoints ensurepip at
    # /usr/share/python-wheels/. version() reports whichever pip it would really
    # install, and comes back empty when there is none.
    try:
        pip_version = ensurepip.version()
    except Exception as exc:
        r.fail("venv module", f"ensurepip.version() raised {type(exc).__name__}: {exc}", apt_remedy)
        return

    if not pip_version:
        r.fail(
            "venv module",
            "ensurepip has no pip wheel to install, so the venv would come up without pip",
            apt_remedy,
        )
        return
    source = getattr(ensurepip, "_WHEEL_PKG_DIR", None) or "bundled wheel"
    r.ok("venv module", f"venv + ensurepip ready, pip {pip_version} from {source}")


def check_externally_managed(r: Report) -> None:
    """PEP 668 marker: explains why this project insists on a venv."""
    if os.path.exists(os.path.join(os.path.dirname(os.__file__), "EXTERNALLY-MANAGED")):
        r.info(
            "PEP 668",
            "interpreter is externally managed (the distro owns it) — bootstrap.sh "
            "only ever installs into ./.venv, so this is fine",
        )


def _ldconfig_cache() -> dict[str, str]:
    """soname -> resolved path, from the ld.so cache. Empty if ldconfig is absent."""
    exe = shutil.which("ldconfig") or next(
        (p for p in ("/sbin/ldconfig", "/usr/sbin/ldconfig") if os.path.exists(p)), None
    )
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "-p"], capture_output=True, text=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    cache: dict[str, str] = {}
    for raw in out.splitlines():
        line = raw.strip()
        if " => " not in line:
            continue
        left, _, path = line.partition(" => ")
        cache.setdefault(left.split(" ", 1)[0], path.strip())
    return cache


def _find_lib(soname: str, cache: dict[str, str]) -> str | None:
    """Resolve a soname the way the dynamic loader would.

    Prefers the ld.so cache (cheap, and reports the path); falls back to
    ctypes.util.find_library where there is no ldconfig, as on musl systems.
    """
    if soname in cache:
        return cache[soname]
    if cache:
        return None
    return ctypes.util.find_library(soname.removeprefix("lib").partition(".so")[0])


def check_shared_libs(r: Report, mgr: PkgMgr | None) -> None:
    cache = _ldconfig_cache()
    missing_required: list[Lib] = []
    missing_optional: list[Lib] = []
    found = 0
    for lib in X11_RUNTIME_LIBS:
        if _find_lib(lib.soname, cache):
            found += 1
        elif lib.required:
            missing_required.append(lib)
        else:
            missing_optional.append(lib)

    total = len(X11_RUNTIME_LIBS)
    if missing_required:
        names = ", ".join(lb.soname for lb in missing_required)
        r.fail(
            "x11 / gl runtime",
            f"{found}/{total} present; missing {names}. The vendored libglfw.so is an "
            "X11-only build and dlopen()s these at glfwInit(), so the window will "
            "simply fail to open.",
            install_hint(mgr, packages_for(mgr, missing_required + missing_optional)),
        )
    elif missing_optional:
        names = ", ".join(lb.soname for lb in missing_optional)
        r.warn(
            "x11 / gl runtime",
            f"{found}/{total} present; optional missing: {names}",
            install_hint(mgr, packages_for(mgr, missing_optional)),
        )
    else:
        r.ok("x11 / gl runtime", f"all {total} GLFW/GLX libraries resolve")


def check_display_linux(r: Report, mgr: PkgMgr | None) -> None:
    """Decide whether a window can open here, and say what to do if not.

    The vendored GLFW has no Wayland backend, so WAYLAND_DISPLAY alone is not
    enough — there has to be an X display, which on a Wayland session means
    XWayland.
    """
    display = os.environ.get("DISPLAY")
    wayland = os.environ.get("WAYLAND_DISPLAY")
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    over_ssh = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))

    if display:
        detail = f"DISPLAY={display} (session type: {session})"
        if wayland and session == "wayland":
            detail += " — Wayland session, so this is XWayland; the bundled GLFW is X11-only"
        if over_ssh:
            r.warn(
                "display",
                detail + ". Over SSH this is X11 forwarding: it works, but every draw "
                "call crosses the network, so the bench numbers will be meaningless.",
            )
        else:
            r.ok("display", detail)
        return

    # Both of the remaining cases are warnings, not failures: the install still
    # succeeds and the data layer is unaffected. The --post smoke test is the
    # check that decides whether GL actually works here.
    if wayland:
        r.warn(
            "display",
            f"WAYLAND_DISPLAY={wayland} but no DISPLAY. The bundled GLFW is X11-only "
            "('3.4.0 X11 GLX Null EGL OSMesa'), so it needs XWayland on a Wayland session.",
            tool_hint(mgr, "xwayland") + "   # and enable XWayland in your compositor",
        )
        return

    xvfb = shutil.which("xvfb-run")
    r.warn(
        "display",
        "headless: neither DISPLAY nor WAYLAND_DISPLAY is set. `make verify`, "
        "`make verify-modes` and `make check` are unaffected; anything that opens a "
        "window needs a virtual X server."
        + (
            "  xvfb-run is installed, so the Makefile's window targets will wrap it."
            if xvfb
            else "  Install Xvfb and they will wrap it automatically."
        ),
        None if xvfb else tool_hint(mgr, "xvfb"),
    )


def check_gpu_linux(r: Report, mgr: PkgMgr | None) -> None:
    """Name the GL renderer: software rasterisation changes the bench verdict.

    llvmpipe draws everything correctly, it just cannot hold 60 fps at 10k rows,
    so `make bench` would report a FAIL that is about the machine, not the code.
    """
    glxinfo = shutil.which("glxinfo")
    if not glxinfo or not os.environ.get("DISPLAY"):
        r.info(
            "gl renderer",
            "not probed (needs glxinfo and a display) — "
            f"{tool_hint(mgr, 'glxinfo')} to have the bench verdict name your GPU",
        )
        return
    try:
        out = subprocess.run([glxinfo, "-B"], capture_output=True, text=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        r.info("gl renderer", f"glxinfo failed: {exc}")
        return

    renderer = next(
        (
            ln.split(":", 1)[1].strip()
            for ln in out.splitlines()
            if ln.strip().startswith(("OpenGL renderer string", "Device:"))
        ),
        "",
    )
    if not renderer:
        r.info("gl renderer", "glxinfo gave no renderer string")
    elif any(sw in renderer.lower() for sw in ("llvmpipe", "softpipe", "swrast", "lavapipe")):
        r.warn(
            "gl renderer",
            f"{renderer} — software rasterisation. Everything draws correctly, but "
            "`make bench` will not reach 60 fps; that is the renderer, not the code.",
        )
    else:
        r.ok("gl renderer", renderer)


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------


def run_pre() -> int:
    print("=== pptmstr probe: pre-install ===")
    r = Report()
    if IS_MAC:
        check_architecture_mac(r)
        check_python(r)
        prefix = check_homebrew(r)
        check_glfw_mac(r, prefix)
        check_display_mac(r)
    elif IS_LINUX:
        mgr = detect_pkg_mgr()
        check_distro(r, mgr)
        check_architecture_linux(r, mgr)
        check_python(r, mgr)
        check_glibc(r)
        check_venv_module(r, mgr)
        check_externally_managed(r)
        check_shared_libs(r, mgr)
        check_display_linux(r, mgr)
        check_gpu_linux(r, mgr)
    else:
        r.warn("platform", f"{sys.platform}: untested; only the generic checks run")
        check_python(r)
    return finish(r)


def smoke_command(smoke: str) -> list[str]:
    """How to invoke the window smoke test here.

    On a headless Linux box, wrap it in xvfb-run so the probe still proves the GL
    stack works end to end instead of skipping the only check that matters. The
    explicit 24-bit screen matters: xvfb-run defaults to 8-bit, and GLX will not
    hand out an OpenGL 3.3 core context on an 8-bit visual.
    """
    cmd = [sys.executable, smoke]
    if IS_LINUX and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        return ["xvfb-run", "-a", "--server-args=-screen 0 1280x800x24", *cmd]
    return cmd


def window_remedy() -> str:
    if IS_MAC:
        return "brew install glfw   # only now is this a plausible fix"
    if IS_LINUX:
        mgr = detect_pkg_mgr()
        pkgs = packages_for(mgr, [lb for lb in X11_RUNTIME_LIBS if lb.required])
        return install_hint(mgr, pkgs) + "   # only now are these plausible fixes"
    return "install a system GLFW/OpenGL runtime"


def run_post(smoke: str) -> int:
    print("=== pptmstr probe: post-install ===")
    r = Report()

    try:
        import imgui_bundle  # noqa: F401
    except Exception as exc:  # pragma: no cover - diagnostic path
        r.fail(
            "import imgui_bundle",
            f"{type(exc).__name__}: {exc}",
            "pip install --force-reinstall imgui-bundle",
        )
        return finish(r)
    r.ok("import imgui_bundle", getattr(imgui_bundle, "__version__", "version unknown"))

    for mod in ("claude_agent_sdk",):
        try:
            m = __import__(mod)
        except Exception as exc:
            r.fail(f"import {mod}", f"{type(exc).__name__}: {exc}")
        else:
            r.ok(f"import {mod}", getattr(m, "__version__", "version unknown"))

    check_agent_cli(r)

    cmd = smoke_command(smoke)
    if IS_LINUX and not os.environ.get("DISPLAY"):
        if cmd[0] == "xvfb-run":
            r.info("window smoke test", "no DISPLAY — running it under xvfb-run")
        else:
            r.warn(
                "window smoke test",
                "skipped: no DISPLAY and no xvfb-run, so no window can open here",
                tool_hint(detect_pkg_mgr(), "xvfb"),
            )
            return finish(r)

    # The window test runs in a subprocess: a failure here is usually a hard crash in
    # native code (dyld/ld.so, GL context), which would take this process down with it.
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    out = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        r.fail("window smoke test", f"exit {proc.returncode}\n{out}", window_remedy())
        return finish(r)

    scale = "unknown"
    for ln in proc.stdout.splitlines():
        if ln.startswith("DPI_SCALE="):
            scale = ln.split("=", 1)[1]
        if ln.startswith("FRAMES="):
            r.ok("window smoke test", f"{ln.split('=', 1)[1]} frames rendered, clean exit")
    if scale != "unknown":
        try:
            value = float(scale)
        except ValueError:
            r.info("hidpi / scale", f"framebuffer scale {scale}")
        else:
            # macOS reports 2.0 on a retina panel. X11 has no per-window
            # framebuffer scale, so 1.0 on Linux is normal, not a problem —
            # fonts are sized from the DPI there instead.
            if value >= 1.9:
                note = "hidpi (2x)"
            elif IS_LINUX:
                note = "normal on X11, which has no per-window framebuffer scale"
            else:
                note = "non-retina"
            r.ok("hidpi / scale", f"framebuffer scale {value:g} — {note}; fonts sized accordingly")
    else:
        r.warn("hidpi / scale", "smoke test did not report a scale factor")

    return finish(r)


def check_agent_cli(r: Report) -> None:
    """Prove the Claude Code CLI the SDK will actually spawn is runnable.

    Every agent session is a CLI subprocess, so an unusable binary means the UI
    comes up fine and every agent fails the moment it is started -- a failure that
    looks like a bug in this app rather than a broken install.

    The SDK prefers the copy bundled in its own wheel and only falls back to PATH
    (subprocess_cli.py: _find_cli). Resolving it the same way here, rather than
    just running `claude` off PATH, is the difference between checking the binary
    that will be used and checking a different one that happens to share a name.
    """
    try:
        import claude_agent_sdk
    except Exception:
        return  # already reported by the import loop

    name = "claude.exe" if sys.platform == "win32" else "claude"
    bundled = os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", name)
    if os.path.exists(bundled):
        cli, origin = bundled, "bundled in claude-agent-sdk"
    elif found := shutil.which(name):
        cli, origin = found, "found on PATH (wheel has no bundled copy)"
    else:
        r.fail(
            "claude CLI",
            "not bundled with the SDK and not on PATH; every agent session would " "fail to spawn",
            "pip install --force-reinstall claude-agent-sdk",
        )
        return

    if not os.access(cli, os.X_OK):
        r.fail("claude CLI", f"{cli} is not executable", f"chmod +x {cli}")
        return

    try:
        proc = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError) as exc:
        r.fail("claude CLI", f"{cli} failed to run: {type(exc).__name__}: {exc}")
        return

    if proc.returncode != 0:
        detail = (proc.stdout + proc.stderr).strip().splitlines()
        r.fail("claude CLI", f"exit {proc.returncode}: {detail[0] if detail else '(no output)'}")
        return

    r.ok("claude CLI", f"{proc.stdout.strip()} — {origin}")


def finish(r: Report) -> int:
    print()
    if r.fatal:
        print(f"DIAGNOSIS: {r.fatal[0]}")
        if r.remedies:
            print("\nTry:")
            for cmd in dict.fromkeys(r.remedies):
                print(f"    {cmd}")
        return 1
    if r.warnings:
        print(f"Probe passed with {len(r.warnings)} warning(s).")
        if r.remedies:
            print("Suggested (not run automatically):")
            for cmd in dict.fromkeys(r.remedies):
                print(f"    {cmd}")
        return 0
    print("Probe passed clean.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=["pre", "post"], default="pre")
    ap.add_argument(
        "--smoke",
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "smoke_test.py"),
    )
    args = ap.parse_args()
    return run_pre() if args.stage == "pre" else run_post(args.smoke)


if __name__ == "__main__":
    raise SystemExit(main())
