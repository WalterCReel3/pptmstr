"""
The resume form: what ``press`` will and will not start, what the form may not
change, and what it must not lose.

The decisions are exercised without a window. The last section drives ``draw``
against Dear ImGui itself, a context with a display size and no backend, for the
things only a widget can show: typed text reaching the state, and where the buttons
are.
"""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui as real_imgui

from pptmstr import brief as brief_mod
from pptmstr import cli_version, sandbox, templates
from pptmstr.approval import Policy
from pptmstr.model_catalog import DEFAULT_MODEL
from pptmstr.sessionfile import UnknownPolicy
from pptmstr.ui import gate_menu, launcher, resume_form, widgets
from pptmstr.ui.resume_form import Hold, Launch, ResumeForm, ResumeForms, UnknownTeam, press

SESSION = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"
TASK = "rebuild the parser\nand say why"


def _meets() -> cli_version.FloorCheck:
    return cli_version.MeetsFloor("2.1.300", (2, 1, 300), cli_version.SANDBOX_FLOOR)


def _below() -> cli_version.FloorCheck:
    return cli_version.BelowFloor("2.1.9", (2, 1, 9), cli_version.SANDBOX_FLOOR)


def _answered(prober: launcher.Prober = _meets) -> launcher.VersionGate:
    gate = launcher.VersionGate()
    worker = gate.request(prober=prober)
    assert worker is not None
    worker.join()
    gate.poll()
    return gate


@pytest.fixture
def own(tmp_path: Path) -> str:
    """A directory standing in for pptmstr's own checkout."""
    checkout = tmp_path / "own"
    (checkout / "pptmstr").mkdir(parents=True)
    return str(checkout)


@pytest.fixture
def work(tmp_path: Path) -> Path:
    """The directory the session was recorded in, outside ``own``."""
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


def _open(
    forms: ResumeForms,
    cwd: Path | str,
    *,
    session_id: str = SESSION,
    as_policy: Policy | None = None,
    **over: object,
) -> ResumeForm:
    facts: dict[str, object] = {
        "cwd": str(cwd),
        "task": TASK,
        "template": "review",
        "policy": Policy.AUTONOMOUS,
        "contained": True,
        "model": "claude-test-model",
        "subagent_cap": 3,
        "brief": "/briefs/existing",
        "last_message": "carry on with the lexer",
    }
    facts.update(over)
    return forms.open_for(session_id=session_id, as_policy=as_policy, **facts)  # type: ignore[arg-type]


def _shown(form: ResumeForm, own: str) -> ResumeForm:
    """As after one frame of ``draw``: the form has been drawn from a reading."""
    form.refresh(checkout=own)
    return form


# -- opening ---------------------------------------------------------------------------


def test_the_gate_opens_strict_whatever_the_session_last_ran_under(work: Path) -> None:
    form = _open(ResumeForms(), work, policy=Policy.AUTONOMOUS, contained=True)
    assert form.policy is Policy.STRICT
    assert form.facts.policy is Policy.AUTONOMOUS


def test_a_gate_nobody_recorded_is_shown_as_unknown_and_never_acted_on(work: Path) -> None:
    form = _open(ResumeForms(), work, policy=UnknownPolicy("yolo"), contained=False)
    assert form.policy is Policy.STRICT
    assert resume_form._last_gate(form.facts) == "not known (recorded as 'yolo')"
    assert resume_form._last_gate(_open(ResumeForms(), work).facts) == "autonomous, contained"


def test_continue_as_opens_under_the_rung_that_was_chosen(work: Path) -> None:
    form = _open(ResumeForms(), work, as_policy=Policy.AUTONOMOUS, policy=Policy.STRICT)
    assert form.policy is Policy.AUTONOMOUS


def test_a_rung_chosen_once_does_not_come_back_on_a_plain_reopen(work: Path) -> None:
    forms = ResumeForms()
    _open(forms, work, as_policy=Policy.AUTONOMOUS)
    forms.close()
    assert _open(forms, work).policy is Policy.STRICT


