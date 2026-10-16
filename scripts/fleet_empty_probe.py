#!/usr/bin/env python3
"""
Draw a mostly-empty FLEET pane in variants that invite the operator to start
more work, for the operator to pick (planning/2026-10-16-fleet-empty-variants.md).

Nothing here changes what the app draws. The sheet is drawn over the running
``--fake`` app under the current ``theme.py``, and every pane on it is the real
rail -- ``rail._project_header`` and ``rail._group`` over a ``Snapshot`` the real
reducer built -- with an invitation drawn after it or between its groups.
``pptmstr/`` is not edited.

    scripts/fleet_empty_probe.py --all                 # dark and light, each on its own Xvfb
    scripts/fleet_empty_probe.py --render --theme dark # one theme, on the display there is
    scripts/fleet_empty_probe.py --describe            # every cell's strings, as text

Writes ``shots/fleet-empty/<theme>.png``.

**What an invitation is, apart from its drawing.** ``invite`` answers an
``Invitation`` from the snapshot, the launcher's cwd field and the resume rows:
the exact strings of each control, the launcher fields its click sets, and what
the click does in the launcher's own terms. The sheet draws that record and
prints it under the pane, so the label and the drawing cannot say different
things. Every string in it has been through ``drawable.text``, once, in ``invite``.

**The links are ``link_probe.link``** at the accent with the underline always
on: the set link-variants picked, and ``imgui.text_link``'s own vertices
(tests/test_link_probe.py). Clicks on the sheet do nothing.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import functools
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from imgui_bundle import imgui  # noqa: E402

from pptmstr import drawable, tree  # noqa: E402
from pptmstr.intents import AgentFinished, AgentSpawned, SessionRecalled, StateChanged  # noqa: E402
from pptmstr.model import AgentState, EndedHow, NodeId, Recalled, Snapshot  # noqa: E402
from pptmstr.sessions import SessionRow  # noqa: E402
from pptmstr.store import Store  # noqa: E402

OUT = ROOT / "shots" / "fleet-empty"
SCENARIO = "fleet-empty"
THEMES = ("dark", "light")

# The FLEET pane: TRIAGE docks it at 0.21 of the window, which at 1600 px is
# 336. The height is cut to what a short fleet leaves mostly empty.
PANE_W = 336.0
PANE_H = 440.0
COLUMN = PANE_W + 24.0
LEFT = 12.0
SHEET_SIZE = (int(LEFT * 2 + COLUMN * 4), 2880)


# -- fixtures --------------------------------------------------------------------------

# Fixture directories. They need not exist: ``tree.session_base`` places a path
# nothing is at as the path itself, which is what files the cards under PPTMSTR
# and SCRATCH.
PPTMSTR = "/src/pptmstr"
SCRATCH = "/src/scratch"
# Wall seconds, as a session file and a resume row hold them.
WALL = 1_800_000_000.0
MONOTONIC = 5000.0


class State(enum.Enum):
    NONE = "no sessions"
    ONE = "one session"
    TWO = "two sessions, one project"
    SHORT = "a short fleet"


def _spawn(sid: str, task: str, cwd: str, started: float) -> AgentSpawned:
    return AgentSpawned(
        node_id=(sid, None),
        parent=None,
        task=task,
        model="claude-sonnet-5",
        started_at=started,
        cwd=cwd,
        session_base=cwd,
    )


def snapshot(state: State) -> Snapshot:
    """What the fleet holds in ``state``, built by the real reducer."""
    store = Store()
    if state is State.NONE:
        return store.snapshot()
    store.apply(_spawn("s-lead", "wire the inter-team record", PPTMSTR, MONOTONIC - 1500))
    store.apply(StateChanged(("s-lead", None), AgentState.THINKING, "reading boardsync.py"))
    if state is State.ONE:
        return store.snapshot()
    store.apply(_spawn("s-fix", "fix the link hover on light", PPTMSTR, MONOTONIC - 900))
    store.apply(AgentFinished(("s-fix", None), AgentState.DONE, ended_at=MONOTONIC - 120))
    if state is State.TWO:
        return store.snapshot()
    store.apply(
        SessionRecalled(
            session_id="s-old",
            task="try imgui.text_link in a dialog",
            model="claude-sonnet-5",
            recalled=Recalled(ended_how=EndedHow.CLOSED, last_active=WALL - 26 * 3600),
            cwd=SCRATCH,
            session_base=SCRATCH,
        )
    )
    return store.snapshot()


def _row(sid: str, summary: str, cwd: str, hours: float, *, bookmarked: bool = False) -> SessionRow:
    ms = int((WALL - hours * 3600) * 1000)
    return SessionRow(
        session_id=sid,
        cwd=cwd,
        git_branch="main",
        summary=summary,
        first_prompt=None,
        created_at=ms,
        last_modified=ms,
        tag=None,
        bookmarked=bookmarked,
    )


# What ``launcher.scan_sessions`` would list, bookmarked first then newest, as it
# sorts them. ``s-old`` is the closed card of the short fleet.
ROWS = (
    _row("s-port", "port the TLE parser to the new schema", PPTMSTR, 50, bookmarked=True),
    _row("s-old", "try imgui.text_link in a dialog", SCRATCH, 26),
    _row("s-menu", "native menus: what macOS takes", PPTMSTR, 5),
    _row("s-gate", "measure the gate's orienting burst", PPTMSTR, 30),
)


def field_cwd(state: State) -> str:
    """
    The launcher's cwd field. ``LauncherState.cwd`` starts at ``"."``, and a
    launch leaves it where it was (``left_by_launch``), so with sessions on the
    fleet it is the directory the last one was launched in.
    """
    return "." if state is State.NONE else PPTMSTR


# -- the invitation, as a value --------------------------------------------------------


class Variant(enum.Enum):
    LINE = "a"
    PANEL = "b"
    RECENT = "c"
    PROJECT = "d"


TITLE = {
    Variant.LINE: "(a) a quiet line at the foot of the cards",
    Variant.PANEL: "(b) the empty space as a drop-target panel",
    Variant.RECENT: "(c) recent and bookmarked sessions offered for resume",
    Variant.PROJECT: "(d) + new in <project>, under each project's group",
}


class Kind(enum.Enum):
    LINK = "link"
    TEXT = "text"
    DIM = "dim"


@dataclass(frozen=True, slots=True)
class Part:
    kind: Kind
    text: str


@dataclass(frozen=True, slots=True)
class Offer:
    """
    One line of an invitation: its parts left to right, the launcher fields its
    click sets (field name and value), and what the click does.

    ``project`` is the group the line is drawn under, for (d); None draws it at
    the foot of the cards. ``enabled`` False is a line drawn with its link
    disabled -- a resume whose directory is gone.
    """

    parts: tuple[Part, ...]
    prefills: tuple[tuple[str, str], ...]
    click: str
    project: str | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class Invitation:
    variant: Variant
    offers: tuple[Offer, ...]
    # (b): drawn inside a dashed panel that fills what the pane has left.
    panel: bool = False
    # What is said once for the whole cell rather than per line.
    note: str = ""


def _parts(*parts: tuple[Kind, str]) -> tuple[Part, ...]:
    return tuple(Part(kind, text) for kind, text in parts)


def chord() -> str:
    """New Task's chord as the Session menu spells it, read off ``launcher.CHORD``."""
    from pptmstr.ui import launcher, main_menu

    return main_menu.chord_label(launcher.CHORD)


