"""
What every test file gets: the typeset seam cannot leak from one test into another.

The seam is module state -- one library context, one font registry, one operator
setting -- and two things about it are properties of a *process* that a test must
not inherit:

- **A library context whose ImGui context is gone.** The library's own
  ``ready()`` stays true after the ImGui context its fonts are on is destroyed,
  and a view then draws into an atlas that is freed. ``typeset.can_draw()``
  compares the current context with the one the fonts were loaded in, but that is
  a comparison of addresses and a context made later can be given the address of
  one destroyed earlier. So **destroying the context the fonts are in unloads the
  library first, whoever destroys it** (``_a_destroyed_context_takes_its_fonts``):
  no test file has to remember ``typeset.unload()`` for that to hold.
- **The operator's settings.** A test that switches typesetting off, a pane off, or
  gives a role another face or size, leaves the seam as it found it: the next
  file's tests are not run with a switch somebody else threw.

**With the library and without.** The library is found through
``IMTYPESET_LIBRARY`` and ``IMTYPESET_PYTHON``; ``make test`` sets them when the
built pair exists and ``make test IMTYPESET_LIBRARY=`` does not. A test that needs
it asks for ``seam``, which skips with the library's own reason when it cannot
draw, and runs otherwise.

It deliberately does not switch the seam off for tests that did not ask for it: the
files that draw panes against a mocked ``imgui`` do that themselves.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr import theme
from pptmstr.ui import typeset
from pptmstr.ui.typeset import gate

BOLD = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"


@pytest.fixture(autouse=True, scope="session")
def _a_destroyed_context_takes_its_fonts() -> Iterator[None]:
    try:
        from imgui_bundle import imgui
    except Exception:
        yield
        return
    real = imgui.destroy_context

    def destroy(context: Any = None) -> None:
        home = gate._GATE.home
        doomed = imgui.get_current_context() if context is None else context
        if home is not None and doomed == home:
            typeset.unload()
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
    settings = (state.typeset, state.scale, state.family, dict(state.roles), state.panes_off)
    yield
    if (state.typeset, state.scale, state.family, state.roles, state.panes_off) != settings:
        typeset.configure(
            typeset=settings[0],
            scale=settings[1],
            proportional=settings[2],
            roles=settings[3],
            panes={pane: pane not in settings[4] for pane in typeset.Pane},
        )


@pytest.fixture(scope="module")
def imgui() -> Iterator[Any]:
    """
    A bare ImGui context for a module, with Inconsolata as its first font.

    A frame needs a context and not a display, so nothing opens a window: the
    context has no backend and the atlas is built from the real font files.
    Destroyed afterwards, the library's context first.
    """
    bundle = pytest.importorskip("imgui_bundle")
    im = bundle.imgui
    context = im.create_context()
    io = im.get_io()
    io.display_size = im.ImVec2(1200.0, 800.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= im.BackendFlags_.renderer_has_textures.value
    medium = Path(bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"
    io.fonts.add_font_from_file_ttf(str(medium), 16.0)
    yield im
    typeset.unload()
    im.destroy_context(context)


@pytest.fixture
def seam(imgui: Any) -> Iterator[Any]:
    """
    The seam with the library's fonts loaded and the settings at their defaults.

    **Skips, with the reason, when the library cannot draw**: absent, refused, or
    its fonts not loaded. The fonts are loaded once a module and kept across its
    tests; a test that unloads them or loads its own has them put back here.
    """
    theme.set_theme("dark")
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})
    if not typeset.can_draw() and not typeset.load_fonts(BOLD):
        pytest.skip(f"needs the typesetting library: {typeset.unavailable_reason()}")
    yield imgui
    theme.set_theme("dark")
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})
