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


# -- the splash's raster line --------------------------------------------------
#
# ``splash.RASTER_ROWS_PER_SECOND = 16.0`` is sound only under
#
#     rows_per_second <= band_rows * displayed_fps
#
# and at this panel's measured idle rate of 8.7fps that requires ``band_rows = 2``: one
# row allows 8.7 rows/s, so at 16 the line would advance 1.84 rows between displayed
# frames and hop down the art with a permanently unlit row inside it. That 2 counts
# ``Luminance.RASTER`` and ``Luminance.TRAIL`` as *one* moving object, which holds only
# where TRAIL is drawn far enough from DIM to be seen as part of the line rather than as
# more background. splash.py cannot check that -- it emits an ordinal and imports no
# palette by design -- so a rate constant in that file is an assertion about
# ``inbox._ink`` and about all nine palettes here, and this is where it is held.
#
# The floor is measured rather than chosen. Across every palette against both surfaces
# the shipping triple's thinnest separations are 1.227:1 (high_contrast, DIM to TRAIL,
# where text_dim is #C0C0C0 and TRAIL is white at 212/255 over black) and 1.196:1 (cde,
# TRAIL to RASTER). 1.19 is the tightest floor both clear, so a regression in the
# triple or in any palette trips this while the shipping choice passes with no room to
# spare on cde.
#
# It is cde that sets the floor, and cde provably cannot do better. Its entire ink range
# against its own bg spans 3.96:1 (text_dim) to 6.99:1 (text_strong), a total of
# 1.77:1 from end to end, so three evenly spaced levels are 1.33:1 per step at the very
# best -- for this triple or for any triple that could be invented on that palette.
# Going *brighter* than the bg to buy range is worse rather than better: the ramp would
# cross the background and the mid-wake rows would vanish into it. So a floor above
# 1.33 here would not be a stricter test, it would be an impossible one, and the honest
# reading of 1.19 is that on cde and win311 this is thin by construction.
SPLASH_BAND_SEPARATION = 1.19


def _as_drawn(packed: int, surface: Color) -> Color:
    """
    What the draw list actually puts on screen when it blends ``packed`` over ``surface``.

    ``inbox._ink`` hands back a packed ImU32 for all three levels, and one of them comes
    from ``theme.faded`` and so carries an alpha under 255. An alpha is not a colour
    until something has composited it, and the thing a contrast ratio has to be taken
    between is the composited result -- measuring the unblended ink would credit TRAIL
    with a separation it never has on screen. The opaque two fall through this
    unchanged, so all three take the same path and none is special-cased.
    """
    alpha = ((packed >> 24) & 0xFF) / 255.0
    # IM_COL32 packs ABGR; test_color_precomputes_both_forms pins that.
    ink = (packed & 0xFF, (packed >> 8) & 0xFF, (packed >> 16) & 0xFF)
    back = (round(c * 255.0) for c in surface.rgb)
    r, g, b = (round(alpha * i + (1.0 - alpha) * s) for i, s in zip(ink, back, strict=True))
    return theme.col((r << 16) | (g << 8) | b)


def _splash_levels(palette: Palette, surface: Color) -> dict[object, Color]:
    from pptmstr.ui import splash
    from pptmstr.ui.inbox import _ink

    theme.set_theme(palette.name)
    return {level: _as_drawn(_ink(level), surface) for level in splash.Luminance}


# Both surfaces, though the panel this draws in is a dockable window and its real
# backdrop is ``bg``. ``panel`` costs nothing to hold and is the only other surface a
# pane in this app is ever drawn on; as it happens both worst cases above are on ``bg``.
_SPLASH_SURFACES = ("bg", "panel")


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_the_splash_luminance_band_is_two_rows_on_every_palette(palette: Palette) -> None:
    """
    TRAIL has to be distinct from both its neighbours, or the raster line is one row.

    Failing this does not make the splash slightly less pretty on an unpopular palette.
    It makes ``splash.RASTER_ROWS_PER_SECOND`` exceed its own bound there, and the hole
    that constant exists to close comes back on exactly the palettes nobody looks at:
    TRAIL collapsing into DIM leaves a one-row band, and TRAIL collapsing into RASTER
    leaves a two-row bar with no leading edge, which is the same failure seen from the
    other side.
    """
    from pptmstr.ui import splash

    for surface_role in _SPLASH_SURFACES:
        surface = getattr(palette, surface_role)
        level = _splash_levels(palette, surface)
        where = f"{palette.name} on {surface_role}"

        dim_to_trail = contrast_ratio(level[splash.Luminance.DIM], level[splash.Luminance.TRAIL])
        assert dim_to_trail >= SPLASH_BAND_SEPARATION, (
            f"{where}: TRAIL is only {dim_to_trail:.3f}:1 from DIM, so the band is one "
            f"row and 16 rows/s hops"
        )

        trail_to_raster = contrast_ratio(
            level[splash.Luminance.TRAIL], level[splash.Luminance.RASTER]
        )
        assert trail_to_raster >= SPLASH_BAND_SEPARATION, (
            f"{where}: TRAIL is only {trail_to_raster:.3f}:1 from RASTER, so the line "
            f"has no leading edge"
        )


