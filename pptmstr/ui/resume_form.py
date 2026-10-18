"""
Resuming a closed card's session: a short form of its own, not the New Task modal
with fields filled in.

**Opened from a card and from nowhere else**: its menu's Resume…, the key for
that, and the gate menu's "Resume as …" and "Continue as …". A session picked in
New Task's session list is resumed there, by New Task, with New Task's fields
(``launcher.LauncherState.resumed``).

A resume differs from a launch in what is already settled. The session, the
directory it runs in and (where a record of it exists) its team are facts about a
conversation that has happened, and a form that let them be edited would be offering
a different session under the same id. So they are drawn as facts and no method
here writes them; what the operator chooses is the message, the gate, the model and
the sub-agent cap.

**Every check is the launcher's, asked through the launcher's functions.** Where the
session runs is ``launcher.launch_directory``'s answer, whether the top rung may be
used there is ``launcher.refusal_for``'s, and whether this machine's CLI can contain
it is ``launcher.VersionGate``'s. Whether a rung may run contained given those
answers is ``launcher.resume_gate_hold``'s, and the spec is
``launcher.resume_spec``'s: the two New Task's own resume goes through, so a
resume is held or granted by one rule whichever surface pressed it. **A rung the
directory is refused for holds the resume instead of downgrading it**, which is
where a resume differs from a new session; ``resume_gate_hold`` says why.

What this module owns is the form: the facts drawn as facts, and one hold New Task
does not have. A session that recorded no directory is not resumed from here,
because this form has no field to say where; New Task falls back to its own.

**The message does not become the session's title.** ``LaunchSpec.task`` is what
``announce`` puts on the record, so a resume that carried "ok, carry on" there
would retitle the card and make Fork and Retry relaunch with it. ``press``
therefore returns the spec with ``task`` holding the session's own task and the
typed text in ``LaunchSpec.message``, which ``AgentSession.run`` sends in place of
the task. Where no task is recorded for the session the title is whatever the
card already reads -- the CLI's label for the conversation -- and the spec says it
is one (``LaunchSpec.task_is_label``), so the card keeps its title and Fork and
Retry go on refusing to start a session from it. The message is never the title.

**Nothing here ends a running session.** A resume of a session that is live in this
window retires the live run, and that is ``SessionPool.submit``'s act, taken when it
accepts the successor and after every refusal ahead of it has passed. The form says
so before the press and does nothing about it.

``ResumeForms``, ``ResumeForm`` and ``press`` draw nothing and are tested without a
window; imgui is imported for ``draw``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import assert_never

from imgui_bundle import imgui

from .. import cli_version, templates
from ..approval import Policy, requires_containment
from ..model import LaunchSpec
from ..model_catalog import DEFAULT_MODEL, MODELS
from ..sessionfile import UnknownPolicy
from ..sessions import SessionRow
from . import launcher, widgets
from .launcher import (
    FieldDirectory,
    GoneDirectory,
    LaunchDirectory,
    RecordedDirectory,
    RedirectedDirectory,
    UnrecordedDirectory,
    VersionGate,
)
from .widgets import Help, Notice, Tone

__all__ = [
    "TITLE",
    "Draft",
    "Facts",
    "Failed",
    "Hold",
    "Launch",
    "Reading",
    "ResumeForm",
    "ResumeForms",
    "UnknownTeam",
    "directory_line",
    "directory_notice",
    "draw",
    "failure_notice",
    "gate_notice",
    "press",
    "task_fact",
]

TITLE = "Resume session"

_WIDTH = widgets.DIALOG_WIDTH
_MESSAGE_HEIGHT = 120.0
_LABEL_COLUMN = widgets.DIALOG_LABEL_COLUMN
# The least the body is given on a display too short for it: the message box and
# its caption.
_BODY_FLOOR = _MESSAGE_HEIGHT + 40.0
# The scrolling child everything above the buttons is drawn in.
BODY_ID = "##body"

_MESSAGE_ID = "##message"

HOLD_EMPTY = "type the message this session is resumed with"
HOLD_GONE = "this session's directory is gone -- restore it to resume"
# Says the one other way in: New Task resumes a row of its session list where the
# CLI's own listing says the session ran, and in its working directory field for a
# row that says nowhere (``launcher.launch_directory``).
HOLD_UNRECORDED = (
    "this session recorded no directory, and a resume is not started in a guessed one. "
    "New Task's session list can resume it: picked there it runs where the CLI ran it, "
    "or in the working directory typed there if the CLI recorded none"
)
HOLD_MOVED = (
    "what this form showed about the directory is not what was found at the press -- "
    "read it again before resuming"
)
HOLD_UNREAD = "the directory has not been read yet"
# The three ``launcher.resume_gate_hold`` chooses between, under the names this
# form's callers know them by. ``HOLD_REFUSED`` has a ``{rung}`` slot.
HOLD_CHECKING = launcher.RESUME_HOLD_CHECKING
HOLD_CLI = launcher.RESUME_HOLD_CLI
HOLD_REFUSED = launcher.RESUME_HOLD_REFUSED

# The line beside a held button for each of this form's own reasons; the
# launcher's are ``launcher.HOLD_LINES``. See ``launcher.hold_notice``.
HOLD_LINES: dict[str, str] = {
    HOLD_EMPTY: "held: type a message first",
    HOLD_GONE: "held: its directory is gone",
    HOLD_UNRECORDED: "held: no directory was recorded",
    HOLD_MOVED: "held: the directory changed",
    HOLD_UNREAD: "held: the directory is not read yet",
}


@dataclass(frozen=True, slots=True)
class UnknownTeam:
    """
    Nothing records which team this session ran as: it has no session file, which
    is every session older than the build that writes one. Its own type, because
    ``None`` already means solo on a ``LaunchSpec`` and "solo" is a claim.
    """


@dataclass(frozen=True, slots=True)
class Facts:
    """
    What a resume does not get to choose, as the caller read it.

    Plain values, so the caller can fill them from a ``sessionfile.SessionFile`` or
    from a live ``AgentRecord``. ``cwd`` is the directory as recorded, empty when
    none was. ``task`` is the card's title: the session's own task, or, with
    ``task_is_label`` set, the CLI's label for a conversation whose task nothing
    recorded. Empty when there is not even a label. ``policy`` and ``contained``
    are the gate the session last ran under, which is shown and is never the
    form's default. ``contained`` must be the
    driver guard's notion of it (``AgentSession._unpaired``: a settings string with
    something in it), not the truth of whatever string was stored.
    """

    session_id: str
    cwd: str
    task: str
    template: str | None | UnknownTeam
    policy: Policy | UnknownPolicy
    contained: bool
    model: str
    subagent_cap: int | None
    brief: str | None
    last_message: str | None
    task_is_label: bool = False

    @property
    def title(self) -> str:
        """
        What the session is announced under: the card's title as it stands, or
        the session's id where it has none, which is the last resort the
        session list's own label falls back to (``SessionRow.label``).
        """
        return self.task.strip() or self.session_id

    @property
    def title_is_label(self) -> bool:
        """Whether ``title`` is no task: said by the caller, or there was none."""
        return self.task_is_label or not self.task.strip()


@dataclass(frozen=True, slots=True)
class Draft:
    """
    What the operator had typed and chosen on one session's form. Travels beside a
    launch so a resume that starts nothing can hand it back; see
    ``ResumeForms.report_failure``.
    """

    message: str
    policy: Policy
    model: str
    subagent_cap: int | None
    template_index: int


@dataclass(frozen=True, slots=True)
class Failed:
    """A resume that was pressed and started nothing: its draft, and why."""

    draft: Draft
    reason: str


@dataclass(frozen=True, slots=True)
class Reading:
    """
    One look at where the session runs: the directory, and the own-checkout
    refusal asked of it. ``refusal`` is ``launcher.refusal_for``'s answer and is
    None for a directory nothing launches in, where the question was not asked.
    """

    directory: LaunchDirectory
    refusal: str | None


@dataclass(frozen=True, slots=True)
class Launch:
    """
    A press that may start: the spec, the message, and the draft to hand back.

    ``spec.task`` is the session's title as it stood and ``spec.message`` is what
    the operator typed, which is what the session is sent. ``message`` is the same
    text, for a caller that reads the press without reading the spec.
    """

    spec: LaunchSpec
    message: str
    draft: Draft


@dataclass(frozen=True, slots=True)
class Hold:
    """A press that starts nothing, and the reason in the words drawn under the buttons."""

    reason: str


def _row(facts: Facts) -> SessionRow:
    """
    ``facts`` as the row ``launcher.launch_directory`` and ``live_resume_line`` read.
    Only the id and the directory are read from it.
    """
    return SessionRow(
        session_id=facts.session_id,
        cwd=facts.cwd or None,
        git_branch=None,
        summary=facts.task,
        first_prompt=None,
        created_at=None,
        last_modified=0,
        tag=None,
    )


def _launches(directory: LaunchDirectory) -> bool:
    """
    Whether a resume starts in ``directory`` at all. A recorded directory that is
    gone does not, as on the launcher. Neither does one that was never recorded:
    the launcher falls back to its cwd field there, and this form has no field.
    """
    match directory:
        case RecordedDirectory() | RedirectedDirectory():
            return True
        case GoneDirectory() | UnrecordedDirectory() | FieldDirectory():
            return False
        case _:
            assert_never(directory)


def read(facts: Facts, *, checkout: str = launcher.OWN_CHECKOUT) -> Reading:
    """
    Look at the disk now: where the session runs, and whether the own-checkout
    question refuses it. Both from one resolution, as ``LauncherState.spec`` takes
    them, so the refusal is about the directory the spec is started in.
    """
    directory = launcher.launch_directory(_row(facts), "")
    if not _launches(directory):
        return Reading(directory, None)
    return Reading(
        directory,
        launcher.refusal_for(
            launcher.resolved_directory(directory),
            checkout=checkout,
            named="this session's directory",
            # A resume the rung is refused for is held and never seated one rung
            # down (``launcher.resume_gate_hold``), and the refusal says so.
            consequence=launcher.REFUSAL_HOLDS,
        ),
    )


def _recovered_brief(facts: Facts) -> str | None:
    """
    The session's brief directory where the caller knew none: the canonical one for
    its id and directory, if it exists. ``SessionPicker.pick``'s derivation, by
    calling it. Without this a team resumed from a session with no file would have
    ``app._seed_brief`` append to that same directory as though it were new.
    """
    if not facts.cwd:
        return None
    picker = launcher.SessionPicker()
    picker.pick(_row(facts), "")
    return picker.recovered_brief


@dataclass
class ResumeForm:
    """
    One session's resume form: its facts, and what the operator has typed and chosen.

    The draft survives a cancel, a held press and a redraw because it lives here and
    nothing but ``launched`` clears it; and a launch that turns out to have started
    nothing hands it back through ``ResumeForms.report_failure``.
    """

    _facts: Facts
    message: str
    policy: Policy
    model: str
    subagent_cap: int | None
    # Index into ``templates.names()``. Read only while the team is not known.
    template_index: int = 0
    brief: str | None = None
    # What the last draw was drawn from. ``press`` compares its own reading with it.
    shown: Reading | None = None
    failures: list[Failed] = field(default_factory=list)
    # What a restore displaced, newest last: text the operator typed is never the
    # price of getting a failed resume's text back.
    set_aside: list[Draft] = field(default_factory=list)
    # What the message box was filled with, so "has the operator typed" is a
    # comparison and not a flag somebody has to remember to set.
    _prefill: str = ""
    _git_layout_for: str | None = None
    _git_layout: launcher.GitLayout = None
    # Set when the message is written from outside the widget; see ``draw``.
    _reload: bool = False

    @property
    def facts(self) -> Facts:
        """The facts this form was opened with. There is no setter."""
        return self._facts

    @property
    def touched(self) -> bool:
        """Whether the box holds something other than what it was filled with."""
        return self.message != self._prefill

    @property
    def ready(self) -> bool:
        return bool(self.message.strip())

    @property
    def team_known(self) -> bool:
        return not isinstance(self._facts.template, UnknownTeam)

    @property
    def template(self) -> str | None:
        """
        The team the resume runs as: the recorded one, or the operator's choice
        where none is recorded.
        """
        recorded = self._facts.template
        if isinstance(recorded, UnknownTeam):
            return templates.names()[self.template_index]
        return recorded

    def draft(self) -> Draft:
        return Draft(
            message=self.message,
            policy=self.policy,
            model=self.model,
            subagent_cap=self.subagent_cap,
            template_index=self.template_index,
        )

    def refresh(self, *, checkout: str = launcher.OWN_CHECKOUT) -> Reading:
        """
        What the screen says about the directory, for this frame.

        The directory is read every frame, an ``lstat`` per component, so a path
        removed or relinked while the form is up is on screen at the next one. The
        refusal is asked again only when that reading changed.
        """
        directory = launcher.launch_directory(_row(self._facts), "")
        if self.shown is None or self.shown.directory != directory:
            self.shown = read(self._facts, checkout=checkout)
        return self.shown

    def git_layout(self, directory: LaunchDirectory) -> launcher.GitLayout:
        """``launcher.git_layout`` of the directory, one read per path per open."""
        if directory.path != self._git_layout_for:
            self._git_layout = launcher.git_layout(directory.path)
            self._git_layout_for = directory.path
        return self._git_layout

    def hold(self, reading: Reading, gate: VersionGate) -> str | None:
        """
        Why a resume under ``reading`` is held, or None. The directory first,
        under every rung, and then whether the rung may run contained, which is
        ``launcher.resume_gate_hold``'s answer here as it is for a session picked
        in New Task.

        The launcher's thin-premise hold is not here. It asks whether the task box
        is a whole brief, and a resumed session's premises are its conversation.
        """
        match reading.directory:
            case GoneDirectory():
                return HOLD_GONE
            case UnrecordedDirectory() | FieldDirectory():
                return HOLD_UNRECORDED
            case RecordedDirectory() | RedirectedDirectory():
                pass
            case _:
                assert_never(reading.directory)
        return launcher.resume_gate_hold(self.policy, reading.refusal, gate)

    @property
    def failure_loaded(self) -> bool:
        """Whether the oldest failed resume's draft is what the fields hold now."""
        return bool(self.failures) and self.failures[0].draft == self.draft()

    def launched(self) -> None:
        """
        The press's clears. The message has gone somewhere, and the rung goes back
        to strict so it does not outlive the resume it was chosen for. A press with
        a failed draft in the fields as it failed is that failure's retry.
        """
        if self.failure_loaded:
            del self.failures[0]
        self.message = ""
        self._prefill = ""
        self.policy = Policy.STRICT
        self._reload = True

    def _load(self, draft: Draft) -> None:
        self.message = draft.message
        self.policy = draft.policy
        self.model = draft.model
        self.subagent_cap = draft.subagent_cap
        self.template_index = draft.template_index
        self._reload = True

    def _set_aside(self, incoming: Draft) -> None:
        current = self.draft()
        if not self.touched or current == incoming:
            return
        if current in self.set_aside or any(f.draft == current for f in self.failures):
            return
        self.set_aside.append(current)

    def failed(self, draft: Draft, reason: str) -> None:
        """
        A resume pressed from this form started nothing. Its draft goes back into
        the fields at once when nothing has been typed since; otherwise what is
        there is the operator's, and the failure waits behind a notice.
        """
        self.failures.append(Failed(draft, reason))
        if not self.touched and self.failures[0].draft == draft:
            self._load(draft)

    def restore_failed(self) -> None:
        """Put the oldest failed resume's draft into the fields; what it displaces is set aside."""
        if not self.failures:
            return
        draft = self.failures[0].draft
        self._set_aside(draft)
        self._load(draft)

    def restore_set_aside(self) -> None:
        """Swap the fields with the draft most recently set aside."""
        if not self.set_aside:
            return
        draft = self.set_aside.pop()
        self._set_aside(draft)
        self._load(draft)

    def discard_set_aside(self) -> None:
        if self.set_aside:
            self.set_aside.pop()

    def discard_failed(self) -> None:
        """Drop the oldest failed resume. The fields are left as they are."""
        if self.failures:
            del self.failures[0]


