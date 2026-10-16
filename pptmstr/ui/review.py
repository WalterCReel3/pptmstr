"""
Approval decisions: the draft state behind a review, and the way one leaves.

Not a pane. The queue and the diff live in ``inbox.py``, where an approval is one
of three kinds of obligation and its diff opens inside the row rather than in a
second pane with a second cursor. What is left here is the part that is about
*deciding* rather than about drawing: the half-typed rejection reason, the edited
arguments, the batch forms, and the keyboard.

Driven by the keyboard. The layout matters less than not reaching for the mouse
forty times an hour: j/k move, a approves, r rejects, e edits. Buttons stay for
discovery.

Everything here is presentation state (design §6). The decision leaves through the
Bridge, and the store learns about it when the gate emits ApprovalResolved -- so
what is rendered is always what the agent actually saw, never an optimistic guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from imgui_bundle import imgui

from ..approval import command_shown
from ..bridge import Bridge, Decision
from ..model import ApprovalNeeded, NodeId, PendingApproval, Snapshot
from .focus import FocusState

# Keys that act on the selected item. Kept in one place because the help line and
# the handler must not drift apart.
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("j / k", "next / previous"),
    ("a", "approve"),
    ("r", "reject"),
    ("e", "edit arguments"),
    ("A", "approve all from this agent"),
    ("esc", "cancel edit / confirm; after typing, makes these keys act again"),
)

# How many lines of one text a pane puts on screen unasked; past it the pane sets
# the head and offers the rest (``head_lines``). A text handed to the typesetting
# library is read and laid out whole in the frame that first draws it, 33 ms for
# each thousand lines of markdown (``scripts/profile_seam.py``): four frames at
# this bound, once, and a stall the operator did not ask for past it -- so they
# are asked.
#
# Here, and not in either pane that applies it: DETAIL and the inbox row show one
# parked call and must cut its body at one place, ``detail`` imports ``inbox``, and
# what the cut costs is this module's -- the lines a pane leaves unset are what it
# reports to ``ReviewState.body_shown``, and ``withheld`` holds the approve on them.
SETTLED_LINES = 2000

# The two panes that show a parked call, as each names itself to ``body_shown`` and
# ``pane_drew``.
INBOX, DETAIL = "inbox", "detail"

NOT_DRAWN = "not sent - this call's body had not been drawn when approve was asked for; ask again"
NOT_WHOLE = (
    "not sent - this call's body was not all on screen when approve was asked for; "
    "ask again once it is"
)
# Said above a parked call's body by each pane that draws it, where the arguments
# drawn are not character for character the ones sent (``PendingApproval.args``).
# ``printf '\\udc80'`` is a command a shell understands, so the escape alone would
# read as something the agent typed.
ESCAPED = (
    "not all of this is text: the arguments hold a code unit that is no character "
    "(a lone surrogate, a NUL), or a backslash that spells the escape of one. A "
    "Bash command spells each out as its row does (a NUL \\x00, a lone surrogate "
    "\\u{DC80}, a backslash doubled); any other argument draws each as \\uXXXX, "
    "and such a backslash as \\u005c. Approve sends the call as the agent made it."
)
# Said of an approve by any route, a batch included, and worded for all of them.
PANE_FAILED = (
    "not sent - a pane that shows parked calls failed to draw when approve was asked "
    "for, and nothing is approved over one; reject still works"
)


@dataclass(frozen=True, slots=True)
class Refusal:
    """
    An approve that was asked for and not sent: the pending ids it covered, and why.

    Worded in the past tense on purpose. It is a record of one request, shown on the
    rows it covered until a decision is sent or it is cancelled, and it stays true
    after the body it was refused over is on screen.
    """

    asked: frozenset[str]
    text: str


@dataclass
class Hold:
    """
    Whether the letter keys are the operator's shortcuts or the operator's prose.

    A text box can stop existing under the person typing in it: the decision is
    withdrawn by its asker, the parked call is settled by its agent ending, the
    session closes. The queue's cursor then lands on the next row, Dear ImGui lets
    go of the keyboard one frame later, and nothing the operator did marks the
    moment -- the next ``a`` of a sentence would approve a call nobody read. So
    having the keyboard free is not enough to act on a key.

    **The rule.** Once a text box has had the keyboard, no shortcut acts until the
    operator has shown they are no longer typing, and there are two ways to show
    it: Escape, or a mouse click. Neither can be part of a sentence. It is one rule
    for every text box in the application and for every way a box goes, because
    it asks only what the keyboard was last doing and never which box or why.

    What it costs: an operator who types anything -- a reason, a reply, an edit --
    presses Escape or clicks once before ``j``/``k``/``a`` answer again, including
    after a reply sent with Ctrl+Enter, since sending puts the caret back in the
    box, and after ``r`` or ``e``, which open a box (``begin``). ``held`` is what
    the inbox reads to say so, so dead keys are explained.

    A click made while the box still has the keyboard counts if the box is not
    there on the next frame: that is clicking a button to leave it. A click made
    in a box that then stays is followed by a frame of typing, which re-arms.
    """

    # A text box has had the keyboard and nothing since has shown the typing over.
    typing: bool = False
    # ``typing``, in a frame where no text box has the keyboard: the keys are dead
    # and the operator is owed the reason.
    held: bool = False
    # Whether the last frame a text box had the keyboard carried Escape or a click.
    _leaving: bool = False
    # What the last ``step`` answered: whether a bare key may act on this frame.
    # Kept because the step is taken once a frame and more than its caller acts
    # on keys (``app._handle_keys``), and no reading of ``typing`` afterwards
    # tells the frame that ends a hold from one nothing was typed before.
    free: bool = True

    def step(self, *, text_input: bool, gesture: bool) -> bool:
        """
        Once per frame. Whether a shortcut may act on this frame's keys, which is
        also left in ``free`` until the next step.

        ``text_input`` is whether a text box has the keyboard; ``gesture`` is
        whether Escape or a mouse button went down this frame. The frame that ends
        a hold acts on nothing either: the Escape that ends it is not also the
        Escape that cancels an edit on a row the cursor was moved to.
        """
        self.free = self._stepped(text_input, gesture)
        return self.free

    def begin(self) -> None:
        """
        A shortcut has just opened a text box: the hold starts at that keypress.

        A box asked for on this frame has the keyboard a frame or more later, or
        only once it is clicked, and until then no text box has it -- so the
        first letters of what the operator types next would be read as
        shortcuts, and a reason beginning with ``a`` approved the call it was
        rejecting. From here the keys are prose until Escape or a click, as
        after any typing, whether or not the box ever takes the keyboard.
        """
        self.typing = True
        self.held = False
        self._leaving = False
        self.free = False

    def _stepped(self, text_input: bool, gesture: bool) -> bool:
        if text_input:
            self.typing = True
            self.held = False
            self._leaving = gesture
            return False
        if not self.typing:
            return True
        if gesture or self._leaving:
            self.typing = False
            self.held = False
            self._leaving = False
        else:
            self.held = True
        return False


@dataclass
class ReviewState:
    """
    UI-side state for the review loop, keyed by pending id and pruned when an item
    leaves the queue.

    The edit buffer is deliberately here rather than in the store: a half-typed
    correction is not a fact about the world, and an approval that gets cancelled
    must take its draft with it.
    """

    reasons: dict[str, str] = field(default_factory=dict)
    edits: dict[str, str] = field(default_factory=dict)
    # Diff text split into lines, cached per pending id. Pruned with the rest.
    diff_lines: dict[str, list[str]] = field(default_factory=dict)
    # A parked call's diff as the typeset seam holds it (``typeset.ParkedDiff``),
    # per pending id: one for every pane that draws the call, so that the model
    # behind it is made once and not once a pane. Opaque here; ``inbox.
    # library_diff`` makes and reads it.
    diffs: dict[str, object] = field(default_factory=dict)
    # The calls whose diff the operator asked to have made past the bound
    # (``inbox.library_diff``).
    diff_whole: set[str] = field(default_factory=set)
    editing: str | None = None
    edit_error: str | None = None
    focus_reason: bool = False
    # Set when a global approve-all has been asked for but not yet confirmed.
    # Approving everything across every agent is the one action here that can
    # write things the operator never looked at, so it does not happen on one
    # keystroke -- see confirm_global_approve.
    confirming_global: bool = False
    # Whether the keys are shortcuts yet. Here and not in ``handle_keys``' caller
    # because the inbox header says when they are not, and reads it from this.
    hold: Hold = field(default_factory=Hold)
    # What each pane last saw of each parked call's body: pending id -> pane ->
    # (the ImGui frame it drew the body in, source lines of it that frame did not
    # put on screen). A reading and not a fact: it is as old as the pane's last
    # draw of that body, and ``handle_keys`` runs before any pane draws, so the
    # newest it can be is the frame before. Nothing in the snapshot implies it --
    # whether a text was set is the seam's and differs between two panes showing
    # one call -- so it is held here, written by ``body_shown`` and read only by
    # ``unset_lines``.
    bodies: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    # How each pane that shows parked calls last came out of its draw: pane ->
    # (the ImGui frame, whether it drew to its end). Written by ``pane_drew`` on
    # every frame the pane is drawn, and as old as ``bodies`` is, for the same
    # reason. Read only by ``failed_panes``.
    panes: dict[str, tuple[int, bool]] = field(default_factory=dict)
    refusal: Refusal | None = None

    def body_shown(self, pane: str, pending_id: str, unbuilt: int, frame: int) -> None:
        """
        A pane drew this call's body in ``frame`` with ``unbuilt`` source lines of it
        not on screen: the lines past ``SETTLED_LINES`` that have not been asked
        for, under either renderer, and every line of a text the typesetting
        library stopped under (``typeset.ViewCache.unbuilt``).
        """
        self.bodies.setdefault(pending_id, {})[pane] = (frame, unbuilt)

    def pane_drew(self, pane: str, frame: int, whole: bool) -> None:
        """
        A pane that shows parked calls was drawn in ``frame``, to its end or --
        ``whole`` false -- as far as whatever it raised.
        """
        self.panes[pane] = (frame, whole)

    def prune(self, live_ids: set[str]) -> None:
        for stale in [k for k in self.reasons if k not in live_ids]:
            del self.reasons[stale]
        for stale in [k for k in self.edits if k not in live_ids]:
            del self.edits[stale]
        for stale in [k for k in self.diff_lines if k not in live_ids]:
            del self.diff_lines[stale]
        for stale in [k for k in self.diffs if k not in live_ids]:
            del self.diffs[stale]
        self.diff_whole &= live_ids
        for stale in [k for k in self.bodies if k not in live_ids]:
            del self.bodies[stale]
        if self.refusal is not None and not (self.refusal.asked & live_ids):
            self.refusal = None
        if self.editing is not None and self.editing not in live_ids:
            self.editing = None
            self.edit_error = None


def head_lines(text: str, limit: int) -> tuple[str, int]:
    """
    The first ``limit`` lines of a text, and how many follow them.

    For text that is read from the top. The count is what a pane says it left
    unset, and what holds an approve when the text is a parked call's body.
    """
    if text.count("\n") < limit:
        return text, 0
    lines = text.split("\n")
    return "\n".join(lines[:limit]), len(lines) - limit


def unset_lines(state: ReviewState, pending_id: str) -> int | None:
    """
    How much of this call's body was missing when it was last on screen: ``None``
    when no pane has drawn it, zero when every pane showing it had all of it.

    The most any pane read, among the panes that drew it in the newest frame any of
    them did or the one before. Two panes show one call in views of their own, and
    one being whole does not make the other so; a pane that has stopped drawing
    the call -- its tab is behind another, the layout changed -- has a reading
    that says nothing about what is on screen now, and is passed over. The frame
    before counts because a click is settled part-way through a frame, after one
    pane has drawn and before the other has.
    """
    seen = state.bodies.get(pending_id)
    if not seen:
        return None
    newest = max(frame for frame, _ in seen.values())
    return max(unbuilt for frame, unbuilt in seen.values() if frame >= newest - 1)


def failed_panes(state: ReviewState) -> tuple[str, ...]:
    """
    The panes showing parked calls whose last draw raised, among those drawn in the
    newest frame any of them was or the one before.

    The same window ``unset_lines`` reads a body by, and for its reasons: the keys
    are read before any pane draws, a click is settled part-way through a frame,
    and a pane that is no longer drawn -- its tab went behind another while it was
    failing -- says nothing about what is on screen now.
    """
    if not state.panes:
        return ()
    newest = max(frame for frame, _ in state.panes.values())
    return tuple(
        pane
        for pane, (frame, whole) in sorted(state.panes.items())
        if not whole and frame >= newest - 1
    )


def queue_on_screen(state: ReviewState, frame: int) -> bool:
    """
    Whether NEEDS YOU, the queue whose rows act, was drawn in ``frame`` or the
    one before: what decides that DETAIL is the place to act (``detail.draw``).

    **In each layout there is exactly one place to act on a parked item**
    (planning/2026-10-15-ui-rework.md, "DETAIL acts in FOCUS only"). Read off
    the queue's own last draw (``ReviewState.panes``, ``app._reviewing``) and
    not off the layout's name: FOCUS docks no NEEDS YOU, so there DETAIL acts,
    but the operator can open NEEDS YOU in FOCUS or close it in TRIAGE, and the
    name would then say one place where there were two, or none. The frame
    before counts because the panes draw in an order of the runner's: DETAIL
    drawn ahead of the queue in a frame sees the queue's reading of the last.
    """
    drew = state.panes.get(INBOX)
    return drew is not None and drew[0] >= frame - 1


def withheld(state: ReviewState, pending: PendingApproval, *, unread: bool = False) -> str | None:
    """
    Why this call may not be approved yet, or ``None`` when it may. Every approve
    path asks here and nowhere else.

    **A pane that shows parked calls failed to draw** (``failed_panes``), and then
    nothing is approved, this call or another, read or not. What such a pane had
    on screen when it raised is not known: a reading it made before raising is of
    a body that may have been drawn over by the failure notice or cut off under
    it, a call further down its queue was not drawn at all, and the batch forms
    count on a list that was not finished. So it is not asked which call the
    failure was under.

    Two things can stand between a call and its approver's eyes. **Its body was
    never drawn**: the keys are read before the panes draw, so on the frame the
    cursor lands on a row there is no reading for it, and "no reading" must not
    mean "nothing missing". **Its body was drawn and is not on screen**: a pane
    sets a body past ``SETTLED_LINES`` only that far until it is asked for the
    rest, and the typesetting library can stop inside a draw, when the pane
    shows a sentence where the text would be. So a call is approved only once
    every pane showing it had all of its body on screen the last time it drew it.

    ``unread`` is the batch forms' question. They approve calls nobody opened, by
    design and behind a count, so for them a body never drawn is not a reason; a
    body known not to have been on screen still is.
    """
    if failed_panes(state):
        return PANE_FAILED
    unset = unset_lines(state, pending.id)
    if unset is None:
        return None if unread else NOT_DRAWN
    return NOT_WHOLE if unset > 0 else None


def refusal_for(state: ReviewState, pending_id: str) -> str | None:
    """
    What to say on this call's row about an approve that was not sent, if one was.
    """
    refusal = state.refusal
    return refusal.text if refusal is not None and pending_id in refusal.asked else None


def _approve_batch(bridge: Bridge, state: ReviewState, batch: list[PendingApproval]) -> int:
    """
    Send all of ``batch`` or none of it. Returns how many were sent.

    None, when any body in it is known not to have been on screen: a batch that
    went out without that one call would leave the operator believing the agent's
    queue was cleared, and the refusal names the calls that held it. None, too,
    while a pane that shows parked calls is failing to draw.
    """
    if batch and failed_panes(state):
        state.refusal = Refusal(frozenset(p.id for p in batch), PANE_FAILED)
        return 0
    short = [p for p in batch if withheld(state, p, unread=True) is not None]
    if short:
        names = "; ".join(p.summary for p in short)
        state.refusal = Refusal(
            frozenset(p.id for p in batch),
            f"none sent - {len(short)} of these {len(batch)} calls had a body that was not "
            f"all on screen when approve all was asked for ({names}); open it, then ask again",
        )
        return 0
    return sum(resolve(bridge, state, p, approved=True, unread=True) for p in batch)


def approve_all_for_node(bridge: Bridge, snap: Snapshot, state: ReviewState, node: NodeId) -> int:
    """
    Approve every pending call from one agent.

    Scoped batching is defensible in a way the global form is not: one agent's
    queue is usually one coherent piece of work, and the operator has just been
    reading its diffs. Returns how many were sent.
    """
    return _approve_batch(bridge, state, [p for p in snap.approvals if p.node == node])


def approve_everything(bridge: Bridge, snap: Snapshot, state: ReviewState) -> int:
    """
    Approve the entire queue across every agent. Only reachable after a confirm.

    This is in genuine tension with the premise -- "nothing is written until a human
    approves it" and "approve twelve things with one key" do not fully coexist. It
    exists because being the bottleneck for a fleet defeats the tool, and it is
    gated behind an explicit confirmation showing the count because the failure mode
    is silent and unrecoverable.
    """
    sent = _approve_batch(bridge, state, list(snap.approvals))
    state.confirming_global = False
    return sent


def resolve(
    bridge: Bridge,
    state: ReviewState,
    pending: PendingApproval,
    *,
    approved: bool,
    unread: bool = False,
) -> bool:
    """
    Send the decision, and answer whether it was sent. The store is not touched
    here -- the gate emits ApprovalResolved once the agent has actually been
    released, so the queue never shows an approval the agent did not receive.

    An approval is not sent while ``withheld`` has a reason, and the reason is left
    in ``state.refusal`` for the panes to say. A rejection is always sent: refusing
    a call does not need its body whole.
    """
    if approved:
        reason = withheld(state, pending, unread=unread)
        if reason is not None:
            state.refusal = Refusal(frozenset({pending.id}), reason)
            return False
    edited = None
    if approved and pending.id in state.edits:
        parsed = _parse_edit(state.edits[pending.id])
        if parsed is None:
            state.edit_error = "arguments are not valid JSON - not sent"
            return False
        edited = parsed
    bridge.resolve(
        pending.id,
        Decision(
            approved=approved,
            reason=state.reasons.get(pending.id) or None,
            edited_args=edited,
        ),
    )
    state.edit_error = None
    state.refusal = None
    return True


def _parse_edit(text: str) -> dict[str, object] | None:
    import json

    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def handle_keys(
    snap: Snapshot,
    focus: FocusState,
    state: ReviewState,
    bridge: Bridge,
    *,
    escape_spent: bool = False,
) -> None:
    """
    Keyboard shortcuts, active only when the keys cannot be somebody's prose.

    Two guards, and they answer different questions. ``want_capture_keyboard`` is
    this frame: a widget has the keyboard, so typing the letter 'a' into a
    rejection reason does not approve the thing being rejected. ``Hold`` is the
    frames after: a text box that had the keyboard may have been removed from
    under the typist, and the keyboard being free again says nothing about
    whether they have noticed.

    j/k walk the whole inbox, not just the approvals in it. Skipping over questions
    and failures would make the two obligation kinds that have no other surface
    unreachable from the keyboard, which is most of the way back to them having no
    surface at all. The action keys below then do nothing on a row that is not an
    approval, which is the right kind of nothing: the cursor is still where the
    operator put it, and the expanded row shows what it *can* do.

    The letters are bindings for the bare key only. ``want_capture_keyboard`` says
    whether an ImGui *item* has the keyboard, and a view that handles its own
    chords without submitting one -- select-all and copy over a diff -- leaves it
    false, so Ctrl+A would otherwise arrive here as ``a`` and approve what was
    being selected. Shift is not a chord in this sense: it is the difference
    between ``a`` and ``A``. Escape is exempt because cancelling is the safe
    direction and should not depend on which modifiers happen to be down.

    This runs before any pane draws, so ``a`` is answered from what the panes showed
    up to the frame before (``withheld``): a body that was not on screen, or one
    never drawn -- the row the cursor reached on this very frame -- is not approved.
    ``r`` and ``e`` ask nothing of the body.

    ``r`` asks for a reason, in the field on the inbox row, and the row's button
    sends it. Where the inbox failed to draw there is no field and no button, and
    a call that cannot be refused is an agent parked until its session is closed:
    there ``r`` sends the rejection itself, with whatever reason was already typed.

    ``escape_spent`` is an Escape another control took (``app._handle_keys``): it
    cancels nothing here, and an edit open under it keeps what was typed. It still
    counts as the gesture that ends a hold: the operator pressed it, and it shows
    they are no longer typing whichever control it was aimed at.
    """
    io = imgui.get_io()
    free = state.hold.step(text_input=bool(io.want_text_input), gesture=_stopped_typing())
    if not free or io.want_capture_keyboard:
        return
    chord = io.key_ctrl or io.key_alt or io.key_super

    def letter(key: imgui.Key) -> bool:
        return not chord and imgui.is_key_pressed(key)

    escape = not escape_spent and bool(imgui.is_key_pressed(imgui.Key.escape))

    if letter(imgui.Key.j):
        focus.move(snap, 1)
    if letter(imgui.Key.k):
        focus.move(snap, -1)

    if escape:
        state.editing = None
        state.confirming_global = False
        state.edit_error = None
        state.refusal = None

    current = focus.obligation(snap)
    if not isinstance(current, ApprovalNeeded):
        return
    pending = current.approval

    # The keys that open a text box come first and end the frame's shortcuts:
    # what is typed next is for the box, and an ``a`` that shares the frame with
    # one of them is the start of it (``Hold.begin``). ``r`` where the inbox failed
    # to draw opens no box -- it sends the rejection -- and ends them too.
    opened = False
    if letter(imgui.Key.r):
        if INBOX in failed_panes(state):
            resolve(bridge, state, pending, approved=False)
            return
        state.focus_reason = True
        opened = True
    if letter(imgui.Key.e):
        state.editing = pending.id
        state.edits.setdefault(pending.id, pretty_args(pending))
        opened = True
    if opened:
        state.hold.begin()
        return

    if letter(imgui.Key.a):
        # Shift+A is the batch form, scoped to the focused agent. Tested as one
        # branch rather than two ifs: an unguarded pair fires both, so Shift+A
        # would approve the focused call *and* everything beside it.
        if io.key_shift:
            approve_all_for_node(bridge, snap, state, pending.node)
        else:
            resolve(bridge, state, pending, approved=True)
    if escape:
        state.edits.pop(pending.id, None)


def _stopped_typing() -> bool:
    """Whether this frame carries Escape or a mouse press: see ``Hold``."""
    return bool(imgui.is_key_pressed(imgui.Key.escape)) or any(
        bool(imgui.is_mouse_clicked(button))
        for button in (imgui.MouseButton_.left, imgui.MouseButton_.right, imgui.MouseButton_.middle)
    )


def pretty_args(pending: PendingApproval) -> str:
    import json

    return json.dumps(dict(pending.raw_args), indent=2, sort_keys=True)


def shown_command(pending: PendingApproval) -> str | None:
    """
    A Bash call's command as every approval surface draws it, or ``None`` for a
    call that has none: ``approval.command_shown`` of the command as the agent
    sent it, which is what the queue row's summary is made from.

    Of ``raw_args`` and not of the drawable twin, and the one reader of it that
    draws. ``commandtext.visible`` emits printable ASCII and newline only, so
    what it returns is drawable whatever the command held. Made from the twin
    it would escape twice: a lone surrogate the twin spells ``\\udc80`` would be
    drawn ``\\\\udc80``, which is how the row spells a backslash the agent typed,
    and two surfaces would show one string for two commands.
    """
    return command_shown(pending.tool_name, "command", pending.raw_args.get("command"))