def resolved(field: str) -> str:
    """The directory the field launches in: ``resolved_directory`` of it."""
    from pptmstr.ui import launcher

    field = field.strip() or "."
    try:
        return launcher.resolved_directory(launcher.FieldDirectory(field))
    except ValueError:
        # A NUL, which ``realpath`` refuses: the field as typed is all there is.
        return field


OPEN = "launcher.request_open(): New Task opens as it was left"


def _line(field: str) -> Invitation:
    return Invitation(
        Variant.LINE,
        (
            Offer(
                _parts((Kind.LINK, "start a session"), (Kind.DIM, f"· {chord()}")),
                (),
                f"{OPEN}; no field is set, so it launches in {resolved(field)}",
            ),
        ),
    )


def _panel(field: str) -> Invitation:
    from pptmstr.ui import launcher

    where = resolved(field)
    return Invitation(
        Variant.PANEL,
        (
            Offer(
                _parts((Kind.LINK, launcher.TITLE), (Kind.DIM, chord())),
                (),
                f"{OPEN}; no field is set",
            ),
            Offer(
                _parts((Kind.DIM, f"in {where}")),
                (),
                "none, it is a statement: the cwd field, resolved",
            ),
        ),
        panel=True,
    )


def on_fleet(snap: Snapshot) -> set[str]:
    from pptmstr.ui import projects

    return {node[0] for node in projects.roots(snap)}