def test_the_box_is_filled_with_the_last_message_and_empty_when_there_is_none(
    work: Path,
) -> None:
    forms = ResumeForms()
    assert _open(forms, work).message == "carry on with the lexer"
    older = _open(forms, work, session_id=OTHER, last_message=None)
    assert older.message == ""
    assert not older.ready


def test_reopening_keeps_what_was_typed_over_the_prefill(work: Path) -> None:
    forms = ResumeForms()
    form = _open(forms, work)
    form.message = "no -- start with the grammar"
    forms.close()
    again = _open(forms, work, last_message="something sent from the composer since")
    assert again is form
    assert again.message == "no -- start with the grammar"


def test_a_box_nobody_changed_takes_the_newer_last_message(work: Path) -> None:
    forms = ResumeForms()
    _open(forms, work)
    forms.close()
    again = _open(forms, work, last_message="something sent from the composer since")
    assert again.message == "something sent from the composer since"
    assert not again.touched


def test_two_sessions_drafts_do_not_mix(work: Path) -> None:
    forms = ResumeForms()
    first = _open(forms, work)
    first.message = "for the first"
    second = _open(forms, work, session_id=OTHER, last_message=None)
    second.message = "for the second"
    assert forms.showing == OTHER
    assert _open(forms, work).message == "for the first"
    assert _open(forms, work, session_id=OTHER, last_message=None).message == "for the second"


def test_an_unrecorded_model_falls_back_to_the_default(work: Path) -> None:
    assert _open(ResumeForms(), work, model="").model == DEFAULT_MODEL


# -- facts -----------------------------------------------------------------------------


def test_no_method_of_the_form_changes_a_fact(work: Path, own: str) -> None:
    forms = ResumeForms()
    form = _shown(_open(forms, work, as_policy=Policy.AUTONOMOUS), own)
    before = form.facts
    outcome = press(form, _answered(), checkout=own)
    assert isinstance(outcome, Launch)
    form.launched()
    forms.report_failure(SESSION, outcome.draft, "the CLI refused")
    form.message = "typed since"
    forms.report_failure(SESSION, outcome.draft, "again")
    form.restore_failed()
    form.restore_set_aside()
    form.discard_set_aside()
    form.discard_failed()
    form.refresh(checkout=own)
    form.hold(form.refresh(checkout=own), launcher.VersionGate())
    form.git_layout(form.refresh(checkout=own).directory)
    assert form.facts is before


def test_the_facts_have_no_setter_and_cannot_be_edited_in_place(work: Path) -> None:
    form = _open(ResumeForms(), work)
    with pytest.raises(AttributeError):
        form.facts = dataclasses.replace(form.facts, cwd="/elsewhere")  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        form.facts.cwd = "/elsewhere"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        form.facts.session_id = OTHER  # type: ignore[misc]


def test_a_known_team_is_not_the_combos_to_change(work: Path) -> None:
    form = _open(ResumeForms(), work, template="review")
    form.template_index = 0
    assert form.team_known
    assert form.template == "review"


def test_a_solo_session_is_known_to_be_solo(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work, template=None), own)
    form.template_index = 1
    assert form.team_known
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert outcome.spec.template is None


def test_an_unknown_team_is_the_operators_choice(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work, template=UnknownTeam()), own)
    assert not form.team_known
    names = templates.names()
    assert form.template == names[0]
    form.template_index = len(names) - 1
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert outcome.spec.template == names[-1]


# -- the spec --------------------------------------------------------------------------


def test_the_spec_resumes_this_session_in_its_directory_with_its_brief(
    work: Path, own: str
) -> None:
    form = _shown(_open(ResumeForms(), work), own)
    form.model = "claude-other-model"
    form.subagent_cap = 1
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    spec = outcome.spec
    assert spec.resume == SESSION
    assert spec.cwd == os.path.realpath(work)
    assert spec.brief == "/briefs/existing"
    assert spec.template == "review"
    assert spec.model == "claude-other-model"
    assert spec.subagent_cap == 1
    assert spec.policy is Policy.STRICT
    assert spec.containment is None


