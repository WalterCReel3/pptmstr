#!/usr/bin/env bash
# Bootstrap: probe -> venv -> install -> re-probe (window smoke test).
#
# Never installs system packages. If something system-level is missing, the probe
# prints the exact command and this script stops.
#
# Runs on macOS and on Linux. The Linux-specific part is stage 4: a headless box
# has no X display, so the window smoke test is wrapped in Xvfb or skipped with
# an explanation, rather than failing for a reason unrelated to the build.
#
# Everything comes from the package index, and pyproject.toml names all of it:
# stage 3 is one install. That brings imscribe, which sets typeset text, and
# imtypeset, the library it draws through, as wheels at exact versions beside
# the one imgui-bundle they are built for; nothing is compiled and no variable
# has to be set. That is CPython in the range the pinned imtypeset's wheels
# admit (scripts/probe.py's IMTYPESET_PYTHONS) on macOS 14 or later on Apple
# Silicon, or on Linux x86-64 with glibc 2.28 or later. On Linux aarch64 the
# install leaves the library out and every pane draws with its own renderer.
# Anywhere else pip either fails (macOS 13, glibc below 2.28, musl: imtypeset's
# requirement is on and no wheel of it matches) or compiles imgui-bundle from
# source (an Intel Mac, CPython 3.15), so stage 1 says which, in one sentence,
# and this script stops before it makes a virtualenv.
#
# With requirements-dev.lock at the root (`make lock`), stage 3 installs exactly
# what it names, every file checked against its hash, and then pptmstr itself
# from this checkout with no dependencies; stage 1 stops where the lock does not
# cover the platform. Without one, or with --unlocked (PPTMSTR_UNLOCKED=1), it
# installs from pyproject.toml's floors and says the install is unlocked: what
# a fresh resolve gives today, which is what the drift job runs.
#
# Building the two from source at a commit nobody has published is the
# development override and no part of this script: `make deps`, afterwards, in
# the .venv this leaves (the Makefile and scripts/typeset_deps.py say how).
#
# Usage: ./bootstrap.sh [--no-window] [--unlocked] [--python PATH]
set -euo pipefail

cd "$(dirname "$0")"

if [ -n "${ADVTEXT_SRC:-}" ]; then
  echo "note: ADVTEXT_SRC is set and is not read. imscribe is installed from the package" >&2
  echo "      index, at the version pyproject.toml pins." >&2
fi

# Keep in step with requires-python in pyproject.toml.
MIN_MAJOR=3
MIN_MINOR=11

SKIP_WINDOW=0
PYTHON=""
LOCK=requirements-dev.lock
UNLOCKED=0
case "${PPTMSTR_UNLOCKED:-}" in ""|0) ;; *) UNLOCKED=1 ;; esac

while [ $# -gt 0 ]; do
  case "$1" in
    --no-window) SKIP_WINDOW=1; shift ;;
    --unlocked) UNLOCKED=1; shift ;;
    --python) PYTHON="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,/^# Usage:/p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# Newest first. A candidate only counts if it runs and clears the floor, so a
# broken pyenv shim or a name that is not really an interpreter is skipped
# rather than carried into the venv.
meets_floor() {
  "$1" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= ($MIN_MAJOR, $MIN_MINOR) else 1)" \
    >/dev/null 2>&1
}

first_usable() {
  for p in "$@"; do
    if command -v "$p" >/dev/null 2>&1 && meets_floor "$p"; then
      command -v "$p"
      return 0
    fi
  done
  return 1
}

find_python() {
  first_usable python3.14 python3.13 python3.12 python3.11 python3
}

if [ -n "$PYTHON" ]; then
  meets_floor "$PYTHON" || {
    echo "$PYTHON does not meet the Python ${MIN_MAJOR}.${MIN_MINOR}+ floor" >&2
    exit 1
  }
  PYTHON_WHY="given by --python"
elif FLOOR_PYTHON="$(find_python)"; then
  # The newest on the floor can still be one the pinned imtypeset has no wheel
  # for, and stage 1 would stop on it. The probe names the ones it has, newest
  # first, so the range is spelled once; any interpreter on the floor can say it.
  IN_RANGE="$("$FLOOR_PYTHON" scripts/probe.py --pythons 2>/dev/null || true)"
  IN_RANGE="${IN_RANGE//$'\n'/ }"
  # shellcheck disable=SC2086
  if [ -n "$IN_RANGE" ] && PYTHON="$(first_usable $IN_RANGE)"; then
    PYTHON_WHY="the newest on PATH that the pinned imtypeset installs on ($IN_RANGE)"
  else
    PYTHON="$FLOOR_PYTHON"
    PYTHON_WHY="the newest on PATH on the ${MIN_MAJOR}.${MIN_MINOR} floor: none that the pinned imtypeset"
    PYTHON_WHY+=" installs on (${IN_RANGE:-the probe named none}) is on PATH and runs, and stage 1 says what to do"
  fi
else
  # No usable interpreter at all: run the probe anyway so the diagnosis and the
  # platform-appropriate install command come from one place.
  echo "No Python ${MIN_MAJOR}.${MIN_MINOR}+ found on PATH."
  echo
  python3 scripts/probe.py --stage pre || true
  exit 1
fi

echo "python: $PYTHON, $PYTHON_WHY"
echo
echo ">>> stage 1/4: pre-install probe (using $PYTHON)"
"$PYTHON" scripts/probe.py --stage pre
if [ "$UNLOCKED" = 1 ]; then
  LOCKED=0
  UNLOCKED_WHY="--unlocked (or PPTMSTR_UNLOCKED) asks for pyproject.toml's floors"
