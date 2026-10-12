"""
What every test file gets: the typeset seam cannot leak from one test into another.

The seam is module state -- one library context, one font registry, one operator
setting -- and one thing about it is a property of a *process* that a test must
not inherit: **the operator's settings.** A test that switches typesetting off,
a pane off, or gives a role another face or size, leaves the seam as it found
it: the next file's tests are not run with a switch somebody else threw.

A library context whose ImGui context is gone is not such a thing: the library
answers that it cannot draw in any context but the one its fonts were loaded
in, a destroyed one included (``typesetter.ready``), so a test file that
destroys its context without ``typeset.unload()`` leaves nothing the next one
can draw into.

**With the library and without.** The library is found through
``IMTYPESET_LIBRARY`` and ``IMTYPESET_PYTHON``; ``make test`` sets them when the
built pair exists and ``make test IMTYPESET_LIBRARY=`` does not. A test that needs
it asks for ``seam``, which runs where the library draws, skips with the reason
where none was named, and **fails** where one was named and cannot draw
(``needs_the_library``).

It deliberately does not switch the seam off for tests that did not ask for it: the
files that draw panes against a mocked ``imgui`` do that themselves.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr import theme
from pptmstr.ui import typeset
from pptmstr.ui.typeset import gate

BOLD = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"


@pytest.fixture(autouse=True)
def _typeset_does_not_leak() -> Iterator[None]:
    state = typeset._STATE
    settings = (state.typeset, state.scale, state.family, dict(state.roles), state.panes_off)
    live = state.live_px
    yield
    if state.live_px != live:
        # A test told the seam the UI font was another height (``columns_hold``).
        state.live_px = live
        typeset.roles.moved()
    if (state.typeset, state.scale, state.family, state.roles, state.panes_off) != settings:
        typeset.configure(
            typeset=settings[0],
            scale=settings[1],
            proportional=settings[2],
            roles=settings[3],
            panes={pane: pane not in settings[4] for pane in typeset.Pane},
        )


def needs_the_library(reason: str | None) -> None:
    """
    Stop a test that draws through the library where the library cannot draw:
    skipped where none was named, failed where one was.
    """
    if os.environ.get("IMTYPESET_LIBRARY"):
        pytest.fail(f"IMTYPESET_LIBRARY is set and the typesetting library cannot draw: {reason}")
    pytest.skip(f"needs the typesetting library: {reason}")


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

    **Skips, with the reason, where no library was named** (``IMTYPESET_LIBRARY``
    unset or empty). **Where one was named and cannot draw, the test fails**:
    a pair that is stale and refused, or a library some earlier test stopped
    for the rest of the process, would otherwise skip every test that draws and
    leave the run green. The fonts are loaded once a module and kept across its
    tests; a test that unloads them or loads its own has them put back here.
    """
    theme.set_theme("dark")
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})
    if not typeset.can_draw() and not typeset.load_fonts(BOLD):
        needs_the_library(typeset.unavailable_reason())
    yield imgui
    theme.set_theme("dark")
    typeset.configure(typeset=True, faces=typeset.DEFAULT_PRESET, scale=1.0, panes={})


EVEN_EM_PX = 16.0
"""
The em ``columns_hold`` sets the monospace roles at: Inconsolata's advance is
half an em, so 8 px a column.
"""


@pytest.fixture
def columns_hold(seam: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """
    The seam where the library as it is keeps a monospace column, so that a pane
    takes the branch that draws a diff with the library's diff view by its own
    measurement and not because a test said so.

    The library lays a column out from the font file and ImGui draws it from
    the rasteriser's advance; the two agree where the em is even
    (``scripts/measure_typeset_diff.py --em``). A role's size is the UI font's
    pixel height plus whole steps, and ImGui reports a whole pixel height, so
    no setting of the product reaches an even em: here the seam is told the UI
    font is 16.78 px tall, which is an em of 16 in Inconsolata. Everything
    after that is the product's: the themes, the measurement
    (``gate.columns``), ``diffs.adopted``, the views.

    Fails, and does not skip, if the measurement then says the columns do not
    hold: every test on this fixture would otherwise pass on the other branch.
    """
    from advtext import Face, em_size_for_pixel_height

    from pptmstr.ui.typeset import diffs, roles

    tall = EVEN_EM_PX / em_size_for_pixel_height(Face.CODE, 1.0)
    monkeypatch.setattr(gate, "_live_font_size", lambda: tall)
    roles.follow(tall)
    found = diffs.columns()
    assert found.holds, f"the library does not keep a column at an em of {EVEN_EM_PX:g}: {found}"
    assert diffs.ADOPT == "measured" and diffs.adopted()
    yield seam
