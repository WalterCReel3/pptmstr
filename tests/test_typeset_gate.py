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

- ``typesetter``: the module itself. ``module``, ``trouble``, ``unload`` and
  ``LIBRARY_VARIABLE`` are reached through it and nowhere else; the library says
  so of the first three in its own docstring, and its examples import it the
  same way.
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
    under test is what pptmstr does when the step after it goes wrong.
    """
    monkeypatch.setattr(typesetter, "module", lambda: object())
    monkeypatch.setattr(gate, "_register", lambda bold, prose: None)


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
    Three faults an operator acts on differently, and the library answers all
    three as "not in use" with a reason. Which it is, is what pptmstr saw happen.
    """
    monkeypatch.setenv(typesetter.LIBRARY_VARIABLE, "/somewhere/libimtypeset_bundle.so")
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
    The library's fonts are on one context's atlas, and its own ``ready()`` does
    not know which. A second context made beside the first must not find the seam
    switched on: ``enabled()`` true there is a pane drawing into an atlas that is
    not the current one, or is gone.
    """
    home = seam.get_current_context()
    assert typeset.can_draw() and typeset.enabled()
    other = seam.create_context()
    try:
        seam.set_current_context(other)
        assert typesetter.ready(), "the library does not tell the two apart"
        assert not typeset.can_draw() and not typeset.enabled()
        assert "another window's context" in (typeset.unavailable_reason() or "")
    finally:
        seam.set_current_context(home)
        seam.destroy_context(other)
    assert typeset.can_draw() and typeset.enabled()


def test_destroying_the_context_the_faces_are_in_forgets_them(seam: Any) -> None:
    """
    ``available()`` knows the fonts' context by its address, and the next context
    made can be given the address of one just destroyed: the seam would then be
    available over an atlas that is freed. So whoever destroys that context, the
    library is unloaded first (``tests/conftest.py``).
    """
    home = seam.get_current_context()
    other = seam.create_context()
    try:
        seam.set_current_context(other)
        # As if the fonts had been loaded here: the seam's only record of where.
        gate._GATE.home = other
        assert typeset.can_draw()
    finally:
        seam.destroy_context(other)
        seam.set_current_context(home)
    assert gate._GATE.home is None and not gate._GATE.asked
    assert not typesetter.ready(), "the library's context went with it"
    again = seam.create_context()
    try:
        seam.set_current_context(again)
        assert not typeset.can_draw()
    finally:
        seam.destroy_context(again)
        seam.set_current_context(home)
    assert typeset.load_fonts(BOLD)