@dataclass
class ResumeForms:
    """
    Every session's resume form, by session id, and which one is on screen.

    Keyed so that two sessions' drafts cannot mix: closing the form on one session
    and opening it on another shows the second its own text, and the first's is
    there when it is opened again.
    """

    forms: dict[str, ResumeForm] = field(default_factory=dict)
    showing: str | None = None
    is_open: bool = False
    # Read once per process, as the launcher's is, and put back to unanswered by a
    # failure for the launcher's reason: the launch path reads the version itself,
    # and its refusal must not be offered again under an answer of "fine".
    version_gate: VersionGate = field(default_factory=VersionGate)
    _open_requested: bool = False
    _focus: bool = False
    # How tall the buttons were on the last frame they were drawn, or zero before
    # the first. The body above them is bounded to what that leaves of the display.
    _footer_height: float = 0.0
    # Where the form is put while it opens and after: the launcher's placement.
    _placement: launcher.ModalPlacement = field(default_factory=launcher.ModalPlacement)

    def open_for(
        self,
        *,
        session_id: str,
        cwd: str,
        task: str,
        template: str | None | UnknownTeam,
        policy: Policy | UnknownPolicy,
        contained: bool,
        model: str,
        subagent_cap: int | None,
        brief: str | None,
        last_message: str | None,
        as_policy: Policy | None = None,
        task_is_label: bool = False,
    ) -> ResumeForm:
        """
        Show the form for a session, with the facts as the caller reads them now.

        **The gate is strict unless ``as_policy`` names another**, whatever the
        session last ran under: a rung is chosen for the resume in front of the
        operator, and ``as_policy`` is the gate menu's "Continue as ..." / "Resume
        as ...", which is that choice already made. This holds on a reopen too.

        **The message box is filled with ``last_message``** when there is one. On a
        reopen for the same session, text the operator typed is kept, and a box
        they never changed takes the new ``last_message``. Model, cap and a chosen
        team are kept as they were left.

        The brief is the caller's when it names one, and otherwise the session's
        canonical directory if that exists; see ``_recovered_brief``.
        """
        facts = Facts(
            session_id=session_id,
            cwd=cwd,
            task=task,
            template=template,
            policy=policy,
            contained=contained,
            model=model,
            subagent_cap=subagent_cap,
            brief=brief,
            last_message=last_message,
            task_is_label=task_is_label,
        )
        # The message is held as it was sent, and a box is a text call: what it
        # cannot take is written out, and what is sent is what the box shows.
        prefill = widgets.author_text(last_message or "")
        form = self.forms.get(session_id)
        if form is None:
            form = ResumeForm(
                _facts=facts,
                message=prefill,
                policy=Policy.STRICT,
                model=model or DEFAULT_MODEL,
                subagent_cap=subagent_cap,
            )
            self.forms[session_id] = form
        else:
            if not form.touched:
                form.message = prefill
            form._facts = facts
            form._reload = True
        form._prefill = prefill
        form.policy = Policy.STRICT if as_policy is None else as_policy
        form.brief = brief if brief is not None else _recovered_brief(facts)
        # Each open reads the disk again, for the reason the launcher's does.
        form.shown = None
        form._git_layout_for = None
        self.showing = session_id
        self._open_requested = True
        return form

    @property
    def form(self) -> ResumeForm | None:
        """The form on screen or asked for, or None."""
        return None if self.showing is None else self.forms.get(self.showing)

    @property
    def busy(self) -> bool:
        """Whether a form is on screen, or has been asked for and not drawn yet."""
        return self.is_open or self._open_requested

    def on_screen(self, session_id: str) -> bool:
        """Whether ``session_id``'s form is the one on screen or asked for."""
        return self.busy and self.showing == session_id

    def report_failure(
        self, session_id: str, draft: Draft, reason: str, *, show: bool = True
    ) -> bool:
        """
        A resume pressed from ``session_id``'s form started nothing. Called on the
        draw thread, on a frame after the press, with the draft ``Launch`` carried.

        The message goes back to that session's form: the reason and the text
        belong on the surface that resumes that session, and New Task would offer
        a fresh one. With ``show`` the form is asked for again, which is the
        telling. **Asking for it closes any other popup that is up** -- Dear ImGui
        holds one popup per level, and this one is opened at the root -- so a
        caller that knows something else is on screen passes ``show`` False and
        tells the operator another way (``app._reap_launches`` does, by a notice
        that waits). Another session's form is never displaced, whatever ``show``
        says: the failure is kept on its own form and the caller is the one who
        must say so.

        False when no form exists for the id, which is a resume this module did not
        make; the caller reports that one elsewhere.
        """
        form = self.forms.get(session_id)
        if form is None:
            return False
        form.failed(draft, reason)
        self.version_gate = VersionGate()
        if show and not self.busy:
            self.showing = session_id
            self._open_requested = True
        return True

    def close(self) -> None:
        """Take the form down. Its draft stays."""
        self.is_open = False
        self._open_requested = False


