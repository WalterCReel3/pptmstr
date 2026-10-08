"""
What every test file gets: the typeset seam cannot leak from one test into another.

The seam is module state -- one atlas, one font registry, one operator setting --
and three things about it are properties of a *process* that a test must not inherit:

- **Faces loaded under an ImGui context that is gone.** ``typeset.available()`` is
  already false once the context is not the current one, so no pane takes the
  typeset branch with them; the fixture below also drops them, so that nothing holds
  ``ImFont`` pointers into a destroyed atlas for the rest of the run. "Not the
  current one" is a comparison of addresses, and a context made later can be given
  the address of one destroyed earlier, so **destroying the context the faces are
  in forgets them, whoever destroys it** (``_a_destroyed_context_takes_its_faces``):
  no test file has to remember ``typeset.reset()`` for that to hold.
- **The operator's settings.** A test that switches typesetting off, a pane off, or
  gives a role another face or size, leaves the seam as it found it: the next
  file's tests are not run with a switch somebody else threw.
- **The build budget.** A real frame may spend a few milliseconds building views and
  leaves the rest for the next frame, which makes what one frame shows depend on how
  fast the machine is. Every test here runs with no budget, so one frame builds
  everything; the tests of the budget itself set one.
- **What layout was found to cost.** The seam learns a rate from each layout it
  times and decides by it how much a frame under a budget may build and whether a
  new width waits. A rate learned in one test is a fact about how fast that test
  ran; each test starts with none and leaves none.

**Which advances lay text out is the run's.** ``typeset.METRICS`` is one constant
in code; ``PPTMSTR_TEST_METRICS=file`` sets it for a whole run of the suite, so the
other path is tested without an edit. A test that holds under one path only says
which, and why, where it skips.

It deliberately does not switch the seam off for tests that did not ask for it: the
files that draw panes against a mocked ``imgui`` do that themselves, and a seam call
made outside a frame raises rather than crashing the interpreter.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from typing import Any

import pytest

from pptmstr.ui import typeset

if os.environ.get("PPTMSTR_TEST_METRICS"):
    typeset.METRICS = os.environ["PPTMSTR_TEST_METRICS"]


@pytest.fixture(autouse=True, scope="session")
def _a_destroyed_context_takes_its_faces() -> Iterator[None]:
    try:
        from imgui_bundle import imgui
    except Exception:
        yield
        return
    real = imgui.destroy_context

    def destroy(context: Any = None) -> None:
        home = typeset._STATE.home
        doomed = imgui.get_current_context() if context is None else context
        if home is not None and doomed == home:
            typeset.reset()
        if context is None:
            real()
        else:
            real(context)

    imgui.destroy_context = destroy
    yield
    imgui.destroy_context = real


@pytest.fixture(autouse=True)
def _typeset_does_not_leak() -> Iterator[None]:
    state = typeset._STATE
    if state.fonts is not None and not typeset.available():
        typeset.reset()
    settings = (state.typeset, state.scale, state.family, dict(state.roles), state.panes_off)
    budget = typeset.set_budget(math.inf)
    state.layout_rate.clear()
    yield
    typeset.set_budget(budget)
    state.layout_rate.clear()
    if (state.typeset, state.scale, state.family, state.roles, state.panes_off) != settings:
        typeset.configure(
            typeset=settings[0],
            scale=settings[1],
            proportional=settings[2],
            roles=settings[3],
            panes={pane: pane not in settings[4] for pane in typeset.Pane},
        )
