"""
The quote line's glitch: the envelope, the sites it lands on, and what the pane draws.

Split out of tests/test_splash.py rather than added to it because the two files ask
different questions of ``quote_frame``. That file owns the alpha sweep -- the contrast
floor, the quantisation, the guarantee that the block never reflows -- and its sampling
grid is built around ``SHIMMER_PERIOD_SECONDS``. This one owns the glyph channel, and it
walks ``QUOTE_GLITCH_STEPS_PER_SECOND`` exhaustively instead of sampling.

**Why exhaustively.** ``tests/test_splash.py``'s ``QUOTE_TIMES`` steps by
``SHIMMER_PERIOD_SECONDS / 97``, 0.1134s, which is 8.82Hz against a glitch that reroms at
8Hz. Any property of the glitch checked on that grid is checked at a beat frequency of
0.8Hz: it would hold or fail depending on where in a burst the grid happened to land, and
it would change the day either constant moved. The glitch's own step is an integer and
there are exactly 52 of them to a period, so the sound thing is not a better sample rate
but no sampling at all -- ``STEPS`` below is every step of three consecutive periods, and
the properties asserted over it are total rather than probable.

The sub-step sampling that remains is deliberate and is testing the opposite thing: that
``now`` values *inside* one step are indistinguishable.
"""

from __future__ import annotations

import math
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui as _real_imgui

from pptmstr.ui import inbox, splash

_ImVec2 = _real_imgui.ImVec2

# One period is 52 steps; three of them is enough to see a burst begin, peak, clear, and
# begin again somewhere else. Offset by half a step so no sample sits on a boundary where
# a float comparison decides which step it is in.
STEPS_PER_PERIOD = round(splash.QUOTE_GLITCH_PERIOD_SECONDS * splash.QUOTE_GLITCH_STEPS_PER_SECOND)
STEPS = [
    (step + 0.5) / splash.QUOTE_GLITCH_STEPS_PER_SECOND for step in range(3 * STEPS_PER_PERIOD)
]

SITES = [
    (row, col)
    for row, line in enumerate(splash.QUOTE_LINES)
    for col, char in enumerate(line)
    if char != " "
]


def corrupted(now: float) -> list[tuple[int, int]]:
    """
    The (line, column) of every character the frame draws differently from the prose.

    This is the observable the whole file is written against, and it is exactly the
    envelope's count only because a substitute is never the character it replaced --
    ``test_a_substitute_is_never_the_character_it_replaced`` is what makes that so, and
    without it every count here would be an undercount by a random 1-in-93 per site.
    """
    frame = splash.quote_frame(now)
    return [
        (row, col)
        for row, (prose, drawn) in enumerate(zip(frame.lines, frame.glyphs, strict=True))
        for col, (a, b) in enumerate(zip(prose, drawn, strict=True))
        if a != b
    ]


# -- the envelope --------------------------------------------------------------


def test_the_glitch_never_exceeds_ten_characters_at_once() -> None:
    """
    The operator's cap, and the only number in this animation that is not this repo's.

    Total over every step of three periods rather than sampled, so "up to 10" is a bound
    and not an observation. The reached-it half matters as much as the bound: an envelope
    that peaked at nine would satisfy the assertion and quietly spend the whole effect at
    90%.
    """
    counts = [splash.quote_glitch_count(now) for now in STEPS]
    assert max(counts) == splash.QUOTE_GLITCH_MAX_CHARS, max(counts)
    assert all(0 <= count <= splash.QUOTE_GLITCH_MAX_CHARS for count in counts)


