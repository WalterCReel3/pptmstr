.PHONY: help bootstrap deps typeset-env probe smoke run shot shot-xvfb test lint format format-file typecheck typecheck-all verify check clean

PY  := .venv/bin/python
PIP := .venv/bin/python -m pip

# xvfb-run needs an explicit screen: its default is 8-bit, and GLX will not give
# out an OpenGL 3.3 core context on an 8-bit visual. That failure surfaces as a
# context-creation error that reads like a driver bug.
XVFB_RUN ?= xvfb-run -a --server-args=-screen 0 1600x1000x24

ifeq ($(strip $(DISPLAY)),)
HEADLESS ?= 1
else
HEADLESS ?= 0
endif

ifeq ($(HEADLESS),1)
GUI := $(XVFB_RUN)
else
GUI :=
endif

# The imtypeset pair `make deps` builds (scripts/typeset_deps.py): the library and
# the ctypes package that was built with it. imscribe looks for the library only
# when IMTYPESET_LIBRARY names it and finds the package through IMTYPESET_PYTHON,
# so both are exported to every recipe here -- tests, the app, the window
# targets -- and always as a pair, because a library with another build's
# package fails at import:
#
#   neither is set by the caller   both are exported if `typeset_deps.py pair`
#                                  answers ok: the pair is whole and was built
#                                  from the commit imtypeset.ref names. If not,
#                                  neither is, and every recipe runs without the
#                                  library; a pair that is there and refused is
#                                  a warning on every run.
#   either is set by the caller    in the environment or on the command line,
#                                  empty or not: nothing is added, so a library
#                                  named from outside never meets this pair's
#                                  package. `make test IMTYPESET_LIBRARY=` is how
#                                  to run without the library.
#
# `make typeset-env` prints what a recipe gets and, on standard error, why.
TYPESET_DIR     ?= .deps/imtypeset
TYPESET_LIBRARY := $(TYPESET_DIR)/libimtypeset_bundle.so
TYPESET_PACKAGE := $(TYPESET_DIR)/pysnap
TYPESET_SET     := $(strip $(foreach v,IMTYPESET_LIBRARY IMTYPESET_PYTHON,$(if $(filter undefined,$(origin $(v))),,$(v))))
ifneq ($(TYPESET_SET),)
TYPESET_WHY := $(TYPESET_SET) set by the caller and left exactly as set; the pair in $(TYPESET_DIR) is exported whole or not at all
else ifeq ($(wildcard $(TYPESET_DIR)),)
TYPESET_WHY := there is no $(TYPESET_DIR); 'make deps' builds the pair
else ifeq ($(wildcard $(PY)),)
TYPESET_WHY := there is no $(PY) to check $(TYPESET_DIR) with; 'make bootstrap' first
else
TYPESET_WHY := $(shell $(PY) scripts/typeset_deps.py pair --pair $(TYPESET_DIR) 2>&1)
ifeq ($(TYPESET_WHY),ok)
export IMTYPESET_LIBRARY := $(abspath $(TYPESET_LIBRARY))
export IMTYPESET_PYTHON  := $(abspath $(TYPESET_PACKAGE))
TYPESET_WHY := the pair in $(TYPESET_DIR), built from the commit imtypeset.ref names
else
$(warning imtypeset is NOT exported and typeset panes fall back: $(TYPESET_WHY))
endif
endif
export TYPESET_WHY

# What installs into .venv: uv when there is one, as bootstrap.sh chooses, since
# a venv uv made has no pip in it. --reinstall-package because uv may otherwise
# keep its cached build of a directory whose version number did not change.
ifneq ($(shell command -v uv 2>/dev/null),)
INSTALL_IMSCRIBE := uv pip install --python $(PY) --reinstall-package imscribe
else
INSTALL_IMSCRIBE := $(PIP) install
endif

.DEFAULT_GOAL := help

help:              ## this list
	@echo "pptmstr targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "display: HEADLESS=$(HEADLESS)$(if $(GUI), — window targets run under: $(GUI))"

venv-check:
	@test -x $(PY) || { echo "no ./.venv — run 'make bootstrap' first"; exit 1; }

gui-check:
ifeq ($(HEADLESS),1)
	@command -v xvfb-run >/dev/null 2>&1 || { \
	  echo "no DISPLAY, and xvfb-run is not installed, so no window can open."; \
	  echo "    sudo apt install xvfb                 # then re-run this target"; \
	  echo "    make HEADLESS=0 <target>              # if you do have a display"; \
	  exit 1; }
endif

bootstrap:         ## probe, create .venv, install, smoke test
	./bootstrap.sh

