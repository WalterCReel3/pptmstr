"""
The link form: what it refuses before anything is emitted, what it does with a
refusal the shell hands back, and that one submit is one call to link.

The refusals are asked of a snapshot built by the real reducer, so the form and
the store cannot come to disagree about what links. The frames at the end are
drawn in a real Dear ImGui context with no backend: what they show is that the
modal opens, balances, and closes on a link and only on one. No pixel of it has
been looked at.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from pptmstr.intents import AgentFinished, AgentSpawned, StateChanged
from pptmstr.model import AgentState, LinkRefusal, NodeId, Snapshot
from pptmstr.store import Store
from pptmstr.ui import link_form, widgets
from pptmstr.ui.link_form import LinkForm, LinkForms

A: NodeId = ("lead-a", None)
B: NodeId = ("lead-b", None)
C: NodeId = ("lead-c", None)
BOTH = frozenset({A[0], B[0]})


def _snap(*, ended: tuple[NodeId, ...] = ()) -> Snapshot:
    store = Store()
    for node, task in ((A, "brand work"), (B, "ops work"), (C, "audit")):
        store.apply(AgentSpawned(node, None, task, "m", 0.0), now=1.0)
        store.apply(StateChanged(node, AgentState.THINKING), now=1.0)
    for node in ended:
        store.apply(AgentFinished(node, AgentState.DONE, 2.0), now=2.0)
    return store.snapshot()


class _Link:
    """A ``link_form.Link`` that answers ``reason`` and keeps every call."""

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason
        self.calls: list[tuple[str, frozenset[str], str]] = []

    def __call__(self, name: str, members: frozenset[str], charter: str) -> str | None:
        self.calls.append((name, members, charter))
        return self.reason


def _form(**typed: object) -> LinkForm:
    form = LinkForm(A[0], peer=B[0], channel_name="brand", charter="share the brand board")
    for key, value in typed.items():
        setattr(form, key, value)
    return form


def test_a_good_submit_links_once_with_both_members_and_the_charter() -> None:
    link, form = _Link(), _form(refused="an earlier refusal")

    assert link_form.press(form, _snap(), BOTH, link)

    assert link.calls == [("brand", BOTH, "share the brand board")]
    assert form.refused is None


@pytest.mark.parametrize("name", ["", "Brand", "../x", "a b", "-lead", "x" * 65, "zero​width"])
def test_a_bad_name_is_refused_with_the_rule_and_nothing_is_linked(name: str) -> None:
    link, form = _Link(), _form(channel_name=name)

    assert not link_form.press(form, _snap(), BOTH, link)

    assert link.calls == []
    assert form.refused == f"{LinkRefusal.BAD_NAME.value}: {link_form.NAME_HINT}"


def test_a_peer_whose_board_is_not_recorded_is_refused_with_why() -> None:
    link, form = _Link(), _form()

    assert not link_form.press(form, _snap(), frozenset({A[0]}), link)

    assert link.calls == []
    assert form.refused == LinkRefusal.NOT_RECORDING.value


def test_a_peer_that_ended_since_it_was_chosen_is_refused_with_why() -> None:
    link, form = _Link(), _form()

    assert not link_form.press(form, _snap(ended=(B,)), BOTH, link)

    assert link.calls == []
    assert form.refused == LinkRefusal.NOT_RUNNING.value


def test_no_peer_chosen_is_refused() -> None:
    link, form = _Link(), _form(peer=None)

    assert not link_form.press(form, _snap(), BOTH, link)

    assert link.calls == [] and form.refused == link_form.NO_PEER_CHOSEN


def test_a_refusal_from_the_shell_is_kept_on_the_form_with_what_was_typed() -> None:
    """
    The charter cap and the logs that could not be listed are the shell's to
    refuse (``app.link_leads``); the form shows the reason and keeps the text.
    """
    link = _Link("the charter is 9000 characters, over the 8192 a link's charter may be")
    form = _form(charter="x" * 9000)

    assert not link_form.press(form, _snap(), BOTH, link)

    assert len(link.calls) == 1
    assert form.refused == link.reason
    assert form.charter == "x" * 9000 and form.channel_name == "brand"


def test_the_form_offers_every_other_running_recorded_lead_and_no_one_else() -> None:
    from pptmstr.ui import session_menu

    snap = _snap(ended=(C,))
    offered = session_menu.link_peers(snap, A[0], frozenset({A[0], B[0], C[0]}))
    assert [rec.node_id for rec in offered] == [B]
    assert session_menu.link_peers(snap, A[0], frozenset({A[0]})) == ()


def test_opening_again_for_the_same_lead_keeps_what_was_typed() -> None:
    forms = LinkForms()
    forms.open_for(A[0])
    assert forms.form is not None
    forms.form.channel_name = "brand"
    forms.open_for(A[0])
    assert forms.form.channel_name == "brand" and forms.open_requested
    forms.open_for(B[0])
    assert forms.form.session_id == B[0] and forms.form.channel_name == ""
    forms.close()
    assert not forms.busy and not forms.is_open


# -- frames --------------------------------------------------------------------------


@pytest.fixture
def context() -> Iterator[imgui.IO]:
    made = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1280.0, 900.0)
    io.delta_time = 1.0 / 60.0
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    io.set_ini_filename("")
    io.add_focus_event(True)
    yield io
    imgui.destroy_context(made)


class _Pressing:
    """``imgui`` for the form, in which the button with one label is pressed."""

    def __init__(self, label: str | None) -> None:
        self._label = label

    def __getattr__(self, name: str) -> object:
        return getattr(imgui, name)

    def button(self, label: str, *args: object) -> bool:
        imgui.button(label, *args)  # type: ignore[arg-type]
        return label == self._label


def _frame(forms: LinkForms, snap: Snapshot, link: _Link) -> None:
    imgui.new_frame()
    link_form.draw(forms, snap, BOTH, link=link, charter_cap=8192, wrap=False)
    imgui.end_frame()


def test_the_form_opens_and_links_nothing_until_its_button_is_pressed(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    forms, link, snap = LinkForms(), _Link(), _snap()
    forms.open_for(A[0])
    assert forms.form is not None
    forms.form.peer, forms.form.channel_name = B[0], "brand"

    monkeypatch.setattr(link_form, "imgui", _Pressing(None))
    for _ in range(3):
        _frame(forms, snap, link)
    assert forms.is_open and link.calls == []

    monkeypatch.setattr(link_form, "imgui", _Pressing("link"))
    _frame(forms, snap, link)
    assert link.calls == [("brand", BOTH, "")]
    assert forms.form is None and not forms.is_open


def test_a_refused_submit_leaves_the_form_open_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    forms, link, snap = LinkForms(), _Link("refused by the shell"), _snap()
    forms.open_for(A[0])
    assert forms.form is not None
    forms.form.peer, forms.form.channel_name = B[0], "brand"

    monkeypatch.setattr(link_form, "imgui", _Pressing("link"))
    _frame(forms, snap, link)
    monkeypatch.setattr(link_form, "imgui", _Pressing(None))
    _frame(forms, snap, link)

    assert len(link.calls) == 1
    assert forms.is_open and forms.form is not None
    assert forms.form.refused == "refused by the shell"


def test_cancel_closes_the_form_and_links_nothing(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    forms, link, snap = LinkForms(), _Link(), _snap()
    forms.open_for(A[0])
    monkeypatch.setattr(link_form, "imgui", _Pressing("cancel"))
    _frame(forms, snap, link)
    assert link.calls == [] and forms.form is None


class _Dropped(_Pressing):
    """
    ``imgui`` for the form with the peer list down, or not, and Escape pressed,
    or not. The list is ImGui's own popup, opened and closed by its id before
    the combo is drawn, as a click and an Escape would.
    """

    def __init__(self, *, dropped: bool, escape: bool) -> None:
        super().__init__(None)
        self.dropped, self.escape = dropped, escape
        self.texts: list[str] = []
        self.selectables: list[str] = []

    def begin_combo(self, label: str, *args: object) -> bool:
        popup = imgui.internal.im_hash_str("##ComboPopup", 0, imgui.get_id(label))
        if self.dropped:
            imgui.internal.open_popup_ex(popup)
        elif imgui.internal.is_popup_open(popup, 0):
            # The modal is level 0 of the popup stack and the list level 1.
            imgui.internal.close_popup_to_level(1, True)
        return bool(imgui.begin_combo(label, *args))  # type: ignore[arg-type]

    def selectable(self, label: str, *args: object) -> tuple[bool, bool]:
        self.selectables.append(label)
        return imgui.selectable(label, *args)  # type: ignore[arg-type,no-any-return]

    def text_unformatted(self, text: str, *args: object) -> None:
        self.texts.append(text)
        imgui.text_unformatted(text, *args)  # type: ignore[arg-type]

    def is_key_pressed(self, key: object, *args: object) -> bool:
        if key == imgui.Key.escape:
            return self.escape
        return imgui.is_key_pressed(key, *args)  # type: ignore[arg-type,no-any-return]


def test_escape_with_the_peer_list_down_closes_the_list_and_not_the_form(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    """
    With keyboard navigation on, ImGui closes the list on the Escape before the
    form draws, so the frame that reads Escape may find the list already up;
    the frame after the list was down is the list's too. Escape after that is
    the form's.
    """
    forms, link, snap = LinkForms(), _Link(), _snap()
    forms.open_for(A[0])
    assert forms.form is not None
    forms.form.channel_name = "brand"
    monkeypatch.setattr(link_form, "imgui", _Dropped(dropped=False, escape=False))
    _frame(forms, snap, link)

    monkeypatch.setattr(link_form, "imgui", _Dropped(dropped=True, escape=True))
    _frame(forms, snap, link)
    assert forms.is_open and forms.form is not None, "Escape on the list discarded the form"
    monkeypatch.setattr(link_form, "imgui", _Dropped(dropped=False, escape=True))
    _frame(forms, snap, link)
    assert forms.is_open and forms.form.channel_name == "brand"

    _frame(forms, snap, link)
    assert forms.form is None and link.calls == []


def test_a_peer_whose_task_holds_hashes_is_listed_and_previewed_whole(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    from pptmstr.ui import session_menu

    store = Store()
    for node, task in ((A, "brand work"), (B, "ops ## half ### rest")):
        store.apply(AgentSpawned(node, None, task, "m", 0.0), now=1.0)
        store.apply(StateChanged(node, AgentState.THINKING), now=1.0)
    snap = store.snapshot()
    label = session_menu.peer_label(snap, B[0])
    assert "##" in label

    forms, link = LinkForms(), _Link()
    forms.open_for(A[0])
    # The modal's first frame sizes it and draws no item.
    monkeypatch.setattr(link_form, "imgui", _Dropped(dropped=False, escape=False))
    _frame(forms, snap, link)
    drawn = _Dropped(dropped=True, escape=False)
    monkeypatch.setattr(link_form, "imgui", drawn)
    _frame(forms, snap, link)
    assert drawn.selectables == [f"##peer-{B[0]}"], "the list was not drawn"
    assert label in drawn.texts

    assert forms.form is not None
    forms.form.peer = B[0]
    drawn = _Dropped(dropped=False, escape=False)
    monkeypatch.setattr(link_form, "imgui", drawn)
    _frame(forms, snap, link)
    assert label in drawn.texts, "the preview is drawn as text, not parsed for an id"


def test_a_field_that_raises_leaves_the_modal_and_its_id_closed_out(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    forms, link, snap = LinkForms(), _Link(), _snap()
    forms.open_for(A[0])
    _frame(forms, snap, link)

    def boom(*args: object, **kw: object) -> object:
        raise RuntimeError("a field raised")

    monkeypatch.setattr(widgets, "multiline_input", boom)
    imgui.new_frame()
    with pytest.raises(RuntimeError):
        link_form.draw(forms, snap, BOTH, link=link, charter_cap=8192, wrap=False)
    # Balanced: the window stack is back at the frame's own, and the id stack
    # with it, so the frame ends without an ImGui error.
    assert imgui.internal.get_current_window().name.startswith("Debug##Default")
    imgui.end_frame()


class _HeldEscape(_Dropped):
    """``_Dropped`` with Escape left to ImGui, which is fed a real key event."""

    def is_key_pressed(self, key: object, *args: object) -> bool:
        return imgui.is_key_pressed(key, *args)  # type: ignore[arg-type,no-any-return]


def test_escape_held_past_its_repeat_delay_after_closing_the_list_keeps_the_form(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    """
    Escape goes down with the list down and is held, well past
    ``io.KeyRepeatDelay``. The list's frames pass; a repeat of the held key is
    not a fresh press, so it does not discard what was typed.
    """
    forms, link, snap = LinkForms(), _Link(), _snap()
    forms.open_for(A[0])
    assert forms.form is not None
    forms.form.channel_name = "brand"
    monkeypatch.setattr(link_form, "imgui", _HeldEscape(dropped=False, escape=False))
    _frame(forms, snap, link)

    monkeypatch.setattr(link_form, "imgui", _HeldEscape(dropped=True, escape=False))
    context.add_key_event(imgui.Key.escape, True)
    _frame(forms, snap, link)
    monkeypatch.setattr(link_form, "imgui", _HeldEscape(dropped=False, escape=False))
    frames = int(4 * context.key_repeat_delay / context.delta_time)
    for _ in range(frames):
        _frame(forms, snap, link)
        assert imgui.is_key_down(imgui.Key.escape)
        assert forms.is_open and forms.form is not None, "a held Escape discarded the form"

    context.add_key_event(imgui.Key.escape, False)
    _frame(forms, snap, link)
    context.add_key_event(imgui.Key.escape, True)
    _frame(forms, snap, link)
    assert forms.form is None and link.calls == [], "a fresh Escape no longer cancels"


def test_a_peer_list_that_raises_leaves_the_list_and_the_modal_closed_out(
    monkeypatch: pytest.MonkeyPatch, context: imgui.IO
) -> None:
    """
    The list is a popup window of its own, opened inside the modal: a raise
    from a row ends it before the modal is ended, so the window stack is back
    at the frame's own.
    """
    from pptmstr.ui import session_menu

    forms, link, snap = LinkForms(), _Link(), _snap()
    forms.open_for(A[0])
    monkeypatch.setattr(link_form, "imgui", _Dropped(dropped=False, escape=False))
    _frame(forms, snap, link)

    def boom(*args: object, **kw: object) -> object:
        raise RuntimeError("the peer list raised")

    monkeypatch.setattr(session_menu, "link_peers", boom)
    monkeypatch.setattr(link_form, "imgui", _Dropped(dropped=True, escape=False))
    imgui.new_frame()
    with pytest.raises(RuntimeError, match="the peer list raised"):
        link_form.draw(forms, snap, BOTH, link=link, charter_cap=8192, wrap=False)
    assert imgui.internal.get_current_window().name.startswith("Debug##Default")
    imgui.end_frame()