def test_the_quote_settles_to_nothing_for_seconds_at_a_time() -> None:
    """
    "Then 0 for a few seconds" -- the half of the sentence that is easy to lose.

    An animation that ebbed to one character instead of none would read as continuous
    damage rather than as bursts, and nothing else in this file would notice. Measured as
    the longest unbroken run of zero-count steps inside one period, in seconds, because
    seconds is the unit the specification is in.
    """
    counts = [splash.quote_glitch_count(now) for now in STEPS[:STEPS_PER_PERIOD]]
    longest = current = 0
    for count in counts + counts:
        current = current + 1 if count == 0 else 0
        longest = max(longest, current)
    quiet = longest / splash.QUOTE_GLITCH_STEPS_PER_SECOND
    assert quiet >= 3.0, quiet
    assert quiet < splash.QUOTE_GLITCH_PERIOD_SECONDS, quiet


def test_a_burst_swells_and_clears_rather_than_switching_on() -> None:
    """
    What makes the envelope an ebb and flow instead of a duty cycle.

    Pinned as unimodality over one period -- the count rises to a single peak and falls,
    never rising again -- plus a cap on how much it can move between adjacent steps. A
    square envelope passes the two tests above and fails this one, which is the point: it
    would be ten characters appearing at once and vanishing at once, which is a strobe.

    The step cap is 2 rather than 1 because a raised cosine over ten characters in twenty
    steps has a peak slope of about 1.6 characters a step; asserting 1 would be asserting
    a shallower burst than this one, not a smoother one.
    """
    counts = [splash.quote_glitch_count(now) for now in STEPS[:STEPS_PER_PERIOD]]
    peak = counts.index(max(counts))
    assert counts[:peak] == sorted(counts[:peak]), counts
    assert counts[peak:] == sorted(counts[peak:], reverse=True), counts
    assert max(abs(b - a) for a, b in zip(counts, counts[1:], strict=False)) <= 2, counts


def test_the_glitch_and_the_highlight_do_not_relock() -> None:
    """
    Two periodic animations over the same two lines, kept incommensurate on purpose.

    If the periods divided each other the panel would be a single loop and the pairing of
    highlight position and corruption would be the same every time round it. Stated as the
    time the pair takes to return to its starting phase, which has to be long against how
    long anybody looks at a cold-start splash.
    """
    a = Fraction(splash.SHIMMER_PERIOD_SECONDS).limit_denominator(1000)
    b = Fraction(splash.QUOTE_GLITCH_PERIOD_SECONDS).limit_denominator(1000)
    relock = a * b / math.gcd(a.numerator * b.denominator, b.numerator * a.denominator)
    relock *= a.denominator * b.denominator
    assert float(relock) >= 100.0, float(relock)


# -- what the frame does with it -----------------------------------------------


def test_the_frame_corrupts_exactly_as_many_characters_as_the_envelope_promises() -> None:
    """
    The envelope and the render are two computations and this is the only thing holding
    them together.

    ``quote_glitch_count`` is what the tests above are written against; a reader only ever
    sees ``quote_frame``. A ranking that returned nine sites for a count of ten, or a
    substitution loop that wrote the same site twice, would leave every envelope test
    above green.
    """
    for now in STEPS:
        assert len(corrupted(now)) == splash.quote_glitch_count(now), now


def test_a_substitute_is_never_the_character_it_replaced() -> None:
    """
    Ten slots is few enough that a wasted one is a tenth of the effect.

    The art panel deliberately allows a draw to land on the glyph already there, and its
    own docstring explains why it can afford that. This one cannot, and the pools are
    built per source character to make it so. Asserted directly rather than left to the
    count test above, because that test's arithmetic *depends* on this being true and so
    cannot detect it failing -- both sides would fall by the same amount.
    """
    seen = 0
    for now in STEPS:
        frame = splash.quote_frame(now)
        for prose in frame.lines:
            for char in prose:
                if char != " ":
                    seen += 1
        assert len(corrupted(now)) == splash.quote_glitch_count(now)
    assert seen == len(SITES) * len(STEPS)

    # The property stated where it actually lives, so a pool built by some other route
    # still has to have it.
    for char, pool in splash._GLITCH_POOLS.items():
        assert char not in pool, char
        assert len(pool) == len(splash.QUOTE_GLITCH_POOL) - 1, char