def test_the_message_does_not_become_the_sessions_task(work: Path, own: str) -> None:
    """
    ``LaunchSpec.task`` is the card's title and what Fork and Retry relaunch with.
    """
    form = _shown(_open(ResumeForms(), work), own)
    form.message = "  ok, carry on  "
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert outcome.spec.task == TASK
    assert outcome.message == "ok, carry on"
    assert outcome.spec.message == "ok, carry on", "what the driver sends in place of the task"
    assert outcome.draft.message == "  ok, carry on  "


def test_a_title_that_is_only_a_label_is_kept_and_said_to_be_one(work: Path, own: str) -> None:
    """
    A session whose original task nothing recorded. The message taking the
    title's place is the hazard: the card is renamed by "ok, carry on", and Fork
    then starts a new session with that as its prompt.
    """
    form = _shown(_open(ResumeForms(), work, task="A summary of it", task_is_label=True), own)
    form.message = "ok, carry on"
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert (outcome.spec.task, outcome.spec.task_is_label) == ("A summary of it", True)
    assert outcome.spec.message == "ok, carry on"
    assert resume_form.task_fact(form.facts) == (
        "title",
        "A summary of it",
        resume_form.TITLE_IS_LABEL,
    )


def test_a_recorded_task_is_not_called_a_label(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work), own)
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert (outcome.spec.task, outcome.spec.task_is_label) == (TASK, False)
    assert resume_form.task_fact(form.facts) == ("task", TASK, None)


@pytest.mark.parametrize("said_to_be_a_label", [False, True])
def test_with_no_title_at_all_the_session_is_announced_under_its_id_and_never_the_message(
    work: Path, own: str, said_to_be_a_label: bool
) -> None:
    """
    Nothing to keep is not leave to use the message: an id is a title nobody
    mistakes for a prompt, and the spec says it is no task whatever the caller
    said of the empty string.
    """
    form = _shown(_open(ResumeForms(), work, task="  ", task_is_label=said_to_be_a_label), own)
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert (outcome.spec.task, outcome.spec.task_is_label) == (SESSION, True)
    assert outcome.spec.message == "carry on with the lexer"
    assert resume_form.task_fact(form.facts)[:2] == ("title", "not known")