def press(
    form: ResumeForm, gate: VersionGate, *, checkout: str = launcher.OWN_CHECKOUT
) -> Launch | Hold:
    """
    What a press of resume does: start this, or hold for this reason.

    The disk is read here, afresh, and nothing remembered decides: the directory,
    its resolution and the own-checkout refusal are one reading, and the spec's
    directory, rung and containment all come from it. That reading is then compared
    with the one the form was last drawn from. If they differ the operator pressed
    under a description of some other resume, so nothing starts, the fields are
    left as they are, and the next frame draws what was found here.

    The message is the first thing asked, because it is the one hold with nothing
    to read from disk.
    """
    shown = form.shown
    fresh = read(form.facts, checkout=checkout)
    form.shown = fresh
    message = form.message.strip()
    if not message:
        return Hold(HOLD_EMPTY)
    held = form.hold(fresh, gate)
    if held is not None:
        return Hold(held)
    if shown is None:
        return Hold(HOLD_UNREAD)
    if fresh != shown:
        return Hold(HOLD_MOVED)
    facts = form.facts
    # The rung and the containment are a pair, and ``resume_spec`` is what pairs
    # them, from this press's reading. Past ``hold`` it has nothing left to refuse.
    spec = launcher.resume_spec(
        session_id=facts.session_id,
        cwd=launcher.resolved_directory(fresh.directory),
        policy=form.policy,
        refusal=fresh.refusal,
        gate=gate,
        # The title the card has, and whether it is only a label. Never the
        # message: the announce puts ``task`` on the record, and Fork and Retry
        # start a new session from what is there.
        task=facts.title,
        task_is_label=facts.title_is_label,
        message=message,
        model=form.model,
        template=form.template,
        brief=form.brief,
        subagent_cap=form.subagent_cap,
    )
    if isinstance(spec, launcher.ResumeHeld):
        return Hold(spec.reason)
    return Launch(spec, message, form.draft())


