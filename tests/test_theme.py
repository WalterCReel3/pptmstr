"""
Theme: role completeness, the proxy, and measurable legibility.

The contrast tests are the point of this file. §6.1 makes claims about a palette
being readable; colour choices get tweaked over time, and a legibility claim that
nothing checks is one that quietly stops being true.
"""

from __future__ import annotations

import pytest

from pptmstr import theme
from pptmstr.model import AgentState, ContextPressure
from pptmstr.theme import (
    HIGH_CONTRAST,
    REQUIRED_THEMES,
    STATE_GLYPH,
    STATE_LABEL,
    THEMES,
    Color,
    Palette,
    contrast_ratio,
)

ALL_PALETTES = list(THEMES.values())
PALETTE_IDS = [p.name for p in ALL_PALETTES]


@pytest.fixture(autouse=True)
def _restore_theme():
    """Theme is module-global; a test that switches it must not leak into the next."""
    before = theme.current().name
    yield
    theme.set_theme(before)


# -- completeness --------------------------------------------------------------


def test_required_themes_exist() -> None:
    """§6.1 makes these three mandatory, not a matter of taste."""
    for name in REQUIRED_THEMES:
        assert name in THEMES


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_every_palette_defines_every_role(palette: Palette) -> None:
    """
    A role only some themes define renders as a crash or an invisible widget in the
    others -- and only on the code path that uses it, which may be rare.
    """
    for role in theme.role_names():
        value = getattr(palette, role)
        assert isinstance(value, Color), f"{palette.name}.{role} is not a Color"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_every_state_and_pressure_resolves(palette: Palette) -> None:
    for state in AgentState:
        assert isinstance(palette.state(state), Color)
    for pressure in ContextPressure:
        assert isinstance(palette.pressure(pressure), Color)


def test_every_state_has_a_glyph_and_a_label() -> None:
    """
    The non-hue channels. If a state loses these it is legible by colour alone,
    which fails in high contrast and for a colour-deficient operator.
    """
    for state in AgentState:
        assert STATE_GLYPH[state]
        assert STATE_LABEL[state]


def test_state_labels_are_distinct() -> None:
    """Two states sharing a label would collapse the text channel for both."""
    labels = [STATE_LABEL[s] for s in AgentState]
    assert len(set(labels)) == len(labels)


# -- the proxy -----------------------------------------------------------------


def test_proxy_tracks_the_active_palette() -> None:
    """
    The bug this design exists to prevent: `from .theme import P` copies a reference,
    so a rebindable module global would leave importing modules on the old palette
    and the UI wearing two themes at once.
    """
    from pptmstr.theme import P

    theme.set_theme("dark")
    dark_bg = P.bg.u32
    theme.set_theme("light")
    assert P.bg.u32 != dark_bg
    assert P.bg.u32 == THEMES["light"].bg.u32


def test_unknown_theme_falls_back_rather_than_raising() -> None:
    """Reachable from a hand-edited settings file; should cost a theme, not a start."""
    assert theme.set_theme("no-such-theme").name == "dark"


def test_color_precomputes_both_forms() -> None:
    """
    Both forms are built once at construction. Converting per access would put
    allocation on a path taken hundreds of times a frame.
    """
    c = theme.col(0x4FC1E9)
    assert c.vec4.x == pytest.approx(0x4F / 255)
    assert c.u32 == 0xFFE9C14F  # IM_COL32 packs ABGR


# -- measurable legibility -----------------------------------------------------


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_body_text_meets_wcag_aa(palette: Palette) -> None:
    """4.5:1 is the AA floor for body text."""
    assert contrast_ratio(palette.text, palette.bg) >= 4.5
    assert contrast_ratio(palette.text, palette.panel) >= 4.5


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_dim_text_stays_legible(palette: Palette) -> None:
    """
    3:1, the large-text AA floor. text_dim carries topics and model names -- it is
    meant to recede, not to become unreadable.
    """
    assert contrast_ratio(palette.text_dim, palette.bg) >= 3.0


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_state_colours_are_legible_on_panel(palette: Palette) -> None:
    """
    Every state is drawn as tinted text, so every state colour is body text and has
    to clear the non-text-element floor against the surface it sits on.
    """
    for state in AgentState:
        ratio = contrast_ratio(palette.state(state), palette.panel)
        assert ratio >= 3.0, f"{palette.name}.{state.name} only {ratio:.2f}:1"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_pressure_colours_are_legible(palette: Palette) -> None:
    for pressure in ContextPressure:
        ratio = contrast_ratio(palette.pressure(pressure), palette.panel)
        assert ratio >= 3.0, f"{palette.name}.{pressure.name} only {ratio:.2f}:1"


def test_high_contrast_actually_is() -> None:
    """
    Otherwise it is just another dark theme with a different name. AAA is 7:1; this
    palette should clear it comfortably rather than scrape past AA.
    """
    assert contrast_ratio(HIGH_CONTRAST.text, HIGH_CONTRAST.bg) >= 15.0
    for state in AgentState:
        ratio = contrast_ratio(HIGH_CONTRAST.state(state), HIGH_CONTRAST.bg)
        assert ratio >= 7.0, f"high_contrast.{state.name} only {ratio:.2f}:1"


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_pressure_steps_differ_in_lightness_not_only_hue(palette: Palette) -> None:
    """
    The ring's redundant channel is fill, but the three pressure colours should also
    separate by lightness -- blue/orange/red are close in luminance by default, and
    a palette where they match relies entirely on hue for anyone reading a static
    frame.
    """
    lums = [theme.relative_luminance(palette.pressure(p)) for p in ContextPressure]
    spread = max(lums) - min(lums)
    assert spread >= 0.05, f"{palette.name} pressure colours span only {spread:.3f} luminance"


# Deliberately not tested: WCAG contrast between two *state* colours. The metric
# does not mean what it looks like it means here -- it compares a foreground to a
# background, and since every state colour must clear 3:1 against the same panel,
# they are pushed toward similar luminance and score ~1.1:1 against each other by
# construction. A palette cannot satisfy both, and the one that matters is
# legibility against the surface. Distinguishing two states from each other is what
# STATE_GLYPH and STATE_LABEL are for, and those are checked above.


# -- widget formatting ---------------------------------------------------------


def test_short_model_drops_a_trailing_date() -> None:
    from pptmstr.ui.widgets import short_model

    assert short_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5"


def test_short_model_leaves_other_ids_alone() -> None:
    """Only a trailing 8-digit run is a date stamp; anything else is information."""
    from pptmstr.ui.widgets import short_model

    assert short_model("claude-opus-5") == "claude-opus-5"
    assert short_model("claude-sonnet-5") == "claude-sonnet-5"
    assert short_model("some-model-1234") == "some-model-1234"
    assert short_model("nodashes") == "nodashes"
