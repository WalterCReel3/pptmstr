"""
The fleet-empty probe's invitations: that each is built from what the rail draws,
that every string in one is drawable, and that its rail is ``rail.draw``'s.

A pixel of the sheet is not asserted here; the last test only draws every cell
in a headless frame and asks that nothing raise.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import imgui_bundle
import pytest
from imgui_bundle import imgui

from pptmstr import drawable, theme
from pptmstr.intents import AgentSpawned
from pptmstr.store import Store
from pptmstr.ui import projects, rail
from pptmstr.ui.focus import FocusState

ROOT = Path(__file__).resolve().parent.parent
INCONSOLATA = Path(imgui_bundle.__file__).parent / "assets" / "fonts" / "Inconsolata-Medium.ttf"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("fleet_empty_probe")


def _strings(inv: Any) -> list[str]:
    out = [inv.note]
    for offer in inv.offers:
        out += [part.text for part in offer.parts]
        out += [s for pair in offer.prefills for s in pair]
        out.append(offer.click)
        out.append(offer.project or "")
    return out


@pytest.mark.parametrize("state", list(probe.State), ids=lambda s: s.name)
def test_per_project_links_are_the_rails_own_groups_and_directories(state: Any) -> None:
    snap = probe.snapshot(state)
    inv = probe.invite(probe.Variant.PROJECT, snap, probe.field_cwd(state))
    groups = [p for p, _ in projects.group_roots(snap) if p != projects.UNFILED]
    if not groups:
        # No group to hang one under: a single line in the field's directory.
        assert [o.project for o in inv.offers] == [None]
        assert inv.offers[0].prefills == ()
        return
    assert [o.project for o in inv.offers] == groups
    for offer, (_, roots) in zip(inv.offers, projects.group_roots(snap), strict=True):
        assert offer.parts[0].text == f"+ new in {offer.project}"
        assert offer.prefills == (("cwd", snap.nodes[roots[-1]].session_base),)


def test_recent_rows_leave_out_every_session_already_a_card() -> None:
    snap = probe.snapshot(probe.State.SHORT)
    carded = probe.on_fleet(snap)
    assert "s-old" in carded
    inv = probe.invite(probe.Variant.RECENT, snap, "x", probe.ROWS, where=probe._present)
    picked = [dict(o.prefills)["picked"] for o in inv.offers if o.prefills]
    assert picked == ["s-port", "s-menu", "s-gate"]
    assert not carded & set(picked)
    assert "1 already a card" in inv.note
    # The last line is (a)'s, so the cell still offers a fresh session.
    assert inv.offers[-1] == probe.invite(probe.Variant.LINE, snap, "x").offers[0]


def test_a_recent_row_whose_directory_is_gone_is_offered_disabled(tmp_path: Path) -> None:
    there = tmp_path / "there"
    there.mkdir()
    rows = (
        dataclasses.replace(probe.ROWS[0], cwd=str(there)),
        dataclasses.replace(probe.ROWS[2], cwd=str(tmp_path / "gone")),
    )
    snap = probe.snapshot(probe.State.NONE)
    inv = probe.invite(probe.Variant.RECENT, snap, str(tmp_path), rows)
    present, gone = inv.offers[:2]
    assert present.enabled and dict(present.prefills)["directory"] == str(there)
    assert not gone.enabled
    assert gone.parts[-1].text == "directory gone"
    assert "held" in gone.click


def test_the_field_is_what_a_fresh_launch_offers(tmp_path: Path) -> None:
    snap = probe.snapshot(probe.State.NONE)
    for variant in (probe.Variant.LINE, probe.Variant.PANEL):
        inv = probe.invite(variant, snap, str(tmp_path))
        assert all(o.prefills == () for o in inv.offers)
        assert any(str(tmp_path) in s for s in _strings(inv))
    assert probe.chord() == "Ctrl+N"


BAD = "a\udc80b\x00c"
# A directory cannot hold a NUL, and ``projects.project_key`` raises on one, so a
# cwd carries the surrogate alone.
BAD_DIR = "a\udc80b"


def test_every_string_an_invitation_holds_is_drawable() -> None:
    """A surrogate in a project's directory, and a NUL as well in the field and a row's summary."""
    store = Store()
    store.apply(
        AgentSpawned(
            node_id=("s", None),
            parent=None,
            task="t",
            model="m",
            started_at=1.0,
            cwd=f"/src/{BAD_DIR}",
            session_base=f"/src/{BAD_DIR}",
        )
    )
    snap = store.snapshot()
    rows = (dataclasses.replace(probe.ROWS[0], summary=BAD, cwd=f"/src/{BAD_DIR}"),)
    for variant in probe.Variant:
        inv = probe.invite(variant, snap, f"/src/{BAD}", rows, where=probe._present)
        strings = _strings(inv)
        assert not drawable.undrawable(strings), (variant, strings)
        assert not drawable.undrawable(probe.labels(inv))
    # And it was the escape that made them so, not a string that lost the text.
    recent = probe.invite(probe.Variant.RECENT, snap, ".", rows, where=probe._present)
    assert recent.offers[0].parts[1].text == drawable.text(BAD)


def test_the_probes_rail_calls_what_rail_draw_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Header and group, in order, with the same arguments, for every state."""
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        rail, "_project_header", lambda snap, project, roots, owed: calls.append(("h", project))
    )
    monkeypatch.setattr(
        rail,
        "_group",
        lambda snap, focus, state, **kw: calls.append(
            ("g", kw["rec"].node_id, len(kw["owed"]), kw["now"], kw["now_wall"])
        ),
    )
    monkeypatch.setattr(imgui, "text_disabled", lambda *_: None)
    monkeypatch.setattr(imgui, "spacing", lambda: None)
    for state in probe.State:
        snap = probe.snapshot(state)
        calls.clear()
        rail.draw(
            snap,
            FocusState(),
            rail.RailState(),
            probe.MONOTONIC,
            None,
            now_wall=probe.WALL,
        )
        by_rail = list(calls)
        calls.clear()
        hooked: list[str] = []
        probe.draw_rail(snap, hooked.append)
        assert calls == by_rail
        assert hooked == [p for p, _ in projects.group_roots(snap)]


@pytest.fixture
def faced() -> Iterator[None]:
    if not INCONSOLATA.exists():
        pytest.skip(f"no {INCONSOLATA.name} in this imgui_bundle")
    made = imgui.create_context()
    imgui.set_current_context(made)
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(*probe.SHEET_SIZE)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename("")
    io.fonts.add_font_from_file_ttf(str(INCONSOLATA), 16.0)
    yield
    imgui.destroy_context(made)


@pytest.mark.parametrize("name", probe.THEMES)
def test_every_cell_draws_in_a_headless_frame(faced: None, name: str) -> None:
    theme.set_theme(name)
    theme.apply_style()
    for _ in range(2):
        imgui.new_frame()
        try:
            probe.draw_sheet()
        finally:
            imgui.render()