def test_the_glitch_never_touches_a_space_and_never_draws_one() -> None:
    """
    The block's metrics have to survive the animation, and in this pane "metrics" means
    two separate things that both key off the spaces.

    A space overwritten with ink moves a word boundary in prose someone is mid-sentence
    through. A character overwritten with a space is one fewer ``add_text`` call, which is
    the count tests/test_inbox_rail.py holds ``_quote`` to, and it reads as a dropped
    glyph rather than a corrupted one. Stated as "the spaces are in the same columns",
    which covers both directions in one assertion.
    """
    blanks = tuple(
        frozenset(col for col, char in enumerate(line) if char == " ")
        for line in splash.QUOTE_LINES
    )
    for now in STEPS:
        frame = splash.quote_frame(now)
        drawn = tuple(
            frozenset(col for col, char in enumerate(line) if char == " ") for line in frame.glyphs
        )
        assert drawn == blanks, now


def test_every_substitute_comes_from_the_printable_ascii_pool() -> None:
    """
    The pool is what stands between this animation and a column shift.

    Not a restatement of how ``quote_frame`` is written: the pools it indexes are derived
    from ``QUOTE_GLITCH_POOL`` by a comprehension, and this is the assertion that a change
    to that derivation -- a fallback, a widened range, a Unicode box character borrowed
    for texture -- has to get past.
    """
    pool = set(splash.QUOTE_GLITCH_POOL)
    assert pool == {chr(code) for code in range(0x21, 0x7F)}
    for now in STEPS:
        for row, col in corrupted(now):
            assert splash.quote_frame(now).glyphs[row][col] in pool


def test_the_text_and_the_alphas_are_untouched_by_the_glitch() -> None:
    """
    The alpha channel is a separate, older animation with a contrast guarantee on it, and
    the glitch must not have moved it.

    ``lines`` is checked here as well as next door because next door checks it against a
    grid that never sees a peak burst. The alphas are checked for independence rather than
    for a value: ``SHIMMER_PERIOD_SECONDS`` is not a multiple of
    ``QUOTE_GLITCH_PERIOD_SECONDS``, so ``now`` and ``now + SHIMMER_PERIOD_SECONDS`` sit at
    different points of the glitch and identical points of the sweep. If the corruption
    had leaked into the alphas -- a corrupted character dimmed, or lit -- these two frames
    would disagree.
    """
    for now in STEPS:
        first = splash.quote_frame(now)
        second = splash.quote_frame(now + splash.SHIMMER_PERIOD_SECONDS)
        assert first.lines == splash.QUOTE_LINES
        assert tuple(len(line) for line in first.glyphs) == tuple(
            len(line) for line in splash.QUOTE_LINES
        )
        for row_a, row_b in zip(first.alphas, second.alphas, strict=True):
            for x, y in zip(row_a, row_b, strict=True):
                assert math.isclose(x, y, abs_tol=1e-9), now
    # ...and the two frames really were at different points of the glitch, or the loop
    # above proved nothing about independence.
    assert any(
        splash.quote_glitch_count(now)
        != splash.quote_glitch_count(now + splash.SHIMMER_PERIOD_SECONDS)
        for now in STEPS
    )


def test_the_glyphs_hold_still_inside_one_step() -> None:
    """
    A frame is one decision.

    The pane redraws faster than the glitch steps, so several displayed frames share a
    step and must be identical in the glyph channel. Without the quantisation the count
    and the ranking would be read off slightly different clocks and a character could
    corrupt and heal inside a single displayed frame.
    """
    dwell = 1.0 / splash.QUOTE_GLITCH_STEPS_PER_SECOND
    for step in range(STEPS_PER_PERIOD):
        base = step * dwell
        within = [
            splash.quote_frame(base + fraction * dwell).glyphs
            for fraction in (0.0, 0.25, 0.5, 0.99)
        ]
        assert len(set(within)) == 1, step
    # The converse, so this is not passing because the glyphs never change at all.
    peak = max(range(STEPS_PER_PERIOD), key=lambda s: splash.quote_glitch_count(s * dwell))
    assert splash.quote_frame(peak * dwell).glyphs != splash.quote_frame((peak + 1) * dwell).glyphs