TITLE_IS_LABEL = (
    "its original task is not recorded: this is the CLI's label for the conversation, "
    "and the card keeps it as its title"
)


def task_fact(facts: Facts) -> tuple[str, str, str | None]:
    """
    The form's line for what the session is called: the row's label, its value,
    and a note under it or None. A title that is only a label is not drawn under
    the word "task", which would be the claim the form exists not to make.
    """
    if not facts.title_is_label:
        return "task", facts.task, None
    return "title", facts.task.strip() or "not known", TITLE_IS_LABEL


def directory_line(directory: LaunchDirectory) -> str:
    """
    What the form says about where the session runs, in the launcher's wording
    where the launcher's sentence is true of this form. Its sentences for a gone
    and for an unrecorded directory point at the cwd field and the picker, neither
    of which is here.
    """
    match directory:
        case RecordedDirectory(path=path) | RedirectedDirectory(path=path):
            line = launcher.resume_directory_line(directory, path)
            assert line is not None
            return line
        case GoneDirectory(path=path):
            return (
                f"{path} is where this session was recorded, and it is no longer a "
                "directory -- resume is held"
            )
        case UnrecordedDirectory() | FieldDirectory():
            return "this session recorded no directory -- resume is held"
        case _:
            assert_never(directory)


_FILLED_CAPTION = "your last message to it -- edit or replace.  Ctrl+Enter resumes"
_MESSAGE_CAPTION = "sent to the session as its next message.  Ctrl+Enter resumes"
_TEAM_UNKNOWN = (
    "not known -- this session has no record from this build, so the team is this "
    "resume's to choose"
)
_NO_THIN_HOLD = (
    "a one-line message is enough here: this session's premises are its conversation "
    "and its brief, so New Task's hold on a one-line task does not apply."
)
_BRIEFING = "the resumed session is given a briefing for the gate chosen here."