Resolver = Callable[[SessionRow | None, str], Any]


def _recent(
    snap: Snapshot,
    field: str,
    rows: Sequence[SessionRow],
    *,
    where: Resolver | None = None,
    limit: int = 3,
) -> Invitation:
    """
    The resume rows not already a card, bookmarked first then newest -- the order
    ``scan_sessions`` hands the picker -- up to ``limit``, then the line (a) draws.
    A click is the picker's own pick: ``LauncherState.pick_session``.
    """
    from pptmstr.ui import launcher

    resolve = where or launcher.launch_directory
    carded = on_fleet(snap)
    left = [row for row in rows if row.session_id not in carded]
    left.sort(key=lambda row: (not row.bookmarked, -row.last_modified))
    offers = []
    for row in left[:limit]:
        directory = resolve(row, field)
        gone = isinstance(directory, launcher.GoneDirectory)
        age = launcher.age_label(row.last_modified, WALL)
        offers.append(
            Offer(
                _parts(
                    (Kind.DIM, "*" if row.bookmarked else "·"),
                    (Kind.LINK, row.label),
                    (Kind.DIM, "directory gone" if gone else age),
                ),
                (
                    ("picked", row.session_id),
                    ("directory", directory.path),
                    ("model, team, cap", "from the session's record"),
                ),
                "request_open() then pick_session(row): New Task opens with it picked;"
                + (" Launch is held, its directory is gone" if gone else " Launch resumes it"),
                enabled=not gone,
            )
        )
    hidden = len(rows) - len(left)
    note = f"{len(rows)} rows scanned in {resolved(field)}"
    if hidden:
        note += f"; {hidden} already a card, left out"
    return Invitation(
        Variant.RECENT,
        (*offers, *_line(field).offers),
        note=note,
    )


def project_dir(snap: Snapshot, roots: Sequence[NodeId]) -> str:
    """
    The directory a project's last-launched session was based in. A project is
    a directory's *name* (``projects.project_key``), so two directories of one
    name share a group, and the newest one is the one taken.
    """
    rec = snap.nodes[roots[-1]]
    return rec.session_base or tree.session_base(rec.cwd or ".")


def _per_project(snap: Snapshot, field: str) -> Invitation:
    from pptmstr.ui import projects

    offers = []
    for project, roots in projects.group_roots(snap):
        if project == projects.UNFILED:
            continue
        where = project_dir(snap, roots)
        offers.append(
            Offer(
                _parts((Kind.LINK, f"+ new in {project}")),
                (("cwd", where),),
                f"request_open() with cwd = {where}: a blank task there;"
                " model, team, brief as left",
                project=project,
            )
        )
    if not offers:
        where = resolved(field)
        name = Path(where).name or where
        offers.append(
            Offer(
                _parts((Kind.LINK, f"+ new in {name}"), (Kind.DIM, f"· {chord()}")),
                (),
                f"{OPEN}; no project yet, so the cwd field's: {where}",
            )
        )
    return Invitation(Variant.PROJECT, tuple(offers))


def invite(
    variant: Variant,
    snap: Snapshot,
    field: str,
    rows: Sequence[SessionRow] = (),
    *,
    where: Resolver | None = None,
) -> Invitation:
    """
    The invitation ``variant`` makes over ``snap``, with every string in it
    passed through ``drawable.text`` once, here: a row's summary, a project's
    name and a directory are each session text, and are all reached.
    """
    match variant:
        case Variant.LINE:
            made = _line(field)
        case Variant.PANEL:
            made = _panel(field)
        case Variant.RECENT:
            made = _recent(snap, field, rows, where=where)
        case Variant.PROJECT:
            made = _per_project(snap, field)
    return drawable.shown(made)


def labels(inv: Invitation) -> list[str]:
    """What the sheet prints under the pane: every control's exact strings."""
    out = []
    if inv.panel:
        out.append("in a dashed panel filling the pane's empty space")
    for offer in inv.offers:
        spelled = " ".join(f"{p.kind.value} '{p.text}'" for p in offer.parts)
        where = f" [under {offer.project.upper()}]" if offer.project else ""
        off = " [disabled]" if not offer.enabled else ""
        clipped = (
            " [link clipped to the pane with an ellipsis]"
            if inv.variant is Variant.RECENT and offer.prefills
            else ""
        )
        out.append(f"{spelled}{where}{off}{clipped}")
        if offer.prefills:
            out.append("  prefills " + "; ".join(f"{k} = {v}" for k, v in offer.prefills))
        else:
            out.append("  prefills nothing")
        out.append(f"  click: {offer.click}")
    if inv.note:
        out.append(inv.note)
    return out


