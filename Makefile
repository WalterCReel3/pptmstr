.PHONY: help bootstrap probe smoke run test lint format typecheck check clean

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
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'
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

format: venv-check ## black, in place
	.venv/bin/black pptmstr scripts tests

typecheck: venv-check
	.venv/bin/mypy pptmstr

check: lint typecheck test   ## everything the CI gate would run

clean:
	rm -rf .mypy_cache .ruff_cache .pytest_cache dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
