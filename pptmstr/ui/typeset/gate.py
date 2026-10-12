"""
Whether the text library can draw, its fonts, and the sentence when it cannot.

The library is ``advtext`` drawing through imtypeset, a native library built for
one ``imgui-bundle`` wheel and found through two environment variables.
``advtext.typesetter`` is its gate; this module is pptmstr's side of that gate
and the only place the two meet for anything but a diff model (``diffs``).

**pptmstr is still pptmstr when the library cannot draw.** Four things can be
wrong -- it is not installed, it was refused, its fonts did not load, it stopped
while drawing -- and a fifth is anything raised in here. Each leaves
``can_draw()`` false, so every pane draws with its own renderer
(``roles.enabled``), and each is one sentence the Text menu and the settings
dialog say (``unavailable_reason``). So **no function here raises**: the library
is imported inside the functions that use it, and whatever one of them raises
is caught, kept as the sentence, and costs the typeset path and nothing else.

**Fonts are loaded once the UI font is, at the end of hello_imgui's
``load_additional_fonts``**, and never from ``post_init``: the runner empties the
atlas before that callback, so a face added earlier is gone by the first frame.
The library puts its own copy of each face on the atlas, after the application's
first font, which stays ``Fonts[0]``. Two files are pptmstr's to name before it
does (``advtext.register_font``): the vendored bold monospace, without which the
library backs bold code with a proportional bold and bold code stops being
monospace; and the upright file of the proportional family in force.

**They are loaded whatever ``Settings.typeset`` says.** Measured in the
application under Xvfb and llvmpipe on 2026-10-11: 21 ms. Switching typeset
text on is then a setting and not a load.

**A change of proportional family loads them again, between frames**
(``pre_frame``): the library reads its files when its context is made, so
another file means another context. Run in a window on 2026-10-11: 11 ms, a view
kept across it draws, and **each load leaves seven more fonts on ImGui's atlas**
that nothing takes off. That is the cost of an operator's click on the family
list and of nothing else.

**The library's context belongs to one ImGui context** (``_Gate.home``).
``advtext.typesetter.ready()`` stays true after the ImGui context its fonts are
on is destroyed, and a view then draws without raising, into an atlas that is
gone. So ``can_draw()`` also asks whether the current ImGui context is the one
the fonts were loaded in, and whoever destroys that context calls ``unload()``
first: ``app`` in ``before_exit``, the tests in ``tests/conftest.py``.

Reached past ``advtext.__all__``: the module ``advtext.typesetter`` itself, for
``module``, ``trouble``, ``unload`` and ``LIBRARY_VARIABLE``, which the library
lists as reached through their module with no promise attached. A test reads
this package's imports and holds them to that one name.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import roles


@dataclass
class _Gate:
    """
    What pptmstr knows about the library that the library does not answer.

    Module state for the reason ``roles._State`` is: one library context, one
    atlas, one process.
    """

    # Whether the fonts have been asked for since the last `unload`.
    asked: bool = False
    # The ImGui context they were asked for in: where the library's context is,
    # if it made one. Kept whether or not the load succeeded, so that whoever
    # destroys that context knows there may be something to unload first.
    home: Any = None
    # Whether the library accepted its pairing with the wheel at that load, and
    # whether the load succeeded: what tells "refused" from "its fonts did not
    # load" from "stopped while drawing" afterwards.
    accepted: bool = False
    loaded: bool = False
    # The proportional family whose file the library was given at that load.
    family: str | None = None
    # The vendored bold monospace the application named, for a later load.
    bold: Path | None = None
    # Something raised in this module, as `Type: message`. While it is set the
    # library is not offered to draw with; the next load clears it.
    failure: str | None = None


_GATE = _Gate()


def _typesetter() -> Any:
    from advtext import typesetter

    return typesetter


def _imgui() -> Any:
    from imgui_bundle import imgui

    return imgui


def failed(failure: BaseException) -> None:
    """
    Keep what was raised as the reason, and stop offering the library to draw with.

    For this module, and for the one caller that stands where an exception may
    not pass: ``theme``, inside hello_imgui's font callback.
    """
    _GATE.failure = f"{type(failure).__name__}: {failure}"


def can_draw() -> bool:
    """
    Whether the library can draw *in the ImGui context that is current*.

    Its fonts are loaded, it has not been switched off since, and the current
    context is the one they were loaded in. Never raises. This is the library's
    half of ``roles.enabled``.
    """
    if not _GATE.loaded or _GATE.failure is not None:
        return False
    try:
        return bool(_typesetter().ready()) and _imgui().get_current_context() == _GATE.home
    except Exception as failure:
        failed(failure)
        return False


def available() -> bool:
    """
    Whether the operator can have typeset text at all: what the Text menu and
    the settings dialog offer their switches by.

    The library can draw, **and some pane's typeset branch is built on it**
    (``roles.ADOPTED``). Without the second half the switches would be live and
    change nothing on screen, and the dialog would draw its samples through
    calls that are not built. Never raises.
    """
    return bool(roles.ADOPTED) and can_draw()


def obtainable() -> bool:
    """
    Whether typeset text can be had by switching it on.

    The fonts are loaded whatever the setting says, so this is ``available()``:
    there is no load a switch would still have to make.
    """
    return available()


def unavailable_reason() -> str | None:
    """
    Why text cannot be typeset, as one sentence for the menu, or ``None`` when it can.

    The sentence says which of the things that can be wrong is: not installed,
    refused, fonts not loaded, stopped while drawing, or a failure in the gate
    itself. The library's own words are quoted, not paraphrased.
    """
    if can_draw():
        if roles.ADOPTED:
            return None
        return "no pane sets its text through the typesetting library in this build"
    if _GATE.failure is not None:
        return f"the typesetting library failed and is not in use ({_GATE.failure})"
    if not _GATE.asked:
        return "the typesetting library's fonts have not been loaded"
    try:
        typesetter = _typesetter()
    except Exception as failure:
        return f"the text library is not installed ({type(failure).__name__}: {failure})"
    try:
        why = str(typesetter.reason())
        if typesetter.module() is None:
            if _GATE.loaded:
                noted = str(typesetter.trouble())
                return f"the typesetting library stopped while drawing ({noted or why})"
            if _GATE.accepted:
                return f"the typesetting library's fonts did not load ({why})"
            if not os.environ.get(typesetter.LIBRARY_VARIABLE):
                return f"the typesetting library is not installed ({why})"
            return f"the typesetting library was refused ({why})"
        if not typesetter.ready():
            noted = str(typesetter.trouble())
            return f"the typesetting library's fonts did not load ({noted or 'no reason given'})"
    except Exception as failure:
        return f"the typesetting library failed and is not in use ({type(failure).__name__})"
    return "the typesetting library's fonts are in another window's context"


def load_fonts(bold: Path | None = None) -> bool:
    """
    Give the library its fonts, in the current ImGui context. Whether it can draw
    (``can_draw``).

    Call it at the end of hello_imgui's ``load_additional_fonts``, after the
    application's own first font. ``bold`` is the vendored bold monospace. Every
    call makes the library's context again, which closes every document a view
    made before it, so the epoch moves.

    Never raises: a library that is absent or refused answers false, and so does
    anything raised on the way; ``unavailable_reason()`` has the sentence.
    """
    _GATE.asked = True
    _GATE.home = None
    _GATE.family = None
    _GATE.bold = bold
    _GATE.accepted = _GATE.loaded = False
    _GATE.failure = None
    try:
        typesetter = _typesetter()
    except Exception:
        # Not a failure of the gate: `unavailable_reason` finds it missing again
        # and says so, with what the import raised.
        return False
    try:
        _GATE.accepted = typesetter.module() is not None
        if _GATE.accepted:
            family = roles.proportional()
            _register(bold, roles.prose_path(family))
            _GATE.home = _imgui().get_current_context()
            if typesetter.load_fonts():
                _GATE.family = family.name
                _GATE.loaded = True
    except Exception as failure:
        failed(failure)
    roles.moved()
    return can_draw()


def _register(bold: Path | None, prose: Path | None) -> None:
    """
    Name pptmstr's two files in the library's registry, which is the process's.

    Only where the registry does not already say so: a registration drops every
    metric the library has read from a font file.
    """
    from advtext import Face, register_font, registered_fonts, unregister_font

    held = registered_fonts()
    if bold is not None and bold.is_file() and held.get(Face.CODE_BOLD) != bold.resolve():
        register_font(Face.CODE_BOLD, bold.resolve())
    if held.get(Face.PROSE) != prose:
        if prose is None:
            unregister_font(Face.PROSE)
        else:
            register_font(Face.PROSE, prose)


def unload() -> None:
    """
    Take the library's context down. For whoever destroys the ImGui context, first.

    ``can_draw()`` is false afterwards and ``load_fonts()`` makes it true again.
    Does nothing when the fonts were never asked for. Never raises.
    """
    if not _GATE.asked:
        return
    _GATE.asked = False
    _GATE.home = None
    _GATE.family = None
    _GATE.accepted = _GATE.loaded = False
    _GATE.failure = None
    try:
        _typesetter().unload()
    except Exception as failure:
        failed(failure)


def pre_frame() -> None:
    """
    The seam's between-frames work. Call before each ``new_frame``. Never raises.

    Both cheap when there is nothing to do, which is nearly always, and neither
    is done while the library cannot draw.

    **A family the library was not given is given to it.** The operator chose
    another proportional family; the fonts are loaded again, here and not in the
    click, because a load remakes the library's context under whatever is
    drawing.

    **The size of running text follows the UI font.** The library draws at the
    size its theme names and not at ImGui's current font size, and
    ``hello_imgui`` scales the UI font for the display, so the size is read from
    what ImGui reports. A change moves the epoch.
    """
    if not can_draw():
        return
    try:
        if roles.proportional().name != _GATE.family:
            load_fonts(_GATE.bold)
        size = _live_font_size()
        if size is not None:
            roles.follow(size)
    except Exception as failure:
        failed(failure)


def _live_font_size() -> float | None:
    """
    The UI font's size as ImGui has it between frames, or ``None`` before the first.

    Between frames the font stack is empty, so this is the default font's size with
    the display scale applied and never a size some pane had pushed.
    """
    im = _imgui()
    if im.get_current_context() is None or im.get_frame_count() < 1:
        return None
    size = float(im.get_font_size())
    return size if size >= 1.0 else None


roles.bind(can_draw)