def test_the_top_rung_and_its_containment_travel_together(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work, as_policy=Policy.AUTONOMOUS), own)
    outcome = press(form, _answered(), checkout=own)
    assert isinstance(outcome, Launch)
    assert outcome.spec.policy is Policy.AUTONOMOUS
    assert outcome.spec.containment == sandbox.containment_settings()

    middle = _shown(_open(ResumeForms(), work, as_policy=Policy.PERMISSIVE), own)
    outcome = press(middle, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert outcome.spec.policy is Policy.PERMISSIVE
    assert outcome.spec.containment is None


def test_a_session_with_no_recorded_brief_gets_its_canonical_one_if_it_exists(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, own: str
) -> None:
    root = tmp_path / "briefs"
    monkeypatch.setattr(brief_mod, "default_root", lambda: root)
    assert _open(ResumeForms(), work, brief=None).brief is None

    canonical = brief_mod.session_dir(SESSION)
    assert canonical is not None and canonical.parent == root
    canonical.mkdir(parents=True)
    form = _shown(_open(ResumeForms(), work, brief=None), own)
    assert form.brief == str(canonical)
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert outcome.spec.brief == str(canonical)


# -- holds -----------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_an_empty_message_holds(work: Path, own: str, text: str) -> None:
    form = _shown(_open(ResumeForms(), work), own)
    form.message = text
    assert press(form, launcher.VersionGate(), checkout=own) == Hold(resume_form.HOLD_EMPTY)


def test_a_directory_that_is_gone_holds_under_every_rung(work: Path, own: str) -> None:
    for rung in Policy:
        form = _shown(_open(ResumeForms(), work, as_policy=rung), own)
        work.rmdir()
        assert press(form, _answered(), checkout=own) == Hold(resume_form.HOLD_GONE)
        work.mkdir()


def test_a_directory_gone_before_the_form_opened_holds_and_is_said(
    tmp_path: Path, own: str
) -> None:
    form = _shown(_open(ResumeForms(), tmp_path / "never"), own)
    assert form.shown is not None
    assert isinstance(form.shown.directory, launcher.GoneDirectory)
    assert "no longer a directory" in resume_form.directory_line(form.shown.directory)
    assert form.hold(form.shown, launcher.VersionGate()) == resume_form.HOLD_GONE
    assert press(form, launcher.VersionGate(), checkout=own) == Hold(resume_form.HOLD_GONE)


def test_a_session_that_recorded_no_directory_holds(own: str) -> None:
    """The launcher falls back to its cwd field here; this form has none to fall back to."""
    form = _shown(_open(ResumeForms(), ""), own)
    assert press(form, launcher.VersionGate(), checkout=own) == Hold(resume_form.HOLD_UNRECORDED)


def test_a_rung_the_directory_is_refused_for_holds_and_says_why(own: str) -> None:
    inside = Path(own) / "pptmstr"
    form = _shown(_open(ResumeForms(), inside, as_policy=Policy.AUTONOMOUS), own)
    outcome = press(form, _answered(), checkout=own)
    assert isinstance(outcome, Hold)
    assert outcome.reason == resume_form.HOLD_REFUSED.format(rung="autonomous")
    assert "pptmstr's own checkout" in outcome.reason
    assert "autonomous" in outcome.reason


def test_the_refusal_behind_the_mark_says_the_resume_is_held_and_not_downgraded(
    own: str,
) -> None:
    """
    A resume the rung is refused for is held (``launcher.resume_gate_hold``), so
    the refusal's sentence about what becomes of it says held; the ordinary gate
    is what a new session in New Task is given, and is not what happens here.
    """
    inside = Path(own) / "pptmstr"
    reading = resume_form.read(
        _open(ResumeForms(), inside, as_policy=Policy.AUTONOMOUS).facts, checkout=own
    )
    assert reading.refusal is not None
    assert launcher.REFUSAL_HOLDS in reading.refusal
    assert launcher.REFUSAL_DOWNGRADES not in reading.refusal
    assert reading.refusal.startswith("refused for this session's directory")


def test_the_refusal_is_about_the_rung_and_not_the_directory(own: str) -> None:
    """Inside the checkout the ordinary gate still resumes: the way past is the dial."""
    inside = Path(own) / "pptmstr"
    form = _shown(_open(ResumeForms(), inside, as_policy=Policy.AUTONOMOUS), own)
    form.policy = Policy.STRICT
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert outcome.spec.containment is None


def test_the_top_rung_holds_until_the_cli_has_answered(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work, as_policy=Policy.AUTONOMOUS), own)
    assert press(form, launcher.VersionGate(), checkout=own) == Hold(resume_form.HOLD_CHECKING)


def test_the_top_rung_holds_on_a_cli_below_the_floor(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work, as_policy=Policy.AUTONOMOUS), own)
    assert press(form, _answered(_below), checkout=own) == Hold(resume_form.HOLD_CLI)


def test_the_ordinary_gate_does_not_wait_on_the_cli(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work), own)
    assert isinstance(press(form, _answered(_below), checkout=own), Launch)


# -- a disk that moved between the draw and the press -----------------------------------


def test_a_directory_swapped_for_a_link_into_the_checkout_holds_the_top_rung(
    work: Path, own: str
) -> None:
    form = _shown(_open(ResumeForms(), work, as_policy=Policy.AUTONOMOUS), own)
    gate = _answered()
    assert form.hold(form.refresh(checkout=own), gate) is None

    work.rmdir()
    work.symlink_to(Path(own) / "pptmstr", target_is_directory=True)

    outcome = press(form, gate, checkout=own)
    assert outcome == Hold(resume_form.HOLD_REFUSED.format(rung="autonomous"))
    # And the next frame draws what the press found.
    assert form.shown is not None
    assert isinstance(form.shown.directory, launcher.RedirectedDirectory)
    assert form.shown.refusal is not None


