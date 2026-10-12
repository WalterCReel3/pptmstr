"""
The gate: whether the library can draw, its fonts, and the sentence when it cannot.

Runs with the library and without. A test that needs it asks for ``seam``
(``tests/conftest.py``), which skips with the library's own reason when it is
not there; the tests of what pptmstr does *without* it need nothing, and the ones
that need a process of their own -- the library's gate is tried once a process --
start one.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import advtext
import pytest
from advtext import Face, registered_fonts, typesetter

from pptmstr import theme
from pptmstr.ui import typeset
from pptmstr.ui.typeset import Pane, gate, roles
from tests.conftest import BOLD

PACKAGE = Path(typeset.__file__).resolve().parent

PAST_THE_FACADE = {"typesetter", "markdown_rules"}
"""
What the seam imports from ``advtext`` that is not in ``advtext.__all__``, and why.

- ``typesetter``: the module itself. ``module``, ``trouble``, ``unload``,
  ``font`` and ``why_not_ready`` are reached through it and nowhere else; they
  are names of the module's own list and not of the facade's, and the library's
  examples import it the same way.
- ``markdown_rules``: for its private ``_parser``, in ``gate._reader`` and nowhere
  else. The library has no supported place for a host's reader rules (request P7
  of ``HANDOFF-advtext.md``), and without them a pane hides what was written:
  HTML, an image, the stars of a glob, where a link goes. ``tests/
  test_typeset_views.py`` has the probe that fails when the reach stops working.