# Under the directory, where the session does not simply run where it was
# recorded. The sentence for each is behind the line's question mark.
RUNS_BY_LINK = "runs where a link leads, not where its recorded path reads"
RUNS_GONE = "its recorded directory is gone — the resume is held"
RUNS_UNRECORDED = "recorded no directory — the resume is held"

_FAILED_LOADED = "the message below is that resume, as it was typed"
_FAILED_KEPT = "its message is kept; restoring it sets what is below aside"


def _directory_help(directory: LaunchDirectory) -> Help:
    return Help("runs-in", "where this session runs", (directory_line(directory),))


def directory_notice(directory: LaunchDirectory) -> Notice | None:
    """
    The line under the directory, or None for a session that runs where it was
    recorded: that one's sentence is behind a question mark on the directory
    itself, and there is nothing to warn of.
    """
    explained = _directory_help(directory)
    match directory:
        case RecordedDirectory():
            return None
        case RedirectedDirectory():
            return Notice(RUNS_BY_LINK, Tone.WARN, explained)
        case GoneDirectory():
            return Notice(RUNS_GONE, Tone.DANGER, explained)
        case UnrecordedDirectory() | FieldDirectory():
            return Notice(RUNS_UNRECORDED, Tone.DANGER, explained)
        case _:
            assert_never(directory)