def test_a_directory_swapped_for_a_link_holds_even_under_the_ordinary_gate(
    work: Path, own: str, tmp_path: Path
) -> None:
    """
    No rung is refused, and it is still not the resume that was on screen: the
    session would start in another tree under a line naming this one.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    form = _shown(_open(ResumeForms(), work), own)
    work.rmdir()
    work.symlink_to(elsewhere, target_is_directory=True)

    assert press(form, launcher.VersionGate(), checkout=own) == Hold(resume_form.HOLD_MOVED)
    assert form.message == "carry on with the lexer"

    # Drawn again, the form names both spellings, and a press under that is a
    # press under the truth.
    assert form.shown is not None
    line = resume_form.directory_line(form.shown.directory)
    assert str(elsewhere) in line and f"NOT in {work}" in line
    outcome = press(form, launcher.VersionGate(), checkout=own)
    assert isinstance(outcome, Launch)
    assert outcome.spec.cwd == str(elsewhere)


def test_a_refusal_that_lifted_since_the_draw_holds_too(own: str, tmp_path: Path) -> None:
    """
    The same directory, a different answer: "refused" was on screen, and the press
    found it allowed. Nothing starts under a description of another resume.
    """
    inside = Path(own) / "pptmstr"
    form = _shown(_open(ResumeForms(), inside, as_policy=Policy.AUTONOMOUS), own)
    assert form.shown is not None and form.shown.refusal is not None
    outcome = press(form, _answered(), checkout=str(tmp_path / "another-checkout"))
    assert outcome == Hold(resume_form.HOLD_MOVED)


def test_a_press_before_anything_was_drawn_holds(work: Path, own: str) -> None:
    form = _open(ResumeForms(), work)
    assert form.shown is None
    assert press(form, launcher.VersionGate(), checkout=own) == Hold(resume_form.HOLD_UNREAD)


def test_the_screen_follows_the_disk_between_frames(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work), own)
    work.rmdir()
    assert isinstance(form.refresh(checkout=own).directory, launcher.GoneDirectory)
    work.mkdir()
    assert isinstance(form.refresh(checkout=own).directory, launcher.RecordedDirectory)


def test_each_open_asks_the_own_checkout_question_again(own: str, tmp_path: Path) -> None:
    """
    The remembered answer is keyed on the directory, and the answer is about more
    than its spelling. A reopen drops it, as the launcher's does.
    """
    forms = ResumeForms()
    inside = Path(own) / "pptmstr"
    form = _shown(_open(forms, inside), own)
    assert form.shown is not None and form.shown.refusal is not None
    forms.close()
    again = _open(forms, inside)
    assert again.refresh(checkout=str(tmp_path / "another-checkout")).refusal is None


# -- nothing typed is lost ---------------------------------------------------------------


def test_a_held_press_leaves_every_field_as_it_was(tmp_path: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), tmp_path / "never", as_policy=Policy.PERMISSIVE), own)
    form.message = "typed with care"
    form.model = "claude-other-model"
    form.subagent_cap = 7
    before = form.draft()
    assert isinstance(press(form, launcher.VersionGate(), checkout=own), Hold)
    assert form.draft() == before


def test_closing_the_form_keeps_its_draft(work: Path) -> None:
    forms = ResumeForms()
    form = _open(forms, work)
    form.message = "typed with care"
    forms.close()
    assert not forms.is_open
    assert forms.forms[SESSION].message == "typed with care"


def test_a_press_that_starts_clears_the_message_and_the_rung(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work, as_policy=Policy.AUTONOMOUS), own)
    assert isinstance(press(form, _answered(), checkout=own), Launch)
    form.launched()
    assert form.message == ""
    assert form.policy is Policy.STRICT
    assert not form.touched


def test_a_resume_that_started_nothing_gets_its_message_back_on_this_form(
    work: Path, own: str
) -> None:
    forms = ResumeForms()
    forms.version_gate = _answered()
    form = _shown(_open(forms, work, as_policy=Policy.AUTONOMOUS), own)
    form.message = "typed with care"
    form.subagent_cap = 2
    outcome = press(form, forms.version_gate, checkout=own)
    assert isinstance(outcome, Launch)
    form.launched()
    forms.close()

    assert forms.report_failure(SESSION, outcome.draft, "the CLI is below the floor")

    assert forms.showing == SESSION and forms._open_requested
    assert form.draft() == outcome.draft
    assert form.message == "typed with care"
    assert form.policy is Policy.AUTONOMOUS
    assert form.failures[0].reason == "the CLI is below the floor"
    assert form.failure_loaded
    # The gate's "fine" is not what the handed-back draft is offered under.
    assert not forms.version_gate.answered
    assert press(form, forms.version_gate, checkout=own) == Hold(resume_form.HOLD_CHECKING)


def test_a_failure_does_not_replace_what_was_typed_since(work: Path, own: str) -> None:
    forms = ResumeForms()
    form = _shown(_open(forms, work), own)
    form.message = "the first try"
    outcome = press(form, forms.version_gate, checkout=own)
    assert isinstance(outcome, Launch)
    form.launched()
    form.message = "typed while that one was in flight"

    forms.report_failure(SESSION, outcome.draft, "refused")

    assert form.message == "typed while that one was in flight"
    assert not form.failure_loaded
    form.restore_failed()
    assert form.message == "the first try"
    assert [d.message for d in form.set_aside] == ["typed while that one was in flight"]
    form.restore_set_aside()
    assert form.message == "typed while that one was in flight"
    # Not set aside a second time: the failure still holds it.
    assert form.set_aside == []
    assert form.failures[0].draft.message == "the first try"


def test_a_failure_lands_on_its_own_sessions_form_and_not_the_one_on_screen(
    work: Path, own: str
) -> None:
    forms = ResumeForms()
    first = _shown(_open(forms, work), own)
    first.message = "for the first"
    outcome = press(first, forms.version_gate, checkout=own)
    assert isinstance(outcome, Launch)
    first.launched()
    second = _open(forms, work, session_id=OTHER, last_message=None)
    second.message = "for the second"
    forms._open_requested = False
    forms.is_open = True

    assert forms.report_failure(SESSION, outcome.draft, "refused")

    assert forms.showing == OTHER
    assert second.message == "for the second" and not second.failures
    assert first.message == "for the first" and first.failures


def test_a_failure_for_a_session_with_no_form_is_handed_back_to_the_caller() -> None:
    draft = resume_form.Draft("m", Policy.STRICT, "x", None, 0)
    assert ResumeForms().report_failure(SESSION, draft, "refused") is False


def test_retrying_a_failed_resume_as_it_failed_takes_the_failure_with_it(
    work: Path, own: str
) -> None:
    forms = ResumeForms()
    form = _shown(_open(forms, work), own)
    outcome = press(form, forms.version_gate, checkout=own)
    assert isinstance(outcome, Launch)
    form.launched()
    forms.report_failure(SESSION, outcome.draft, "refused")
    assert form.failure_loaded
    assert isinstance(press(form, forms.version_gate, checkout=own), Launch)
    form.launched()
    assert form.failures == []


# -- what the form says ----------------------------------------------------------------


def test_a_live_session_is_warned_about_in_the_launchers_own_sentence(work: Path) -> None:
    form = _open(ResumeForms(), work)
    row = resume_form._row(form.facts)
    assert launcher.live_resume_line(row, frozenset({SESSION})) == launcher._LIVE_RESUME
    assert launcher.live_resume_line(row, frozenset({OTHER})) is None


def test_the_form_does_what_the_gate_menu_says_continue_as_does(work: Path, own: str) -> None:
    """
    The menu's row says the form opens under the rung and that nothing starts until
    it is sent from. Opening is all ``open_for`` does: no launch, no close.
    """
    rows = gate_menu.rows(Policy.STRICT, False, True, adds=widgets.gate_adds)
    row = next(r for r in rows if r.kind is gate_menu.Kind.CONTINUE_AS)
    assert "Opens the resume form" in row.consequence
    forms = ResumeForms()
    form = _open(forms, work, as_policy=row.rung)
    assert form.policy is row.rung
    assert forms.showing == SESSION and not forms.is_open


def test_a_recorded_directory_is_described_in_the_launchers_words(work: Path, own: str) -> None:
    form = _shown(_open(ResumeForms(), work), own)
    assert form.shown is not None
    real = os.path.realpath(work)
    assert resume_form.directory_line(form.shown.directory) == launcher.resume_directory_line(
        launcher.RecordedDirectory(real), real
    )


# -- Dear ImGui itself -------------------------------------------------------------------


class _Frames:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, forms: ResumeForms, own: str) -> None:
        self.forms = forms
        self.own = own
        self.live: frozenset[str] = frozenset()
        self.launched: list[Launch] = []
        self.at: dict[str, tuple[float, float]] = {}
        self._monkeypatch = monkeypatch

    def __enter__(self) -> _Frames:
        imgui = real_imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        at = self.at

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def button(self, label: str, size: object) -> bool:
                pressed = imgui.button(label, size)  # type: ignore[arg-type]
                low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
                at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)
                return bool(pressed)

        self._monkeypatch.setattr(resume_form, "imgui", Noting())
        return self

    def __exit__(self, *exc: object) -> None:
        real_imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            real_imgui.new_frame()
            resume_form.draw(
                self.forms,
                launch=self.launched.append,
                wrap=True,
                prober=_meets,
                subagent_cap=4,
                live=self.live,
                checkout=self.own,
            )
            real_imgui.end_frame()

    def type(self, text: str) -> None:
        self.io.add_input_characters_utf8(text)
        self.frame(2)

    def click(self, label: str) -> None:
        x, y = self.at[label]
        self.io.add_mouse_pos_event(x, y)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)

    def window(self) -> tuple[float, float, float, float]:
        found = real_imgui.internal.find_window_by_name(resume_form.TITLE)
        assert found is not None
        return found.pos.x, found.pos.y, found.size.x, found.size.y


@pytest.fixture
def frames(monkeypatch: pytest.MonkeyPatch, work: Path, own: str) -> Iterator[_Frames]:
    forms = ResumeForms()
    _open(forms, work, last_message=None)
    with _Frames(monkeypatch, forms, own) as real:
        real.frame(3)
        yield real


def test_in_dear_imgui_the_form_comes_up_and_holds_an_empty_message(frames: _Frames) -> None:
    assert frames.forms.is_open
    frames.click("resume")
    assert frames.launched == []
    assert frames.forms.is_open


def test_in_dear_imgui_what_is_typed_is_what_is_resumed_with(frames: _Frames) -> None:
    frames.type("pick up at the grammar")
    form = frames.forms.forms[SESSION]
    assert form.message == "pick up at the grammar"
    frames.frame(5)
    assert form.message == "pick up at the grammar"

    frames.click("resume")

    assert len(frames.launched) == 1
    launch = frames.launched[0]
    assert launch.message == "pick up at the grammar"
    assert launch.spec.resume == SESSION
    assert launch.spec.task == TASK
    assert not frames.forms.is_open
    assert form.message == ""


def test_in_dear_imgui_cancel_keeps_the_text_and_reopening_shows_it(
    frames: _Frames, work: Path
) -> None:
    frames.type("half a thought")
    frames.click("cancel")
    assert not frames.forms.is_open
    assert frames.launched == []

    _open(frames.forms, work, last_message=None)
    frames.frame(3)
    assert frames.forms.is_open
    assert frames.forms.forms[SESSION].message == "half a thought"
    frames.type(", finished")
    frames.click("resume")
    assert [launch.message for launch in frames.launched] == ["half a thought, finished"]


def test_in_dear_imgui_a_failed_resume_comes_back_under_the_cursor(frames: _Frames) -> None:
    frames.type("try this")
    frames.click("resume")
    launch = frames.launched[0]
    assert frames.forms.report_failure(SESSION, launch.draft, "the pool is shut")
    frames.frame(4)
    form = frames.forms.forms[SESSION]
    assert frames.forms.is_open
    assert form.message == "try this"
    frames.frame(4)
    assert form.message == "try this"


def test_in_dear_imgui_a_failure_landing_under_the_focused_box_is_still_there(
    frames: _Frames, work: Path
) -> None:
    """
    A focused InputText writes its own copy over the caller's every frame. The
    operator resumes, opens the form again with the cursor in the empty box, and
    the failure lands: the message assigned under the box has to survive it.
    """
    frames.type("try this")
    frames.click("resume")
    launch = frames.launched[0]
    _open(frames.forms, work, last_message=None)
    frames.frame(3)
    form = frames.forms.forms[SESSION]
    assert form.message == ""
    assert real_imgui.internal.get_active_id() != 0

    assert frames.forms.report_failure(SESSION, launch.draft, "the pool is shut")
    frames.frame(4)

    assert form.message == "try this"


def test_in_dear_imgui_the_form_is_redrawn_from_the_disk_every_frame(
    frames: _Frames, work: Path
) -> None:
    form = frames.forms.forms[SESSION]
    assert form.shown is not None
    assert isinstance(form.shown.directory, launcher.RecordedDirectory)
    work.rmdir()
    frames.frame()
    assert isinstance(form.shown.directory, launcher.GoneDirectory)


def test_in_dear_imgui_a_directory_removed_under_the_form_kills_the_button(
    frames: _Frames, work: Path
) -> None:
    frames.type("go")
    work.rmdir()
    frames.frame(2)
    frames.click("resume")
    assert frames.launched == []
    assert frames.forms.forms[SESSION].message == "go"


def test_in_dear_imgui_only_a_rung_that_needs_containment_reads_the_cli_version(
    frames: _Frames,
) -> None:
    gate = frames.forms.version_gate
    frames.type("go")
    assert not gate.checking and not gate.answered

    frames.forms.forms[SESSION].policy = Policy.AUTONOMOUS
    for _ in range(400):
        frames.frame()
        if gate.answered:
            break
        time.sleep(0.005)
    assert gate.answered and gate.refusal is None

    frames.click("resume")
    assert len(frames.launched) == 1
    assert frames.launched[0].spec.policy is Policy.AUTONOMOUS
    assert frames.launched[0].spec.containment == sandbox.containment_settings()


def test_in_dear_imgui_the_window_grows_with_what_it_holds_and_stays_on_screen(
    frames: _Frames, own: str
) -> None:
    """
    Across the states that add the most lines above the buttons: a live session's
    warning, the top rung, a failure notice. The form is as tall as what it holds,
    so it grows and the buttons go down with it; it stays on the display, with the
    buttons inside it.
    """
    before = dict(frames.at)
    window = frames.window()
    form = frames.forms.forms[SESSION]

    frames.live = frozenset({SESSION})
    form.policy = Policy.AUTONOMOUS
    form.failed(form.draft(), "a reason long enough to wrap " * 6)
    frames.frame(4)

    x, y, width, height = frames.window()
    assert height > window[3], "it grew"
    # Down the window, which is measured from its top: a form that reaches the
    # display's foot is moved up whole, so on the display the buttons may not move.
    assert frames.at["resume"][1] - y > before["resume"][1] - window[1], "and the buttons with it"
    # Three more lines and a row of buttons, not three paragraphs.
    assert height - window[3] < 4.0 * 2.0 * real_imgui.get_text_line_height_with_spacing() + 30.0
    assert x >= 0.0 and y >= 0.0
    assert x + width <= 1280.0 and y + height <= 900.0
    assert frames.at["resume"][1] < y + height
    assert frames.at["cancel"][1] == frames.at["resume"][1]