def _present(row: SessionRow | None, field: str) -> Any:
    """The fixture rows' directories, taken as present: they are not on this disk."""
    from pptmstr.ui import launcher

    if row is None or not row.cwd:
        return launcher.launch_directory(row, field)
    return launcher.RecordedDirectory(row.cwd)


def cells() -> Iterator[tuple[Variant, State, Snapshot, Invitation]]:
    for variant in Variant:
        for state in State:
            snap = snapshot(state)
            yield variant, state, snap, invite(
                variant, snap, field_cwd(state), ROWS, where=_present
            )


def describe() -> Iterator[str]:
    for variant, state, _, inv in cells():
        yield f"{TITLE[variant]} · {state.value}"
        for line in labels(inv):
            yield f"    {line}"


# -- drawing ---------------------------------------------------------------------------


def _ink() -> Any:
    import link_probe

    return link_probe.ink_of("accent")


def draw_offer(offer: Offer, key: str) -> bool:
    """One line of parts; True on the frame its link is clicked."""
    import link_probe

    from pptmstr.theme import P
    from pptmstr.ui import widgets

    clicked = False
    gap = imgui.get_style().item_spacing.x
    for n, part in enumerate(offer.parts):
        if n:
            imgui.same_line()
        if part.kind is Kind.LINK:
            # The link is the part that gives way: what follows it -- an age, a
            # chord -- is short and is what tells two lines apart.
            after = sum(imgui.calc_text_size(p.text).x + gap for p in offer.parts[n + 1 :])
            room = imgui.get_content_region_avail().x - after
            shown = widgets.ellipsis(part.text, room)
            if not offer.enabled:
                imgui.begin_disabled()
            clicked |= link_probe.link(f"{shown}##{key}", _ink())
            if not offer.enabled:
                imgui.end_disabled()
        elif part.kind is Kind.DIM:
            imgui.text_colored(P.text_dim.vec4, part.text)
        else:
            imgui.text(part.text)
    return clicked


# The panel's dashes, and the least room it is drawn in. Below that the pane is
# not empty enough to be invited into, and the panel is not drawn at all.
DASH = 6.0
GAP = 4.0
PANEL_MIN = 72.0
PANEL_PAD = 10.0


def _dashed(lo: imgui.ImVec2, hi: imgui.ImVec2, colour: int) -> None:
    dl = imgui.get_window_draw_list()

    def run(x0: float, y0: float, x1: float, y1: float) -> None:
        length = abs(x1 - x0) + abs(y1 - y0)
        at = 0.0
        while at < length:
            end = min(at + DASH, length)
            t0, t1 = at / length, end / length
            dl.add_line(
                imgui.ImVec2(x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0),
                imgui.ImVec2(x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1),
                colour,
            )
            at = end + GAP

    run(lo.x, lo.y, hi.x, lo.y)
    run(hi.x, lo.y, hi.x, hi.y)
    run(hi.x, hi.y, lo.x, hi.y)
    run(lo.x, hi.y, lo.x, lo.y)


def draw_invitation(inv: Invitation, key: str, project: str | None = None) -> None:
    """The offers drawn at this point: the foot's, or one project's for (d)."""
    from pptmstr.theme import P
    from pptmstr.ui import widgets

    offers = [o for o in inv.offers if o.project == project]
    if not offers:
        return
    if project is not None:
        imgui.indent(4.0)
        for n, offer in enumerate(offers):
            draw_offer(offer, f"{key}-{project}-{n}")
        imgui.unindent(4.0)
        imgui.spacing()
        return
    imgui.spacing()
    if not inv.panel:
        for n, offer in enumerate(offers):
            draw_offer(offer, f"{key}-{n}")
        return
    room = imgui.get_content_region_avail()
    if room.y < PANEL_MIN:
        return
    lo = imgui.get_cursor_screen_pos()
    hi = imgui.ImVec2(lo.x + room.x, lo.y + room.y - 2.0)
    _dashed(lo, hi, P.text_dim.u32)
    imgui.set_cursor_screen_pos(imgui.ImVec2(lo.x + PANEL_PAD, lo.y + PANEL_PAD))
    imgui.begin_group()
    draw_offer(offers[0], f"{key}-0")
    # The rest are statements, not controls, in the rail's small face: clipped
    # rather than wrapped, so a deep path costs its head and not a line.
    imgui.push_font(None, 12.5)
    for offer in offers[1:]:
        text = " ".join(part.text for part in offer.parts)
        imgui.text_colored(P.text_dim.vec4, widgets.ellipsis(text, room.x - PANEL_PAD * 2))
    imgui.pop_font()
    imgui.end_group()
    imgui.set_cursor_screen_pos(imgui.ImVec2(lo.x, hi.y))
    imgui.dummy(imgui.ImVec2(1.0, 1.0))