@pytest.mark.parametrize("palette", ALL_PALETTES, ids=PALETTE_IDS)
def test_the_splash_trail_sits_between_the_art_and_the_raster_line(palette: Palette) -> None:
    """
    Separation alone would be satisfied by a TRAIL on the wrong side of RASTER.

    Ordering is what makes the two lit rows read as one object with a direction. It is
    stated against the surface rather than as raw luminance because half these palettes
    are light, where the strongest ink is the *darkest* -- distance from the background
    is the quantity that means the same thing in both regimes.
    """
    from pptmstr.ui import splash

    for surface_role in _SPLASH_SURFACES:
        surface = getattr(palette, surface_role)
        level = _splash_levels(palette, surface)
        steps = [contrast_ratio(level[lum], surface) for lum in splash.Luminance]
        assert steps == sorted(steps), (
            f"{palette.name} on {surface_role}: DIM/TRAIL/RASTER stand at "
            f"{steps[0]:.2f}/{steps[1]:.2f}/{steps[2]:.2f}:1 from the surface, which is "
            f"not increasing"
        )


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


def test_review_state_prunes_its_diff_cache() -> None:
    """
    Split diff lines are cached per pending id; without pruning they outlive the
    approvals they belong to and the largest ones never get released.
    """
    from pptmstr.ui.review import ReviewState

    state = ReviewState()
    state.diff_lines["gone"] = ["a", "b"]
    state.diff_lines["live"] = ["c"]
    state.prune({"live"})
    assert set(state.diff_lines) == {"live"}


# -- font faces ----------------------------------------------------------------


def test_a_face_that_is_not_loaded_is_none_rather_than_an_error() -> None:
    """
    The degradation contract. push_font(None, size) means "keep the current face",
    so a renderer asking for a face nobody loaded gets body text at the right size.
    Nothing here runs a window, which is exactly the case this must survive.
    """
    assert theme.face(theme.Face.BODY) is None
    assert theme.face(theme.Face.BOLD) is None


def test_there_is_no_italic_face() -> None:
    """
    Deliberate, and not a gap to be filled: Inconsolata has no italic upstream and
    ImGui will not synthesise an oblique, so emphasis is a colour shift. Adding
    ITALIC here without vendoring a whole family gives a face that silently renders
    as regular.
    """
    assert {f.name for f in theme.Face} == {"BODY", "BOLD"}


def test_the_bold_face_ships_inside_the_package() -> None:
    """
    At the repo root it would be absent from the wheel, and the bold face would
    degrade for everyone who installed rather than cloned -- invisibly, because a
    missing face is a supported state.
    """
    vendored = theme._ASSETS / "fonts" / "Inconsolata-Bold.ttf"
    assert vendored.is_file(), f"missing vendored face: {vendored}"
    assert (theme._ASSETS / "fonts" / "OFL.txt").is_file(), "OFL requires the licence to ship"


def test_fading_scales_only_the_alpha() -> None:
    base = theme.col(0x4FC1E9)
    assert theme.faded(base, 1.0) == base.u32
    assert theme.faded(base, 0.0) == 0x00E9C14F
    assert theme.faded(base, 0.5) >> 24 == pytest.approx(127, abs=2)


def test_fading_is_quantised_and_memoised() -> None:
    """
    The caller is an animation running per cell per frame per card. Neighbouring
    alphas must collapse onto one cached entry rather than packing a new colour each
    frame.
    """
    base = theme.col(0x4FC1E9)
    theme._FADE_CACHE.clear()
    first = theme.faded(base, 0.500)
    assert theme.faded(base, 0.505) == first
    assert len(theme._FADE_CACHE) == 1


def test_fading_out_of_range_clamps() -> None:
    base = theme.col(0x4FC1E9)
    assert theme.faded(base, -1.0) == theme.faded(base, 0.0)
    assert theme.faded(base, 5.0) == theme.faded(base, 1.0)
