.PHONY: help bootstrap probe smoke run test lint format format-file typecheck typecheck-all check clean

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

probe:             ## stdlib-only environment diagnosis (no venv needed)
	@python3 scripts/probe.py --stage pre

smoke: venv-check gui-check   ## post-install probe: imports + a real window
	@$(PY) scripts/probe.py --stage post

run: venv-check gui-check     ## launch the orchestrator
	$(GUI) $(PY) -m pptmstr

bench: venv-check gui-check   ## measure idling CPU and cross-thread wake latency
	$(GUI) $(PY) scripts/bench_idle.py

shot: venv-check gui-check    ## render the UI to shot.png
	$(GUI) $(PY) scripts/screenshot.py --argv --fake

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

check: lint typecheck test   ## everything the CI gate would run

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