def draw_rail(
    snap: Snapshot,
    after_group: Callable[[str], None] | None = None,
) -> None:
    """
    ``rail.draw``'s own body, with a hook after each project's group: the
    headers and cards are ``rail._project_header`` and ``rail._group``, in the
    order ``rail.draw`` calls them (pinned in tests/test_fleet_empty_probe.py).
    With no sessions it is ``rail.draw`` itself.
    """
    from pptmstr.ui import projects, rail
    from pptmstr.ui.focus import FocusState

    focus = FocusState()
    state = rail.RailState()
    fleet = projects.group_roots(snap)
    if not fleet or after_group is None:
        rail.draw(snap, focus, state, MONOTONIC, None, now_wall=WALL)
        return
    state.prune(snap)
    owed = rail._by_session(snap)
    cursor = focus.node(snap)
    state.arm_scroll(cursor)
    for project, roots in fleet:
        rail._project_header(snap, project, roots, owed)
        for root in roots:
            rail._group(
                snap,
                focus,
                state,
                rec=snap.nodes[root],
                owed=owed.get(root[0], []),
                cursor=cursor,
                now=MONOTONIC,
                menu=None,
                now_wall=WALL,
            )
        after_group(project)


PANE_NAME = "FLEET"


def lane_height() -> float:
    """A dock's tab lane: the UI font plus ``2 * frame_padding.y`` (``tabs``)."""
    return float(imgui.get_font_size() + 2.0 * imgui.get_style().frame_padding.y)


def draw_tab_lane() -> None:
    """
    The pane's own header, as the app shows it: the dock lane with its window-menu
    arrow, and on it the ``fleet`` tab painted by ``tabs._paint`` -- the same
    painter the app runs over ImGui's tab -- in the ``selected`` look of a pane
    that is not focused, with its close cross where ``tabs.close_button`` puts it
    when ``tabs.shows_close`` says the style shows one.

    **The geometry is this function's, not ImGui's bar.** The tab's rectangle is
    ``TabItemCalcSize``'s (label, ``frame_padding.x`` each side, room for the
    close button) and it starts past an arrow button of the UI font's size plus
    ``frame_padding.x`` each side and ``item_inner_spacing.x``. The lane is
    ``title_bg``. Compared by eye against the running app's FLEET tab, not
    measured. Claims the lane's height.
    """
    from pptmstr.ui import tabs

    style = imgui.get_style()
    ui_px = float(imgui.get_font_size())
    pad = (float(style.frame_padding.x), float(style.frame_padding.y))
    lo = imgui.get_cursor_screen_pos()
    dl = imgui.get_window_draw_list()
    dl.add_rect_filled(
        imgui.ImVec2(lo.x, lo.y),
        imgui.ImVec2(lo.x + PANE_W, lo.y + lane_height()),
        imgui.get_color_u32(imgui.Col_.title_bg),
    )
    arrow_w = ui_px + pad[0] * 2.0
    imgui.internal.render_arrow(
        dl,
        imgui.ImVec2(lo.x + pad[0], lo.y + pad[1]),
        imgui.get_color_u32(imgui.Col_.text),
        imgui.Dir.down,
        1.0,
    )
    lane = tabs.Rect(
        lo.x + arrow_w + float(style.item_inner_spacing.x),
        lo.y,
        lo.x + PANE_W,
        lo.y + lane_height(),
    )
    width = float(
        imgui.calc_text_size(PANE_NAME).x + pad[0] * 2.0 + style.item_inner_spacing.x + ui_px
    )
    shown = tabs.shows_close(
        True,
        True,
        False,
        float(style.tab_close_button_min_width_selected),
        float(style.tab_close_button_min_width_unselected),
        width,
        ui_px,
    )
    close = tabs.close_button(lane, 0.0, width, 0.0, pad, ui_px) if shown else None
    tab = tabs.Tab(
        tabs.tab_box(lane, 0.0, width, 0.0),
        tabs.display_label(PANE_NAME),
        "selected",
        close,
        False,
    )
    imgui.push_font(None, tabs.TEXT_PX)
    try:
        tabs._paint(dl, tab, float(imgui.get_font_size()), imgui.get_font())
    finally:
        imgui.pop_font()
    imgui.dummy(imgui.ImVec2(PANE_W, lane.h))