"""


def _run(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    """
    ``script`` in an interpreter of its own, with the library's two variables as given.
    """
    clean = {k: v for k, v in os.environ.items() if not k.startswith("IMTYPESET_")}
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**clean, **env},
    )


# -- the boundary --------------------------------------------------------------


def _advtext_imports(path: Path) -> list[tuple[str, str]]:
    """
    Every ``(module, name)`` a file imports from the library, wherever in the file.
    """
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found += [(alias.name, "") for alias in node.names if alias.name.startswith("advtext")]
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("advtext"):
            found += [(node.module or "", alias.name) for alias in node.names]
    return found


def test_no_other_module_imports_the_layout_library() -> None:
    """
    One seam means one importer. A pane that reaches past it has its own idea of the
    fonts, the palette and who keeps a view alive, which is what this package is for.
    """
    application = PACKAGE.parent.parent
    offenders = [
        str(path.relative_to(application))
        for path in sorted(application.rglob("*.py"))
        if PACKAGE not in path.parents and _advtext_imports(path)
    ]
    assert offenders == []


def test_the_seam_imports_the_librarys_facade_and_one_named_module_past_it() -> None:
    """
    The library promises the names in ``advtext.__all__`` and nothing behind them.
    Every import the package makes is ``from advtext import <name>``, the name is
    on that list or is one ``PAST_THE_FACADE`` gives a reason for, and each of
    those is in fact imported -- a reason for a reach nobody makes is a stale list.
    """
    found = [each for path in sorted(PACKAGE.glob("*.py")) for each in _advtext_imports(path)]
    assert found, "the package no longer imports the library at all"
    assert {module for module, _ in found} == {"advtext"}, "a module behind the facade"
    past = {name for _, name in found if name not in advtext.__all__}
    assert past == PAST_THE_FACADE


@pytest.mark.parametrize("module", ["roles", "parts"])
def test_policy_and_parts_import_neither_the_library_nor_imgui(module: str) -> None:
    """
    What pptmstr decides before any library is asked is importable, and the same,
    where there is none.
    """
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    names = {
        name
        for node in top
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
    }
    assert not {name for name in names if name.split(".")[0] in ("advtext", "imgui_bundle")}
    assert _advtext_imports(PACKAGE / f"{module}.py") == []


# -- without the library ---------------------------------------------------------

_LOAD_IN_A_BARE_CONTEXT = (
    "from pathlib import Path\n"
    "import imgui_bundle\n"
    "from imgui_bundle import imgui\n"
    "import pptmstr.app\n"
    "from pptmstr import theme\n"
    "from pptmstr.ui import typeset\n"
    "imgui.create_context()\n"
    "io = imgui.get_io()\n"
    "io.backend_flags |= imgui.BackendFlags_.renderer_has_textures.value\n"
    "fonts = Path(imgui_bundle.__file__).parent / 'assets' / 'fonts'\n"
    "io.fonts.add_font_from_file_ttf(str(fonts / 'Inconsolata-Medium.ttf'), 16.0)\n"
    "typeset.configure(typeset=True, panes={})\n"
    "theme._load_typeset_faces()\n"
    "typeset.pre_frame()\n"
    "print(typeset.can_draw(), typeset.available())\n"
    "print([pane.value for pane in typeset.Pane if typeset.enabled(pane)])\n"
    "print(typeset.unavailable_reason())\n"
    "typeset.unload()\n"
)


def test_with_no_library_named_no_pane_is_typeset_and_the_menu_has_a_sentence() -> None:
    """
    ``IMTYPESET_LIBRARY`` unset is the ordinary state of a machine the library was
    never built on. The application's own font callback runs, nothing is typeset,
    and what the Text menu will say is one sentence that says *not installed*.
    """
    done = _run(_LOAD_IN_A_BARE_CONTEXT)
    assert done.returncode == 0, done.stderr
    drawing, panes, reason = done.stdout.strip().split("\n")
    assert drawing == "False False"
    assert panes == "[]"
    assert reason == "the typesetting library is not installed (IMTYPESET_LIBRARY is not set)"


def test_a_library_that_is_named_and_cannot_be_used_is_refused_in_its_own_words(
    tmp_path: Path,
) -> None:
    """
    Named and unusable is a fault the operator must act on, where not named is
    not: the sentence says *refused* and quotes what the library said.
    """
    done = _run(_LOAD_IN_A_BARE_CONTEXT, IMTYPESET_LIBRARY=str(tmp_path / "not-there.so"))
    assert done.returncode == 0, done.stderr
    drawing, panes, reason = done.stdout.strip().split("\n")
    assert (drawing, panes) == ("False False", "[]")
    assert reason.startswith("the typesetting library was refused (") and reason.endswith(")")
    assert "IMTYPESET_LIBRARY is not set" not in reason


def test_the_application_imports_with_the_library_absent_and_says_why() -> None:
    """
    ``typeset=False`` has to be a working application on a machine where the library
    is missing or broken, and the menu has to be able to say which.
    """
    script = (
        "import sys\n"
        "sys.modules['advtext'] = None\n"
        "from pptmstr.ui import typeset\n"
        "import pptmstr.app, pptmstr.theme\n"
        "assert not typeset.can_draw() and not typeset.enabled()\n"
        "assert not typeset.obtainable()\n"
        "typeset.configure(typeset=True, faces='mono', scale=1.25)\n"
        "assert typeset.load_fonts() is False\n"
        "typeset.pre_frame()\n"
        "typeset.unload()\n"
        "assert typeset.load_fonts() is False\n"
        "assert not typeset.obtainable()\n"
        "assert typeset.prose_parts('a\\n') == (typeset.Part('a\\n'),)\n"
        "print(typeset.unavailable_reason())\n"
    )
    done = _run(script)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("the text library is not installed (ModuleNotFoundError")


def _a_library_that_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The library's gate answering yes, whether or not there is a library: what is
    under test is what pptmstr does when the step after it goes wrong. Nothing
    is sized at the load: a theme is sized from the library's own font metrics,
    and what stands in for the library here has none.
    """
    monkeypatch.setattr(typesetter, "module", lambda: object())
    monkeypatch.setattr(gate, "_register", lambda bold, prose: None)
    monkeypatch.setattr(gate.themes, "document_theme", lambda: None)


