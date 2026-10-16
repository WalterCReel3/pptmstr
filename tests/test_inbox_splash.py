"""
The cold-start splash is switched off (planning/2026-10-15-ui-rework.md): NEEDS YOU
with no fleet draws one quiet line, and the splash's drawing is kept, uncalled, in
``splash_view`` for an About dialog.

Two halves. Which of ``inbox.draw``'s three renderings a snapshot reaches, and what
the no-fleet one draws -- STYLE.md section 2, test the wiring. Then the splash's own
pins, held against ``splash_view`` where its drawing now lives, so none of them was
lost with the move.
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui as _real_imgui

from pptmstr.model import AgentState, Snapshot
from pptmstr.ui import decision_view, inbox, splash, splash_view
from pptmstr.ui.focus import FocusState
from pptmstr.ui.review import ReviewState
from tests import test_typeset_views as rig
from tests.test_inbox_rail import OTHER, ROOT, _reduced, approval, record, snapshot

_ImVec2 = _real_imgui.ImVec2


# -- which rendering a snapshot reaches --------------------------------------------------


def _branch(monkeypatch: pytest.MonkeyPatch, snap: Snapshot) -> list[str]:
    """
    Which of ``draw``'s three exits ran, with the pixels stubbed out.

    Every branch is stubbed, the no-fleet line included, so the answer is the branch
    taken and not a side effect of one of them happening to draw something
    recognisable.
    """
    taken: list[str] = []
    monkeypatch.setattr(inbox, "imgui", MagicMock())
    monkeypatch.setattr(inbox, "_no_fleet", lambda: taken.append("no fleet"))
    monkeypatch.setattr(inbox, "_header", MagicMock())
    monkeypatch.setattr(inbox, "_zero_state", lambda snap, now: taken.append("zero"))
    monkeypatch.setattr(inbox, "_row", lambda *a, **k: taken.append("row"))
    monkeypatch.setattr(inbox, "_batch_controls", MagicMock())

    focus = MagicMock()
    focus.obligation.return_value = None
    focus.departed = None
    inbox.draw(
        snap,
        focus,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        100.0,
        decisions=decision_view.DecisionState(),
    )
    return taken


def test_no_fleet_gets_the_no_fleet_line_and_only_that(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _branch(monkeypatch, Snapshot.empty()) == ["no fleet"]


def test_a_fleet_that_owes_nothing_keeps_its_own_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The guard the ordering holds: the no-fleet line must not appear over a running
    fleet the moment its queue drains. ``_zero_state`` is a different emptiness.
    """
    assert _branch(monkeypatch, snapshot(record(ROOT))) == ["zero"]


def test_a_fleet_with_obligations_gets_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = snapshot(record(ROOT), needs_you=(approval(ROOT),))
    assert _branch(monkeypatch, snap) == ["row"]