def draw_pane(inv: Invitation, snap: Snapshot, key: str) -> None:
    """
    One FLEET pane: its ``fleet`` tab header, the real rail, and the invitation
    where it goes. Every variant keeps the header; none puts a title of its own
    in its place.
    """
    from pptmstr.theme import P

    imgui.begin_group()
    imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(0.0, 0.0))
    draw_tab_lane()
    imgui.pop_style_var()
    imgui.push_style_color(imgui.Col_.child_bg, P.bg.vec4)
    imgui.begin_child(
        f"##pane-{key}",
        imgui.ImVec2(PANE_W, PANE_H - lane_height()),
        imgui.ChildFlags_.borders,
        imgui.WindowFlags_.no_scrollbar,
    )
    hook: Callable[[str], None] | None = None
    if inv.variant is Variant.PROJECT:

        def hook(project: str) -> None:
            draw_invitation(inv, key, project)

    draw_rail(snap, hook)
    draw_invitation(inv, key)
    imgui.end_child()
    imgui.pop_style_color()
    imgui.end_group()


class _Shown:
    sheet = False
    theme = "dark"


def _sheet_window() -> bool:
    from pptmstr.theme import P

    imgui.set_next_window_pos(imgui.ImVec2(0.0, 0.0))
    imgui.set_next_window_size(imgui.ImVec2(*SHEET_SIZE))
    imgui.push_style_color(imgui.Col_.window_bg, P.bg.vec4)
    opened, _ = imgui.begin(
        "fleet empty###fleet-sheet",
        None,
        imgui.WindowFlags_.no_decoration | imgui.WindowFlags_.no_saved_settings,
    )
    imgui.pop_style_color()
    return opened


def draw_sheet() -> None:
    import link_probe

    from pptmstr.theme import P

    if not _sheet_window():
        imgui.end()
        return
    ink = _ink()
    imgui.text_colored(
        P.text_strong.vec4,
        "FLEET, mostly empty, in variants, each under the pane's own fleet tab"
        f" · theme {_Shown.theme} · pane {PANE_W:g} x {PANE_H:g}"
        f" (0.21 of a 1600 px window) · links: link_probe.link, accent {link_probe.hex_of(ink)},"
        " underline always",
    )
    imgui.text_colored(
        P.text_dim.vec4,
        "Each pane is the real rail over a Snapshot the Store built; fixture projects"
        f" {PPTMSTR} and {SCRATCH}; the resume rows are fixtures with their directories taken"
        " as present. Clicks here do nothing.",
    )
    imgui.dummy(imgui.ImVec2(0.0, 4.0))
    top = imgui.get_cursor_pos_y()
    for n, state in enumerate(State):
        imgui.set_cursor_pos(imgui.ImVec2(LEFT + n * COLUMN, top))
        imgui.text_colored(P.text_strong.vec4, state.value)
    imgui.set_cursor_pos(imgui.ImVec2(LEFT, imgui.get_cursor_pos_y()))
    imgui.separator()
    row_top = imgui.get_cursor_pos_y()
    current: Variant | None = None
    bottom = row_top
    for variant, state, snap, inv in cells():
        if variant is not current:
            current = variant
            row_top = bottom + 6.0
            imgui.set_cursor_pos(imgui.ImVec2(LEFT, row_top))
            imgui.text_colored(P.accent.vec4, TITLE[variant])
            row_top = imgui.get_cursor_pos_y() + 2.0
        n = list(State).index(state)
        x = LEFT + n * COLUMN
        imgui.set_cursor_pos(imgui.ImVec2(x, row_top))
        draw_pane(inv, snap, f"{variant.value}{n}")
        imgui.set_cursor_pos_x(x)
        imgui.push_text_wrap_pos(x + PANE_W)
        imgui.push_font(None, 12.5)
        for line in labels(inv):
            imgui.set_cursor_pos_x(x)
            imgui.text_colored(P.text_dim.vec4, line)
        imgui.pop_font()
        imgui.pop_text_wrap_pos()
        bottom = max(bottom, imgui.get_cursor_pos_y())
        if n == len(State) - 1:
            imgui.set_cursor_pos(imgui.ImVec2(LEFT, bottom + 2.0))
            imgui.separator()
            bottom = imgui.get_cursor_pos_y()
    imgui.end()