def test_a_font_load_that_raises_leaves_pptmstr_drawing_and_the_reason_said(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The callback hello_imgui calls for fonts is not one an exception may leave:
    ``theme.load_fonts`` reaches the gate from inside it. Whatever the library
    raises there, the call returns, every pane is on its own renderer, and the
    sentence carries what was raised.
    """

    def explode() -> bool:
        raise OSError("the atlas is full")

    _a_library_that_accepts(monkeypatch)
    monkeypatch.setattr(typesetter, "load_fonts", explode)
    typeset.configure(typeset=True, panes={})

    assert typeset.load_fonts(BOLD) is False, "the gate's own call returns"
    theme._load_typeset_faces()

    assert not typeset.can_draw() and not typeset.obtainable()
    assert not typeset.enabled() and not any(typeset.enabled(pane) for pane in Pane)
    assert typeset.unavailable_reason() == (
        "the typesetting library failed and is not in use (OSError: the atlas is full)"
    )
    typeset.pre_frame()
    assert not typeset.can_draw()


def test_a_failure_where_the_gate_is_only_asked_is_caught_there_too(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``available()`` is asked by every pane every frame, inside a window that is
    open. It answers false for a library that raises when asked.
    """

    def explode() -> bool:
        raise RuntimeError("the context is gone")

    _a_library_that_accepts(monkeypatch)
    monkeypatch.setattr(typesetter, "load_fonts", lambda: True)
    monkeypatch.setattr(typesetter, "ready", lambda: True)
    assert typeset.load_fonts(BOLD)
    monkeypatch.setattr(typesetter, "ready", explode)

    assert not typeset.can_draw()
    assert "RuntimeError: the context is gone" in (typeset.unavailable_reason() or "")
    monkeypatch.setattr(typesetter, "ready", lambda: True)
    assert not typeset.can_draw(), "and it is not offered again until the fonts are loaded again"


@pytest.mark.parametrize(
    ("accepted", "loaded", "said", "noted", "sentence"),
    [
        (False, False, "ImportError: wrong wheel", "", " was refused (ImportError: wrong wheel)"),
        (True, False, "ValueError: bad font", "", "'s fonts did not load (ValueError: bad font)"),
        (True, True, "KeyError: 7", "", " stopped while drawing (KeyError: 7)"),
        (True, True, "KeyError: 7", "a block", " stopped while drawing (a block)"),
    ],
)
def test_the_sentence_tells_refused_from_fonts_from_stopped_while_drawing(
    monkeypatch: pytest.MonkeyPatch,
    accepted: bool,
    loaded: bool,
    said: str,
    noted: str,
    sentence: str,
) -> None:
    """
    Three faults an operator acts on differently. A library that was never
    accepted says which kind of thing it is itself; one that was, and is not in
    use now, is told apart by what pptmstr saw happen.
    """
    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.REFUSED)
    monkeypatch.setattr(typesetter, "module", lambda: None)
    monkeypatch.setattr(typesetter, "reason", lambda: said)
    monkeypatch.setattr(typesetter, "trouble", lambda: noted)
    monkeypatch.setattr(gate, "_GATE", gate._Gate(asked=True, accepted=accepted, loaded=loaded))

    assert typeset.unavailable_reason() == "the typesetting library" + sentence


def test_fonts_the_library_reports_unloaded_are_said_with_what_it_noted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(typesetter, "module", lambda: object())
    monkeypatch.setattr(typesetter, "ready", lambda: False)
    monkeypatch.setattr(typesetter, "trouble", lambda: "no Dear ImGui context")
    monkeypatch.setattr(gate, "_GATE", gate._Gate(asked=True, accepted=True))

    assert typeset.unavailable_reason() == (
        "the typesetting library's fonts did not load (no Dear ImGui context)"
    )


# -- the wiring ------------------------------------------------------------------


def test_the_application_unloads_the_library_before_its_context_goes() -> None:
    """
    ``unload`` is worth what its caller is. Read off the source, as the idling
    wiring is: the runner's ``before_exit`` is the seam's ``unload``.
    """
    import inspect

    from pptmstr import app

    assert "runner.callbacks.before_exit = typeset.unload" in inspect.getsource(app.main)