# -- the fork ------------------------------------------------------------------


def test_a_burst_grows_out_of_the_sites_it_started_with() -> None:
    """
    THIS TEST IS THE FORK. ``splash._glitching`` documents the choice; this is what pins
    the half of it that is visible.

    The ranking is keyed on the period number, so within one burst the corrupted set only
    ever grows as the count climbs and only ever shrinks as it falls -- the same
    characters join and leave, which is what makes the envelope's shape something an eye
    can follow. Re-keying on the step is the other reading and this assertion is the one
    it breaks; if that swap is made deliberately, delete this test rather than loosening
    it, because a weakened version would assert nothing.
    """
    dwell = 1.0 / splash.QUOTE_GLITCH_STEPS_PER_SECOND
    sets = [frozenset(corrupted(step * dwell)) for step in range(STEPS_PER_PERIOD)]
    for earlier, later in zip(sets, sets[1:], strict=False):
        smaller, larger = sorted((earlier, later), key=len)
        assert smaller <= larger, (sorted(earlier), sorted(later))


def test_the_set_the_eye_could_learn_is_redrawn_every_burst() -> None:
    """
    The cost of keying the ranking on anything slower than the step, paid off.

    A ranking that never changed would start every burst with the same character, and the
    eye would learn it inside three cycles -- which is the failure the per-step reading
    exists to avoid, and the reason the key is the period rather than a constant. Stated
    two ways: consecutive bursts do not open on the same site, and over ten bursts the
    corruption reaches most of the line rather than circling a fixed tenth of it.
    """
    period = splash.QUOTE_GLITCH_PERIOD_SECONDS
    dwell = 1.0 / splash.QUOTE_GLITCH_STEPS_PER_SECOND
    firsts: list[tuple[int, int]] = []
    touched: set[tuple[int, int]] = set()
    for burst in range(10):
        for step in range(STEPS_PER_PERIOD):
            now = burst * period + step * dwell
            hit = corrupted(now)
            touched.update(hit)
            if hit and len(firsts) == burst:
                firsts.append(hit[0])
    assert len(firsts) == 10
    assert len(set(firsts)) >= 8, firsts
    assert len(touched) >= 0.6 * len(SITES), (len(touched), len(SITES))


def test_the_glyph_a_site_shows_rerolls_faster_than_the_site_set_does() -> None:
    """
    "Character cycling", as distinct from "characters replaced".

    The site ranking holds for a burst; the glyph does not, and a site that held one
    substitute for its whole two seconds on screen would read as a typo rather than as
    churn. Measured on the site that is corrupted longest in a burst, over the steps it is
    corrupted for.
    """
    dwell = 1.0 / splash.QUOTE_GLITCH_STEPS_PER_SECOND
    lifetimes: dict[tuple[int, int], list[str]] = {}
    for step in range(STEPS_PER_PERIOD):
        now = step * dwell
        frame = splash.quote_frame(now)
        for row, col in corrupted(now):
            lifetimes.setdefault((row, col), []).append(frame.glyphs[row][col])
    longest = max(lifetimes.values(), key=len)
    assert len(longest) >= 10, len(longest)
    assert len(set(longest)) >= 0.7 * len(longest), longest


# -- the pane ------------------------------------------------------------------