# -- the run --------------------------------------------------------------------------


def _scenario(state: Any, fixtures: Path) -> Iterator[str | None]:
    import screenshot
    from imgui_bundle import hello_imgui

    del fixtures
    yield from screenshot._until(
        "the fake fleet", lambda: state.driver is not None and state.frame_snap is not None
    )
    hello_imgui.change_window_size(SHEET_SIZE)
    yield from screenshot._until(
        f"the window at {SHEET_SIZE}",
        lambda: tuple(int(v) for v in imgui.get_io().display_size) == SHEET_SIZE,
    )
    yield from screenshot._frames(30)
    _Shown.sheet = True
    yield from screenshot._frames(10)
    yield _Shown.theme
    _Shown.sheet = False


def _render(out_dir: Path, theme_name: str) -> int:
    import screenshot

    from pptmstr import app as pptmstr_app
    from pptmstr import settings as settings_mod

    real_read = settings_mod.read

    @functools.wraps(real_read)
    def read(path: Path | None = None) -> Any:
        found = real_read(path)
        return dataclasses.replace(
            found, settings=dataclasses.replace(found.settings, theme=theme_name)
        )

    settings_mod.read = read  # type: ignore[assignment]
    real_overlays = pptmstr_app._draw_overlays

    def overlays(state: Any) -> None:
        real_overlays(state)
        if _Shown.sheet:
            draw_sheet()

    pptmstr_app._draw_overlays = overlays  # type: ignore[assignment]
    _Shown.theme = theme_name
    screenshot.SCENARIOS[SCENARIO] = _scenario
    out_dir.mkdir(parents=True, exist_ok=True)
    sys.argv = [
        "screenshot.py",
        "--scenario",
        SCENARIO,
        "--out-dir",
        str(out_dir),
        "--argv",
        "--fake",
    ]
    return int(screenshot.main())


def _on_xvfb(out_dir: Path, themes: tuple[str, ...], root: Path) -> int:
    worst = 0
    for name in themes:
        env = dict(os.environ)
        env["XVFB_SHOT_SCREEN"] = f"{SHEET_SIZE[0] + 100}x{SHEET_SIZE[1] + 100}x24"
        started = time.monotonic()
        done = subprocess.run(
            [
                str(root / "scripts" / "xvfb_shot.sh"),
                "--",
                str(root / ".venv" / "bin" / "python"),
                str(root / "scripts" / "fleet_empty_probe.py"),
                "--render",
                "--theme",
                name,
                "--out-dir",
                str(out_dir),
            ],
            cwd=root,
            env=env,
        )
        print(f"{name}: exit {done.returncode} in {time.monotonic() - started:.0f}s", flush=True)
        if done.returncode or not (out_dir / f"{name}.png").is_file():
            print(f"{name}: no sheet", file=sys.stderr)
            worst = worst or done.returncode or 1
    return worst


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").strip().splitlines()[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="every theme, each on its own Xvfb")
    which.add_argument("--render", action="store_true", help="one theme, on the display there is")
    which.add_argument("--describe", action="store_true", help="print every cell's strings")
    ap.add_argument("--out-dir", type=Path, default=OUT)
    ap.add_argument("--theme", action="append", choices=THEMES)
    ap.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="the tree whose app and scripts --all runs (a `git archive` export)",
    )
    args = ap.parse_args()
    themes = tuple(args.theme or THEMES)
    if args.describe:
        for line in describe():
            print(line)
        print(json.dumps({"chord": chord()}))
        return 0
    if args.all:
        return _on_xvfb(args.out_dir.resolve(), themes, args.root.resolve())
    return _render(args.out_dir, themes[0])


if __name__ == "__main__":
    raise SystemExit(main())
