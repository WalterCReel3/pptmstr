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
# advtext is on no package index. Stage 3 fetches it at the commit advtext.ref
# names, installs that, and then builds the imtypeset library advtext draws
# through, from the commit imtypeset.ref names, for the interpreter in .venv
# (scripts/typeset_deps.py; everything lands in .deps/). The sources come from
# GitHub unless a variable names another repository -- a URL, a file:// URL or a
# local path:
#
#   ADVTEXT_REPO  IMTYPESET_REPO  IMGUI_BUNDLE_REPO  IMGUI_REPO
#
# ADVTEXT_PIP_ARGS is added to the advtext install and to nothing else. This
# script as a whole needs a package index: a .venv it has just made holds
# nothing, so advtext's build backend and everything pyproject.toml names come
# from one. Where there is no index, `make deps` repeats the advtext and library
# part alone in a .venv that already holds them, and there
# ADVTEXT_PIP_ARGS="--no-deps --no-index --no-build-isolation" is what it takes.
#
# Fetching needs git, and bootstrap stops without advtext. The library also
# needs a C++ compiler and Linux; without those bootstrap says so and carries
# on, because pptmstr runs without the library and typeset panes fall back.
#
# Usage: ./bootstrap.sh [--no-window] [--python PATH]
set -euo pipefail

cd "$(dirname "$0")"

if [ -n "${ADVTEXT_SRC:-}" ]; then
  echo "note: ADVTEXT_SRC is set and is not read. advtext is installed at the commit" >&2
  echo "      in advtext.ref; ADVTEXT_REPO names a repository to fetch that commit from." >&2
fi

# Keep in step with requires-python in pyproject.toml.
MIN_MAJOR=3
MIN_MINOR=11

SKIP_WINDOW=0
PYTHON=""

while [ $# -gt 0 ]; do
  case "$1" in
    --no-window) SKIP_WINDOW=1; shift ;;
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

find_python() {
  for p in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1 && meets_floor "$p"; then
      command -v "$p"
      return 0
    fi
  done
  return 1
}

if [ -n "$PYTHON" ]; then
  meets_floor "$PYTHON" || {
    echo "$PYTHON does not meet the Python ${MIN_MAJOR}.${MIN_MINOR}+ floor" >&2
    exit 1
  }
elif ! PYTHON="$(find_python)"; then
  # No usable interpreter at all: run the probe anyway so the diagnosis and the
  # platform-appropriate install command come from one place.
  echo "No Python ${MIN_MAJOR}.${MIN_MINOR}+ found on PATH."
  echo
  python3 scripts/probe.py --stage pre || true
  exit 1
fi

echo ">>> stage 1/4: pre-install probe (using $PYTHON)"
"$PYTHON" scripts/probe.py --stage pre

echo
echo ">>> stage 2/4: virtualenv"
if command -v uv >/dev/null 2>&1; then
  uv venv --python "$PYTHON" .venv
  INSTALL=(uv pip install --python .venv/bin/python)
  # uv may otherwise keep its cached build of a directory whose version number
  # did not change, which is every bump of advtext.ref.
  REINSTALL_ADVTEXT="--reinstall-package advtext"
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
  .venv/bin/python -m pip install --quiet --upgrade pip
  INSTALL=(.venv/bin/python -m pip install)
  REINSTALL_ADVTEXT=""
fi

echo
echo ">>> stage 3/4: install"
# advtext first: the resolver cannot find a bare `advtext` on an index, and an
# already-installed distribution of that name is what satisfies it. Installed
# as a copy, not editable, so what .venv holds is the pinned commit whatever
# happens to .deps afterwards; `verify` then holds the interpreter to it, since
# anything ahead of site-packages on sys.path would win without a word.
.venv/bin/python scripts/typeset_deps.py advtext
# shellcheck disable=SC2086  # both are lists of options, possibly empty
"${INSTALL[@]}" $REINSTALL_ADVTEXT ${ADVTEXT_PIP_ARGS:-} .deps/advtext
.venv/bin/python scripts/typeset_deps.py verify
"${INSTALL[@]}" -e ".[dev]"
# Again, after the last thing that installs: pyproject.toml names advtext too,
# and what that install left in .venv is what runs.
.venv/bin/python scripts/typeset_deps.py verify
# After the install: the library is built for the imgui-bundle wheel that is now
# in .venv. The step reports its own failures and exits 0 for them; the guard is
# for the step itself breaking, which must not take bootstrap with it either.
.venv/bin/python scripts/typeset_deps.py library || {
  echo "    The imtypeset library step ended abnormally. pptmstr runs without the" >&2
  echo "    library and typeset panes fall back; 'make deps' tries again." >&2
}

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