elif [ -e "$LOCK" ]; then
  LOCKED=1
  # The lock covers fewer platforms than the floors install on (not Linux
  # aarch64), so the probe passing is not enough.
  "$PYTHON" scripts/lock.py covers
else
  LOCKED=0
  UNLOCKED_WHY="there is no $LOCK (\`make lock\` writes it)"
fi

echo
echo ">>> stage 2/4: virtualenv"
# An existing .venv is kept, and said so before anything touches it: uv 0.8 and
# later asks before deleting one (and declining stops this script) or deletes it
# unasked where nothing can answer, and `python -m venv` keeps what is in it. So
# the one behaviour is to keep it, and stage 3 replaces what it holds that an
# install of the pins would otherwise leave.
if [ -e .venv ]; then
  echo "    .venv exists and is kept as it is: stage 3 installs pyproject.toml's pins into it,"
  echo "    and reinstalls from the index an imscribe or imtypeset in it that was not installed"
  echo "    by name. For a clean one:  rm -rf .venv  and run this again."
fi
if command -v uv >/dev/null 2>&1; then
  uv venv --allow-existing --python "$PYTHON" .venv
  INSTALL=(uv pip install --python .venv/bin/python)
  REINSTALL=(uv pip install --python .venv/bin/python --no-deps)
  REINSTALL_EACH="--reinstall-package"
else
  echo "    uv not found, falling back to python -m venv"
  # Debian splits ensurepip into python3-venv, so this is the step that fails on
  # a fresh box. Stage 1 checks for it; this is the backstop for anyone running
  # the stages by hand.
  if ! "$PYTHON" -m venv .venv; then
    pkg="$("$PYTHON" -c 'import sys; v=sys.version_info; print(f"python{v.major}.{v.minor}-venv")')"
    echo
    echo "    'python -m venv' failed. On Debian/Ubuntu the venv machinery is a"
    echo "    separate package:"
    echo
    echo "        sudo apt install $pkg python3-pip-whl"
    echo
    exit 1
  fi
  # Not from the lock: everything it installs is checked against a hash, and an
  # upgrade of pip from the index would be the one thing that is not. The pip
  # the interpreter's ensurepip brings has hash-checking mode.
  if [ "$LOCKED" = 0 ]; then
    .venv/bin/python -m pip install --quiet --upgrade pip
  fi
  INSTALL=(.venv/bin/python -m pip install)
  REINSTALL=(.venv/bin/python -m pip install --no-deps --force-reinstall)
  REINSTALL_EACH=""
fi

echo
echo ">>> stage 3/4: install"
# Nothing here names a version. Locked, the lock names every one and its hashes,
# wheels only, and pptmstr is then installed from this checkout with nothing
# else, built by the hatchling the lock installed rather than one fetched into
# an isolated build. Unlocked, pyproject.toml pins imgui-bundle, imscribe and
# imtypeset exactly, as one unit, and the floors decide the rest. imscribe's is
# a beta, which an installer takes because the pin names it.
if [ "$LOCKED" = 1 ]; then
  echo "    locked: installing exactly what $LOCK names, every file checked against its hash"
  "${INSTALL[@]}" --require-hashes --only-binary :all: -r "$LOCK"
  "${INSTALL[@]}" --no-deps --no-build-isolation -e .
else
  echo "    UNLOCKED: $UNLOCKED_WHY, so every version but the three pins is today's newest"
  "${INSTALL[@]}" -e ".[dev]"
fi
# An exact pin is met by a source build of the same version (`make deps` installs
# imscribe from a directory), and the install above keeps it. Each one named here
# is replaced by the published wheel, and the reason is printed above the names.
REPLACE="$(.venv/bin/python -m pptmstr.pairing unpublished)"
if [ -n "$REPLACE" ]; then
  echo "    not installed by name, so reinstalled from the package index: $REPLACE"
  for requirement in $REPLACE; do
    if [ -n "$REINSTALL_EACH" ]; then
      REINSTALL+=("$REINSTALL_EACH" "${requirement%%==*}")
    fi
    if [ "$LOCKED" = 0 ]; then
      REINSTALL+=("$requirement")
    fi
  done
  if [ "$LOCKED" = 1 ]; then
    # The lock's own lines for them, hashes and all, so the replacement is
    # checked like everything else.
    # shellcheck disable=SC2086
    "$PYTHON" scripts/lock.py subset "$LOCK" $REPLACE > .venv/pptmstr-reinstall.lock
    REINSTALL+=(--require-hashes -r .venv/pptmstr-reinstall.lock)
  fi
  "${REINSTALL[@]}"
fi

echo
if [ "$SKIP_WINDOW" = 1 ]; then
  echo ">>> stage 4/4: skipped (--no-window)"
elif [ "$(uname -s)" = "Linux" ] && [ -z "${DISPLAY:-}" ] && ! command -v xvfb-run >/dev/null 2>&1; then
  # Headless with no virtual X server: the smoke test cannot pass, and failing
  # here would blame the build for the environment.
  echo ">>> stage 4/4: skipped — headless (no DISPLAY) and xvfb-run is not installed"
  echo "    Unaffected:  make test  make check"
  echo "    For the window test:  sudo apt install xvfb   then  make probe"
else
  echo ">>> stage 4/4: post-install probe (opens a window briefly)"
  .venv/bin/python scripts/probe.py --stage post
fi

echo
echo "Ready.  Run:  make run"
echo "What was installed, and whether the typesetting library is accepted beside it:"
echo "    .venv/bin/python -m pptmstr --version"