def test_the_two_empty_states_cannot_both_render(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Structural, not incidental. An empty ``order`` implies an empty ``needs_you``, so
    both conditions are true with no fleet and only the ordering of the returns
    decides that one rendering happens rather than two.
    """
    taken = _branch(monkeypatch, Snapshot.empty())
    assert taken == ["no fleet"]
    assert "zero" not in taken


def test_a_fleet_of_crashed_sessions_is_not_an_empty_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The case the trigger is keyed on ``nodes`` for. Every session terminal is not the
    same state as no session ever having existed: these are exactly the sessions an
    operator still has to see and dismiss.
    """
    crashed = snapshot(
        record(ROOT, state=AgentState.FAILED),
        record(OTHER, state=AgentState.FAILED),
    )
    assert _branch(monkeypatch, crashed) == ["zero"]


def test_the_trigger_asks_the_node_table_not_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ``store._preorder`` re-attaches orphans at the root so the walk can never be
    shorter than the node table, which makes ``order`` and ``nodes`` equivalent
    today. This pins the choice against that recovery regressing: a node table with
    something in it is a fleet, whatever the walk managed to reach.
    """
    populated = snapshot(record(ROOT))
    lost_the_walk = dataclasses.replace(populated, order=())

    assert lost_the_walk.nodes
    assert _branch(monkeypatch, lost_the_walk) == ["zero"]


@pytest.mark.parametrize(
    "fleet",
    [{"closed": True}, {"removed": True}, {"closed": True, "removed": True}],
    ids=["closed-only", "removed-only", "both"],
)
def test_a_window_where_nothing_on_the_fleet_ran_gets_the_no_fleet_line(
    monkeypatch: pytest.MonkeyPatch, fleet: dict[str, bool]
) -> None:
    """
    Closed cards are in the node table from the first frame of every start after
    the first, so "no nodes" would retire the no-fleet state for anyone with a
    history and hand them "nothing is running either" over a list of nobody.
    """
    snap = _reduced(**fleet)
    assert snap.nodes

    assert _branch(monkeypatch, snap) == ["no fleet"]


def test_one_session_that_ran_here_keeps_the_empty_queue_over_closed_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _branch(monkeypatch, _reduced(ran=True, closed=True, removed=True)) == ["zero"]


def test_something_owed_is_drawn_whoever_owes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The no-fleet line is asked of the queue as well as of the fleet. The store does
    not let a recalled or removed session owe anything; if a snapshot ever said one
    did, the row is what the operator has to see.
    """
    from tests.test_ui_text import CLOSED_ROOT

    snap = dataclasses.replace(_reduced(closed=True), needs_you=(approval(CLOSED_ROOT),))

    assert _branch(monkeypatch, snap) == ["row"]


# -- what the no-fleet state draws, in a real frame ---------------------------------------


def test_no_fleet_draws_one_quiet_line_and_no_splash_and_no_way_to_start(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    One dim line saying nothing is waiting. Not the splash, and no New Task link or
    start-a-session hint: starting work is offered in the fleet pane alone.
    """
    drawn: list[str] = []
    for name in ("text", "text_colored", "text_disabled", "text_unformatted", "text_link"):
        real = getattr(imgui, name)
        monkeypatch.setattr(
            imgui,
            name,
            lambda *a, _real=real, _name=name: (drawn.append(f"{_name}:{a[-1]}"), _real(*a))[1],
        )
    monkeypatch.setattr(splash_view, "draw", lambda now: drawn.append("splash"))
    art: list[float] = []
    real_frame = splash.ArtFrames.frame
    monkeypatch.setattr(
        splash.ArtFrames, "frame", lambda self, *a: (art.append(a[-1]), real_frame(self, *a))[1]
    )

    def body() -> None:
        inbox.draw(
            Snapshot.empty(),
            FocusState(),
            ReviewState(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            100.0,
            decisions=decision_view.DecisionState(),
        )

    rig.frame(imgui, body)

    assert drawn == [f"text_disabled:{inbox.NO_FLEET}"]
    assert art == [], "and the art's memo is not asked: the splash costs NEEDS YOU nothing"
    assert not any(word in inbox.NO_FLEET.lower() for word in ("new task", "ctrl", "start"))


def test_needs_you_does_not_reach_the_splash_at_all() -> None:
    """Nothing in the pane can call it: the module is not imported there."""
    assert not hasattr(inbox, "splash_view")
    assert not hasattr(inbox, "_splash")
    assert not hasattr(inbox, "_art")


# -- the splash, as splash_view draws it ---------------------------------------------------


def test_the_splash_draws_whole_in_a_real_frame(
    imgui: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``draw`` renders where it is called, in a real context: the intro, the quote
    (through the draw list), the hint, and the art when it fits.
    """
    said: list[str] = []
    real = imgui.text_colored
    monkeypatch.setattr(
        imgui, "text_colored", lambda colour, text: (said.append(text), real(colour, text))[1]
    )
    hint: list[str] = []
    real_dim = imgui.text_disabled
    monkeypatch.setattr(imgui, "text_disabled", lambda text: (hint.append(text), real_dim(text))[1])
    asked: list[float] = []
    real_frame = splash.ArtFrames.frame
    monkeypatch.setattr(
        splash.ArtFrames, "frame", lambda self, *a: (asked.append(a[-1]), real_frame(self, *a))[1]
    )
    rig.frame(imgui, lambda: splash_view.draw(3.0))

    assert said == [splash_view._INTRO, splash_view._HINT_CHORD]
    assert hint == [splash_view._HINT_TEXT]
    assert asked == [3.0], "the art fitted, and was drawn from its memo"


def test_the_splash_asks_fit_size_rather_than_choosing_a_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The view must not hardcode a size. It was specified against a floor that has since
    moved from 10 to 6, and a constant copied out of splash.py at the time would still
    render -- just never at the sizes the new floor was introduced to reach.
    """
    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_content_region_avail.return_value = _ImVec2(900.0, 700.0)
    fake.get_cursor_pos_x.return_value = 0.0
    monkeypatch.setattr(splash_view, "imgui", fake)

    seen: list[tuple[float, float, int, int]] = []

    def spy(avail_w: float, avail_h: float, rows: int, cols: int, **kw: Any) -> float:
        seen.append((avail_w, avail_h, rows, cols))
        return 8.0

    monkeypatch.setattr(splash_view.splash, "fit_size", spy)
    splash_view._art(3.0)

    assert seen == [(900.0, 700.0, splash_view._ART_ROWS, splash_view._ART_COLS)]
    # And the size it answered is the size that was pushed, not one of its own.
    assert fake.push_font.call_args.args[1] == 8.0


def test_the_art_is_skipped_when_it_does_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ``fit_size`` clamps up to its floor instead of reporting failure, so a region
    smaller than the floor gets an answer that does not fit. A clipped half-silhouette
    reads as a rendering fault; nothing reads as deliberate.
    """
    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_content_region_avail.return_value = _ImVec2(40.0, 30.0)
    fake.get_cursor_pos_x.return_value = 0.0
    monkeypatch.setattr(splash_view, "imgui", fake)

    splash_view._art(3.0)

    fake.push_font.assert_not_called()
    fake.get_window_draw_list.assert_not_called()


def _art_calls(
    monkeypatch: pytest.MonkeyPatch, avail: tuple[float, float] = (2000.0, 2000.0)
) -> tuple[list[Any], MagicMock]:
    """
    Draw the art against a fake ImGui and hand back the draw-list calls and the fake.

    The art needs more of the API stubbed than a bare MagicMock gives -- it reads the
    screen cursor and the window draw list.
    """
    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_content_region_avail.return_value = _ImVec2(*avail)
    fake.get_cursor_pos_x.return_value = 0.0
    fake.get_cursor_screen_pos.return_value = _ImVec2(100.0, 50.0)
    draw = MagicMock()
    fake.get_window_draw_list.return_value = draw
    monkeypatch.setattr(splash_view, "imgui", fake)

    splash_view._art(3.0)
    return draw.add_text.call_args_list, fake


def test_the_art_rows_are_pitched_by_line_pitch_em_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The art must occupy exactly what ``required_extent`` reserved, to the row.

    The rows are placed by hand, a colour each, so the guarantee comes from this
    view's own arithmetic rather than from ImGui's block layout. Stated against the
    resulting spacing, so it catches the mistake sitting right there to be made:
    reaching for ``get_text_line_height_with_spacing()``, which ``_quote`` correctly
    uses and which would stand the art 60 ``style.item_spacing.y`` taller than the
    region was promised.
    """
    calls, _ = _art_calls(monkeypatch)

    assert len(calls) == splash_view._ART_ROWS, "one add_text per art row"

    size = splash.fit_size(2000.0, 2000.0, splash_view._ART_ROWS, splash_view._ART_COLS)
    tops = [call.args[0].y for call in calls]
    pitches = {round(b - a, 4) for a, b in zip(tops, tops[1:], strict=False)}
    assert pitches == {round(size * splash.LINE_PITCH_EM, 4)}, (
        f"rows are {pitches} apart; anything but a single pitch of "
        f"size * LINE_PITCH_EM means a spacing crept in"
    )
    assert tops[-1] - tops[0] == pytest.approx(
        (splash_view._ART_ROWS - 1) * splash.LINE_PITCH_EM * size
    )


def test_the_art_claims_exactly_the_extent_it_was_measured_against(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The draw list writes pixels without moving the cursor, so the space has to be
    claimed or the next widget lands on top of the art -- at ``required_extent``
    exactly, the number ``_art`` just checked against the region, or a pane could
    scroll to a place where the art is cut off.
    """
    _, fake = _art_calls(monkeypatch)

    size = splash.fit_size(2000.0, 2000.0, splash_view._ART_ROWS, splash_view._ART_COLS)
    width, height = splash.required_extent(size, splash_view._ART_ROWS, splash_view._ART_COLS)
    claimed = fake.dummy.call_args.args[0]
    # approx rather than ==: ImVec2 stores float32, and required_extent computes in
    # float64, so the last couple of bits differ for reasons that are not this test's.
    assert claimed.x == pytest.approx(width)
    assert claimed.y == pytest.approx(height)


def test_the_art_draws_each_row_in_its_own_luminance(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    One pushed style colour cannot express a bright line over dim art, so the frame's
    three levels reach the draw list as three distinct colours, row for row. Checking
    that some colour was passed would pass just as well if every row were handed
    ``P.text_dim``.
    """
    calls, _ = _art_calls(monkeypatch)

    frame = splash_view._ART_FRAMES.frame(
        splash_view.splash_art.ART,
        splash_view.splash_art.RANK_OF,
        splash_view.splash_art.RANKS,
        3.0,
    )
    drawn = [call.args[1] for call in calls]
    assert drawn == [splash_view._ink(level) for level in frame.luminance]
    assert len(set(drawn)) == len(set(frame.luminance)) > 1


def test_the_hint_names_the_chord_rather_than_only_colouring_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    STYLE.md section 1: hue is never the only channel. On ``high_contrast`` the accent
    and the body text collapse toward each other, and a hint discoverable only by
    being a different colour is no hint at all there.
    """
    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_content_region_avail.return_value = _ImVec2(10.0, 10.0)
    fake.get_cursor_screen_pos.return_value = _ImVec2(0.0, 0.0)
    fake.calc_text_size.return_value = _ImVec2(7.0, 14.0)
    fake.get_text_line_height_with_spacing.return_value = 18.0
    fake.get_cursor_pos_x.return_value = 0.0
    monkeypatch.setattr(splash_view, "imgui", fake)

    splash_view.draw(3.0)

    coloured = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]
    disabled = [str(c.args[0]) for c in fake.text_disabled.call_args_list if c.args]
    assert "Ctrl+N" in coloured
    assert any("start a session" in line for line in disabled)


def test_the_quote_never_drops_a_character_below_the_readable_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Every character the frame carries is drawn. A renderer that skipped the dim end
    of the sweep would leave holes in the prose at exactly the moment the highlight
    is elsewhere.
    """
    fake = MagicMock()
    fake.ImVec2 = _ImVec2
    fake.get_cursor_screen_pos.return_value = _ImVec2(0.0, 0.0)
    fake.calc_text_size.return_value = _ImVec2(7.0, 14.0)
    fake.get_text_line_height_with_spacing.return_value = 18.0
    draw_list = MagicMock()
    fake.get_window_draw_list.return_value = draw_list
    monkeypatch.setattr(splash_view, "imgui", fake)

    splash_view._quote(3.0)

    expected = sum(len(line.replace(" ", "")) for line in splash.QUOTE_LINES)
    assert draw_list.add_text.call_count == expected
