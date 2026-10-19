"""
The link form: the operator's link of one lead to another, opened from a card's
"Link leads…" (``session_menu``) and submitted by its own button and nothing else.

It asks three things -- which other lead, under what name, with what charter --
and links nothing until it is submitted. **A refusal is shown in the form and the
form stays open**, with what was typed, whichever of the two places refuses it:
the store's rule, asked of the frame's snapshot before anything is emitted
(``Snapshot.link_refusal``), or the shell's, which ``Link`` hands back
(``app.link_leads``: a charter over the cap, logs that could not be listed). A
link the store refuses after the form has closed -- a frame that changed between
the ask and the apply -- is the shell's to say, and it does (``app._record_channels``).

**Pure above the line, Dear ImGui below it**, as ``session_menu`` is: ``press``
says what submitting does, ``draw`` is the thin half.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from imgui_bundle import imgui

from .. import drawable
from ..model import MAX_CHANNEL_CHARS, LinkRefusal, Snapshot
from ..theme import P
from . import session_menu, widgets

TITLE = "Link leads###link-leads"
NAME_HINT = (
    "lowercase letters, digits, '.', '_' and '-', starting with a letter or digit;"
    f" at most {MAX_CHANNEL_CHARS}"
)
NO_PEER_CHOSEN = "choose the lead to link to"
# A refusal is drawn under the fields and not in a tooltip, as a menu's is.
_WIDTH = widgets.DIALOG_WIDTH
_CHARTER_HEIGHT = 140.0
# Handed the name, both members and the charter; answers why nothing was linked,
# or None when the link was emitted. ``app._link_pressed`` is the one there is.
Link = Callable[[str, frozenset[str], str], str | None]
# A ``link_refusal`` refuses any id it already holds. The form has minted none:
# the shell mints it with the emit, so this one is held by no channel.
_NO_ID_YET = ""


@dataclass
class LinkForm:
    """
    What one lead's form holds: the chosen peer by session id, the name and the
    charter as typed, and the last refusal, which stays until the next submit.
    """

    session_id: str
    peer: str | None = None
    channel_name: str = ""
    charter: str = ""
    refused: str | None = None


@dataclass
class LinkForms:
    """
    The one link form, on ``AppState``; nothing here enters the store. At most
    one is open, since it is a modal.
    """

    form: LinkForm | None = None
    # Asked for by a card and not yet opened: the popup is opened at root level
    # on the next ``draw``, outside the menu that asked.
    open_requested: bool = False
    is_open: bool = False
    # Frames the form has been drawn for. Escape is read from the second: the
    # Enter that chose the menu item is still down on the first.
    frames: int = 0
    # Whether the peer list was dropped down on the last frame. The Escape that
    # closes it is gone from it by the frame that reads it, and is not the form's.
    dropped: bool = False

    def open_for(self, session_id: str) -> None:
        """
        Ask for the form for ``session_id``. A form already open for it keeps
        what was typed; one for another lead is replaced.
        """
        if self.form is None or self.form.session_id != session_id:
            self.form = LinkForm(session_id)
        self.open_requested = True

    def close(self) -> None:
        self.form = None
        self.open_requested = False
        self.is_open = False
        self.frames = 0
        self.dropped = False

    @property
    def busy(self) -> bool:
        """Whether the form is up or asked for: what another modal must not open over."""
        return self.form is not None


def refusal(form: LinkForm, snap: Snapshot, recording: frozenset[str]) -> str | None:
    """
    Why the store would refuse this link now, in words, or None. Asked before
    anything is emitted, so a refusal costs the operator nothing they typed.

    A name it refuses is said with the rule, which is the part the operator
    can act on.
    """
    if form.peer is None:
        return NO_PEER_CHOSEN
    refused = snap.link_refusal(
        _NO_ID_YET, form.channel_name, frozenset({form.session_id, form.peer}), recording
    )
    if refused is None:
        return None
    if refused is LinkRefusal.BAD_NAME:
        return f"{refused.value}: {NAME_HINT}"
    return str(refused.value)


def press(form: LinkForm, snap: Snapshot, recording: frozenset[str], link: Link) -> bool:
    """
    Submit the form: whether a link was emitted. ``link`` is called at most
    once, and only when ``refusal`` has nothing to say; a refusal from either
    is kept on the form.
    """
    reason = refusal(form, snap, recording)
    if reason is None and form.peer is not None:
        reason = link(form.channel_name, frozenset({form.session_id, form.peer}), form.charter)
        if reason is None:
            form.refused = None
            return True
    form.refused = reason
    return False


# ---------------------------------------------------------------------------
# Dear ImGui
# ---------------------------------------------------------------------------


def draw(
    state: LinkForms,
    snap: Snapshot | None,
    recording: frozenset[str],
    *,
    link: Link,
    charter_cap: int,
    wrap: bool,
) -> None:
    """
    Draw the form if it has been asked for. Root level, as the resume form is.

    ``recording`` is the sessions whose boards are recorded
    (``session_menu.Facts.recording``) and ``charter_cap`` the longest charter
    the shell takes, said beside the box. A lead that has ended or left the
    store since the form opened is not offered; a choice of one that has is
    refused on submit with the store's reason.
    """
    form = state.form
    if form is None or snap is None:
        if form is None:
            state.close()
        return
    if state.open_requested:
        state.open_requested = False
        state.is_open = True
        state.frames = 0
        imgui.open_popup(TITLE)
    if not state.is_open:
        return
    with widgets.dialog_style():
        _modal(state, form, snap, recording, link=link, charter_cap=charter_cap, wrap=wrap)


def _modal(
    state: LinkForms,
    form: LinkForm,
    snap: Snapshot,
    recording: frozenset[str],
    *,
    link: Link,
    charter_cap: int,
    wrap: bool,
) -> None:
    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(_WIDTH, 0.0), imgui.ImVec2(_WIDTH, viewport.work_size.y)
    )
    opened, _ = imgui.begin_popup_modal(TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        # Closed from outside; what was typed goes with it, as the resume form's does.
        state.close()
        return
    listening = state.frames > 0
    state.frames += 1
    linked = cancelled = False
    imgui.push_id(form.session_id)
    # Everything from here to ``end_popup`` is inside, so whatever a field or
    # ``link`` raises leaves the id and the popup closed out.
    try:
        cancelled, linked = _fields(
            state, form, snap, recording, link, charter_cap, wrap, listening
        )
    finally:
        imgui.pop_id()
        if cancelled or linked:
            state.close()
            imgui.close_current_popup()
        imgui.end_popup()


def _fields(
    state: LinkForms,
    form: LinkForm,
    snap: Snapshot,
    recording: frozenset[str],
    link: Link,
    charter_cap: int,
    wrap: bool,
    listening: bool,
) -> tuple[bool, bool]:
    """The form's body, inside the open modal: whether it was cancelled, and linked."""
    own = session_menu.peer_label(snap, form.session_id)
    imgui.push_text_wrap_pos(0.0)
    try:
        imgui.text_disabled("link")
        imgui.same_line()
        imgui.text_colored(P.text_strong.vec4, own)
    finally:
        imgui.pop_text_wrap_pos()

    dropped = _peer_choice(form, snap, recording)
    was_dropped, state.dropped = state.dropped, dropped

    imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
    _, form.channel_name = imgui.input_text("channel name", form.channel_name)
    imgui.text_disabled(NAME_HINT)

    imgui.text_disabled("charter")
    _, form.charter = widgets.multiline_input(
        "##charter", form.charter, imgui.ImVec2(-1.0, _CHARTER_HEIGHT), wrap=wrap
    )
    over = len(form.charter) > charter_cap
    imgui.text_colored(
        (P.warn if over else P.text_dim).vec4, f"{len(form.charter)} of {charter_cap} characters"
    )

    if form.refused is not None:
        imgui.push_text_wrap_pos(0.0)
        try:
            imgui.text_colored(P.warn.vec4, drawable.text(form.refused))
        finally:
            imgui.pop_text_wrap_pos()

    submitted = bool(imgui.button("link", imgui.ImVec2(110.0, 0.0)))
    imgui.same_line()
    cancelled = bool(imgui.button("cancel", imgui.ImVec2(90.0, 0.0)))
    # Escape in a text box, or with the peer list down (or put up by it on the
    # frame before), is that control's and not a cancel of the form.
    if listening and not dropped and not was_dropped and not imgui.is_any_item_active():
        # Not repeated: a held Escape that closed the list must not go on to
        # discard the form once the list's frames have passed.
        cancelled = cancelled or bool(imgui.is_key_pressed(imgui.Key.escape, False))
    linked = submitted and not cancelled and press(form, snap, recording, link)
    return cancelled, linked