def _quote_calls(monkeypatch: pytest.MonkeyPatch, now: float) -> tuple[list[str], MagicMock]:
    """
    ``inbox._quote`` against a fake ImGui, returning the characters it handed the draw
    list in order, and the fake.
    """
    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_cursor_screen_pos.return_value = _ImVec2(0.0, 0.0)
    fake.calc_text_size.return_value = _ImVec2(7.0, 14.0)
    fake.get_text_line_height_with_spacing.return_value = 18.0
    draw = MagicMock()
    fake.get_window_draw_list.return_value = draw
    monkeypatch.setattr(inbox, "imgui", fake)

    inbox._quote(now)
    return [call.args[2] for call in draw.add_text.call_args_list], fake


def _peak_time() -> float:
    """The instant in the first period at which the most characters are corrupted."""
    dwell = 1.0 / splash.QUOTE_GLITCH_STEPS_PER_SECOND
    return dwell * max(range(STEPS_PER_PERIOD), key=lambda s: splash.quote_glitch_count(s * dwell))


def test_the_pane_draws_the_corrupted_glyphs_and_not_the_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The mistake nothing else can catch.

    ``QuoteFrame`` carries the prose and the drawn glyphs side by side, and a renderer
    that reached for the wrong one would put a correct, still, entirely plausible quote on
    screen. Every other test in this file would stay green, and the defect would only be
    visible to somebody who knew the animation was supposed to exist.
    """
    now = _peak_time()
    drawn, _ = _quote_calls(monkeypatch, now)
    expected = [char for line in splash.quote_frame(now).glyphs for char in line if char != " "]
    prose = [char for line in splash.QUOTE_LINES for char in line if char != " "]

    assert drawn == expected
    assert drawn != prose, "the peak of a burst must not render as the untouched quote"


def test_the_pane_claims_the_same_block_whatever_the_glitch_is_doing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The extent is measured from the prose, so it cannot move when a character corrupts.

    Also the draw-call count, which is the property tests/test_inbox_rail.py states
    against a quiet instant: checked here at the peak of a burst, where a substitution
    that produced a space would show up as one call short.
    """
    quiet, fake_quiet = _quote_calls(monkeypatch, 0.0)
    loud, fake_loud = _quote_calls(monkeypatch, _peak_time())

    assert len(quiet) == len(loud) == len(SITES)
    claimed_quiet = fake_quiet.dummy.call_args.args[0]
    claimed_loud = fake_loud.dummy.call_args.args[0]
    assert (claimed_quiet.x, claimed_quiet.y) == (claimed_loud.x, claimed_loud.y)


# -- the pin against the font ------------------------------------------------------


def test_no_substitute_can_overhang_the_character_to_its_right() -> None:
    """
    The pool's one hard guarantee, checked against the face rather than asserted.

    ``_quote`` places every character at ``column * calc_text_size("M").x``, so a
    substitute wider than one advance does not reflow the line -- it overlaps its
    neighbour, which is worse, because it looks like a rendering fault in a way that is
    not the intended one. Inconsolata is monospace and this is expected to be a formality;
    it is here because "expected to be a formality" is exactly the assumption that a
    widened pool would quietly break.

    fontTools is in the ``dev`` extra, so this skips where it is absent rather than
    failing -- the same arrangement tests/test_splash_art.py's font pins use.
    """
    pytest.importorskip("fontTools", reason="the dev extra is not installed")
    import imgui_bundle
    from fontTools.ttLib import TTFont

    face = Path(imgui_bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"
    assert face.is_file(), f"the face theme.py loads is missing: {face}"

    font = TTFont(face)
    cmap = font.getBestCmap()
    hmtx = font["hmtx"]
    advances: set[int] = set()
    for glyph in splash.QUOTE_GLITCH_POOL:
        assert ord(glyph) in cmap, glyph
        advances.add(hmtx[cmap[ord(glyph)]][0])
    # One advance across the pool, and the same one the space it may sit beside has, so a
    # corrupted character occupies exactly the column the prose put there.
    assert len(advances) == 1, advances
    assert advances == {hmtx[cmap[ord(" ")]][0]}