# -- with the library ------------------------------------------------------------


def test_nothing_is_typeset_until_the_faces_are_loaded(imgui: Any) -> None:
    typeset.unload()
    typeset.configure(typeset=True, mono=False)
    assert not typeset.can_draw()
    assert not typeset.enabled()
    assert typeset.unavailable_reason() == "the typesetting library's fonts have not been loaded"


def test_the_setting_turns_it_off_with_the_faces_still_loaded(seam: Any) -> None:
    assert typeset.enabled()
    typeset.configure(typeset=False, mono=False)
    assert typeset.can_draw()
    assert not typeset.enabled()


NO_PANE = "no pane sets its text through the typesetting library in this build"


def test_with_the_library_drawing_and_no_pane_adopted_the_switch_is_not_offered(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The library can draw and the operator has everything on; no pane's typeset
    branch is built on it, so no pane takes one. A switch that changed nothing
    on screen would read as broken, so the menu's question is answered no, with
    a sentence that is not a fault of the library's.
    """
    monkeypatch.setattr(roles, "ADOPTED", frozenset())
    assert typeset.can_draw() and typesetter.ready()
    assert not any(typeset.enabled(pane) for pane in Pane)
    assert not typeset.available() and not typeset.obtainable()
    assert typeset.unavailable_reason() == NO_PANE

    monkeypatch.setattr(roles, "ADOPTED", frozenset({Pane.BRIEF}))
    assert typeset.available() and typeset.obtainable()
    assert typeset.unavailable_reason() is None
    assert [pane for pane in Pane if typeset.enabled(pane)] == [Pane.BRIEF]


def test_the_vendored_bold_monospace_is_the_librarys_bold_code_face(seam: Any) -> None:
    """
    Without the registration the library backs bold code with a proportional bold
    and bold code stops being monospace.
    """
    assert registered_fonts()[Face.CODE_BOLD] == BOLD.resolve()


def test_a_family_chosen_is_given_to_the_library_between_frames_and_taken_back(
    seam: Any,
) -> None:
    """
    The library reads its font files when its context is made, so a family with a
    file of its own is a load. Not in the click, which arrives inside a frame: in
    the next ``pre_frame``. Every document made before is stale either way.
    """
    droid = roles.prose_path(next(each for each in typeset.PROPORTIONALS if each.file))
    assert Face.PROSE not in registered_fonts()
    epoch = typeset._STATE.epoch

    typeset.configure(typeset=True, proportional="droid")
    assert Face.PROSE not in registered_fonts(), "nothing is registered inside a frame"
    typeset.pre_frame()
    assert registered_fonts()[Face.PROSE] == droid
    assert typeset.can_draw() and typeset._STATE.epoch > epoch
    loaded = len(seam.get_io().fonts.fonts)
    typeset.pre_frame()
    assert len(seam.get_io().fonts.fonts) == loaded, "and only once"

    typeset.configure(typeset=True, proportional=typeset.DEFAULT_PROPORTIONAL)
    typeset.pre_frame()
    assert Face.PROSE not in registered_fonts()
    assert typeset.can_draw()


def test_a_saved_family_is_the_librarys_from_the_first_load(seam: Any) -> None:
    """
    The app configures before the atlas exists; the first load is what applies it.
    """
    typeset.unload()
    typeset.configure(typeset=True, proportional="droid")
    assert typeset.load_fonts(BOLD)
    assert registered_fonts()[Face.PROSE].name == "DroidSans.ttf"
    typeset.configure(typeset=True, proportional=typeset.DEFAULT_PROPORTIONAL)
    typeset.pre_frame()
    assert Face.PROSE not in registered_fonts()


def test_the_body_size_follows_the_ui_font_between_frames(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``hello_imgui`` scales the UI font for the display and the library draws at the
    size its theme names, so the size is read from what ImGui reports between
    frames. A document built at the old size is stale.
    """
    monkeypatch.setattr(typeset._STATE, "live_px", 16.0)
    epoch = typeset._STATE.epoch
    monkeypatch.setattr(gate, "_live_font_size", lambda: 24.0)
    typeset.pre_frame()
    assert typeset.body_px() == 24.0 and typeset._STATE.epoch == epoch + 1
    typeset.pre_frame()
    assert typeset._STATE.epoch == epoch + 1


def test_unloading_stops_the_library_drawing_and_loading_starts_it_again(seam: Any) -> None:
    typeset.unload()
    assert not typeset.can_draw() and not typesetter.ready()
    assert typeset.unavailable_reason() == "the typesetting library's fonts have not been loaded"
    assert typeset.load_fonts(BOLD)
    assert typeset.can_draw()
    assert typeset.unavailable_reason() == (None if roles.ADOPTED else NO_PANE)


def test_faces_loaded_under_one_context_are_not_available_under_another(seam: Any) -> None:
    """
    The library's fonts are on one context's atlas, and its ``ready()`` answers
    for the context that is current. A second context made beside the first
    does not find the seam switched on: ``enabled()`` true there is a pane
    drawing into an atlas that is not the current one. The sentence is the
    library's own.
    """
    home = seam.get_current_context()
    assert typeset.can_draw() and typeset.enabled()
    other = seam.create_context()
    try:
        seam.set_current_context(other)
        assert not typesetter.ready()
        assert not typeset.can_draw() and not typeset.enabled()
        assert typeset.unavailable_reason() == (
            "the typesetting library's fonts are in another window's context "
            f"({typesetter.why_not_ready()})"
        )
        assert "is not the current one" in typesetter.why_not_ready()
        assert typeset.face_font("CODE") is None
    finally:
        seam.set_current_context(home)
        seam.destroy_context(other)
    assert typeset.can_draw() and typeset.enabled()


def _a_context_with_a_font(im: Any) -> Any:
    import imgui_bundle

    context = im.create_context()
    im.set_current_context(context)
    io = im.get_io()
    io.display_size = im.ImVec2(1200.0, 800.0)
    io.backend_flags |= im.BackendFlags_.renderer_has_textures.value
    medium = Path(imgui_bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"
    io.fonts.add_font_from_file_ttf(str(medium), 16.0)
    return context


def test_a_context_destroyed_with_the_faces_in_it_and_nobody_told_takes_them_with_it(
    seam: Any,
) -> None:
    """
    Nobody calls ``unload()`` here, and the seam keeps no record of where the
    fonts are. The context they were loaded in is destroyed under the library;
    the next context made is often given that one's address, with a font of
    the test's own where the library's stood. In neither that context nor the
    one the module began with is the seam switched on over an atlas that is
    gone, and a load in a live context is the whole way back.
    """
    home = seam.get_current_context()
    doomed = _a_context_with_a_font(seam)
    try:
        assert typeset.load_fonts(BOLD) and typeset.can_draw()
    finally:
        seam.destroy_context(doomed)
        seam.set_current_context(home)
    assert not typeset.can_draw() and not typeset.enabled()
    assert "another window's context" in (typeset.unavailable_reason() or "")
    again = _a_context_with_a_font(seam)
    try:
        assert not typeset.can_draw() and typeset.face_font("CODE") is None
        assert typeset.load_fonts(BOLD) and typeset.can_draw()
        assert typeset.face_font("CODE") is not None
    finally:
        seam.destroy_context(again)
        seam.set_current_context(home)
    assert not typeset.can_draw()
    assert typeset.load_fonts(BOLD) and typeset.can_draw()


def test_the_epoch_the_atlas_guard_follows_moves_with_sizes_and_with_a_load(seam: Any) -> None:
    """
    ``theme.keep_baked_sizes`` lets go of every size on ImGui's atlas when this
    moves. A text scale that did not move it would leave the sizes of the scale
    before on the atlas for good, and a load that did not would leave the sizes
    of fonts nothing draws from again.
    """
    before = gate.epoch()
    typeset.configure(typeset=True, scale=1.125)
    scaled = gate.epoch()
    assert scaled > before
    typeset.configure(typeset=True, scale=1.125)
    assert gate.epoch() == scaled
    assert typeset.load_fonts(BOLD)
    assert gate.epoch() > scaled


# -- a call into the library that raises something of its own ---------------------


def _log_since(mark: int) -> list[tuple[str, str]]:
    from pptmstr.log import LOG

    entries, _ = LOG.snapshot()
    return [(entry.level.name, entry.text) for entry in entries[mark:] if entry.source == "typeset"]


def _log_mark() -> int:
    from pptmstr.log import LOG

    return len(LOG.snapshot()[0])


def test_what_a_call_into_the_library_raises_is_the_library_stopping_said_and_logged_once(
    seam: Any,
) -> None:
    """
    The library passes on whatever it does not take for its own failure: a name
    a newer library dropped, a string it could not encode. Raised out of a draw
    that leaves the pane on every frame the text is on screen. Through
    ``into_library`` it is the stop a draw already handles -- the same frame
    still answers ``can_draw``, the next does not -- with the exception's type
    in the sentence, and one entry in the log with the traceback beside it
    however many calls raise in that frame.
    """
    mark = _log_mark()
    for attempt in range(2):
        with pytest.raises(advtext.TypesetUnavailable, match="AttributeError") as raised:
            with gate.into_library("drawing a view"):
                raise AttributeError(f"'FileDiff' object has no attribute 'label' ({attempt})")
        assert isinstance(raised.value.__cause__, AttributeError)
    assert typeset.can_draw(), "the frame it happened in is drawn as it began"
    logged = _log_since(mark)
    assert [level for level, _ in logged] == ["ERROR", "DEBUG"], logged
    assert logged[0][1] == (
        "the library raised AttributeError while drawing a view: "
        "'FileDiff' object has no attribute 'label' (0)"
    )
    assert "Traceback" in logged[1][1] and "AttributeError" in logged[1][1]

    typeset.pre_frame()
    assert not typeset.can_draw() and not typeset.enabled(Pane.INBOX)
    reason = typeset.unavailable_reason() or ""
    assert "AttributeError" in reason and "while drawing a view" in reason
    assert typeset.load_fonts(BOLD), "a load is the way back"


def test_the_librarys_own_stop_and_what_is_not_an_error_pass_through_as_they_are(
    seam: Any,
) -> None:
    mark = _log_mark()
    with pytest.raises(advtext.TypesetUnavailable, match="^the library said no$"):
        with gate.into_library("drawing a view"):
            raise advtext.TypesetUnavailable("the library said no")
    with pytest.raises(KeyboardInterrupt):
        with gate.into_library("drawing a view"):
            raise KeyboardInterrupt
    with gate.into_library("drawing a view"):
        pass
    assert _log_since(mark) == [] and gate._GATE.stopping is None
    typeset.pre_frame()
    assert typeset.can_draw()


# -- the columns: what a measurement is of, and what it leaves behind ---------------


def _baked(im: Any) -> dict[int, list[float]]:
    """
    Every baked size on the atlas, by the index of its font, none made or marked.
    """
    atlas = im.get_io().fonts
    return {
        index: [size for size, _ in theme.baked_sizes(atlas, font)]
        for index, font in enumerate(atlas.fonts)
        if theme.baked_sizes(atlas, font)
    }


@pytest.mark.parametrize("whole", [False, True], ids=["the default cell", "a whole-pixel cell"])
def test_measuring_the_columns_leaves_no_baked_size_on_the_atlas(
    request: pytest.FixtureRequest, whole: bool
) -> None:
    """
    The measurement draws a row to buffers, which rasterises it at the size it
    is set at, and looks for the size the glyphs came from; and on a
    whole-pixel cell the library asks ImGui for the advance at the size asked
    for, which makes that size too (16 and 17 px at 16). Each is a size on
    ImGui's atlas that nothing draws from, and ``theme.keep_baked_sizes``
    keeps whatever is there until the epoch moves. What a font load leaves on
    the atlas is nothing, and a measurement taken later adds nothing to what
    it found.
    """
    from pptmstr.ui.typeset import themes

    seam = request.getfixturevalue("seam" if whole else "default_cell")
    before = _baked(seam)
    assert typeset.load_fonts(BOLD)
    assert _baked(seam) == before, "a load, with its measurement, bakes nothing"
    found = gate._measured_columns(themes.diff_theme())
    assert found.glyphs == 156 and found.holds is whole
    assert found.size == (17.0 if whole else 16.0)
    assert _baked(seam) == before


def test_a_size_the_measurement_found_on_the_atlas_is_left_there(seam: Any) -> None:
    """
    Only what the measurement made is discarded. A size something drew from
    before it is still there afterwards, with its glyphs.
    """
    from pptmstr.ui.typeset import themes
    from tests.test_typeset_views import frame

    assert typeset.load_fonts(BOLD)
    cache = typeset.ViewCache()
    frame(seam, lambda: typeset.markdown(cache, "k", "```\ncode at the code role's size\n```\n"))
    drawn = _baked(seam)
    assert drawn, "a fence is the library's code face"
    gate._measured_columns(themes.diff_theme())
    assert _baked(seam) == drawn
    frame(seam, lambda: typeset.markdown(cache, "k", "```\ncode at the code role's size\n```\n"))
    assert _baked(seam) == drawn


def test_a_measurement_taken_at_one_display_density_is_not_the_answer_at_another(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ImGui bakes a size again for another density, and the advance it bakes is a
    whole pixel of that raster. A window dragged to a display of another
    density keeps its size in points, so the code style is the same and the
    measurement is not: it is taken again there, and the first is still the
    answer back on the first display.
    """
    from pptmstr.ui.typeset import themes

    taken: list[object] = []
    real = gate._measured_columns
    monkeypatch.setattr(gate, "_measured_columns", lambda theme: (taken.append(1), real(theme))[1])
    assert typeset.load_fonts(BOLD)
    theme_now = themes.diff_theme()
    assert len(taken) == 1, "the load measured, at the density there is"
    gate.columns(theme_now)
    assert len(taken) == 1
    scale = seam.get_io().display_framebuffer_scale
    assert gate._density() == (float(scale.x), float(scale.y))

    monkeypatch.setattr(gate, "_density", lambda: (2.0, 2.0))
    gate.columns(theme_now)
    gate.columns(theme_now)
    assert len(taken) == 2
    monkeypatch.setattr(gate, "_density", lambda: (float(scale.x), float(scale.y)))
    gate.columns(theme_now)
    assert len(taken) == 2


def test_how_long_a_row_the_columns_answer_for_follows_how_far_out_the_cell_is(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The row measured is 186 characters and the cell is held to the baked
    advance within a bound. A cell out by ``e`` has a pen half a pixel out
    after ``0.5 / e`` columns, so the answer is for rows that long and no
    longer: 5,000 columns at the bound itself, some 45,000 at the rounding
    the library has at a pixel height of 16, and none where nothing holds.
    """
    from pptmstr.ui.typeset import themes

    exact = gate.Columns(True, "", cell=8.0, baked=8.0)
    assert exact.reach == gate._FARTHEST_COLUMN
    at_the_bound = gate.Columns(True, "", cell=8.0 - gate.COLUMN_EPSILON_PX, baked=8.0)
    assert 4_990 <= at_the_bound.reach <= 5_000
    assert gate.Columns(False, "", cell=8.0, baked=8.0).reach == 0
    found = gate.columns(themes.diff_theme())
    assert found.holds and 40_000 < found.reach < 50_000, found


def test_what_follows_a_tab_is_held_to_the_grid_like_any_other_glyph(
    seam: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A tab is the library's to expand, and the row of code measured has none. A
    second row of tabs is set and every glyph after one must be a whole number
    of columns in: on the library as it is they are (stops every four cells),
    and a library that put one a pixel off would not keep a diff's columns.
    """
    from pptmstr.ui.typeset import themes

    assert typeset.load_fonts(BOLD)
    found = gate.columns(themes.diff_theme())
    assert found.holds and found.tabs

    real = gate._glyph_quads

    def nudged(native: Any, vertices: bytes) -> list[tuple[float, float, float, float]]:
        quads = real(native, vertices)
        if len(quads) == len(gate._TAB_ROW.replace("\t", "")):
            left, right, u, v = quads[-1]
            quads[-1] = (left + 1.0, right + 1.0, u, v)
        return quads

    monkeypatch.setattr(gate, "_glyph_quads", nudged)
    assert typeset.load_fonts(BOLD), "so that nothing measured earlier is held"
    off = gate.columns(themes.diff_theme())
    assert (off.off, off.mishit, off.tabs, off.holds) == (0, 0, False, False)
    assert off.why == "draws what follows a tab off its column"
    monkeypatch.undo()
    assert typeset.load_fonts(BOLD)


def test_the_code_face_is_asked_which_characters_it_has_no_glyph_for(seam: Any) -> None:
    """
    What ``diffs.unfit`` asks of a diff's rows. The bundled code face has Latin
    with its accents, box drawing and the replacement character, and neither
    Greek nor Cyrillic nor CJK: those are set from another face, at its advance.
    """
    assert gate.code_lacks("plain ASCII, with ~ and |") == ()
    assert gate.code_lacks("café │ � →") == ()
    assert gate.code_lacks("a π b Ж c а π 中") == (
        "π",
        "Ж",
        "а",
        "中",
    )


@pytest.mark.parametrize("guarded", [False, True], ids=["no guard", "the guard each frame"])
def test_a_size_made_where_the_measurements_was_discarded_is_rasterised_as_itself(
    seam: Any, guarded: bool
) -> None:
    """
    The hazard ``theme.keep_baked_sizes`` exists for is a size at an address
    ImGui's font loader remembers for another. The measurement makes two sizes
    of the code face, the one asked for and the one its glyphs come from (16
    and 17 px), the second of which is then the last the loader made current,
    and discards them; the next size made is put where they were. That one is made, and
    not moved there, so the loader is told: its glyphs, and those of a size
    made after it and of the measured size made again, are their own -- each
    compared with the same character when its size is the only one on the
    atlas. With other sizes ahead of it in the pool, which a discard leaves
    where they are.
    """
    from pptmstr.ui.typeset import themes
    from tests import test_baked_sizes as baked

    assert typeset.load_fonts(BOLD)
    code = gate.face_font("CODE")
    chars = baked.FIRST + baked.LATER

    def before() -> None:
        typeset.pre_frame()
        if guarded:
            theme.keep_baked_sizes(gate.epoch())

    was = theme._KEPT.under
    theme._KEPT.under = None
    try:
        baked.drop_everything(seam)
        baked.frame(seam, lambda: baked.line(seam, None, baked.DECOY_PX, "decoy"), before)
        baked.frame(seam, lambda: baked.line(seam, code, 13.0, "abc"), before)
        ahead = _baked(seam)
        found = gate._measured_columns(themes.diff_theme())
        assert found.size == 17.0 and _baked(seam) == ahead

        def both() -> None:
            baked.line(seam, code, 19.0, chars)
            baked.line(seam, code, 17.0, chars)
            baked.line(seam, code, 16.0, chars)
            baked.line(seam, code, 13.0, chars)

        for _ in range(3):
            baked.frame(seam, both, before)
        got = {size: baked.dims(code, size, chars) for size in (19.0, 17.0, 16.0, 13.0)}
        for size, glyphs in got.items():
            assert glyphs == baked.fresh(seam, code, size, chars), size
        assert got[19.0] != got[17.0] != got[16.0] != got[13.0], "four sizes, told apart"
    finally:
        theme._KEPT.under = was
        baked.drop_everything(seam)