def _peer_choice(form: LinkForm, snap: Snapshot, recording: frozenset[str]) -> bool:
    """
    The peer list, and whether it is dropped down. A label is the card's title,
    which may hold "##": drawn as text beside an id-only item, and in a preview
    the combo does not parse, so it is never cut there.
    """
    preview = NO_PEER_CHOSEN if form.peer is None else session_menu.peer_label(snap, form.peer)
    imgui.set_next_item_width(-1.0)
    dropped = bool(
        imgui.begin_combo("##peer", "", imgui.internal.ComboFlagsPrivate_.custom_preview.value)
    )
    if dropped:
        # The list is a popup window of its own: whatever a row raises, it is
        # ended here, so the modal's ``end_popup`` ends the modal.
        try:
            for rec in session_menu.link_peers(snap, form.session_id, recording):
                sid = rec.node_id[0]
                at = imgui.get_cursor_pos()
                clicked, _ = imgui.selectable(f"##peer-{sid}", form.peer == sid)
                imgui.set_cursor_pos(at)
                imgui.text_unformatted(session_menu.peer_label(snap, sid))
                if clicked:
                    form.peer = sid
        finally:
            imgui.end_combo()
    if imgui.internal.begin_combo_preview():
        imgui.text_unformatted(preview)
        imgui.internal.end_combo_preview()
    return dropped