def failure_notice(failure: Failed, *, loaded: bool) -> Notice:
    """
    The oldest resume that started nothing, as one line with as much of the
    reason as the row holds; behind its question mark, the reason whole and what
    has become of its message.
    """
    said = f"that resume did not start: {failure.reason}"
    return Notice(
        said,
        Tone.DANGER,
        Help(
            "failure",
            "why that resume did not start",
            (said, _FAILED_LOADED if loaded else _FAILED_KEPT),
        ),
    )


def gate_notice(
    form: ResumeForm, reading: Reading, gate: VersionGate, subagent_cap: int | None
) -> Notice:
    """
    The one line under the dial and what is behind its question mark:
    ``launcher.gate_notice``, as a resume. A rung the directory is refused for
    holds the resume, and the line and the reason both say held.
    """
    needs = requires_containment(form.policy)
    launches = _launches(reading.directory)
    return launcher.gate_notice(
        form.policy,
        resuming=True,
        directory=reading.directory,
        refusal=reading.refusal if needs else None,
        gate=gate,
        layout=lambda: form.git_layout(reading.directory),
        subagent_cap=subagent_cap,
        launches=launches,
        also=(_BRIEFING, *((_NO_THIN_HOLD,) if needs and launches else ())),
    )


def _fact(label: str, value: str, explained: Help | None = None) -> None:
    """
    One settled thing about the session: its name in the label column and its
    value on the rest of the row, cut to fit, with a question mark after it when
    there is more to say: a link on the line's own baseline, so the line is set
    as text whether or not it ends in one.
    """
    imgui.text_disabled(label)
    imgui.same_line(_LABEL_COLUMN)
    room = imgui.get_content_region_avail().x
    if explained is not None:
        room -= widgets.help_mark_width()
    imgui.text(widgets.ellipsis(widgets.flat_line(value), room))
    if explained is not None:
        widgets.help_mark(explained.mark, explained.title, explained.paragraphs)


def _last_gate(facts: Facts) -> str:
    contained = "contained" if facts.contained else "not contained"
    match facts.policy:
        case Policy():
            return f"{facts.policy.value}, {contained}"
        case UnknownPolicy(stated=stated):
            return "not known" if stated is None else f"not known (recorded as {stated!r})"
        case _:
            assert_never(facts.policy)