# The part of bootstrap that pins imscribe and builds the imtypeset library, on its
# own: after a bump of imscribe.ref or imtypeset.ref, or once whatever stopped the
# library building is dealt with. IMSCRIBE_REPO, IMTYPESET_REPO, IMGUI_BUNDLE_REPO
# and IMGUI_REPO name where to fetch from (scripts/typeset_deps.py lists them),
# and IMTYPESET_BUNDLE_HEADERS=stand-in asks for upstream Dear ImGui's headers
# where the wheel's fork cannot be fetched (BUILT-FROM records which were used);
# IMSCRIBE_PIP_ARGS is added to the install, e.g. "--no-deps --no-index
# --no-build-isolation" where no package index is reachable -- which works in a
# .venv that already holds imscribe's build backend and what imscribe requires.
deps: venv-check   ## imscribe at imscribe.ref, and the imtypeset library for .venv
	$(PY) scripts/typeset_deps.py imscribe
	$(INSTALL_IMSCRIBE) $(IMSCRIBE_PIP_ARGS) .deps/imscribe
	$(PY) scripts/typeset_deps.py verify
	$(PY) scripts/typeset_deps.py library

typeset-env:       ## the IMTYPESET_* variables a recipe here runs with
	@echo "IMTYPESET_LIBRARY=$${IMTYPESET_LIBRARY-}"
	@echo "IMTYPESET_PYTHON=$${IMTYPESET_PYTHON-}"
	@echo "# $$TYPESET_WHY" >&2

probe:             ## stdlib-only environment diagnosis (no venv needed)
	@python3 scripts/probe.py --stage pre

smoke: venv-check gui-check   ## post-install probe: imports + a real window
	@$(PY) scripts/probe.py --stage post

run: venv-check gui-check     ## launch the orchestrator
	$(GUI) $(PY) -m pptmstr

bench: venv-check gui-check   ## measure idling CPU and cross-thread wake latency
	$(GUI) $(PY) scripts/bench_idle.py

shot: venv-check gui-check    ## render the UI to shots/shot.png
	$(GUI) $(PY) scripts/screenshot.py --argv --fake

# For a DISPLAY that is set and cannot be reached, which `HEADLESS` cannot see: it
# is computed from DISPLAY being empty. A sandbox that refuses socket(AF_UNIX) is
# that case, and `xvfb-run` is no way out of it either -- its server listens on a
# unix socket, so it hangs there rather than failing. scripts/xvfb_shot.sh says
# what it does instead. SHOT_ARGS reaches screenshot.py as written, so a quoted
# window name survives: make shot-xvfb SHOT_ARGS='--focus "NEEDS YOU"'.
FOCUS ?=
OUT   ?= shots/shot.png
shot-xvfb: venv-check         ## the same on a private Xvfb: FOCUS=BOARD OUT=shots/x.png
	scripts/xvfb_shot.sh --out $(OUT) $(if $(FOCUS),--focus $(FOCUS)) $(SHOT_ARGS)

test: venv-check   ## pytest
	$(PY) -m pytest -q

lint: venv-check   ## ruff check + black --check
	.venv/bin/ruff check pptmstr scripts tests
	.venv/bin/black --check pptmstr scripts tests

# The only target here that writes source files, and the only one that can cross a
# file boundary the task board relies on. A team run had one agent reformat another
# agent's untracked file -- untracked, so `git checkout` had nothing to restore from
# and there was no revert path at all. Tree-wide *reading* is not the hazard and is
# how an unowned failure becomes visible in the first place; tree-wide *writing* is.
format: venv-check ## black, in place, across the whole tree
	.venv/bin/black pptmstr scripts tests

# The safe path made the easy one, so an agent formatting its own work does not have
# to know the invocation. Several files may be given: `make format-file FILE="a b"`.
format-file: venv-check ## black, in place, on FILE= only
	@test -n "$(FILE)" || { echo 'usage: make format-file FILE=path/to/file.py'; exit 2; }
	.venv/bin/black $(FILE)

typecheck: venv-check ## mypy over the application
	.venv/bin/mypy pptmstr

# Widened coverage, deliberately outside `check` for now. `lint` reads all three
# directories and `typecheck` reads one, so a tree that passes `make check` has had
# two thirds of itself typechecked by nobody -- but turning that on is not the
# one-line change it looks like: at the strictness `pyproject.toml` sets for the
# application, `scripts/` and `tests/` carry several hundred findings, most of them
# missing annotations on test functions rather than defects.
#
# A separate target rather than a widened gate, because the two honest options are
# to fix the backlog or to relax the settings for these directories, and both are
# decisions rather than a Makefile edit. This makes the number visible to whoever
# takes it, which is the thing that was missing.
typecheck-all: venv-check ## mypy over the application, scripts and tests
	.venv/bin/mypy pptmstr scripts tests

# The differential probe against `/bin/bash`, in full. Outside `check` because it
# shells out tens of thousands of times; `make test` already runs its `NAMED` arm,
# which is the 20 cases somebody worked out and the arm measured to catch a real
# mutation. What this adds is the exhaustive and sampled arms, which cover the
# mistakes nobody thought of.
#
# Run it after any edit to `_split_segments`, `_METACHARACTERS` or `_SEPARATORS`.
# The suite can tell you the splitter still handles the cases we know about; only
# this can tell you it still agrees with the shell.
verify: venv-check ## the full bash differential probe for the segment split
	$(PY) scripts/verify_split_against_bash.py

check: lint typecheck test   ## everything the CI gate would run

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