def _facts_block(form: ResumeForm, reading: Reading, live: frozenset[str]) -> None:
    facts = form.facts
    _fact("session", facts.session_id)
    label, value, note = task_fact(facts)
    _fact(label, value, None if note is None else Help("title", "this session's title", (note,)))
    under = directory_notice(reading.directory)
    _fact(
        "directory",
        reading.directory.path or "not recorded",
        _directory_help(reading.directory) if under is None else None,
    )
    if under is not None:
        widgets.notice(under)
    if form.team_known:
        _fact("team", form.template or templates.names()[0])
    else:
        # The label on the combo's own baseline, as a fact's is on its value's.
        imgui.align_text_to_frame_padding()
        imgui.text_disabled("team")
        imgui.same_line(_LABEL_COLUMN)
        imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
        _, form.template_index = imgui.combo("##team", form.template_index, list(templates.names()))
        widgets.help_mark("team", "this session's team", (_TEAM_UNKNOWN,))
    _fact("brief", form.brief or "none")
    _fact("last gate", _last_gate(facts))
    live_line = launcher.live_notice(_row(facts), live)
    if live_line is not None:
        widgets.notice(live_line)


def _failure_notice(form: ResumeForm) -> None:
    if not form.failures and not form.set_aside:
        return
    if form.failures:
        loaded = form.failure_loaded
        widgets.notice(failure_notice(form.failures[0], loaded=loaded))
        if loaded:
            if widgets.small_button("dismiss this notice"):
                form.discard_failed()
        else:
            if widgets.small_button("restore that message"):
                form.restore_failed()
            imgui.same_line()
            if widgets.small_button("discard it"):
                form.discard_failed()
    if form.set_aside:
        widgets.notice(Notice(f"{len(form.set_aside)} message(s) set aside by a restore are kept"))
        if widgets.small_button("bring back what was set aside"):
            form.restore_set_aside()
        imgui.same_line()
        if widgets.small_button("discard what was set aside"):
            form.discard_set_aside()
    widgets.dialog_group()


def _gate_rows(
    form: ResumeForm,
    reading: Reading,
    gate: VersionGate,
    *,
    prober: launcher.Prober,
    subagent_cap: int | None,
) -> None:
    """
    The dial, and under it the one line ``gate_notice`` has for the chosen rung
    here.
    """
    rungs = launcher.POLICIES
    imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
    changed, picked = imgui.combo("gate", rungs.index(form.policy), [p.value for p in rungs])
    if changed:
        form.policy = rungs[picked]
    needs = requires_containment(form.policy)
    if needs and _launches(reading.directory) and reading.refusal is None:
        # Started from the draw and only under a rung that needs it, as on the
        # launcher, so a resume under the ordinary gate spawns no subprocess.
        gate.poll()
        gate.request(prober=prober)
    widgets.dialog_caption()
    widgets.notice(gate_notice(form, reading, gate, subagent_cap))


def draw(
    state: ResumeForms,
    *,
    launch: Callable[[Launch], None],
    wrap: bool,
    prober: launcher.Prober = cli_version.check_installed_cli,
    subagent_cap: int | None = None,
    models: tuple[str, ...] = MODELS,
    live: frozenset[str] = frozenset(),
    checkout: str = launcher.OWN_CHECKOUT,
) -> None:
    """
    Draw the form if it has been asked for. Root level, as the launcher's modal is.

    ``launch`` is handed a press that may start; ``app._resume_pressed`` gives
    its spec to ``app._launch``. On a later frame, and never from inside
    ``launch``, a resume that started nothing goes to
    ``ResumeForms.report_failure`` with the session id and ``Launch.draft``.

    ``subagent_cap`` is the operator's setting, ``models`` what the combo offers and
    ``live`` the ids of the sessions this process is running, each as
    ``launcher.draw`` takes them.

    Sized to what it holds, up to the display, as the launcher is: the facts and
    controls are a body that scrolls past that, and the buttons are a footer under
    it that does not.

    Under a control there is one line, and an explanation is behind a question
    mark, as in the launcher (``widgets.notice``, ``widgets.help_mark``).
    """
    form = state.form
    if form is None:
        state.is_open = False
        return
    if state._open_requested:
        state._open_requested = False
        state.is_open = True
        state._focus = True
        state._placement.begin()
        imgui.open_popup(TITLE)
    if not state.is_open:
        return

    # The launcher's scope, from before the window begins -- which is when it reads
    # its padding -- to after it ends.
    with widgets.dialog_style():
        _modal(
            state,
            form,
            launch=launch,
            wrap=wrap,
            prober=prober,
            subagent_cap=subagent_cap,
            models=models,
            live=live,
            checkout=checkout,
        )


def _modal(
    state: ResumeForms,
    form: ResumeForm,
    *,
    launch: Callable[[Launch], None],
    wrap: bool,
    prober: launcher.Prober,
    subagent_cap: int | None,
    models: tuple[str, ...],
    live: frozenset[str],
    checkout: str,
) -> None:
    """
    The open form, begun and ended here. ``draw`` documents the arguments.

    The groups, each set off from the next by ``widgets.dialog_group``: what is
    settled about the session, the message, the gate, and what runs it; the buttons
    are the footer.
    """
    # Before anything is drawn: the Escape that closes an explanation is not also
    # this form's (``widgets.help_showing``).
    explaining = widgets.help_showing()
    # The launcher's area, placement and window, for its reasons: the work area less
    # the status bar, centred until its size settles and then the operator's to move,
    # one width and a height that fits what is drawn up to the area's.
    area_pos, area_size = launcher.modal_area()
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(_WIDTH, 0.0), imgui.ImVec2(_WIDTH, area_size[1])
    )
    state._placement.before(TITLE, area_pos, area_size)
    opened, _ = imgui.begin_popup_modal(TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        state.is_open = False
        return
    state._placement.after()

    reading = form.refresh(checkout=checkout)
    gate = state.version_gate
    # Two sessions' forms are one window, and a text box is identified by its
    # label: under the session's id each has its own, with its own edit state.
    imgui.push_id(form.facts.session_id)

    widgets.begin_dialog_body(BODY_ID, state._footer_height, _BODY_FLOOR, area_size[1])
    imgui.push_text_wrap_pos(0.0)

    _failure_notice(form)
    _facts_block(form, reading, live)

    widgets.dialog_group()

    if state._focus:
        imgui.set_keyboard_focus_here()
        state._focus = False
    # An active InputText keeps its own copy of the text and writes it over the
    # caller's every frame, so a message written from outside it -- a failed
    # resume's, handed back -- has to be read by the widget. Only while it is the
    # active item: asking an inactive box to reload trips an assertion in its
    # word-wrapped draw. ``launcher.draw`` measured both halves.
    if form._reload:
        form._reload = False
        box = imgui.get_id(_MESSAGE_ID)
        if imgui.internal.get_active_id() == box:
            held_text = imgui.internal.get_input_text_state(box)
            if held_text is not None:
                held_text.reload_user_buf_and_move_to_end()
    submitted, form.message = widgets.multiline_input(
        _MESSAGE_ID,
        form.message,
        imgui.ImVec2(-1.0, _MESSAGE_HEIGHT),
        wrap=wrap,
        flags=widgets.CTRL_ENTER_SUBMITS,
    )
    filled = form.facts.last_message is not None and not form.touched
    widgets.dialog_caption()
    widgets.notice(Notice(_FILLED_CAPTION if filled else _MESSAGE_CAPTION))

    effective = subagent_cap if form.subagent_cap is None else form.subagent_cap
    widgets.dialog_group()
    _gate_rows(form, reading, gate, prober=prober, subagent_cap=effective)

    widgets.dialog_group()
    imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
    options = list(models)
    if form.model not in options:
        options.insert(0, form.model)
    changed, chosen = imgui.combo("model", options.index(form.model), options)
    if changed:
        form.model = options[chosen]

    if effective is not None:
        imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
        changed, typed = imgui.input_int("sub-agents", effective)
        if changed:
            form.subagent_cap = max(0, typed)

    imgui.pop_text_wrap_pos()
    imgui.end_child()

    # The footer: items of the window itself, under the body and outside its scroll.
    footer_top = imgui.get_cursor_pos().y
    widgets.dialog_group()
    held = form.hold(reading, gate)
    enabled = form.ready and held is None
    if not enabled:
        imgui.begin_disabled()
    clicked = imgui.button("resume", imgui.ImVec2(110.0, 0.0))
    if not enabled:
        imgui.end_disabled()
    imgui.same_line()
    cancelled = imgui.button("cancel", imgui.ImVec2(90.0, 0.0))

    # Not while an explanation is up: that Escape closes the explanation.
    dismissed = cancelled or (not explaining and imgui.is_key_pressed(imgui.Key.escape))
    # ``enabled`` and not the click alone: a disabled button does nothing to the
    # keyboard, and Ctrl+Enter in the box is a second way in.
    if enabled and (clicked or submitted):
        outcome = press(form, gate, checkout=checkout)
        match outcome:
            case Launch():
                launch(outcome)
                form.launched()
                dismissed = True
            case Hold(reason=reason):
                held = reason
            case _:
                assert_never(outcome)
    # Beside the buttons, on their row, so a reason appearing moves nothing.
    if held is not None:
        imgui.same_line()
        imgui.align_text_to_frame_padding()
        widgets.notice(launcher.hold_notice(held, what="the resume", lines=HOLD_LINES))

    state._footer_height = imgui.get_cursor_pos().y - footer_top
    imgui.pop_id()
    if dismissed:
        state.close()
        imgui.close_current_popup()
    imgui.end_popup()
